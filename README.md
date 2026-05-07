# Widefield Controller

Widefield Controller is a Windows desktop application for running a widefield imaging workflow with a Dear PyGui interface. It combines camera control, live image analysis, laser and power-meter monitoring, and PicoScope acquisition/AWG control in a single application.

The application is designed for lab use where an Andor camera is the primary image source, but it can also start in a development mode with a mock camera when no physical camera is available.

## Requirements

- Windows
- Python 3.10
- [Andor SDK](https://drive.google.com/file/d/1o8cqAKx5AXTWnpsL8CmMANWgeE_r0-RY/view?usp=drive_link)
- [PicoSDK](https://www.picotech.com/library/our-oscilloscope-software-development-kit-sdk#sdk_dl)

Optional hardware:

- Andor camera
- PicoScope Series 2000 or 4000
- PM1000 power meter

## Installation

From the repository root:

```powershell
span
```

Complete setup in this order:

1. Install the Andor SDK on Windows if you plan to use a real Andor camera. RMIT users can download it from [here](https://drive.google.com/file/d/1o8cqAKx5AXTWnpsL8CmMANWgeE_r0-RY/view?usp=drive_link).
2. Install PicoSDK if you plan to use a PicoScope with the application. You can download it from [here](https://www.picotech.com/library/our-oscilloscope-software-development-kit-sdk#sdk_dl).
3. Create and activate the Python 3.10 virtual environment.

   ```powershell
   py -3.10 -m venv venv
   venv\Scripts\Activate.ps1
   ```
4. Upgrade `pip` and install the Python dependencies from `requirements.txt`.

   ```
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

Notes:

- `requirements.txt` includes the local SDK wrapper packages under `src/APIs/pyAndorSDK3` and `src/APIs/picosdk`.
- `pythonnet` and `pywin32` are included for hardware integrations such as the Thorlabs power-meter driver.
- Running The Program

Start the application from the repository root:

```powershell
venv\Scripts\Activate.ps1
python .\src\WidefieldController.py
```

If no supported camera is found, startup may fall back to a development mode using a mock camera so the UI can still be opened and you can test things out without needing the zyla camera.

## Troubleshooting

- If the app opens without camera hardware, verify that the Andor SDK is installed correctly and that the camera drivers are available on the machine.
- If Python package installation fails, confirm you are using Python 3.10 and an activated virtual environment.
- If only part of the hardware stack is available, the UI may still be usable for development or partial workflows.
