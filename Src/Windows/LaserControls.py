import time
import Drivers.LaserDriver as LaserDriverModule
import dearpygui.dearpygui as dpg

class LaserControls:

    laser               = LaserDriverModule.LaserDriver()
    calibrating_mode    = False

    def __init__(self):

        with dpg.window(
            label                = "Laser Controls",
            tag                  = "#LaserControls",
            width                = 250,
            height               = 100,
            pos                  = (1015, 850),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):
            self.window_id  = dpg.last_item()
            
            with dpg.group(horizontal=True):
                # self.laser_power_group_id = dpg.add_group(horizontal=True)
                
                self.laser_indicator_id = dpg.add_color_button(
                    label           = "",
                    width           = 19,
                    height          = 19,
                    callback        = None,
                    tag             = "laser_indicator"
                )
                
                self.laser_button_id = dpg.add_button(
                    label           = "Laser Off",
                    width           = -1,  
                    callback        = lambda: self.laser.set_laser_state(not self.laser.get_laser_state() ),
                )


            self.laser_power_id = dpg.add_slider_float(
                label           = "Power",
                default_value   = self.laser.get_laser_power(),
                min_value       = 0.0,
                max_value       = 1.0,
                format          = "%.2f%%", 
                callback        = self.request_laser_power,
                user_data       = None,
            )

            self.laser_actual_id = dpg.add_slider_float(
                label           = "Target",
                default_value   = self.laser.get_laser_power(),
                min_value       = 0.0,
                max_value       = 1.0,
                format          = "%.0f%%",  
                callback        = None,
            )

    # Set up a timer to call self.loop() every second
    def render(self):                
        # Check the state of the laser
        self.laser_on = self.laser.get_laser_state()
        dpg.set_item_label(self.laser_button_id, "Laser On" if self.laser_on else "Laser Off")
        color = (0, 255, 0, 255) if self.laser_on else (10, 10, 10, 255)
        dpg.configure_item(self.laser_indicator_id, default_value=color)

        # Get the actual power from the laser driver
        dpg.set_value(self.laser_actual_id, self.laser.get_laser_power())
        


    def request_laser_power(self, sender, app_data, user_data):

        # Get the requested power from the slider
        requested_power = dpg.get_value(self.laser_power_id)
        self.laser.set_laser_power(requested_power)


    def checkLaserState(self):
        return self.laser_on