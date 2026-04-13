import threading
import time
from typing import Any, Dict, Optional

import serial
import serial.tools.list_ports


class ZAxisDriver:
    """Serial driver for the microscope z-axis stepper controller.

    Firmware protocol from StepperController:
        P           -> returns current position as P<steps>
        M<n>        -> relative move in steps
        G<n>        -> move to absolute position in steps
        C<n>        -> continuous move at signed steps/second
        S           -> stop motion
        V<n>        -> set move speed in steps/second for M/G
        A<n>        -> set acceleration in steps/s^2
        R<n>        -> set acceleration in degrees/s^2
        Z<n>        -> set current position to n steps
    """

    CONTINUOUS_KEEPALIVE_MS = 100.0
    STARTUP_SETTLE_SECONDS = 2.0
    CONNECT_PROBE_ATTEMPTS = 3
    CONNECT_PROBE_TIMEOUT_SECONDS = 0.5
    STEPS_PER_OUTPUT_REV = 200 * 32 * 10

    def __init__(self, port: Optional[str] = None, baud: int = 115200, poll_interval_ms: int = 200, serial_timeout: float = 0.1):
        self.port = port
        self.baud = int(baud)
        self.poll_interval_ms = max(50, int(poll_interval_ms))
        self.serial_timeout = float(serial_timeout)

        self.connected = False
        self.state = "Disconnected"
        self.position_steps: Optional[int] = None
        self.speed_steps_per_s: Optional[float] = None
        self.target_position_steps: Optional[int] = None
        self.is_moving = False
        self.last_status_line: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_poll_time_ms = 0.0
        self.last_keepalive_time_ms = 0.0
        self.commanded_speed_steps_per_s = float(1000.0)
        self.commanded_acceleration_steps_per_s2 = float(5000.0)
        self.active_continuous_speed_steps_per_s = 0.0

        self.lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.serial_port: Optional[serial.Serial] = None
        self.status_thread: Optional[threading.Thread] = None

        if self.port:
            self.connect(self.port)

    @staticmethod
    def list_ports() -> list[str]:
        try:
            return [port.device for port in serial.tools.list_ports.comports()]
        except Exception:
            return []

    def connect(self, port: Optional[str] = None) -> bool:
        if port is not None:
            self.port = str(port).strip() or None
        if not self.port:
            self.last_error = "No COM port selected"
            return False
        if self.connected and self.serial_port and self.serial_port.is_open:
            return True

        self.disconnect()

        try:
            self.serial_port = serial.Serial(
                self.port,
                self.baud,
                timeout=self.serial_timeout,
                write_timeout=self.serial_timeout,
            )
            time.sleep(self.STARTUP_SETTLE_SECONDS)
            try:
                self.serial_port.reset_input_buffer()
                self.serial_port.reset_output_buffer()
            except Exception:
                pass

            initial_position = self._probe_position()
            if initial_position is None:
                raise RuntimeError("No valid position response from controller")

            self.connected = True
            self.state = "Idle"
            self.position_steps = initial_position
            self.target_position_steps = None
            self.is_moving = False
            self.last_error = None
            self.stop_event.clear()
            self.status_thread = threading.Thread(target=self._status_loop, daemon=True, name="ZAxisStatusPoll")
            self.status_thread.start()
            self.request_status()
            return True
        except Exception as exc:
            self.connected = False
            self.state = "Disconnected"
            self.serial_port = None
            self.last_error = f"Connect failed: {exc}"
            return False

    def disconnect(self):
        self.stop_event.set()
        if self.status_thread is not None and self.status_thread.is_alive():
            self.status_thread.join(timeout=1.0)
        self.status_thread = None

        try:
            if self.serial_port is not None and self.serial_port.is_open:
                self.serial_port.close()
        except Exception:
            pass

        self.serial_port = None
        self.connected = False
        self.state = "Disconnected"
        self.is_moving = False
        self.target_position_steps = None
        self.active_continuous_speed_steps_per_s = 0.0

    def _probe_position(self) -> Optional[int]:
        if self.serial_port is None or not self.serial_port.is_open:
            return None

        previous_timeout = self.serial_port.timeout
        try:
            self.serial_port.timeout = self.CONNECT_PROBE_TIMEOUT_SECONDS
            for _ in range(self.CONNECT_PROBE_ATTEMPTS):
                try:
                    self.serial_port.reset_input_buffer()
                except Exception:
                    pass
                self._send_raw(self._build_status_command())
                response = self.serial_port.readline()
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
            self.serial_port.timeout = previous_timeout

    def close(self):
        self.disconnect()

    def request_status(self):
        self.send_command(self._build_status_command())

    def jog(self, steps: int, speed_steps_per_s: Optional[float] = None):
        steps = int(steps)
        if steps == 0:
            return

        if speed_steps_per_s is not None:
            self.set_speed_steps_per_second(speed_steps_per_s)

        with self.lock:
            if self.position_steps is not None:
                self.target_position_steps = int(self.position_steps) + steps
            self.is_moving = True
            self.state = "Jogging"

        self.send_command(self._build_jog_command(steps))

    def jog_with_revs_per_second(self, steps: int, revs_per_second: Optional[float] = None):
        speed_steps_per_s = None if revs_per_second is None else self.revs_per_second_to_steps_per_second(revs_per_second)
        self.jog(steps, speed_steps_per_s)

    def start_continuous(self, direction: int, speed_steps_per_s: float):
        direction = -1 if int(direction) < 0 else 1
        signed_speed = direction * max(1, int(round(float(speed_steps_per_s))))

        with self.lock:
            self.active_continuous_speed_steps_per_s = float(signed_speed)
            self.target_position_steps = None
            self.is_moving = signed_speed != 0
            self.state = "Continuous" if signed_speed != 0 else "Idle"

        self.send_command(self._build_continuous_command(signed_speed))

    def start_continuous_revs_per_second(self, direction: int, revs_per_second: float):
        self.start_continuous(direction, self.revs_per_second_to_steps_per_second(revs_per_second))

    def stop_motion(self):
        with self.lock:
            self.active_continuous_speed_steps_per_s = 0.0
            self.target_position_steps = None
            self.is_moving = False
            self.state = "Idle"
        self.send_command(self._build_stop_command())

    def set_zero(self):
        with self.lock:
            self.position_steps = 0
            self.target_position_steps = 0
            self.is_moving = False
            self.state = "Idle"
        self.send_command(self._build_zero_command(0))

    def set_speed_steps_per_second(self, speed_steps_per_s: float):
        speed_steps_per_s = max(1, int(round(float(speed_steps_per_s))))
        with self.lock:
            if int(round(self.commanded_speed_steps_per_s)) == speed_steps_per_s:
                return
            self.commanded_speed_steps_per_s = float(speed_steps_per_s)
            self.speed_steps_per_s = float(speed_steps_per_s)
        self.send_command(self._build_set_speed_command(speed_steps_per_s))

    def set_speed_revs_per_second(self, revs_per_second: float):
        self.set_speed_steps_per_second(self.revs_per_second_to_steps_per_second(revs_per_second))

    def set_acceleration_steps_per_second_squared(self, acceleration_steps_per_s2: float):
        acceleration_steps_per_s2 = max(1, int(round(float(acceleration_steps_per_s2))))
        with self.lock:
            if int(round(self.commanded_acceleration_steps_per_s2)) == acceleration_steps_per_s2:
                return
            self.commanded_acceleration_steps_per_s2 = float(acceleration_steps_per_s2)
        self.send_command(self._build_set_acceleration_steps_command(acceleration_steps_per_s2))

    def set_acceleration_degrees_per_second_squared(self, acceleration_deg_per_s2: float):
        acceleration_deg_per_s2 = max(0.0001, float(acceleration_deg_per_s2))
        self.send_command(self._build_set_acceleration_degrees_command(acceleration_deg_per_s2))
        with self.lock:
            self.commanded_acceleration_steps_per_s2 = self.degrees_per_second_squared_to_steps_per_second_squared(acceleration_deg_per_s2)

    def revs_per_second_to_steps_per_second(self, revs_per_second: float) -> float:
        return max(1.0, float(revs_per_second) * float(self.STEPS_PER_OUTPUT_REV))

    def steps_per_second_to_revs_per_second(self, steps_per_second: Optional[float]) -> Optional[float]:
        if steps_per_second is None:
            return None
        return float(steps_per_second) / float(self.STEPS_PER_OUTPUT_REV)

    def degrees_per_second_squared_to_steps_per_second_squared(self, degrees_per_second_squared: float) -> float:
        return max(1.0, (float(degrees_per_second_squared) * float(self.STEPS_PER_OUTPUT_REV)) / 360.0)

    def steps_per_second_squared_to_degrees_per_second_squared(self, steps_per_second_squared: Optional[float]) -> Optional[float]:
        if steps_per_second_squared is None:
            return None
        return (float(steps_per_second_squared) * 360.0) / float(self.STEPS_PER_OUTPUT_REV)

    def send_command(self, command: str):
        self._send_raw(command)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "connected": self.connected,
                "port": self.port,
                "state": self.state,
                "position_steps": self.position_steps,
                "speed_steps_per_s": self.speed_steps_per_s,
                "speed_revs_per_s": self.steps_per_second_to_revs_per_second(self.speed_steps_per_s),
                "acceleration_steps_per_s2": self.commanded_acceleration_steps_per_s2,
                "acceleration_deg_per_s2": self.steps_per_second_squared_to_degrees_per_second_squared(self.commanded_acceleration_steps_per_s2),
                "target_position_steps": self.target_position_steps,
                "is_moving": self.is_moving,
                "last_status_line": self.last_status_line,
                "last_error": self.last_error,
            }

    def _status_loop(self):
        while not self.stop_event.is_set():
            if not self.connected or self.serial_port is None or not self.serial_port.is_open:
                time.sleep(0.1)
                continue

            current_time_ms = time.perf_counter() * 1000.0
            if current_time_ms - self.last_poll_time_ms >= self.poll_interval_ms:
                self._send_raw(self._build_status_command())
                self.last_poll_time_ms = current_time_ms

            with self.lock:
                active_continuous_speed = int(round(self.active_continuous_speed_steps_per_s))

            if active_continuous_speed != 0 and (current_time_ms - self.last_keepalive_time_ms) >= self.CONTINUOUS_KEEPALIVE_MS:
                self._send_raw(self._build_continuous_command(active_continuous_speed))
                self.last_keepalive_time_ms = current_time_ms

            try:
                self._read_available_lines()
            except Exception as exc:
                self.last_error = f"Read error: {exc}"
            time.sleep(0.01)

    def _send_raw(self, command: str):
        if not command:
            return
        if self.serial_port is None or not self.serial_port.is_open:
            self.last_error = "Serial port is not open"
            return

        try:
            payload = f"{command.strip()}\n".encode("ascii")
            with self.io_lock:
                self.serial_port.write(payload)
                self.serial_port.flush()
        except Exception as exc:
            self.last_error = f"Write error: {exc}"

    def _read_available_lines(self):
        if self.serial_port is None:
            return

        while True:
            raw = self.serial_port.readline()
            if not raw:
                break

            try:
                line = raw.decode(errors="replace").strip()
            except Exception:
                continue

            if not line:
                continue

            with self.lock:
                self.last_status_line = line
            self._parse_status_line(line)

    def _parse_status_line(self, line: str):
        with self.lock:
            if line == "OK":
                self.last_error = None
                return

            if line.startswith("ERR"):
                self.last_error = line
                self.state = "Error"
                self.is_moving = False
                self.active_continuous_speed_steps_per_s = 0.0
                return

            if line.startswith("P"):
                try:
                    position_steps = int(line[1:])
                except ValueError:
                    self.last_error = f"Unexpected position response: {line}"
                    return

                previous_position = self.position_steps
                self.position_steps = position_steps
                self.last_error = None

                if self.active_continuous_speed_steps_per_s != 0:
                    self.is_moving = True
                    self.state = "Continuous"
                    self.speed_steps_per_s = abs(self.active_continuous_speed_steps_per_s)
                    return

                if self.target_position_steps is not None and position_steps == self.target_position_steps:
                    self.is_moving = False
                    self.state = "Idle"
                    self.target_position_steps = None
                    return

                if previous_position is not None and position_steps != previous_position:
                    self.is_moving = True
                    if self.target_position_steps is not None:
                        self.state = "Moving"
                    else:
                        self.state = "Jogging"
                else:
                    self.is_moving = False
                    if self.state != "Error":
                        self.state = "Idle"
                return

            if line.startswith("CMDS"):
                return

            self.last_error = f"Unexpected response: {line}"

    def _build_status_command(self) -> str:
        return "P"

    def _build_jog_command(self, steps: int) -> str:
        return f"M{int(steps)}"

    def _build_continuous_command(self, speed_steps_per_s: int) -> str:
        return f"C{int(speed_steps_per_s)}"

    def _build_stop_command(self) -> str:
        return "S"

    def _build_zero_command(self, steps: int) -> str:
        return f"Z{int(steps)}"

    def _build_set_speed_command(self, speed_steps_per_s: int) -> str:
        return f"V{int(speed_steps_per_s)}"

    def _build_set_acceleration_steps_command(self, acceleration_steps_per_s2: int) -> str:
        return f"A{int(acceleration_steps_per_s2)}"

    def _build_set_acceleration_degrees_command(self, acceleration_deg_per_s2: float) -> str:
        return f"R{float(acceleration_deg_per_s2):g}"
