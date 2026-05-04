import os  
import shutil
import psutil
import time
import numpy as np
import threading
import dearpygui.dearpygui as dpg
from pyparsing import deque
from Drivers.Andor import Andor
from Drivers.pHSensor import pHSensor
from Windows.SubWindows.CameraFeed import CameraFeedWindow
from Windows.SubWindows.GraphWindow import GraphWindow
from Windows.CameraControls import CameraSystem
from Utils.utils import scale
from Utils.themes import read_only_theme, red_green_button_disabled, red_green_button_enabled
from Utils.shared_state import class_objects

class pHExperiment:

    def __init__(self):
        with dpg.window(
            label                = "pH Sensing Experiment",
            tag                  = "#pHExperiment",
            width                = 300,
            height               = 340,
            pos                  = (1325, 10),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):
            
            # STARTUP
            # ################################################################
            # Set up the window and thread lock
            self.window_id      = dpg.last_item()
            self.lock           = threading.Lock()

            # Find the CameraSystem instance
            for obj in class_objects:
                if obj.__class__.__name__ == "CameraSystem":   
                    self.camera = obj
                    break
            
            # Set up the camera
            self.Andor          = self.camera.Andor
            self.camera_feed    = self.camera.camera_feed

            # Set up the pH Sensor
            self.pHSensor       = pHSensor(self.Andor)            

            # Set up experiment
            self.started        = False            
            self.finishing      = False

            with dpg.tree_node(label="Settings", default_open=True, span_full_width=True):
                self.settings_trigger_mode = dpg.add_combo(
                    label           = "COM port",
                    width           = -110,
                    items           = self.pHSensor.comports,
                    default_value   = self.pHSensor.comports[0] if self.pHSensor.comports else None,
                    callback        = self.on_comport_change
                )

                self.settings_num_frames = dpg.add_input_int(
                    label           = "Number of Frames",
                    width           = -110,
                    default_value   = self.Andor.max_acquisitions,
                    min_value       = 1,
                    max_value       = 10000,
                    step            = 1,
                    callback        = lambda: setattr(self.Andor, 'max_acquisitions', dpg.get_value(self.settings_num_frames))
                )

                self.settings_continuous = dpg.add_checkbox(
                    label           = "Continuous Acquisition",
                    default_value   = True,
                )

                self.settings_pH_interval = dpg.add_input_float(
                    label           = "pH Interval (s)",
                    width           = -110,
                    default_value   = 1,
                    min_value       = 0,
                    max_value       = 1000,
                    step            = 0.01,
                    callback        = lambda: setattr(self.pHSensor, 'interval', dpg.get_value(self.settings_pH_interval))
                )

            dpg.add_separator()

            with dpg.tree_node(label="Output", default_open=True, span_full_width=True):
                dpg.add_text("Directory")
                with dpg.group(horizontal=True):
                    self.file_dialog_id = dpg.add_file_dialog(
                        directory_selector=True, 
                        show=False, 
                        tag="file_dialog_id",
                        width=700, 
                        height=400
                    )

                    self.settings_output_dir = dpg.add_input_text(        
                        width = -1,       
                        default_value   = os.path.join(os.getcwd(), "Experiments"),
                    )

                    self.file_select_button = dpg.add_button(
                        width           = 20,
                        label           = "📁",  # Folder icon
                        callback        = lambda: dpg.show_item(self.file_dialog_id)
                    )

                dpg.add_text("File Name")
                self.settings_file_name = dpg.add_input_text(        
                    width = -1,       
                    default_value   = "pH_Experiment",
                )

            dpg.add_separator()

            # Add the start/stop button
            self.start_button_id = dpg.add_button(
                label           = "Start Experiment",
                width           = -1,  
                callback        = self.toggle_experiment,
                tag             = "start_experiment_button"
            )
    

    def toggle_experiment(self):

        # Don't do anything if in the process of saving data
        if self.finishing:
            return

        # If started, stop, if stopped, start
        if self.started:
            self.Andor.stop_capture()

        else:
            # Reset the camera feed texture size
            self.camera_feed.reset_texture()
            self.started = True
            
            # Start the pH sensor
            self.pHSensor.start()
            self.Andor.start_capture(continuous=dpg.get_value(self.settings_continuous), callback=self.finish_experiment)

    
    @property
    def settings(self):
        return [getattr(self, attr) for attr in vars(self) if attr.startswith('settings_')]

    @property
    def file_index(self):
        # Read the output directory and find the highest existing index. File format is <index>_<name>.npz
        outdir          = dpg.get_value(self.settings_output_dir)
        existing_files  = [f for f in os.listdir(outdir) if f.endswith('.npz')]
        indices         = [ int(f.split('_')[0]) for f in existing_files if f.split('_')[0].isdigit() ]
        return max(indices) + 1 if indices else 1
    

    def finish_experiment(self, _acq):
        print("Experiment finished")
        self.finishing = True

        # Stop the pH sensor
        self.pHSensor.stop()

        # Ensure the output directory exists
        outdir = dpg.get_value(self.settings_output_dir)
        if not os.path.exists(outdir):
            os.makedirs(outdir)

        # Get the pH buffer values
        pH_time         = self.pHSensor.timestamps
        pH_values       = self.pHSensor.buffer
        pH_frames       = self.pHSensor.frameNums

        # Get the acquisition values
        acquisitions    = self.Andor.acquisitions  
        timestamps      = self.Andor.timestamps

        file_index     = self.file_index
        name           = dpg.get_value(self.settings_file_name)
        save_time      = time.strftime("%Y%m%d-%H%M%S")

        filename        = os.path.join(outdir, f"{file_index}_{name}.npz")

        np.savez_compressed(filename, 
            acquisitions = np.array(acquisitions), 
            timestamps   = np.array(timestamps),
            frames       = np.array(range(len(acquisitions))),
            pH_time      = np.array(pH_time),
            pH_values    = np.array(pH_values),
            pH_frames    = np.array(pH_frames),
        )

        # Change the button back
        self.started = False
        self.finishing = False

        print("Finished Saving")


    def on_comport_change(self, sender, app_data, user_data):
        with self.lock:
            self.pHSensor.connect(app_data)

    def render(self):
        # Disable the button if we are capturing something unrelated to experiment
        if self.Andor.is_capturing and not self.started:            
            dpg.configure_item(self.start_button_id, enabled=False)
        elif not self.Andor.is_capturing and not self.started:
            dpg.configure_item(self.start_button_id, enabled=True)

        if self.finishing:
            dpg.bind_item_theme(self.start_button_id, red_green_button_disabled)
            dpg.configure_item(self.start_button_id, label="Finishing Experiment", enabled=False)
        elif self.started:
            dpg.bind_item_theme(self.start_button_id, red_green_button_enabled)
            dpg.configure_item(self.start_button_id, label="Stop Experiment")
        else:
            dpg.bind_item_theme(self.start_button_id, red_green_button_disabled)
            dpg.configure_item(self.start_button_id, label="Start Experiment")

        # If capturing at all, disable the settings
        for setting in self.settings:
            if self.Andor.is_capturing:
                dpg.configure_item(setting, enabled=False)
                dpg.bind_item_theme(setting, read_only_theme)
            else:
                dpg.configure_item(setting, enabled=True)
                dpg.bind_item_theme(setting, None)
