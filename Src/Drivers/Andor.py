
import time
import numpy as np
import threading
from collections import deque
from pyAndorSDK3 import AndorSDK3
from Mocks.MockCamera import MockCamera 
from Utils.StorageDTypes import (
    SUPPORTED_STORAGE_DTYPES,
    canonicalize_storage_bit_depth_name,
    get_raw_storage_dtype,
    get_signed_storage_dtype,
    quantize_to_raw_storage_dtype,
    quantize_to_signed_storage_dtype,
)
from Utils.TypedDeque import TypedDeque


class Andor:

    max_acquisitions            = 200    
    acquisitions                = None
    filtered                    = None
    difference                  = None
    contrast                    = None
    timestamps                  = None
    frameIdx                    = 0

    meanBuffer                  = None

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
        self.filtered           = self._new_raw_frame_buffer()
        self.difference         = self._new_signed_frame_buffer()
        self.contrast           = self._new_signed_frame_buffer()
        self.timestamps         = self._new_scalar_buffer(np.float64)
        self.meanBuffer         = self._new_scalar_buffer(np.float64)
        self.zero               = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
        self.latest_frame       = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
        self.latest_filtered    = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
        self.latest_difference  = np.zeros(frame_shape, dtype=self.signed_storage_dtype)
        self.latest_contrast    = np.zeros(frame_shape, dtype=self.signed_storage_dtype)
        self.lp_filter_enabled  = False
        self.lp_filter_cutoff_hz = min(10.0, max(0.5, self.get_frame_rate() * 0.1))
        self.zero_version       = 0
        self.scope_frame_mean_channels = ()
        self.scope_frame_mean_capacity = 0
        self.scope_frame_mean_buffers = {}
        self.scope_frame_mean_source = None
        self.scope_frame_mean_calculate_mean = True
        self.scope_frame_mean_last_scope_sample_count = 0

    def _new_raw_frame_buffer(self, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=self.raw_storage_dtype, shape=self.frame_shape)

    def _new_signed_frame_buffer(self, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=self.signed_storage_dtype, shape=self.frame_shape)

    def _new_scalar_buffer(self, dtype, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=dtype, shape=())

    def _new_scope_frame_mean_buffers(self):
        return {
            channel_name: TypedDeque(maxlen=self.scope_frame_mean_capacity, dtype=np.float16, shape=())
            for channel_name in self.scope_frame_mean_channels
        }

    def _coerce_raw_frame_to_storage(self, frame):
        return quantize_to_raw_storage_dtype(frame, self.storage_dtype_name, source_max_value=self.frame_max_value)

    def _coerce_signed_frame_to_storage(self, frame):
        return quantize_to_signed_storage_dtype(frame, self.storage_dtype_name)

    def _empty_raw_storage_frame(self):
        return np.zeros(self.frame_shape, dtype=self.raw_storage_dtype)

    def _empty_signed_storage_frame(self):
        return np.zeros(self.frame_shape, dtype=self.signed_storage_dtype)

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

    def set_storage_dtype(self, dtype_name):
        normalized_dtype_name = canonicalize_storage_bit_depth_name(dtype_name)
        normalized_raw_dtype = get_raw_storage_dtype(normalized_dtype_name)
        normalized_signed_dtype = get_signed_storage_dtype(normalized_dtype_name)
        if normalized_dtype_name not in SUPPORTED_STORAGE_DTYPES:
            supported = ", ".join(SUPPORTED_STORAGE_DTYPES)
            raise ValueError(f"Unsupported storage dtype '{dtype_name}'. Supported values: {supported}")

        with self.frame_lock:
            if normalized_dtype_name == self.storage_dtype_name:
                return
            existing_acquisitions = [
                np.asarray(
                    quantize_to_raw_storage_dtype(frame, normalized_dtype_name, source_max_value=self.frame_max_value),
                    dtype=normalized_raw_dtype,
                )
                for frame in self.acquisitions
            ]
            self.raw_storage_dtype = normalized_raw_dtype
            self.signed_storage_dtype = normalized_signed_dtype
            self.storage_dtype = self.raw_storage_dtype
            self.storage_dtype_name = normalized_dtype_name
            self.acquisitions = self._new_raw_frame_buffer(existing_acquisitions)
            self.zero = self._coerce_raw_frame_to_storage(self.zero)
            self._rebuild_processed_buffers_locked()
            self.frame_ready_event.set()

    def _get_lp_filter_coefficients_locked(self):
        sample_rate_hz = max(float(self.get_frame_rate()), 1e-6)
        nyquist_hz = sample_rate_hz * 0.5
        cutoff_hz = float(np.clip(self.lp_filter_cutoff_hz, 1e-6, max(1e-6, nyquist_hz * 0.99)))
        k = float(np.tan(np.pi * cutoff_hz / sample_rate_hz))
        norm = 1.0 / (1.0 + k)
        return (k * norm), (k * norm), ((k - 1.0) * norm)

    def _apply_lp_filter_step(self, current_input, previous_input, previous_output, coefficients):
        if previous_input is None or previous_output is None:
            return np.array(current_input, dtype=np.float32, copy=True)

        b0, b1, a1 = coefficients
        filtered = (b0 * current_input) + (b1 * previous_input) - (a1 * previous_output)
        return np.clip(filtered, 0.0, self.frame_max_value).astype(np.float32, copy=False)

    def _compute_latest_filtered_locked(self):
        if len(self.acquisitions) == 0:
            return self._empty_raw_storage_frame()

        if not self.lp_filter_enabled:
            return np.array(self.acquisitions[-1], copy=True)

        coefficients = self._get_lp_filter_coefficients_locked()
        previous_input = None
        previous_output = None
        filtered_output = None

        for frame in self.acquisitions:
            current_input = np.asarray(frame, dtype=np.float32)
            filtered_output = self._apply_lp_filter_step(current_input, previous_input, previous_output, coefficients)
            previous_input = current_input
            previous_output = filtered_output

        return self._coerce_raw_frame_to_storage(filtered_output)

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

    def _rebuild_processed_buffers_locked(self):
        if len(self.acquisitions) == 0:
            self.filtered = self._new_raw_frame_buffer()
            self.latest_filtered = self._empty_raw_storage_frame()
            self.difference = self._new_signed_frame_buffer()
            self.contrast = self._new_signed_frame_buffer()
            self.latest_difference = self._empty_signed_storage_frame()
            self.latest_contrast = self._empty_signed_storage_frame()
            return

        if self.lp_filter_enabled:
            coefficients = self._get_lp_filter_coefficients_locked()
            previous_input = None
            previous_output = None
            filtered_frames = []

            for frame in self.acquisitions:
                current_input = np.asarray(frame, dtype=np.float32)
                filtered_output = self._apply_lp_filter_step(current_input, previous_input, previous_output, coefficients)
                filtered_frames.append(np.array(self._coerce_raw_frame_to_storage(filtered_output), copy=True))
                previous_input = current_input
                previous_output = filtered_output

            self.filtered = self._new_raw_frame_buffer(filtered_frames)
        else:
            self.filtered = self._new_raw_frame_buffer([np.array(frame, copy=True) for frame in self.acquisitions])

        self.latest_filtered = np.array(self.filtered[-1], copy=True)
        processing_frames = self.filtered if self.lp_filter_enabled else self.acquisitions
        self.difference = self._new_signed_frame_buffer([self._compute_difference_frame(frame) for frame in processing_frames])
        self.contrast = self._new_signed_frame_buffer([self._compute_contrast_frame(frame) for frame in processing_frames])
        self.latest_frame = np.array(self.acquisitions[-1], copy=True)
        self.latest_difference = np.array(self.difference[-1], copy=True)
        self.latest_contrast = np.array(self.contrast[-1], copy=True)

    def set_zero_frame(self, frame):
        if frame is None:
            return

        with self.frame_lock:
            self.zero = np.array(self._coerce_raw_frame_to_storage(frame), copy=True)
            self.zero_version += 1
            self._rebuild_processed_buffers_locked()
            self.frame_ready_event.set()

    def set_lp_filter_enabled(self, enabled):
        with self.frame_lock:
            self.lp_filter_enabled = bool(enabled)
            self._rebuild_processed_buffers_locked()
            self.frame_ready_event.set()

    def set_lp_filter_cutoff_hz(self, cutoff_hz):
        cutoff_hz = max(1e-3, float(cutoff_hz))
        with self.frame_lock:
            self.lp_filter_cutoff_hz = cutoff_hz
            self._rebuild_processed_buffers_locked()
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
            self._rebuild_processed_buffers_locked()
            self.frame_ready_event.set()

    def clear_buffers(self, *, reset_frame_index=True):
        frame_shape = (int(self.camera.AOIHeight), int(self.camera.AOIWidth))
        zero_shape_changed = tuple(np.shape(self.zero)) != tuple(frame_shape)
        self.frame_shape = frame_shape
        with self.frame_lock:
            self.acquisitions = self._new_raw_frame_buffer()
            self.filtered = self._new_raw_frame_buffer()
            self.difference = self._new_signed_frame_buffer()
            self.contrast = self._new_signed_frame_buffer()
            self.timestamps = self._new_scalar_buffer(np.float64)
            self.meanBuffer = self._new_scalar_buffer(np.float64)
            self.scope_frame_mean_buffers = self._new_scope_frame_mean_buffers()
            self.scope_frame_mean_last_scope_sample_count = 0
            if zero_shape_changed:
                self.zero = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
                self.zero_version = 0
            self.latest_frame = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
            self.latest_filtered = np.zeros(frame_shape, dtype=self.raw_storage_dtype)
            self.latest_difference = np.zeros(frame_shape, dtype=self.signed_storage_dtype)
            self.latest_contrast = np.zeros(frame_shape, dtype=self.signed_storage_dtype)
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
                "filtered": [np.array(frame, copy=True) for frame in self.filtered],
                "difference": [np.array(frame, copy=True) for frame in self.difference],
                "contrast": [np.array(frame, copy=True) for frame in self.contrast],
                "timestamps": list(self.timestamps),
                "mean_buffer": list(self.meanBuffer),
                "frame_index": int(self.frameIdx),
                "zero": np.array(self.zero, copy=True),
                "latest_frame": np.array(self.latest_frame, copy=True),
                "latest_filtered": np.array(self.latest_filtered, copy=True),
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

        try:
            cam.AcquisitionStart()
            previous_input = None
            previous_output = None
            lp_filter_was_enabled = False
            while True:            

                # If using software trigger, trigger it
                if soft_trigger:
                    cam.SoftwareTrigger()

                # Wait until the next frame is ready in the buffer
                acq = cam.wait_buffer(timeout)

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

                # Update the latest frame in a thread-safe manner
                with self.frame_lock:
                    raw_frame = np.asarray(acq.image, dtype=self.sensor_dtype)
                    storage_frame = np.array(self._coerce_raw_frame_to_storage(raw_frame), copy=True)

                    # Store the acquisition and timestamp in the buffers
                    frame_timestamp = float(getattr(acq, "frame_ready_timestamp", time.time()))
                    self.acquisitions.append(storage_frame)
                    self.timestamps.append(frame_timestamp)
                    self._capture_fps_times.append(time.time())
                    self.latest_frame = np.array(storage_frame, copy=True)

                    lp_filter_enabled = bool(self.lp_filter_enabled)
                    if lp_filter_enabled != lp_filter_was_enabled:
                        previous_input = None
                        previous_output = None
                        lp_filter_was_enabled = lp_filter_enabled

                    source_frame = self.latest_frame
                    if lp_filter_enabled:
                        coefficients = self._get_lp_filter_coefficients_locked()
                        current_input = np.asarray(raw_frame, dtype=np.float32)
                        filtered_output = self._apply_lp_filter_step(current_input, previous_input, previous_output, coefficients)
                        filtered_frame = np.array(self._coerce_raw_frame_to_storage(filtered_output), copy=True)
                        self.filtered.append(filtered_frame)
                        self.latest_filtered = np.array(filtered_frame, copy=True)
                        previous_input = current_input
                        previous_output = filtered_output
                        source_frame = self.latest_filtered
                    else:
                        self.latest_filtered = np.array(self.latest_frame, copy=True)
                        self.filtered.append(np.array(self.latest_filtered, copy=True))

                    processed_difference_frame = self._compute_difference_frame(source_frame)
                    processed_contrast_frame = self._compute_contrast_frame(source_frame)
                    self.difference.append(np.array(processed_difference_frame, copy=True))
                    self.contrast.append(np.array(processed_contrast_frame, copy=True))
                    self.latest_difference = np.array(processed_difference_frame, copy=True)
                    self.latest_contrast = np.array(processed_contrast_frame, copy=True)

                    # calculate the mean intensity and update the mean buffer
                    self.meanBuffer.append(float(np.mean(raw_frame, dtype=np.float64)))
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
        self.start_capture(continuous=False, callback=callback)

    def stop_capture(self):
        # Stop the capture thread
        self.stop_capture_event.set()
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        self.capture_thread = None

