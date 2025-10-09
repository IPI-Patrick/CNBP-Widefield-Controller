import serial
import threading
import time
from typing import Optional, Dict, Any

# NOTE: This driver talks to the firmware in `main.cpp` (peristaltic pump controller).
# The firmware exposes a newline terminated text protocol with commands like STATUS, DO, STOP, etc.
# A typical STATUS response looks like:
#   State: RUN_DOSE | stepsPerMl=1000.0000 | pos=123 | movedSteps=456 | movedMl=0.4567 | targetSteps=1000 | speedStepsPerS=250.00 | elapsedMs=1234 | runDurationMs=5000
# We poll the device periodically (poll_interval_ms) and parse these key/value pairs into attributes.

class SyringePumpDriver:
    # Public attributes (updated by polling thread)
    connected: bool
    state: str
    position: Optional[int]
    moved_steps: Optional[int]
    moved_ml: Optional[float]
    target_steps: Optional[int]
    speed_steps_per_s: Optional[float]
    elapsed_ms: Optional[int]
    run_duration_ms: Optional[int]
    steps_per_ml: Optional[float]
    awaiting_cal: bool

    def __init__(self, port: str | None, poll_interval_ms: int = 500, baud: int = 19200, serial_timeout: float = 0.05):
        """
        Initialize the syringe pump driver.

        Args:
            port: Serial port (e.g. 'COM5')
            poll_interval_ms: How often to request STATUS from device.
            baud: Serial baud rate (firmware uses 19200 in provided code).
            serial_timeout: Timeout for serial read operations (seconds).
        """
        self.port               = port
        self.baud               = baud
        self.serial_timeout     = serial_timeout
        self.poll_interval_ms   = poll_interval_ms

        # Runtime / status values
        self.connected          = False
        self.state              = "Disconnected"
        self.position           = None
        self.moved_steps        = None
        self.moved_ml           = None
        self.target_steps       = None
        self.speed_steps_per_s  = None
        self.elapsed_ms         = None
        self.run_duration_ms    = None
        self.steps_per_ml       = None
        self.awaiting_cal       = False

        # Bookkeeping
        self.last_status_line: Optional[str] = None
        self.last_poll_time = 0.0
        self.last_error: Optional[str] = None

        # Threading primitives
        self.stop_event  = threading.Event()
        self.lock        = threading.Lock()
        self.ser: Optional[serial.Serial] = None
        self.status_thread: Optional[threading.Thread] = None

        if self.port:
            self.connect(self.port)


    # ---------------- Connection Management ----------------
    def connect(self, port: Optional[str] = None):
        if port:
            self.port = port
        if self.connected:
            return
        try:
            self.state      = "Connecting"
            self.ser        = serial.Serial(self.port, self.baud, timeout=self.serial_timeout)


            # Perform handshake (multiple pings)
            if not self._handshake(attempts=5, per_attempt_timeout=0.5):
                # Handshake failed
                self.state = "Disconnected"
                self.last_error = "Handshake failed (no STATUS response)"
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                return

            self.connected  = True
            self.last_error = None

            # Start polling thread
            self.stop_event.clear()
            self.status_thread = threading.Thread(target=self._status_loop, daemon=True, name="SyringePumpPoll")
            self.status_thread.start()
        except Exception as e:
            self.last_error = f"Connect failed: {e}"
            self.connected = False
            self.ser = None

    def disconnect(self):
        if not self.connected:
            return
        self.state      = "Disconnected"
        self.connected  = False

        self.stop_event.set()
        if self.status_thread and self.status_thread.is_alive():
            self.status_thread.join(timeout=1.5)
        self.status_thread = None
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    # ---------------- Polling Thread ----------------
    def _status_loop(self):
        """Continuously polls STATUS and reads any incoming lines."""
        while not self.stop_event.is_set():
            if not self.connected or not self.ser:
                time.sleep(0.5)
                continue

            now = time.time() * 1000.0
            if now - self.last_poll_time >= self.poll_interval_ms:
                self._send_raw("STATUS")  # ask for a fresh line
                self.last_poll_time = now

            # Read any available lines (non-blocking due to timeout)
            try:
                self._read_available_lines()
            except Exception as e:
                self.last_error = f"Read error: {e}"
                # If serial failures persist we could attempt reconnect; for now just wait
                time.sleep(0.2)
            # Sleep a short amount to avoid a tight loop
            time.sleep(0.01)

    def send_command(self, cmd: str): 
        """Public API to send a command line to the device."""
        self._send_raw(cmd)

    def _send_raw(self, cmd: str):
        if not self.ser or not self.ser.is_open:
            return
        try:
            line = (cmd.strip() + "\n").encode()
            self.ser.write(line)
        except Exception as e:
            self.last_error = f"Write error: {e}"

    def start_dose(self, volume, duration):
        if self.connected:
            self.send_command(f"DO {volume} {duration}")
    
    def get_status(self):
        self.send_command("STATUS")


    # ---------------- Handshake / Ping ----------------
    def ping(self, timeout: float = 0.5) -> bool:
        """
        Send a STATUS and wait up to `timeout` seconds for a new status line.
        Returns True if a fresh status line was parsed.
        """
        if not self.ser or not self.ser.is_open:
            return False
        start_line = self.last_status_line
        start_time = time.time()
        self._send_raw("STATUS")
        while time.time() - start_time < timeout:
            self._read_available_lines()
            if self.last_status_line and self.last_status_line != start_line:
                return True
            time.sleep(0.02)
        return False

    def _handshake(self, attempts: int = 3, per_attempt_timeout: float = 0.5) -> bool:
        """
        Try multiple pings to confirm the device responds.
        """
        for _ in range(attempts):
            if self.ping(timeout=per_attempt_timeout):
                return True
        return False

    # ---------------- Reading & Parsing ----------------
    def _read_available_lines(self):
        """Read all currently available newline-terminated lines from serial."""
        if not self.ser:
            return
        # Loop until no more characters buffered
        while True:
            try:
                raw = self.ser.readline()  # uses timeout set in serial
            except Exception as e:
                raise e
            if not raw:
                break  # no (more) data
            try:
                line = raw.decode(errors='replace').strip()
            except Exception:
                continue
            if not line:
                continue
            # The firmware only emits status lines when asked; ignore other chatter
            if line.startswith("State:"):
                with self.lock:
                    self.last_status_line = line
                self._parse_status_line(line)

    # ---------------- Status Parsing ----------------
    def _parse_status_line(self, line: str):
        """Parse a status line from the firmware and update attributes."""
        try:
            parts = [p.strip() for p in line.split('|')]
            if not parts:
                return
            if parts[0].startswith('State:'):
                self.state = parts[0].split(':', 1)[1].strip()

            for p in parts[1:]:
                if '=' in p:
                    k, v = p.split('=', 1)
                    k = k.strip(); v = v.strip()
                    try:
                        if k == 'stepsPerMl':
                            self.steps_per_ml = float(v)
                        elif k == 'pos':
                            self.position = int(v)
                        elif k == 'movedSteps':
                            self.moved_steps = int(v)
                        elif k == 'movedMl':
                            self.moved_ml = float(v)
                        elif k == 'targetSteps':
                            self.target_steps = int(v)
                        elif k == 'speedStepsPerS':
                            self.speed_steps_per_s = float(v)
                        elif k == 'elapsedMs':
                            self.elapsed_ms = int(v)
                        elif k == 'runDurationMs':
                            self.run_duration_ms = int(v)
                    except Exception:
                        # Ignore parse failures for individual fields
                        pass
                if 'Awaiting CAL_ACTUAL' in p:
                    self.awaiting_cal = True
            if 'Awaiting CAL_ACTUAL' not in line:
                self.awaiting_cal = False
        except Exception:
            pass

    # ---------------- Helpers ----------------
    def snapshot(self) -> Dict[str, Any]:
        """Return a thread-safe copy of the current device status."""
        with self.lock:
            return {
                'connected': self.connected,
                'state': self.state,
                'position': self.position,
                'moved_steps': self.moved_steps,
                'moved_ml': self.moved_ml,
                'target_steps': self.target_steps,
                'speed_steps_per_s': self.speed_steps_per_s,
                'elapsed_ms': self.elapsed_ms,
                'run_duration_ms': self.run_duration_ms,
                'steps_per_ml': self.steps_per_ml,
                'awaiting_cal': self.awaiting_cal,
                'last_status_line': self.last_status_line,
                'last_error': self.last_error,
            }

    # Context manager support
    def close(self):  # alias
        self.disconnect()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()

