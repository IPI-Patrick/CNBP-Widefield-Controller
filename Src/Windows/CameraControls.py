import os  
import shutil
import psutil
import time
import numpy as np
import Windows.SubWindows.CameraFeed as CameraFeed
import threading
import dearpygui.dearpygui as dpg
from Mocks.MockAndor import Andor
from Windows.SubWindows.VideoSettings import VideoSettings
from Utils.utils import scale
from Utils.themes import read_only_theme, red_green_button_disabled, red_green_button_enabled

class CameraSystem:

    andor           = Andor()
    imageArray      = np.zeros(andor.frame_width * andor.frame_height, dtype=np.uint16)


    frame_width     = andor.frame_width
    frame_height    = andor.frame_height

    latest_frame    = np.array([0.1, 0.0, 0.0, 1.0] * andor.frame_height * andor.frame_width)
    null_frame      = np.array([0.0, 0.0, 0.0, 1.0] * andor.frame_height * andor.frame_width)
    diff_frame      = null_frame.copy()
    zero_frame      = null_frame.copy()

    num_frames      = 10
    frames          = np.array([null_frame] * num_frames)

    window_width     = 300
    window_height    = 825

    frame_idx       = 0
    new_frame_avail = False
    last_frame_time = time.time()

    live            = False
    started         = False
    
    settings        = dict(
        acquisition_mode      = "Kinetics",
        exposure_time         = 0.1,
        trigger_mode          = "Internal",
        video_duration        = 60.0,
        frame_rate            = 10.0,
        num_frames            = 600,
        image_area            = "Full Frame",
        pixel_binning         = "1x1",
        image_width           = frame_width,
        image_height          = frame_height,
        image_left            = 0,
        image_top             = 0,
        pixel_readout_rate    = "100MHz",
        shutter_mode          = "Global",
        preAmpGain            = "16-bit",
        pixel_encoding        = "Mono16",
        noise_filter          = True,
        blemish_correction    = True,
        spool_to_disk         = False,
        spool_location        = os.path.realpath(os.path.join(os.getcwd(), "../Spool")),
        ram_estimate          = "1024 MB",        
    )    

    def __init__(self):
        with dpg.window(
            label                = "Camera Controls",
            tag                  = "#CameraControls",
            width                = self.window_width,
            height               = self.window_height,
            pos                  = (1015, 10),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):

            # STARTUP
            # ################################################################
            self.window_id      = dpg.last_item()
            self.lock           = threading.Lock()
            running             = False


            self.feed           = CameraFeed.CameraFeedWindow(self, self.latest_frame,  self.frame_width,  self.frame_height, "Camera Feed",       "CameraFeed", size=595, pos=(10, 10))
            self.zero           = CameraFeed.CameraFeedWindow(self, self.zero_frame,    self.frame_width,  self.frame_height, "Zero Frame",        "ZeroFrame" , size=250, pos=(10, 640))
            self.diff           = CameraFeed.CameraFeedWindow(self, self.diff_frame,    self.frame_width,  self.frame_height, "Difference Frame",  "DiffFrame" , size=250, pos=(270, 640))
            self.controls       = VideoSettings(self)

            # SETTINGS
            # ################################################################
            with dpg.group():
                self.settings_group = dpg.last_item()
                dpg.add_text("Acquisition Settings")
                dpg.add_separator()

                self.settings_acquisition_mode = dpg.add_combo(
                    label           = "Aquisition Mode",
                    width           = -110,
                    items           = ["Single Scan", "Kinetics"], 
                    default_value   = self.settings["acquisition_mode"],
                    callback        = self.update_settings,                    
                )

                self.settings_exposure_time = dpg.add_input_float(
                    label           = "Exposure Time",
                    width           = -110,
                    default_value   = self.settings["exposure_time"],
                    min_value       = 0.001,
                    max_value       = 1,
                    step            = 0.01,
                    format          = "%.3f s",
                    callback        = self.update_settings,
                    on_enter        = True
                )

                self.settings_trigger_mode = dpg.add_combo(
                    label           = "Trigger Mode",
                    width           = -110,
                    items           = ["Internal", "External"], 
                    default_value   = self.settings["trigger_mode"],
                    callback        = self.update_settings,
                )

                self.settings_video_duration = dpg.add_input_float(
                    label           = "Video Duration",
                    width           = -110,
                    default_value   = self.settings["video_duration"],
                    min_value       = 1.0,
                    step            = 0.1,
                    format          = "%.2f s",
                    callback        = self.update_settings,
                    on_enter        = True
                )

                self.settings_frame_rate = dpg.add_input_float(
                    label           = "Frame Rate",
                    width           = -110,
                    default_value   = self.settings["frame_rate"],
                    min_value       = 0.1,
                    max_value       = 100.0,
                    step            = 0.1,
                    format          = "%.6f FPS",
                    callback        = self.update_settings,
                    on_enter        = True
                )

                self.settings_num_frames = dpg.add_input_int(
                    label           = "Frames",
                    width           = -110,
                    default_value   = self.settings["num_frames"],
                    min_value       = 1,
                    max_value       = 1000,
                    callback        = self.update_settings,
                    on_enter        = True
                )

                dpg.add_spacer(height=20)
                dpg.add_text("Frame Settings")
                dpg.add_separator()

                self.settings_image_area = dpg.add_combo(
                    label           = "Image Area",
                    width           = -110,
                    items           = ["Full Frame", "Custom"], 
                    default_value   = self.settings["image_area"],
                    callback        = self.update_settings,
                )

                self.settings_pixel_binning = dpg.add_combo(
                    label           = "Pixel Binning",
                    width           = -110,
                    items           = ["1x1", "2x2", "4x4"],
                    default_value   = self.settings["pixel_binning"],
                    callback        = self.update_settings,
                )

                self.settings_image_width = dpg.add_input_int(
                    label           = "Width",
                    width           = -110,
                    default_value   = self.settings["image_width"],
                    min_value       = 1,
                    max_value       = self.settings["image_width"],
                    enabled         = False,  # Disable this input if Full Frame is selected
                    callback        = self.update_settings,
                    on_enter        = True
                )

                self.settings_image_height = dpg.add_input_int(
                    label           = "Height",
                    width           = -110,
                    default_value   = self.settings["image_height"],
                    min_value       = 1,
                    max_value       = self.settings["image_height"],
                    enabled         = False,  # Disable this input if Full Frame is selected
                    callback        = self.update_settings,
                    on_enter        = True
                )

                self.settings_image_left = dpg.add_input_int(
                    label           = "Left",
                    width           = -110,
                    default_value   = self.settings["image_left"],
                    min_value       = 1,
                    max_value       = self.settings["image_left"],
                    enabled         = False,  # Disable this input if Full Frame is selected
                    callback        = self.update_settings,
                    on_enter        = True
                )

                self.settings_image_top = dpg.add_input_int(
                    label           = "Top",
                    width           = -110,
                    default_value   = self.settings["image_top"],
                    min_value       = 1,
                    max_value       = self.settings["image_top"],
                    enabled         = False,  # Disable this input if Full Frame is selected
                    callback        = self.update_settings,
                    on_enter        = True
                )

                dpg.add_spacer(height=20)
                dpg.add_text("Readout Settings")
                dpg.add_separator()

                self.settings_pixel_readout_rate = dpg.add_combo(
                    label           = "Readout Rate",
                    width           = -110,
                    items           = ["100MHz", "280MHz"], 
                    default_value   = self.settings["pixel_readout_rate"],
                    callback        = self.update_settings,
                )

                self.settings_shutter_mode = dpg.add_combo(
                    label           = "Shutter Mode",
                    width           = -110,
                    items           = ["Rolling", "Global"], 
                    default_value   = self.settings["shutter_mode"],
                    callback        = self.update_settings,
                )

                self.settings_preAmpGain = dpg.add_combo(
                    label           = "Pre-Amp Gain",
                    width           = -110,
                    items           = ["16-bit", "12-bit"], 
                    default_value   = self.settings["preAmpGain"],
                    callback        = self.update_settings,
                )

                self.settings_pixel_encoding = dpg.add_combo(
                    label           = "Pixel Encoding",
                    width           = -110,
                    items           = ["Mono16", "Mono12"], 
                    default_value   = self.settings["pixel_encoding"],
                    callback        = self.update_settings,
                )

                self.settings_noise_filter = dpg.add_checkbox(
                    label           = "Noise Filter",
                    default_value   = self.settings["noise_filter"],
                    callback        = self.update_settings,
                )
                    
                self.settings_blemish_correction = dpg.add_checkbox(
                    label           = "Blemish Correction",
                    default_value   = self.settings["blemish_correction"],
                    callback        = self.update_settings,
                )            


                dpg.add_spacer(height=20)
                dpg.add_text("Spooling Settings")
                dpg.add_separator()

                self.settings_spool_to_disk = dpg.add_checkbox(
                    label           = "Spool to Disk",
                    default_value   = self.settings["spool_to_disk"],
                )

                self.settings_spool_location = dpg.add_input_text(
                    label           = "Spool Location",
                    width           = -110,
                    default_value   = os.path.realpath(os.path.join(os.getcwd(), "Spool")),
                    enabled         = self.settings["spool_to_disk"], 
                )
                
                dpg.add_spacer(height=20)
                dpg.add_text("Capacity")
                dpg.add_separator()

                with dpg.group(horizontal=True):
                    dpg.add_text("File Size Estimate:")
                    self.settings_ram_estimate = dpg.add_text("1024 MB")

                dpg.add_spacer(height=1)
                self.disk_capacity_bar      = self.draw_multi_bar("Disk")
                self.camera_capacity_bar    = self.draw_multi_bar("Camera")
                self.ram_capacity_bar       = self.draw_multi_bar("Ram")
                

            # Update the settings for the first time
            self.update_settings()

    def update_multi_bar(self, bar, text, fg01_end, fg02_end):
        bar_w               = bar["w"]
        bar_h               = bar["h"]

        dpg.configure_item( bar["text"], text = text )
        dpg.configure_item( bar["fg01"], pmax = (bar_w * fg01_end, bar_h) )
        dpg.configure_item( bar["fg02"], pmin = (bar_w * fg01_end, 0))
        dpg.configure_item( bar["fg02"], pmax = (bar_w * fg01_end + bar_w * fg02_end , bar_h) )


    def draw_multi_bar(self, name):
        bar_w  = self.window_width - 17
        bar_h  = 20
        bar_f  = 13
        bar_p1 = 0.5
        bar_p2 = 0.1
        bar    = dict(
            w       = bar_w,
            h       = bar_h,
            f       = bar_f,
        )
        with dpg.drawlist( width=bar_w, height=bar_h):                    
            bar["bg"]   = dpg.draw_rectangle( (0, 0),               (bar_w, bar_h),                          color=(50, 50, 50, 0), fill=(50, 50, 50, 255)   )
            bar["fg01"] = dpg.draw_rectangle( (0, 0),               (bar_p1 * bar_w, bar_h),                 color=(0, 100, 60, 0), fill=(0, 100, 60, 255)   )
            bar["fg02"] = dpg.draw_rectangle( (0, 0),               (0.5 * bar_w, bar_h),                    color=(0, 100, 60, 0), fill=(100, 60, 60, 255)  )
            bar["text"] = dpg.draw_text(      (5, (bar_h / 2) - (bar_f/2)),                                  text=f"{name}: {bar_p2:.2f}%",       size=bar_f )
        
        return bar

    
    def update_settings(self):
        
        # Create a new settings dictionary
        settings = {
            "acquisition_mode"      : dpg.get_value(self.settings_acquisition_mode),
            "exposure_time"         : dpg.get_value(self.settings_exposure_time),
            "trigger_mode"          : dpg.get_value(self.settings_trigger_mode),
            "video_duration"        : dpg.get_value(self.settings_video_duration),
            "frame_rate"            : dpg.get_value(self.settings_frame_rate),
            "num_frames"            : dpg.get_value(self.settings_num_frames),
            "image_area"            : dpg.get_value(self.settings_image_area),
            "pixel_binning"         : dpg.get_value(self.settings_pixel_binning),
            "image_width"           : dpg.get_value(self.settings_image_width),
            "image_height"          : dpg.get_value(self.settings_image_height),
            "image_left"            : dpg.get_value(self.settings_image_left),
            "image_top"             : dpg.get_value(self.settings_image_top),
            "pixel_readout_rate"    : dpg.get_value(self.settings_pixel_readout_rate),
            "shutter_mode"          : dpg.get_value(self.settings_shutter_mode),
            "preAmpGain"            : dpg.get_value(self.settings_preAmpGain),
            "pixel_encoding"        : dpg.get_value(self.settings_pixel_encoding),
            "noise_filter"          : dpg.get_value(self.settings_noise_filter),
            "blemish_correction"    : dpg.get_value(self.settings_blemish_correction),
            "spool_to_disk"         : dpg.get_value(self.settings_spool_to_disk),
            "spool_location"        : dpg.get_value(self.settings_spool_location),
        }

        def isChanged(key):
            return key in self.settings and settings[key] != self.settings[key]

        # Update any settings that depend on eachother
        
        # If the exposure time changes, update the frame rate if it is out of bounds
        if isChanged("exposure_time") and settings["frame_rate"] * settings["exposure_time"] > 1.0:
            settings["frame_rate"] = round(1.0 / settings["exposure_time"], 6)
            dpg.set_value(self.settings_frame_rate, settings["frame_rate"])


        # If the frame rate changes, update the number of frames
        if isChanged("frame_rate"):

            # Make sure the frame rate does not exceed the exposure time
            if settings["frame_rate"] * settings["exposure_time"] > 1.0:
                settings["frame_rate"] = round(1.0 / settings["exposure_time"], 6)
                dpg.set_value(self.settings_frame_rate, settings["frame_rate"])

            else:
                settings["num_frames"] = int(settings["video_duration"] * settings["frame_rate"])
                dpg.set_value(self.settings_num_frames, settings["num_frames"])

        # If the video duration changes, update the number of frames
        elif isChanged("video_duration"):
            settings["num_frames"] = int(settings["video_duration"] * settings["frame_rate"])
            dpg.set_value(self.settings_num_frames, settings["num_frames"])

        # If the number of frames changes, update the video duration
        elif isChanged("num_frames"):
            settings["video_duration"] = settings["num_frames"] / settings["frame_rate"]
            dpg.set_value(self.settings_video_duration, settings["video_duration"])
        
        # If the image area changes to Custom, enable the width, height, left, and top inputs
        if isChanged("image_area"):
            if settings["image_area"] == "Custom":
                dpg.enable_item(self.settings_image_width)
                dpg.enable_item(self.settings_image_height)
                dpg.enable_item(self.settings_image_left)
                dpg.enable_item(self.settings_image_top)
            else:
                dpg.disable_item(self.settings_image_width)
                dpg.disable_item(self.settings_image_height)
                dpg.disable_item(self.settings_image_left)
                dpg.disable_item(self.settings_image_top)
        
        # Make sure the image width and height are within bounds
        if isChanged("image_width"):
            if settings["image_width"] < 1:
                settings["image_width"] = 1
                dpg.set_value(self.settings_image_width, settings["image_width"])
            elif settings["image_width"] > self.frame_width:
                settings["image_width"] = self.frame_width
                dpg.set_value(self.settings_image_width, settings["image_width"])
        
        if isChanged("image_height"):
            if settings["image_height"] < 1:
                settings["image_height"] = 1
                dpg.set_value(self.settings_image_height, settings["image_height"])
            elif settings["image_height"] > self.frame_height:
                settings["image_height"] = self.frame_height
                dpg.set_value(self.settings_image_height, settings["image_height"])
        
        # Make sure the image left and top are within bounds
        if isChanged("image_left"):
            if settings["image_left"] < 1:
                settings["image_left"] = 1
                dpg.set_value(self.settings_image_left, settings["image_left"])
            elif settings["image_left"] + settings["image_width"] > self.frame_width:
                settings["image_left"] = self.frame_width - settings["image_width"]
                dpg.set_value(self.settings_image_left, settings["image_left"])
            
        if isChanged("image_top"):
            if settings["image_top"] < 1:
                settings["image_top"] = 1
                dpg.set_value(self.settings_image_top, settings["image_top"])
            elif settings["image_top"] + + settings["image_height"] > self.frame_height:
                settings["image_top"] = self.frame_height - settings["image_height"]
                dpg.set_value(self.settings_image_top, settings["image_top"])


        # Update the ram calculations
        width           = settings["image_width"]
        height          = settings["image_height"]
        num_frames      = settings["num_frames"]
        pixel_encoding  = settings["pixel_encoding"]

        #  Determine the number of bits per pixel based on the pixel encoding
        if pixel_encoding == "Mono16":
            pixel_size = 8
        elif pixel_encoding == "Mono12":
            pixel_size = 6
                
        # Calculate the estimated file size in MB
        # pixel_size is in bits, so convert to bytes first
        file_size_b     = (width * height * pixel_size * num_frames) // 8  # bits to bytes
        file_size_mb    = file_size_b / (1024 * 1024)
        dpg.set_value(self.settings_ram_estimate, f"{file_size_mb:.2f} MB")

        # Calculate the disk capacity
        file_location       = settings["spool_location"]
        total, used, free   = shutil.disk_usage(file_location)
        disk_used           = used / total
        file_used           = file_size_b / total
        total_used          = disk_used + file_used

        self.update_multi_bar( self.disk_capacity_bar, f"Disk: {total_used:.2%}", disk_used, file_used )
        
        # Calculate the estimated RAM usage
        ram_used            = psutil.virtual_memory().used / psutil.virtual_memory().total
        ram_total           = psutil.virtual_memory().total        
        file_used           = file_size_b / ram_total

        self.update_multi_bar( self.ram_capacity_bar, f"Ram: {ram_used + file_used:.2%}", ram_used, file_used )

        # Calculate camera ram usage
        camera_ram_cap      = self.andor.getRamCapacity()
        camera_ram_used     = file_size_b / camera_ram_cap
        self.update_multi_bar( self.camera_capacity_bar, f"Camera: {camera_ram_used:.2%}", camera_ram_used, 0.0 )

        self.settings = settings
        
    def camera_run_continuous(self):        
        self.andor.runContinuous(self.onNewFrame, self.settings)         
            
        

    def onNewFrame(self):        
        self.andor.GetAcquiredData(self.imageArray)
        self.frame_idx          += 1
        self.new_frame_avail    = True

        self.convertImageArrayToLatestFrame()
        self.createDiffImage()

        # Update all the rois with the new frame
        for roi in self.feed.rois:
            roi.update_roi_data()


    def convertImageArrayToLatestFrame(self):
        
        # Convert the image array to a format suitable for the texture
        # The texture format is RGBA, so we need to convert the image array accordingly
        # Assuming the image array is in grayscale (1 channel), we need to convert it to 4 channels
        arr                 = self.imageArray.astype(np.float32) / 65535.0  # Normalize to [0,1]
        rgba                = np.zeros((self.frame_height, self.frame_width, 4), dtype=np.float32)
        rgba[..., 0]        = arr.reshape(self.frame_height, self.frame_width)  # Red channel
        rgba[..., 1]        = 0.0  # Green channel
        rgba[..., 2]        = 0.0  # Blue channel
        rgba[..., 3]        = 1.0  # Alpha channel

        # Update the texture with the new image data
        self.latest_frame[:] = rgba.flatten()


    def createDiffImage(self):        

        diff = self.zero_frame - self.latest_frame
        diff_reshaped = diff.reshape((self.frame_height, self.frame_width, 4)).astype(np.float32)

        # Set red channel for diff > 0, blue channel for diff < 0, both normalized to [0, 1]
        red = np.clip(diff_reshaped[..., 0], 0, 1)
        blue = np.clip(-diff_reshaped[..., 0], 0, 1)

        rgba = np.zeros_like(diff_reshaped)
        rgba[..., 0] = red  # Red channel for positive diff
        rgba[..., 1] = 0.0  # Green channel
        rgba[..., 2] = blue  # Blue channel for negative diff
        rgba[..., 3] = 1.0   # Alpha channel


        self.diff_frame[:]  = rgba.flatten()

    def render(self):   
        if(self.new_frame_avail):
            self.new_frame_avail = False
                        
            self.feed.render()
            self.zero.render()
            self.diff.render()