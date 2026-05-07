# Widefield Controller

Widefield Controller is a Windows desktop application for running a widefield imaging workflow with a Dear PyGui interface. It combines camera control, live image analysis, laser and power-meter monitoring, and PicoScope acquisition/AWG control in a single application.

The application is designed for lab use where an Andor camera is the primary image source, but it can also start in a development mode with a mock camera when no physical camera is available.

## What The Program Does

Widefield Controller brings together three main runtime surfaces:

- Camera control and live preview
- Laser and power-meter control
- PicoScope acquisition and AWG control

From the UI you can:

- Start and stop live camera preview
- View image data in normal, difference, or contrast display modes
- Define and monitor ROIs with live traces
- Configure fixed-length acquisitions
- Pair camera acquisitions with PicoScope data during an experiment
- Save completed acquisitions to compressed `.npz` files

## How It Works

The application entrypoint is `src/WidefieldController.py`.

At startup it:

1. Creates the Dear PyGui context and main viewport.
2. Dynamically loads the window classes found in `src/Windows/`.
3. Instantiates each top-level window.
4. Restores saved UI state from `AppState/` when available.
5. Enters the main render loop and calls each window's `render()` method every frame.

The main windows are:

- `CameraControls`: camera setup, preview, acquisition workflow, and save pipeline
- `LaserControls`: laser driver and PM1000 power-meter control
- `PicoScope`: oscilloscope configuration and AWG control

Live camera acquisition and analysis run through background worker threads, while Dear PyGui updates are applied on the main render thread. Completed acquisitions can include camera frames, timestamps, ROI-derived analysis, and paired PicoScope data depending on the active hardware and settings.

## Requirements

- Windows
- Python 3.10
- An Andor SDK installation if you want to use a real Andor camera
- Access to the bundled or installed PicoScope Python SDK components used by this repository

Optional hardware:

- Andor camera
- PicoScope
- Laser controller
- PM1000 power meter

## Install Andor SDK

If you need real Andor camera support, install the Andor SDK before running the application.

RMIT users can download it here:

- https://drive.google.com/file/d/1o8cqAKx5AXTWnpsL8CmMANWgeE_r0-RY/view?usp=drive_link

After downloading:

1. Install the Andor SDK on Windows.
2. Make sure the Andor drivers and native libraries are available to the system.
3. Then install this project's Python dependencies and start the app.

If the Andor camera is not available, the application can still start in development mode using its mock camera path.

## Installation

From the repository root:

```powershell
py -3.10 -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Notes:

- `requirements.txt` includes the local SDK wrapper packages under `src/APIs/pyAndorSDK3` and `src/APIs/picosdk`.
- `pythonnet` and `pywin32` are included for hardware integrations such as the Thorlabs power-meter driver.
- The repository currently includes `node_modules`, but the main application is a Python desktop app and is launched with Python, not Node.

## Running The Program

Start the application from the repository root:

```powershell
venv\Scripts\python.exe .\src\WidefieldController.py
```

If no supported camera is found, startup may fall back to a development mode using a mock camera so the UI can still be opened.

## Data And State

- UI layout and persisted settings are stored in `AppState/`.
- Saved acquisitions are written as compressed `.npz` files.
- Example output files can be found in `Saves/`.

## Project Structure

```text
src/
  WidefieldController.py
  Drivers/
  Utils/
  Windows/
AppState/
Saves/
```

- `Drivers/` contains hardware-facing integrations.
- `Windows/` contains the top-level Dear PyGui windows and subwindows.
- `Utils/` contains shared state, persistence helpers, theming, and supporting utilities.

## Troubleshooting

- If the app opens without camera hardware, verify that the Andor SDK is installed correctly and that the camera drivers are available on the machine.
- If Python package installation fails, confirm you are using Python 3.10 and an activated virtual environment.
- If only part of the hardware stack is available, the UI may still be usable for development or partial workflows.