import numpy as np
import dearpygui.dearpygui as dpg
from Utils.themes import no_padding_theme, default_theme

class RegionOfInterest:

    def __init__(self, name, tag, parent, x1, x2, y1, y2):

        self.width          = 500
        self.height         = 200
        self.parent         = parent        

        self.frame_width    = self.parent.image_width
        self.frame_height   = self.parent.image_height
        self.x1             = x1
        self.x2             = x2
        self.y1             = y1
        self.y2             = y2

        rgba                = self.parent.imageArray.reshape((self.frame_height, self.frame_width, 4))
        self.imageArray     = rgba[y1:y2, x1:x2, :].flatten()

        self.image_width    = int(np.abs(x2 - x1))
        self.image_height   = int(np.abs(y2 - y1))

        self.num_frames     = self.parent.parent.settings["num_frames"]
        self.frame_index    = 0
        self.x_axis         = [i for i in range(self.num_frames)]
        self.y_axis         = [np.nan for i in range(self.num_frames)]

        self.name           = name
        self.tag            = tag
        with dpg.window(
            label                = name,
            tag                  = f"{tag}_Window",
            width                = self.width,
            height               = self.height,
            pos                  = (0, 0),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
            on_close             = lambda: self.parent._close_roi(tag)
        ):
            
            self.window_id     = dpg.last_item()
            dpg.bind_item_theme( self.window_id, no_padding_theme) 

            with dpg.item_handler_registry(tag=f"{tag}_ResizeHandler"):
                dpg.add_item_resize_handler( callback=self._on_window_resize )
                dpg.bind_item_handler_registry(self.window_id, f"{tag}_ResizeHandler")

            if(0 in self.imageArray.shape):
                print(f"RegionOfInterest: {tag} has no data, skipping texture creation.")
                return
            
            with dpg.texture_registry(show=False):
                self.texture_id = dpg.add_dynamic_texture(
                    width           = self.image_width,
                    height          = self.image_height,
                    tag             = f"{tag}_Texture",
                    default_value   = self.imageArray,
                )
            

            with dpg.group(horizontal=True):
                self.group_id = dpg.last_item()
                self.image_id = dpg.add_image(
                    tag             = f"{tag}_Image",
                    texture_tag     = f"{tag}_Texture",
                    width           = self.height,
                    height          = self.height,
                )   


                with dpg.plot(height=-1, width=-1):
                    self.plot_id    = dpg.last_item()
                    self.x_axis_id = dpg.add_plot_axis(dpg.mvXAxis, label="", lock_max=True, lock_min=True, no_tick_labels=False)
                    self.y_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="", no_tick_labels=False)
                    # Example: plot mean intensity along x-axis
                    self.trace_series_id = dpg.add_line_series(
                        self.x_axis,
                        self.y_axis,
                        label="Mean Intensity",
                        parent=self.y_axis_id
                    )

                dpg.set_axis_limits(self.x_axis_id, 0, self.num_frames - 1)
            
            dpg.bind_item_theme( self.plot_id, default_theme )


    def _on_window_resize(self, sender, app_data):
        new_width, new_height = dpg.get_item_rect_size(self.window_id)
        dpg.set_item_height(self.image_id, new_height)
        dpg.set_item_width(self.image_id, new_height)

    def update_roi_data(self):
        self.frame_index = self.parent.parent.frame_idx % self.num_frames

        self.x_axis[self.frame_index]   = self.frame_index
        self.y_axis[self.frame_index]   = np.mean(self.imageArray)

    def render(self):
        rgba                = self.parent.imageArray.reshape((self.frame_height, self.frame_width, 4))
        self.imageArray     = rgba[self.y1:self.y2, self.x1:self.x2, :].flatten()

        dpg.set_value(f"{self.tag}_Texture", self.imageArray)

        dpg.set_value(self.trace_series_id, [self.x_axis, self.y_axis])