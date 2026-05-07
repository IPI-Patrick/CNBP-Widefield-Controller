import threading
import time
import numpy as np

# Return a mock acquisition object
class Acquisition:

    def __init__(self, image, BitDepth, frame_ready_timestamp=None, frame_delivery_timestamp=None):
        self.image = image
        self._np_data = self.image.reshape(-1).view(np.uint8)
        self.BitDepth = BitDepth
        self.frame_ready_timestamp = frame_ready_timestamp
        self.frame_delivery_timestamp = frame_delivery_timestamp

class MockCamera:
    ExposureTime            = 0.01
    FrameRate               = 100.0
    TriggerMode             = "Internal"
    CycleMode               = "Continuous"
    min_AOIWidth            = 1
    max_AOIWidth            = 1024
    AOIWidth                = 1024
    min_AOIHeight           = 1
    max_AOIHeight           = 1024
    AOIHeight               = 1024
    BitDepth                = 16
    options_bitDepth        = [8, 10, 12, 14, 16]
    options_CycleMode       = ["Continuous", "Single"]
    options_TriggerMode     = ["Internal", "Software", "External"]
    options_AOIBinning      = ["1x1", "2x2", "4x4"]
    available_options_TemperatureControl = ["-25", "-20", "-15", "-10", "-5", "0", "5", "10"]
    AOIBinning              = "1x1"
    min_AOILeft             = 1
    AOILeft                 = 1
    min_AOITop              = 1
    AOITop                  = 1
    SoftwareTrigger         = lambda self: ()
    queue                   = lambda self, buf, size: ()

    def __init__(self):
        self._mock_blob_state = None
        self._sensor_cooling = False
        self._temperature_control = "-20"
        self._mock_next_delivery_deadline = None
        self._mock_time_origin_perf = None
        self._mock_time_origin_wall = None
        self._mock_cached_acquisition = None
        self._mock_visual_update_period_seconds = 1.0 / 60.0
        self._mock_cache_lock = threading.Lock()
        self._mock_frame_condition = threading.Condition(self._mock_cache_lock)
        self._mock_render_stop_event = threading.Event()
        self._mock_render_thread = None
        self._mock_last_visual_render_time = None

    def AcquisitionStart(self):
        print("Mocking Starting Acquisition")
        if self._mock_render_thread is not None and self._mock_render_thread.is_alive():
            self.AcquisitionStop()
        with self._mock_frame_condition:
            self._mock_next_delivery_deadline = None
            self._mock_time_origin_perf = None
            self._mock_time_origin_wall = None
            self._mock_cached_acquisition = None
            self._mock_last_visual_render_time = None
        self._mock_render_stop_event = threading.Event()
        self._mock_render_thread = threading.Thread(target=self._mock_render_loop, daemon=True)
        self._mock_render_thread.start()

    def AcquisitionStop(self):
        self._mock_render_stop_event.set()
        if self._mock_render_thread is not None and self._mock_render_thread.is_alive():
            self._mock_render_thread.join(timeout=1.0)
        self._mock_render_thread = None
        with self._mock_frame_condition:
            self._mock_frame_condition.notify_all()
        print("Mocking Stopping Acquisition")

    def flush(self):
        print("Mocking Flushing buffers")

    def _get_requested_frame_period_seconds(self):
        requested_frame_rate_hz = max(float(getattr(self, "FrameRate", 100.0)), 1e-6)
        return 1.0 / requested_frame_rate_hz

    def _wait_until_deadline(self, deadline):
        while True:
            if self._mock_render_stop_event.is_set():
                return False
            remaining_seconds = float(deadline - time.perf_counter())
            if remaining_seconds <= 0.0:
                return True
            if remaining_seconds > 0.003:
                time.sleep(remaining_seconds - 0.001)
            elif remaining_seconds > 0.0005:
                time.sleep(0)
            else:
                while time.perf_counter() < deadline:
                    pass

    def _ensure_mock_blob_state(self):
        height = int(self.AOIHeight)
        width = int(self.AOIWidth)

        state = getattr(self, "_mock_blob_state", None)
        state_is_valid = isinstance(state, dict)
        if state_is_valid:
            state_is_valid = state.get("width") == width and state.get("height") == height

        if state_is_valid:
            return state

        rng = np.random.default_rng(12345)
        blob_count = max(10, min(28, (width * height) // 70000))
        min_sigma = max(3.0, min(width, height) * 0.006)
        max_sigma = max(min_sigma + 1.0, min(width, height) * 0.016)
        blobs = []

        for _ in range(blob_count):
            sigma = float(rng.uniform(min_sigma, max_sigma))
            blobs.append(
                {
                    "x": int(rng.integers(int(4 * sigma), max(int(4 * sigma) + 1, width - int(4 * sigma)))),
                    "y": int(rng.integers(int(4 * sigma), max(int(4 * sigma) + 1, height - int(4 * sigma)))),
                    "sigma": sigma,
                    "base": float(rng.uniform(0.10, 0.30)),
                    "amplitude": float(rng.uniform(0.18, 0.55)),
                    "period": float(rng.uniform(45.0, 180.0)),
                    "phase": float(rng.uniform(0.0, 2.0 * np.pi)),
                    "twinkle": float(rng.uniform(0.08, 0.18)),
                }
            )

        # Add in an extra blob that doesn't grow and shrink
        blobs.append(
            {
                "x": 10,
                "y": 10,
                "sigma": float(rng.uniform(min_sigma, max_sigma)),
                "base": float(rng.uniform(0.30, 0.50)),
                "amplitude": 0.0,
                "period": 60.0,
                "phase": 0.0,
                "twinkle": 0.0,
            }
        )

        state = {
            "width": width,
            "height": height,
            "frame_index": 0,
            "rng": rng,
            "blobs": blobs,
        }
        self._mock_blob_state = state
        return state

    def _render_mock_frame(self):
        height = int(self.AOIHeight)
        width = int(self.AOIWidth)
        max_value = float((2 ** int(self.BitDepth)) - 1)
        background_floor = min(10000.0, max_value)
        dtype = np.dtype(f"u{self.BitDepth // 8}")
        state = self._ensure_mock_blob_state()

        frame_index = state["frame_index"]
        rng = state["rng"]
        frame = np.zeros((height, width), dtype=np.float32)
        frame += background_floor

        drift_speed = 0.5  # 1.0 = original speed; lower values = slower drift
        drift_amplitude_x = max(4, int(min(width, height) * 0.030))
        drift_amplitude_y = max(4, int(min(width, height) * 0.022))
        drift_offset_x = int(round(drift_amplitude_x * np.sin(2.0 * np.pi * frame_index * drift_speed / 300.0)))
        drift_offset_y = int(round(drift_amplitude_y * np.sin(2.0 * np.pi * frame_index * drift_speed / 420.0 + 1.1)))

        for blob in state["blobs"]:
            sigma = blob["sigma"]
            radius = max(3, int(np.ceil(3.5 * sigma)))
            x_center = blob["x"] + drift_offset_x
            y_center = blob["y"] + drift_offset_y

            x_min = max(0, x_center - radius)
            x_max = min(width, x_center + radius + 1)
            y_min = max(0, y_center - radius)
            y_max = min(height, y_center + radius + 1)

            local_x = np.arange(x_min, x_max, dtype=np.float32) - float(x_center)
            local_y = np.arange(y_min, y_max, dtype=np.float32) - float(y_center)
            distance_sq = local_y[:, None] ** 2 + local_x[None, :] ** 2
            gaussian_patch = np.exp(-distance_sq / (2.0 * sigma * sigma), dtype=np.float32)

            phase = (2.0 * np.pi * frame_index / blob["period"]) + blob["phase"]
            modulation = 0.5 + 0.35 * np.sin(phase) + blob["twinkle"] * np.sin((phase * 0.5) + blob["phase"])
            intensity = max_value * np.clip(blob["base"] + (blob["amplitude"] * modulation), 0.03, 0.92)

            frame[y_min:y_max, x_min:x_max] += gaussian_patch * intensity        

        # Shot noise: lower shot_noise_photons = noisier image (good for testing LP filter)
        shot_noise_photons = 300.0
        photons = np.clip(frame * (shot_noise_photons / max_value), 0.0, None)
        frame = rng.poisson(photons).astype(np.float32) * (max_value / shot_noise_photons)
        frame = np.clip(frame, 0.0, max_value).astype(dtype)
        state["frame_index"] += 1
        return Acquisition(frame, self.BitDepth)

    def _mock_render_loop(self):
        next_frame_deadline = time.perf_counter() + self._get_requested_frame_period_seconds()
        while not self._mock_render_stop_event.is_set():
            if not self._wait_until_deadline(next_frame_deadline):
                break

            now = time.perf_counter()

            should_refresh_visual = False
            with self._mock_frame_condition:
                should_refresh_visual = (
                    self._mock_cached_acquisition is None
                    or self._mock_last_visual_render_time is None
                    or (now - self._mock_last_visual_render_time) >= self._mock_visual_update_period_seconds
                )

            if should_refresh_visual:
                acquisition = self._render_mock_frame()
            else:
                with self._mock_frame_condition:
                    acquisition = self._mock_cached_acquisition

            with self._mock_frame_condition:
                self._mock_cached_acquisition = acquisition
                if should_refresh_visual:
                    self._mock_last_visual_render_time = time.perf_counter()
                self._mock_frame_condition.notify_all()

            next_frame_deadline += self._get_requested_frame_period_seconds()

    @property
    def SensorCooling(self):
        return self._sensor_cooling

    @SensorCooling.setter
    def SensorCooling(self, enabled):
        self._sensor_cooling = bool(enabled)

    @property
    def SensorTemperature(self):
        if self._sensor_cooling:
            return float(self._temperature_control)
        return 24.0

    @property
    def TemperatureControl(self):
        return self._temperature_control

    @TemperatureControl.setter
    def TemperatureControl(self, value):
        requested_value = float(value)
        options = [float(option) for option in self.available_options_TemperatureControl]
        nearest_value = min(options, key=lambda option: abs(option - requested_value))
        self._temperature_control = str(int(nearest_value) if nearest_value.is_integer() else nearest_value)

    @property
    def ImageSizeBytes(self):
        return self.AOIWidth * self.AOIHeight * (self.BitDepth // 8)

    def wait_buffer(self, _timeout):
        timeout_seconds = None if _timeout is None else max(float(_timeout) / 1000.0, 0.0)
        timeout_deadline = None if timeout_seconds is None else (time.perf_counter() + timeout_seconds)

        with self._mock_frame_condition:
            while self._mock_cached_acquisition is None:
                if self._mock_render_stop_event.is_set():
                    raise TimeoutError("Mock camera acquisition stopped before the first frame was available")

                if timeout_deadline is None:
                    self._mock_frame_condition.wait()
                    continue

                remaining_seconds = timeout_deadline - time.perf_counter()
                if remaining_seconds <= 0.0:
                    raise TimeoutError("Mock camera frame buffer was not populated before wait_buffer timeout")
                self._mock_frame_condition.wait(remaining_seconds)

        now = time.perf_counter()
        frame_period_seconds = self._get_requested_frame_period_seconds()
        if self._mock_next_delivery_deadline is None:
            self._mock_time_origin_perf = now
            self._mock_time_origin_wall = time.time()
            self._mock_next_delivery_deadline = now + frame_period_seconds
        scheduled_delivery_deadline = float(self._mock_next_delivery_deadline)
        if not self._wait_until_deadline(scheduled_delivery_deadline):
            raise TimeoutError("Mock camera acquisition stopped before the next frame delivery deadline")
        self._mock_next_delivery_deadline += frame_period_seconds

        with self._mock_frame_condition:
            cached_acquisition = self._mock_cached_acquisition

        if cached_acquisition is None:
            raise TimeoutError("Mock camera frame buffer was not populated before wait_buffer timeout")

        scheduled_wall_timestamp = float(self._mock_time_origin_wall + (scheduled_delivery_deadline - self._mock_time_origin_perf))
        delivery_timestamp = time.time()
        return Acquisition(
            cached_acquisition.image,
            self.BitDepth,
            frame_ready_timestamp=scheduled_wall_timestamp,
            frame_delivery_timestamp=delivery_timestamp,
        )
