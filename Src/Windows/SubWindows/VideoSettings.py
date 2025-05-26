import threading
import numpy as np
import dearpygui.dearpygui as dpg
from Utils.themes import read_only_theme, red_green_button_disabled, red_green_button_enabled

class VideoSettings:

    window_width        = 300
    window_height       = 500

    live                = False 
    started             = False

    num_frames          = 0
    frame_idx           = 0
    zero_started        = False
    last_frame          = None

    def __init__(self, parent):
        
        self.parent         = parent    


        with dpg.window(
            label                = "Video Settings",
            tag                  = "#VideoSettings",
            width                = self.window_width,
            height               = self.window_height,
            pos                  = (705, 10),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):
            self.window_id       = dpg.last_item()
            self.controls        = parent
            
            with dpg.group():
                self.settings_group = dpg.last_item()

                dpg.add_text("Zero Frame")
                dpg.add_separator()

                self.zero_frame_time = dpg.add_input_float(
                    label           = "Time",      
                    width           = -40,          
                    default_value   = 2.0,
                    min_value       = 0.1,
                    max_value       = 100.0,
                    format          = "%.1f s",                
                )

                self.zero_frame_progess = dpg.add_progress_bar(
                    width           = -1,
                    height          = 20,
                    default_value   = 0.0,
                    overlay         = "0%",
                )

                self.zero_start_button = dpg.add_button(
                    label           = "Record Zero Frame",
                    width           = -1,
                    height          = 20,
                    callback        = self.record_zero,
                )



                # Buttons need to be outside of the settings group so they're always enabled
                dpg.add_spacer(height=20)
                dpg.add_text("Controls")
                dpg.add_separator()

                self.start_button = dpg.add_button(
                    label           = "Start",
                    width           = -1,
                    height          = 60,
                    callback        = self.start_acquisition,
                )

                self.live_button = dpg.add_button(
                    label           = "Live",
                    width           = -1,
                    height          = 60,
                    callback        = self.start_live_feed,
                )

    def start_acquisition(self):
        pass

    def start_live_feed(self):

        if self.live == False:
            self.controls.andor.Initialize()
            self.controls.andor.SetReadMode(4)
            
            self.live = True

            dpg.configure_item(self.live_button, label="Stop")
            dpg.bind_item_theme(self.live_button, red_green_button_enabled )

            dpg.configure_item(self.controls.settings_group, enabled=False)
            dpg.configure_item(self.start_button, enabled=False)

            self.parent.feed.reset_rois()
            
            self.camera_thread = threading.Thread(target=self.controls.camera_run_continuous, daemon=True)
            self.camera_thread.start()        
        else:
            self.live = False
            self.controls.andor.AbortAquisition()

            dpg.configure_item(self.controls.settings_group, enabled=True)
            dpg.configure_item(self.live_button, label="Live")
            dpg.bind_item_theme(self.live_button, red_green_button_disabled )
            dpg.configure_item(self.start_button, enabled=True)
            self.camera_thread.join() 
    




    def start_zero_frame(self):
        self.controls.andor.runForXFrames(self.on_zero_frame, self.num_frames, self.controls.settings)

    def on_zero_frame(self):
        self.controls.andor.GetAcquiredData(self.controls.imageArray)

        dpg.configure_item(self.zero_frame_progess, default_value=self.frame_idx / self.num_frames, overlay=f"{self.frame_idx}/{self.num_frames}")

        self.controls.convertImageArrayToLatestFrame()
        self.frames[self.frame_idx]     = self.controls.latest_frame
        self.frame_idx += 1

        if(self.frame_idx >= self.num_frames):

            self.record_zero()
            
            dpg.configure_item(self.live_button, enabled=True)
            dpg.configure_item(self.start_button, enabled=True)
            dpg.configure_item(self.zero_frame_time, enabled=True)

            dpg.configure_item(self.zero_start_button, label="Record Zero Frame")
            dpg.bind_item_theme(self.zero_start_button, red_green_button_disabled)
            dpg.configure_item(self.zero_frame_progess, default_value=1.0, overlay=f"Done")
            
            self.controls.zero_frame[:] = np.mean(self.frames, axis=0)

        self.controls.new_frame_avail   = True


    def record_zero(self):
        if self.zero_started == False:
        
            self.num_frames        = int(dpg.get_value(self.zero_frame_time) * self.controls.settings["frame_rate"])
            self.frame_idx         = 0
            self.zero_started      = True

            self.frame_idx         = 0
            self.frames            = np.array([self.controls.null_frame] * self.num_frames) 

            dpg.configure_item(self.live_button, enabled=False)
            dpg.configure_item(self.start_button, enabled=False)
            dpg.configure_item(self.zero_frame_time, enabled=False)

            dpg.configure_item(self.zero_start_button, label="Stop Recording")
            dpg.bind_item_theme(self.zero_start_button, red_green_button_enabled)
            
            self.camera_thread = threading.Thread(target=self.start_zero_frame, args=[], daemon=True)
            self.camera_thread.start()        
        else:
            self.zero_started = False
            dpg.configure_item(self.live_button, enabled=True)
            dpg.configure_item(self.start_button, enabled=True)
            dpg.configure_item(self.zero_frame_time, enabled=True)

            dpg.configure_item(self.zero_start_button, label="Stop Recording")
            dpg.bind_item_theme(self.zero_start_button, red_green_button_disabled)

            self.controls.andor.AbortAquisition()
