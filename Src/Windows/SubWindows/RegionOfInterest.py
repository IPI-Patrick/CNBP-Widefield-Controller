from collections import deque
import threading
import time

import numpy as np

from Utils.state_persistence import load_state_file, save_state_file


class RegionOfInterest:
    """Tracks a rectangular region of interest on a camera frame.

    Worker-loop behaviour depends on whether the parent exposes full-history
    access (i.e. ``AcquisitionPreviewWindow``) or not (live ``CameraFeedWindow``).

    Live-camera mode
    ----------------
    The worker appends one data point per processed frame, continuously.  When a
    rebuild is requested without ``nan_pad=True``, the accumulated trace is
    cleared and restarts from the current frame.  When a rebuild is requested
    with ``nan_pad=True`` (ROI moved/resized), the existing trace values are
    replaced with ``float('nan')`` so the graph maintains its x-axis alignment
    while clearly showing that the old positions are no longer valid.

    Preview / full-history mode
    ---------------------------
    The worker only reacts when ``rebuild_event`` is set.  When triggered, it
    fetches **all** available frames at once via ``parent.get_roi_processing_update``
    and computes the complete trace in a single pass.  It then waits idle until
    the next rebuild request.  This means the graph only recalculates when a
    setting changes; it stays static during normal playback scrubbing.
    """

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
        # Flag set by request_trace_rebuild() and consumed by the incremental worker.
        # True  → fill existing trace with nan instead of clearing it.
        self._rebuild_nan_pad = False
        self.pending_image_rgba = None
        self.pending_image_shape = (1, 1)
        self.pending_plot_data = ([], [])
        self.pending_version = 0
        self.applied_version = -1
        self.last_frame_idx = -1
        self.trace_min_value = 0.0
        self.trace_max_value = 0.0
        self._last_processed_frame_idx = -1

        # Detect whether the parent provides full-history access (preview mode).
        self._full_history_mode = hasattr(self.parent, "get_roi_processing_update")

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
        self.request_trace_rebuild(clear_existing=True)

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

    def request_trace_rebuild(self, clear_existing=False, nan_pad=False):
        """Schedule a trace rebuild on the worker thread.

        Parameters
        ----------
        clear_existing:
            If *True*, reset the trace to zero length before rebuilding.
            Use this when the metric or signal-processing pipeline changes so
            that old data is no longer meaningful.
        nan_pad:
            If *True* (and ``clear_existing`` is *False*), fill the currently
            accumulated trace with ``float('nan')`` instead of clearing it.
            This keeps the x-axis aligned with other ROIs while visually
            showing that the previous positions are no longer tracked.
            Applies to live-camera mode only; in full-history mode the full
            trace is always rebuilt from scratch.
        """
        with self.data_lock:
            if clear_existing:
                self._rebuild_nan_pad = False
                self._last_processed_frame_idx = -1
                self.pending_plot_data = ([], [])
                self.trace_min_value = 0.0
                self.trace_max_value = 0.0
                self.pending_version += 1
            elif nan_pad and not self._full_history_mode:
                # Only applies to live-camera mode.
                self._rebuild_nan_pad = True
        self.rebuild_event.set()

    def rebuild_trace_from_history(self):
        self.request_trace_rebuild(clear_existing=True)

    def close(self):
        self.stop_event.set()

    def _get_plot_x_axis(self, point_count):
        return self.Andor.get_estimated_time_axis_values(point_count).tolist()

    # ------------------------------------------------------------------
    # Worker loop — live-camera (incremental) mode
    # ------------------------------------------------------------------

    def _worker_loop(self):
        if self._full_history_mode:
            self._worker_loop_full_history()
        else:
            self._worker_loop_incremental()

    def _worker_loop_incremental(self):
        """Live-camera: append one point per processed frame."""
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
                with self.data_lock:
                    do_nan_pad = self._rebuild_nan_pad
                    self._rebuild_nan_pad = False

                if do_nan_pad:
                    # Replace existing values with nan to preserve x-axis
                    # alignment while showing the old positions are invalid.
                    pad_count = len(y_axis)
                    y_axis.clear()
                    for _ in range(pad_count):
                        y_axis.append(float('nan'))
                    trace_min_value = 0.0
                    trace_max_value = 0.0
                else:
                    # Default rebuild: clear and restart from the current frame.
                    # This covers settings changes (zero ref, display mode, etc.)
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

            # Recompute min/max ignoring nan values.
            finite_vals = [v for v in y_axis if not (v != v)]  # nan check
            if finite_vals:
                trace_min_value = min(finite_vals)
                trace_max_value = max(finite_vals)

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

    # ------------------------------------------------------------------
    # Worker loop — full-history (preview) mode
    # ------------------------------------------------------------------

    def _worker_loop_full_history(self):
        """Preview mode: compute the complete trace from all frames on each rebuild.

        The worker sits idle between rebuilds so that normal playback scrubbing
        does not cause the graph to update continuously.  A rebuild is triggered
        whenever ``rebuild_event`` is set (settings change, new file loaded,
        ROI moved, metric changed, etc.).

        Crops are obtained through ``parent.get_roi_processing_update`` which
        returns raw frame crops.  Each crop is then passed through
        ``parent.process_analysis_frame`` to apply zero-reference adjustment
        (difference/contrast modes) and background removal.  LP filtering is
        not applied to the batch because it is a per-frame cache in the parent;
        the trace values will therefore represent the unfiltered intensity.
        This is acceptable — the graph is a coarse summary, not a pixel-exact
        replica of the display pipeline.
        """
        while not self.stop_event.is_set():
            # Block until a rebuild is requested or we time out.
            triggered = self.rebuild_event.wait(timeout=0.1)
            if not triggered:
                continue
            if self.stop_event.is_set():
                break

            self.rebuild_event.clear()
            # Consume any pending nan-pad flag (not used in full-history mode
            # since the full trace is always recomputed from scratch).
            with self.data_lock:
                self._rebuild_nan_pad = False

            bounds = self.get_bounds()
            result = self.parent.get_roi_processing_update(
                bounds,
                include_history=True,
                include_timestamps=True,
            )
            (latest_crop, current_frame_idx, have_history,
             raw_crops, timestamp_values, zero_crop, state_key, first_frame_idx) = result

            if not have_history or raw_crops is None or raw_crops.size == 0:
                # No data available yet (file not loaded or empty).
                if latest_crop is not None:
                    # Show the current frame thumbnail at least.
                    with self.data_lock:
                        self.pending_image_rgba = self._frame_to_rgba(latest_crop)
                        self.pending_image_shape = (latest_crop.shape[0], latest_crop.shape[1])
                        self.pending_version += 1
                continue

            # Compute the full trace from all available crops, applying the
            # zero-reference / display-mode pipeline per frame.
            process_fn = getattr(self.parent, "process_analysis_frame", None)
            n_frames = raw_crops.shape[0]
            y_values = np.empty(n_frames, dtype=np.float64)
            for i in range(n_frames):
                crop_i = raw_crops[i]
                if process_fn is not None:
                    crop_i = process_fn(crop_i, zero_frame=zero_crop)
                if crop_i is None or np.asarray(crop_i).size == 0:
                    y_values[i] = float('nan')
                else:
                    y_values[i] = self.rois_window.compute_trace_value(np.asarray(crop_i))

                # Yield the GIL periodically so other threads aren't starved.
                if i % 50 == 0 and self.stop_event.is_set():
                    break

            finite_mask = np.isfinite(y_values)
            if np.any(finite_mask):
                trace_min = float(np.min(y_values[finite_mask]))
                trace_max = float(np.max(y_values[finite_mask]))
            else:
                trace_min = 0.0
                trace_max = 0.0

            if timestamp_values is not None and len(timestamp_values) == n_frames:
                plot_x = list(timestamp_values)
            else:
                plot_x = self._get_plot_x_axis(n_frames)

            plot_y = y_values.tolist()

            # Use the current frame's crop for the thumbnail image.
            thumb_crop = latest_crop if latest_crop is not None else raw_crops[-1]

            with self.data_lock:
                self.last_frame_idx = current_frame_idx
                self.trace_min_value = trace_min
                self.trace_max_value = trace_max
                self.pending_image_rgba = self._frame_to_rgba(thumb_crop)
                self.pending_image_shape = (thumb_crop.shape[0], thumb_crop.shape[1])
                self.pending_plot_data = (plot_x, plot_y)
                self.pending_version += 1

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
            self.request_trace_rebuild(clear_existing=True)
