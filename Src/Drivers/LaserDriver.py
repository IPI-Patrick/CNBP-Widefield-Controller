import os 
import time
import random
from Utils.utils import scale, clamp

class LaserDriver():
    
    # Add the path to the driver here
    driver_path    = "C:/Program Files/"

    power           = 0.5
    state           = False

    def __init__(self):

        # Initialise the driver

        # Get the current state of the laser
        self.state  = False
        self.power  = 0.5


    
    def get_laser_state(self):
        """
        Check the state of the laser.
        Returns True if the laser is on, False otherwise.
        """

        # Placeholder for actual implementation
        return self.state
    
    def set_laser_state(self, state):
        """
        Set the state of the laser.
        :param state: True to turn on the laser, False to turn it off.
        """
        self.state = state

        # Placeholder for actual implementation
        return self.state


    def get_laser_power(self):
        """
        Get the current power of the laser.
        Returns a float representing the power in % of maximum.
        """
        
        # Scale the servo value to a percentage
        laser_power     = 0.5


        # Placeholder for actual implementation
        return laser_power




    def set_laser_power(self, power):
        """
        Set the power of the laser.
        :param power: Power in mW.
        """
        # Convert power from percentage to servo value
        power           = scale(power, 0, 1, 0, 255)
        
        # Placeholder for actual implementation
        pass    


    