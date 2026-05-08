# Implementation Plan

Generated from TODO.md. Each group clusters tasks that share the same files/context so they
can be done in a single agent context window. Agents work sequentially — one group at a time.

---

## Status Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Done

---

## Group A — Camera Controls: Button UI & Layout Cleanup

**Status: [x] Done**
**Primary file:** `src/Windows/CameraControls.py`

Tasks (from TODO.md, in priority order):

- [X] Task 9 — Make the "Acquire" button be the regular button color whilst inactive.
- [X] Task 10 — Make the "Stop Preview" and "Stop Acquiring" buttons red.
- [X] Task 11 — Make the "Acquire" and "Preview" buttons disabled whilst saving.
- [X] Task 12 — Make the "Save" button be half the size that it currently is.
- [X] Task 13 — Remove the "Open" button and its functionality.
- [X] Task 14 — Remove the "Calculate Frame Mean" checkbox.
- [X] Task 15 — Make the buttons in the "Camera Controls" window display over the top of the settings. The buttons area should have a grey background so that they don't overlap weirdly.

---

## Group B — Camera Controls: Functional Settings + Hardware Requirements

**Status: [x] Done**
**Primary file:** `src/Windows/CameraControls.py`

Tasks (from TODO.md, in priority order):

- [X] Task 7 — Restore the "Zero on Start" functionality in the Acquisition Settings section of "Camera Controls".
- [X] Task 8 — Add a "Zero on Start" setting to the "Preview Settings" which works the same as the one in the "Acquisition Settings" but only when the preview is started.
- [X] Task 16 — Double check the hardware Reqs calculation. Currently a 60-second, 500x500 pixel, 60FPS video is coming out at 1.68GB — verify the maths (16-bits per pixel × 500×500 px × 60FPS × 60s = 1.8 GB) and fix the calculation if wrong.

---

## Group C — Camera Driver: Timeout Tolerance

**Status: [X] Done**
**Primary files:** `src/Drivers/Andor.py`, `src/Mocks/MockAndor.py`

Tasks (from TODO.md, in priority order):

- [X] Task 1 — Ignore up to 5 sequential AT_ERR_TIMEDOUT errors during preview/acquisition. On the 6th consecutive timeout print "Timed out more than 5 times" and safely stop the preview.

---

## Group D — Camera Feed: Signal Processing + Crop Slider

**Status: [x] Done**
**Primary files:** `src/Windows/SubWindows/CameraFeed.py`, `src/Windows/SubWindows/FeedControlsWindow.py`, `src/Windows/SubWindows/AcquisitionPreviewWindow.py`

Tasks (from TODO.md, in priority order):

- [X] Task 2 — Make the preview window correctly implement all signal processing using the same signal processing pipeline as the live camera feed.
- [X] Task 6 — Add a "Crop" slider into the feed controls. Crops the processed image as the final processing step before display by setting pixels outside the crop region to black (not resizing). Exempt those pixels from "zero-referenced display" calculations.

---

## Group E — ROI Graphs: Behavior Fixes

**Status: [x] Done**
**Primary files:** `src/Windows/SubWindows/ROIsWindow.py`, `src/Windows/SubWindows/RegionOfInterest.py`, `src/Windows/SubWindows/CameraFeed.py`

Tasks (from TODO.md, in priority order):

- [X] Task 3 — Make ROI graphs in the preview window fully recalculate whenever settings change or the ROI window is changed. Calculate once, then remain static.
- [X] Task 4 — When moving or rescaling an ROI in the Camera Feed window, instead of restarting the graph, pad all earlier positions with null values (so they exist but don't render) and append newest values to the end, maintaining the same x-axis as other ROIs.
- [X] Task 5 — Make graphs behave like regular DearPyGui graphs: remove manual autoscale calculations, restore native right-click axis options (min, max, autoscale) and mouse zoom/pan behaviour.

---

## Group F — File Browser: Delete + NPZ Drag-and-Drop + Loading Bar

**Status: [x] Done**
**Primary files:** `src/Windows/FileBrowser.py`, `src/Windows/SubWindows/AcquisitionPreviewWindow.py`, `src/WidefieldController.py`

Tasks (from TODO.md, in priority order):

- [x] Task 17 — Add the ability to delete videos in the File Browser with a "Are you sure you want to delete…" confirmation modal.
- [x] Task 18 — When dragging and dropping an NPZ file anywhere into the program, open it in the preview window.
- [x] Task 19 — When loading a file in the "Preview" window, add a loading bar where it currently says "Loading XXX.npz" showing how much of the file has been loaded.

---

## Group G — Window Management: Context Menu

**Status: [x] Done**
**Primary files:** `src/WidefieldController.py`, `src/Utils/state_persistence.py`

Tasks (from TODO.md, in priority order):

- [x] Task 20 — Allow right-clicking on the program background or any window title-bar to get "Reset All Windows", "Save Windows State", and "Collate All Windows" (stacks windows side-by-side).

---

## Group H — Save Speed Optimisation

**Status: [~] In progress**
**Primary files:** `src/Windows/CameraControls.py` (save logic), `src/Utils/StorageDTypes.py`

Tasks (from TODO.md, in priority order):

- [ ] Task 21 — Investigate and implement faster NPZ saving. Target: ~20 s for a 2 GB file at 300 MB/s disk write speed. Options to explore: raw numpy `tofile` + metadata sidecar, memory-mapped writes, chunked async writes, or switching format entirely (HDF5/zarr).

---

## Completion Notes

- **Group A** — commit `9f63a12` (2026-05-08). Button UI & layout cleanup: removed green Acquire theme, added red Stop buttons, save-in-progress disables Acquire/Preview, Save button halved, Open button removed, Calculate Frame Mean checkbox removed, buttons pinned in grey footer child window.
- **Group B** — commit `ddb3fac` (2026-05-08). Task 7: added `_on_acquisition_zero_on_start_changed` callback to sync instance variable (zero action at acquisition start was already implemented). Task 8: added `preview_zero_on_start` variable, "Zero on Start" checkbox in Preview Settings, `_on_preview_zero_on_start_changed` callback, and wired into `toggle_preview()` via `preview_zero_reference_pending`. Task 16: fixed `_format_gigabytes` to return "GiB"/"MiB" (was incorrectly labelling binary-prefixed values as "GB"/"MB"); updated initial widget labels and all string comparisons in `_refresh_hardware_requirements` accordingly.
- **Group C** — commit `888dc03` (2026-05-08). Task 1: added `_consecutive_timeout_count` counter in `_capture_loop`; inner try/except on `cam.wait_buffer` catches `CameraException` (err_code == AT_ERR_TIMEDOUT / 13) and `TimeoutError` (MockCamera); silently skips timeouts 1-5 via `continue`; on the 6th consecutive timeout prints "Timed out more than 5 times" and breaks cleanly; successful frames reset the counter to 0. Also added `from pyAndorSDK3.andor_sdk3_exceptions import CameraException, ErrorCodes` import.
- **Group D** — commit `0670445` (2026-05-08). Task 2: preview window pipeline verified to already include LP filter, drift correction, BG removal, and mode (Difference/Contrast) processing; added crop mask as the final step to `_get_display_frame()` and updated `_get_autoscale_range()` and `_compute_display_bounds()` to exclude blacked-out border pixels from percentile autoscale. Task 6: added `crop_percent = 100.0` and `_compute_crop_mask()` to `CameraFeedWindow`; added `_on_crop_changed` callback; applied crop mask as the last step in `process_frame()` (after drift mask); updated `_frame_to_rgba()` to exclude border pixels from autoscale; added `Crop (%)` slider to `FeedControlsWindow` Signal Processing section; mirrored all changes in `AcquisitionPreviewWindow`; crop_percent persisted in SaveState/LoadState for both windows.
- **Group E** — commit `1828ba4` (2026-05-08). Task 3: added `_worker_loop_full_history` in RegionOfInterest for preview mode — waits on rebuild_event, fetches all frame crops via `get_roi_processing_update(include_history=True)`, applies `process_analysis_frame` per crop, builds the full trace in one pass, then sleeps until next settings change or ROI edit. Task 4: added `nan_pad` parameter to `request_trace_rebuild`; incremental worker fills existing y_axis with `float('nan')` instead of clearing on `nan_pad=True`, preserving x-axis alignment; `CameraFeed._on_mouse_release` now calls `request_trace_rebuild(nan_pad=True)` after move/resize. Task 5: removed `no_menus` and `no_box_select` from plot creation; replaced manual y-axis limit management with `dpg.set_axis_limits_auto()`; removed Min/Max/Auto/Grace/Mirrored controls from _build_controls, keeping only Metric combo; marker series uses y=[-1e38, 1e38] to always span the visible range; `invalidate_autoscale_cache` reduced to a no-op.
- **Group F** — commit `33d28a8` (2026-05-08). Task 17: added `Del` button per file row in FileBrowser table; clicking shows a no-title-bar modal (`#FileBrowserDeleteModal`) with truncated filename, Yes/Delete and No/Cancel buttons; on confirm calls `os.remove` and bumps `_watch_generation` to trigger a listing refresh. Task 18: added `_register_viewport_drop_callback` in WidefieldController; `dpg.set_viewport_drop_callback` receives dropped paths, finds the `FileBrowser` instance in `class_objects`, and calls `_open_preview_window` for the first `.npz` path. Task 19: replaced the bare loading text in AcquisitionPreviewWindow with a `dpg.add_progress_bar`; `_set_load_progress` writes thread-safe progress at five stages (5%/15%/50%/80%/95%); `_apply_pending_loaded_payload` reads `_pending_load_progress` each render tick and updates the bar, then hides it on load complete or error.
- **Group G** — commit `3941efe` (2026-05-08). Task 20: added `collate_all_windows()` to tile visible windows in a 2-column grid; added `create_context_menu()` which builds a `popup=True` dpg.window with "Reset All Windows", "Save Windows State", and "Collate All Windows" menu items wired to the existing reset/save helpers and the new collate function; added `on_right_click()` handler that positions the popup at the cursor; registered `dpg.add_mouse_click_handler(button=mvMouseButton_Right)` inside the existing handler registry in `setup()`.
