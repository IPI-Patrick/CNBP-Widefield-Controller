import io
import os
import threading
import zipfile
from pathlib import Path

import numpy as np
import dearpygui.dearpygui as dpg

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Windows.SubWindows.AcquisitionPreviewWindow import AcquisitionPreviewWindow


class FileBrowser:

    def __init__(self):
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watch_thread = None
        self._pending_directory_snapshot = None
        self._watch_directory = ""
        self._watch_generation = 0

        self.directory_path = ""
        self.file_entries = []
        self._rows_dirty = True
        self._preview_windows = {}
        self._last_logged_error = None
        self._pending_delete_path = None
        self._delete_modal_id = None
        self._delete_modal_text_id = None

        with dpg.window(
            label="File Browser",
            tag="#FileBrowser",
            width=520,
            height=480,
            pos=(60, 120),
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()

            with dpg.group(horizontal=True):
                self.directory_input_id = dpg.add_input_text(
                    label="Directory",
                    width=-160,
                    default_value="",
                    hint="Browse or paste a folder path and press Enter",
                    on_enter=True,
                    callback=self._on_directory_input_submitted,
                )
                self.browse_button_id = dpg.add_button(
                    label="Browse",
                    width=80,
                    callback=self._show_directory_dialog,
                )

            dpg.add_separator()

            with dpg.child_window(border=False, autosize_x=True, autosize_y=True):
                self.list_container_id = dpg.last_item()

            with dpg.file_dialog(
                directory_selector=True,
                show=False,
                callback=self._on_directory_selected,
                width=700,
                height=400,
                modal=True,
            ) as self.directory_dialog_id:
                pass

        with dpg.window(
            modal=True,
            show=False,
            tag="#FileBrowserDeleteModal",
            no_title_bar=True,
            width=360,
            height=100,
            pos=(560, 340),
        ):
            self._delete_modal_id = dpg.last_item()
            self._delete_modal_text_id = dpg.add_text("Are you sure?")
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Yes, Delete",
                    width=120,
                    callback=self._confirm_delete,
                )
                dpg.add_button(
                    label="No, Cancel",
                    width=120,
                    callback=self._cancel_delete,
                )

        self._start_watch_thread()
        self._rebuild_file_rows()

    def _start_watch_thread(self):
        self._watch_thread = threading.Thread(
            target=self._directory_watch_worker,
            name="FileBrowserDirectoryWatch",
            daemon=True,
        )
        self._watch_thread.start()

    def _show_directory_dialog(self, sender=None, app_data=None, user_data=None):
        dpg.show_item(self.directory_dialog_id)

    def _on_directory_selected(self, sender, app_data, user_data=None):
        selected_path = str(app_data.get("file_path_name") or "").strip()
        self._set_directory(selected_path)

    def _on_directory_input_submitted(self, sender, app_data, user_data=None):
        self._set_directory(str(app_data or "").strip())

    def _set_directory(self, directory_path):
        normalized_path = str(directory_path or "").strip()
        if normalized_path:
            normalized_path = os.path.abspath(normalized_path)

        self.directory_path = normalized_path
        if dpg.does_item_exist(self.directory_input_id):
            dpg.set_value(self.directory_input_id, normalized_path)

        with self._state_lock:
            self._watch_directory = normalized_path
            self._watch_generation += 1

    def _directory_watch_worker(self):
        last_signature = None
        last_generation = -1

        while not self._stop_event.is_set():
            with self._state_lock:
                watch_directory = self._watch_directory
                watch_generation = self._watch_generation

            entries, error_message, signature = self._scan_directory(watch_directory)
            should_publish = watch_generation != last_generation or signature != last_signature

            if should_publish:
                with self._state_lock:
                    self._pending_directory_snapshot = {
                        "entries": entries,
                        "error_message": error_message,
                        "directory_path": watch_directory,
                    }

            last_signature = signature
            last_generation = watch_generation
            self._stop_event.wait(1.0)

    @staticmethod
    def _read_npz_metadata(path):
        meta = {}
        try:
            with zipfile.ZipFile(str(path), mode="r") as zf:
                names = set(zf.namelist())
                for key in ("meta_type", "meta_frame_count", "meta_created_at"):
                    npy_name = f"{key}.npy"
                    if npy_name not in names:
                        continue
                    with zf.open(npy_name) as f:
                        buf = io.BytesIO(f.read())
                    arr = np.lib.format.read_array(buf, allow_pickle=False)
                    meta[key] = arr.item() if arr.shape == () else str(arr)
        except Exception:
            pass
        return meta

    @staticmethod
    def _format_created_at(iso_string):
        if not iso_string:
            return "-"
        try:
            date_part, time_part = str(iso_string).split("T", 1)
            return f"{date_part} {time_part[:5]}"
        except Exception:
            return str(iso_string)[:16]

    def _scan_directory(self, directory_path):
        selected_path = str(directory_path or "").strip()
        if not selected_path:
            return [], None, ("empty",)

        directory = Path(selected_path)
        if not directory.exists():
            return [], f"Directory not found: {selected_path}", ("missing", selected_path)
        if not directory.is_dir():
            return [], f"Path is not a directory: {selected_path}", ("not-directory", selected_path)

        try:
            npz_paths = sorted(directory.glob("*.npz"), key=lambda path: path.name.lower())
        except OSError as exc:
            return [], f"Unable to read directory '{selected_path}': {exc}", ("error", selected_path, str(exc))

        entries = []
        signature = []
        for npz_path in npz_paths:
            try:
                stats = npz_path.stat()
            except OSError:
                continue

            resolved_path = str(npz_path.resolve())
            size_bytes = int(stats.st_size)
            modified_ns = int(getattr(stats, "st_mtime_ns", int(stats.st_mtime * 1_000_000_000)))

            meta = self._read_npz_metadata(npz_path)
            meta_type = str(meta["meta_type"]).strip() if "meta_type" in meta else None
            meta_frame_count = int(meta["meta_frame_count"]) if "meta_frame_count" in meta else None
            meta_created_at = str(meta["meta_created_at"]).strip() if "meta_created_at" in meta else None

            entries.append(
                {
                    "path": resolved_path,
                    "title": npz_path.stem,
                    "filename": npz_path.name,
                    "size_bytes": size_bytes,
                    "size_label": self._format_size(size_bytes),
                    "meta_type": meta_type,
                    "meta_frame_count": meta_frame_count,
                    "meta_created_at": meta_created_at,
                }
            )
            signature.append((resolved_path, size_bytes, modified_ns))

        return entries, None, tuple(signature)

    def _format_size(self, size_bytes):
        size_value = float(max(0, int(size_bytes)))
        units = ("B", "KB", "MB", "GB", "TB")
        unit_index = 0

        while size_value >= 1024.0 and unit_index < (len(units) - 1):
            size_value /= 1024.0
            unit_index += 1

        return f"{int(round(size_value))} {units[unit_index]}"

    def _apply_pending_directory_snapshot(self):
        with self._state_lock:
            snapshot = self._pending_directory_snapshot
            self._pending_directory_snapshot = None

        if snapshot is None:
            return

        self.directory_path = str(snapshot.get("directory_path") or "")
        self.file_entries = list(snapshot.get("entries") or [])
        self._rows_dirty = True
        error_message = snapshot.get("error_message")

        if dpg.does_item_exist(self.directory_input_id):
            dpg.set_value(self.directory_input_id, self.directory_path)

        self._log_error_once(error_message)

    def _log_error_once(self, error_message):
        normalized_message = str(error_message or "").strip() or None
        if normalized_message == self._last_logged_error:
            return

        self._last_logged_error = normalized_message
        if normalized_message is not None:
            print(f"File Browser: {normalized_message}")

    def _rebuild_file_rows(self):
        if not dpg.does_item_exist(self.list_container_id):
            return

        dpg.delete_item(self.list_container_id, children_only=True)

        if not self.file_entries:
            dpg.add_text("No files to show.", parent=self.list_container_id)
            self._rows_dirty = False
            return

        with dpg.table(
            parent=self.list_container_id,
            header_row=True,
            row_background=True,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            borders_outerV=True,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(label="Title",  init_width_or_weight=0.34)
            dpg.add_table_column(label="Type",   init_width_or_weight=0.11)
            dpg.add_table_column(label="Frames", init_width_or_weight=0.09)
            dpg.add_table_column(label="Date",   init_width_or_weight=0.21)
            dpg.add_table_column(label="Size",   init_width_or_weight=0.09)
            dpg.add_table_column(label="",       init_width_or_weight=0.09)
            dpg.add_table_column(label="",       init_width_or_weight=0.07)

            for entry in self.file_entries:
                meta_type        = entry.get("meta_type")
                meta_frame_count = entry.get("meta_frame_count")
                meta_created_at  = entry.get("meta_created_at")

                type_label   = meta_type.capitalize() if meta_type else "-"
                frames_label = str(meta_frame_count) if meta_frame_count is not None else "-"
                date_label   = self._format_created_at(meta_created_at)

                with dpg.table_row():
                    title_text_id = dpg.add_text(str(entry["title"]))
                    with dpg.tooltip(title_text_id):
                        dpg.add_text(str(entry["filename"]))
                        dpg.add_text(str(entry["path"]))
                    dpg.add_text(type_label)
                    dpg.add_text(frames_label)
                    dpg.add_text(date_label)
                    dpg.add_text(str(entry["size_label"]))
                    dpg.add_button(
                        label="Open",
                        width=-1,
                        callback=self._open_preview_window,
                        user_data=str(entry["path"]),
                    )
                    dpg.add_button(
                        label="Del",
                        width=-1,
                        callback=self._request_delete,
                        user_data=str(entry["path"]),
                    )

        self._rows_dirty = False

    def _open_preview_window(self, sender, app_data, user_data=None):
        file_path = str(user_data or "").strip()
        if not file_path:
            return
        if not os.path.isfile(file_path):
            self._log_error_once(f"File no longer exists: {file_path}")
            with self._state_lock:
                self._watch_generation += 1
            return

        existing_preview = self._preview_windows.get(file_path)
        if existing_preview is not None and not existing_preview.is_closed() and dpg.does_item_exist(existing_preview.window_id):
            dpg.show_item(existing_preview.window_id)
            dpg.focus_item(existing_preview.window_id)
            return

        self._preview_windows[file_path] = AcquisitionPreviewWindow(file_path)

    def _request_delete(self, sender, app_data, user_data=None):
        file_path = str(user_data or "").strip()
        if not file_path:
            return
        self._pending_delete_path = file_path
        filename = os.path.basename(file_path)
        short_name = filename if len(filename) <= 40 else f"...{filename[-37:]}"
        if dpg.does_item_exist(self._delete_modal_text_id):
            dpg.set_value(self._delete_modal_text_id, f"Delete '{short_name}'?")
        if dpg.does_item_exist(self._delete_modal_id):
            dpg.configure_item(self._delete_modal_id, show=True)

    def _confirm_delete(self, sender=None, app_data=None, user_data=None):
        if dpg.does_item_exist(self._delete_modal_id):
            dpg.configure_item(self._delete_modal_id, show=False)
        file_path = self._pending_delete_path
        self._pending_delete_path = None
        if not file_path:
            return
        try:
            os.remove(file_path)
        except OSError as exc:
            print(f"File Browser: Failed to delete '{file_path}': {exc}")
            return
        with self._state_lock:
            self._watch_generation += 1

    def _cancel_delete(self, sender=None, app_data=None, user_data=None):
        self._pending_delete_path = None
        if dpg.does_item_exist(self._delete_modal_id):
            dpg.configure_item(self._delete_modal_id, show=False)

    def _render_preview_windows(self):
        closed_paths = []
        for file_path, preview_window in list(self._preview_windows.items()):
            if preview_window is None:
                closed_paths.append(file_path)
                continue
            if not preview_window.render():
                closed_paths.append(file_path)

        for file_path in closed_paths:
            self._preview_windows.pop(file_path, None)

    def render(self):
        self._apply_pending_directory_snapshot()
        if self._rows_dirty:
            self._rebuild_file_rows()
        self._render_preview_windows()

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "directory_path": self.directory_path,
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))
        saved_directory = str(state.get("directory_path") or "").strip()
        if saved_directory:
            self._set_directory(saved_directory)