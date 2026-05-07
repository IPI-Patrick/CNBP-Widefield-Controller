import serial
import serial.tools.list_ports
import threading
import time
from typing import Optional


class SyringePumpDriver:

    def __init__(self, port: Optional[str] = None, poll_interval_ms: int = 500,
                 baud: int = 19200, serial_timeout: float = 0.05):
        self.port = port
        self._baud = baud
        self._serial_timeout = serial_timeout
        self._poll_interval_ms = poll_interval_ms

        self.connected = False
        self.state = "Disconnected"
        self.position: Optional[int] = None
        self.moved_ml: Optional[float] = None
        self.target_steps: Optional[int] = None
        self.steps_per_ml: Optional[float] = None
        self.elapsed_ms: Optional[int] = None
        self.run_duration_ms: Optional[int] = None
        self.awaiting_cal = False
        self.last_error: Optional[str] = None

        self._last_status_line: Optional[str] = None
        self._last_poll_time = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._serial: Optional[serial.Serial] = None
        self._status_thread: Optional[threading.Thread] = None

        if self.port:
            self.connect(self.port)

    @staticmethod
    def list_ports() -> list:
        try:
            return [p.device for p in serial.tools.list_ports.comports()]
        except Exception:
            return []

    def connect(self, port: Optional[str] = None) -> bool:
        if port:
            self.port = port
        if self.connected:
            return True
        try:
            self.state = "Connecting"
            self._serial = serial.Serial(self.port, self._baud, timeout=self._serial_timeout)
            if not self._handshake(attempts=5, per_attempt_timeout=0.5):
                self.state = "Disconnected"
                self.last_error = "Handshake failed"
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                return False
            self.connected = True
            self.last_error = None
            self._stop_event.clear()
            self._status_thread = threading.Thread(
                target=self._status_loop, daemon=True, name="SyringePumpPoll"
            )
            self._status_thread.start()
            return True
        except Exception as e:
            self.last_error = f"Connect failed: {e}"
            self.connected = False
            self._serial = None
            return False

    def disconnect(self):
        self.state = "Disconnected"
        self.connected = False
        self._stop_event.set()
        if self._status_thread and self._status_thread.is_alive():
            self._status_thread.join(timeout=1.5)
        self._status_thread = None
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    def get_state(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "state": self.state,
                "position": self.position,
                "moved_ml": self.moved_ml,
                "target_steps": self.target_steps,
                "steps_per_ml": self.steps_per_ml,
                "elapsed_ms": self.elapsed_ms,
                "run_duration_ms": self.run_duration_ms,
                "awaiting_cal": self.awaiting_cal,
                "last_error": self.last_error,
            }

    def snapshot(self) -> dict:
        return self.get_state()

    def start_dose(self, volume_ml: float, duration_s: float):
        self._send_raw(f"DO {volume_ml} {duration_s}")

    def stop(self):
        self._send_raw("STOP")

    def jog(self, steps: int):
        self._send_raw(f"JOG {int(steps)}")

    def goto(self, steps: int):
        self._send_raw(f"GOTO_STEPS {int(steps)}")

    def set_zero(self):
        self._send_raw("ZERO")

    def calibrate_start(self, volume_ml: float, duration_s: float):
        self._send_raw(f"CAL_START {volume_ml} {duration_s}")

    def calibrate(self, volume_ml: float, duration_s: float, actual_ml: float):
        self._send_raw(f"CAL {volume_ml} {duration_s} {actual_ml}")

    # --- Internal ---

    def _send_raw(self, cmd: str):
        if not self._serial or not self._serial.is_open:
            return
        try:
            self._serial.write((cmd.strip() + "\n").encode())
        except Exception as e:
            self.last_error = f"Write error: {e}"

    def _status_loop(self):
        while not self._stop_event.is_set():
            if not self.connected or not self._serial:
                time.sleep(0.5)
                continue
            now = time.time() * 1000.0
            if now - self._last_poll_time >= self._poll_interval_ms:
                self._send_raw("STATUS")
                self._last_poll_time = now
            try:
                self._read_available_lines()
            except Exception as e:
                self.last_error = f"Read error: {e}"
                time.sleep(0.2)
            time.sleep(0.01)

    def _handshake(self, attempts: int = 3, per_attempt_timeout: float = 0.5) -> bool:
        for _ in range(attempts):
            start_line = self._last_status_line
            start_time = time.time()
            self._send_raw("STATUS")
            while time.time() - start_time < per_attempt_timeout:
                self._read_available_lines()
                if self._last_status_line and self._last_status_line != start_line:
                    return True
                time.sleep(0.02)
        return False

    def _read_available_lines(self):
        if not self._serial:
            return
        while True:
            raw = self._serial.readline()
            if not raw:
                break
            try:
                line = raw.decode(errors="replace").strip()
            except Exception:
                continue
            if line.startswith("State:"):
                with self._lock:
                    self._last_status_line = line
                self._parse_status_line(line)

    def _parse_status_line(self, line: str):
        try:
            parts = [p.strip() for p in line.split("|")]
            if parts[0].startswith("State:"):
                self.state = parts[0].split(":", 1)[1].strip()
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    k, v = k.strip(), v.strip()
                    try:
                        if k == "stepsPerMl":      self.steps_per_ml = float(v)
                        elif k == "pos":           self.position = int(v)
                        elif k == "movedMl":       self.moved_ml = float(v)
                        elif k == "targetSteps":   self.target_steps = int(v)
                        elif k == "elapsedMs":     self.elapsed_ms = int(v)
                        elif k == "runDurationMs": self.run_duration_ms = int(v)
                    except Exception:
                        pass
                if "Awaiting CAL_ACTUAL" in p:
                    self.awaiting_cal = True
            if "Awaiting CAL_ACTUAL" not in line:
                self.awaiting_cal = False
        except Exception:
            pass
