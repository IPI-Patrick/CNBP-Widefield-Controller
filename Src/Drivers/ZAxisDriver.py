import threading
import time
from typing import Optional

import serial
import serial.tools.list_ports


class ZAxisDriver:

    STEPS_PER_OUTPUT_REV = 200 * 32 * 10
    _CONTINUOUS_KEEPALIVE_MS = 100.0
    _STARTUP_SETTLE_SECONDS = 2.0
    _PROBE_ATTEMPTS = 3
    _PROBE_TIMEOUT_SECONDS = 0.5

    def __init__(self, port: Optional[str] = None, baud: int = 115200,
                 poll_interval_ms: int = 200, serial_timeout: float = 0.1):
        self.port = port
        self.baud = int(baud)
        self._poll_interval_ms = max(50, int(poll_interval_ms))
        self._serial_timeout = float(serial_timeout)

        self.connected = False
        self.state = "Disconnected"
        self.position_steps: Optional[int] = None
        self.is_moving = False
        self.last_error: Optional[str] = None

        self._speed_steps_per_s = 1000.0
        self._accel_steps_per_s2 = 5000.0
        self._active_continuous_speed = 0.0
        self._target_position_steps: Optional[int] = None
        self._last_poll_ms = 0.0
        self._last_keepalive_ms = 0.0

        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
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
        if port is not None:
            self.port = str(port).strip() or None
        if not self.port:
            self.last_error = "No COM port selected"
            return False
        if self.connected and self._serial and self._serial.is_open:
            return True

        self.disconnect()
        try:
            self._serial = serial.Serial(
                self.port, self.baud,
                timeout=self._serial_timeout,
                write_timeout=self._serial_timeout,
            )
            time.sleep(self._STARTUP_SETTLE_SECONDS)
            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
            except Exception:
                pass

            initial_position = self._probe_position()
            if initial_position is None:
                raise RuntimeError("No valid position response from controller")

            self.connected = True
            self.state = "Idle"
            self.position_steps = initial_position
            self._target_position_steps = None
            self.is_moving = False
            self.last_error = None
            self._stop_event.clear()
            self._status_thread = threading.Thread(
                target=self._status_loop, daemon=True, name="ZAxisStatusPoll"
            )
            self._status_thread.start()
            self._send("P")
            return True
        except Exception as exc:
            self.connected = False
            self.state = "Disconnected"
            self._serial = None
            self.last_error = f"Connect failed: {exc}"
            return False

    def disconnect(self):
        self._stop_event.set()
        if self._status_thread is not None and self._status_thread.is_alive():
            self._status_thread.join(timeout=1.0)
        self._status_thread = None
        try:
            if self._serial is not None and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass
        self._serial = None
        self.connected = False
        self.state = "Disconnected"
        self.is_moving = False
        self._target_position_steps = None
        self._active_continuous_speed = 0.0

    def get_state(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "state": self.state,
                "position_steps": self.position_steps,
                "is_moving": self.is_moving,
                "last_error": self.last_error,
                "speed_revs_per_s": self._speed_steps_per_s / self.STEPS_PER_OUTPUT_REV,
            }

    def get_settings(self) -> dict:
        with self._lock:
            return {
                "speed_steps_per_s": self._speed_steps_per_s,
                "accel_steps_per_s2": self._accel_steps_per_s2,
                "speed_revs_per_s": self._speed_steps_per_s / self.STEPS_PER_OUTPUT_REV,
                "accel_deg_per_s2": (self._accel_steps_per_s2 * 360.0) / self.STEPS_PER_OUTPUT_REV,
            }

    def set_settings(self, *, speed_steps_per_s=None, accel_steps_per_s2=None):
        if speed_steps_per_s is not None:
            speed = max(1, int(round(float(speed_steps_per_s))))
            with self._lock:
                if int(round(self._speed_steps_per_s)) != speed:
                    self._speed_steps_per_s = float(speed)
            self._send(f"V{speed}")
        if accel_steps_per_s2 is not None:
            accel = max(1, int(round(float(accel_steps_per_s2))))
            with self._lock:
                if int(round(self._accel_steps_per_s2)) != accel:
                    self._accel_steps_per_s2 = float(accel)
            self._send(f"A{accel}")

    def jog(self, steps: int, speed_steps_per_s: Optional[float] = None):
        steps = int(steps)
        if steps == 0:
            return
        if speed_steps_per_s is not None:
            self.set_settings(speed_steps_per_s=speed_steps_per_s)
        with self._lock:
            if self.position_steps is not None:
                self._target_position_steps = int(self.position_steps) + steps
            self.is_moving = True
            self.state = "Jogging"
        self._send(f"M{steps}")

    def move_continuous(self, direction: int, speed_steps_per_s: float):
        direction = -1 if int(direction) < 0 else 1
        signed_speed = direction * max(1, int(round(float(speed_steps_per_s))))
        with self._lock:
            self._active_continuous_speed = float(signed_speed)
            self._target_position_steps = None
            self.is_moving = signed_speed != 0
            self.state = "Continuous" if signed_speed != 0 else "Idle"
        self._send(f"C{signed_speed}")

    def stop(self):
        with self._lock:
            self._active_continuous_speed = 0.0
            self._target_position_steps = None
            self.is_moving = False
            self.state = "Idle"
        self._send("S")

    def set_zero(self):
        with self._lock:
            self.position_steps = 0
            self._target_position_steps = 0
            self.is_moving = False
            self.state = "Idle"
        self._send("Z0")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "port": self.port,
                "state": self.state,
                "position_steps": self.position_steps,
                "is_moving": self.is_moving,
                "last_error": self.last_error,
                "speed_steps_per_s": self._speed_steps_per_s,
                "speed_revs_per_s": self._speed_steps_per_s / self.STEPS_PER_OUTPUT_REV,
                "accel_steps_per_s2": self._accel_steps_per_s2,
                "accel_deg_per_s2": (self._accel_steps_per_s2 * 360.0) / self.STEPS_PER_OUTPUT_REV,
            }

    # --- Internal ---

    def _probe_position(self) -> Optional[int]:
        if self._serial is None or not self._serial.is_open:
            return None
        prev_timeout = self._serial.timeout
        try:
            self._serial.timeout = self._PROBE_TIMEOUT_SECONDS
            for _ in range(self._PROBE_ATTEMPTS):
                try:
                    self._serial.reset_input_buffer()
                except Exception:
                    pass
                self._send_raw("P")
                response = self._serial.readline()
                if not response:
                    continue
                try:
                    line = response.decode("ascii", errors="replace").strip()
                except Exception:
                    continue
                if line.startswith("P"):
                    try:
                        return int(line[1:])
                    except ValueError:
                        continue
            return None
        finally:
            self._serial.timeout = prev_timeout

    def _send(self, command: str):
        self._send_raw(command)

    def _send_raw(self, command: str):
        if not command or self._serial is None or not self._serial.is_open:
            return
        try:
            payload = f"{command.strip()}\n".encode("ascii")
            with self._io_lock:
                self._serial.write(payload)
                self._serial.flush()
        except Exception as exc:
            self.last_error = f"Write error: {exc}"

    def _status_loop(self):
        while not self._stop_event.is_set():
            if not self.connected or self._serial is None or not self._serial.is_open:
                time.sleep(0.1)
                continue

            now_ms = time.perf_counter() * 1000.0
            if now_ms - self._last_poll_ms >= self._poll_interval_ms:
                self._send_raw("P")
                self._last_poll_ms = now_ms

            with self._lock:
                cont_speed = int(round(self._active_continuous_speed))
            if cont_speed != 0 and (now_ms - self._last_keepalive_ms) >= self._CONTINUOUS_KEEPALIVE_MS:
                self._send_raw(f"C{cont_speed}")
                self._last_keepalive_ms = now_ms

            try:
                self._read_available_lines()
            except Exception as exc:
                self.last_error = f"Read error: {exc}"
            time.sleep(0.01)

    def _read_available_lines(self):
        if self._serial is None:
            return
        while True:
            raw = self._serial.readline()
            if not raw:
                break
            try:
                line = raw.decode(errors="replace").strip()
            except Exception:
                continue
            if line:
                self._parse_status_line(line)

    def _parse_status_line(self, line: str):
        with self._lock:
            if line == "OK":
                self.last_error = None
                return
            if line.startswith("ERR"):
                self.last_error = line
                self.state = "Error"
                self.is_moving = False
                self._active_continuous_speed = 0.0
                return
            if line.startswith("P"):
                try:
                    pos = int(line[1:])
                except ValueError:
                    self.last_error = f"Unexpected response: {line}"
                    return
                prev = self.position_steps
                self.position_steps = pos
                self.last_error = None
                if self._active_continuous_speed != 0:
                    self.is_moving = True
                    self.state = "Continuous"
                    return
                if self._target_position_steps is not None and pos == self._target_position_steps:
                    self.is_moving = False
                    self.state = "Idle"
                    self._target_position_steps = None
                    return
                if prev is not None and pos != prev:
                    self.is_moving = True
                    self.state = "Moving" if self._target_position_steps is not None else "Jogging"
                else:
                    self.is_moving = False
                    if self.state != "Error":
                        self.state = "Idle"
