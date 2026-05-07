import pathlib
import sys
import time
import threading
from collections import deque

_drivers_dir = str(pathlib.Path(__file__).parent.resolve())
if _drivers_dir not in sys.path:
    sys.path.insert(0, _drivers_dir)

from Drivers.ThorlabsPowerMeter import ThorlabsPowerMeter


class PM1000:

    def __init__(self, wavelength=635, average_time=0.001, timeout=1000,
                 brightness=0.3, attenuation=0, auto_range=True,
                 history_maxlen=1000, reading_interval=0.1):
        self._wavelength = wavelength
        self._average_time = average_time
        self._timeout = timeout
        self._brightness = brightness
        self._attenuation = attenuation
        self._auto_range = auto_range
        self._reading_interval = reading_interval

        self._device_list = None
        self._device = None
        self._device_names = []
        self._power_reading = None
        self._power_unit = None
        self._power_history = deque(maxlen=history_maxlen)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    def list_devices(self) -> list:
        self._device_list = ThorlabsPowerMeter.list_devices()
        self._device_names = list(self._device_list.resourceName)
        return self._device_names

    def get_device_names(self) -> list:
        return list(self._device_names)

    def connect(self, resource_index=0):
        if self._device_list is None:
            self.list_devices()
        resource = self._device_list.resourceName[resource_index]
        self._device = self._device_list.connect(resource)
        self._device.set_settings(
            wavelength=self._wavelength,
            brightness=self._brightness,
            attenuation=self._attenuation,
            auto_range=self._auto_range,
            average_time=self._average_time,
            timeout=self._timeout,
        )

    def disconnect(self):
        self.stop()
        if self._device is not None:
            self._device.disconnect()
            self._device = None

    def get_state(self) -> dict:
        with self._lock:
            return {
                "connected": self._device is not None,
                "reading": self._power_reading,
                "unit": self._power_unit,
            }

    def read_power(self):
        reading, unit = self._device.read_power()
        with self._lock:
            self._power_reading = reading
            self._power_unit = unit
            self._power_history.append((time.time(), reading, unit))
        return reading, unit

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _read_loop(self):
        while not self._stop_event.is_set():
            try:
                self.read_power()
            except Exception as e:
                print(f"PM1000 read error: {e}")
            self._stop_event.wait(self._reading_interval)
