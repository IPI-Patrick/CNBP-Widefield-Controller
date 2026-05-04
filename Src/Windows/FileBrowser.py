import os
import threading
from pathlib import Path

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
            entries.append(
                {
                    "path": resolved_path,
                    "title": npz_path.stem,
                    "filename": npz_path.name,
                    "size_bytes": size_bytes,
                    "size_label": self._format_size(size_bytes),
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

        if unit_index == 0:
            return f"{int(size_value)} {units[unit_index]}"
        return f"{size_value:.2f} {units[unit_index]}"

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
            dpg.add_table_column(label="Title", init_width_or_weight=0.6)
            dpg.add_table_column(label="Size", init_width_or_weight=0.2)
            dpg.add_table_column(label="Preview", init_width_or_weight=0.2)

            for entry in self.file_entries:
                with dpg.table_row():
                    title_text_id = dpg.add_text(str(entry["title"]))
                    with dpg.tooltip(title_text_id):
                        dpg.add_text(str(entry["filename"]))
                        dpg.add_text(str(entry["path"]))
                    dpg.add_text(str(entry["size_label"]))
                    dpg.add_button(
                        label="Open",
                        width=-1,
                        callback=self._open_preview_window,
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