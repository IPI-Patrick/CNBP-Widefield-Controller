from __future__ import annotations

# RENDER_PIPELINE_EXAMPLE.py
#
# This file is a deliberately verbose, heavily commented reference
# implementation of the real-time render pipeline prototype built in this
# repository.
#
# Purpose:
# - show another agent the exact moving parts of the pipeline in one place
# - explain the intent of each section directly in the code
# - provide a working Python/CuPy example that can be ported into another
#   program
#
# This file is not meant to be the smallest or prettiest implementation.
# It is meant to be easy to read, easy to follow, and easy to translate into:
# - a larger Python application
# - a native CUDA/C++ application
# - a camera SDK integration
# - a GUI application with a live display
#
# The pipeline demonstrated here does all of the following:
# - select a compatible CUDA runtime for CuPy
# - mock a scientific camera that produces uint16 frames on the GPU
# - blur the frame with a small low-pass filter
# - estimate translational drift via phase correlation or centroid fallback
# - shift the frame to compensate for drift
# - update a running background model
# - compute the foreground / difference image
# - convert the result to RGB for display-like output
# - compute ROI means, ROI difference, and ROI contrast
# - optionally copy every raw frame into a host RAM ring buffer
# - optionally decimate expensive tasks so the benchmark reflects realistic
#   cadences
#
# Important implementation choice:
# This example is optimized around CUDA 13.x and cupy-cuda13x, because that is
# the runtime combination validated in this workspace.

import argparse
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cupy as cp
import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None


# We explicitly look for CUDA toolkit installs here because, on Windows in
# particular, it is common to have multiple toolkit versions installed at once.
# CuPy can fail in confusing ways if the process inherits a mixed PATH.
CUDA_TOOLKIT_ROOT = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")


def parse_cuda_version(folder_name: str) -> tuple[int, ...]:
    """Convert a folder name like 'v13.0' into a sortable tuple like (13, 0)."""
    return tuple(int(part) for part in folder_name.removeprefix("v").split("."))


def configure_cuda_runtime() -> Path | None:
    """
    Force this process to prefer the highest installed CUDA 13.x toolkit.

    Why this exists:
    - CuPy loads CUDA DLLs from the current process environment.
    - If PATH contains both CUDA 12 and CUDA 13 entries, FFT or NVRTC imports can
      fail depending on DLL resolution order.
    - We standardize the process on CUDA 13.x because that is what this example
      was validated against.

    What we do:
    1. Discover installed CUDA toolkits.
    2. Choose the newest v13.* installation.
    3. Set CUDA_PATH and CUDA_HOME for this process.
    4. Remove all existing CUDA entries from PATH.
    5. Prepend the chosen toolkit bin folder.

    This affects only the current Python process and children launched from it.
    """
    if not CUDA_TOOLKIT_ROOT.exists():
        return None

    candidates = sorted(
        (path for path in CUDA_TOOLKIT_ROOT.iterdir() if path.is_dir() and path.name.startswith("v13.")),
        key=lambda path: parse_cuda_version(path.name),
        reverse=True,
    )
    if not candidates:
        return None

    preferred = candidates[0]
    bin_dir = preferred / "bin" / "x64"
    if not bin_dir.exists():
        bin_dir = preferred / "bin"
    if not bin_dir.exists():
        return None

    os.environ["CUDA_PATH"] = str(preferred)
    os.environ["CUDA_HOME"] = str(preferred)

    filtered_path_parts: list[str] = []
    for entry in os.environ.get("PATH", "").split(";"):
        normalized = entry.strip()
        if not normalized:
            continue
        if "NVIDIA GPU Computing Toolkit\\CUDA\\v" in normalized:
            continue
        filtered_path_parts.append(normalized)
    os.environ["PATH"] = ";".join([str(bin_dir), *filtered_path_parts])
    return preferred


def validate_runtime() -> None:
    """
    Fail early with a readable error if the active CuPy/CUDA runtime is broken.

    We intentionally perform a tiny CuPy allocation. Simple imports are not always
    enough to prove the runtime works, because some failures occur only once CuPy
    compiles or loads a backend module.
    """
    try:
        configure_cuda_runtime()
        cp.cuda.runtime.getDeviceCount()
        cp.arange(1, dtype=cp.float32)
    except Exception as exc:  # We want to intercept whatever runtime path failed.
        message = str(exc)
        if "nvrtc64_" in message or "CuPy failed to load" in message:
            raise RuntimeError(
                "The active Python environment has an incompatible CuPy/CUDA runtime. "
                "This example expects cupy-cuda13x 14.1.x or newer. "
                "Repair it with: python -m pip install --upgrade \"cupy-cuda13x>=14.1.0,<15\""
            ) from exc
        raise


# ---------------------------------------------------------------------------
# GPU kernels
# ---------------------------------------------------------------------------
#
# We keep the kernels inline in this file because the goal is explainability.
# In a production C++/CUDA port, these would usually live in .cu files, be
# precompiled, and be invoked through a more explicit scheduling layer.


MOCK_CAMERA_KERNEL = cp.RawKernel(
    r'''
    extern "C" __global__
    void generate_mock_frame(
        unsigned short* frame,
        const int width,
        const int height,
        const int frame_index,
        const float base_level,
        const float signal_level,
        const float blob_sigma,
        const float drift_amplitude_x,
        const float drift_amplitude_y
    ) {
        // Each thread generates one pixel.
        // The synthetic image has:
        // - a broad background level
        // - some low-frequency structured variation
        // - a bright gaussian-like signal blob
        // - a drifting center so registration has something to do
        const int idx = blockDim.x * blockIdx.x + threadIdx.x;
        const int total = width * height;
        if (idx >= total) {
            return;
        }

        const int x = idx % width;
        const int y = idx / width;
        const float fx = (float)x / (float)width;
        const float fy = (float)y / (float)height;
        const float t = (float)frame_index;

        const float center_x = 0.5f * width + drift_amplitude_x * __sinf(t * 0.033f);
        const float center_y = 0.5f * height + drift_amplitude_y * __cosf(t * 0.025f);
        const float dx = x - center_x;
        const float dy = y - center_y;
        const float radial = sqrtf(dx * dx + dy * dy);

        const float base = base_level
            + 420.0f * __sinf(fx * 40.0f + t * 0.018f)
            + 310.0f * __cosf(fy * 33.0f - t * 0.013f)
            + 180.0f * __sinf((fx + fy) * 22.0f + t * 0.011f);

        const float blob = signal_level * __expf(-(dx * dx + dy * dy) / (2.0f * blob_sigma * blob_sigma));
        const float ring = 700.0f * __sinf(radial * 0.055f - t * 0.047f);

        float value = base + blob + ring;
        value = fminf(fmaxf(value, 0.0f), 65535.0f);
        frame[idx] = (unsigned short)(value + 0.5f);
    }
    ''',
    "generate_mock_frame",
)


BLUR_3X3_U16_KERNEL = cp.RawKernel(
    r'''
    extern "C" __global__
    void blur_u16_gaussian3x3(
        const unsigned short* input,
        float* output,
        const int width,
        const int height
    ) {
        // One thread computes one blurred output pixel.
        // This is a tiny 3x3 gaussian-like filter.
        // We use it to demonstrate low-pass filtering before drift estimation.
        const int idx = blockDim.x * blockIdx.x + threadIdx.x;
        const int total = width * height;
        if (idx >= total) {
            return;
        }

        const int x = idx % width;
        const int y = idx / width;
        const int xm1 = x > 0 ? x - 1 : 0;
        const int xp1 = x + 1 < width ? x + 1 : width - 1;
        const int ym1 = y > 0 ? y - 1 : 0;
        const int yp1 = y + 1 < height ? y + 1 : height - 1;

        const int r0 = ym1 * width;
        const int r1 = y * width;
        const int r2 = yp1 * width;

        float sum = 0.0f;
        sum += (float)input[r0 + xm1];
        sum += 2.0f * (float)input[r0 + x];
        sum += (float)input[r0 + xp1];
        sum += 2.0f * (float)input[r1 + xm1];
        sum += 4.0f * (float)input[r1 + x];
        sum += 2.0f * (float)input[r1 + xp1];
        sum += (float)input[r2 + xm1];
        sum += 2.0f * (float)input[r2 + x];
        sum += (float)input[r2 + xp1];
        output[idx] = sum * 0.0625f;
    }
    ''',
    "blur_u16_gaussian3x3",
)


RGB_KERNEL = cp.RawKernel(
    r'''
    extern "C" __global__
    void mono_to_rgb(
        const float* input,
        unsigned char* output,
        const float display_min,
        const float scale,
        const int total
    ) {
        // Convert a float monochrome foreground image into RGB bytes.
        // This is intentionally grayscale, but the same slot could host a LUT or
        // false-colour mapping in a real application.
        const int idx = blockDim.x * blockIdx.x + threadIdx.x;
        if (idx >= total) {
            return;
        }

        const float normalized = fminf(fmaxf((input[idx] - display_min) * scale, 0.0f), 1.0f);
        const unsigned char value = (unsigned char)(normalized * 255.0f);
        const int rgb_idx = idx * 3;
        output[rgb_idx] = value;
        output[rgb_idx + 1] = value;
        output[rgb_idx + 2] = value;
    }
    ''',
    "mono_to_rgb",
)


POSTPROCESS_KERNEL = cp.RawKernel(
    r'''
    extern "C" __global__
    void postprocess_frame(
        const float* input,
        float* background,
        float* foreground,
        unsigned char* rgb,
        const int width,
        const int height,
        const int shift_x,
        const int shift_y,
        const float alpha,
        const float display_min,
        const float scale,
        const int write_rgb
    ) {
        // This fused kernel is an important performance trick.
        // Instead of running three or four separate kernels after registration,
        // we do all of this at once:
        // 1. sample the drift-corrected pixel
        // 2. update the running background model
        // 3. compute the foreground value
        // 4. optionally write RGB bytes
        //
        // Fusing these steps reduces extra global memory traffic and extra kernel launches.
        const int idx = blockDim.x * blockIdx.x + threadIdx.x;
        const int total = width * height;
        if (idx >= total) {
            return;
        }

        const int x = idx % width;
        const int y = idx / width;
        int src_x = x - shift_x;
        int src_y = y - shift_y;

        while (src_x < 0) {
            src_x += width;
        }
        while (src_y < 0) {
            src_y += height;
        }

        src_x %= width;
        src_y %= height;

        const float corrected = input[src_y * width + src_x];
        const float previous = background[idx];
        const float next_value = previous + alpha * (corrected - previous);
        const float fg_value = corrected - next_value;

        background[idx] = next_value;
        foreground[idx] = fg_value;

        if (write_rgb != 0) {
            const float normalized = fminf(fmaxf((fg_value - display_min) * scale, 0.0f), 1.0f);
            const unsigned char value = (unsigned char)(normalized * 255.0f);
            const int rgb_idx = idx * 3;
            rgb[rgb_idx] = value;
            rgb[rgb_idx + 1] = value;
            rgb[rgb_idx + 2] = value;
        }
    }
    ''',
    "postprocess_frame",
)


@dataclass(frozen=True)
class ExampleRoi:
    """One named region of interest used for metrics."""

    label: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ExampleConfig:
    """
    All runtime parameters in one immutable container.

    Another agent porting this logic should treat this structure as the contract
    between command-line parsing / UI configuration and the execution engine.
    """

    width: int
    height: int
    fps: float
    frames: int
    warmup: int
    background_alpha: float
    drift_mode: str
    drift_downsample: int
    phase_every: int
    metrics_stride: int
    output_stride: int
    gpu_timing_stride: int
    base_level: float
    signal_level: float
    blob_sigma: float
    drift_amplitude_x: float
    drift_amplitude_y: float
    camera_backend: str
    pace: str
    rgb_min: float
    rgb_max: float
    raw_ram_buffer_frames: int
    rois: tuple[ExampleRoi, ...]
    metrics_json: Path | None
    preview_path: Path | None


def parse_rois(raw: str, width: int, height: int) -> tuple[ExampleRoi, ...]:
    """Parse ROI strings or build a sensible default pair."""
    if not raw:
        roi_width = max(32, width // 10)
        roi_height = max(32, height // 10)
        half_w = width // 2
        half_h = height // 2
        return (
            ExampleRoi("signal", max(0, half_w - roi_width // 2), max(0, half_h - roi_height // 2), roi_width, roi_height),
            ExampleRoi("reference", max(0, half_w + roi_width), max(0, half_h - roi_height // 2), roi_width, roi_height),
        )

    rois: list[ExampleRoi] = []
    for chunk in raw.split(","):
        name, values = chunk.split("=", maxsplit=1)
        x_raw, y_raw, width_raw, height_raw = values.split(":")
        roi = ExampleRoi(name.strip(), int(x_raw), int(y_raw), int(width_raw), int(height_raw))
        if roi.x < 0 or roi.y < 0 or roi.x + roi.width > width or roi.y + roi.height > height:
            raise ValueError(f"ROI {roi.label} is outside the frame bounds.")
        rois.append(roi)
    return tuple(rois)


def parse_window(raw: str) -> tuple[float, float]:
    """Parse display mapping window in min:max format."""
    low_raw, high_raw = raw.split(":", maxsplit=1)
    low = float(low_raw)
    high = float(high_raw)
    if high <= low:
        raise ValueError("RGB window max must be greater than min.")
    return low, high


class MockCamera:
    """
    Mock frame source.

    Why two backends exist:
    - `device` generates raw frames directly into GPU memory. This is the highest
      throughput path for benchmarking the processing pipeline itself.
    - `host` generates raw frames in pinned host memory. This better resembles a
      CPU-visible capture buffer that is later uploaded or consumed by the GPU.
    """

    def __init__(self, config: ExampleConfig) -> None:
        self.config = config
        self.frame_shape = (config.height, config.width)
        self.total_pixels = config.width * config.height
        self._device_frame = cp.empty(self.frame_shape, dtype=cp.uint16)
        self._host_frame = self._build_host_frame_buffer() if config.camera_backend == "host" else None
        self._host_x: np.ndarray | None = None
        self._host_y: np.ndarray | None = None

        if self._host_frame is not None:
            host_y, host_x = np.mgrid[0 : config.height, 0 : config.width]
            self._host_y = host_y.astype(np.float32)
            self._host_x = host_x.astype(np.float32)

    def _build_host_frame_buffer(self) -> np.ndarray:
        pinned = cp.cuda.alloc_pinned_memory(self.total_pixels * np.dtype(np.uint16).itemsize)
        return np.frombuffer(pinned, dtype=np.uint16, count=self.total_pixels).reshape(self.frame_shape)

    def capture(self, frame_index: int) -> cp.ndarray | np.ndarray:
        """
        Produce one raw uint16 frame.

        In a real camera integration, this method would be replaced with one of:
        - camera SDK DMA callback
        - driver-owned ring buffer dequeue
        - NIC / framegrabber receive path
        - GPUDirect RDMA target buffer handling
        """
        if self.config.camera_backend == "device":
            blocks = (self.total_pixels + 255) // 256
            MOCK_CAMERA_KERNEL(
                (blocks,),
                (256,),
                (
                    self._device_frame,
                    np.int32(self.config.width),
                    np.int32(self.config.height),
                    np.int32(frame_index),
                    np.float32(self.config.base_level),
                    np.float32(self.config.signal_level),
                    np.float32(self.config.blob_sigma),
                    np.float32(self.config.drift_amplitude_x),
                    np.float32(self.config.drift_amplitude_y),
                ),
            )
            return self._device_frame

        if self._host_frame is None or self._host_x is None or self._host_y is None:
            raise RuntimeError("Host camera backend was not initialized correctly.")

        t = float(frame_index)
        center_x = 0.5 * self.config.width + self.config.drift_amplitude_x * math.sin(t * 0.033)
        center_y = 0.5 * self.config.height + self.config.drift_amplitude_y * math.cos(t * 0.025)
        dx = self._host_x - center_x
        dy = self._host_y - center_y
        radial = np.sqrt(dx * dx + dy * dy, dtype=np.float32)
        fx = self._host_x / float(self.config.width)
        fy = self._host_y / float(self.config.height)

        base = (
            self.config.base_level
            + 420.0 * np.sin(fx * 40.0 + t * 0.018)
            + 310.0 * np.cos(fy * 33.0 - t * 0.013)
            + 180.0 * np.sin((fx + fy) * 22.0 + t * 0.011)
        )
        blob = self.config.signal_level * np.exp(-(dx * dx + dy * dy) / (2.0 * self.config.blob_sigma * self.config.blob_sigma))
        ring = 700.0 * np.sin(radial * 0.055 - t * 0.047)
        np.clip(base + blob + ring, 0.0, 65535.0, out=self._host_frame, casting="unsafe")
        return self._host_frame


class RawFrameRamBuffer:
    """
    Host RAM ring buffer for raw frames.

    Why it matters:
    A real scientific capture stack often has to preserve recent raw frames in RAM,
    even if it does not immediately write them to disk. That extra copy can change
    the real throughput envelope, so it is modeled explicitly here.

    Implementation detail:
    We prefer pinned host memory because GPU->host copies into pinned buffers are
    much faster and more predictable than pageable host memory copies.
    """

    def __init__(self, frame_shape: tuple[int, int], capacity_frames: int) -> None:
        self.frame_shape = frame_shape
        self.capacity_frames = capacity_frames
        self.saved_frames = 0
        self.next_index = 0
        self.storage_kind = "paged"

        frame_elements = capacity_frames * frame_shape[0] * frame_shape[1]
        try:
            pinned = cp.cuda.alloc_pinned_memory(frame_elements * np.dtype(np.uint16).itemsize)
            self.buffer = np.frombuffer(pinned, dtype=np.uint16, count=frame_elements).reshape((capacity_frames, *frame_shape))
            self.storage_kind = "pinned"
            self._pinned_memory = pinned
        except (cp.cuda.runtime.CUDARuntimeError, MemoryError, BufferError, ValueError):
            self.buffer = np.empty((capacity_frames, *frame_shape), dtype=np.uint16)
            self._pinned_memory = None

    @property
    def total_bytes(self) -> int:
        return int(self.buffer.nbytes)

    def save(self, frame: cp.ndarray | np.ndarray) -> None:
        """
        Copy one raw frame into the ring buffer.

        In a larger application, this might become:
        - a lock-free queue into a separate consumer thread
        - a zero-copy camera DMA target
        - a double-buffered staging path into a RAM recorder
        """
        slot = self.buffer[self.next_index]
        if isinstance(frame, cp.ndarray):
            frame.get(out=slot, blocking=True)
        else:
            np.copyto(slot, frame)
        self.next_index = (self.next_index + 1) % self.capacity_frames
        self.saved_frames += 1


class RenderPipelineExample:
    """
    The main pipeline engine.

    This class owns GPU buffers, FFT reference state, drift state, and output state.
    Each call to `process()` does one frame worth of work.
    """

    def __init__(self, config: ExampleConfig) -> None:
        self.config = config
        self.active_drift_mode = config.drift_mode
        self.frame_shape = (config.height, config.width)
        self.total_pixels = config.width * config.height
        self.blocks = (self.total_pixels + 255) // 256

        # One non-blocking stream is enough for this example.
        # A larger implementation may want:
        # - dedicated copy stream(s)
        # - compute stream(s)
        # - output / presentation stream(s)
        self.stream = cp.cuda.Stream(non_blocking=True)

        # Working buffers.
        self.filtered = cp.empty(self.frame_shape, dtype=cp.float32)
        self.foreground = cp.empty_like(self.filtered)
        self.background = cp.zeros_like(self.filtered)
        self.rgb = cp.empty((config.height, config.width, 3), dtype=cp.uint8)

        # Drift state.
        self.reference_fft: cp.ndarray | None = None
        self.reference_centroid: tuple[float, float] | None = None
        self._last_shift = (0, 0)

        self.display_scale = 1.0 / max(config.rgb_max - config.rgb_min, 1e-6)
        self._initialized = False
        self._fallback_reported = False

        # Precomputed coordinate grids for centroid estimation.
        # We compute these only once to avoid rebuilding them every frame.
        self._x_coords = cp.arange(0, config.width, config.drift_downsample, dtype=cp.float32)[None, :]
        self._y_coords = cp.arange(0, config.height, config.drift_downsample, dtype=cp.float32)[:, None]

    def process(
        self,
        frame: cp.ndarray | np.ndarray,
        frame_index: int,
        collect_metrics: bool,
        collect_output: bool,
        collect_gpu_timing: bool,
    ) -> dict[str, object]:
        """
        Process one frame end-to-end.

        Execution order:
        1. blur the raw frame
        2. initialize reference state or estimate drift
        3. fused postprocess: shift, update background, compute foreground, maybe RGB
        4. optionally synchronize for timing / metrics collection

        Why cadence flags exist:
        In a real application, not every expensive operation needs to run every frame.
        For example:
        - you may display at 60 Hz but acquire at 1200 FPS
        - you may compute ROI metrics at display cadence only
        - you may run phase correlation every N frames and reuse the last shift
        - you may sample GPU timing only during diagnostics
        """
        frame_gpu = frame if isinstance(frame, cp.ndarray) else cp.asarray(frame)
        start_event = cp.cuda.Event() if collect_gpu_timing else None
        end_event = cp.cuda.Event() if collect_gpu_timing else None

        with self.stream:
            if start_event is not None:
                start_event.record(self.stream)

            BLUR_3X3_U16_KERNEL(
                (self.blocks,),
                (256,),
                (frame_gpu, self.filtered, np.int32(self.config.width), np.int32(self.config.height)),
            )

            if not self._initialized:
                if self.active_drift_mode == "phase":
                    self._initialize_phase_reference()
                elif self.active_drift_mode == "centroid":
                    self.reference_centroid = self._compute_centroid(self.filtered)

                # First frame seeds the background model.
                self.background[...] = self.filtered
                self.foreground.fill(0.0)

                if collect_output:
                    RGB_KERNEL(
                        (self.blocks,),
                        (256,),
                        (
                            self.foreground,
                            self.rgb,
                            np.float32(self.config.rgb_min),
                            np.float32(self.display_scale),
                            np.int32(self.total_pixels),
                        ),
                    )

                shift_x = 0
                shift_y = 0
                self._last_shift = (shift_x, shift_y)
                self._initialized = True
            else:
                shift_x, shift_y = self._select_shift(frame_index)

                POSTPROCESS_KERNEL(
                    (self.blocks,),
                    (256,),
                    (
                        self.filtered,
                        self.background,
                        self.foreground,
                        self.rgb,
                        np.int32(self.config.width),
                        np.int32(self.config.height),
                        np.int32(shift_x),
                        np.int32(shift_y),
                        np.float32(self.config.background_alpha),
                        np.float32(self.config.rgb_min),
                        np.float32(self.display_scale),
                        np.int32(1 if collect_output else 0),
                    ),
                )

            if end_event is not None:
                end_event.record(self.stream)

        # In the benchmark we synchronize here because we want deterministic wall timing.
        # In a real streaming application you would often avoid synchronizing every frame
        # and instead consume completed work through events or ring-buffer state.
        self.stream.synchronize()

        gpu_time_ms = float(cp.cuda.get_elapsed_time(start_event, end_event)) if start_event is not None and end_event is not None else None
        roi_means = self._compute_roi_means() if collect_metrics else None
        difference = None
        contrast = None

        if roi_means and len(roi_means) >= 2:
            first_value = roi_means[0]["mean"]
            second_value = roi_means[1]["mean"]
            difference = first_value - second_value
            contrast = difference / (abs(first_value) + abs(second_value) + 1e-6)

        return {
            "gpu_time_ms": gpu_time_ms,
            "shift_x": shift_x,
            "shift_y": shift_y,
            "roi_means": roi_means,
            "difference": difference,
            "contrast": contrast,
        }

    def _select_shift(self, frame_index: int) -> tuple[int, int]:
        """
        Decide whether to recompute phase correlation or reuse the last shift.

        This is one of the major throughput levers in the benchmark. Running phase
        correlation every frame is accurate but expensive. Running it every N frames
        and reusing the previous shift is often good enough for slowly varying drift.
        """
        if self.active_drift_mode == "phase" and self.config.phase_every > 1 and frame_index % self.config.phase_every != 0:
            return self._last_shift

        shift = self._estimate_shift()
        self._last_shift = shift
        return shift

    def _estimate_shift(self) -> tuple[int, int]:
        if self.active_drift_mode == "none":
            return 0, 0
        if self.active_drift_mode == "centroid":
            return self._estimate_shift_centroid()
        return self._estimate_shift_phase()

    def _initialize_phase_reference(self) -> None:
        drift_view = self.filtered[:: self.config.drift_downsample, :: self.config.drift_downsample]
        try:
            self.reference_fft = cp.fft.rfft2(drift_view).copy()
        except ImportError:
            self._fallback_to_centroid("cuFFT is unavailable; falling back to centroid drift estimation.")

    def _fallback_to_centroid(self, message: str) -> None:
        self.active_drift_mode = "centroid"
        self.reference_fft = None
        self.reference_centroid = self._compute_centroid(self.filtered)
        if not self._fallback_reported:
            print(message)
            self._fallback_reported = True

    def _estimate_shift_phase(self) -> tuple[int, int]:
        """
        Phase correlation drift estimator.

        Steps:
        1. downsample the filtered frame
        2. compute RFFT of current frame and reference frame
        3. compute normalized cross-power spectrum
        4. inverse FFT to get correlation surface
        5. find the correlation peak and convert to integer shift
        """
        drift_view = self.filtered[:: self.config.drift_downsample, :: self.config.drift_downsample]
        try:
            frame_fft = cp.fft.rfft2(drift_view)
        except ImportError:
            self._fallback_to_centroid("cuFFT is unavailable; falling back to centroid drift estimation.")
            return self._estimate_shift_centroid()

        if self.reference_fft is None:
            raise RuntimeError("Reference FFT was not initialized.")

        cross_power = frame_fft * cp.conj(self.reference_fft)
        cross_power /= cp.maximum(cp.abs(cross_power), 1e-6)
        correlation = cp.fft.irfft2(cross_power, s=drift_view.shape)

        peak_index = int(cp.argmax(correlation).get())
        peak_y, peak_x = divmod(peak_index, correlation.shape[1])
        shift_y = peak_y if peak_y < correlation.shape[0] // 2 else peak_y - correlation.shape[0]
        shift_x = peak_x if peak_x < correlation.shape[1] // 2 else peak_x - correlation.shape[1]
        return shift_x * self.config.drift_downsample, shift_y * self.config.drift_downsample

    def _estimate_shift_centroid(self) -> tuple[int, int]:
        current_x, current_y = self._compute_centroid(self.filtered)
        if self.reference_centroid is None:
            self.reference_centroid = current_x, current_y
        reference_x, reference_y = self.reference_centroid
        return int(round(reference_x - current_x)), int(round(reference_y - current_y))

    def _compute_centroid(self, frame: cp.ndarray) -> tuple[float, float]:
        downsampled = frame[:: self.config.drift_downsample, :: self.config.drift_downsample]
        positive = cp.maximum(downsampled, 0.0)
        total = float(positive.sum().get())
        if total <= 1e-6:
            return self.config.width / 2.0, self.config.height / 2.0
        centroid_x = float((positive * self._x_coords).sum().get() / total)
        centroid_y = float((positive * self._y_coords).sum().get() / total)
        return centroid_x, centroid_y

    def _compute_roi_means(self) -> list[dict[str, float | str]]:
        results: list[dict[str, float | str]] = []
        for roi in self.config.rois:
            roi_mean = float(cp.mean(self.foreground[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]).get())
            results.append({"label": roi.label, "mean": roi_mean})
        return results

    def save_preview(self, path: Path) -> None:
        if Image is None:
            raise RuntimeError("Pillow is required to save a preview image. Install pillow and rerun.")
        image = Image.fromarray(cp.asnumpy(self.rgb), mode="RGB")
        image.save(path)


def pace_frame_loop(mode: str, start_time: float, frame_index: int, frame_period: float) -> int:
    """
    Optional real-time pacing helper.

    For pure throughput benchmarking we do not sleep.
    For a more realistic simulation, we can try to release one frame every
    frame_period seconds.
    """
    if mode != "realtime":
        return 0

    expected_time = start_time + frame_index * frame_period
    now = time.perf_counter()
    late_frames = 0

    if now < expected_time:
        remaining = expected_time - now
        if remaining > 0.003:
            time.sleep(remaining - 0.001)
        while time.perf_counter() < expected_time:
            pass
        return 0

    late_frames = int((now - expected_time) / frame_period)
    return late_frames


def percentile(values: Iterable[float], q: float) -> float:
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        return 0.0
    return float(np.percentile(array, q))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Heavily commented render pipeline example.")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--fps", type=float, default=1200.0)
    parser.add_argument("--frames", type=int, default=1200)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--background-alpha", type=float, default=0.02)
    parser.add_argument("--drift-mode", choices=("phase", "centroid", "none"), default="phase")
    parser.add_argument("--drift-downsample", type=int, default=4)
    parser.add_argument("--phase-every", type=int, default=1)
    parser.add_argument("--metrics-stride", type=int, default=1)
    parser.add_argument("--output-stride", type=int, default=1)
    parser.add_argument("--gpu-timing-stride", type=int, default=1)
    parser.add_argument("--base-level", type=float, default=12000.0)
    parser.add_argument("--signal-level", type=float, default=18000.0)
    parser.add_argument("--blob-sigma", type=float, default=52.0)
    parser.add_argument("--drift-amplitude-x", type=float, default=18.0)
    parser.add_argument("--drift-amplitude-y", type=float, default=14.0)
    parser.add_argument("--camera-backend", choices=("device", "host"), default="device")
    parser.add_argument("--pace", choices=("benchmark", "realtime"), default="benchmark")
    parser.add_argument("--rgb-window", default="-4096:4096")
    parser.add_argument("--raw-ram-buffer-frames", type=int, default=0)
    parser.add_argument("--rois", default="")
    parser.add_argument("--metrics-json", type=Path)
    parser.add_argument("--preview-path", type=Path)
    return parser


def run_example(config: ExampleConfig) -> dict[str, object]:
    """
    Drive the whole benchmark.

    This function is the best summary of the system at runtime:
    - configure runtime
    - create camera
    - create pipeline
    - optionally create raw RAM ring buffer
    - warm up kernels and FFT plans
    - run benchmark loop
    - summarize results
    """
    validate_runtime()

    camera = MockCamera(config)
    pipeline = RenderPipelineExample(config)
    raw_ram_buffer = RawFrameRamBuffer((config.height, config.width), config.raw_ram_buffer_frames) if config.raw_ram_buffer_frames > 0 else None

    for warmup_index in range(config.warmup):
        warmup_frame = camera.capture(warmup_index)
        if raw_ram_buffer is not None:
            raw_ram_buffer.save(warmup_frame)

        pipeline.process(
            warmup_frame,
            frame_index=warmup_index,
            collect_metrics=False,
            collect_output=config.output_stride > 0 and ((warmup_index + 1) % config.output_stride == 0),
            collect_gpu_timing=False,
        )

    frame_period = 1.0 / config.fps
    loop_start = time.perf_counter()

    wall_times_ms: list[float] = []
    gpu_times_ms: list[float] = []
    shifts_x: list[int] = []
    shifts_y: list[int] = []
    late_frames = 0
    last_result: dict[str, object] | None = None

    for frame_index in range(config.frames):
        late_frames += pace_frame_loop(config.pace, loop_start, frame_index, frame_period)

        raw_frame = camera.capture(frame_index + config.warmup)
        if raw_ram_buffer is not None:
            raw_ram_buffer.save(raw_frame)

        is_last_frame = frame_index == config.frames - 1
        collect_metrics = config.metrics_stride > 0 and ((frame_index + 1) % config.metrics_stride == 0 or is_last_frame)
        collect_output = (
            (config.output_stride > 0 and ((frame_index + 1) % config.output_stride == 0 or is_last_frame))
            or (is_last_frame and config.preview_path is not None)
        )
        collect_gpu_timing = config.gpu_timing_stride > 0 and ((frame_index + 1) % config.gpu_timing_stride == 0 or is_last_frame)

        frame_start = time.perf_counter()
        result = pipeline.process(
            raw_frame,
            frame_index=frame_index + config.warmup,
            collect_metrics=collect_metrics,
            collect_output=collect_output,
            collect_gpu_timing=collect_gpu_timing,
        )
        frame_end = time.perf_counter()

        wall_times_ms.append((frame_end - frame_start) * 1000.0)
        if result["gpu_time_ms"] is not None:
            gpu_times_ms.append(float(result["gpu_time_ms"]))
        shifts_x.append(int(result["shift_x"]))
        shifts_y.append(int(result["shift_y"]))
        if result["roi_means"] is not None or last_result is None:
            last_result = result

    elapsed_seconds = time.perf_counter() - loop_start
    achieved_fps = config.frames / max(elapsed_seconds, 1e-9)
    frame_budget_ms = 1000.0 / config.fps
    p95_wall_ms = percentile(wall_times_ms, 95.0)

    summary = {
        "device": cp.cuda.runtime.getDeviceProperties(0)["name"].decode(),
        "camera_backend": config.camera_backend,
        "pace": config.pace,
        "requested_drift_mode": config.drift_mode,
        "active_drift_mode": pipeline.active_drift_mode,
        "phase_every": config.phase_every,
        "metrics_stride": config.metrics_stride,
        "output_stride": config.output_stride,
        "gpu_timing_stride": config.gpu_timing_stride,
        "raw_ram_buffer_frames": config.raw_ram_buffer_frames,
        "raw_ram_buffer_bytes": raw_ram_buffer.total_bytes if raw_ram_buffer is not None else 0,
        "raw_ram_buffer_kind": raw_ram_buffer.storage_kind if raw_ram_buffer is not None else None,
        "raw_frames_saved": raw_ram_buffer.saved_frames if raw_ram_buffer is not None else 0,
        "resolution": {"width": config.width, "height": config.height},
        "requested_fps": config.fps,
        "achieved_fps": achieved_fps,
        "frame_budget_ms": frame_budget_ms,
        "avg_wall_ms": statistics.fmean(wall_times_ms),
        "p95_wall_ms": p95_wall_ms,
        "avg_gpu_ms": statistics.fmean(gpu_times_ms) if gpu_times_ms else None,
        "p95_gpu_ms": percentile(gpu_times_ms, 95.0) if gpu_times_ms else None,
        "gpu_timing_samples": len(gpu_times_ms),
        "late_frames": late_frames,
        "meets_requested_rate": achieved_fps >= config.fps and p95_wall_ms <= frame_budget_ms,
        "avg_shift_x_px": statistics.fmean(shifts_x) if shifts_x else 0.0,
        "avg_shift_y_px": statistics.fmean(shifts_y) if shifts_y else 0.0,
        "last_result": last_result,
    }

    if config.preview_path is not None:
        pipeline.save_preview(config.preview_path)

    if config.metrics_json is not None:
        config.metrics_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    rgb_min, rgb_max = parse_window(args.rgb_window)

    config = ExampleConfig(
        width=args.width,
        height=args.height,
        fps=args.fps,
        frames=args.frames,
        warmup=args.warmup,
        background_alpha=args.background_alpha,
        drift_mode=args.drift_mode,
        drift_downsample=max(1, args.drift_downsample),
        phase_every=max(1, args.phase_every),
        metrics_stride=max(0, args.metrics_stride),
        output_stride=max(0, args.output_stride),
        gpu_timing_stride=max(0, args.gpu_timing_stride),
        base_level=args.base_level,
        signal_level=args.signal_level,
        blob_sigma=args.blob_sigma,
        drift_amplitude_x=args.drift_amplitude_x,
        drift_amplitude_y=args.drift_amplitude_y,
        camera_backend=args.camera_backend,
        pace=args.pace,
        rgb_min=rgb_min,
        rgb_max=rgb_max,
        raw_ram_buffer_frames=max(0, args.raw_ram_buffer_frames),
        rois=parse_rois(args.rois, args.width, args.height),
        metrics_json=args.metrics_json,
        preview_path=args.preview_path,
    )

    if config.width <= 0 or config.height <= 0:
        raise ValueError("Frame dimensions must be positive.")
    if config.frames <= 0:
        raise ValueError("Frame count must be positive.")
    if config.fps <= 0.0:
        raise ValueError("FPS must be positive.")

    summary = run_example(config)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
