import threading
import time

import dearpygui.dearpygui as dpg
import numpy as np

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import no_padding_theme, default_theme


class RegionOfInterest:

    def __init__(self, name, tag, parent, bounds):
        self.width = 640
        self.height = 200
        self.parent = parent
        self.Andor = parent.Andor
        self.name = name
        self.tag = tag
        self.bounds = self._normalize_bounds(bounds)
        self.max_points = int(getattr(self.Andor, "max_acquisitions", 1000))
        self.image_width = 1
        self.image_height = 1
        self.data_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.rebuild_event = threading.Event()
        self.pending_image_rgba = None
        self.pending_image_shape = (1, 1)
        self.pending_plot_data = ([], [])
        self.pending_version = 0
        self.applied_version = -1
        self.series_x = []
        self.series_y = []
        self.last_frame_idx = -1

        initial_crop, _ = self.parent.get_roi_frame(self.bounds)
        if initial_crop is None:
            initial_crop = np.zeros((1, 1), dtype=np.uint16)
        self.image_width = max(1, int(initial_crop.shape[1]))
        self.image_height = max(1, int(initial_crop.shape[0]))
        self.pending_image_rgba = self._frame_to_rgba(initial_crop)
        self.pending_image_shape = (self.image_height, self.image_width)

        with dpg.window(
            label=name,
            tag=f"{tag}_Window",
            width=self.width,
            height=self.height,
            pos=(935, 10),
            no_scrollbar=True,
            no_resize=False,
            no_scroll_with_mouse=True,
            on_close=lambda: self.parent._close_roi(tag),
        ):

            self.window_id = dpg.last_item()
            dpg.bind_item_theme(self.window_id, no_padding_theme)

            with dpg.item_handler_registry(tag=f"{tag}_ResizeHandler"):
                dpg.add_item_resize_handler(callback=self._on_window_resize)
                dpg.bind_item_handler_registry(self.window_id, f"{tag}_ResizeHandler")

            with dpg.texture_registry(show=False):
                self.texture_id = dpg.add_dynamic_texture(
                    width=self.image_width,
                    height=self.image_height,
                    default_value=self.pending_image_rgba,
                )

            with dpg.group(horizontal=True):
                self.group_id = dpg.last_item()
                self.image_id = dpg.add_image(
                    tag=f"{tag}_Image",
                    texture_tag=self.texture_id,
                    width=self.height,
                    height=self.height,
                )

                with dpg.plot(height=-1, width=-1, no_title=True, no_menus=True):
                    self.plot_id = dpg.last_item()
                    self.x_axis_id = dpg.add_plot_axis(dpg.mvXAxis, label="Frame", auto_fit=True)
                    self.y_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="Mean", auto_fit=True)
                    self.trace_series_id = dpg.add_line_series(
                        [],
                        [],
                        label="Mean Intensity",
                        parent=self.y_axis_id,
                    )

            dpg.bind_item_theme(self.plot_id, default_theme)
            self._update_image_widget_size()

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.request_trace_rebuild()

    def _normalize_bounds(self, bounds):
        x1, y1, x2, y2 = bounds
        x1, x2 = sorted((int(round(x1)), int(round(x2))))
        y1, y2 = sorted((int(round(y1)), int(round(y2))))
        x1 = int(np.clip(x1, 0, self.parent.image_width))
        x2 = int(np.clip(x2, 0, self.parent.image_width))
        y1 = int(np.clip(y1, 0, self.parent.image_height))
        y2 = int(np.clip(y2, 0, self.parent.image_height))
        return x1, y1, x2, y2

    def get_bounds(self):
        with self.data_lock:
            return self.bounds

    def set_bounds(self, bounds):
        with self.data_lock:
            self.bounds = self._normalize_bounds(bounds)

    def request_trace_rebuild(self, clear_existing=False):
        if clear_existing:
            with self.data_lock:
                self.series_x = []
                self.series_y = []
                self.last_frame_idx = -1
                self.pending_plot_data = ([], [])
                self.pending_version += 1
        self.rebuild_event.set()

    def rebuild_trace_from_history(self):
        self.request_trace_rebuild()

    def close(self):
        self.stop_event.set()

    def _build_history_trace(self, bounds, acquisitions, current_frame_idx):
        start_frame_idx = max(0, current_frame_idx - len(acquisitions))
        series = []

        for index, frame in enumerate(acquisitions, start=start_frame_idx + 1):
            crop = self.parent.extract_roi_frame(frame, bounds)
            if crop is None or crop.size == 0:
                continue
            series.append((index, float(np.mean(crop, dtype=np.float64))))

        x_axis = [point[0] for point in series][-self.max_points:]
        y_axis = [point[1] for point in series][-self.max_points:]
        return x_axis, y_axis

    def _queue_update(self, crop, frame_idx, x_axis, y_axis):
        with self.data_lock:
            self.series_x = list(x_axis)
            self.series_y = list(y_axis)
            self.last_frame_idx = frame_idx
            if crop is not None and crop.size > 0:
                self.pending_image_shape = crop.shape
                self.pending_image_rgba = self._frame_to_rgba(crop)
            self.pending_plot_data = (list(x_axis), list(y_axis))
            self.pending_version += 1

    def _worker_loop(self):
        processed_frame_idx = -1

        while not self.stop_event.is_set():
            rebuild_requested = self.rebuild_event.is_set()
            latest_frame, current_frame_idx, has_acquisitions, acquisitions = self.parent.get_analysis_snapshot(
                include_history=rebuild_requested
            )

            bounds = self.get_bounds()

            if rebuild_requested:
                self.rebuild_event.clear()
                x_axis, y_axis = self._build_history_trace(bounds, acquisitions or [], current_frame_idx)
                crop = self.parent.extract_roi_frame(latest_frame, bounds)
                if crop is not None and crop.size == 0:
                    crop = None
                self._queue_update(crop, current_frame_idx, x_axis, y_axis)
                processed_frame_idx = current_frame_idx

            elif has_acquisitions and latest_frame is not None and current_frame_idx != processed_frame_idx:
                crop = self.parent.extract_roi_frame(latest_frame, bounds)
                if crop is not None and crop.size > 0:
                    with self.data_lock:
                        x_axis = list(self.series_x)
                        y_axis = list(self.series_y)
                    x_axis.append(current_frame_idx)
                    y_axis.append(float(np.mean(crop, dtype=np.float64)))
                    x_axis = x_axis[-self.max_points:]
                    y_axis = y_axis[-self.max_points:]
                    self._queue_update(crop, current_frame_idx, x_axis, y_axis)
                    processed_frame_idx = current_frame_idx

            time.sleep(0.01)

    def _frame_to_rgba(self, frame):
        return self.parent._frame_to_rgba(frame)

    def _ensure_texture_shape(self, image_shape):
        crop_height, crop_width = image_shape
        crop_height = max(1, int(crop_height))
        crop_width = max(1, int(crop_width))

        if crop_width == self.image_width and crop_height == self.image_height:
            return

        self.image_width = crop_width
        self.image_height = crop_height

        with dpg.texture_registry(show=False):
            replacement_texture_id = dpg.add_dynamic_texture(
                width=self.image_width,
                height=self.image_height,
                default_value=np.zeros((self.image_width * self.image_height * 4,), dtype=np.float32),
            )

        previous_texture_id = self.texture_id
        self.texture_id = replacement_texture_id
        dpg.configure_item(self.image_id, texture_tag=self.texture_id)

        if dpg.does_item_exist(previous_texture_id):
            dpg.delete_item(previous_texture_id)

        self._update_image_widget_size()

    def _update_image_widget_size(self):
        window_width, window_height = dpg.get_item_rect_size(self.window_id)
        max_image_height = max(120, int(window_height) - 24)
        max_image_width = max(160, int(window_width * 0.45))
        aspect_ratio = self.image_width / max(1, self.image_height)

        image_height = max_image_height
        image_width = int(image_height * aspect_ratio)
        if image_width > max_image_width:
            image_width = max_image_width
            image_height = max(1, int(image_width / max(aspect_ratio, 1e-6)))

        dpg.set_item_width(self.image_id, image_width)
        dpg.set_item_height(self.image_id, image_height)

    def _on_window_resize(self, sender=None, app_data=None):
        self._update_image_widget_size()

    def render(self):
        if not dpg.does_item_exist(self.window_id):
            return

        with self.data_lock:
            pending_version = self.pending_version
            pending_shape = self.pending_image_shape
            pending_image_rgba = self.pending_image_rgba
            pending_plot_x, pending_plot_y = self.pending_plot_data

        if pending_version != self.applied_version:
            self._ensure_texture_shape(pending_shape)
            if pending_image_rgba is not None:
                dpg.set_value(self.texture_id, pending_image_rgba)
            dpg.set_value(self.trace_series_id, [pending_plot_x, pending_plot_y])
            dpg.fit_axis_data(self.x_axis_id)
            dpg.fit_axis_data(self.y_axis_id)
            self.applied_version = pending_version

        self._update_image_widget_size()

    def _state_name(self):
        return f"{type(self).__name__}_{self.tag}"

    def SaveState(self):
        save_state_file(
            self._state_name(),
            {
                "window": capture_window_state(self.window_id),
                "name": self.name,
                "bounds": list(self.get_bounds()),
            },
        )

    def LoadState(self, state_name=None):
        state = load_state_file(state_name or self._state_name())
        if not state:
            return

        self.name = str(state.get("name") or self.name)
        dpg.configure_item(self.window_id, label=self.name)
        if "bounds" in state:
            self.set_bounds(state["bounds"])
            self.request_trace_rebuild()
        apply_window_state(self.window_id, state.get("window"))