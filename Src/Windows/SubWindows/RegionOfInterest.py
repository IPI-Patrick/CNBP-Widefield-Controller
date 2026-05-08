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
        self._last_processed_frame_idx = -1

        with self.Andor.processed_frame_condition:
            proc_frame = self.Andor.processed_frame
        x1, y1, x2, y2 = self._normalize_bounds(self.bounds)
        initial_crop = np.array(proc_frame[y1:y2, x1:x2], copy=True) if proc_frame is not None and x2 > x1 and y2 > y1 else np.zeros((1, 1), dtype=np.float32)
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
                self._last_processed_frame_idx = -1
                self.pending_plot_data = ([], [])
                self.trace_min_value = 0.0
                self.trace_max_value = 0.0
                self.pending_version += 1
        self.rebuild_event.set()

    def rebuild_trace_from_history(self):
        self.request_trace_rebuild()

    def close(self):
        self.stop_event.set()

    def _get_plot_x_axis(self, point_count):
        return self.Andor.get_estimated_time_axis_values(point_count).tolist()

    def _worker_loop(self):
        y_axis = deque(maxlen=self.max_points)
        trace_min_value = 0.0
        trace_max_value = 0.0

        while not self.stop_event.is_set():
            rebuild_requested = self.rebuild_event.is_set()

            with self.Andor.processed_frame_condition:
                current_idx = self.Andor.processed_frame_idx
                if not rebuild_requested and current_idx == self._last_processed_frame_idx:
                    self.Andor.processed_frame_condition.wait(timeout=0.05)
                    continue
                frame_ref = self.Andor.processed_frame

            if frame_ref is None:
                self._last_processed_frame_idx = current_idx
                time.sleep(0.01)
                continue

            if rebuild_requested:
                self.rebuild_event.clear()
                y_axis.clear()
                trace_min_value = 0.0
                trace_max_value = 0.0

            bounds = self.get_bounds()
            x1, y1, x2, y2 = self.parent._normalize_bounds(bounds)
            if x2 <= x1 or y2 <= y1:
                self._last_processed_frame_idx = current_idx
                continue

            crop = np.array(frame_ref[y1:y2, x1:x2], copy=True)
            if crop.size == 0:
                self._last_processed_frame_idx = current_idx
                continue

            y_value = self.rois_window.compute_trace_value(crop)
            y_axis.append(y_value)

            if len(y_axis) == 1:
                trace_min_value = y_value
                trace_max_value = y_value
            else:
                trace_min_value = min(trace_min_value, y_value)
                trace_max_value = max(trace_max_value, y_value)

            plot_x_axis = self._get_plot_x_axis(len(y_axis))

            with self.data_lock:
                self.last_frame_idx = current_idx
                self.trace_min_value = float(trace_min_value)
                self.trace_max_value = float(trace_max_value)
                self.pending_image_rgba = self._frame_to_rgba(crop)
                self.pending_image_shape = (crop.shape[0], crop.shape[1])
                self.pending_plot_data = (plot_x_axis, list(y_axis))
                self.pending_version += 1

            self._last_processed_frame_idx = current_idx

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