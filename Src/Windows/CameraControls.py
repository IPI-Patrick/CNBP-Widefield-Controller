import os  
import shutil
import psutil
import time
import numpy as np
import threading
import dearpygui.dearpygui as dpg
from Drivers.Andor import Andor
from Windows.SubWindows.CameraFeed import CameraFeedWindow
from Windows.SubWindows.GraphWindow import GraphWindow
from Utils.utils import scale
from Utils.themes import read_only_theme, red_green_button_disabled, red_green_button_enabled
from Utils.shared_state import shared_andor

class CameraSystem:    

    def __init__(self):

        with dpg.window(
            label                = "Camera Controls",
            tag                  = "#CameraControls",
            width                = 300,
            height               = 300,
            pos                  = (1015, 10),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):

            # STARTUP
            # ################################################################
            # Set up the window and thread lock
            self.window_id      = dpg.last_item()
            self.lock           = threading.Lock()

            # Set up the camera
            self.Andor          = Andor()
            self.camera         = self.Andor.camera
            cam                 = self.camera   
            self.started        = False

            # Set up the Preview Window
            self.camera_feed   = CameraFeedWindow(
                parent      = self.window_id,
                Andor       = self.Andor
            )

            self.mean_graph   = GraphWindow(
                name        = "Mean Intensity",
                id          = "MeanIntensityGraph",
                getYValues  = lambda: self.Andor.meanBuffer,
                getXValues  = lambda: range(len(self.Andor.meanBuffer)),
                xlabel      = "Acquisitions",
                ylabel      = "Mean Intensity",
                xpos        = 1015,
                ypos        = 325
            )

            dpg.add_text("Acquisition Settings")
            dpg.add_separator()

            self.settings_exposure_time = dpg.add_input_float(
                label           = "Exposure Time",
                width           = -110,
                default_value   = cam.ExposureTime,
                min_value       = 0.001,
                max_value       = 1,
                step            = 0.01,
                format          = "%.3f s",
                callback        = lambda: self.setprop("ExposureTime", self.settings_exposure_time)
            )

            
            self.settings_trigger_mode = dpg.add_combo(
                label           = "Trigger Mode",
                width           = -110,
                items           = cam.options_TriggerMode,
                default_value   = cam.TriggerMode,
                callback        = lambda: self.setprop("TriggerMode", self.settings_trigger_mode)
            )

            dpg.add_spacer(height=20)
            dpg.add_text("Frame Settings")
            dpg.add_separator()

            self.settings_pixel_binning = dpg.add_combo(
                label           = "Pixel Binning",
                width           = -110,
                items           = cam.options_AOIBinning,
                default_value   = cam.AOIBinning,
                callback        = lambda: self.setprop("AOIBinning", self.settings_pixel_binning)
            )

            self.settings_image_width = dpg.add_input_int(
                label           = "Width",
                width           = -110,
                default_value   = cam.AOIWidth,
                min_value       = 1,
                max_value       = cam.AOIWidth,
                callback        = lambda: self.setprop("AOIWidth", self.settings_image_width)
            )

            self.settings_image_height = dpg.add_input_int(
                label           = "Height",
                width           = -110,
                default_value   = cam.AOIHeight,
                min_value       = 1,
                max_value       = cam.AOIHeight,
                callback        = lambda: self.setprop("AOIHeight", self.settings_image_height),
            )

            self.settings_image_left = dpg.add_input_int(
                label           = "Left",
                width           = -110,
                default_value   = cam.AOILeft,
                min_value       = 1,
                max_value       = cam.AOILeft,
                callback        = lambda: self.setprop("AOILeft", self.settings_image_left),
            )

            self.settings_image_top = dpg.add_input_int(
                label           = "Top",
                width           = -110,
                default_value   = cam.AOITop,
                min_value       = 1,
                max_value       = cam.AOITop,
                callback        = lambda: self.setprop("AOITop", self.settings_image_top),
            )


            # Add the start/stop button
            self.start_button_id = dpg.add_button(
                label           = "Start Preview",
                width           = -1,  
                callback        = self.toggle_preview,
                tag             = "start_camera_button"
            )

    @property
    def settings(self):
        return [getattr(self, attr) for attr in vars(self) if attr.startswith('settings_')]

    def toggle_preview(self):
        if self.Andor.is_capturing:
            self.Andor.stop_capture()
            self.started = False
            dpg.configure_item(self.start_button_id, label="Start Preview")
            dpg.bind_item_theme(self.start_button_id, red_green_button_disabled)

        else:
            # Reset the camera feed texture size
            self.started = True
            self.camera_feed.reset_texture()
            self.Andor.start_capture_continuous()
            dpg.configure_item(self.start_button_id, label="Stop Preview")
            dpg.bind_item_theme(self.start_button_id, red_green_button_enabled)

    def setprop(self, prop, setting):
        setattr(self.camera, prop, dpg.get_value(setting))


    def render(self):
        # Disable the button if we are capturing something unrelated to experiment
        if self.Andor.is_capturing and not self.started:            
            dpg.configure_item(self.start_button_id, enabled=False)
        elif not self.Andor.is_capturing and not self.started:
            dpg.configure_item(self.start_button_id, enabled=True)

        # If capturing at all, disable the settings
        for setting in self.settings:
            if self.Andor.is_capturing:
                dpg.configure_item(setting, enabled=False)
                dpg.bind_item_theme(setting, read_only_theme)
            else:
                dpg.configure_item(setting, enabled=True)
                dpg.bind_item_theme(setting, None)