# Widefield Controller — Implementation Plan

Each group targets a coherent slice of the codebase so the agent's context window stays focused.
After each group the agent will confirm the work is complete and wait for user sign-off before proceeding.

Status legend: `[ ]` pending · `[x]` complete

---

## Group 1 — Enter-to-Commit Inputs

**Scope:** All number and text inputs across the entire program should only apply their value
when the user presses Enter (or loses focus). No value should update on every keystroke.

**Primary files:**

- `src/Windows/CameraControls.py`
- `src/Windows/LaserControls.py`
- `src/Windows/PicoScope.py`
- `src/Windows/SubWindows/FeedControlsWindow.py`
- `src/Windows/SubWindows/ROIsWindow.py`
- `src/Windows/AppLayout.py`

**TODO items covered:**

- [X] Make it so that all number and text inputs in the entire program only apply when you hit Enter (items 2 & 12)
- [X] Make the exposure input in the top-right sidebar apply the exposure (matches Camera tab exposure) (item 1)

---

## Group 2 — Camera Settings, Cooler & Capture Bugs

**Scope:** Camera connection-time behaviour, exposure/frame-rate coupling, temperature UI,
and two capture-thread bugs.

**Primary files:**

- `src/Drivers/Andor.py`
- `src/Windows/CameraControls.py`
- `src/Windows/AppLayout.py` (sidebar exposure + temperature indicator)

**TODO items covered:**

- [X] Change "Max Exposure" setting to "Max Frame Rate" — when exposure changes, set max frame rate for that exposure (item 3)
- [X] Gracefully handle `AT_ERR_OUTOFRANGE` by stepping the frame rate down until it is in range (item 4)
- [X] Add a Temperature indicator symbol to the right of the exposure slider in the sidebar; red when camera is above −9.5 °C; clicking it enables the cooler at −10 °C (item 5)
- [X] Set cooler ON and −10 °C automatically when the camera connects (item 6)
- [X] Fix intermittent `AT_ERR_INVALIDSIZE` crash in the capture thread (item 25)

---

## Group 3 — Laser Controls & Auto-Connect

**Scope:** Toolbar laser widget enhancements and laser startup behaviour.

**Primary files:**

- `src/Windows/LaserControls.py`
- `src/Windows/AppLayout.py`

**TODO items covered:**

- [ ] Laser Enable/Disable button in toolbar always reflects current laser state — green when enabled (item 8)
- [ ] Laser power in toolbar goes up to 500 mW (item 9)
- [ ] Add a small progress bar beneath the laser power slider showing current set-point power; total height (bar + gap + slider) must equal the original slider height (item 10)
- [ ] Add a "Low Power" button in toolbar and laser menu that sets power to 1 mW (item 11)
- [ ] Laser auto-connects on startup by trying all COM ports in sequence (item 22)

---

## Group 4 — Stage Controls & Toolbar Stage Section

**Scope:** Stage-related UI: disabling controls while typing, position map scrolling fix,
and a new Stage section in the toolbar.

**Primary files:**

- `src/Windows/StageControls.py`
- `src/Windows/AppLayout.py`

**TODO items covered:**

- [ ] Position map is not scrollable — always fits its container exactly (item 7)
- [ ] Add a "Stage" section to the toolbar with a "Lower" button that moves Z to 2 mm at 3 mm/s (item 20)
- [ ] Add the current X/Y/Z values to the "Stage" section in the toolbar

---

## Group 5 — Feed Controls & Camera Feed Display

**Scope:** Min Z / Max Z behaviour improvements and objective-name overlay positioning.

**Primary files:**

- `src/Windows/SubWindows/FeedControlsWindow.py`
- `src/Windows/SubWindows/CameraFeed.py`

**TODO items covered:**

- [ ] When auto-scale is active it also updates the stored Min Z and Max Z values (item 13)
- [ ] Add a checkbox to switch Min Z / Max Z between percent (0–100) and absolute (0–65535) (item 14)
- [ ] Move the objective name label up so it sits closer to the scale bar line (item 21)

---

## Group 6 — Export, File Open & ROI Export

**Scope:** All export and file-loading enhancements: PNG defaults, drag-and-drop open,
Min/Max Z persistence in .npz, Title Text overlay, and ROI graph export.

**Primary files:**

- `src/Windows/SubWindows/CameraFeed.py` (PNG export, overlay rendering, drag-drop)
- `src/Windows/CameraControls.py` (npz save/load, Min/Max Z persistence)
- `src/Windows/SubWindows/FeedControlsWindow.py` (Min/Max Z load on open)
- `src/Windows/SubWindows/ROIsWindow.py` (ROI graph rendering for export)
- `src/Windows/FileBrowser.py` (drag-and-drop npz open)
- `src/WidefieldController.py` (drag-and-drop hook)

**TODO items covered:**

- [ ] PNG export dialog defaults the filename and directory to the current open file (item 16)
- [ ] Add "Title Text" to Rendered Info options — checkbox + text input, defaults to open file name; renders text onto exported image/video (item 17)
- [ ] Drag a .npz file anywhere into the program to open it (item 18)
- [ ] Save current Min Z and Max Z inside the .npz; restore them when the file is opened (item 19)
- [ ] Add "Include ROIs in export" option — renders each ROI as a graph beside the image frame in exported video/image, with a red line marking the current frame (item 24)

---

## Group 7 — Drift Correction Bug Fix

**Scope:** Single targeted bug: drift correction must be applied before computing
difference and contrast frames.

**Primary files:**

- `src/Drivers/Andor.py`
- `src/Windows/SubWindows/CameraFeed.py`

**TODO items covered:**

- [ ] Drift correction is applied before calculating difference / contrast frames (item 23)

---

## Group 8 — Prevent Toolbar Shortcuts While Typing

**Scope:** When the user is typing in any input widget, toolbar keyboard shortcuts (spacebar
starts live feed, etc.) must not fire. Implemented via a `currently_editing` flag in shared
state, set/cleared by focus/deactivate handlers baked into the custom widgets. A custom
save-dialog widget keeps the flag raised for its entire lifetime.

**Primary files:**

- `src/Utils/custom_widgets.py`
- `src/Utils/shared_state.py`
- `src/Windows/AppLayout.py` (shortcut guard)

**TODO items covered:**

- [X] Typing in any input does not trigger toolbar keyboard shortcuts (e.g. spacebar → live feed)
- [X] `shared_state.currently_editing` is `True` while any custom input widget has focus
- [X] `shared_state.currently_editing` is `True` for the full duration any save dialog is open

---

## Execution Notes

1. Launch one agent per group. The agent reads this file, implements the items, then posts a
   summary and waits for the user to confirm everything works before the next group begins.
2. After user confirmation, mark each completed item `[x]` in this file and move to the next group.
3. If a fix in one group breaks a later group's scope, note it here before starting that group.
