from collections import deque
import threading
import time

import numpy as np

from Utils.state_persistence import load_state_file, save_state_file


class RegionOfInterest:

    def __init__(self, name, tag, parent, rois_window, bounds):
        self.parent = parent
        self.rois_window = rois_window
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
        self.last_frame_idx = -1
        self.trace_min_value = 0.0
        self.trace_max_value = 0.0

        initial_crop, _ = self.parent.get_roi_frame(self.bounds)
        if initial_crop is None:
            initial_crop = np.zeros((1, 1), dtype=self.Andor.storage_dtype)
        self.image_width = max(1, int(initial_crop.shape[1]))
        self.image_height = max(1, int(initial_crop.shape[0]))
        self.pending_image_rgba = self._frame_to_rgba(initial_crop)
        self.pending_image_shape = (self.image_height, self.image_width)

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
                self.last_frame_idx = -1
                self.pending_plot_data = ([], [])
                self.trace_min_value = 0.0
                self.trace_max_value = 0.0
                self.pending_version += 1
        self.rebuild_event.set()

    def rebuild_trace_from_history(self):
        self.request_trace_rebuild()

    def close(self):
        self.stop_event.set()

    def _normalize_plot_timestamps(self, timestamps):
        if not timestamps:
            return []

        start_timestamp = float(timestamps[0])
        return [float(timestamp) - start_timestamp for timestamp in timestamps]

    def _build_history_trace(self, bounds, acquisitions, timestamps, current_frame_idx):
        start_frame_idx = max(0, current_frame_idx - len(acquisitions))
        series = []

        for local_index, frame in enumerate(acquisitions):
            frame_number = start_frame_idx + local_index + 1
            crop = self.parent.extract_roi_frame(frame, bounds)
            if crop is None or crop.size == 0:
                continue
            if timestamps and len(timestamps) > local_index:
                x_value = float(timestamps[local_index])
            else:
                x_value = float(frame_number)
            series.append((x_value, self.rois_window.compute_trace_value(crop)))

        x_axis = [point[0] for point in series][-self.max_points:]
        y_axis = [point[1] for point in series][-self.max_points:]
        return x_axis, y_axis

    def _queue_update(self, crop, frame_idx, timestamps, y_axis, trace_min_value, trace_max_value):
        plot_x_axis = self._normalize_plot_timestamps(timestamps)
        with self.data_lock:
            self.last_frame_idx = frame_idx
            self.trace_min_value = float(trace_min_value)
            self.trace_max_value = float(trace_max_value)
            if crop is not None and crop.size > 0:
                self.pending_image_shape = crop.shape
                self.pending_image_rgba = self._frame_to_rgba(crop)
            self.pending_plot_data = (plot_x_axis, list(y_axis))
            self.pending_version += 1

    def _append_trace_points_from_history(self, bounds, acquisitions, timestamps, current_frame_idx, processed_frame_idx, x_axis, y_axis, trace_min_value, trace_max_value):
        if not acquisitions:
            return x_axis, y_axis, trace_min_value, trace_max_value, None

        start_frame_idx = max(0, int(current_frame_idx) - len(acquisitions))
        first_available_frame_number = start_frame_idx + 1
        next_frame_number = max(int(processed_frame_idx) + 1, first_available_frame_number)
        start_offset = max(0, next_frame_number - first_available_frame_number)
        latest_valid_crop = None

        for local_index, frame in enumerate(acquisitions[start_offset:], start=start_offset):
            frame_number = start_frame_idx + local_index + 1
            crop = self.parent.extract_roi_frame(frame, bounds)
            if crop is None or crop.size == 0:
                continue
            latest_valid_crop = crop
            x_value = float(timestamps[local_index]) if timestamps and len(timestamps) > local_index else float(frame_number)
            y_value = self.rois_window.compute_trace_value(crop)
            x_axis.append(x_value)
            y_axis.append(y_value)
            if len(y_axis) == 1:
                trace_min_value = y_value
                trace_max_value = y_value
            else:
                trace_min_value = min(trace_min_value, y_value)
                trace_max_value = max(trace_max_value, y_value)

        return x_axis, y_axis, trace_min_value, trace_max_value, latest_valid_crop

    def _worker_loop(self):
        processed_frame_idx = -1
        x_axis = deque(maxlen=self.max_points)
        y_axis = deque(maxlen=self.max_points)
        trace_min_value = 0.0
        trace_max_value = 0.0

        while not self.stop_event.is_set():
            rebuild_requested = self.rebuild_event.is_set()
            latest_frame, current_frame_idx, has_acquisitions, acquisitions, timestamps = self.parent.get_analysis_snapshot(
                include_history=rebuild_requested,
                include_timestamps=True,
            )

            bounds = self.get_bounds()

            if rebuild_requested:
                self.rebuild_event.clear()
                rebuilt_x_axis, rebuilt_y_axis = self._build_history_trace(bounds, acquisitions or [], timestamps or [], current_frame_idx)
                x_axis = deque(rebuilt_x_axis, maxlen=self.max_points)
                y_axis = deque(rebuilt_y_axis, maxlen=self.max_points)
                trace_min_value = min(y_axis) if y_axis else 0.0
                trace_max_value = max(y_axis) if y_axis else 0.0
                crop = self.parent.extract_roi_frame(latest_frame, bounds)
                if crop is not None and crop.size == 0:
                    crop = None
                self._queue_update(crop, current_frame_idx, x_axis, y_axis, trace_min_value, trace_max_value)
                processed_frame_idx = current_frame_idx

            elif has_acquisitions and latest_frame is not None and current_frame_idx != processed_frame_idx:
                latest_frame, current_frame_idx, has_acquisitions, acquisitions, timestamps = self.parent.get_analysis_snapshot(
                    include_history=True,
                    include_timestamps=True,
                    history_start_frame_idx=processed_frame_idx,
                )
                if has_acquisitions:
                    x_axis, y_axis, trace_min_value, trace_max_value, crop = self._append_trace_points_from_history(
                        bounds,
                        acquisitions or [],
                        timestamps or [],
                        current_frame_idx,
                        processed_frame_idx,
                        x_axis,
                        y_axis,
                        trace_min_value,
                        trace_max_value,
                    )
                    if crop is not None and crop.size > 0:
                        self._queue_update(crop, current_frame_idx, x_axis, y_axis, trace_min_value, trace_max_value)
                    processed_frame_idx = current_frame_idx

            time.sleep(0.01)

    def _frame_to_rgba(self, frame):
        return self.parent.frame_to_rgba(frame)

    def render(self):
        self.rois_window.render_roi(self)

    def _state_name(self):
        return f"{type(self).__name__}_{self.tag}"

    def state_name(self):
        return self._state_name()

    def SaveState(self):
        save_state_file(
            self._state_name(),
            {
                "name": self.name,
                "bounds": list(self.get_bounds()),
            },
        )

    def LoadState(self, state_name=None):
        state = load_state_file(state_name or self._state_name())
        if not state:
            return

        self.name = str(state.get("name") or self.name)
        if "bounds" in state:
            self.set_bounds(state["bounds"])
            self.request_trace_rebuild()