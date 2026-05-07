import os
import pathlib
import sys
import glob
import time
from copy import deepcopy

_drivers_dir = str(pathlib.Path(__file__).parent.resolve())
_dll_dir = os.path.join(_drivers_dir, 'Thorlabs_DotNet_dll', '')
if _dll_dir.upper() not in [p.upper() for p in sys.path]:
    sys.path.insert(0, _dll_dir)
sys.path.extend(glob.glob(f'{_drivers_dir}/*/**/', recursive=True))

import pythonnet  # noqa: F401 — required before clr
import clr
clr.AddReference('System')
from System import Text, UInt32, IntPtr  # noqa: E402


class ThorlabsPowerMeter:

    _dll_name = 'Thorlabs.TLPM_64.Interop'
    _default_dll_path: str = os.path.join(os.path.dirname(__file__), 'Thorlabs_DotNet_dll', '')

    # Class-level device inventory populated by list_devices()
    TLPM = None
    resourceName: list = []
    modelName: list = []
    serialNumber: list = []
    manufacturer: list = []

    def __init__(self):
        self.connected = False
        self.resourceNameConnected = None
        self.sensorName = None
        self.sensorType = None
        self.meterPowerReading = None
        self.meterPowerUnit = None

    @classmethod
    def list_devices(cls, library_path: str = None) -> 'ThorlabsPowerMeter':
        if library_path is None:
            library_path = cls._default_dll_path
        if library_path.upper() not in [p.upper() for p in sys.path]:
            sys.path.insert(0, library_path)

        try:
            clr.AddReference(cls._dll_name)
            from Thorlabs.TLPM_64.Interop import TLPM
            cls.TLPM = TLPM
        except Exception:
            pass

        cls.resourceName = []
        cls.modelName = []
        cls.serialNumber = []
        cls.manufacturer = []

        try:
            desc = [Text.StringBuilder(2048) for _ in range(4)]
            tmp = cls.TLPM(IntPtr(0))
            _, count = tmp.findRsrc()
            for i in range(count):
                tmp.getRsrcName(UInt32(i), desc[0])
                tmp.getRsrcInfo(UInt32(i), desc[1], desc[2], desc[3])
                cls.resourceName.append(deepcopy(desc[0].ToString()))
                cls.modelName.append(deepcopy(desc[1].ToString()))
                cls.serialNumber.append(deepcopy(desc[2].ToString()))
                cls.manufacturer.append(deepcopy(desc[3].ToString()))
            tmp.Dispose()
        except Exception:
            pass

        return cls()

    def connect(self, resource_name: str, force: bool = False) -> 'ThorlabsPowerMeter':
        if self.connected and not force:
            return self
        try:
            copy = deepcopy(self)
            copy.TLPM = self.TLPM
            copy.deviceNET = copy.TLPM(resource_name, True, True)
            idx = self.resourceName.index(resource_name)
            copy.resourceNameConnected = resource_name
            copy.resourceName = self.resourceName[idx]
            copy.modelName = self.modelName[idx]
            copy.serialNumber = self.serialNumber[idx]
            copy.manufacturer = self.manufacturer[idx]
            copy.connected = True
            copy._init_sensor()
            return copy
        except Exception:
            return self

    def disconnect(self):
        if self.connected:
            try:
                self.deviceNET.Dispose()
            except Exception:
                pass
            self.connected = False

    def get_state(self) -> dict:
        return {
            "connected": self.connected,
            "sensor_name": self.sensorName,
            "sensor_type": self.sensorType,
            "power_w": self.meterPowerReading,
            "power_unit": self.meterPowerUnit,
        }

    def set_settings(self, **kwargs):
        if not self.connected:
            return
        try:
            if "wavelength" in kwargs:
                wl = float(kwargs["wavelength"])
                _, wl_min = self.deviceNET.getWavelength(1)
                _, wl_max = self.deviceNET.getWavelength(2)
                self.deviceNET.setWavelength(max(wl_min, min(wl_max, wl)))
            if "brightness" in kwargs:
                self.deviceNET.setDispBrightness(max(0.0, min(1.0, float(kwargs["brightness"]))))
            if "attenuation" in kwargs:
                att = float(kwargs["attenuation"])
                _, att_min = self.deviceNET.getAttenuation(1)
                _, att_max = self.deviceNET.getAttenuation(2)
                self.deviceNET.setAttenuation(max(att_min, min(att_max, att)))
            if "auto_range" in kwargs:
                self.deviceNET.setPowerAutoRange(bool(kwargs["auto_range"]))
            if "average_time" in kwargs:
                at = float(kwargs["average_time"])
                _, at_min = self.deviceNET.getAvgTime(1)
                _, at_max = self.deviceNET.getAvgTime(2)
                self.deviceNET.setAvgTime(max(at_min, min(at_max, at)))
            if "timeout" in kwargs:
                self.deviceNET.setTimeoutValue(int(kwargs["timeout"]))
        except Exception:
            pass

    def read_power(self) -> tuple:
        _, self.meterPowerReading = self.deviceNET.measPower()
        _, unit_code = self.deviceNET.getPowerUnit()
        self.meterPowerUnit = "W" if unit_code == 0 else "dBm"
        return self.meterPowerReading, self.meterPowerUnit

    # --- Internal ---

    def _init_sensor(self):
        try:
            desc = [Text.StringBuilder(1024) for _ in range(3)]
            _, _type, _, _ = self.deviceNET.getSensorInfo(desc[0], desc[1], desc[2])
            self.sensorName = desc[0].ToString()
            self.sensorType = {
                0x00: "No sensor", 0x01: "Photodiode",
                0x02: "Thermopile", 0x03: "Pyroelectric",
            }.get(_type, "Unknown")
        except Exception:
            pass
