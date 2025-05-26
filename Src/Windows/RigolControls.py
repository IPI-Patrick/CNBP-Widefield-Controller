import pyvisa
import time
import numpy as np
from Utils.themes import selected_theme, default_theme, red_green_button_disabled, red_green_button_enabled, disabled_theme
import dearpygui.dearpygui as dpg
# from Drivers.RigolInstruments import RigolDG4162
from Mocks.RigolMock import RigolDG4162
import threading

class RigolControls:

    current_waveform            = "DC"
    safe_min_voltage            = 0.0
    safe_max_voltage            = 1.25
    enabled                     = False

    rigol                       = None
    rm                          = pyvisa.ResourceManager('C:\\Windows\\System32\\visa32.dll')
    ilist                       = []    
    selected_resource           = None
    
    starting_height             = 630

    num_plot_points             = 5000
    plot_time                   = 10
    last_pushed_time            = time.time_ns()
    start_time                  = time.time()

    def __init__(self):
        
        self.time_axis          = [ time.time() - self.start_time ] * self.num_plot_points
        self.voltage_axis       = np.zeros(self.num_plot_points)    
        self.points_per_second  = self.num_plot_points / self.plot_time

        with dpg.window(
            label                = "Rigol Controls",
            tag                  = "#RigolControls",
            width                = 250,
            height               = self.starting_height,
            pos                  = (1325, 350),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):

            # STARTUP
            # ################################################################
            self.window_id      = dpg.last_item()


            # with dpg.item_handler_registry(tag="#rigol_resize_handler"):
            #     dpg.add_item_resize_handler( callback=self._on_window_resize )
            #     dpg.bind_item_handler_registry(self.window_id, "#rigol_resize_handler")

            self.ilist              = self.rm.list_resources()
            self.selected_resource  = self.ilist[0] if len(self.ilist) > 0 else None            

            # DRAW
            # ################################################################
            dpg.add_text("Connection")
            dpg.add_separator()

            dpg.add_combo(
                items           = self.ilist,
                default_value   = self.selected_resource,
                label           = "VISA",
                width           = -35,
                callback        = self.resource_selector_callback,
                tag             = "#visa_resource_combo"
            )

            with dpg.group(horizontal=True):

                self.connection_indictor = dpg.add_color_button(
                    label           = "",
                    width           = 19,
                    height          = 19,
                    default_value   = (200, 0, 0),
                    callback        = None,
                )
                
                self.connection_button = dpg.add_button(
                    label           = "Connect",
                    width           = -1,
                    callback        = self.connect_callback,
                )

            # The stuff below here should only be enabled if a resource is selected
            with dpg.group():
                
                self.settings_group_id = dpg.last_item()
                
                if self.rigol == None:
                    dpg.bind_item_theme(self.settings_group_id, disabled_theme)
                    dpg.configure_item(self.settings_group_id, enabled=False)


                dpg.add_spacer(height=10)
                dpg.add_text("Current Voltage")
                dpg.add_separator()
                with dpg.table( header_row=False, width=-1 ):
                    dpg.add_table_column()

                    with dpg.table_row():
                        dpg.add_button (
                            label           = 0.0,
                            width           = -1,
                            height          = 50,
                            tag             = "#Voltage_Display"
                        )


                with dpg.plot(height=120, width=-1, no_frame=True):
                    dpg.add_plot_axis(
                        dpg.mvXAxis,
                        label="",
                        tag="#trace_x_axis",
                        no_tick_labels=True,
                        no_side_switch=True,
                    )

                    dpg.add_plot_axis(
                        dpg.mvYAxis,
                        label="",
                        tag="#trace_y_axis",
                        no_tick_labels=True,
                        no_side_switch=True,                    
                    )

                    self.trace_series = dpg.add_line_series(
                        x       = self.time_axis,
                        y       = self.voltage_axis,
                        parent  = "#trace_y_axis",
                    )
                with dpg.table(header_row=False, width=-1):
                    dpg.add_table_column()
                    dpg.add_table_column()
                    dpg.add_table_column()

                    with dpg.table_row():
                        dpg.add_text("Time")
                        dpg.add_text("Min")
                        dpg.add_text("Max")

                    dpg.add_table_row()
                    
                    with dpg.table_row():
                        dpg.add_input_int( default_value=self.plot_time,    min_value=0,   max_value=60 * 5, width=-1, tag="#plot_time", callback=self.update_plot, on_enter=True, step=0 )
                        dpg.add_input_int( default_value=-0.25,             min_value=-10, max_value=10,     width=-1, tag="#plot_min",  callback=self.update_plot, on_enter=True, step=0 )
                        dpg.add_input_int( default_value=0.25,              min_value=-10, max_value=10,     width=-1, tag="#plot_max",  callback=self.update_plot, on_enter=True, step=0 )

                dpg.add_checkbox( label="Auto Range", tag="#auto_range_checkbox", default_value=True, callback=self.update_plot, )

                dpg.add_spacer(height=10)
                dpg.add_text("Function")
                dpg.add_separator()

                with dpg.table(
                    tag             = "#rigol_table",
                    resizable       = False,
                    header_row      = False,
                    width           = -1,
                ):
                    
                    dpg.add_table_column()
                    dpg.add_table_column()
                    dpg.add_table_column()

                    with dpg.table_row():
                        dpg.add_button(tag="#DC_Button",     label="DC",      height=40, width=-1, callback=self.select_waveform, user_data=(False, "DC")     )
                        dpg.add_button(tag="#Sine_Button",   label="Sine",    height=40, width=-1, callback=self.select_waveform, user_data=(False, "Sine")   )
                        dpg.add_button(tag="#Square_Button", label="Square",  height=40, width=-1, callback=self.select_waveform, user_data=(False, "Square") )



                dpg.add_spacer(height=10)
                dpg.add_text("Controls")
                dpg.add_separator()

                self.controls_group_id = dpg.add_group(horizontal=False)
                
                # Add a spacer that expands to fill available space, pushing the button to the bottom
                self.bottom_spacer = dpg.add_spacer()
                self.enabled_button = dpg.add_button( 
                    label               = "Enable",
                    width               = -1,
                    height              = 50,
                    callback            = self.toggle_enabled,
                    user_data           = None,                
                )        


        
        # After the window is created, attempt to connect to the device
        self.connect_callback()

        # Set default state to DC
        self.select_waveform("#Sine_Button", None, (False, "Sine"))                 

        self.start_polling()

    # def _on_window_resize(self, sender, app_data):
    #     width, height = dpg.get_item_rect_size(self.window_id)


    def render(self):

        if dpg.get_frame_count() % 60 == 0:
            self.ilist = self.rm.list_resources()
            
        dpg.configure_item("#Voltage_Display", label="{:.4f} V".format(self.rigol.getVoltage(1)))

        self.enabled = self.rigol.getOutputState(1)
        dpg.configure_item(self.enabled_button, label="Disable" if self.enabled else "Enable")
        dpg.bind_item_theme(self.enabled_button, red_green_button_enabled if self.enabled else red_green_button_disabled)

        # Update the x-axis values
        dpg.set_value(self.trace_series, [self.time_axis, self.voltage_axis])

        # Update the x-axis limits
        now                 = time.time() - self.start_time
        dpg.set_axis_limits("#trace_x_axis", now - self.plot_time, now)

        if dpg.get_value("#auto_range_checkbox") == True:
            min_voltage         = np.min(self.voltage_axis)
            max_voltage         = np.max(self.voltage_axis)
            dpg.set_axis_limits("#trace_y_axis", min_voltage - np.abs(min_voltage) * 0.2, max_voltage + np.abs(max_voltage) * 0.2)

            
    
    def _poll_voltage(self):
        while True:
            
            if self.rigol is not None:
                self.rigol.updateVoltage()
                voltage                 = self.rigol.getVoltage(1)
                self.voltage_axis       = np.roll(self.voltage_axis, -1)
                self.voltage_axis[-1]   = voltage
                now                     = time.time() - self.start_time
                self.time_axis          = np.roll(self.time_axis, -1)
                self.time_axis[-1]      = now

            time.sleep(1 / self.points_per_second)

    def start_polling(self):

        if not hasattr(self, "_poll_thread_started"):
            self._poll_thread_started = True
            threading.Thread(target=self._poll_voltage, daemon=True).start()



    def update_plot(self):
        # Get the new values from the input fields
        self.plot_time       = dpg.get_value("#plot_time")

        # Update the plot with the new data
        dpg.set_value(self.trace_series, [self.time_axis, self.voltage_axis])


    def toggle_enabled(self):

        self.enabled    = not self.enabled

        if self.rigol != None:
            self.rigol.setOutputState(1, not self.rigol.getOutputState(1))

        dpg.configure_item(self.enabled_button, label="Disable" if self.enabled else "Enable")
        dpg.bind_item_theme(self.enabled_button, red_green_button_enabled if self.enabled else red_green_button_disabled)


    def connect_callback(self):
        print("Connecting to Rigol DG4162...")

        if self.rigol != None:
            self.rigol.close()
            self.rigol = None        

            dpg.configure_item(self.connection_indictor, default_value=(200, 0, 0))
            dpg.configure_item(self.connection_button, label="Connect")
            dpg.bind_item_theme(self.settings_group_id, disabled_theme)
            dpg.configure_item(self.settings_group_id, enabled=False)

            return
        
        try:     
            if self.selected_resource == None: 
                raise Exception("No VISA resource selected.")

            self.rigol = RigolDG4162(self.rm, self.selected_resource)
            
            dpg.configure_item(self.connection_indictor, default_value=(0, 200, 0))
            dpg.configure_item(self.connection_button, label="Disconnect")
            dpg.bind_item_theme(self.settings_group_id, default_theme)
            dpg.configure_item(self.settings_group_id, enabled=True)


            print("Connected to Rigol DG4162")
        except Exception as e:
            self.rigol = None
            
            dpg.configure_item(self.connection_indictor, default_value=(200, 0, 0))
            dpg.configure_item(self.connection_button, label="Connect")
            dpg.bind_item_theme(self.settings_group_id, disabled_theme)
            dpg.configure_item(self.settings_group_id, enabled=False)

            print("Failed to connect to Rigol DG4162")
            print(e)



    def resource_selector_callback(self, sender, app_data):
        self.selected_resource = app_data


    def select_waveform(self, sender, app_data, user_data ):
        state, name = user_data

        newState = not state        

        # Get all button tags
        button_tags = ["#DC_Button", "#Sine_Button", "#Square_Button"]
        for tag in button_tags:
            if tag != sender:
                dpg.bind_item_theme(tag, default_theme)
                dpg.set_item_user_data(tag, (False, dpg.get_item_label(tag)))

        # Only update this button if it is not the same as the sender
        if name != self.current_waveform or newState is True:
            self.current_waveform = name
            dpg.bind_item_theme(sender, selected_theme if newState is True else default_theme)
            dpg.set_item_user_data(sender, (newState, name))

            # Remove old controls (sliders) before adding new ones
            children = dpg.get_item_children(self.controls_group_id, 1)
            for child in children:
                dpg.delete_item(child)
            
            # Disable the output if enabled
            if self.enabled:
                self.toggle_enabled()

            # DC
            ###############################################
            if self.current_waveform == "DC":

                def DC_callback():
                    # self.rigol.setDC( dpg.get_value(sender) )
                    value   = round(dpg.get_value(self.DC_voltage_id), 2)
                    if self.rigol != None:
                        self.rigol.setDC(1, value)

                self.DC_voltage_id	= dpg.add_input_float(
                    label			    = "Offset",
                    default_value	    = 0.0,
                    min_value		    = self.safe_min_voltage,
                    max_value		    = self.safe_max_voltage,
                    format			    = "%.2f V",
                    callback		    = DC_callback,
                    parent			    = self.controls_group_id,
                )

                DC_callback()
                dpg.configure_item(self.window_id, height=self.starting_height + 0)

            # SINE WAVE
            ###############################################
            elif self.current_waveform == "Sine":                
                def sine_callback():
                    if self.rigol != None:
                        self.rigol.setSineWave( 1, 1/dpg.get_value(self.sine_period_id), dpg.get_value(self.sine_amplitude_id), dpg.get_value(self.sine_offset_id) )                   

                self.sine_period_id	= dpg.add_input_float(
                    label			        = "Period",
                    default_value	        = 1.0,
                    min_value		        = 0.0,
                    max_value		        = 100.0,
                    format			        = "%.2f s",
                    callback		        = sine_callback,
                    parent			        = self.controls_group_id
                )
                self.sine_amplitude_id	= dpg.add_input_float(
                    label			        = "Amplitude",
                    default_value	        = 0.2,
                    min_value		        = 0.0,
                    max_value		        = 5.0,
                    format			        = "%.2f V",
                    callback		        = sine_callback,
                    parent			        = self.controls_group_id
                )
                self.sine_offset_id		= dpg.add_input_float(
                    label			        = "Offset",
                    default_value	        = 0.0,
                    min_value		        = 0.0,
                    max_value		        = 5.0,
                    format			        = "%.2f V",
                    callback		        = sine_callback,
                    parent			        = self.controls_group_id
                )

                sine_callback()
                dpg.configure_item(self.window_id, height=self.starting_height + 50)

            # SQUARE WAVE
            ###############################################
            elif self.current_waveform == "Square":
                def square_callback():
                    if self.rigol != None:
                        self.rigol.setSquareWave( 1, dpg.get_value(self.max_value_id), dpg.get_value(self.min_value_id), dpg.get_value(self.square_period_id) )

                self.square_period_id = dpg.add_input_float(
                    label			    = "Period",
                    default_value	    = 1.0,
                    min_value		    = 0.0,
                    max_value		    = 100.0,
                    format			    = "%.2f s",
                    callback		    = square_callback,
                    parent			    = self.controls_group_id
                )

                self.max_value_id	= dpg.add_input_float(
                    label			    = "High V",
                    default_value	    = 0.1,
                    min_value		    = 0.0,
                    max_value		    = 5.0,
                    format			    = "%.2f V",
                    callback		    = square_callback,
                    parent			    = self.controls_group_id
                )

                self.min_value_id	= dpg.add_input_float(
                    label			    = "Low V",
                    default_value	    = -0.1,
                    min_value		    = 0.0,
                    max_value		    = 5.0,
                    format			    = "%.2f V",
                    callback		    = square_callback,
                    parent			    = self.controls_group_id
                )            

                square_callback()
                dpg.configure_item(self.window_id, height=self.starting_height + 50)
