import serial
import serial.tools.list_ports
import threading
import time
import random
from typing import Optional


class pHSensor:

    def __init__(self):
        self._interval = 1.0
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._serial: Optional[serial.Serial] = None
        self._ph = 7.0
        self._history_values: list = []
        self._history_timestamps: list = []
        self._thread: Optional[threading.Thread] = None

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, value):
        self._interval = max(0.01, float(value))

    @staticmethod
    def list_ports() -> list:
        try:
            return [p.device for p in serial.tools.list_ports.comports()]
        except Exception:
            return []

    def connect(self, port: str, baudrate: int = 19200, timeout: float = 1.0) -> bool:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        try:
            self._serial = serial.Serial(port, baudrate, timeout=timeout)
            return True
        except Exception:
            self._serial = None
            return False

    def disconnect(self):
        self.stop()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def get_state(self) -> dict:
        with self._lock:
            return {
                "connected": self._serial is not None and self._serial.is_open,
                "current_ph": self._ph,
                "history_values": list(self._history_values),
                "history_timestamps": list(self._history_timestamps),
            }

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        with self._lock:
            self._history_values = []
            self._history_timestamps = []
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # --- Internal ---

    def _read(self) -> Optional[float]:
        # Mock random walk; uncomment serial block below when hardware is connected
        self._ph += 0.05 - 0.1 * random.random()
        self._ph = max(0.0, min(14.0, self._ph))
        return self._ph
        # self._serial.write(b'R\r\n')
        # line = self._serial.readline().decode('utf-8').strip()
        # return float(line) if line else None

    def _read_loop(self):
        while not self._stop_event.is_set():
            try:
                value = self._read()
                if value is not None:
                    with self._lock:
                        self._history_values.append(value)
                        self._history_timestamps.append(time.time())
            except Exception:
                pass
            self._stop_event.wait(self._interval)
