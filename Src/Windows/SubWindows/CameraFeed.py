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

    def __init__(self, parent, imageArray, image_width, image_height, name, tag, size=500, pos=(10, 10)):

        self.width          = size
        self.height         = size + (self.padding * 1.2)

        self.name           = name
        self.tag            = tag
        self.imageArray     = imageArray
        self.parent         = parent

        self.image_width    = image_width
        self.image_height   = image_height

        self.selecting      = False
        self.select_start   = None
        self.select_end     = None
        
        self.popup_opened   = False

        self.rois           = []
        self.roi_index      = 0

        with dpg.window(
            label                = name,
            tag                  = f"{tag}_Window",
            width                = self.width,
            height               = self.height,
            pos                  = pos,
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):
            
            self.window_id      = dpg.last_item()

            dpg.bind_item_theme( self.window_id, no_padding_theme)

            # # STARTUP
            # # ################################################################
            with dpg.item_handler_registry(tag=f"{tag}_ResizeHandler"):
                dpg.add_item_resize_handler( callback=self._on_window_resize )
                dpg.bind_item_handler_registry(self.window_id, f"{tag}_ResizeHandler")

            # Create a textures for the camera feed
            with dpg.texture_registry(show=False):
                self.texture_id = dpg.add_dynamic_texture(
                    width           = image_width,
                    tag             = f"{tag}_Texture",
                    height          = image_height,
                    default_value   = self.imageArray,
                )
            

            self.image_id = dpg.add_image(
                tag             = f"{tag}_Image",
                texture_tag     = f"{tag}_Texture",
                width           = self.width,
                height          = self.height,
                pos             = (0, 0),  
            )

            if tag == "CameraFeed":
                # Drawlist for selection rectangle
                with dpg.drawlist(width=self.width, height=self.height, pos=(0, 0)) as self.drawlist_id:
                    
                    with dpg.popup(self.drawlist_id, mousebutton=dpg.mvMouseButton_Right, tag=f"{tag}_roi_popup") as self.roi_popup:
                        dpg.add_button(label="Make Region of Interest", width=200, callback=self._make_window_for_roi)
                        # dpg.add_button(label="Delete ROI",  width=-1, callback=self._delete_roi)


                with dpg.handler_registry():
                    dpg.add_mouse_down_handler(callback=self._on_mouse_down , button=dpg.mvMouseButton_Left)                
                    dpg.add_mouse_move_handler(callback=self._on_mouse_drag)
                    dpg.add_mouse_release_handler(callback=self._on_mouse_up)
            


    def _on_mouse_down(self, sender, app_data):

        if dpg.is_item_visible(self.roi_popup):
            return

        if not self.selecting:
            mouse_pos           = dpg.get_mouse_pos(local=True)
            self.selecting      = True
            self.select_start   = mouse_pos
            self.select_end     = mouse_pos

    def _on_mouse_drag(self, sender, app_data):
        if self.selecting:
            mouse_pos = dpg.get_mouse_pos(local=True)
            shift_pressed = dpg.is_key_down(dpg.mvKey_LShift)
            if shift_pressed and self.select_start:
                x1, y1 = self.select_start
                x2, y2 = mouse_pos
                dx = x2 - x1
                dy = y2 - y1
                # Make the box square by using the smaller delta
                side = min(abs(dx), abs(dy))
                # Preserve the drag direction
                x2 = x1 + side * (1 if dx >= 0 else -1)
                y2 = y1 + side * (1 if dy >= 0 else -1)
                self.select_end = (x2, y2)
            else:
                self.select_end = mouse_pos

            self._draw_selection_box()

    def _on_mouse_up(self, sender, app_data):
        self.selecting = False        

    def _draw_selection_box(self):
        dpg.delete_item(self.drawlist_id, children_only=True)
        self.draw_roi_rectangles()

        if self.select_start and self.select_end:
            x1, y1 = self.select_start
            x2, y2 = self.select_end

            dpg.draw_rectangle((x1, y1), (x2, y2), color=(0, 255, 0, 255), thickness=1, parent=self.drawlist_id)


    def _make_window_for_roi(self):
        if self.select_start and self.select_end:
            x1, y1 = self.select_start
            x2, y2 = self.select_end

            # Convert the coordinates to the image array scale
            x1 = int(scale(x1, 0, self.width  , 0, self.image_width))
            y1 = int(scale(y1, 0, self.height , 0, self.image_height))
            x2 = int(scale(x2, 0, self.width  , 0, self.image_width))
            y2 = int(scale(y2, 0, self.height , 0, self.image_height))
            
            self.roi_index += 1
            roi                     = RegionOfInterest("ROI", f"{self.tag}_ROI_{self.roi_index}", self, x1, x2, y1, y2 )
            self.rois.append(roi)

            self.select_start   = None
            self.select_end     = None
            self.selecting      = False         

            self.draw_roi_rectangles()   

    def _close_roi(self, tag):

        # Find the ROI with the given tag and remove it
        for roi in self.rois:
            if roi.tag == tag:
                dpg.delete_item(roi.window_id)
                self.rois.remove(roi)
                break        

        self.draw_roi_rectangles()



    def _on_window_resize(self):
        # Get the new size of the window
        new_width, new_height = dpg.get_item_rect_size(self.window_id)
        
        # Update the size of the image
        dpg.set_item_width(self.image_id, new_width)
        dpg.set_item_height(self.image_id, new_height)

        if self.tag == "CameraFeed":
            dpg.set_item_width(self.drawlist_id, new_width)
            dpg.set_item_height(self.drawlist_id, new_height)
        
        # dpg.set_item_height(self.window_id, new_width + (self.padding * 1.2))
        self.draw_roi_rectangles()
        
        self.width      = new_width
        self.height     = new_height
        

    def draw_roi_rectangles(self):
        for roi in self.rois:
            # Convert the coordinates to the image array scale
            x1 = int(scale(roi.x1, 0, self.image_width  , 0, self.width ))
            y1 = int(scale(roi.y1, 0, self.image_height , 0, self.height))
            x2 = int(scale(roi.x2, 0, self.image_width  , 0, self.width ))
            y2 = int(scale(roi.y2, 0, self.image_height , 0, self.height))

                        
            dpg.draw_rectangle((x1, y1), (x2, y2), color=(0, 0, 255, 255), thickness=1, parent=self.drawlist_id)

    def reset_rois(self):
        # Re-initialize all ROIs while maintaining their properties
        old_rois = self.rois.copy()

        for roi in self.rois:
            dpg.delete_item(roi.window_id)

        self.rois.clear()

        # Recreate each ROI with the same properties
        for roi in old_rois:
            self.roi_index += 1
            new_roi = RegionOfInterest( roi.name, f"{self.tag}_ROI_{self.roi_index}", self, roi.x1, roi.x2, roi.y1, roi.y2 )
            # Copy any additional properties if needed
            self.rois.append(new_roi)
        
        self.draw_roi_rectangles()

    def render(self):
        
        # If we are resizing the width, update the height
        dpg.set_item_height(self.window_id, self.width)

        dpg.set_value(self.texture_id, self.imageArray)

        self.draw_roi_rectangles()

        # Render all the ROI rectangles
        for roi in self.rois:
            roi.render()

        

