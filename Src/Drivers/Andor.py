
import time
import numpy as np
import threading
from pyAndorSDK3 import AndorSDK3
from Mocks.MockCamera import MockCamera 
from Utils.StorageDTypes import (
    SUPPORTED_FLOAT_STORAGE_DTYPES,
    canonicalize_float_storage_dtype_name,
    get_float_storage_dtype,
    quantize_to_float_storage_dtype,
)
from Utils.TypedDeque import TypedDeque


SUPPORTED_STORAGE_DTYPES = SUPPORTED_FLOAT_STORAGE_DTYPES

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
        frame_dtype             = np.dtype(f'u{self.camera.BitDepth//8}')
        self.frame_shape        = frame_shape
        self.sensor_dtype       = frame_dtype
        self.frame_max_value    = float((2 ** int(self.camera.BitDepth)) - 1)
        self.storage_dtype_name = "float16"
        self.storage_dtype      = get_float_storage_dtype(self.storage_dtype_name)
        self.acquisitions       = self._new_frame_buffer()
        self.filtered           = self._new_frame_buffer()
        self.difference         = self._new_frame_buffer()
        self.contrast           = self._new_frame_buffer()
        self.timestamps         = self._new_scalar_buffer(np.float64)
        self.meanBuffer         = self._new_scalar_buffer(np.float64)
        self.zero               = np.zeros(frame_shape, dtype=self.storage_dtype)
        self.latest_frame       = np.zeros(frame_shape, dtype=self.storage_dtype)
        self.latest_filtered    = np.zeros(frame_shape, dtype=self.storage_dtype)
        self.latest_difference  = np.zeros(frame_shape, dtype=self.storage_dtype)
        self.latest_contrast    = np.zeros(frame_shape, dtype=self.storage_dtype)
        self.lp_filter_enabled  = False
        self.lp_filter_cutoff_hz = min(10.0, max(0.5, self.get_frame_rate() * 0.1))
        self.zero_version       = 0

    def _new_frame_buffer(self, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=self.storage_dtype, shape=self.frame_shape)

    def _new_scalar_buffer(self, dtype, iterable=None):
        return TypedDeque(iterable, maxlen=self.max_acquisitions, dtype=dtype, shape=())

    def _coerce_frame_to_storage(self, frame):
        return quantize_to_float_storage_dtype(frame, self.storage_dtype_name)

    def _empty_storage_frame(self):
        return np.zeros(self.frame_shape, dtype=self.storage_dtype)

    def set_storage_dtype(self, dtype_name):
        normalized_dtype_name = canonicalize_float_storage_dtype_name(dtype_name)
        normalized_dtype = get_float_storage_dtype(normalized_dtype_name)
        if normalized_dtype_name not in SUPPORTED_STORAGE_DTYPES:
            supported = ", ".join(SUPPORTED_STORAGE_DTYPES)
            raise ValueError(f"Unsupported storage dtype '{dtype_name}'. Supported values: {supported}")

        with self.frame_lock:
            if normalized_dtype_name == self.storage_dtype_name:
                return
            existing_acquisitions = [
                np.asarray(quantize_to_float_storage_dtype(frame, normalized_dtype_name), dtype=normalized_dtype)
                for frame in self.acquisitions
            ]
            self.storage_dtype = normalized_dtype
            self.storage_dtype_name = normalized_dtype_name
            self.acquisitions = self._new_frame_buffer(existing_acquisitions)
            self.zero = self._coerce_frame_to_storage(self.zero)
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
            return self._empty_storage_frame()

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

        return self._coerce_frame_to_storage(filtered_output)

    def _compute_difference_frame(self, frame):
        difference_frame = np.asarray(frame, dtype=np.float32) - np.asarray(self.zero, dtype=np.float32)
        return self._coerce_frame_to_storage(difference_frame)

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
        return self._coerce_frame_to_storage(contrast_frame)

    def _rebuild_processed_buffers_locked(self):
        if len(self.acquisitions) == 0:
            self.filtered = self._new_frame_buffer()
            self.latest_filtered = self._empty_storage_frame()
            self.difference = self._new_frame_buffer()
            self.contrast = self._new_frame_buffer()
            self.latest_difference = self._empty_storage_frame()
            self.latest_contrast = self._empty_storage_frame()
            return

        if self.lp_filter_enabled:
            coefficients = self._get_lp_filter_coefficients_locked()
            previous_input = None
            previous_output = None
            filtered_frames = []

            for frame in self.acquisitions:
                current_input = np.asarray(frame, dtype=np.float32)
                filtered_output = self._apply_lp_filter_step(current_input, previous_input, previous_output, coefficients)
                filtered_frames.append(np.array(self._coerce_frame_to_storage(filtered_output), copy=True))
                previous_input = current_input
                previous_output = filtered_output

            self.filtered = self._new_frame_buffer(filtered_frames)
        else:
            self.filtered = self._new_frame_buffer([np.array(frame, copy=True) for frame in self.acquisitions])

        self.latest_filtered = np.array(self.filtered[-1], copy=True)
        processing_frames = self.filtered if self.lp_filter_enabled else self.acquisitions
        self.difference = self._new_frame_buffer([self._compute_difference_frame(frame) for frame in processing_frames])
        self.contrast = self._new_frame_buffer([self._compute_contrast_frame(frame) for frame in processing_frames])
        self.latest_frame = np.array(self.acquisitions[-1], copy=True)
        self.latest_difference = np.array(self.difference[-1], copy=True)
        self.latest_contrast = np.array(self.contrast[-1], copy=True)

    def set_zero_frame(self, frame):
        if frame is None:
            return

        with self.frame_lock:
            self.zero = np.array(self._coerce_frame_to_storage(frame), copy=True)
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
            self.acquisitions = self._new_frame_buffer()
            self.filtered = self._new_frame_buffer()
            self.difference = self._new_frame_buffer()
            self.contrast = self._new_frame_buffer()
            self.timestamps = self._new_scalar_buffer(np.float64)
            self.meanBuffer = self._new_scalar_buffer(np.float64)
            if zero_shape_changed:
                self.zero = np.zeros(frame_shape, dtype=self.storage_dtype)
                self.zero_version = 0
            self.latest_frame = np.zeros(frame_shape, dtype=self.storage_dtype)
            self.latest_filtered = np.zeros(frame_shape, dtype=self.storage_dtype)
            self.latest_difference = np.zeros(frame_shape, dtype=self.storage_dtype)
            self.latest_contrast = np.zeros(frame_shape, dtype=self.storage_dtype)
            if reset_frame_index:
                self.frameIdx = 0
            self.frame_ready_event.clear()

    def set_preview_max_frames(self, frame_count):
        frame_count = max(1, int(frame_count))
        self.default_max_acquisitions = frame_count
        if not self.is_capturing:
            self.max_acquisitions = frame_count

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


                # Update the latest frame in a thread-safe manner
                with self.frame_lock:
                    raw_frame = np.asarray(acq.image, dtype=self.sensor_dtype)
                    storage_frame = np.array(self._coerce_frame_to_storage(raw_frame), copy=True)

                    # Store the acquisition and timestamp in the buffers
                    self.acquisitions.append(storage_frame)
                    self.timestamps.append(time.time())
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
                        filtered_frame = np.array(self._coerce_frame_to_storage(filtered_output), copy=True)
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

