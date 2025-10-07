import numpy as np
import threading
import time
from Windows.SubWindows.RegionOfInterest import RegionOfInterest
import dearpygui.dearpygui as dpg
from Mocks.MockAndor import Andor
from Utils.utils import scale
from Utils.themes import no_padding_theme

class CameraFeedWindow:

    padding                 = 16

    def __init__(self, parent, Andor):

        self.parent         = parent
        self.Andor          = Andor

        self.width          = 1000
        self.height         = 1000

        self.name           = "Camera Feed"
        self.tag            = "CameraFeed"
        self.imageArray     = self._process_frame(self.Andor.latest_frame)

        self.image_width    = self.Andor.camera.AOIWidth
        self.image_height   = self.Andor.camera.AOIHeight

        self.selecting      = False
        self.select_start   = None
        self.select_end     = None
        
        self.popup_opened   = False

        self.rois           = []
        self.roi_index      = 0        

        with dpg.window(
            label                = self.name,
            tag                  = f"{self.tag}_Window",
            width                = self.width,
            height               = self.height,
            pos                  = (10, 10),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):
            
            self.window_id      = dpg.last_item()

            dpg.bind_item_theme( self.window_id, no_padding_theme)

            # STARTUP
            # ################################################################
            with dpg.item_handler_registry(tag=f"{self.tag}_ResizeHandler"):
                dpg.add_item_resize_handler( callback=self._on_window_resize )
                dpg.bind_item_handler_registry(self.window_id, f"{self.tag}_ResizeHandler")

            self.reset_texture()

            # Create a separate thread for the drawlist
            threading.Thread(target=self._process_camera_feed, daemon=True).start()


    def reset_texture(self):

        # Destroy the texture and image
        if(hasattr(self, 'texture_id')):
            dpg.delete_item(self.texture_id)
        if(hasattr(self, 'image_id')):
            dpg.delete_item(self.image_id)

        # Create a new texture for the camera feed
        with dpg.texture_registry(show=False):
            self.texture_id = dpg.add_dynamic_texture(
                width           = self.Andor.camera.AOIWidth,
                height          = self.Andor.camera.AOIHeight,
                tag             = f"{self.tag}_Texture",
                default_value   = self._process_frame(self.Andor.latest_frame),
            )
    
        self.image_id = dpg.add_image(
            tag             = f"{self.tag}_Image",
            texture_tag     = f"{self.tag}_Texture",
            width           = self.width,
            height          = self.height,
            pos             = (0, 0),
            parent          = self.window_id,
        )

    def _on_window_resize(self):
        # Get the new size of the window
        new_width, new_height = dpg.get_item_rect_size(self.window_id)
        
        # Update the size of the image
        dpg.set_item_width(self.image_id, new_width)
        dpg.set_item_height(self.image_id, new_height)
        
        self.width      = new_width
        self.height     = new_height
        

    def _process_frame(self, frame, min=0, max=2**16-1):

        # Return the latest frame as an RGBA image scaled between min and max
        with self.Andor.frame_lock:
            frame = self.Andor.latest_frame
            if frame is None:
                return np.zeros((frame.shape[0], frame.shape[1], 4), dtype=np.float32)

            scaled  = np.clip((frame - min) / (max - min), 0, 1)
            rgba    = np.zeros((frame.shape[0], frame.shape[1], 4), dtype=np.float32)
            rgba[..., 0] = scaled  # Red channel
            rgba[..., 1] = scaled  # Green channel
            rgba[..., 2] = scaled  # Blue channel
            rgba[..., 3] = 1.0     # Alpha channel
            
            return rgba


    def _process_camera_feed(self):
                
        # Check if new frames are available
        while(True):

            try:
                if self.Andor.frame_ready_event.is_set():
                    self.imageArray = self._process_frame(self.Andor.latest_frame)
                    self.Andor.frame_ready_event.clear()

                dpg.set_value(self.texture_id, self.imageArray)

            except Exception as e:
                print("Error updating camera feed:")
                print(e)
                print()

            # Limit to ~60 FPS
            time.sleep(0.016) 



    def render(self):
        
        # If we are resizing the width, update the height
        dpg.set_item_height(self.window_id, self.width)

        

