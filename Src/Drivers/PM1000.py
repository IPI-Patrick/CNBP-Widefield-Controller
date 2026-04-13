import pathlib
import sys

_drivers_dir = str(pathlib.Path(__file__).parent.resolve())
if _drivers_dir not in sys.path:
    sys.path.insert(0, _drivers_dir)

from Drivers.ThorlabsPowerMeter import ThorlabsPowerMeter
import time
import threading
from collections import deque


class PM1000:

    def __init__(self, wavelength=635, average_time=0.001, timeout=1000,
                 brightness=0.3, attenuation=0, auto_range=True,
                 history_maxlen=1000, reading_interval=0.1):
        self._device_list = None
        self._device = None
        self.logger = None

        self.wavelength = wavelength
        self.average_time = average_time
        self.timeout = timeout
        self.brightness = brightness
        self.attenuation = attenuation
        self.auto_range = auto_range
        self.reading_interval = reading_interval

        self.power_reading = None
        self.power_unit = None
        self.power_history = deque(maxlen=history_maxlen)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._device_names = []

    def list_devices(self):
        self._device_list = ThorlabsPowerMeter.listDevices()
        self.logger = self._device_list.logger
        self._device_names = list(self._device_list.resourceName)
        return self._device_names

    def get_device_names(self):
        return list(self._device_names)

    def get_latest_reading(self):
        with self._lock:
            return self.power_reading, self.power_unit

    def connect(self, resource_index=0):
        if self._device_list is None:
            self.list_devices()
        resource = self._device_list.resourceName[resource_index]
        self._device = self._device_list.connect(resource)
        self._device.getSensorInfo()
        self._device.setWaveLength(self.wavelength)
        self._device.setDispBrightness(self.brightness)
        self._device.setAttenuation(self.attenuation)
        self._device.setPowerAutoRange(self.auto_range)
        self._device.setAverageTime(self.average_time)
        self._device.setTimeoutValue(self.timeout)

    def read_power(self):
        self._device.updatePowerReading(0)
        reading = self._device.meterPowerReading
        unit = self._device.meterPowerUnit
        with self._lock:
            self.power_reading = reading
            self.power_unit = unit
            self.power_history.append((time.time(), reading, unit))
        return reading, unit

    def _continuous_loop(self):
        while not self._stop_event.is_set():
            try:
                self.read_power()
            except Exception as e:
                self.logger.error(f'|PM1000| Error reading power: {e}')
            self._stop_event.wait(self.reading_interval)

    def start_continuous(self):
        if self._thread is not None and self._thread.is_alive():
            self.logger.warning('|PM1000| Continuous reading already running.')
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._continuous_loop, daemon=True)
        self._thread.start()
        self.logger.info('|PM1000| Continuous reading started.')

    def stop_continuous(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.logger.info('|PM1000| Continuous reading stopped.')

    def disconnect(self):
        if self._thread is not None and self._thread.is_alive():
            self.stop_continuous()
        if self._device is not None:
            self._device.disconnect()
            self._device = None


if __name__ == '__main__':
    pm = PM1000()
    pm.connect()

    # Single reading
    power, power_unit = pm.read_power()
    pm.logger.info(f'|PM1000| Single reading: {power} {power_unit}')

    # Continuous reading for 10 seconds
    pm.start_continuous()
    time.sleep(10)
    pm.stop_continuous()

    last_reading, last_unit = pm.get_latest_reading()
    pm.logger.info(f'|PM1000| Last reading: {last_reading} {last_unit}')
    pm.logger.info(f'|PM1000| History length: {len(pm.power_history)}')

    pm.disconnect()
