# Camera Pipeline >1000 FPS — Implementation Plan

Branch: `CUDA-Interop`

## Objective

Run the **entire** per-frame pipeline at **>1000 FPS (≤1 ms/frame) with every processing option
enabled**, at 1200×1200 (stretch goal: 1500×1500). The pipeline, end to end:

1. Read frame from camera into host memory
2. Upload to GPU
3. Science processing: drift correction → LP filter → background removal → difference/contrast → crop
4. ROI mean calculation
5. Colormap (autoscale + LUT → RGBA)
6. Update the display texture

"Every option enabled" = drift correction **on**, LP filter **on**, background removal **on**,
Difference/Contrast mode (uses the zero reference), crop **on**, ≥4 ROIs, autoscale **on**.

---

## Current state (measured, RTX 4070 SUPER)

Already completed (Phase B — these stay):
- Science pipeline runs on GPU (CuPy). `process_frame` with features **off** ≈ **0.23 ms (4600 fps)**.
- Raw frame uploaded as `uint16` and converted to float32 on-device (CPU convert was 1.33 ms/frame).
- Colormap moved out of the hot path; runs lazily at display rate.
- Display RGBA transfer moved **outside** `processed_frame_condition` and into a **pinned** host buffer.
- Live feed uses a **raw** texture refreshed with `dpg.set_value(tex, buf)` (**0.001 ms**, GIL-friendly)
  — NOT a dynamic texture (`set_value` there is **24 ms** and holds the GIL). RGB raw textures render
  black on DX11 (`R32G32B32_FLOAT` not sampleable), so the feed is RGBA.

**The wall — with ALL features enabled:**

| | 1200² | 1500² |
|---|---|---|
| `process_frame` (all features) | **3.70 ms → 270 fps** | **5.11 ms → 196 fps** |
| capture-loop CPU copies (3× full-frame) | 1.19 ms → 837 fps | 1.97 ms → 507 fps |

Per-stage GPU cost @1200² (the 3.70 ms):

| stage | ms | notes |
|---|---|---|
| BG removal (`uniform_filter`, σ=20) | **0.83** | largest single kernel; cupy box filter is overhead-heavy |
| drift `phase_cross_correlation_ds` | **0.50** | FFT + **D2H of corr-map + CPU argmax** (sync point) |
| autoscale `min`+`max` | 0.15 | 2× synchronizing `float()` |
| LP IIR | 0.16 | elementwise |
| drift `gpu_shift` | 0.10 | |
| contrast | 0.09 | |
| + uint16 H2D ~0.34, colormap ~0.56, 4× ROI means, ~20 kernel launches, allocations | balance | |

### Root cause — it's overhead, not compute
The frame is **5.76 MB** (1200²·float32). The GPU has ~**500 GB/s**. A fully fused pipeline making
~8 full-frame memory passes moves ~46 MB → **~0.1 ms of actual memory-bound work**. The measured
3.70 ms is **~30× the compute floor**, spent on:
1. **~20 separate CuPy ops/frame**, each a Python call + CUDA kernel launch (~10–30 µs each) + a fresh
   device allocation for the result.
2. **Mid-pipeline synchronizing `float()` calls** — drift's corr-map D2H + CPU `argmax`, and autoscale
   `min`/`max` — each forces a **full device sync**, destroying pipelining.
3. **`uniform_filter`** using a generic, non-fused implementation.
4. **3× full-frame CPU copies** in the capture loop (837 fps ceiling before processing).
5. **GIL** serialization across capture/processing/display/ROI threads.

**Conclusion:** >1000 fps with all features is firmly achievable — the hardware floor is ~0.1–0.3 ms.
The work is to remove overhead: **fuse kernels, eliminate mid-pipeline syncs, do reductions/argmax on
the GPU, capture the per-frame sequence in a CUDA graph, and make capture zero-copy.**

---

## Strategy — two tiers

Both target the same architecture; Tier A stays in Python, Tier B is the C++ endpoint. **Do Tier A
first** — the tooling is already installed (verified: `cp.RawKernel`, `cp.RawModule`, CUDA **graph
capture+launch**, cuFFT, `ReductionKernel`) and it is expected to clear 1000 fps at 1200².

### Tier A — Fused CuPy kernels + CUDA graph (in-process Python)
Keep CuPy, but replace the ~20 op chain with a handful of **custom fused CUDA kernels** (`cp.RawKernel`/
`cp.RawModule`, CUDA C written inline), do all reductions/argmax on-device, and **capture the whole
per-frame sequence into a CUDA graph** that replays with near-zero launch overhead. No separate build
toolchain; ships with the existing app.

### Tier B — C++/CUDA acquisition engine (pybind11), GIL-free
A compiled module owning a C++ thread that releases the GIL: camera read → async H2D → fused CUDA
kernels → ROI/colormap → result, orchestrated by a CUDA graph, with Python only starting/stopping and
reading the latest result. The definitive path for 1500²+, heavy feature stacks, or removing the GIL
tax entirely. Tier A's `.cu` kernels port directly into Tier B.

---

## Detailed plan

### 1. Zero-copy capture (removes the 837 fps copy wall)
- Pre-allocate a ring of **N pinned host buffers**; queue them to the Andor SDK (`cam.queue(buf)`),
  which DMAs frames directly into them. `wait_buffer` returns a filled buffer; **store its reference**
  for history/latest (no `np.array(copy=True)`), and re-queue the evicted buffer.
- `latest_frame` becomes a reference to the newest ring slot; the processing thread already reads it
  by reference. Net: **0 full-frame CPU copies** vs 3 today.
- Rework `TypedDeque`: for live capture, store buffer references in the ring (the contiguous-array
  copy used by `range_array` for save/ROI-history is rebuilt lazily only when saving, off the hot path).
- Pinned host buffers also make the subsequent H2D a true async DMA.

### 2. Async upload, overlapped (CUDA streams)
- Upload `uint16` on a dedicated **upload stream** with `cudaMemcpyAsync` from the pinned ring slot;
  convert to float32 in the first fused kernel (not a separate op).
- Double-buffer so H2D of frame N+1 overlaps compute of frame N (separate copy engine). At ~0.15 ms
  pinned H2D this mostly hides under compute.

### 3. Fused science kernels (replace ~15 ops with 2–3)
Write as `cp.RawModule` CUDA C. Target kernel set:

- **K1 `convert_shift_lp`**: reads `uint16` input (+ previous-filter state), applies the integer/sub-
  pixel **drift shift** (bilinear, shift passed in as 2 floats from K0 below), and the **LP IIR**
  recurrence, writing `float32` filtered output. One pass, one launch.
- **K2 `bg_subtract`**: background removal as a **separable box filter** (two 1-D passes in shared
  memory) — replaces the 0.83 ms `uniform_filter`. A separable box is ~2 memory passes ≈ tens of µs.
  Subtract-and-clip fused into the second pass. (Alternative: integral-image/summed-area table for
  O(1)-per-pixel independent of σ; box filter is simpler and already far faster.)
- **K3 `contrast_crop_color_roi`**: difference/contrast vs the cached `zero` (also BG-subtracted once,
  cached when zero changes), apply crop mask, then in the **same** kernel: accumulate per-ROI sums
  (atomic adds into a small per-ROI accumulator) and global min/max for autoscale (block reductions),
  and — if coloring per frame — write the RGBA via the LUT. Single fused pass over the frame.

Design rules:
- **No `float()` mid-pipeline.** Drift offsets, autoscale min/max, and ROI means stay in small device
  arrays; a single tiny D2H (a few floats) happens once per frame after the graph runs.
- All intermediates are **pre-allocated** persistent buffers (no per-frame `cp.empty`).

### 4. Drift correction without a CPU sync
- Keep cuFFT phase cross-correlation on the **downsampled** frame (reference FFT cached).
- Replace the `to_cpu(corr) + np.argmax` with a **GPU argmax** (`cp.argmax` / a reduction kernel) and
  GPU-side sub-pixel quadratic refinement, leaving the (dy,dx) shift as a 2-element **device** array
  consumed directly by K1. Removes the per-frame corr-map D2H + CPU work (~0.3 ms + a sync).
- The small downsampled FFT (~300²) is cheap; the win is deleting the sync.

### 5. ROI means + autoscale = on-GPU reductions
- ROI sums via atomics or per-ROI `ReductionKernel` fused into K3; divide by area on the host from the
  tiny result vector. No Python per-ROI loop, no per-ROI `float(xp.mean(...))` sync.
- Autoscale min/max via the same fused reduction. One combined D2H of `[roi_sums…, min, max, dy, dx]`
  per frame.

### 6. CUDA graph capture
- After warmup, **stream-capture** the full per-frame sequence (H2D → K1 → K2 → K3 → tiny D2H) into a
  `cp.cuda.Graph` and `launch()` it each frame. This collapses ~20 launches into ~1, removing the
  dominant Python/launch overhead. Re-capture only when shape/feature-set/zero changes (graphs are
  keyed on the static op sequence; toggling a feature rebuilds the graph).
- Inputs/outputs are fixed device buffers the graph reads/writes; per frame we only copy the new
  `uint16` into the pinned input slot and `launch()`.

### 7. Texture update — decouple from the 1000 fps compute
- The colormapped RGBA is produced on-GPU (K3, or a separate display-rate kernel). The **screen** only
  needs ~display rate; pull RGBA to the pinned texture buffer and `set_value(raw_tex, buf)` at ≤display
  Hz. Producing color *per frame* (e.g. to save colored frames) stays cheap because it's fused into K3;
  pushing it to the monitor faster than refresh has no value and `render_dearpygui_frame` can't anyway.
- (Optional, later) true zero-copy display via CUDA↔DX11 interop needs a DPG fork to expose the texture
  handle — not required to hit the target; keep the cheap `set_value(raw)` path.

### 8. Threading
- **Tier A:** one acquisition+processing thread runs `graph.launch()` per frame; display/ROI consumers
  read published results at their own rate (already decoupled). Minimize Python glue in the loop (the
  graph is one call). The GIL is held only for the thin launch + a few-float D2H — small enough at 1 ms.
- **Tier B:** the loop is a C++ thread with the GIL released; Python never touches the hot path.

---

## Tier B — C++/CUDA module (if/when needed)

- `pybind11` extension `fastcam` exposing an `AcquisitionEngine`:
  - `start(config)`, `stop()`, `get_latest_rgba()` (into a shared pinned/mapped buffer),
    `get_roi_means()`, `get_history_for_save()`.
  - Owns: Andor SDK3 buffers (or receives frames from the existing Python capture), pinned ring,
    CUDA streams/events, the `.cu` kernels (ported verbatim from Tier A), and the CUDA graph.
- Build: CMake + `scikit-build-core` (or `setuptools` + `nvcc`); requires the CUDA Toolkit (`nvcc`) at
  build time. Ship a prebuilt wheel for the lab machine to avoid per-machine compilation.
- Python side shrinks to: start/stop, poll latest RGBA for the texture, poll ROI means for plots,
  pull history on Save. All visible DPG mutations stay on the render thread.

---

## Expected budget after the work (1200², all features)

| stage | target |
|---|---|
| capture (zero-copy ref) | ~0 |
| pinned async H2D (overlapped) | ~0.15 ms (hidden) |
| fused K1+K2+K3 (incl. drift shift, LP, BG, contrast, crop, ROI, autoscale, color) | ~0.3–0.5 ms |
| drift FFT + GPU argmax | ~0.15 ms |
| tiny D2H (few floats) + graph launch | ~0.05 ms |
| **total compute** | **~0.6–0.8 ms → >1200 fps** |

Screen texture update runs at display rate, off the critical path. 1500² is ~1.6× the memory work →
~1.0–1.3 ms; if it must clear 1000 fps with all features, Tier B + further kernel tuning closes it.

---

## Milestones

1. **M1 — Zero-copy capture** (Python). Ring of pinned camera buffers; references not copies. Verify
   capture loop clears ~1000 fps feeding a no-op consumer. *(removes the 837 fps wall)*
2. **M2 — Drift argmax on GPU.** Remove the corr-map D2H/CPU argmax; (dy,dx) stays on device.
3. **M3 — Fused kernels K1/K2/K3** as `cp.RawModule`, replacing the op chain; pre-allocated buffers;
   reductions on GPU. Validate numerical parity vs current `process_frame` (max abs diff within tol).
4. **M4 — CUDA graph capture** of the per-frame sequence; re-capture on feature/shape change.
5. **M5 — Integrate + benchmark** all-features end-to-end at 1200²/1500² with the real camera; confirm
   >1000 fps and unchanged display/ROI/save behavior.
6. **M6 (conditional) — Tier B** C++/CUDA engine if Tier A misses target at the required resolution.

## Validation
- **Numerical parity:** for each feature combination, assert the fused output matches the current
  reference pipeline within float tolerance (regression test on synthetic frames).
- **Throughput:** measure `process` fps with all features at 1200²/1500² (mock can now expose ≥1000 fps
  after the earlier mock fix; real camera for the true number).
- **Behavior:** live feed updates continuously; ROI traces/thumbnails correct; difference/contrast,
  drift, LP, BG visually correct; acquisition `.npz` save unchanged; mock + CPU-fallback still start.

## Risks & mitigations
- **Kernel correctness** (drift shift, separable box, contrast edge cases): port one stage at a time
  behind a feature flag, parity-tested against the current implementation.
- **CUDA graph rigidity:** any change in op sequence (feature toggle, shape, ROI count) must trigger a
  re-capture; key the cached graph on (shape, feature-mask, n_rois, zero_version).
- **`nvcc`/toolkit dependency (Tier B):** ship a prebuilt wheel; keep Tier A as the no-build fallback.
- **GIL (Tier A):** if the thin per-frame Python glue still caps below target, escalate to Tier B.
- **Camera interface bandwidth:** 1200²·2 B·1000 fps = **2.88 GB/s** — requires USB3.1 Gen2 / CameraLink
  / CoaXPress. This is a hardware prerequisite independent of the software pipeline; confirm the
  camera+interface can sustain the target frame rate.
- Keep CPU-only and mock-camera paths working (generic CuPy path as fallback when kernels/graph
  unavailable).

## Session validation findings (measured — these constrain the build)

Experiments run against the live pipeline + a parity harness (deterministic synthetic frames,
all features on, 1200²):

- **All-features baseline:** ~2.5–2.9 ms (~350–390 fps). BG `uniform_filter` ~0.67 ms and drift
  FFT ~0.5 ms together exceed the 1 ms budget on their own.
- **Per-op sync reduction backfires:** moving drift argmax to GPU + slice transfers added syncs and was
  *slower* (2.55→2.90 ms). What matters is the **number** of host syncs, not transfer size. Reverted.
- **CUDA graphs cannot wrap cupy library ops:** capturing a chain containing `uniform_filter`/FFT and
  launching it fails with `cudaErrorInvalidValue` (internal allocations during capture). ⇒ the graph
  win **requires every stage to be an alloc-free custom kernel** writing to pre-allocated buffers.
- **Beating the libraries is hard:** two straightforward exact BG kernels were *slower* than cupyx —
  integral-image (cumsum) 0.79 ms, naive separable running-sum 1.09 ms, both vs 0.67 ms. Interior
  parity was exact (diff ~2e-4). A faster exact BG needs a **tiled, coalesced, shared-memory**
  separable kernel (real CUDA tuning), not a first-pass implementation.
- **Toolchain present:** nvcc 13.0, CUDA Toolkit v13.0, cupy on CUDA 13.0 (interop clean), VS2019
  installed (cl.exe not on PATH). **pybind11 not installed.** cupy `RawModule` compiles CUDA C at
  runtime via NVRTC → the exact kernels can be written/validated *without* the MSVC+nvcc+pybind11
  build; the C++/pybind11 wrapper only adds the GIL-free thread.

**Implication:** >1000 fps all-features @1200² is feasible (theoretical ~0.85 ms) but **only** via a
*complete* alloc-free fused-kernel pipeline that out-performs cupy/cupyx, plus a CUDA graph — an
all-or-nothing build (partial kernelization gives no graph benefit). This is a specialist CUDA effort
with iterative profiling and **real-camera/real-resolution validation checkpoints**, not a single-pass
change. The kernels (tiled box BG, bilinear shift reading device dy/dx, fused contrast+crop+ROI-reduce+
autoscale+color, GPU drift argmax/subpixel) are the deliverables; each must be parity-tested AND
profiled to confirm it beats the library op before it earns its place in the graph.

## Recommendation
Implement **Tier A** (M1–M5). The installed CuPy supports RawKernels + CUDA graphs, the hardware floor
is ~0.1–0.3 ms, and fusing + de-syncing + zero-copy capture is expected to land all-features 1200² well
above 1000 fps without leaving Python. Reserve **Tier B** (C++/CUDA pybind11, GIL-free) for 1500²+ or
as the guaranteed endpoint; its kernels are the same `.cu` code.

---

## Option C build — informed by the prototype + GPU Express (IN PROGRESS)

Decision (user): **Option C** — both background modes, selectable (exact spatial `uniform_filter`
AND fast temporal-EMA), so 1000 fps runs can use EMA while exact science keeps the spatial filter.

### Andor GPU Express — investigated, NOT adopted
GPU Express (Andor, v1.0 2015) is **not** GPUDirect/RDMA. Path: camera SDK3 → pinned **Input CPU
Buffer** → `AT_GPU_CopyInputCpuToInputGpu` (async H2D in a CUDA stream) → Input GPU Buffer → user
CUDA kernels (`AT_GPU_CallUserFunction`). The CPU pinned buffer stays in the path; the value is
stream-overlapped H2D + buffer/stream management. **Blockers:** built against CUDA 6.0/7.0
(CC 2.0/3.0) — cannot target the RTX 4070 SUPER (Ada `sm_89`, needs CUDA ≥11.8) and won't interop
with our CuPy/CUDA-13 context; C-only, no Python. **Verdict:** replicate its proven model
(pinned input-buffer ring + CUDA-stream-overlapped H2D + persistent GPU buffers) **natively in CuPy**
— which the prototype already does at 1200 fps. Re-evaluate only if Andor ships a current CUDA-12/13
GPU Express build.

### Techniques adopted from the prototype (`src/APIs/Render_Pipeline_Example/`)
1. **`phase_every` drift cadence** — recompute phase-correlation every N frames, reuse the shift
   between. ✅ **DONE** (`ProcessingSettings.phase_every`, default 1 = exact). Measured all-features
   @1200²: 307 fps (every-frame) → 400 fps (every-4/8), parity-exact at default.
2. **Fused postprocess RawKernel** — shift + difference/contrast + crop-mask in one alloc-free pass
   (prototype's `POSTPROCESS_KERNEL` adapted). (pending)
3. **Persistent pre-allocated GPU buffers** keyed on shape; reused frame-to-frame. (pending)
4. **Decoupled cadences** — strided ROI means at display rate (avoid per-ROI per-frame `.get()`);
   colormap already display-rate. (pending)
5. **Pinned input buffer + CUDA-stream-overlapped H2D** (GPU Express's core technique, native CuPy);
   ties into the zero-copy capture ring. (pending)
6. **BG mode switch (Option C):** temporal EMA (`bg += α(cur−bg)`, fused, ~free) OR spatial
   `uniform_filter` (exact). `ProcessingSettings.bg_mode ∈ {"spatial","temporal"}`. (pending)

### Build sequence (each parity-validated via the harness; EMA mode validated separately as new science)
M-a phase_every ✅ → M-b persistent buffers + strided ROI → M-c fused elementwise RawKernel
(shift+contrast+crop) → M-d BG temporal-EMA mode fused into the kernel → M-e pinned/stream H2D +
capture ring → M-f benchmark all-features @1200²/1500², drift on, EMA BG, target >1000 fps.

### Result — >1000 fps target ACHIEVED (compute path, validated headless)

Measured all-features @1200² (drift+LP+BG+contrast+crop+2 ROIs), incl. H2D, RTX 4070 SUPER:

| mode | phase_every=1 | phase_every=8 |
|---|---|---|
| spatial (exact `uniform_filter`) | 365 fps | 444 fps |
| **temporal (fused EMA kernel)** | 784 fps | **1300 fps** ✅ |

- Fused kernel (`src/Utils/fused_kernels.py`, `lp_ema_postprocess`) does LP + temporal-EMA BG +
  difference/contrast in one alloc-free launch; **validated numerically correct** vs a pure-CuPy
  reference of the same math (max|diff| 5.6e-5).
- Spatial mode is **unchanged / parity-exact** (regression harness max|diff| 0).
- `phase_every` (default 1, exact) and `bg_mode` (default "spatial", exact) are both opt-in — the
  default pipeline behaviour is byte-identical to before.

**Done:** M-a (phase_every), M-c+M-d (fused temporal kernel + selectable bg_mode, Option C). Spatial
path preserved.

**Remaining (clearly scoped):**
- **UI exposure** — add `bg_mode` (Spatial/Temporal) + `phase_every` + `bg_temporal_alpha` controls to
  FeedControls, wired through CameraFeed with SaveState/LoadState + `_collect_settings_snapshot`
  (per AGENTS.md "setting in 3 places"). Needs visual validation in the running app.
- **M-e** — pinned input buffer + CUDA-stream-overlapped H2D + zero-copy capture ring (camera-side;
  GPU Express's technique done natively). Needs the real Zyla to validate; not required to clear
  1000 fps on the compute path (temporal pe=8 already includes H2D at 1300 fps).
- **Real-hardware confirmation** of the temporal-mode science and end-to-end fps on the Zyla.

**Note on temporal-mode science:** temporal mode subtracts a per-pixel exponential-moving-average
background (`bg += α(cur−bg)`, α=`bg_temporal_alpha`) instead of the spatial `uniform_filter`, and its
difference/contrast are taken relative to that EMA background (no static zero frame). This is the
deliberate Option-C trade for >1000 fps; spatial mode remains for exact static-illumination removal.

### UI controls wired (ready for live validation)
FeedControls now exposes (with full SaveState/LoadState + acquisition-snapshot persistence):
- **BG Mode** combo: `Spatial` (exact, default) / `Temporal` (fast EMA, >1000 fps path)
- **EMA Alpha** float (temporal-mode background rate; enabled only in temporal mode)
- **Drift Every N** int (`phase_every`; 1 = every frame, exact)

Wired through `CameraFeed` callbacks → `Andor.settings`; defaults unchanged so the pipeline is
byte-identical until the user opts in. App constructs/runs clean (headless smoke). **Next: user
validates live on the Zyla** — flip BG Mode to Temporal, raise Drift Every N (e.g. 8), confirm the
feed + Processing FPS and eyeball the temporal-mode science. **M-e** (pinned/stream H2D + zero-copy
capture ring) remains, needs the real camera, and is not required to clear 1000 fps on the compute path.

---

## Robust GIL-free backend — DELIVERED (C++/CUDA via pybind11)

Root cause of "Processing FPS < Capture FPS" was confirmed to be **GIL contention**: the Dear PyGui
render loop holds the GIL in long chunks and starves the camera processing thread (capture is
I/O-bound and unaffected). Two fixes shipped:

**1. `sys.setswitchinterval(...)` at startup** (`WidefieldController.setup`) — finer GIL hand-off so
the processing thread interleaves with the render loop each frame. Cheap; recovered ~920→1000 fps
under a simulated 10 ms/60 Hz UI GIL hold.

**2. `fastproc` — GIL-free C++/CUDA engine** (`src/APIs/fastproc/`, loaded via
`Utils/fast_backend.py`). One pybind11 call (`Engine.process`) runs the whole temporal per-frame
pipeline — H2D (pinned) + integer drift shift + LP IIR + temporal-EMA background +
difference/contrast + ROI means — with **`py::gil_scoped_release`**, so the render loop can never
starve it. The processed frame stays on the GPU (exposed as a CuPy view for the display colormap).

Build: `src/APIs/fastproc/build.bat` (nvcc 13 + VS2019 → `fastproc.pyd`). Verified toolchain:
pybind11 3.0.4, CUDA 13.0, MSVC 14.29. Uses the device primary context (interoperates with CuPy).

**Validation (headless, RTX 4070 SUPER):**
- Numerically **bit-identical** to the CuPy temporal path with drift off (max|frame diff|=0.0;
  ROI means match to 1e-10). With drift on it uses integer (not sub-pixel) shift with wraparound —
  the documented fast-path approximation.
- Throughput under a 10 ms/60 Hz UI GIL hog @500²: **CuPy 1224 fps → fastproc 2391 fps (~2×)**, and
  far more resilient as the UI gets heavier (one GIL release/frame vs many).

**Integration:** `Andor.process_frame` routes to `_process_frame_cpp` when
`settings.use_cpp_backend and bg_mode=="temporal"` and the extension is present; otherwise the CuPy
path runs. Any backend error falls back to CuPy. **Off by default** — exposed as the **"C++ Backend
(temporal)"** checkbox in Feed Controls (persisted; in the acquisition snapshot). So the default app
is unchanged; flip BG Mode→Temporal + C++ Backend to use it.

**Still needs real-hardware validation** on the Zyla: confirm Processing FPS now tracks Capture FPS
with the UI running, and eyeball the temporal-mode science. Future: sub-pixel shift in the kernel,
and porting the spatial-BG/colormap into the engine if a fully GIL-free exact path is wanted.

---

## fastacq — fully GIL-free C++/CUDA acquisition engine (BUILT + VALIDATED)

The remaining `setswitchinterval`/`fastproc.process()` approaches still had Python in the per-frame
loop (event handshake + GIL to enter/exit the call), capping ~950 fps. The robust fix: move the
**entire capture→process loop into a C++ `std::thread`** that never touches the GIL.

**`src/APIs/fastacq/fastacq.cu`** — `AcquisitionEngine(H,W)`:
- A C++ worker thread runs the whole pipeline: frame source → integer drift shift (cuFFT phase
  correlation, `phase_every`) → LP IIR → temporal-EMA background → difference/contrast → crop → ROI
  means → autoscale → colormap(LUT) → **RGBA float32**.
- **Frame sources:** `"mock"` (frames generated on the GPU by a CUDA kernel — no Python at all) or
  `"push"` (real camera: Python's Andor thread calls `submit(raw_u16)`; worker consumes).
- **`output_stride`** decouples the expensive display products (autoscale + colormap + 16 B/px RGBA
  D2H + ROI means) from the science loop — produced every N frames (≈display rate), not every frame.
  This is what unlocks the throughput (matches the prototype's cadence idea).
- Latest RGBA is **double-buffered** in pinned host memory; `get_latest_rgba(out)` copies it. ROI
  means + `capture_fps()`/`processing_fps()` are published atomically. cudart is static; only
  `cufft64_12.dll` is an external dep.

**Build:** `src/APIs/fastacq/build.bat` (nvcc 13 + `-lcudart -lcufft` + MSVC). Loader
`Utils/fast_acquisition.py` adds the **CUDA v13 toolkit `bin\x64`** to the DLL search path (the
machine has both v12.9 and v13.0; `CUDA_PATH` pointed at v12.9 — fastacq needs v13's `cufft64_12.dll`).

**Measured (RTX 4070 SUPER, ALL features: drift+LP+EMA-BG+contrast+crop+2 ROIs+autoscale+colormap):**
| resolution | processing FPS | under a 10 ms/60 Hz Python GIL hog |
|---|---|---|
| 500×500 | **8313** | 8344 |
| 1200×1200 | **2014** | 2005 |

The GIL hog has **no effect** — the engine is fully independent of the renderer. Pipeline correctness
sanity-checked (animation live, drift/ROI responsive, valid RGBA).

### Integration plan (remaining — needs the real Zyla + GUI to validate)
The renderer becomes a thin client of the engine:
1. **Settings → `fastacq.Config`** mapping (drift/phase_every/lp coeffs/alpha/mode/crop/autoscale/
   scale/LUT/ROI rects/zero/output_stride from the Feed Controls). `set_lut`, `set_rois`, `set_zero`.
2. **Preview start (engine mode):** instead of `Andor.start_capture_continuous` + the Python
   processing thread, `engine.start("mock")` (dev) or `engine.start("push")` + the Andor capture
   thread calls `engine.submit(raw)` per frame.
3. **Display:** `CameraFeed.render()` calls `engine.get_latest_rgba(self._raw_texture_buffer)` +
   `set_value`; ROI traces from `engine.get_roi_means()`; the overlay FPS from
   `engine.capture_fps()/processing_fps()`.
4. **Stop / acquisition-save:** `engine.stop()`; the fixed-acquisition `.npz` path still needs the
   raw-frame history (either keep the Python capture deque in push mode, or add a raw-ring to the
   engine).
Gate all of this behind a `use_acquisition_engine` flag with the existing pipeline as the default,
and validate live on the camera. Sub-pixel drift shift in the kernel is a later refinement (currently
integer shift with wraparound, the documented fast-path approximation).

### Integration — WIRED INTO THE LIVE APP (mock-validated; Zyla pending)

The engine is now a selectable preview backend, fully behind the **"Acquisition Engine (GIL-free)"**
checkbox in Feed Controls (default **off**; persisted + in the acquisition snapshot). Default app
behaviour is unchanged.

**Worker refactor for live reconfigure:** every `AcquisitionEngine` setter
(`configure`/`set_lut`/`set_rois`/`set_zero`) now only **stages** data + sets a dirty flag under a
mutex; the worker thread applies *all* CUDA/cuFFT ops on its own stream at the top of each iteration.
So the renderer can reconfigure the running engine every frame with no cross-thread CUDA race.

**Push mode for both mock and Zyla.** The renderer uses one uniform path: the Andor capture loop
produces frames → the Python processing thread's only per-frame job is `engine.submit(raw_u16)` (a
GIL-released memcpy) → the GIL-free worker does everything else. (Testing with the Andor mock camera
therefore exercises the exact path the Zyla will use.)

Code wired:
- **`CameraControls`** — `_engine_start_preview()` (build `AcquisitionEngine(H,W)` from
  `Andor.frame_shape`, `apply_engine_settings`, `engine.start("push")`, set `Andor.active_engine`) /
  `_engine_stop_preview()` (detach `active_engine` then `engine.stop()`); `toggle_preview` branches on
  `camera_feed.use_acquisition_engine`. Falls back to the CuPy pipeline if the extension is absent.
- **`Andor`** — `active_engine`; the processing `_loop` submits to it when set (else the CuPy path);
  `get_processing_fps()` delegates to `engine.processing_fps()`.
- **`CameraFeed`** — `apply_engine_settings(engine)` maps `Andor.settings` → `fastacq.Config` (+ LUT /
  ROI rects / zero), guarded by cached signatures so the engine is only re-touched on actual change;
  `_engine_rects()` builds the `int32 [y0,y1,x0,x1]` rect array from the `ProcessingROI`s;
  `render()` branches to `_render_from_engine` → `get_latest_rgba` into the raw texture + `set_value`,
  and feeds `get_roi_means()` to the trace plots by tag.
- **`RegionOfInterest.feed_external_value(value)`** — engine-mode trace accumulator (the normal
  incremental worker has no `processed_frame` to read in engine mode).

**Validated (headless, mock camera, RTX 4070 SUPER):** GUI launches clean; enabling the engine +
starting preview gives **capture 986 fps == processing 986 fps** (processing tracks capture exactly —
the GIL-contention symptom is gone), display throttled to ~57 fps via `output_stride`; a created ROI
yields a live engine mean and an accumulating trace; stop cleanly detaches (`active_engine → None`).
(986 fps here is the Andor *mock* generation rate; the engine's own headroom is 2014 fps @1200² /
8313 fps @500² as above.)

**Known semantics / limitations (documented; fix with the Zyla / later):**
- The engine implements the **temporal-EMA** pipeline only: background removal is always the EMA model
  (`alpha`=`bg_temporal_alpha`); **Normal and Difference produce the same** background-subtracted
  output (only Contrast differs), and Difference/Contrast use the running EMA background as the
  reference — `zero_frame` is used *only* as the drift template, not as a static subtraction reference.
- **ROI-mean traces accumulate at display (`output_stride`) cadence**, not the full capture rate
  (means are a strided display product).
- Drift is **integer** shift with wraparound edges (sub-pixel is a later kernel refinement).
- Colormap normalization is single-sided (`[mn,mx]`); diverging maps are not yet zero-centred.
- The **fixed-acquisition `.npz` save path is unchanged** (still the Andor/CuPy path) — the engine is
  preview-only; a raw-ring in the engine for engine-mode saves is future work.

**Next:** user validates live on the Zyla (flip the checkbox, Start Preview; confirm feed + that
Processing FPS now tracks Capture FPS with the UI running) and fixes any hardware-specific bugs.
