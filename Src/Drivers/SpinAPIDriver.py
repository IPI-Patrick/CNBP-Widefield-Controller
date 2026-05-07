import time
from Utils.utils import scale, clamp


class SpinAPIDriver:

    min_servo_value = 150
    max_servo_value = 790

    def __init__(self, spin_api_path=None):
        self._emission_on = False
        self._servo_value = 540.0
        self._interp = {
            "target": 540.0,
            "start": 540.0,
            "start_time": time.time(),
            "duration": 3.0,
        }

    def get_state(self) -> dict:
        self._update_servo()
        return {
            "emission_on": self._emission_on,
            "power_pct": clamp(
                scale(self._servo_value, self.min_servo_value, self.max_servo_value, 0, 100), 0, 100
            ),
            "servo_position": self._servo_value,
        }

    def set_emission(self, enabled: bool):
        self._emission_on = bool(enabled)

    def set_power(self, power_pct: float):
        target = scale(clamp(float(power_pct), 0, 100), 0, 100, self.min_servo_value, self.max_servo_value)
        self._start_interp(target)

    def set_servo(self, position: float):
        self._start_interp(float(position))

    # --- Internal ---

    def _start_interp(self, target: float):
        self._update_servo()
        now = time.time()
        self._interp = {
            "target": target,
            "start": self._servo_value,
            "start_time": now,
            "duration": 3.0,
        }

    def _update_servo(self):
        now = time.time()
        elapsed = now - self._interp["start_time"]
        duration = self._interp["duration"]
        if elapsed < duration:
            t = elapsed / duration
            self._servo_value = self._interp["start"] + (self._interp["target"] - self._interp["start"]) * t
        else:
            self._servo_value = self._interp["target"]
