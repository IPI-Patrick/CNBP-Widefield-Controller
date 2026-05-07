import time
import serial.tools.list_ports
from Utils.utils import clamp


class LaserDriver:

    def __init__(self):
        self.max_power_mw = 150.0
        self.COMPort = None
        self.connected = False
        self._emission_on = False
        self._target_power_mw = 25.0
        self._actual_power_mw = 0.0
        self._last_update_time = time.monotonic()

    @staticmethod
    def list_ports() -> list:
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port=None) -> bool:
        if port is not None:
            self.COMPort = port
        if self.COMPort is None:
            self.connected = False
            return False
        self.connected = True
        self._last_update_time = time.monotonic()
        return True

    def disconnect(self):
        self.connected = False
        self._emission_on = False
        self._update_power_model(force_zero=True)

    def get_state(self) -> dict:
        self._update_power_model()
        return {
            "connected": self.connected,
            "emission_on": self._emission_on,
            "target_power_mw": self._target_power_mw,
            "actual_power_mw": self._actual_power_mw,
        }

    def set_emission(self, enabled: bool):
        self._emission_on = bool(enabled) and self.connected
        self._update_power_model()

    def set_power(self, power_mw: float):
        self._target_power_mw = clamp(float(power_mw), 0.0, self.max_power_mw)
        self._update_power_model()

    def _update_power_model(self, force_zero=False):
        now = time.monotonic()
        dt = max(now - self._last_update_time, 0.0)
        self._last_update_time = now
        target = 0.0 if force_zero else (self._target_power_mw if self.connected and self._emission_on else 0.0)
        max_step = 80.0 * dt
        if abs(target - self._actual_power_mw) <= max_step:
            self._actual_power_mw = target
        elif target > self._actual_power_mw:
            self._actual_power_mw += max_step
        else:
            self._actual_power_mw -= max_step
        self._actual_power_mw = clamp(self._actual_power_mw, 0.0, self.max_power_mw)
