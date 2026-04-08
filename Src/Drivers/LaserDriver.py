import time
from Utils.utils import clamp
import serial.tools.list_ports

class LaserDriver():

    def __init__(self):
        self.max_power_mw = 100.0
        self.COMPort = self.find_laser_com_port()
        self.available_ports = []
        self.connected = self.COMPort is not None
        self.state = False
        self.target_power_mw = 25.0
        self.actual_power_mw = 0.0
        self._last_update_time = time.monotonic()
        self.refresh_ports()

    def list_com_ports(self):
        """
        List available COM ports.
        Returns a list of serial.tools.list_ports_common.ListPortInfo objects.
        """
        return serial.tools.list_ports.comports()

    def find_laser_com_port(self):
        """
        Scan available COM ports to find the laser controller.
        This is a placeholder implementation and should be replaced with actual logic to identify the correct port.
        """
        ports = self.list_com_ports()
        
        for port in ports:
            description = (port.description or "").lower()
            if "laser" in description:
                return port.device

        if ports:
            return ports[0].device
        return None

    def refresh_ports(self):
        ports = self.list_com_ports()
        self.available_ports = [port.device for port in ports]

        if self.COMPort not in self.available_ports:
            self.COMPort = self.available_ports[0] if self.available_ports else None
            self.connected = False
            self.state = False

        return self.available_ports

    def connect(self, port=None):
        if port is not None:
            self.COMPort = port

        self.refresh_ports()

        if self.COMPort is None:
            self.connected = False
            return False

        self.connected = True
        self._last_update_time = time.monotonic()
        return True

    def disconnect(self):
        self.connected = False
        self.state = False
        self._update_power_model(force_zero=True)
        return True

    def is_connected(self):
        return self.connected

    def _update_power_model(self, force_zero=False):
        now = time.monotonic()
        dt = max(now - self._last_update_time, 0.0)
        self._last_update_time = now

        if force_zero:
            target = 0.0
        else:
            target = self.target_power_mw if self.connected and self.state else 0.0

        ramp_rate_mw_per_second = 80.0
        max_step = ramp_rate_mw_per_second * dt

        if abs(target - self.actual_power_mw) <= max_step:
            self.actual_power_mw = target
        elif target > self.actual_power_mw:
            self.actual_power_mw += max_step
        else:
            self.actual_power_mw -= max_step

        self.actual_power_mw = clamp(self.actual_power_mw, 0.0, self.max_power_mw)
    
    
    def get_laser_state(self):
        """
        Check the state of the laser.
        Returns True if the laser is on, False otherwise.
        """
        self._update_power_model()
        return self.state
    
    def set_laser_state(self, state):
        """
        Set the state of the laser.
        :param state: True to turn on the laser, False to turn it off.
        """
        self.state = bool(state) and self.connected
        self._update_power_model()

        return self.state

    def get_target_power(self):
        return self.target_power_mw


    def get_laser_power(self):
        """
        Get the current measured power of the laser.
        Returns a float representing the power in mW.
        """
        self._update_power_model()
        return self.actual_power_mw




    def set_laser_power(self, power):
        """
        Set the power of the laser.
        :param power: Power in mW.
        """
        self.target_power_mw = clamp(float(power), 0.0, self.max_power_mw)
        self._update_power_model()
        return self.target_power_mw

    def get_status(self):
        return {
            "port": self.COMPort or "Not Found",
            "connected": self.is_connected(),
            "emission_enabled": self.get_laser_state(),
            "target_power_mw": self.get_target_power(),
            "actual_power_mw": self.get_laser_power(),
        }


    