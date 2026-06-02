import math
import threading
import time
import numpy as np

from skimage.draw import polygon as skimage_polygon # pylint: disable=no-name-in-module

import Utils.shared_state as shared_state

# Stage-to-image scale factors (dev-mode only — positions in SI metres)
# 34 304 000 steps/m ÷ 200 steps/pixel = 171 520 pixels per metre.
_STAGE_PX_PER_M = 100.0
# Each metre of Z defocus adds this much Gaussian blur sigma.
# At ±0.3 mm → ~+1 σ; at ±3 mm → ~+10 σ (heavy defocus).
_Z_SIGMA_PER_M = 10.0

class Acquisition:

    def __init__(self, image, BitDepth, frame_ready_timestamp=None, frame_delivery_timestamp=None):
        self.image = image
        self._np_data = self.image.reshape(-1).view(np.uint8)
        self.BitDepth = BitDepth
        self.frame_ready_timestamp = frame_ready_timestamp
        self.frame_delivery_timestamp = frame_delivery_timestamp


class MockCamera:
    ExposureTime            = 0.01
    min_ExposureTime        = 0.000028
    max_ExposureTime        = 30.0
    FrameRate               = 100.0
    min_FrameRate           = 0.001
    max_FrameRate           = 1000.0
    TriggerMode             = "Internal"
    CycleMode               = "Continuous"
    min_AOIWidth            = 1
    max_AOIWidth            = 2048
    AOIWidth                = 2048
    min_AOIHeight           = 1
    max_AOIHeight           = 2048
    AOIHeight               = 2048
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
        self._sensor_cooling = False
        self._temperature_control = "-20"
        self._mock_next_delivery_deadline = None
        self._mock_time_origin_perf = None
        self._mock_time_origin_wall = None
        self._mock_cached_acquisition = None
        # How often the animated frame CONTENT is regenerated (wall-clock),
        # decoupled from the exposure/delivery rate. Frames are re-exposed by
        # wait_buffer at the full FrameRate (up to max_FrameRate) regardless;
        # this only controls visual animation cadence. Kept modest so the
        # (relatively expensive) render does not dominate at high frame rates.
        self._mock_visual_update_period_seconds = 0.1
        self._mock_cache_lock = threading.Lock()
        self._mock_frame_condition = threading.Condition(self._mock_cache_lock)
        self._mock_render_stop_event = threading.Event()
        self._mock_render_thread = None
        self._mock_last_visual_render_time = None

        # Externally-settable simulation parameters
        self.focus_sigma = 0.8          # Gaussian blur sigma for defocus (0 = sharp)
        self.num_particles = 20
        self.particle_min_radius = 2.0  # px
        self.particle_max_radius = 4.0  # px
        self.particle_mean = 0.15       # mean resting intensity (fraction of max)
        self.particle_std = 0.10        # std dev of per-particle base intensity
        self.particle_amplitude = 0.35  # oscillation amplitude (fraction of max)

        # Gaussian illumination profile (fixed in image space, independent of drift/translation)
        self.illum_enabled = True
        self.illum_center_x_frac = 0.5  # 0 = left edge, 1 = right edge
        self.illum_center_y_frac = 0.5  # 0 = top edge, 1 = bottom edge
        self.illum_sigma = 300.0        # px — controls how wide the illumination falls off
        self.illum_peak = 0.3           # peak intensity as fraction of max sensor value

        # Sinusoidal drift simulation
        self.drift_enabled = True
        self.drift_speed = 0.5          # arbitrary frequency units
        self.drift_amplitude = 20.0     # peak displacement in px (same for X and Y)

        # Manual sample translation (added on top of sinusoidal drift)
        self.translation_x = 0          # px
        self.translation_y = 0          # px

        # Global brightness pulse — all particles share this slow sinusoidal multiplier
        self.global_pulse_period = 3.0  # seconds (real-time, independent of frame rate)
        self.global_pulse_amplitude = 0.5  # multiplier swings between 1-amp and 1+amp

        self._mock_particle_state = None  # invalidated when structural params change
        self._illum_cache = None          # cached 2-D Gaussian array
        self._illum_cache_key = None

        # Fiducial rectangles — move with the sample (drift + manual translation)
        # but are NOT affected by the global brightness pulse or per-frame oscillation.
        # Four small squares placed symmetrically around the image centre at ±offset_frac.
        self.fiducial_enabled = True
        self.fiducial_size = 6            # side length in pixels (at native 1×1 binning)
        self.fiducial_offset_frac = 0.20  # fraction of image width/height from centre
        self.fiducial_intensity = 0.60    # fixed intensity as fraction of max sensor value

    # ── Particle regeneration ───────────────────────────────────────────────────

    def regenerate_particles(self):
        """Fully rebuild all particles with new random positions and properties."""
        rng = np.random.default_rng()
        height = int(self.AOIHeight)
        width = int(self.AOIWidth)
        num_particles = int(self.num_particles)
        p_min_r = float(self.particle_min_radius)
        p_max_r = float(max(self.particle_min_radius, self.particle_max_radius))
        margin = int(math.ceil(p_max_r * 3.5)) + 8

        particles = []
        for _ in range(num_particles):
            radius = float(rng.uniform(p_min_r, p_max_r))
            mask = self._generate_particle_mask(rng, radius)
            x = int(rng.integers(margin, max(margin + 1, width - margin)))
            y = int(rng.integers(margin, max(margin + 1, height - margin)))
            particles.append({
                "mask": mask,
                "x": x,
                "y": y,
                "base_val": float(np.clip(rng.normal(self.particle_mean, self.particle_std), 0.0, 1.0)),
                "period": float(rng.uniform(45.0, 180.0)),
                "phase": float(rng.uniform(0.0, 2.0 * np.pi)),
            })

        ref_radius = float(rng.uniform(p_min_r, p_max_r))
        ref_mask = self._generate_particle_mask(rng, ref_radius)
        particles.append({
            "mask": ref_mask,
            "x": margin,
            "y": margin,
            "base_val": float(np.clip(self.particle_mean + self.particle_std, 0.0, 1.0)),
            "period": 60.0,
            "phase": 0.0,
        })

        self._mock_particle_state = {
            "width": width,
            "height": height,
            "num_particles": num_particles,
            "p_min_r": p_min_r,
            "p_max_r": p_max_r,
            "frame_index": 0,
            "rng": rng,
            "particles": particles,
        }

    def rerandomize_particle_properties(self):
        """Keep particle positions; re-randomize masks and intensity params.

        If the particle count changed, new particles are added at random positions
        or excess particles are trimmed from the end.  If no state exists yet the
        call is a no-op — _ensure_particle_state will build fresh state on the
        first rendered frame.
        """
        state = self._mock_particle_state
        if state is None:
            return

        rng = np.random.default_rng()
        num_particles = int(self.num_particles)
        p_min_r = float(self.particle_min_radius)
        p_max_r = float(max(self.particle_min_radius, self.particle_max_radius))
        height = state["height"]
        width = state["width"]
        margin = int(math.ceil(p_max_r * 3.5)) + 8

        existing = state["particles"]
        ref = existing[-1] if existing else None
        working = list(existing[:-1]) if existing else []

        while len(working) < num_particles:
            x = int(rng.integers(margin, max(margin + 1, width - margin)))
            y = int(rng.integers(margin, max(margin + 1, height - margin)))
            working.append({"x": x, "y": y})
        working = working[:num_particles]

        new_particles = []
        for p in working:
            radius = float(rng.uniform(p_min_r, p_max_r))
            mask = self._generate_particle_mask(rng, radius)
            new_particles.append({
                "mask": mask,
                "x": p["x"],
                "y": p["y"],
                "base_val": float(np.clip(rng.normal(self.particle_mean, self.particle_std), 0.0, 1.0)),
                "period": float(rng.uniform(45.0, 180.0)),
                "phase": float(rng.uniform(0.0, 2.0 * np.pi)),
            })

        ref_pos = (ref["x"], ref["y"]) if ref is not None else (margin, margin)
        ref_radius = float(rng.uniform(p_min_r, p_max_r))
        ref_mask = self._generate_particle_mask(rng, ref_radius)
        new_particles.append({
            "mask": ref_mask,
            "x": ref_pos[0],
            "y": ref_pos[1],
            "base_val": float(np.clip(self.particle_mean + self.particle_std, 0.0, 1.0)),
            "period": 60.0,
            "phase": 0.0,
        })

        new_state = dict(state)
        new_state.update({
            "particles": new_particles,
            "num_particles": num_particles,
            "p_min_r": p_min_r,
            "p_max_r": p_max_r,
            "rng": rng,
        })
        self._mock_particle_state = new_state

    # ── Structural params ───────────────────────────────────────────────────────

    def set_num_particles(self, value):
        self.num_particles = max(1, int(value))
        self.rerandomize_particle_properties()

    def set_particle_min_radius(self, value):
        self.particle_min_radius = max(1.0, float(value))
        self.rerandomize_particle_properties()

    def set_particle_max_radius(self, value):
        self.particle_max_radius = max(self.particle_min_radius, float(value))
        self.rerandomize_particle_properties()

    # ── Acquisition start / stop ────────────────────────────────────────────────

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

    # ── Internal helpers ────────────────────────────────────────────────────────

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
            if remaining_seconds > 0.0015:
                time.sleep(remaining_seconds - 0.001)
            else:
                # Near the deadline, yield the GIL with sleep(0) rather than a
                # `pass` busy-spin. A pure-Python `pass` spin HOLDS the GIL, so at
                # high frame rates it starved the consumer threads (capture /
                # processing) and capped the achievable rate. sleep(0) releases
                # the GIL each iteration while still pacing tightly enough for a
                # dev mock.
                time.sleep(0)

    @staticmethod
    def _generate_particle_mask(rng, radius):
        """Return a float32 mask of an irregular polygon approximating the given radius."""
        size = int(math.ceil(radius * 2.5))
        canvas_size = max(5, size * 2 + 3)
        cx = cy = canvas_size // 2

        n_vertices = int(rng.integers(5, 9))
        angles = np.sort(rng.uniform(0.0, 2.0 * np.pi, n_vertices))
        radii = rng.uniform(radius * 0.45, radius * 0.95, n_vertices)

        ys = (cy + radii * np.sin(angles)).astype(np.float64)
        xs = (cx + radii * np.cos(angles)).astype(np.float64)

        canvas = np.zeros((canvas_size, canvas_size), dtype=np.float32)
        rr, cc = skimage_polygon(ys, xs, shape=canvas.shape)
        canvas[rr, cc] = 1.0
        return canvas

    def _ensure_particle_state(self):
        height = int(self.AOIHeight)
        width = int(self.AOIWidth)

        state = self._mock_particle_state
        if isinstance(state, dict):
            if state["width"] == width and state["height"] == height:
                return state

        self.regenerate_particles()
        return self._mock_particle_state

    def _get_illumination_gaussian(self, height, width):
        if not self.illum_enabled:
            return None
        key = (height, width, self.illum_center_x_frac, self.illum_center_y_frac,
               self.illum_sigma, self.illum_peak, self.BitDepth)
        if self._illum_cache_key == key and self._illum_cache is not None:
            return self._illum_cache
        max_value = float((2 ** int(self.BitDepth)) - 1)
        cx = float(self.illum_center_x_frac) * (width - 1)
        cy = float(self.illum_center_y_frac) * (height - 1)
        sigma = max(1.0, float(self.illum_sigma))
        peak_counts = float(np.clip(self.illum_peak, 0.0, 1.0)) * max_value
        y = np.arange(height, dtype=np.float32)[:, None]
        x = np.arange(width, dtype=np.float32)[None, :]
        dist_sq = (x - cx) ** 2 + (y - cy) ** 2
        gaussian = (peak_counts * np.exp(-dist_sq / (2.0 * sigma ** 2))).astype(np.float32)
        self._illum_cache = gaussian
        self._illum_cache_key = key
        return gaussian

    def _render_mock_frame(self):
        from scipy.ndimage import gaussian_filter

        height = int(self.AOIHeight)
        width = int(self.AOIWidth)
        max_value = float((2 ** int(self.BitDepth)) - 1)
        background_floor = min(10000.0, max_value)
        dtype = np.dtype(f"u{self.BitDepth // 8}")
        state = self._ensure_particle_state()

        frame_index = state["frame_index"]
        rng = state["rng"]

        # Drift offsets (sinusoidal, simulates mechanical sample drift)
        if self.drift_enabled and self.drift_amplitude > 0.0:
            amp = float(self.drift_amplitude)
            spd = float(self.drift_speed)
            drift_offset_x = int(round(amp * np.sin(2.0 * np.pi * frame_index * spd / 300.0)))
            drift_offset_y = int(round(amp * np.sin(2.0 * np.pi * frame_index * spd / 420.0 + 1.1)))
        else:
            drift_offset_x = 0
            drift_offset_y = 0

        # Stage XY position → pixel shift (particles and fiducials move with stage)
        _stage = getattr(shared_state, "shared_stage", None)
        if _stage is not None:
            stage_x = float(_stage["x"].snapshot().get("position") or 0.0) * _STAGE_PX_PER_M
            stage_y = float(_stage["y"].snapshot().get("position") or 0.0) * _STAGE_PX_PER_M
            stage_z = float(_stage["z"].snapshot().get("position") or 0.0)
        else:
            stage_x = stage_y = stage_z = 0.0

        # Combined sample offset: drift + manual translation + stage XY
        total_offset_x = drift_offset_x + int(self.translation_x) + int(round(stage_x))
        total_offset_y = drift_offset_y + int(self.translation_y) + int(round(stage_y))

        # Effective focus sigma: base setting + Z-axis defocus contribution
        effective_focus_sigma = float(self.focus_sigma) + abs(stage_z) * _Z_SIGMA_PER_M

        # Illumination Gaussian (fixed in image space — does NOT move with sample).
        # Blurred by effective_focus_sigma to simulate defocus spreading the beam.
        illum = self._get_illumination_gaussian(height, width)

        # Current intensity params (may change live without regenerating masks)
        amplitude = float(np.clip(self.particle_amplitude, 0.0, 1.0))

        # Pre-compute illumination geometry for per-particle Gaussian weighting.
        # Particles are fluorescent — their brightness is proportional to the laser
        # intensity at their position, so we multiply base intensity by the Gaussian.
        if self.illum_enabled:
            _illum_cx = float(self.illum_center_x_frac) * (width - 1)
            _illum_cy = float(self.illum_center_y_frac) * (height - 1)
            _illum_two_sigma_sq = 2.0 * max(1.0, float(self.illum_sigma)) ** 2
        else:
            _illum_cx = _illum_cy = _illum_two_sigma_sq = None

        # Global brightness pulse: all particles share one slow sinusoidal multiplier
        # driven by wall-clock time so it is independent of frame rate.
        _pulse_t = time.time()
        _pulse_period = max(0.1, float(self.global_pulse_period))
        _pulse_amp = float(np.clip(self.global_pulse_amplitude, 0.0, 1.0))
        global_brightness = 1.0 + _pulse_amp * math.sin(2.0 * math.pi * _pulse_t / _pulse_period)

        particle_layer = np.zeros((height, width), dtype=np.float32)

        for p in state["particles"]:
            mask = p["mask"]
            mh, mw = mask.shape
            x_center = p["x"] + total_offset_x
            y_center = p["y"] + total_offset_y

            y_start = y_center - mh // 2
            x_start = x_center - mw // 2
            y_img_min = max(0, y_start)
            y_img_max = min(height, y_start + mh)
            x_img_min = max(0, x_start)
            x_img_max = min(width, x_start + mw)

            if y_img_min >= y_img_max or x_img_min >= x_img_max:
                continue

            my_min = y_img_min - y_start
            my_max = y_img_max - y_start
            mx_min = x_img_min - x_start
            mx_max = x_img_max - x_start

            base = float(p["base_val"])
            phase = (2.0 * np.pi * frame_index / p["period"]) + p["phase"]
            modulation = 0.5 + 0.5 * np.sin(phase)

            # Weight by normalized Gaussian: particles only fluoresce inside the laser spot
            if _illum_two_sigma_sq is not None:
                dist_sq = (x_center - _illum_cx) ** 2 + (y_center - _illum_cy) ** 2
                illum_weight = math.exp(-dist_sq / _illum_two_sigma_sq)
            else:
                illum_weight = 1.0

            base_counts = max_value * base * illum_weight
            amplitude_counts = base_counts * amplitude  # amplitude as fraction of base
            intensity = float(np.clip(
                (base_counts + amplitude_counts * modulation) * global_brightness,
                0.0, max_value * 0.97
            ))

            particle_layer[y_img_min:y_img_max, x_img_min:x_img_max] += (
                mask[my_min:my_max, mx_min:mx_max] * intensity
            )

        # Apply Gaussian blur to particle layer to simulate focus/defocus
        if effective_focus_sigma > 0.1:
            particle_layer = gaussian_filter(particle_layer, sigma=effective_focus_sigma)

        # Fiducial rectangles — rendered on a separate layer so they are
        # unaffected by the global brightness pulse, but they DO move with the
        # sample (drift + manual translation) just like the particles.
        # The same focus_sigma is applied so they blur/sharpen with focus changes.
        fiducial_layer = np.zeros((height, width), dtype=np.float32)
        if self.fiducial_enabled:
            fid_intensity = float(np.clip(self.fiducial_intensity, 0.0, 1.0)) * max_value
            fid_half = max(1, int(self.fiducial_size) // 2)
            cx = width / 2.0
            cy = height / 2.0
            off_x = int(round(float(self.fiducial_offset_frac) * width))
            off_y = int(round(float(self.fiducial_offset_frac) * height))
            # Four centres: (±off_x, ±off_y) relative to image centre, shifted by
            # the same drift + translation offset applied to the particles.
            fid_centres = [
                (int(round(cx + off_x)) + total_offset_x, int(round(cy + off_y)) + total_offset_y),
                (int(round(cx - off_x)) + total_offset_x, int(round(cy + off_y)) + total_offset_y),
                (int(round(cx + off_x)) + total_offset_x, int(round(cy - off_y)) + total_offset_y),
                (int(round(cx - off_x)) + total_offset_x, int(round(cy - off_y)) + total_offset_y),
            ]
            for fx, fy in fid_centres:
                y0 = max(0, fy - fid_half)
                y1 = min(height, fy + fid_half + 1)
                x0 = max(0, fx - fid_half)
                x1 = min(width, fx + fid_half + 1)
                if y0 < y1 and x0 < x1:
                    fiducial_layer[y0:y1, x0:x1] = fid_intensity
            if effective_focus_sigma > 0.1:
                fiducial_layer = gaussian_filter(fiducial_layer, sigma=effective_focus_sigma)

        # Frame = background floor + fixed illumination profile + particle signal + fiducials
        # Illumination is blurred by defocus so the beam spreads when Z moves off focus.
        frame = np.full((height, width), background_floor, dtype=np.float32)
        if illum is not None:
            if effective_focus_sigma > 0.1:
                frame += gaussian_filter(illum, sigma=effective_focus_sigma)
            else:
                frame += illum
        frame += particle_layer
        frame += fiducial_layer

        # Shot noise
        shot_noise_photons = 300.0
        photons = np.clip(frame * (shot_noise_photons / max_value), 0.0, None)
        frame = rng.poisson(photons).astype(np.float32) * (max_value / shot_noise_photons)
        frame = np.clip(frame, 0.0, max_value).astype(dtype)

        state["frame_index"] += 1
        return Acquisition(frame, self.BitDepth)

    def _mock_render_loop(self):
        # Regenerate the animated frame at a MODEST visual rate (~60 Hz),
        # independent of the (possibly very high) delivery frame rate. Frame
        # *exposure* is paced separately by wait_buffer, which simply re-delivers
        # the latest cached frame — so the mock can expose up to max_FrameRate
        # without re-rendering every frame. Re-rendering a full frame per
        # delivery (and spinning this loop at the delivery rate) is what
        # previously capped the achievable rate and starved consumer threads.
        # The first frame is rendered immediately so wait_buffer has data at once.
        next_deadline = time.perf_counter()
        while not self._mock_render_stop_event.is_set():
            acquisition = self._render_mock_frame()
            with self._mock_frame_condition:
                self._mock_cached_acquisition = acquisition
                self._mock_last_visual_render_time = time.perf_counter()
                self._mock_frame_condition.notify_all()

            next_deadline += self._mock_visual_update_period_seconds
            now = time.perf_counter()
            if next_deadline < now:          # fell behind on a slow render; don't accrue backlog
                next_deadline = now + self._mock_visual_update_period_seconds
            if not self._wait_until_deadline(next_deadline):
                break

    # ── Camera properties ───────────────────────────────────────────────────────

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
