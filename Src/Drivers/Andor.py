
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
        self._capture_fps_times = deque(maxlen=60)

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
        self.scope_frame_mean_channels = ()
        self.scope_frame_mean_capacity = 0
        self.scope_frame_mean_buffers = {}
        self.scope_frame_mean_source = None
        self.scope_frame_mean_calculate_mean = True
        self.scope_frame_mean_last_scope_sample_count = 0
        self.frames_axis = np.zeros((0,), dtype=np.float64)
        self.estimated_time_axis = np.zeros((0,), dtype=np.float64)
        self._configure_display_axes_locked(self.default_max_acquisitions, self.get_frame_rate())

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
        contrast_frame = np.zeros_like(frame_float, dtype=np.float32)
        np.divide(
            difference_frame,
            zero_float,
            out=contrast_frame,
            where=np.abs(zero_float) > 0.0,
        )
        contrast_frame *= 100.0
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
            self.latest_frame = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
            if reset_frame_index:
                self.frameIdx = 0
            self._capture_fps_times.clear()
            self.frame_ready_event.clear()

    def get_capture_loop_fps(self):
        with self.frame_lock:
            if len(self._capture_fps_times) < 2:
                return 0.0
            elapsed = float(self._capture_fps_times[-1] - self._capture_fps_times[0])
            if elapsed <= 0.0:
                return 0.0
            return float((len(self._capture_fps_times) - 1) / elapsed)

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

        self.clear_buffers(reset_frame_index=True)

        # Pre-allocate the buffers
        for _ in range(0, buffer_count):
            buf = np.empty((imgsize,), dtype='B')
            cam.queue(buf, imgsize)

        _consecutive_timeout_count = 0

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
                        if _consecutive_timeout_count >= 6:
                            print("Timed out more than 5 times")
                            break
                        continue
                    raise

                # current_delivery_time = time.time()
                # current_ready_time = getattr(acq, "frame_ready_timestamp", None)
                # if current_ready_time is not None and last_frame_ready_time is not None:
                #     print(
                #         "Time:",
                #         float(current_ready_time) - float(last_frame_ready_time),
                #         "| Delivery:",
                #         current_delivery_time - (last_frame_delivery_time or current_delivery_time),
                #     )
                # else:
                #     print("Time: ", current_delivery_time - (last_frame_delivery_time or current_delivery_time))
                # last_frame_delivery_time = current_delivery_time
                # if current_ready_time is not None:
                    # last_frame_ready_time = float(current_ready_time)

                # Successful frame — reset the consecutive timeout counter
                _consecutive_timeout_count = 0

                # Update the latest frame in a thread-safe manner
                with self.frame_lock:
                    raw_frame = np.asarray(acq.image, dtype=self.sensor_dtype)
                    storage_frame = np.array(raw_frame, dtype=self.raw_storage_dtype, copy=True)

                    # Store the acquisition and timestamp in the buffers
                    frame_timestamp = float(getattr(acq, "frame_ready_timestamp", time.time()))
                    self.acquisitions.append(storage_frame)
                    self.timestamps.append(frame_timestamp)
                    self._capture_fps_times.append(time.time())
                    self.latest_frame = np.array(storage_frame, copy=True)

                    self._append_scope_frame_values_from_source_locked()

                    # Signal that a new frame is ready
                    self.frame_ready_event.set()
                    self.frameIdx += 1

                    # If not in continuous mode and we've reached the max acquisitions, stop
                    if not continuous and self.frameIdx >= self.max_acquisitions:
                        break

                # Re-add this buffer to the queue
                queue_buffer = getattr(acq, "buffer_data", getattr(acq, "_np_data"))
                cam.queue(queue_buffer, imgsize)

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

