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
            height               = 315,
            pos                  = (1325, 10),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):
            self.window_id  = dpg.last_item()

            # with dpg.item_handler_registry(tag="#resize_handler"):
            #     dpg.add_item_resize_handler( callback=self._on_window_resize )
            #     dpg.bind_item_handler_registry(self.window_id, "#resize_handler")
            
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
                max_value       = 100.0,
                format          = "%.2f%%", 
                callback        = self.request_laser_power,
                user_data       = None,
            )

            self.laser_actual_id = dpg.add_slider_float(
                label           = "Actual",
                default_value   = self.laser.get_laser_power(),
                min_value       = 0.0,
                max_value       = 100.0,
                format          = "%.0f%%",  
                callback        = None,
            )

            self.servo_actual_id = dpg.add_slider_int(
                label           = "Servo",
                default_value   = self.laser.get_servo_position(),
                min_value       = 0,
                max_value       = 1000,            
                callback        = None,
            )        

            dpg.add_spacer(height=20)
            dpg.add_text("Power Calibration")
            dpg.add_separator()

            with dpg.group(horizontal=True):

                self.calibrating_indicator_id = dpg.add_color_button(
                    label       = "",
                    width       = 19,
                    height      = 19,
                    callback    = None,
                )
                
                self.calibrating_button = dpg.add_button(
                    label       = "Calibrate",
                    width       = -1,
                    callback    = self.toggle_calibrating_mode,
                )

            self.servo_id = dpg.add_slider_int(
                label           = "Servo",
                default_value   = self.laser.get_servo_position(),
                min_value       = 0,
                max_value       = 1000,            
                parent          = self.window_id,
                callback        = self.request_servo_position,
                show            = True,
                enabled         = False,
            )

            
            with dpg.group(horizontal=True):

                self.min_button_id = dpg.add_button(
                    label       = "Min",
                    width       = 75,
                    callback    = lambda : self.laser.calibrate_laser("min"),
                    show        = True,
                    enabled     = False,
                )

                self.max_button_id = dpg.add_button(
                    label       = "Max",
                    width       = -1,
                    callback    = lambda : self.laser.calibrate_laser("max"),
                    show        = True,
                    enabled     = False,
            )



            # Input for spinApi folder path
            self.spin_api_path = self.laser.default_spinapi_path
        
            dpg.add_spacer(height=20)
            dpg.add_text("SpinAPI Path")
            dpg.add_separator()

            self.spin_api_input_id = dpg.add_input_text(
                label           = "",
                default_value   = self.spin_api_path,
                parent          = self.window_id,
                width           = -1,
                callback=self._on_spin_api_path_changed
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

        # Get the actual servo position from the laser driver
        dpg.set_value(self.servo_actual_id, self.laser.get_servo_position())


        self.laser.mock_servo_movement()            
        
    


    def toggle_calibrating_mode(self, sender, app_data=None, user_data=None):
        self.calibrating_mode = not self.calibrating_mode

        if self.calibrating_mode:
            dpg.configure_item(self.calibrating_button, label="Stop Calibrating")
            dpg.configure_item(self.calibrating_indicator_id, default_value=(0, 255, 0, 255))
            dpg.configure_item(self.min_button_id,  enabled=True)
            dpg.configure_item(self.max_button_id,  enabled=True)
            dpg.configure_item(self.servo_id,       enabled=True)
            dpg.configure_item(self.laser_power_id, enabled=False)

        else:
            dpg.configure_item(self.calibrating_button, label="Calibrate")
            dpg.configure_item(self.calibrating_indicator_id, default_value=(10, 10, 10, 255))
            dpg.configure_item(self.min_button_id,  enabled=False)
            dpg.configure_item(self.max_button_id,  enabled=False)
            dpg.configure_item(self.servo_id,       enabled=False)
            dpg.configure_item(self.laser_power_id, enabled=True)



    # def _on_window_resize(self, sender):
    #     # Get the new size of the window
    #     width, height = dpg.get_item_rect_size(self.window_id)

    #     # Resize the horizontal group to fit the new window size
    #     dpg.set_item_width(self.min_button_id, (width / 2) - 15)


    def _on_spin_api_path_changed(self, sender, app_data, user_data):
        self.spin_api_path = app_data
        self.laser.set_spinapi_path(self.spin_api_path)
        self.laser.reinitialize_spinapi()



    def request_laser_power(self, sender, app_data, user_data):

        # Get the requested power from the slider
        requested_power = dpg.get_value(self.laser_power_id)
        self.laser.set_laser_power(requested_power)

    def request_servo_position(self, sender, app_data, user_data):
        # Get the requested servo position from the slider
        requested_servo_position = dpg.get_value(self.servo_id)

        self.laser.set_servo_position(requested_servo_position)



    def checkLaserState(self):
        return self.laser_on