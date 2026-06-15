
import math
import time
import numpy as np
import threading
from collections import deque
from pyAndorSDK3 import AndorSDK3
from pyAndorSDK3.andor_sdk3_exceptions import CameraException, ErrorCodes
from Mocks.MockCamera import MockCamera
from Utils.StorageDTypes import (
    get_raw_storage_dtype,
    get_signed_storage_dtype,
    quantize_to_raw_storage_dtype,
    quantize_to_signed_storage_dtype,
)
from Utils.TypedDeque import TypedDeque
from Utils.ProcessingSettings import ProcessingSettings
from Utils.gpu import xp, to_gpu, to_cpu, GPU_AVAILABLE, ndimage_shift as _gpu_shift, phase_cross_correlation_ds, background_filter as _bg_filter
from Utils import fast_backend


class Andor:

    max_acquisitions            = 200    
    acquisitions                = None
    timestamps                  = None
    frameIdx                    = 0

    def __init__(self):

        # Thread-safe data sharing
        self.latest_frame       = None
        self.frame_lock         = threading.Lock()
        self.capture_thread     = None
        self.is_capturing       = False
        self.frame_ready_event  = threading.Event()        
        self.stop_capture_event = threading.Event()
        self.default_max_acquisitions = int(type(self).max_acquisitions)
        self._capture_fps_times = deque(maxlen=2000)

        # Set up the camera
        self.sdk3               = AndorSDK3()

        try:
            self.camera         = self.sdk3.GetCamera(0)
        except Exception:
            
            # Add the mock flag to show that no camera was found
            self.isMock = True

            # For development without a camera
            self.camera     = MockCamera()

            print("Error: No camera found. Running in development mode.")

        # Task 4: Auto-enable cooler at -10 °C on connection
        try:
            self.set_sensor_cooling_enabled(True)
            self.set_temperature_setpoint_c(-10.0)
        except Exception as _cooler_exc:
            print(f"Warning: could not auto-enable cooler on connection: {_cooler_exc}")

        frame_shape             = (self.camera.AOIHeight, self.camera.AOIWidth)
        frame_dtype             = np.dtype(f'u{max(1, (self.bit_depth + 7)//8)}')
        self.frame_shape        = frame_shape
        self.sensor_dtype       = frame_dtype
        self.frame_max_value    = float((2 ** self.bit_depth) - 1)
        self.storage_dtype_name = "16"
        self.raw_storage_dtype  = get_raw_storage_dtype(self.storage_dtype_name)
        self.signed_storage_dtype = get_signed_storage_dtype(self.storage_dtype_name)
        self.storage_dtype      = self.raw_storage_dtype
        self.acquisitions       = self._new_raw_frame_buffer()
        self.timestamps         = self._new_scalar_buffer(np.float64)
        self.zero               = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
        self.latest_frame       = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
        self.lp_filter_enabled  = False
        self.lp_filter_cutoff_hz = min(10.0, max(0.5, self.get_frame_rate() * 0.1))
        self.zero_version       = 0
        self.processed_frame      = np.zeros(frame_shape, dtype=np.float32)
        self.processed_frame_idx  = -1
        self.processed_frame_condition = threading.Condition()
        self.processed_frame_gpu  = None   # device float32 (H,W) latest processed frame
        self._frame_cpu_cache     = None   # lazily materialized CPU processed frame
        self._frame_cpu_cache_idx = -2     # frame idx the frame cache corresponds to
        self.processed_rgba_scale = (0.0, 1.0)  # (min_val, max_val) used for colorbar
        self._rgba_cpu_cache      = None   # lazily materialized flat CPU RGBA
        self._rgba_cpu_cache_idx  = -2     # frame idx the cache corresponds to
        self._rgba_pinned         = None   # reused pinned host buffer for fast D2H
        self._rgba_pull_lock      = threading.Lock()  # serializes RGBA D2H pulls
        self.scope_frame_mean_channels = ()
        self.scope_frame_mean_capacity = 0
        self.scope_frame_mean_buffers = {}
        self.scope_frame_mean_source = None
        self.scope_frame_mean_calculate_mean = True
        self.scope_frame_mean_last_scope_sample_count = 0
        self.frames_axis = np.zeros((0,), dtype=np.float64)
        self.estimated_time_axis = np.zeros((0,), dtype=np.float64)
        self._configure_display_axes_locked(self.default_max_acquisitions, self.get_frame_rate())

        # Processing pipeline settings and ROI list (updated by GUI).
        # Whenever any science setting changes, all ROI plot buffers are cleared
        # so stale trace data is not shown alongside new data.
        self.settings = ProcessingSettings()
        self.settings.frame_rate_hz = float(self.get_frame_rate())
        self.settings.max_value = float(self.frame_max_value)
        self.settings.add_change_callback(self._on_settings_changed)
        self.rois = []

        # When set (by CameraControls in engine mode), the live processing thread
        # submits raw frames to this GIL-free C++/CUDA AcquisitionEngine instead
        # of running the CuPy pipeline. FPS getters delegate to it.
        self.active_engine = None

        # FPS tracking for the processing thread
        self._processing_fps_times = deque(maxlen=600)
        self._processing_thread_stop_event = threading.Event()
        self._processing_thread_stop_event.set()  # no thread running yet

    def _new_raw_frame_buffer(self, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=self.raw_storage_dtype, shape=self.frame_shape)

    def _new_scalar_buffer(self, dtype, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=dtype, shape=())

    def _new_scope_frame_mean_buffers(self):
        return {
            channel_name: TypedDeque(maxlen=self.scope_frame_mean_capacity, dtype=np.float16, shape=())
            for channel_name in self.scope_frame_mean_channels
        }

    def _configure_display_axes_locked(self, frame_count, frame_rate_hz=None):
        frame_count = max(1, int(frame_count))
        if frame_rate_hz is None:
            frame_rate_hz = self.get_frame_rate()
        frame_rate_hz = max(float(frame_rate_hz), 1e-12)
        self.frames_axis = np.arange(1, frame_count + 1, dtype=np.float64)
        self.estimated_time_axis = np.arange(frame_count, dtype=np.float64) / frame_rate_hz

    def configure_display_axes(self, frame_count, frame_rate_hz=None):
        with self.frame_lock:
            self._configure_display_axes_locked(frame_count, frame_rate_hz)

    def _get_display_axis_values_locked(self, axis_values, count):
        count = max(0, int(count))
        if count <= 0 or axis_values.size <= 0:
            return np.zeros((0,), dtype=np.float64)
        count = min(count, int(axis_values.size))
        return np.array(axis_values[:count], copy=True)

    def get_frames_axis_values(self, count):
        with self.frame_lock:
            return self._get_display_axis_values_locked(self.frames_axis, count)

    def get_estimated_time_axis_values(self, count):
        with self.frame_lock:
            return self._get_display_axis_values_locked(self.estimated_time_axis, count)

    def _coerce_raw_frame_to_storage(self, frame):
        return quantize_to_raw_storage_dtype(frame, self.storage_dtype_name, source_max_value=self.frame_max_value)

    def _coerce_signed_frame_to_storage(self, frame):
        return quantize_to_signed_storage_dtype(frame, self.storage_dtype_name)

    def coerce_raw_frame_to_storage(self, frame):
        return self._coerce_raw_frame_to_storage(frame)

    def coerce_signed_frame_to_storage(self, frame):
        return self._coerce_signed_frame_to_storage(frame)

    def _empty_raw_storage_frame(self):
        return np.zeros(self.frame_shape, dtype=self.raw_storage_dtype)

    @property
    def bit_depth(self):
        """Return the camera bit depth as an integer (e.g. 12 from '12 Bit')."""
        return int(str(self.camera.BitDepth).split()[0])

    def supports_sensor_cooling(self):
        return hasattr(self.camera, "SensorCooling")

    def get_sensor_cooling_enabled(self):
        if not self.supports_sensor_cooling():
            return None
        try:
            return bool(getattr(self.camera, "SensorCooling"))
        except Exception:
            return None

    def set_sensor_cooling_enabled(self, enabled):
        if not self.supports_sensor_cooling():
            print("Camera cooler control is not supported by the active camera.")
            return False
        cooler_state = "ON" if bool(enabled) else "OFF"
        print(f"Sending camera cooler command: {cooler_state}")
        try:
            setattr(self.camera, "SensorCooling", bool(enabled))
            print(f"Camera cooler command applied: {cooler_state}")
            return True
        except Exception as exc:
            print(f"Failed to set camera cooler state to {cooler_state}: {exc}")
            return False

    def get_sensor_temperature_c(self):
        if not hasattr(self.camera, "SensorTemperature"):
            return None
        try:
            return float(getattr(self.camera, "SensorTemperature"))
        except Exception:
            return None

    def supports_temperature_setpoint(self):
        return hasattr(self.camera, "TemperatureControl")

    def get_temperature_setpoint_c(self):
        if not self.supports_temperature_setpoint():
            return None
        try:
            return float(getattr(self.camera, "TemperatureControl"))
        except Exception:
            return None

    def get_temperature_setpoint_options(self):
        options = getattr(self.camera, "available_options_TemperatureControl", None)
        if options is None:
            return []
        return [str(option) for option in options]

    def get_temperature_setpoint_options_c(self):
        parsed_options = []
        for option in self.get_temperature_setpoint_options():
            try:
                parsed_options.append(float(option))
            except (TypeError, ValueError):
                continue
        return parsed_options

    def _resolve_temperature_setpoint_option(self, temperature_c):
        target_temperature_c = float(temperature_c)
        parsed_options = []
        for option in self.get_temperature_setpoint_options():
            try:
                parsed_options.append((float(option), option))
            except (TypeError, ValueError):
                continue

        if not parsed_options:
            return str(temperature_c)

        _, option_label = min(parsed_options, key=lambda item: abs(item[0] - target_temperature_c))
        return option_label

    def set_temperature_setpoint_option(self, option_label):
        if not self.supports_temperature_setpoint():
            print("Camera temperature setpoint control is not supported by the active camera.")
            return False

        target_option_label = str(option_label)
        print(f"Sending camera temperature setpoint command: {target_option_label} C")
        try:
            setattr(self.camera, "TemperatureControl", target_option_label)
            print(f"Camera temperature setpoint command applied: {target_option_label} C")
            return True
        except Exception as exc:
            print(f"Failed to set camera temperature setpoint to {target_option_label} C: {exc}")
            return False

    def set_temperature_setpoint_c(self, temperature_c):
        return self.set_temperature_setpoint_option(self._resolve_temperature_setpoint_option(temperature_c))

    def _get_lp_filter_coefficients_locked(self):
        sample_rate_hz = max(float(self.get_frame_rate()), 1e-6)
        nyquist_hz = sample_rate_hz * 0.5
        cutoff_hz = float(np.clip(self.lp_filter_cutoff_hz, 1e-6, max(1e-6, nyquist_hz * 0.99)))
        k = float(np.tan(np.pi * cutoff_hz / sample_rate_hz))
        norm = 1.0 / (1.0 + k)
        return (k * norm), (k * norm), ((k - 1.0) * norm)

    def get_lp_filter_coefficients(self):
        return self._get_lp_filter_coefficients_locked()

    def _apply_lp_filter_step(self, current_input, previous_input, previous_output, coefficients):
        if previous_input is None or previous_output is None:
            return np.array(current_input, dtype=np.float32, copy=True)

        b0, b1, a1 = coefficients
        filtered = (b0 * current_input) + (b1 * previous_input) - (a1 * previous_output)
        return np.clip(filtered, 0.0, self.frame_max_value).astype(np.float32, copy=False)

    def apply_lp_filter_step(self, current_input, previous_input, previous_output, coefficients):
        return self._apply_lp_filter_step(current_input, previous_input, previous_output, coefficients)

    def _get_display_filter_window_size_locked(self):
        if len(self.acquisitions) <= 0:
            return 1

        sample_rate_hz = max(float(self.get_frame_rate()), 1e-6)
        cutoff_hz = max(float(self.lp_filter_cutoff_hz), 1e-6)
        settling_seconds = 6.0 / (2.0 * np.pi * cutoff_hz)
        window_size = int(np.ceil(sample_rate_hz * settling_seconds)) + 1
        return max(1, min(int(len(self.acquisitions)), window_size))

    def get_display_filter_window_size(self):
        return self._get_display_filter_window_size_locked()

    def _compute_filtered_frames_locked(self, frames, *, lp_filter_enabled=None):
        if frames is None:
            return []

        if isinstance(frames, np.ndarray):
            if frames.size <= 0:
                return []
        elif len(frames) <= 0:
            return []

        if lp_filter_enabled is None:
            lp_filter_enabled = self.lp_filter_enabled

        if not lp_filter_enabled:
            return [np.array(frame, copy=True) for frame in frames]

        coefficients = self._get_lp_filter_coefficients_locked()
        previous_input = None
        previous_output = None
        filtered_frames = []

        for frame in frames:
            current_input = np.asarray(frame, dtype=np.float32)
            filtered_output = self._apply_lp_filter_step(current_input, previous_input, previous_output, coefficients)
            filtered_frames.append(np.array(self._coerce_raw_frame_to_storage(filtered_output), copy=True))
            previous_input = current_input
            previous_output = filtered_output

        return filtered_frames

    def _compute_processed_frames_locked(self, frames, *, display_mode="Normal", lp_filter_enabled=None):
        normalized_mode = str(display_mode or "Normal")
        filtered_frames = self._compute_filtered_frames_locked(frames, lp_filter_enabled=lp_filter_enabled)

        if normalized_mode == "Difference":
            return [np.array(self._compute_difference_frame(frame), copy=True) for frame in filtered_frames]
        if normalized_mode == "Contrast":
            return [np.array(self._compute_contrast_frame(frame), copy=True) for frame in filtered_frames]
        return filtered_frames

    def get_processed_frame_view_locked(self, *, display_mode="Normal", lp_filter_enabled=None, include_history=False, history_start_frame_idx=None):
        current_frame_idx = int(self.frameIdx)
        frame_count = len(self.acquisitions)
        if frame_count <= 0:
            return None, current_frame_idx, False, None

        if lp_filter_enabled is None:
            lp_filter_enabled = self.lp_filter_enabled

        first_available_frame_idx = max(0, current_frame_idx - frame_count) + 1
        history_start_offset = 0
        if include_history and history_start_frame_idx is not None:
            next_frame_idx = max(int(history_start_frame_idx) + 1, first_available_frame_idx)
            history_start_offset = max(0, next_frame_idx - first_available_frame_idx)

        filter_window_size = self._get_display_filter_window_size_locked() if lp_filter_enabled else 1
        processing_start_offset = max(0, history_start_offset - max(0, filter_window_size - 1))
        if not include_history:
            processing_start_offset = max(0, frame_count - filter_window_size)

        sample_count = max(0, frame_count - processing_start_offset)
        raw_frames = self.acquisitions.range_array(processing_start_offset, sample_count, copy=True)
        if raw_frames.size <= 0:
            return None, current_frame_idx, False, None

        processed_frames = self._compute_processed_frames_locked(
            raw_frames,
            display_mode=display_mode,
            lp_filter_enabled=lp_filter_enabled,
        )
        if not processed_frames:
            return None, current_frame_idx, False, None

        latest_frame = np.array(processed_frames[-1], copy=True)
        history_frames = None
        if include_history:
            trim_offset = max(0, history_start_offset - processing_start_offset)
            history_frames = [np.array(frame, copy=True) for frame in processed_frames[trim_offset:]]

        return latest_frame, current_frame_idx, True, history_frames

    def _compute_difference_frame(self, frame):
        difference_frame = np.asarray(frame, dtype=np.float32) - np.asarray(self.zero, dtype=np.float32)
        return self._coerce_signed_frame_to_storage(difference_frame)

    def _compute_contrast_frame(self, frame):
        frame_float = np.asarray(frame, dtype=np.float32)
        zero_float = np.asarray(self.zero, dtype=np.float32)
        difference_frame = frame_float - zero_float
        contrast_frame = (difference_frame / (zero_float + 1.0)) * 100.0
        return self._coerce_signed_frame_to_storage(contrast_frame)

    def set_zero_frame(self, frame):
        if frame is None:
            return

        with self.frame_lock:
            self.zero = np.array(self._coerce_raw_frame_to_storage(frame), copy=True)
            self.zero_version += 1
            self.frame_ready_event.set()

    def set_lp_filter_enabled(self, enabled):
        with self.frame_lock:
            self.lp_filter_enabled = bool(enabled)
            self.frame_ready_event.set()

    def set_lp_filter_cutoff_hz(self, cutoff_hz):
        cutoff_hz = max(1e-3, float(cutoff_hz))
        with self.frame_lock:
            self.lp_filter_cutoff_hz = cutoff_hz
            self.frame_ready_event.set()

    def get_frame_rate(self):
        frame_rate = getattr(self.camera, "FrameRate", None)
        if frame_rate is None:
            exposure_time = max(float(getattr(self.camera, "ExposureTime", 0.01)), 1e-6)
            return 1.0 / exposure_time
        return float(frame_rate)

    def set_frame_rate(self, frame_rate_hz):
        frame_rate_hz = float(frame_rate_hz)
        if frame_rate_hz <= 0:
            raise ValueError("frame_rate_hz must be > 0")
        if hasattr(self.camera, "FrameRate"):
            self.camera.FrameRate = frame_rate_hz
        with self.frame_lock:
            axis_frame_count = max(int(self.max_acquisitions), int(self.default_max_acquisitions), int(self.frames_axis.size or 0), 1)
            self._configure_display_axes_locked(axis_frame_count, frame_rate_hz)
            self.frame_ready_event.set()

    def clear_buffers(self, *, reset_frame_index=True):
        frame_shape = (int(self.camera.AOIHeight), int(self.camera.AOIWidth))
        zero_shape_changed = tuple(np.shape(self.zero)) != tuple(frame_shape)
        self.frame_shape = frame_shape
        with self.frame_lock:
            self.acquisitions = self._new_raw_frame_buffer()
            self.timestamps = self._new_scalar_buffer(np.float64)
            self.scope_frame_mean_buffers = self._new_scope_frame_mean_buffers()
            self.scope_frame_mean_last_scope_sample_count = 0
            if zero_shape_changed:
                self.zero = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
                self.zero_version = 0
            self.processed_frame = np.zeros(frame_shape, dtype=np.float32)
            self.processed_frame_idx = -1
            self.processed_frame_gpu = None
            self._frame_cpu_cache = None
            self._frame_cpu_cache_idx = -2
            self._rgba_cpu_cache = None
            self._rgba_cpu_cache_idx = -2
            self.latest_frame = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
            if reset_frame_index:
                self.frameIdx = 0
            self._capture_fps_times.clear()
            self.frame_ready_event.clear()

    def get_capture_loop_fps(self):
        with self.frame_lock:
            times = list(self._capture_fps_times)
        if len(times) < 2:
            return 0.0
        cutoff = times[-1] - 2.0
        recent = [t for t in times if t >= cutoff]
        if len(recent) < 2:
            return 0.0
        elapsed = recent[-1] - recent[0]
        return 0.0 if elapsed <= 0.0 else float((len(recent) - 1) / elapsed)

    def set_preview_max_frames(self, frame_count):
        frame_count = max(1, int(frame_count))
        self.default_max_acquisitions = frame_count
        self.configure_display_axes(frame_count, self.get_frame_rate())
        if not self.is_capturing:
            self.max_acquisitions = frame_count

    def configure_scope_frame_mean_buffers(self, channel_names, frame_count):
        normalized_channels = tuple(
            sorted(
                {
                    str(channel_name).upper()
                    for channel_name in channel_names
                    if str(channel_name).strip()
                }
            )
        )
        frame_count = max(0, int(frame_count))

        with self.frame_lock:
            self.scope_frame_mean_channels = normalized_channels
            self.scope_frame_mean_capacity = frame_count
            self.scope_frame_mean_buffers = self._new_scope_frame_mean_buffers()
            self.scope_frame_mean_last_scope_sample_count = 0

    def set_scope_frame_mean_source(self, scope_source, *, calculate_mean=True):
        with self.frame_lock:
            self.scope_frame_mean_source = scope_source
            self.scope_frame_mean_calculate_mean = bool(calculate_mean)
            self.scope_frame_mean_last_scope_sample_count = 0

    def disable_scope_frame_mean_buffers(self):
        with self.frame_lock:
            self.scope_frame_mean_channels = ()
            self.scope_frame_mean_capacity = 0
            self.scope_frame_mean_buffers = {}
            self.scope_frame_mean_source = None
            self.scope_frame_mean_last_scope_sample_count = 0

    def append_scope_frame_mean_values(self, channel_values):
        with self.frame_lock:
            for channel_name in self.scope_frame_mean_channels:
                value = float(channel_values.get(channel_name, np.nan))
                self.scope_frame_mean_buffers[channel_name].append(np.float16(value))

    def _append_scope_frame_values_from_source_locked(self):
        if not self.scope_frame_mean_channels or self.scope_frame_mean_source is None:
            return

        try:
            scope_snapshot = self.scope_frame_mean_source.get_buffer_snapshot(channel_names=self.scope_frame_mean_channels)
        except Exception:
            return

        scope_timestamps = np.asarray(scope_snapshot.get("timestamps", []), dtype=np.float64)
        total_samples_received = int(scope_snapshot.get("total_samples_received", 0))
        if scope_timestamps.size <= 0 or total_samples_received <= 0:
            for channel_name in self.scope_frame_mean_channels:
                self.scope_frame_mean_buffers[channel_name].append(np.float16(np.nan))
            return

        oldest_retained_sample_index = max(0, total_samples_received - int(scope_timestamps.size))
        start_sample_index = max(int(self.scope_frame_mean_last_scope_sample_count), oldest_retained_sample_index)
        start_offset = max(0, start_sample_index - oldest_retained_sample_index)
        self.scope_frame_mean_last_scope_sample_count = total_samples_received

        frame_values = {}
        for channel_name in self.scope_frame_mean_channels:
            raw_samples = np.asarray(scope_snapshot.get("channels", {}).get(channel_name, []), dtype=np.float32)
            if raw_samples.size <= 0:
                frame_values[channel_name] = np.nan
                continue

            if self.scope_frame_mean_calculate_mean:
                recent_samples = raw_samples[start_offset:]
                if recent_samples.size <= 0:
                    recent_samples = raw_samples[-1:]
                voltage_samples = self.scope_frame_mean_source.convert_samples_to_volts(channel_name, recent_samples)
                frame_values[channel_name] = float(np.mean(voltage_samples, dtype=np.float64))
            else:
                latest_sample = raw_samples[-1:]
                voltage_samples = self.scope_frame_mean_source.convert_samples_to_volts(channel_name, latest_sample)
                frame_values[channel_name] = float(voltage_samples[-1])

        for channel_name in self.scope_frame_mean_channels:
            value = float(frame_values.get(channel_name, np.nan))
            self.scope_frame_mean_buffers[channel_name].append(np.float16(value))

    def get_scope_frame_mean_channels(self):
        with self.frame_lock:
            return tuple(self.scope_frame_mean_channels)

    def get_scope_frame_mean_count(self):
        with self.frame_lock:
            if not self.scope_frame_mean_buffers:
                return 0
            first_channel = next(iter(self.scope_frame_mean_buffers.values()))
            return len(first_channel)

    def get_scope_frame_mean_snapshot(self, *, start_index=0):
        with self.frame_lock:
            if not self.scope_frame_mean_buffers:
                scope_frame_mean_count = 0
            else:
                first_channel = next(iter(self.scope_frame_mean_buffers.values()))
                scope_frame_mean_count = len(first_channel)

            start_index = max(0, min(int(start_index), scope_frame_mean_count))
            sample_count = max(0, scope_frame_mean_count - start_index)

            if scope_frame_mean_count > 0 and len(self.timestamps) >= scope_frame_mean_count:
                timestamp_start_index = max(0, len(self.timestamps) - scope_frame_mean_count + start_index)
                timestamps = self.timestamps.range_array(timestamp_start_index, sample_count, copy=True)
            else:
                timestamps = self.timestamps.range_array(start_index, sample_count, copy=True)

            return {
                "timestamps": timestamps,
                "scope_frame_mean_count": int(scope_frame_mean_count),
                "scope_frame_mean_start_index": int(start_index),
                "scope_frame_mean_channels": list(self.scope_frame_mean_channels),
                "scope_frame_mean_capacity": int(self.scope_frame_mean_capacity),
                "scope_frame_mean_buffers": {
                    channel_name: buffer.range_array(start_index, sample_count, copy=True)
                    for channel_name, buffer in self.scope_frame_mean_buffers.items()
                },
            }

    def get_snapshot(self):
        with self.frame_lock:
            return {
                "acquisitions": [np.array(frame, copy=True) for frame in self.acquisitions],
                "timestamps": list(self.timestamps),
                "frame_index": int(self.frameIdx),
                "zero": np.array(self.zero, copy=True),
                "latest_frame": np.array(self.latest_frame, copy=True),
                "storage_dtype": self.storage_dtype_name,
                "lp_filter_enabled": bool(self.lp_filter_enabled),
                "lp_filter_cutoff_hz": float(self.lp_filter_cutoff_hz),
                "scope_frame_mean_channels": list(self.scope_frame_mean_channels),
                "scope_frame_mean_capacity": int(self.scope_frame_mean_capacity),
                "scope_frame_mean_buffers": {
                    channel_name: np.asarray(buffer, dtype=np.float16)
                    for channel_name, buffer in self.scope_frame_mean_buffers.items()
                },
            }
    

    def _capture_loop(self, continuous=False, callback=None):    
        print(f"{'Continuous' if continuous else ''} Capture Started")

        # Set up the camera for acquisition
        cam                         = self.camera
        timeout                     = 1000
        imgsize                     = cam.ImageSizeBytes
        soft_trigger                = cam.TriggerMode == "Software"
        cam.CycleMode               = "Continuous"
        buffer_count                = 10
        # last_frame_delivery_time    = None
        # last_frame_ready_time       = None

        def _queue_capture_buffers():
            # Task 5: Refresh imgsize in case it changed (e.g. after AOI change),
            # and catch AT_ERR_INVALIDSIZE by flushing and retrying once.
            nonlocal imgsize
            imgsize = cam.ImageSizeBytes
            try:
                for _ in range(buffer_count):
                    buf = np.empty((imgsize,), dtype='B')
                    cam.queue(buf, imgsize)
            except (CameraException, Exception) as _queue_exc:
                is_invalid_size = (
                    isinstance(_queue_exc, CameraException)
                    and getattr(_queue_exc, "err_code", None) == ErrorCodes.AT_ERR_INVALIDSIZE
                )
                if is_invalid_size:
                    print("AT_ERR_INVALIDSIZE during buffer queue; flushing and retrying with refreshed size")
                    try:
                        cam.flush()
                        imgsize = cam.ImageSizeBytes
                        for _ in range(buffer_count):
                            buf = np.empty((imgsize,), dtype='B')
                            cam.queue(buf, imgsize)
                    except Exception as _retry_exc:
                        print(f"Buffer re-queue failed after AT_ERR_INVALIDSIZE flush: {_retry_exc}")
                        raise
                else:
                    raise

        def _restart_acquisition_after_timeout():
            print("Preview timed out twice; restarting acquisition")
            cam.AcquisitionStop()
            cam.flush()
            _queue_capture_buffers()
            cam.AcquisitionStart()

        self.clear_buffers(reset_frame_index=True)

        # Pre-allocate the buffers
        _queue_capture_buffers()

        _consecutive_timeout_count = 0
        _startup_black_frames_skipped = 0
        _MAX_STARTUP_BLACK_FRAMES = 5

        try:
            cam.AcquisitionStart()
            while True:

                # If using software trigger, trigger it
                if soft_trigger:
                    cam.SoftwareTrigger()

                # Wait until the next frame is ready in the buffer.
                # Tolerate up to 5 consecutive AT_ERR_TIMEDOUT errors silently;
                # on the 6th consecutive timeout, print a message and stop.
                try:
                    acq = cam.wait_buffer(timeout)
                except (CameraException, TimeoutError) as _te:
                    is_andor_timeout = (
                        isinstance(_te, CameraException)
                        and getattr(_te, "err_code", None) == ErrorCodes.AT_ERR_TIMEDOUT
                    )
                    is_mock_timeout = isinstance(_te, TimeoutError)
                    if is_andor_timeout or is_mock_timeout:
                        _consecutive_timeout_count += 1
                        if continuous and _consecutive_timeout_count >= 2:
                            _restart_acquisition_after_timeout()
                            _consecutive_timeout_count = 0
                            continue
                        if _consecutive_timeout_count >= 6:
                            print("Timed out more than 5 times")
                            break
                        continue
                    raise


                # Successful frame — reset the consecutive timeout counter
                _consecutive_timeout_count = 0

                # Update the latest frame in a thread-safe manner
                submit_frame = None
                engine = self.active_engine
                with self.frame_lock:
                    raw_frame = np.asarray(acq.image, dtype=self.sensor_dtype)

                    # Discard all-zero frames that arrive before the sensor is ready
                    # (camera startup artifact), up to a small limit.
                    if (
                        _startup_black_frames_skipped < _MAX_STARTUP_BLACK_FRAMES
                        and raw_frame.size > 0
                        and int(raw_frame.max()) == 0
                    ):
                        _startup_black_frames_skipped += 1
                    else:
                        storage_frame = np.array(raw_frame, dtype=self.raw_storage_dtype, copy=True)

                        # Store the acquisition and timestamp in the buffers
                        frame_timestamp = float(getattr(acq, "frame_ready_timestamp", time.time()))
                        self.acquisitions.append(storage_frame)
                        self.timestamps.append(frame_timestamp)
                        self._capture_fps_times.append(time.time())
                        # storage_frame is a fresh array that is never mutated after
                        # this point, and the deque copies it on append — so in
                        # engine mode we can share the reference instead of paying a
                        # second full-frame copy (less GIL-held work per capture).
                        self.latest_frame = (storage_frame if engine is not None
                                             else np.array(storage_frame, copy=True))

                        self._append_scope_frame_values_from_source_locked()

                        # Signal that a new frame is ready
                        self.frame_ready_event.set()
                        self.frameIdx += 1

                        # Engine mode: submit straight to the GIL-free engine HERE
                        # rather than waking the Python processing thread to forward
                        # it. That eliminates an entire ~capture-rate Python thread
                        # of GIL contention which otherwise starves the UI render
                        # loop (the engine compute itself is already GIL-free).
                        if engine is not None:
                            submit_frame = storage_frame

                        # If not in continuous mode and we've reached the max acquisitions, stop
                        if not continuous and self.frameIdx >= self.max_acquisitions:
                            break

                # submit() releases the GIL for the memcpy; do it outside frame_lock
                # so we never hold the lock across the copy into the engine.
                if submit_frame is not None:
                    try:
                        engine.submit(submit_frame)
                    except Exception as exc:
                        print(f"engine.submit error: {exc}")

                # Re-add this buffer to the queue; handle AT_ERR_INVALIDSIZE by
                # restarting the buffer pool with the current image size.
                try:
                    queue_buffer = getattr(acq, "buffer_data", getattr(acq, "_np_data"))
                    cam.queue(queue_buffer, imgsize)
                except (CameraException, Exception) as _requeue_exc:
                    is_invalid_size = (
                        isinstance(_requeue_exc, CameraException)
                        and getattr(_requeue_exc, "err_code", None) == ErrorCodes.AT_ERR_INVALIDSIZE
                    )
                    if is_invalid_size:
                        print("AT_ERR_INVALIDSIZE on buffer re-queue; flushing and re-seeding buffers")
                        try:
                            cam.AcquisitionStop()
                            cam.flush()
                            _queue_capture_buffers()
                            cam.AcquisitionStart()
                            _consecutive_timeout_count = 0
                        except Exception as _recover_exc:
                            print(f"Failed to recover from AT_ERR_INVALIDSIZE: {_recover_exc}")
                            break
                    else:
                        raise

                # If the stop event is triggered, stop
                if self.stop_capture_event.is_set():
                    break

        except Exception as e:
            print("Error occurred during acquisition")
            print(e)
            print()

        # Stop the acquisition
        with self.frame_lock:
            self.is_capturing = False
            self.stop_capture_event.clear()

        print("Preview capture stopped")
        cam.AcquisitionStop()
        cam.flush()

        # Call the callback if provided
        if callback:
            callback(self.acquisitions)

    def start_capture_continuous(self, callback=None):
        self.max_acquisitions = self.default_max_acquisitions
        self.configure_display_axes(self.max_acquisitions, self.get_frame_rate())
        self.start_capture(continuous=True, callback=callback)

    def start_capture(self, continuous=False, callback=None):
        
        # Start continuously capturing in a seperate thread
        if self.is_capturing:
            print("Capture already running")
            return
            
        self.is_capturing   = True
        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            kwargs={"continuous": continuous, "callback": callback},
        )
        self.capture_thread.start()

    def start_capture_fixed(self, frame_count, callback=None):
        frame_count = max(1, int(frame_count))
        self.max_acquisitions = frame_count
        self.configure_display_axes(frame_count, self.get_frame_rate())
        self.start_capture(continuous=False, callback=callback)

    def stop_capture(self):
        # Stop the capture thread
        self.stop_capture_event.set()
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        self.capture_thread = None

    # ══════════════════════════════════════════════════════════════════════
    # Unified processing pipeline
    # ══════════════════════════════════════════════════════════════════════

    def _on_settings_changed(self, field_name, new_value):
        """Clear all ROI plot buffers whenever any processing setting changes."""
        for roi in list(self.rois):
            try:
                roi.clear_plot_buffers()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Static frame-shift helpers (used by process_frame for drift correction)
    # ------------------------------------------------------------------

    @staticmethod
    def _shift_frame_for_display(frame, dy, dx):
        if dy == 0 and dx == 0:
            return frame
        shifted = np.zeros_like(frame)
        h, w = frame.shape[:2]
        src_y = slice(max(0, -dy), h - max(0, dy))
        dst_y = slice(max(0, dy), h - max(0, -dy))
        src_x = slice(max(0, -dx), w - max(0, dx))
        dst_x = slice(max(0, dx), w - max(0, -dx))
        shifted[dst_y, dst_x] = frame[src_y, src_x]
        return shifted

    @staticmethod
    def _shift_frame_subpixel(frame, dy, dx):
        frame_f32 = np.asarray(frame, dtype=np.float32)
        dy_int = int(math.floor(dy))
        dx_int = int(math.floor(dx))
        fy = float(dy - dy_int)
        fx = float(dx - dx_int)
        s00 = Andor._shift_frame_for_display(frame_f32, dy_int, dx_int)
        row0 = (
            (1.0 - fx) * s00
            + fx * Andor._shift_frame_for_display(frame_f32, dy_int, dx_int + 1)
            if fx > 1e-6 else s00
        )
        if fy > 1e-6:
            s10 = Andor._shift_frame_for_display(frame_f32, dy_int + 1, dx_int)
            row1 = (
                (1.0 - fx) * s10
                + fx * Andor._shift_frame_for_display(frame_f32, dy_int + 1, dx_int + 1)
                if fx > 1e-6 else s10
            )
            return (1.0 - fy) * row0 + fy * row1
        return row0

    @staticmethod
    def _compute_drift_valid_mask(shape, dy, dx):
        h, w = shape
        mask = np.ones(shape, dtype=bool)
        top    = max(0, math.ceil(dy))
        bottom = max(0, math.ceil(-dy))
        left   = max(0, math.ceil(dx))
        right  = max(0, math.ceil(-dx))
        if top    > 0: mask[:top, :]        = False
        if bottom > 0: mask[h - bottom:, :] = False
        if left   > 0: mask[:, :left]       = False
        if right  > 0: mask[:, w - right:]  = False
        return mask

    # ------------------------------------------------------------------
    # process_frame — full science pipeline for a single raw frame
    # ------------------------------------------------------------------

    def process_frame(self, raw_frame, settings, rois=None, state=None,
                      want_cpu_frame=False):
        """Apply the full science pipeline to one raw frame.

        Pipeline order: Drift-Correction → LP-Filter → BG-Removal →
        Difference/Contrast → Crop → ROI-Calculation.

        Parameters
        ----------
        raw_frame      : array-like, raw pixel data
        settings       : ProcessingSettings
        rois           : list of ProcessingROI (optional)
        state          : dict carrying LP-filter continuity between calls.
                         Pass ``None`` to start a fresh filter chain.
        want_cpu_frame : when True, transfer the processed frame to the host and
                         return it (used by the batch/preview path).  When False
                         (the live path) the processed frame stays on the GPU —
                         ``state["_frame_gpu"]`` holds the device array and the
                         return value is ``None``.  Callers fetch a CPU copy
                         lazily via ``get_processed_frame_cpu`` /
                         ``get_roi_crop_cpu``.

        Returns
        -------
        (processed_frame : float32 ndarray or None, updated_state : dict)
        """
        if rois is None:
            rois = []
        if state is None:
            state = {}

        # --- GIL-free C++/CUDA backend (optional) ---
        # When enabled (and the compiled extension is present), run the whole
        # temporal per-frame pipeline in one GIL-released call so the Dear PyGui
        # render loop can't starve the processing thread. Off by default.
        if (getattr(settings, "use_cpp_backend", False)
                and getattr(settings, "bg_mode", "spatial") == "temporal"
                and GPU_AVAILABLE and fast_backend.available()):
            try:
                return self._process_frame_cpp(raw_frame, settings, rois, state, want_cpu_frame)
            except Exception as exc:
                # Never let the fast path break acquisition; fall back to CuPy.
                print(f"fastproc backend error, falling back to CuPy path: {exc}")

        # Upload the raw frame in its NATIVE dtype (e.g. uint16) and convert to
        # float32 ON the GPU. Converting uint16->float32 on the CPU first costs
        # ~1.3 ms at 1200x1200 (the single largest per-frame cost) and doubles
        # the bytes crossing PCIe; doing it on-device makes the whole step
        # ~0.25 ms. .astype always returns a fresh array for non-float32 input,
        # so the in-place border-zeroing below is safe.
        frame_gpu = to_gpu(raw_frame)
        frame_f32 = frame_gpu if frame_gpu.dtype == xp.float32 else frame_gpu.astype(xp.float32)

        # --- Refresh zero_frame caches when the reference array changes ---
        # Keyed by object identity — changes whenever a new zero frame is captured.
        zero_id = id(settings.zero_frame) if settings.zero_frame is not None else None
        if state.get("_zero_id") != zero_id:
            state["_zero_id"] = zero_id
            if settings.zero_frame is not None:
                _ref_cpu = np.asarray(settings.zero_frame, dtype=np.float32)
                state["zero_gpu"]    = to_gpu(_ref_cpu)
                state["ref_std_ok"]  = float(_ref_cpu.std()) >= 1e-6
                _ds = 4
                _ref_ds = xp.asarray(_ref_cpu[::_ds, ::_ds], dtype=xp.float64)
                state["ref_fft_ds"]  = xp.fft.rfft2(_ref_ds)  # cached FFT of downsampled ref
            else:
                state["zero_gpu"]    = None
                state["ref_std_ok"]  = False
                state["ref_fft_ds"]  = None

        # --- Drift Correction ---
        # One FFT per frame (reference FFT cached above) + tiny corr-map D2H transfer.
        drift_border = None
        if settings.drift_correction_enabled and state.get("ref_std_ok") and state.get("ref_fft_ds") is not None:
            try:
                # phase_every cadence: drift is slow relative to the frame rate, so
                # recompute the phase-correlation shift only every N frames and reuse
                # the last shift in between (the example prototype's biggest lever).
                # phase_every == 1 recomputes every frame (exact, default).
                phase_every = max(1, int(getattr(settings, "phase_every", 1)))
                drift_count = state.get("_drift_count", 0)
                if "_last_drift" not in state or drift_count % phase_every == 0:
                    dy, dx = phase_cross_correlation_ds(
                        None, frame_f32, downsample=4, _ref_fft=state["ref_fft_ds"]
                    )
                    state["_last_drift"] = (dy, dx)
                else:
                    dy, dx = state["_last_drift"]
                state["_drift_count"] = drift_count + 1
                if abs(dy) > 0.05 or abs(dx) > 0.05:
                    frame_f32 = _gpu_shift(frame_f32, (dy, dx))
                drift_border = (
                    max(0, math.ceil(dy)),
                    max(0, math.ceil(-dy)),
                    max(0, math.ceil(dx)),
                    max(0, math.ceil(-dx)),
                )
            except Exception:
                pass

        # --- Temporal-BG fast path (Option C) ---
        # When bg_mode == "temporal", a single fused kernel does LP + temporal-EMA
        # background removal + difference/contrast, replacing the eager LP / spatial
        # uniform_filter / difference-contrast chain (~5 ops + the 0.67 ms spatial
        # filter) with one alloc-free launch into persistent state buffers. This is
        # the >1000 fps all-features path. The LP/EMA state (prev_in/out, bg) is
        # persistent; the OUTPUT is freshly pooled each frame so it is never aliased
        # by a later frame while the display is still reading processed_frame_gpu.
        temporal_done = False
        if getattr(settings, "bg_mode", "spatial") == "temporal" and GPU_AVAILABLE:
            from Utils.fused_kernels import (
                lp_ema_postprocess, MODE_NORMAL, MODE_DIFFERENCE, MODE_CONTRAST,
            )
            shp = frame_f32.shape
            if state.get("_fused_shape") != shp:
                state["_fused_shape"]    = shp
                state["_fused_prev_in"]  = frame_f32.copy()
                state["_fused_prev_out"] = frame_f32.copy()
                state["_fused_bg"]       = frame_f32.copy()   # seed EMA background
                state["_fused_lp_was_on"] = bool(settings.lp_filter_enabled)
            lp_on = bool(settings.lp_filter_enabled)
            # Re-seed LP state on an off->on transition so the recurrence restarts
            # cleanly (mirrors the eager path resetting prev to None when LP is off).
            if lp_on and not state.get("_fused_lp_was_on", False):
                state["_fused_prev_in"][...]  = frame_f32
                state["_fused_prev_out"][...] = frame_f32
            state["_fused_lp_was_on"] = lp_on
            if lp_on:
                sample_rate = max(float(settings.frame_rate_hz), 1e-6)
                cutoff = float(np.clip(settings.lp_filter_cutoff_hz, 1e-6,
                                       max(1e-6, sample_rate * 0.5 * 0.99)))
                k = float(np.tan(np.pi * cutoff / sample_rate)); norm = 1.0 / (1.0 + k)
                b0, b1, a1 = k * norm, k * norm, (k - 1.0) * norm
            else:
                b0 = b1 = a1 = 0.0
            mode_code = (MODE_CONTRAST if settings.display_mode == "Contrast"
                         else MODE_DIFFERENCE if settings.display_mode == "Difference"
                         else MODE_NORMAL)
            fused_out = xp.empty(shp, dtype=xp.float32)   # fresh per frame (pooled)
            lp_ema_postprocess(
                frame_f32, state["_fused_prev_in"], state["_fused_prev_out"],
                state["_fused_bg"], fused_out,
                lp_enabled=lp_on, b0=b0, b1=b1, a1=a1,
                max_value=float(settings.max_value),
                alpha=float(getattr(settings, "bg_temporal_alpha", 0.02)),
                mode=mode_code,
            )
            frame_f32 = fused_out
            temporal_done = True

        # --- LP Filter ---
        # Filter coefficients are recomputed only when cutoff or frame-rate changes.
        if settings.lp_filter_enabled and not temporal_done:
            lp_key = (settings.lp_filter_cutoff_hz, settings.frame_rate_hz)
            if state.get("_lp_key") != lp_key:
                sample_rate = max(float(settings.frame_rate_hz), 1e-6)
                nyquist     = sample_rate * 0.5
                cutoff = float(np.clip(settings.lp_filter_cutoff_hz, 1e-6,
                                       max(1e-6, nyquist * 0.99)))
                k    = float(np.tan(np.pi * cutoff / sample_rate))
                norm = 1.0 / (1.0 + k)
                state["_lp_key"]    = lp_key
                state["_lp_coeffs"] = (k * norm, k * norm, (k - 1.0) * norm)
            b0, b1, a1 = state["_lp_coeffs"]

            prev_in  = state.get("lp_prev_input")
            prev_out = state.get("lp_prev_output")
            if prev_in is not None and prev_out is not None:
                filtered = xp.clip(
                    b0 * frame_f32 + b1 * prev_in - a1 * prev_out,
                    0.0, float(settings.max_value),
                ).astype(xp.float32)
            else:
                filtered = frame_f32.copy()

            # lp_prev_input saves the pre-filter frame; no copy needed because
            # frame_f32 is immediately rebound to `filtered` and subsequent ops
            # (BG, Diff, crop) all produce new arrays rather than mutating it.
            state["lp_prev_input"]  = frame_f32
            state["lp_prev_output"] = filtered
            frame_f32 = filtered
        else:
            state["lp_prev_input"]  = None
            state["lp_prev_output"] = None

        # --- Background Removal (spatial mode) ---
        # uniform_filter approximates Gaussian in O(N·M) regardless of sigma.
        if settings.bg_removal_enabled and not temporal_done:
            bg = _bg_filter(frame_f32, float(settings.bg_removal_sigma))
            frame_f32 = xp.clip(frame_f32 - bg, 0.0, None)

        # --- Difference / Contrast ---
        # zero_gpu is cached — no per-frame host→device upload.
        if (not temporal_done and settings.display_mode in ("Difference", "Contrast")
                and state.get("zero_gpu") is not None): 
            zero_f = state["zero_gpu"]
            if settings.bg_removal_enabled:
                zero_bg = _bg_filter(zero_f, float(settings.bg_removal_sigma))
                zero_f = xp.clip(zero_f - zero_bg, 0.0, None)
            if settings.display_mode == "Difference":
                frame_f32 = frame_f32 - zero_f
            else:  # Contrast
                frame_f32 = (frame_f32 - zero_f) / (zero_f + 1.0) * 100.0

        # --- Zero-out drifted border (slice-based) ---
        if drift_border is not None:
            top, bottom, left, right = drift_border
            h_fr, w_fr = frame_f32.shape
            if top    > 0: frame_f32[:top, :]             = 0.0
            if bottom > 0: frame_f32[h_fr - bottom:, :]  = 0.0
            if left   > 0: frame_f32[:, :left]            = 0.0
            if right  > 0: frame_f32[:, w_fr - right:]    = 0.0

        # --- Crop (slice-based) ---
        if settings.crop_percent < 100.0:
            h_fr, w_fr = frame_f32.shape[:2]
            frac = float(np.clip(settings.crop_percent, 0.0, 100.0)) / 100.0
            ch   = int(round(h_fr * frac))
            cw   = int(round(w_fr * frac))
            top  = (h_fr - ch) // 2
            left = (w_fr - cw) // 2
            frame_out = xp.zeros((h_fr, w_fr), dtype=xp.float32)
            frame_out[top:top + ch, left:left + cw] = frame_f32[top:top + ch, left:left + cw]
            frame_f32 = frame_out

        # --- ROI Calculation ---
        # Use slice_bounds (rectangular slice) — no GPU boolean mask upload.
        if rois:
            frame_shape = frame_f32.shape[:2]
            for roi in rois:
                if roi._frame_shape != frame_shape:
                    roi.update_mask(frame_shape)
                y1, y2, x1, x2 = roi.slice_bounds
                if y2 > y1 and x2 > x1:
                    value = float(xp.mean(frame_f32[y1:y2, x1:x2]))
                else:
                    value = float("nan")
                roi.plot_x.append(float(len(roi.plot_x)))
                roi.plot_y.append(value)

        # NOTE: The display colormap (autoscale + LUT -> RGBA) is intentionally
        # NOT done here. It is needed only at the display refresh rate (~60 Hz),
        # not at the full capture rate, so it runs lazily in the display path
        # (get_processed_rgba_cpu -> _compute_display_rgba_gpu) on the latest
        # processed_frame_gpu. Keeping it out of this hot path is worth ~0.56 ms
        # per frame at 1200x1200.

        # frame_f32 stays on the GPU. Only callers that explicitly ask for a CPU
        # frame (batch/preview) pay the GPU->CPU transfer here; the live path
        # leaves it on-device and transfers lazily (get_processed_frame_cpu /
        # get_roi_crop_cpu), keeping the per-frame hot path host-transfer-free.
        state["_frame_gpu"] = frame_f32
        if want_cpu_frame:
            result = to_cpu(frame_f32)
            return (result if result.dtype == np.float32 else result.astype(np.float32)), state
        return None, state

    # ------------------------------------------------------------------
    # _process_frame_cpp — GIL-free C++/CUDA temporal path (optional backend)
    # ------------------------------------------------------------------

    def _process_frame_cpp(self, raw_frame, settings, rois, state, want_cpu_frame):
        """Temporal pipeline via the fastproc C++/CUDA engine (GIL released).

        Pipeline: integer drift shift + LP IIR + temporal-EMA background +
        difference/contrast + ROI means, all in one GIL-free call. Drift is
        ESTIMATED in CuPy (phase_every cadence) and applied as an integer shift
        inside the kernel. Numerically equivalent to the CuPy temporal path
        (validated), with integer (not sub-pixel) shift and wraparound edges.
        """
        from Utils.fused_kernels import MODE_NORMAL, MODE_DIFFERENCE, MODE_CONTRAST

        raw = np.asarray(raw_frame)
        if raw.dtype != np.uint16:
            raw = raw.astype(np.uint16)
        if not raw.flags.c_contiguous:
            raw = np.ascontiguousarray(raw)
        H, W = raw.shape[:2]

        backend = state.get("_cpp_backend")
        if backend is None or state.get("_cpp_shape") != (H, W):
            backend = fast_backend.FastBackend(H, W)
            state["_cpp_backend"] = backend
            state["_cpp_shape"] = (H, W)

        # --- Drift estimation (CuPy FFT, phase_every) -> integer shift ---
        shift_x = shift_y = 0
        if settings.drift_correction_enabled:
            zero_id = id(settings.zero_frame) if settings.zero_frame is not None else None
            if state.get("_zero_id") != zero_id:
                state["_zero_id"] = zero_id
                if settings.zero_frame is not None:
                    _ref = np.asarray(settings.zero_frame, dtype=np.float32)
                    state["ref_std_ok"] = float(_ref.std()) >= 1e-6
                    state["ref_fft_ds"] = xp.fft.rfft2(
                        xp.asarray(_ref[::4, ::4], dtype=xp.float64))
                else:
                    state["ref_std_ok"] = False
                    state["ref_fft_ds"] = None
            if state.get("ref_std_ok") and state.get("ref_fft_ds") is not None:
                pe = max(1, int(getattr(settings, "phase_every", 1)))
                cnt = state.get("_drift_count", 0)
                if "_last_drift" not in state or cnt % pe == 0:
                    try:
                        frame_gpu = xp.asarray(raw, dtype=xp.float32)  # upload only for FFT
                        dy, dx = phase_cross_correlation_ds(
                            None, frame_gpu, downsample=4, _ref_fft=state["ref_fft_ds"])
                        state["_last_drift"] = (dy, dx)
                    except Exception:
                        state.setdefault("_last_drift", (0.0, 0.0))
                state["_drift_count"] = cnt + 1
                dy, dx = state.get("_last_drift", (0.0, 0.0))
                shift_y, shift_x = int(round(dy)), int(round(dx))

        # --- LP coefficients (same recurrence as the CuPy path) ---
        lp_on = bool(settings.lp_filter_enabled)
        if lp_on:
            sr = max(float(settings.frame_rate_hz), 1e-6)
            cutoff = float(np.clip(settings.lp_filter_cutoff_hz, 1e-6,
                                   max(1e-6, sr * 0.5 * 0.99)))
            k = float(np.tan(np.pi * cutoff / sr)); norm = 1.0 / (1.0 + k)
            b0, b1, a1 = k * norm, k * norm, (k - 1.0) * norm
        else:
            b0 = b1 = a1 = 0.0

        mode_code = (MODE_CONTRAST if settings.display_mode == "Contrast"
                     else MODE_DIFFERENCE if settings.display_mode == "Difference"
                     else MODE_NORMAL)

        # --- ROI rectangles (int32 N x [y0, y1, x0, x1]) ---
        rects = []
        for roi in rois:
            try:
                if roi._frame_shape != (H, W):
                    roi.update_mask((H, W))   # compute slice_bounds for this shape
                y1, y2, x1, x2 = roi.slice_bounds
                rects.append((int(y1), int(y2), int(x1), int(x2))
                             if (y2 > y1 and x2 > x1) else (0, 0, 0, 0))
            except Exception:
                rects.append((0, 0, 0, 0))
        rects_arr = (np.asarray(rects, dtype=np.int32)
                     if rects else np.zeros((0, 4), dtype=np.int32))

        out_view, means = backend.process(
            raw, shift_x, shift_y, lp_enabled=lp_on, b0=b0, b1=b1, a1=a1,
            max_value=float(settings.max_value),
            alpha=float(getattr(settings, "bg_temporal_alpha", 0.02)),
            mode=mode_code, roi_rects=rects_arr,
        )

        # Mirror the eager ROI block: append one mean per ROI per frame.
        for roi, mean_val in zip(rois, means):
            try:
                roi.plot_x.append(float(len(roi.plot_x)))
                roi.plot_y.append(float(mean_val))
            except Exception:
                pass

        # The engine reuses its output buffer; copy so the display can read a
        # stable frame while the next frame is being processed.
        frame_result = out_view.copy()
        state["_frame_gpu"] = frame_result
        if want_cpu_frame:
            result = to_cpu(frame_result)
            return (result if result.dtype == np.float32 else result.astype(np.float32)), state
        return None, state

    # ------------------------------------------------------------------
    # process_frames — batch processing (used by the preview window)
    # ------------------------------------------------------------------

    def process_frames(self, frame_buffer, settings, rois=None,
                       result_buffer=None, stop_event=None, progress_callback=None):
        """Process every frame in *frame_buffer* through the science pipeline.

        Parameters
        ----------
        frame_buffer      : iterable of raw frames
        settings          : ProcessingSettings
        rois              : list of ProcessingROI (optional; buffers reset at start)
        result_buffer     : numpy array (n, h, w) to store processed frames, or None
        stop_event        : threading.Event — when set, processing halts early
        progress_callback : callable(float 0..1) called after each frame

        Returns
        -------
        True if all frames were processed; False if stopped early.
        """
        if rois is None:
            rois = []

        # Clear ROI buffers so the new trace starts from scratch.
        for roi in rois:
            roi.clear_plot_buffers()

        frames = list(frame_buffer)
        n = len(frames)
        if n == 0:
            return True

        state = {}

        for i, frame in enumerate(frames):
            if stop_event is not None and stop_event.is_set():
                return False

            processed, state = self.process_frame(
                frame, settings, rois, state, want_cpu_frame=True
            )
            state.pop("_frame_gpu", None)

            if result_buffer is not None and i < len(result_buffer):
                result_buffer[i] = processed

            if progress_callback is not None:
                try:
                    progress_callback((i + 1) / max(n, 1))
                except Exception:
                    pass

        return True

    # ------------------------------------------------------------------
    # create_processing_thread — live-feed background worker
    # ------------------------------------------------------------------

    def create_processing_thread(self):
        """Start a background thread that processes each new raw frame.

        The thread waits on ``frame_ready_event``, grabs the latest raw frame
        from ``latest_frame``, runs the full science pipeline via
        ``process_frame`` using ``self.settings`` and ``self.rois``, and stores
        the result in ``processed_frame`` before notifying
        ``processed_frame_condition``.

        Any previously running processing thread is stopped first.
        """
        # Stop any old thread.
        self._processing_thread_stop_event.set()
        self._processing_fps_times.clear()

        stop_event = threading.Event()
        self._processing_thread_stop_event = stop_event

        def _loop():
            state = {}

            while not stop_event.is_set():
                # Engine mode: the capture loop submits frames to the GIL-free
                # engine directly, so this thread has no per-frame work. Idle at a
                # low rate instead of waking on every frame_ready_event — a Python
                # thread spinning at the capture rate (even just to no-op) holds
                # the GIL often enough to starve the UI render loop.
                if self.active_engine is not None:
                    time.sleep(0.05)
                    continue

                fired = self.frame_ready_event.wait(timeout=0.1)
                if not fired:
                    continue
                self.frame_ready_event.clear()

                with self.frame_lock:
                    current_idx = int(self.frameIdx)
                    # No copy: the capture loop rebinds self.latest_frame to a
                    # FRESH array every frame (it never mutates in place), so this
                    # reference stays valid after we release the lock. Skipping the
                    # full-frame CPU copy saves ~0.4 ms/frame at 1200x1200, and
                    # process_frame never mutates the raw input.
                    raw_frame   = self.latest_frame

                try:
                    # Live path: processed frame stays on the GPU (want_cpu_frame
                    # defaults to False). No full-frame host transfer here.
                    _, state = self.process_frame(
                        raw_frame, self.settings, self.rois, state
                    )
                except Exception as exc:
                    print(f"ProcessingThread error: {exc}")
                    continue

                frame_gpu = state.pop("_frame_gpu", None)

                # Track real processed-frame FPS (one append per frame).
                self._processing_fps_times.append(time.time())

                # Publish only the science frame; the display colormap runs lazily
                # at display rate in get_processed_rgba_cpu.
                with self.processed_frame_condition:
                    self.processed_frame_gpu  = frame_gpu
                    self.processed_frame_idx  = current_idx
                    self.processed_frame_condition.notify_all()

        thread = threading.Thread(target=_loop, daemon=True, name="AndorProcessingThread")
        thread.start()

    def stop_processing_thread(self):
        """Signal the processing thread to stop and wait briefly for it to exit."""
        self._processing_thread_stop_event.set()

    # ------------------------------------------------------------------
    # get_processed_rgba_cpu — single display-rate GPU->CPU transfer
    # ------------------------------------------------------------------

    def get_processed_rgba_cpu(self):
        """Return the latest display RGBA as a flat float32 NumPy array.

        The colormap is applied HERE (not in process_frame): the latest science
        frame is kept on the GPU, and only when the display actually asks for a
        new image do we run autoscale + LUT and transfer the result to the host.
        This means the colormap runs at the display refresh rate (~60 Hz), not at
        the full capture rate. Result is cached by frame index so repeated calls
        within one displayed frame neither recolor nor re-transfer.  Returns
        ``None`` when no frame or no colormap LUT is available.

        Acquires ``processed_frame_condition`` internally — callers must not
        already hold it.
        """
        with self.processed_frame_condition:
            gpu = self.processed_frame_gpu
            idx = self.processed_frame_idx
            if gpu is None:
                return None
            if (self._rgba_cpu_cache is not None
                    and self._rgba_cpu_cache_idx == idx):
                return self._rgba_cpu_cache
        # Colormap + transfer OUTSIDE the lock so the processing thread's publish
        # step is never blocked. `gpu` stays valid because the processing thread
        # allocates a fresh frame array per frame and our reference keeps it
        # alive. The pull lock serializes the rare case of the display worker and
        # a render-thread refresh pulling at once.
        with self._rgba_pull_lock:
            rgba_gpu, scale = self._compute_display_rgba_gpu(gpu, self.settings)
            if rgba_gpu is None:
                return None
            # Reused PINNED host buffer: a pageable cupy.asnumpy D2H is ~2x slower
            # and cannot DMA-overlap with compute.
            if GPU_AVAILABLE:
                pinned = self._ensure_rgba_pinned(int(rgba_gpu.size))
                rgba_gpu.reshape(-1).get(out=pinned)
                rgba_cpu = pinned
            else:
                rgba_cpu = to_cpu(rgba_gpu).reshape(-1)
        self.processed_rgba_scale = scale
        with self.processed_frame_condition:
            if self.processed_frame_idx == idx:
                self._rgba_cpu_cache = rgba_cpu
                self._rgba_cpu_cache_idx = idx
        return rgba_cpu

    @staticmethod
    def _compute_display_rgba_gpu(frame_gpu, settings):
        """Map a processed science frame (on the GPU) to a display RGBA image on
        the GPU. Returns ``(rgba_gpu (H,W,4) float32, (min_val, max_val))`` or
        ``(None, (0.0, 1.0))`` when no colormap LUT is set. Runs at display rate.
        """
        lut = settings.colormap_lut_gpu
        if lut is None:
            return None, (0.0, 1.0)
        double_sided = bool(settings.colormap_double_sided)
        if settings.autoscale_enabled:
            data_min = float(xp.min(frame_gpu))
            data_max = float(xp.max(frame_gpu))
            if data_max <= data_min:
                data_max = data_min + 1.0
            grace = settings.autoscale_grace_percent / 100.0
            padding = (data_max - data_min) * grace
            if double_sided:
                min_val = data_min - padding
                max_val = data_max + padding
                if settings.mirrored_difference_scale:
                    amp = max(abs(min_val), abs(max_val), 1e-12)
                    min_val, max_val = -amp, amp
            else:
                min_val = max(0.0, data_min - padding)
                max_val = min(float(settings.max_value), data_max + padding)
        else:
            min_val = float(settings.scale_min)
            max_val = float(settings.scale_max)
            if double_sided and settings.mirrored_difference_scale:
                amp = max(abs(min_val), abs(max_val), 1e-12)
                min_val, max_val = -amp, amp
        if max_val <= min_val:
            max_val = min_val + 1.0

        if double_sided:
            neg_ext = max(abs(min_val), 1e-12) if min_val < 0.0 else 1e12
            pos_ext = max(max_val, 1e-12) if max_val > 0.0 else 1e12
            neg_norm = xp.clip(0.5 + 0.5 * frame_gpu / neg_ext, 0.0, 0.5)
            pos_norm = xp.clip(0.5 + 0.5 * frame_gpu / pos_ext, 0.5, 1.0)
            normalized = xp.where(frame_gpu < 0.0, neg_norm, pos_norm)
        else:
            normalized = xp.clip((frame_gpu - min_val) / (max_val - min_val), 0.0, 1.0)

        n_entries = lut.shape[0]
        indices = xp.clip((normalized * (n_entries - 1)).astype(xp.int32), 0, n_entries - 1)
        # RGBA: DX11 does not reliably support 3-channel 32-bit float
        # (R32G32B32_FLOAT) as a sampleable texture, so an RGB raw texture renders
        # BLACK even though DPG/the docs accept mvFormat_Float_rgb. Use RGBA.
        rgba_gpu = xp.empty((frame_gpu.shape[0], frame_gpu.shape[1], 4), dtype=xp.float32)
        rgba_gpu[..., :3] = lut[indices]
        rgba_gpu[..., 3] = 1.0
        return rgba_gpu, (min_val, max_val)

    def _ensure_rgba_pinned(self, size):
        """Return a reused pinned host float32 buffer of *size* elements.

        Pinned (page-locked) memory lets the RGBA device->host copy run as a
        true async DMA instead of a staged pageable copy. Caller holds
        ``_rgba_pull_lock``.
        """
        if self._rgba_pinned is None or self._rgba_pinned.size != size:
            mem = xp.cuda.alloc_pinned_memory(size * 4)   # 4 bytes per float32
            self._rgba_pinned = np.frombuffer(mem, dtype=np.float32, count=size)
        return self._rgba_pinned

    def get_processed_frame_cpu(self):
        """Return the latest full processed frame as a float32 NumPy array.

        The processed frame is kept on the GPU by the live pipeline; this
        materializes a CPU copy on demand and caches it by frame index so
        repeated reads within one frame do not re-transfer.  Returns ``None``
        when no processed frame is available yet.

        Acquires ``processed_frame_condition`` internally — callers must not
        already hold it.
        """
        with self.processed_frame_condition:
            gpu = self.processed_frame_gpu
            idx = self.processed_frame_idx
            if gpu is None:
                return None
            if (self._frame_cpu_cache is not None
                    and self._frame_cpu_cache_idx == idx):
                return self._frame_cpu_cache
        # Transfer OUTSIDE the lock (see get_processed_rgba_cpu for why).
        frame_cpu = to_cpu(gpu)
        if frame_cpu.dtype != np.float32:
            frame_cpu = frame_cpu.astype(np.float32)
        with self.processed_frame_condition:
            if self.processed_frame_idx == idx:
                self._frame_cpu_cache = frame_cpu
                self._frame_cpu_cache_idx = idx
        return frame_cpu

    def get_roi_crop_cpu(self, y1, y2, x1, x2):
        """Return a CPU float32 crop of the latest processed frame.

        The frame is sliced **on the GPU** and only the (small) crop is
        transferred to the host, so per-ROI updates do not move the whole frame
        across PCIe.  Returns ``None`` when no frame is available or the bounds
        are degenerate.

        Acquires ``processed_frame_condition`` internally — callers must not
        already hold it.
        """
        if y2 <= y1 or x2 <= x1:
            return None
        with self.processed_frame_condition:
            frame_gpu = self.processed_frame_gpu
            if frame_gpu is None:
                return None
            h, w = frame_gpu.shape[:2]
            yy1, yy2 = max(0, int(y1)), min(h, int(y2))
            xx1, xx2 = max(0, int(x1)), min(w, int(x2))
            if yy2 <= yy1 or xx2 <= xx1:
                return None
            view = frame_gpu[yy1:yy2, xx1:xx2]
        # Transfer the crop OUTSIDE the lock (see get_processed_rgba_cpu).
        crop = to_cpu(view)
        return crop if crop.dtype == np.float32 else crop.astype(np.float32)

    # ------------------------------------------------------------------
    # get_processing_fps — real frames-per-second of the processing thread
    # ------------------------------------------------------------------

    def get_processing_fps(self):
        """Return the actual processed-frames-per-second of the live pipeline."""
        engine = self.active_engine
        if engine is not None:
            try:
                return float(engine.processing_fps())
            except Exception:
                return 0.0
        times = list(self._processing_fps_times)
        if len(times) < 2:
            return 0.0
        cutoff = times[-1] - 2.0
        recent = [t for t in times if t >= cutoff]
        if len(recent) < 2:
            return 0.0
        elapsed = recent[-1] - recent[0]
        return 0.0 if elapsed <= 0.0 else float((len(recent) - 1) / elapsed)

