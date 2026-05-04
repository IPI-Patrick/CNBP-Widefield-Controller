
# Widefield Controller Agent Guide

This repository is a Dear PyGui desktop application for coordinating a widefield imaging workflow. The current app combines three main runtime surfaces:

- Camera control and live image analysis
- Laser and power-meter control
- PicoScope acquisition and AWG control

The codebase is small enough that most behavior is still wired directly through window classes, but there are a few important architectural boundaries that future agents should preserve.

## Startup And Object Model

The app entrypoint is `src/WidefieldController.py`.

- `setup()` creates the Dear PyGui context and viewport.
- It dynamically imports every Python file in `src/Windows/` using `Utils.utils.load_window_classes()`.
- Every discovered class is instantiated immediately and appended to `Utils.shared_state.class_objects`.
- After construction, `LoadState()` is called on each object if present.
- The main render loop calls `render()` on every object once per Dear PyGui frame.

Important implications:

- Any new `.py` file placed directly in `src/Windows/` will be treated as a top-level window module and instantiated automatically.
- Helper modules should not live directly in `src/Windows/` unless they are intended to create a window on startup.
- Most windows perform substantial work inside `__init__`, not in a separate bootstrapping phase.

## Shared State And Persistence

Shared global state is intentionally minimal and currently lives in `src/Utils/shared_state.py`.

- `class_objects` holds the instantiated top-level windows.
- `shared_andor` exposes the camera driver instance to other windows when needed.

UI state persistence lives in `src/Utils/state_persistence.py`.

- State files are stored under `AppState/` as JSON.
- Most windows and subwindows implement `SaveState()` and `LoadState()`.
- `src/WidefieldController.py` autosaves state roughly once per second and again on exit.

If you add a new long-lived window or subwindow, add explicit save/load support. The repo already expects windows to restore position, visibility, and key configuration values.

## Top-Level Windows

### `src/Windows/CameraControls.py`

This is the main camera workflow controller.

- Owns the `Andor` driver instance.
- Builds the camera settings UI and acquisition controls.
- Owns the `CameraFeedWindow` subwindow tree.
- Coordinates preview start/stop.
- Coordinates fixed-length acquisitions that run camera capture and PicoScope collection together.
- Saves acquisition results to a compressed `.npz` file via the **Save** button, which is enabled once a fixed acquisition completes or is stopped early.
- The Camera Controls `Hardware Reqs` estimate must stay aligned with every captured payload element. If acquisition capture or save contents change, update that calculation too so RAM, disk-space, and bitrate estimates still include all captured data.
- **If you add a new setting widget to Camera Controls, you must also add it to `_collect_settings_snapshot()`** so it is included in every saved `.npz`. See the Acquisition Save Format section below for the full picture.

It also contains cross-device experiment logic, including:

- optional zeroing on acquisition start
- optional AWG configuration and delayed AWG enable
- preview-start behavior such as ROI rebuilds and first-frame automatic zero creation

### `src/Windows/LaserControls.py`

This window manages the laser driver and the PM1000 power meter.

- Connects to the laser over COM.
- Connects to the PM1000 and starts continuous measurement.
- Shows target power, actual laser power, and measured PM1000 power.
- Maintains rolling history plots for laser power and PM1000 readings.

This window is mostly self-contained and is driven by polling during `render()`.

### `src/Windows/PicoScope.py`

This is the oscilloscope and AWG control surface.

- Owns the `Drivers.PicoScope.PicoScope` driver.
- Manages device discovery, open/close, channel enablement, sample settings, and AWG settings.
- Owns the `OscilloscopeWindow` subwindow for plotting active traces.
- Keeps UI settings disabled while collection is running.

The control window is intentionally separate from waveform rendering. Channel configuration lives here; plotting lives in the oscilloscope subwindow.

## Camera Feed And Image Pipeline

The camera/image path is split across:

- `src/Drivers/Andor.py`
- `src/Windows/SubWindows/CameraFeed.py`
- `src/Windows/SubWindows/FeedControlsWindow.py`
- `src/Windows/SubWindows/RegionOfInterest.py`
- `src/Windows/SubWindows/ROIsWindow.py`
- `src/Windows/SubWindows/ImageWindow.py`

### Driver-Side Camera Data Ownership

`Andor` is the canonical owner of frame history and derived image caches.

It maintains:

- `acquisitions`: raw frames
- `filtered`: low-pass filtered frames
- `difference`: zero-referenced difference frames
- `contrast`: zero-referenced contrast frames
- `timestamps`
- `meanBuffer`
- `latest_frame`, `latest_filtered`, `latest_difference`, `latest_contrast`
- `zero`: stored as `float32`
- `zero_version`: monotonic counter for zero updates

These buffers are protected by `frame_lock`.

Derived frame rules:

- Difference mode is `current - zero`
- Contrast mode is `(current - zero) / zero * 100`, with divide-by-zero avoided using `np.divide(..., where=...)`
- LP filtering is applied before difference/contrast if the filter is enabled

This means future agents should treat zero-referenced modes as driver-backed cached products, not as UI-only transforms.

### Live Preview Flow

The normal live path is:

1. `CameraControls.toggle_preview()` starts `Andor.start_capture_continuous()`.
2. `Andor._capture_loop()` runs in a background thread and appends raw and derived frames under `frame_lock`.
3. The driver sets `frame_ready_event` whenever a new frame is available.
4. `CameraFeedWindow._process_camera_feed()` watches that event, computes the next display payload, and stores it in window-owned shared state.
5. `CameraFeedWindow.render()` is the only place that uploads the pending texture to Dear PyGui and performs other visible UI work such as zero-window updates, ROI window layout, and overlay drawing.

The active display source is selected in `CameraFeedWindow`:

- `Normal` uses raw frames unless LP filter is enabled, in which case it uses `filtered`
- `Difference` uses precomputed `difference`
- `Contrast` uses precomputed `contrast`

### Feed Controls

`FeedControlsWindow` is a pure UI companion for `CameraFeedWindow`.

It exposes:

- autoscaling and manual `Min Z` / `Max Z`
- mirrored signed scaling for zero-referenced modes
- display mode switching between Normal, Difference, and Contrast
- positive/negative colors for signed displays
- LP Filter enable and cutoff frequency
- Set Zero and Reset Zoom actions

Current behavior to preserve:

- Contrast mode is clamped to a signed display range of `-200` to `200`
- LP cutoff commits on Enter or focus loss, not on every keystroke
- enabling/disabling LP filter rebuilds dependent caches and ROI traces

### Zero Reference Workflow

`CameraFeedWindow` owns the zero-reference UX.

- Pressing `Set Zero` snapshots the current active source frame into `Andor.zero`
- A separate `ImageWindow` displays the zero reference preview
- `zero_version` is used to detect whether a meaningful zero has ever been set
- Starting preview with no zero set marks `preview_zero_reference_pending`, then the first real preview frame becomes the zero reference

Important: the zero preview is a static view of `Andor.zero`, not a live alias of the feed texture.

### ROI Architecture

ROI behavior is split into three layers:

- `CameraFeedWindow` owns ROI creation, selection, movement, resize, and overlay hit-testing
- `RegionOfInterest` owns per-ROI crop/trace computation on a worker thread
- `ROIsWindow` owns the shared multi-row UI that displays each ROI image and its trace

Each ROI has its own worker thread.

The ROI data path is:

1. `CameraFeedWindow.get_analysis_snapshot()` returns the current active frame and, when requested, history for the currently selected display mode.
2. `RegionOfInterest` rebuilds trace history from those cached frames or appends one new mean value per new frame index.
3. `ROIsWindow.render_roi()` applies the pending crop texture and plot data to the Dear PyGui widgets.

Preserve these invariants:

- ROI analysis must go through `CameraFeedWindow.extract_roi_frame()` or `get_analysis_snapshot()` so display mode stays consistent.
- Preview startup should rebuild ROI traces from current history.
- Bounds edits should request a rebuild rather than manually patching plot series.

## PicoScope Data Flow

The PicoScope path is separate from the camera path.

- `src/Drivers/PicoScope.py` owns device I/O, channel buffers, timestamps, and AWG operations.
- `src/Windows/PicoScope.py` owns device configuration UI.
- `src/Windows/SubWindows/Oscilloscope.py` renders traces returned by the control window.

Operationally:

- the driver starts a worker thread for hardware capture
- a listener thread drains worker output into driver-owned buffers
- a re-entrant device API lock serializes Pico SDK calls
- UI code consumes driver snapshots and converts raw samples to volts before plotting

The camera acquisition workflow in `CameraControls.py` can start a fixed camera capture and a PicoScope collection together, then save both into one `.npz` file.

## Multithreading And Performance Model

This app stays responsive by separating hardware acquisition, frame analysis, and UI rendering.

### Threads That Matter

- Main Dear PyGui thread: `WidefieldController.setup()` render loop
- Andor capture thread: `Andor.capture_thread`
- Camera feed worker thread: `CameraFeedWindow._process_camera_feed()`
- One ROI worker thread per ROI: `RegionOfInterest.worker_thread`
- Camera acquisition orchestration thread: `CameraControls._acquisition_thread`
- Optional delayed AWG start thread during acquisition: `CameraControls._acquisition_awg_thread`
- PicoScope device refresh thread: `PicoScopeControl._device_refresh_thread`
- PicoScope worker/listener threads inside the driver
- PM1000 continuous read thread inside `Drivers/PM1000.py`

### Performance Techniques Already In Use

- Raw and derived camera frames are cached in deques so ROI rebuilds do not need to recompute transforms from scratch.
- Difference and contrast are computed once in the driver, not per ROI or per draw.
- LP filtering is also cached driver-side.
- Camera feed textures are only uploaded when a new frame or display-state change marks the image dirty.
- ROI workers keep plot/image payloads in pending buffers and the UI layer only applies the latest version.
- The zero-reference preview only refreshes when the source state changes and the window is visible.
- PicoScope plotting consumes snapshots rather than touching hardware directly.

### Concurrency Caveats

The thread model to preserve is now stricter than the older ad hoc pattern: background threads may collect hardware data, transform it, and publish the latest snapshots or pending payloads, but they should not directly mutate Dear PyGui items.

Safe guideline for future changes:

- keep hardware I/O, numeric processing, and snapshot construction on worker threads
- keep item creation, widget layout, and all visible `dpg.configure_item` / `dpg.set_value` / `dpg.bind_item_theme` UI mutations on the main render path
- treat each window's `render()` method as the frontend commit point for that window; callbacks and worker threads should update only backend state, flags, queues, or pending payload fields that `render()` consumes
- if a worker needs to signal freshness, use events, dirty flags, version counters, or snapshot replacement; do not use that signal as permission to call Dear PyGui from the worker

If you introduce a new high-rate feature, prefer this pattern:

- worker thread computes or snapshots data into shared/window-owned state
- the owning window `render()` reads the latest state and applies it to Dear PyGui items
- if intermediate worker outputs would pile up, keep only the newest pending payload rather than queueing unbounded UI work

## Subwindow Inventory

### `src/Windows/SubWindows/CameraFeed.py`

- live image texture
- zoom and pan
- ROI editing overlay
- mode-aware frame selection
- zero-reference coordination
- persistence for feed settings and ROI list

### `src/Windows/SubWindows/FeedControlsWindow.py`

- feed display settings UI only

### `src/Windows/SubWindows/ImageWindow.py`

- reusable image-display window used for the zero-reference preview
- preserves aspect ratio during resize

### `src/Windows/SubWindows/RegionOfInterest.py`

- per-ROI worker model for crop image and mean-intensity trace generation

### `src/Windows/SubWindows/ROIsWindow.py`

- shared ROI dashboard that renders each ROI image and graph in a table layout
- maintains synchronized x-axis limits across ROI traces

### `src/Windows/SubWindows/Oscilloscope.py`

- renders active PicoScope traces as stacked linked-x plots
- supports y zoom and x zoom/pan interactions

### `src/Windows/SubWindows/GraphWindow.py`

- older generic plotting helper
- currently appears to be legacy and is not central to the current camera workflow

### `src/Windows/SubWindows/VideoSettings.py`

- appears to be legacy/unused compared with the current `CameraControls` + `CameraFeedWindow` flow

## Acquisition Save Format

Pressing **Save** after a fixed acquisition writes a `np.savez_compressed` file. The data flow is:

1. `_on_acquire_button_pressed()` — snaps all current UI values via `_collect_settings_snapshot()` into `self._acquisition_settings_snapshot` right before `Andor.start_capture_fixed()` is called.
2. When the acquisition ends (or is stopped), `_run_acquisition()` calls `_build_completed_acquisition_payload(stopped_early)`, which combines `Andor.get_snapshot()`, an optional PicoScope snapshot, the acquisition parameters stored on `self`, and the settings snapshot into a single dict stored in `self._pending_acquisition_result`.
3. `render()` transfers `_pending_acquisition_result` to `self._completed_acquisition_payload` via `_apply_pending_acquisition_result()` and enables the Save button.
4. `_on_save_dialog_selected()` calls `_build_acquisition_save_arrays(camera, scope, payload)` and passes the result to `np.savez_compressed`.

### Arrays written to the `.npz`

**Camera frame buffers** (one array per frame, shaped `[N, H, W]`):

| Key | Content | dtype |
|-----|---------|-------|
| `camera_acquisitions` | Raw frames at storage bitness | raw storage dtype |
| `camera_filtered` | LP-filtered frames (or copy of raw if filter off) | raw storage dtype |
| `camera_difference` | `filtered − zero` | signed storage dtype |
| `camera_contrast` | `(filtered − zero) / zero × 100` | signed storage dtype |
| `camera_timestamps` | Per-frame hardware timestamps (seconds) | float64 |
| `camera_mean_buffer` | Per-frame mean intensity | float64 |
| `camera_zero` | Zero-reference frame used during acquisition | raw storage dtype |
| `camera_storage_dtype` | Storage bit-depth label (e.g. `"16"`) | string array |

**Oscilloscope per-frame mean buffers** (one array per enabled channel, length `N`):

| Key | Content | dtype |
|-----|---------|-------|
| `scope_channel_A`, `scope_channel_B`, … | Mean voltage sampled during each camera frame | float16 |
| `scope_timestamps` | Camera timestamps aligned to scope | float64 |
| `scope_paired_camera_timestamps` | Same as above | float64 |
| `scope_unpaired_camera_timestamps` | Camera timestamps with no matching scope sample | float64 |
| `scope_actual_sample_rate_hz` | Scope sample rate that was active | float64 |
| `scope_history_seconds` | Scope ring-buffer duration | float64 |

**Acquisition parameters** (scalars):

`requested_duration_seconds`, `requested_frame_rate_hz`, `requested_scope_sample_rate_hz`, `requested_storage_dtype`, `requested_scope_storage_dtype`, `requested_scope_sample_count`, `paired_scope_sample_count`, `requested_scope_buffer_seconds`, `zero_on_start`, `set_awg_on_start`, `awg_waveform`, `awg_start_after_seconds`, `stopped_early`

**Camera Controls settings snapshot** — captured at acquisition-start time, prefixed `settings_`:

`settings_exposure_time`, `settings_max_exposure`, `settings_trigger_mode`, `settings_cooler_enabled`, `settings_temperature_setpoint`, `settings_pixel_binning`, `settings_image_width`, `settings_image_height`, `settings_image_left`, `settings_image_top`, `settings_image_auto_center`, `settings_frame_storage_dtype`, `settings_preview_max_frames`, `settings_acquisition_duration_seconds`, `settings_acquisition_frame_rate_hz`, `settings_acquisition_scope_sample_rate_hz`, `settings_acquisition_zero_on_start`, `settings_calculate_frame_mean`, `settings_auto_scope_freq`, `settings_set_awg_on_start`, `settings_awg_waveform`, `settings_awg_dc_offset_volts`, `settings_awg_frequency_hz`, `settings_awg_amplitude_vpp_volts`, `settings_awg_periodic_offset_volts`, `settings_awg_start_after_seconds`, `settings_hardware_storage_drive`, `settings_spool_to_disk_enabled`, `settings_spool_to_disk_directory`, `settings_spool_to_disk_filename`, `settings_spool_write_difference`, `settings_spool_write_contrast`, `settings_spool_write_scope`, `settings_spool_write_power`

### Keeping the settings snapshot in sync

`_collect_settings_snapshot()` in `CameraControls.py` is the single source of truth for which settings are saved. **Whenever a new widget is added to the Camera Controls window, add a corresponding entry to `_collect_settings_snapshot()`.**

The serialization loop in `_build_acquisition_save_arrays()` handles all Python primitive types automatically (`bool` → `np.bool_`, `int` → `np.int64`, `float` → `np.float64`, anything else → string array), so only the dict entry in `_collect_settings_snapshot()` needs updating — no changes to the serialization loop are needed.

## Practical Rules For Future Agents

1. Start from the owning window or driver, not from a random callback. Many behaviors are split across a UI class, a driver cache, and one or more worker threads.
2. If a new camera feature changes what a frame means, update `Andor`, `CameraFeedWindow`, ROI snapshot routing, and acquisition export together.
3. If you add a new top-level window, placing it in `src/Windows/` is enough to auto-load it. If you do not want auto-instantiation, keep it elsewhere.
4. Preserve `SaveState()` and `LoadState()` behavior when changing windows. This app expects persistent layout and settings.
5. Be careful when changing capture startup and stop paths. Camera preview, fixed acquisition, PicoScope collection, and AWG timing are coupled in `CameraControls.py`.
6. Prefer buffer rebuilds and event-based invalidation over ad hoc recomputation inside `render()`.
7. Keep the render-thread boundary explicit: worker threads and control callbacks can update shared model state, but frontend re-renders and visible widget mutations should happen from the owning window `render()` only.
8. When refactoring threaded code, move the smallest useful unit toward a `worker publishes state -> render consumes state` shape instead of adding more callback-driven UI synchronization.
9. Validate both hardware-present and hardware-absent startup paths. When no Andor camera is present, the app falls back to `Mocks.MockCamera`.
10. When adding a new setting widget to Camera Controls, add it to three places: `SaveState()` / `LoadState()` for UI persistence, and `_collect_settings_snapshot()` for acquisition save. See the Acquisition Save Format section for the full list of already-saved settings.

## Suggested Entry Points For Common Tasks

- Add or change camera display logic: `src/Drivers/Andor.py` and `src/Windows/SubWindows/CameraFeed.py`
- Add feed-side controls: `src/Windows/SubWindows/FeedControlsWindow.py`
- Change ROI behavior: `src/Windows/SubWindows/CameraFeed.py`, `src/Windows/SubWindows/RegionOfInterest.py`, `src/Windows/SubWindows/ROIsWindow.py`
- Change acquisition/save behavior: `src/Windows/CameraControls.py`
- Change scope capture or AWG behavior: `src/Drivers/PicoScope.py` and `src/Windows/PicoScope.py`
- Change oscilloscope display behavior: `src/Windows/SubWindows/Oscilloscope.py`
- Change startup/autoloading/persistence: `src/WidefieldController.py`, `src/Utils/utils.py`, `src/Utils/state_persistence.py`

## Runtime Validation

For local GUI validation, the existing repo note uses:

`c:/CodingProjects/Widefield-Controller/.venv/Scripts/python.exe .\\src\\WidefieldController.py`

That path is useful because the repo often runs without real camera hardware by falling back to the mock camera.
