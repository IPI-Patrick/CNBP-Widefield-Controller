import time
import numpy as np

# Return a mock acquisition object
class Acquisition:

    def __init__(self, image, BitDepth):
        self.image = image
        self._np_data = self.image.reshape(-1).view(np.uint8)
        self.BitDepth = BitDepth

class MockCamera:
    ExposureTime            = 0.01
    FrameRate               = 100.0
    TriggerMode             = "Internal"
    CycleMode               = "Continuous"
    AOIWidth                = 1024
    AOIHeight               = 1024
    BitDepth                = 16
    options_bitDepth        = [8, 10, 12, 14, 16]
    options_CycleMode       = ["Continuous", "Single"]
    options_TriggerMode     = ["Internal", "Software", "External"]
    options_AOIBinning      = ["1x1", "2x2", "4x4"]
    AOIBinning              = "1x1"
    AOILeft                 = 1
    AOITop                  = 1
    SoftwareTrigger         = lambda self: ()
    AcquisitionStart        = lambda self: print("Mocking Starting Acquisition")
    AcquisitionStop         = lambda self: print("Mocking Stopping Acquisition")
    queue                   = lambda self, buf, size: ()
    flush                   = lambda self: print("Mocking Flushing buffers")

    @property
    def ImageSizeBytes(self):
        return self.AOIWidth * self.AOIHeight * (self.BitDepth // 8)

    def wait_buffer(self, timeout):

        # Wait for the exposure time (in seconds)
        time_to_wait = max(self.ExposureTime, 1.0 / max(float(getattr(self, "FrameRate", 100.0)), 1e-6))
        time.sleep(time_to_wait)

        # Keep the entire fake image generator in this method so it is easy to
        # remove later and swap back to a simpler mock if needed.
        height = int(self.AOIHeight)
        width = int(self.AOIWidth)
        max_value = float((2 ** int(self.BitDepth)) - 1)
        background_floor = min(10000.0, max_value)
        dtype = np.dtype(f"u{self.BitDepth // 8}")

        state = getattr(self, "_mock_blob_state", None)
        if state is None or state["width"] != width or state["height"] != height:
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

            state = {
                "width": width,
                "height": height,
                "frame_index": 0,
                "rng": rng,
                "blobs": blobs,
            }
            self._mock_blob_state = state

        frame_index = state["frame_index"]
        rng = state["rng"]
        frame = np.zeros((height, width), dtype=np.float32)
        frame += background_floor

        for blob in state["blobs"]:
            sigma = blob["sigma"]
            radius = max(3, int(np.ceil(3.5 * sigma)))
            x_center = blob["x"]
            y_center = blob["y"]

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

        frame += rng.normal(0.0, max_value * 0.004, size=(height, width)).astype(np.float32)
        frame = np.clip(frame, background_floor, max_value).astype(dtype)
        state["frame_index"] += 1

        return Acquisition(frame, self.BitDepth)
