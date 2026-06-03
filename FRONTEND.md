# Frontend Replication Guide

This document describes the actual frontend implemented in this repository so another AI coding agent can reproduce it faithfully. It is based on the Dear PyGui code in `src/WidefieldController.py`, `src/Windows/`, `src/Windows/SubWindows/`, and the shared UI utilities in `src/Utils/`.

The application is not a browser frontend. It is a desktop UI built with Dear PyGui and organized as a single full-viewport shell containing embedded control panels, live image surfaces, plots, and file-preview tooling.

## 1. Frontend Technology And Runtime Model

### Stack

- UI toolkit: Dear PyGui
- Styling: Dear PyGui themes created in code
- Icons: Segoe MDL2 (`src/Assets/Fonts/SegMDL2.ttf`)
- Plotting: Dear PyGui plots/subplots
- Images: Dear PyGui raw textures and drawlists
- State persistence: custom JSON files in `AppState/` plus Dear PyGui init-file persistence

### Runtime shell

The entrypoint is `src/WidefieldController.py`.

Startup does the following:

1. Lowers the Python thread switch interval to support high-rate capture and processing.
2. Installs console capture.
3. Loads viewport state from `AppState/WidefieldController.json`.
4. Creates the Dear PyGui context and viewport.
5. Configures Dear PyGui to use the saved init file (`dpg_layout.ini`).
6. Creates a global disabled-item theme and binds it application-wide.
7. Dynamically imports every Python file directly inside `src/Windows/` and instantiates the discovered classes.
8. Calls `LoadState()` on each instantiated window object if present.
9. Shows the viewport and enters the main render loop.

The app is not composed of routed pages. It is a persistent desktop workspace where all major panels exist simultaneously, and visibility changes are driven by tab selection, acquisition state, and preview mode.

### Render-loop model

The frontend is render-loop driven.

- Each top-level window class may implement `render()`.
- The main loop calls `render()` on every class object every Dear PyGui frame.
- Worker threads compute data in the background.
- Visible Dear PyGui mutations are applied on the main thread during `render()`.

This is a core architectural rule. Reproducing the frontend means reproducing this split:

- background threads collect and process data
- foreground render methods update widgets, textures, and plots

## 2. Global Visual Language

### Overall look

The app uses a dark technical workstation aesthetic.

- Viewport clear color: black (`[0, 0, 0, 255]`)
- Main toolbar background: dark gray (`[30, 30, 30, 255]`)
- Drag handles: medium dark gray (`[55, 58, 60, 255]`)
- Muted label text: around `[140, 140, 140]`
- Active/selected accent color: green (`[0, 124, 80]`)
- Warning/hot indicator: red family (`[160, 30, 30]` etc.)

The layout is dense and instrument-like rather than decorative. Most content appears inside tree nodes, child windows, tables, and plots. Padding is selectively removed in image and plot surfaces to maximize usable space.

### Theme inventory

Global and reusable themes come from `src/Utils/themes.py`.

- `selected_theme`: green button state
- `red_button`: red destructive/stop state
- `yellow_button`: yellow attention state
- `read_only_theme`: muted text/button appearance
- `disabled_theme`: dimmed disabled-state visuals
- `no_padding_theme`: zero window padding
- `no_spacing_theme`: zero item spacing and padding
- `transparent_plot_theme`: transparent plot and child backgrounds

`AppLayout.py` also creates local themes for:

- toolbar background
- toolbar icon buttons
- toolbar control padding
- active toolbar buttons
- red temperature indicator button
- drag handles
- the main window itself with zero padding

### Fonts and icons

Font setup is centralized in `src/Utils/fonts.py`.

- Icon font: Segoe MDL2 at 12 pt and 18 pt
- Plot label font in oscilloscope: Arial from `src/Assets/Fonts/arial.ttf`

Common icon glyphs used by the frontend include:

- play / preview
- pause
- record / acquire
- snapshot
- save
- autoscale
- power / laser
- refresh
- connect / disconnect
- temperature / snowflake
- repeat

The toolbar and some device panels rely on icon glyphs rather than text labels for compact controls.

## 3. Primary Shell Layout

The UI shell is defined in `src/Windows/AppLayout.py`.

### Main structure

`AppLayout` creates the primary window tagged `MainWindow` and sets it as the Dear PyGui primary window.

The shell is composed of:

1. A fixed-height top toolbar
2. A three-column content area below it

The content area is built with a Dear PyGui table:

- left sidebar: fixed width `285 px`
- center column: stretch/fill
- right sidebar: fixed width `285 px`

### Top toolbar

Height: `64 px`

The toolbar is visually grouped with label text above compact controls. Each group is separated by a 1-pixel vertical line drawlist.

Toolbar groups:

#### Camera group

- Preview button
- Acquire button
- Snapshot button
- Save Acquisition button
- Exposure input
- Sensor temperature indicator button

Behavior:

- preview button toggles play/stop icon and green active theme while preview runs
- acquire button turns green while an acquisition is active
- save button is disabled until acquisition data exists
- temperature button turns green when cold enough and red when too warm or unknown
- temperature tooltip displays current sensor temperature and cooler hint

#### Laser group

- Laser power toggle button
- Target laser power slider

Behavior:

- laser button turns green while laser emission is enabled
- power slider mirrors the laser panel's target power source

#### Scaling group

- Autoscale toggle button
- Scale min slider
- Scale max slider

Behavior:

- autoscale button turns green when feed autoscaling is enabled
- slider values proxy into the feed controls window

#### Scope group

- AWG button labeled `AWG Off` or `AWG On`

Behavior:

- turns green when the function generator output is enabled

#### Performance overlay

Three text rows are dynamically positioned at the right edge of the toolbar:

- UI FPS
- Capture FPS
- Processing FPS

These are updated every frame from `update_performance_overlay()`.

### Left sidebar

The left sidebar is a tab bar with scrollable tab content areas.

Tab list:

- Camera
- Feed
- Laser
- PicoScope
- Stage
- Mock Cam
- Preview

Important visibility rule:

- `Preview` is hidden during normal live operation
- `Preview` becomes visible when a file-preview tab is active in the center column
- while preview mode is active, the standard control tabs are hidden and the Preview tab is selected automatically

This is how the app swaps from live acquisition controls to acquisition-preview controls without changing the outer shell.

### Center column

The center column is vertically split into two sections with a draggable divider.

#### Center top

Default height: `520 px`

Contains `CenterTabBar` with a permanent `Live Feed` tab whose content area is `CenterLiveFeedContainer`.

This area is where the live camera feed is embedded.

Other tabs can be inserted here by preview windows or file-driven workflows.

#### Center drag handle

Height: `6 px`

Behavior:

- gray strip
- dragging changes the split between live feed and bottom content

#### Center bottom

Contains a second tab bar with:

- Console
- Files

These are embedded containers for the console capture and file browser.

### Right sidebar

The right sidebar is vertically split into three stacked areas with two drag handles.

Default heights:

- top ROI area: `300 px`
- middle scope area: `200 px`
- bottom position-map area: remaining height

Sections:

- `RightROIs`
- `RightScope`
- `RightPositionMap`

Behavior:

- the top handle resizes ROI area versus scope/position-map
- the second handle resizes scope height
- the position-map panel fills the remaining space automatically

## 4. Window Loading And Embedding Rules

All Python modules directly inside `src/Windows/` are auto-loaded and instantiated at startup. This is not optional behavior.

Implications:

- any new file placed directly in `src/Windows/` will be treated as a top-level window class candidate
- helper modules must not live directly in `src/Windows/` unless they are intended to render a top-level surface

Most windows do not create floating windows when the shell exists. Instead, they look up container tags in `shared_state.layout_containers` and embed into those containers.

Examples:

- Camera controls embed into `LeftContent_Camera`
- Feed controls embed into `LeftContent_Feed`
- Laser controls embed into `LeftContent_Laser`
- File browser embeds into `CenterBottomContent_Files`
- ROIs embed into `RightROIs`
- Oscilloscope embeds into `RightScope`

Fallback behavior exists: if the expected parent container is not present, most components create standalone windows instead.

## 5. Top-Level Windows

## 5.1 AppLayout

File: `src/Windows/AppLayout.py`

Purpose:

- defines the entire app shell
- wires toolbar interactions to other window objects
- manages panel resizing and tab visibility

Key behaviors:

- `Space` toggles preview
- `Shift+Space` starts or stops acquisition
- toolbar widgets proxy into `CameraSystem`, `LaserControls`, `CameraFeedWindow`, and `PicoScopeControl`
- toolbar state stays synchronized with backend state during render

## 5.2 CameraSystem

File: `src/Windows/CameraControls.py`

Visible location:

- left sidebar, Camera tab

Companion surfaces it owns or instantiates:

- live `CameraFeedWindow` in center pane
- `CalibrationModal`
- `OscilloscopeWindow` for frame-scope means in right scope pane
- `ZAxisControlsWindow`
- `AcquisitionPreviewWindow` when previewing acquired data

Main visible sections inside the Camera tab:

### Camera Settings

- FPS input
- Exposure Time input
- read-only Max Frame Rate display
- `Auto Max FPS` checkbox
- Trigger Mode combo
- Cooler checkbox
- Temp Set combo
- read-only Temperature display

### Frame Settings

- Pixel Binning combo
- Width input
- Height input
- Left input
- Top input
- Auto Center checkbox

### Preview Settings

- Max Frames input
- Zero on Start checkbox

### Calibration

- objective selection combo
- calibration button
- calibration-derived state

### Acquisition Settings

- acquisition duration
- acquisition frame rate
- scope sample rate
- storage dtype
- zero-on-start checkbox
- frame-mean calculation checkbox
- auto scope frequency checkbox
- AWG-on-start checkbox
- AWG waveform and related waveform parameters
- delayed AWG start input

### Hardware Reqs

- storage drive combo
- derived hardware readouts for memory, disk, and throughput requirements

This section acts like a live calculator tied to acquisition parameters and enabled capture payloads.

### Saving

- save directory input
- browse button
- base filename input
- file index input
- prompt-every-time checkbox
- auto-save checkbox
- save/spool related options

### Footer action buttons

At the bottom of the camera control area there are large primary action buttons:

- Acquire
- Preview Start/Stop
- Snapshot
- Save

Behavioral notes:

- these actions overlap with the toolbar, but the toolbar is the compact global control surface and the Camera tab is the detailed control surface
- camera state drives toolbar appearance and button enabled-state
- the camera tab is the source of truth for most capture settings

## 5.3 FeedControlsWindow

File: `src/Windows/SubWindows/FeedControlsWindow.py`

Visible location:

- left sidebar, Feed tab

Purpose:

- controls how the live image is displayed and processed

Sections:

### Display Scaling

- Autoscale checkbox
- Min Z (%)
- Max Z (%)
- Grace (%)
- Mirrored checkbox
- Color Scale Bar checkbox

### Scale Bar

- Enabled checkbox
- Auto Width checkbox
- Width input in micrometers
- Size input
- Position combo: bottom-left, bottom-right, top-left, top-right
- X offset input
- Y offset input

### Zero-Referenced Display

- Display Mode combo: Normal, Difference, Contrast
- Color Scale combo

### Signal Processing

- LP Filter checkbox
- LP cutoff frequency input
- Drift Correction checkbox
- Background Removal checkbox
- BG Sigma input
- BG Mode combo: Spatial or Temporal
- EMA Alpha input
- Drift Every N input
- C++ Backend (temporal) checkbox
- Acquisition Engine (GIL-free) checkbox

The feed controls are heavily coupled to the behavior of the central live image.

## 5.4 LaserControls

File: `src/Windows/LaserControls.py`

Visible location:

- left sidebar, Laser tab

Sections:

### Laser Connection

- COM port combo
- refresh button
- connect/disconnect icon button

### Power Meter Connection

- PM1000 device combo
- refresh button
- connect button

### Power Control

- laser emission indicator color button
- Enable Emission / Disable Emission button
- Target Power slider
- Target Entry numeric input
- Actual laser power progress bar
- PM1000 measured power progress bar

### Laser Output plot

- rolling history plot
- two series: actual laser power and measured PM1000 power

Behavior:

- emission indicator turns green while enabled
- COM-port controls disable when connected
- history updates approximately every 0.2 seconds

## 5.5 PicoScopeControl

File: `src/Windows/PicoScope.py`

Visible location:

- left sidebar, PicoScope tab

Sections:

### Connection Settings

- device combo
- refresh devices button
- Open button
- Close button
- Start button
- Stop button
- multiline status text

### Sample Settings

- Sample Rate input
- Seconds input

### Channels

- dynamically generated per-channel panels for up to 8 channels
- enable/disable state per channel
- color-coded channel identity
- channel headers named `Channel 1` through `Channel 8`

### Function Generator

- embedded `FunctionGeneratorWindow`

### Oscilloscope Buffer

- embedded oscilloscope display showing live traces

Behavior:

- device discovery can cover multiple driver families
- UI swaps driver family when a different compatible device is selected
- status text reflects idle, open, collecting, and error states

## 5.6 StageControls

File: `src/Windows/StageControls.py`

Visible location:

- left sidebar, Stage tab
- also renders the position map into the right sidebar bottom panel

The file begins with an accurate layout summary. The stage frontend is dense and instrument-like.

Sections in the left Stage tab:

### Connection

- one connection row per axis: X, Y, Z
- serial-number combo for each axis
- refresh button for each axis
- connect/disconnect button for each axis

### Keypad

- 2x3 hold-to-move button grid
- buttons: `-Z`, `+Y`, `+Z`, `-X`, `-Y`, `+X`
- Home All button
- Stop All button

### Speeds

- XY fast speed
- XY slow speed
- Z fast speed
- Z slow speed

### Auto Focus

- autofocus start button
- autofocus stop button
- autofocus settings including search range, coarse step, fine step, settle time, frames per position, ROI fraction
- checkboxes for `focus_to_top_surface` and `always_calculate_focus_level`
- autofocus progress / plots / best focus feedback

### X Settings

Per-axis parameter form with apply/reset actions.

### Y Settings

Per-axis parameter form with apply/reset actions.

### Z Settings

Per-axis parameter form with apply/reset actions.

### Developer section

- additional lower-level or diagnostic stage controls

Position-map surface in the right sidebar:

- square XY scatter plot with current position marker
- vertical Z bar chart
- autofocus plot overlays and markers

Behavior:

- hold-to-move buttons use press/release handlers, not click-only actions
- global keyboard support: `W`, `A`, `S`, `D`, `Q`, `E`
- `Shift` modifies motion speed behavior
- stage state is refreshed by background polling

## 5.7 ConsoleWindow

File: `src/Windows/ConsoleWindow.py`

Visible location:

- center bottom, Console tab

Appearance:

- scrollable child window containing one read-only multiline input text

Behavior:

- automatically grows the text widget height to match content
- scrolls to bottom on updates
- driven from the console capture snapshot version counter

## 5.8 FileBrowser

File: `src/Windows/FileBrowser.py`

Visible location:

- center bottom, Files tab

Top row:

- directory input text with Enter-to-apply
- Browse button

Main content:

- table of `.npz` files in the selected directory

Table columns:

- Title
- Type
- Frames
- Date
- Size
- preview action column
- delete action column

Behavior:

- a background watcher thread rescans the directory every second
- rows are rebuilt when the directory snapshot changes
- hovering the title shows tooltip details including filename and full path
- files can be previewed via per-row action button
- files can be deleted via modal confirmation dialog
- dragging a `.npz` file into the viewport routes into the file preview workflow

## 5.9 MockCameraControls

File: `src/Windows/MockCameraControls.py`

Visible location:

- left sidebar, Mock Cam tab
- only shown when `shared_state.dev_mode` is true

Purpose:

- controls the development-mode simulated camera scene

Sections:

- Focus
- Particles
- Intensity
- Illumination
- Translation
- Drift
- Global Pulse
- Fiducials

This window is only relevant when the Andor camera is running in mock mode, but it is still part of the frontend contract in development workflows.

## 6. Core Subwindows

## 6.1 CameraFeedWindow

File: `src/Windows/SubWindows/CameraFeed.py`

Visible location:

- center top `Live Feed` surface

This is the most visually important surface in the app.

### Rendering approach

- creates a drawlist with separate image, overlay, and colorbar layers
- uploads a raw float32 RGBA texture
- keeps a persistent raw texture buffer for in-place updates
- recalculates draw extents to preserve aspect ratio inside the center container

### Mouse and keyboard interactions

- left mouse: ROI interaction
- middle mouse: panning
- right mouse: context menu
- mouse wheel: zoom
- Delete key: delete selected ROI

### Context menu

Right-clicking the feed opens a popup with:

- `Set Frame to ROI`
- `Reset Zoom`

### Display modes

- Normal
- Difference
- Contrast

### Visual overlays and extras

- ROI rectangles and handles
- optional color bar
- optional scale bar
- optional crosshair
- zero-reference preview support

### Behavior

- maintains zoom, pan, and selected ROI state
- redraws overlays separately from image texture updates
- supports live autoscaling or manual scaling
- rate-limits on-screen texture pulls to display FPS cap
- uses a worker thread to process camera frames and a main-thread render path to commit the result

### Companion surfaces

- `FeedControlsWindow`
- zero-reference `ImageWindow`
- `ROIsWindow`

## 6.2 ROIsWindow

File: `src/Windows/SubWindows/ROIsWindow.py`

Visible location:

- right sidebar top panel for live mode
- embedded inside preview settings panel for acquisition preview mode

Appearance:

- metric combo at the top
- scrollable list of ROI rows beneath
- transparent overlay child window used for positioning floating close/fit controls

Each ROI row is approximately `120 px` tall and includes:

- ROI crop image
- ROI trace plot
- close button
- fit button
- ROI label text

Metric options:

- Mean
- Max
- Max & Min

Behavior:

- synchronized x-axis handling across ROI plots
- custom overlay scroll-wheel handling
- rows rebuild dynamically as ROIs are added and removed
- designed for embedded usage as a living analysis dashboard

## 6.3 RegionOfInterest

File: `src/Windows/SubWindows/RegionOfInterest.py`

Purpose:

- worker model for each ROI
- computes crop textures and trace data

Important frontend implication:

- every ROI has its own background analysis thread
- the visible ROI dashboard only commits the latest pending crop/plot data

## 6.4 OscilloscopeWindow

File: `src/Windows/SubWindows/Oscilloscope.py`

Visible location:

- live scope pane in the right sidebar
- frame-scope means pane
- embedded preview oscilloscope in acquisition preview

Appearance:

- one stacked plot per active trace
- linked X axes
- floating colored text labels for each channel over the subplot stack

Trace colors use a fixed palette:

- blue
- orange
- green
- magenta
- orange-red
- deep blue
- yellow
- gray

Interactions:

- mouse wheel: Y zoom by default
- `Shift` + mouse wheel: X zoom
- middle mouse drag: X pan when zoomed in

Behavior:

- if no traces are active, shows a single empty plot
- rebuilds layout when the active trace set changes
- preserves a windowed X view when zoomed

## 6.5 AcquisitionPreviewWindow

File: `src/Windows/SubWindows/AcquisitionPreviewWindow.py`

This is the main file-preview UI for saved acquisitions.

### Standalone appearance

When not embedded, it creates a sizable split window with:

- left panel: image canvas and playback dock
- right panel: controls, scope, ROIs, and metadata/settings

It uses a green title-bar theme:

- inactive title bg: `[27, 92, 53]`
- active title bg: `[36, 122, 70]`

### Canvas interactions

Like the live feed, the preview canvas supports zoom, pan, ROI editing, and right-click reset behavior.

### Right-side control tree

Sections:

#### Scaling

- autoscale
- min/max scale
- autoscale grace
- mirrored difference
- display mode
- color scale
- `Set Zero`

#### Signal Processing

- LP filter
- LP cutoff
- drift correction
- background removal
- crop percent

#### Oscilloscope

- embedded scope panel for acquisition traces

#### ROIs

- embedded ROI dashboard and ROI controls

#### Settings

- snapshot of saved acquisition settings

#### Rendered Info

- show frame index
- show time
- show voltage
- info font size
- overlay position
- voltage channel selection

#### Color Scale

- colorbar toggle

#### Scale Bar

- scale-bar controls mirroring the live feed concepts

#### Export

- export current frame
- export video as MP4

### Playback dock

The preview includes a dedicated playback dock with a darker gray rounded child-window style.

Controls:

- frame slider
- jump to start
- rewind
- play/pause button
- repeat button
- fast forward
- jump to end
- playback FPS control

Behavior:

- repeat button changes theme when enabled
- preview can be file-backed or adapted from live data
- preview mode drives left-sidebar tab visibility via `AppLayout`

## 6.6 ImageWindow

File: `src/Windows/SubWindows/ImageWindow.py`

Purpose:

- reusable standalone image display window
- used for zero-reference display and similar static image surfaces

Behavior:

- preserves aspect ratio
- updates texture binding on resize with deferred resize handling

## 6.7 FunctionGeneratorWindow

File: `src/Windows/SubWindows/FunctionGenerator.py`

Visible location:

- embedded inside the PicoScope tab

Purpose:

- waveform control UI for the AWG
- acts as the state source for toolbar AWG status

## 6.8 ZAxisControlsWindow

File: `src/Windows/SubWindows/ZAxisControlsWindow.py`

Purpose:

- Z-axis control subwindow owned from camera workflows
- complements stage/focus-related camera operations

## 7. High-Level Interaction Rules

### 7.1 Live mode versus preview mode

The frontend has two major operating contexts:

- live mode, centered on the camera feed and live device controls
- preview mode, centered on acquisition playback and saved-data inspection

The mode switch is visible in the center tab bar and mirrored by left-sidebar tab visibility.

If a non-live center tab is active:

- standard control tabs are hidden
- the Preview tab is shown and selected

### 7.2 Data-to-UI flow

The frontend relies on a consistent pattern:

1. callbacks update backend state or request actions
2. worker threads gather or compute data
3. render methods update Dear PyGui widgets only when new data is available

This is important for texture-heavy and plot-heavy parts of the interface.

Examples:

- live camera frames are processed in the background, then copied into a persistent raw texture buffer
- ROI workers compute crop and trace payloads, then `ROIsWindow.render()` applies them
- the file browser scans the filesystem on a worker thread and publishes a snapshot

### 7.3 State synchronization

The toolbar is not an independent subsystem. It is a mirrored control surface that proxies into other windows and keeps itself visually synchronized.

Examples:

- toolbar exposure mirrors the camera settings exposure
- toolbar laser power mirrors the laser panel source value
- toolbar autoscale mirrors feed autoscale
- toolbar AWG state mirrors the function generator

### 7.4 Input gating

The shared flag `shared_state.currently_editing` suppresses some keyboard shortcuts while the user is typing or using dialogs.

Examples:

- viewport reset shortcut ignores keypresses while editing
- file dialogs set editing state while open

## 8. Keyboard And Pointer Behavior

Global keyboard and pointer behavior includes:

- `Ctrl+R`: reset viewport geometry
- `Space`: toggle preview
- `Shift+Space`: start or stop acquisition
- stage movement: `W`, `A`, `S`, `D`, `Q`, `E`
- stage speed modifier: `Shift`
- live feed and preview canvases support mouse-driven pan/zoom/ROI editing
- oscilloscope supports middle-mouse pan and wheel zoom

The center and right vertical splits are resized by dragging dedicated child-window handles, not by DPG docking.

## 9. Persistence Model

The frontend persists both geometry and semantic UI state.

### Viewport persistence

Stored in `AppState/WidefieldController.json`.

Includes:

- width
- height
- position
- maximized state

Saved with a debounce after viewport resize.

### Dear PyGui init-file persistence

Stored in `AppState/dpg_layout.ini`.

This captures standard Dear PyGui layout state such as positions and sizes for normal windows.

### Per-window JSON state

Each major window can define `SaveState()` and `LoadState()` and store custom fields under `AppState/`.

Examples already present in the repo include:

- Camera system state
- feed controls state
- ROI window state
- preview window state
- laser controls state
- stage controls state
- oscilloscope state

Tree-node open state is also captured and restored for many windows.

### Autosave cadence

Global state autosaves roughly once per second during runtime and again at exit.

## 10. Source-Of-Truth Files By Concern

Use these files when reconstructing or modifying the frontend:

- app entrypoint and render loop: `src/WidefieldController.py`
- primary shell and toolbar: `src/Windows/AppLayout.py`
- camera control panel: `src/Windows/CameraControls.py`
- live image surface: `src/Windows/SubWindows/CameraFeed.py`
- feed control panel: `src/Windows/SubWindows/FeedControlsWindow.py`
- ROI dashboard: `src/Windows/SubWindows/ROIsWindow.py`
- acquisition preview UI: `src/Windows/SubWindows/AcquisitionPreviewWindow.py`
- laser panel: `src/Windows/LaserControls.py`
- scope panel: `src/Windows/PicoScope.py`
- oscilloscope widget: `src/Windows/SubWindows/Oscilloscope.py`
- stage panel and right-side position map: `src/Windows/StageControls.py`
- file browser: `src/Windows/FileBrowser.py`
- console panel: `src/Windows/ConsoleWindow.py`
- themes: `src/Utils/themes.py`
- fonts and icons: `src/Utils/fonts.py`
- persistence helpers: `src/Utils/state_persistence.py`

## 11. Replication Rules For Another Agent

If another AI agent is recreating this frontend, it should preserve these invariants:

1. Keep the application as a single Dear PyGui desktop shell, not as multiple unrelated floating dialogs.
2. Preserve the top-toolbar plus three-column layout.
3. Preserve the left-tab / center-feed / right-analysis workspace model.
4. Preserve live mode and preview mode as two states of the same shell.
5. Preserve the render-thread boundary: workers publish state, render methods mutate DPG widgets.
6. Preserve raw-texture image rendering for live feed and preview surfaces.
7. Preserve ROI editing as an overlay interaction on the feed canvas.
8. Preserve the right-sidebar split between ROI analysis, scope plotting, and stage position map.
9. Preserve the dark theme with green active accents and compact icon-heavy toolbar controls.
10. Preserve persistence for viewport geometry, layout, and panel-specific state.

## 12. Short Visual Summary

If you need a one-paragraph mental model of the frontend:

This app looks like a dark laboratory control console. A compact icon-heavy toolbar spans the top. A narrow left sidebar holds tabbed control panels for camera, feed processing, laser, scope, stage, and mock-camera settings. The center of the screen is dominated by the live camera image, with console and file-browser tabs underneath. The right sidebar shows ROI analysis plots at the top, oscilloscope traces in the middle, and a stage position map at the bottom. Saved acquisitions open into a green-titled preview interface that reuses the same visual language but swaps the left controls into preview-specific settings and adds playback controls, export tools, and embedded scope/ROI playback surfaces.
