import os 
import time
import random
from Utils.utils import scale, clamp

class LaserDriver():
    
    default_spinapi_path    = "C:/Program Files/SpinCore Technologies/SpinAPI"


    # Values sent to the servo to control the laser power
    min_servo_value         = 150
    max_servo_value         = 790


    def __init__(self, spin_api_path = None ):
        
        # Values for mocking the laser functionality
        self._starter_power           = 540
        self._mock_servo_value        = self._starter_power
        self._mock_laser_state        = False 
        self._interp_state = {
            'last_target_time':     time.time(),
            'target_power':         self._starter_power,
            'start_power':          self._starter_power,
            'start_time':           time.time(),
            'interp_duration':      3
        }

        if spin_api_path is None:
            spin_api_path = self.default_spinapi_path
        else:
            spin_api_path = os.path.abspath(spin_api_path)

    
    def get_laser_state(self):
        """
        Check the state of the laser.
        Returns True if the laser is on, False otherwise.
        """

        # Placeholder for actual implementation
        return self._mock_laser_state
    
    def set_laser_state(self, state):
        """
        Set the state of the laser.
        :param state: True to turn on the laser, False to turn it off.
        """
        self._mock_laser_state = state

        # Placeholder for actual implementation
        return self._mock_laser_state


    # Mock function to simulate servo movement
    def mock_servo_movement(self):
        
        now      = time.time()
        state    = self._interp_state

        # Interpolate towards the target power
        elapsed     = now - state['start_time']
        duration    = state['interp_duration']
        if elapsed < duration:
            t   = elapsed / duration
            self._mock_servo_value = (
            state['start_power'] + (state['target_power'] - state['start_power']) * t )
        else:
            self._mock_servo_value = state['target_power']



    def get_laser_power(self):
        """
        Get the current power of the laser.
        Returns a float representing the power in % of maximum.
        """
        
        # Scale the servo value to a percentage
        servo_value     = self.get_servo_position()

        laser_power     = clamp(scale(servo_value, self.min_servo_value, self.max_servo_value, 0, 100), 0, 100)


        # Placeholder for actual implementation
        return laser_power




    def set_laser_power(self, power):
        """
        Set the power of the laser.
        :param power: Power in mW.
        """
        # Convert power from percentage to servo value
        power           = scale(power, 0, 100, self.min_servo_value, self.max_servo_value)

        now             = time.time()

        self._interp_state['last_target_time'] = now
        self._interp_state['target_power']     = power
        self._interp_state['start_power']      = self._mock_servo_value
        self._interp_state['start_time']       = now
        self._interp_state['interp_duration']  = 3

        # Placeholder for actual implementation
        pass

    


    def get_servo_position(self):
        """
        Get the current servo value.
        This function should be implemented to return the actual servo value.
        """
        
        return self._mock_servo_value
    



    def set_servo_position(self, value):
        """
        Set the servo position.
        This function should be implemented to set the actual servo position.
        """

        now             = time.time()

        self._interp_state['last_target_time'] = now
        self._interp_state['target_power']     = value
        self._interp_state['start_power']      = self._mock_servo_value
        self._interp_state['start_time']       = now
        self._interp_state['interp_duration']  = 3

        pass




    def calibrate_laser(self, type: 'min'  'max'):
        """
        Calibrate the laser.
        This function should be implemented to perform the actual calibration.
        """
        
        # Get the current servo position
        servo_position      = self.get_servo_position()

        print(f"Calibrating Laser {type} = {servo_position}") 

        # Set the servo position of the min/max power
        if type == 'min':
            self.min_servo_value = servo_position
        else:
            self.max_servo_value = servo_position

        pass
    