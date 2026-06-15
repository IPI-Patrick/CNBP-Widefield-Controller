# Render Pipeline Example Guide

This document explains, in detail, how the example render pipeline works and how another agent can port it into a different application.

The companion code file is `RENDER_PIPELINE_EXAMPLE.py`.

## Goal

The goal of the example is to show a practical structure for a high-speed scientific imaging pipeline that:

- acquires or mocks `uint16` grayscale frames
- performs low-pass filtering
- performs drift correction
- performs live background subtraction
- computes ROI measurements and contrast-like metrics
- converts data to RGB for display or export
- optionally stores raw frames in host RAM
- exposes cadence controls so not every expensive operation has to run every frame

The example is built around CuPy on CUDA 13.x because that was the runtime combination validated in this repository.

## High-level architecture

The pipeline is organized into these stages:

1. Runtime setup
2. Raw frame acquisition
3. Low-pass filtering
4. Drift estimation
5. Drift-compensated postprocess
6. Optional display/output conversion
7. Optional ROI metric extraction
8. Optional raw-frame RAM retention
9. Benchmarking and summary reporting

The most important design idea is that the pipeline separates the concepts of:

- acquisition cadence
- display cadence
- metric cadence
- drift-update cadence
- diagnostics cadence

That separation is what allows the benchmark to exceed the requested acquisition rate without doing every heavy operation at full frame rate.

## Why CUDA runtime selection matters

On Windows, multiple CUDA versions can easily be installed at once. When that happens, a Python environment may load:

- `nvrtc` from one CUDA version
- `cufft` from another version
- runtime DLLs from a third location found earlier on PATH

The result is often that CuPy imports successfully but specific backends like `cupy.cuda.cufft` fail.

That is why the example explicitly:

1. finds installed CUDA toolkits
2. chooses the newest `v13.*` install
3. sets `CUDA_PATH` and `CUDA_HOME`
4. removes old CUDA entries from PATH
5. prepends the chosen CUDA 13 bin directory

In another application, you have three reasonable options:

1. Keep this same process-local environment rewrite.
2. Enforce a single CUDA runtime at deployment time and remove the rewrite entirely.
3. Move runtime detection into a startup service or launcher.

## Data model

The example uses a small set of explicit data containers.

### `ExampleConfig`

This structure contains every runtime choice in one place.

That is important because in larger software systems it keeps the execution layer independent from:

- CLI parsing
- GUI widgets
- config files
- remote procedure calls
- agent-generated parameter sets

The most important fields are:

- `width`, `height`, `fps`, `frames`, `warmup`
- `drift_mode`, `drift_downsample`, `phase_every`
- `metrics_stride`, `output_stride`, `gpu_timing_stride`
- `raw_ram_buffer_frames`
- `rgb_min`, `rgb_max`
- `rois`

### `ExampleRoi`

Each ROI is a named rectangle used for metric extraction.

The example computes the mean of each ROI, then derives:

- `difference = roi_a - roi_b`
- `contrast = difference / (abs(roi_a) + abs(roi_b) + epsilon)`

In a real program you might replace or extend that with:

- sum
- standard deviation
- signal-to-noise estimate
- dark/reference-normalized signal
- line profiles
- histogram summaries

## Camera simulation

The example supports two camera backends.

### Device backend

The device backend generates the raw frame directly on the GPU with a CUDA kernel.

This is useful because it removes Python-side generation overhead and lets the benchmark focus on the image pipeline itself.

The mock frame contains:

- a broad base level
- low-frequency structured variation
- a bright gaussian-like signal
- a drifting center position

That drifting signal is what gives the registration stage something meaningful to estimate.

### Host backend

The host backend generates the raw frame in pinned host memory using NumPy.

That path is closer to a CPU-visible capture buffer and is useful when you want to approximate a capture SDK that does not write directly into GPU memory.

In a real integration, you would replace the camera class with logic that reads from:

- a camera SDK ring buffer
- a framegrabber callback
- a NIC receive queue
- a GPUDirect RDMA target

## GPU processing stages

### 1. Blur / low-pass filter

The raw `uint16` frame is blurred with a small 3x3 gaussian-like kernel.

Why it exists:

- removes some high-frequency noise
- stabilizes registration
- mimics a simple low-pass denoising stage

This step outputs a `float32` image.

### 2. Drift estimation

The example supports three drift modes:

1. `phase`
2. `centroid`
3. `none`

#### Phase mode

Phase mode performs classical phase correlation:

1. take a downsampled view of the filtered image
2. compute the RFFT of the current image
3. multiply by the conjugate of the reference FFT
4. normalize the magnitude to get cross-power spectrum
5. inverse FFT to obtain the correlation surface
6. find the peak location
7. convert peak location into an integer translational shift

Why downsample first:

- FFT cost is one of the most expensive parts of the pipeline
- downsampling makes that step much cheaper
- translational drift often varies slowly enough that coarse estimation is acceptable

#### Centroid mode

Centroid mode computes the center of mass of the positive-valued image.

It is much cheaper than phase correlation, but it is also a weaker registration method because it assumes the image content can be summarized by a stable brightness centroid.

#### None mode

This simply disables drift correction.

### 3. Phase cadence control

One of the largest practical throughput levers is `phase_every`.

If `phase_every = 1`, phase correlation runs every frame.

If `phase_every = 8`, the pipeline computes a fresh FFT-based shift every eighth frame and reuses the last shift in between.

Why this works:

- drift in many scientific setups is slow relative to frame rate
- updating phase correlation every frame is often unnecessary
- reusing the last shift is usually much cheaper than recomputing it

This is one of the main reasons the tuned benchmark can exceed the target rate.

### 4. Fused postprocess kernel

After drift estimation, the example uses a fused kernel named `POSTPROCESS_KERNEL`.

This kernel does four jobs in one pass:

1. apply the integer drift-compensation shift
2. update the running background model
3. compute the foreground image
4. optionally write RGB bytes

Why fuse these steps:

- fewer kernel launches
- fewer round-trips through global memory
- simpler dependency chain

If these were separate kernels, the pipeline would read and write large frame buffers more times than necessary.

### 5. Background subtraction

The background model uses an exponential moving average:

`next_bg = prev_bg + alpha * (current - prev_bg)`

The foreground is:

`foreground = current - next_bg`

Why this is useful:

- stable enough for live subtraction
- cheap to compute
- easy to keep on the GPU

### 6. RGB conversion

The example maps the foreground image to grayscale RGB bytes using a linear display window:

`normalized = clamp((foreground - rgb_min) * scale, 0, 1)`

This is intentionally simple. In another program, you might replace this with:

- LUT-based false colour mapping
- gamma correction
- histogram-equalized display mapping
- camera-specific tone curves

## ROI metrics

ROI metrics are intentionally decoupled from frame rate.

The example only computes ROI means when `collect_metrics` is true. That is driven by `metrics_stride`.

This matters because host synchronization and `.get()` calls for metrics can dominate benchmark overhead if they happen every frame.

If your display updates at 60 Hz and acquisition runs at 1200 FPS, then computing metrics every 20th frame is often sufficient.

## Raw frame RAM retention

The example optionally copies every raw frame into a host RAM ring buffer.

This is controlled by `raw_ram_buffer_frames`.

Why it exists:

- many real systems need a recent history of raw frames in RAM
- this adds real copy overhead
- ignoring that overhead can produce unrealistically optimistic benchmarks

The ring buffer prefers pinned host memory when available.

Why pinned memory matters:

- GPU to host copies into pinned buffers are faster and more predictable
- pageable buffers can introduce large latency spikes

This turned out to matter materially in the benchmark runs performed in this repository.

## Cadence controls

The example exposes several cadence controls. These are central to how the prototype reaches high throughput.

### `metrics_stride`

Collect ROI metrics every `N` frames.

For example, at `1200 FPS`, a 60 Hz metric cadence is approximately:

`metrics_stride = 1200 / 60 = 20`

### `output_stride`

Produce RGB output every `N` frames.

This is useful when display cadence is far lower than acquisition cadence.

### `gpu_timing_stride`

Sample CUDA event timings every `N` frames.

This is diagnostic overhead. It is useful during profiling, but should often be disabled in throughput benchmarks.

### `phase_every`

Run phase correlation every `N` frames and reuse the last shift between updates.

This is the main registration-performance tradeoff knob.

## Synchronization model

The example uses one CUDA stream and then synchronizes once per frame.

That is intentionally simple and easy to reason about.

In a production system, another agent should consider extending it to:

- separate copy and compute streams
- a multi-buffer design
- asynchronous frame completion handling
- a dedicated output / display queue
- a dedicated raw recorder thread or process

Why the example still synchronizes each frame:

- benchmark wall time is easier to interpret
- control flow is easier for a handoff example
- correctness is more obvious

This is one of the first things you would restructure in a lower-level production port.

## Benchmark loop

The benchmark loop does the following for every frame:

1. optionally pace the loop to a target rate
2. capture or generate a raw frame
3. optionally copy the raw frame into the RAM ring buffer
4. decide whether metrics/output/timing should run this frame
5. process the frame
6. record wall-clock time
7. collect summary statistics at the end

This separation is important because it allows the benchmark to reflect realistic usage patterns instead of forcing every downstream task to run at the acquisition rate.

## Summary output

The example reports:

- achieved FPS
- frame budget in milliseconds
- average and p95 wall time
- average and p95 GPU timing if collected
- late-frame count if realtime pacing is enabled
- whether the requested rate was met
- average measured shift
- most recent ROI metrics
- raw RAM buffer size and number of raw frames saved

The key criterion used by the benchmark is:

- `achieved_fps >= requested_fps`
- `p95_wall_ms <= frame_budget_ms`

That is stricter than average throughput alone and is a better proxy for “can this path really sustain the requested cadence?”

## What another agent should reuse directly

If another agent is implementing this in a different application, the most reusable ideas are:

1. explicit runtime selection for CUDA/CuPy
2. a configuration object that isolates execution from input/UI code
3. GPU-resident working buffers reused frame to frame
4. a fused postprocess kernel
5. phase-correlation decimation via `phase_every`
6. decoupled cadences for metrics, display, and diagnostics
7. pinned host RAM for raw-frame retention

## What another agent should probably change in production

The example is a guide, not the final architecture.

A production implementation should strongly consider:

1. replacing inline CuPy raw kernels with native CUDA/C++ kernels
2. replacing Python per-frame orchestration with a lower-overhead scheduler
3. using multiple CUDA streams and preallocated ring buffers
4. turning raw-frame RAM retention into an asynchronous producer-consumer path
5. integrating with a real camera SDK or GPUDirect path
6. compiling kernels ahead of time instead of relying on runtime compilation
7. adding better drift-quality validation if `phase_every > 1`

## Porting checklist

If another agent is porting this into a new program, the recommended order is:

1. Build the runtime/bootstrap layer.
2. Implement raw frame ingestion.
3. Allocate persistent GPU buffers.
4. Implement the blur stage.
5. Implement drift estimation.
6. Implement the fused postprocess stage.
7. Add RGB conversion and ROI metrics.
8. Add cadence controls.
9. Add raw-frame RAM retention.
10. Add summary/telemetry output.
11. Benchmark with realistic cadences, not just all-features-every-frame.

## Recommended interpretation of this example

Treat this example as a reference architecture with working defaults and explicit tradeoffs.

Do not copy every detail blindly.

Instead, another agent should use it to answer these questions in the target program:

1. Which work must happen every acquisition frame?
2. Which work can happen at display cadence?
3. Which work can happen on a different thread or stream?
4. Which buffers must remain on the GPU?
5. Which data must be copied to host RAM?
6. What latency metric actually matters for the application?

That framing is the main point of this example.
