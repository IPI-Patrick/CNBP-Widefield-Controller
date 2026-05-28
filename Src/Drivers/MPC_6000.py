"""
Driver for the Novanta gem laser with MPC6000 PSU controller.
Communicates over RS232 at 19200 baud (8N1, no flow control).

All commands are ASCII strings terminated with \r.
The PSU echoes a response terminated with \r\n before the next
command may be sent.  A background polling thread reads POWER?,
LASTEMP?, PSUTEMP?, and STATUS? continuously so get_state() is
always cheap and non-blocking.
"""

import re
import threading
import time

import serial
import serial.tools.list_ports

from Utils.utils import clamp

_BAUD_RATE      = 19200
_TIMEOUT_S      = 2.0
_POLL_INTERVAL  = 0.5   # seconds between full state reads


class MPC_6000:

    def __init__(self, max_power_mw: float = 500.0):
        self.max_power_mw = max_power_mw

        self._port: str | None = None
        self._serial: serial.Serial | None = None

        # Separate locks: _serial_lock serialises RS232 I/O;
        # _state_lock guards the cached state fields.
        self._serial_lock = threading.Lock()
        self._state_lock  = threading.Lock()

        self._stop_event  = threading.Event()
        self._thread: threading.Thread | None = None

        # Cached state (written by poll thread, read by get_state)
        self._connected       = False
        self._emission_on     = False
        self._actual_power_mw = 0.0
        self._target_power_mw = 0.0
        self._laser_temp_c    = 0.0
        self._psu_temp_c      = 0.0
        self._status          = ""
        self._control_mode    = "POWER"
        self._version         = ""

    # ── Port helpers ─────────────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()]

    @property
    def COMPort(self) -> str:
        return self._port or ""

    @COMPort.setter
    def COMPort(self, value: str):
        self._port = value.strip() if value and value.strip() else None

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Connection ───────────────────────────────────────────────────────

    def connect(self, port: str | None = None):
        if port:
            self._port = port.strip()
        if not self._port:
            raise ValueError("No COM port specified")

        ser = serial.Serial(
            port=self._port,
            baudrate=_BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=_TIMEOUT_S,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

        with self._state_lock:
            self._serial = ser
            self._connected = True

        # Fetch version once on connect (non-poll command)
        ver = self._send("VERSION?")
        with self._state_lock:
            self._version = ver

        self._start_poll()

    def disconnect(self):
        self._stop_poll()
        with self._serial_lock:
            ser = self._serial
            self._serial = None
        if ser and ser.is_open:
            try:
                ser.close()
            except Exception:
                pass
        with self._state_lock:
            self._connected       = False
            self._emission_on     = False
            self._actual_power_mw = 0.0
            self._laser_temp_c    = 0.0
            self._psu_temp_c      = 0.0
            self._status          = ""

    # ── State ────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        with self._state_lock:
            return {
                "connected":       self._connected,
                "emission_on":     self._emission_on,
                "actual_power_mw": self._actual_power_mw,
                "target_power_mw": self._target_power_mw,
                "laser_temp_c":    self._laser_temp_c,
                "psu_temp_c":      self._psu_temp_c,
                "status":          self._status,
                "control_mode":    self._control_mode,
                "version":         self._version,
            }

    # ── Commands ─────────────────────────────────────────────────────────

    def set_emission(self, enable: bool):
        """Send ON or OFF to the PSU."""
        self._send("ON" if enable else "OFF")
        with self._state_lock:
            self._emission_on = enable

    def set_power(self, power_mw: float):
        """Set output power in mW (Power mode only)."""
        power_mw = clamp(power_mw, 0.0, self.max_power_mw)
        self._send(f"POWER={int(round(power_mw))}")
        with self._state_lock:
            self._target_power_mw = power_mw

    def set_current(self, percent: float):
        """Set diode current as a percentage (0–100) of maximum."""
        percent = clamp(percent, 0.0, 100.0)
        self._send(f"CURRENT={int(round(percent))}")

    def set_control_mode(self, mode: str):
        """Switch between 'POWER' and 'CURRENT' control modes."""
        mode = mode.upper()
        if mode not in ("POWER", "CURRENT"):
            raise ValueError("mode must be 'POWER' or 'CURRENT'")
        self._send(f"CONTROL={mode}")
        with self._state_lock:
            self._control_mode = mode

    def set_startup_emission(self, enable: bool):
        """Configure whether the laser enables automatically at power-on.
        Must call write_memory() afterwards to persist."""
        self._send(f"STEN={'YES' if enable else 'NO'}")

    def set_startup_power(self, power_mw: float):
        """Set the default power used at start-up.
        Must call write_memory() afterwards to persist."""
        power_mw = clamp(power_mw, 0.0, self.max_power_mw)
        self._send(f"STPOW={int(round(power_mw))}")

    def calibrate_power(self, measured_mw: float):
        """Recalibrate APC mode against an external power meter reading (mW).
        Must call write_memory() afterwards to persist."""
        self._send(f"ACTP={int(round(measured_mw))}")

    def write_memory(self):
        """Persist APC calibration, STEN and STPOW to non-volatile memory."""
        self._send("WRITE")

    def query_timers(self) -> dict:
        """Return power-on and diode runtime timers as a dict of floats."""
        response = self._send("TIMERS?")
        return self._parse_timers(response)

    def query_version(self) -> str:
        """Return the firmware version string."""
        return self._send("VERSION?")

    # ── Internal send/receive ────────────────────────────────────────────

    def _send(self, cmd: str) -> str:
        """Send *cmd* over RS232 and return the stripped response line.
        Thread-safe; returns '' on any error."""
        with self._serial_lock:
            ser = self._serial
            if ser is None or not ser.is_open:
                return ""
            try:
                ser.reset_input_buffer()
                ser.write((cmd + "\r").encode("ascii"))
                raw = ser.readline()
                return raw.decode("ascii", errors="ignore").strip()
            except Exception as exc:
                print(f"MPC_6000 send error ({cmd}): {exc}")
                return ""

    # ── Polling thread ───────────────────────────────────────────────────

    def _start_poll(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _stop_poll(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=4.0)
            self._thread = None

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                print(f"MPC_6000 poll error: {exc}")
            self._stop_event.wait(_POLL_INTERVAL)

    def _poll_once(self):
        """Read all dynamic fields from the PSU and update cached state."""
        power_resp  = self._send("POWER?")
        ltemp_resp  = self._send("LASTEMP?")
        ptemp_resp  = self._send("PSUTEMP?")
        status_resp = self._send("STATUS?")

        power  = self._parse_float(power_resp)
        ltemp  = self._parse_float(ltemp_resp)
        ptemp  = self._parse_float(ptemp_resp)

        with self._state_lock:
            if power is not None:
                self._actual_power_mw = power
            if ltemp is not None:
                self._laser_temp_c = ltemp
            if ptemp is not None:
                self._psu_temp_c = ptemp
            if status_resp:
                self._status = status_resp
                self._emission_on = status_resp.strip().upper() == "ENABLED"

    # ── Parsing helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_float(text: str) -> float | None:
        if not text:
            return None
        match = re.search(r"[-+]?\d*\.?\d+", text)
        if match:
            try:
                return float(match.group())
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_timers(text: str) -> dict:
        result: dict = {}
        if not text:
            return result
        for line in text.splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                try:
                    result[key.strip()] = float(val.strip())
                except ValueError:
                    result[key.strip()] = val.strip()
        return result


# Alias used by the existing LaserControls import:
#   import Drivers.MPC_6000 as LaserDriverModule
#   self.laser = LaserDriverModule.LaserDriver()
LaserDriver = MPC_6000
