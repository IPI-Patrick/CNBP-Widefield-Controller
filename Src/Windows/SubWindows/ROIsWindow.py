import dearpygui.dearpygui as dpg
import numpy as np

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import transparent_plot_theme


class ROIsWindow:

    ROW_HEIGHT = 120
    XAXIS_ROW_HEIGHT = 30

    def __init__(self):
        self.width = 640
        self.height = 400
        self.name = "ROIs"
        self.tag = "ROIsWindow"
        self._current_roi_tags = []
        self._roi_ui = {}
        self._xaxis_ui = None

        with dpg.window(
            label=self.name,
            tag=f"{self.tag}_Window",
            width=self.width,
            height=self.height,
            pos=(935, 10),
            no_scrollbar=False,
            no_resize=False,
        ):
            self.window_id = dpg.last_item()

            with dpg.child_window(border=False, autosize_x=True, autosize_y=True):
                self.content_id = dpg.last_item()

    def rebuild_layout(self, rois):
        new_tags = [roi.tag for roi in rois]
        if new_tags == self._current_roi_tags:
            return

        for ui in self._roi_ui.values():
            tex = ui.get("texture_id")
            if tex and dpg.does_item_exist(tex):
                dpg.delete_item(tex)
        self._roi_ui = {}
        self._xaxis_ui = None
        self._current_roi_tags = list(new_tags)

        if dpg.does_item_exist(self.content_id):
            dpg.delete_item(self.content_id, children_only=True)

        if not rois:
            return

        with dpg.table(
            parent=self.content_id,
            header_row=False,
            resizable=True,
            borders_innerV=False,
            borders_outerV=False,
            borders_innerH=False,
            borders_outerH=False,
        ):
            dpg.add_table_column(label="Image", width_fixed=True, init_width_or_weight=150)
            dpg.add_table_column(label="Graph", width_stretch=True)

            for roi in rois:
                with dpg.table_row():
                    # Image cell
                    with dpg.texture_registry(show=False):
                        initial_rgba = roi.pending_image_rgba
                        if initial_rgba is None:
                            initial_rgba = np.zeros((roi.image_width * roi.image_height * 4,), dtype=np.float32)
                        tex_id = dpg.add_dynamic_texture(
                            width=roi.image_width,
                            height=roi.image_height,
                            default_value=initial_rgba,
                        )

                    with dpg.group():
                        with dpg.group(horizontal=True):
                            name_id = dpg.add_text(roi.name)
                            dpg.add_spacer(width=-1)
                            _tag = roi.tag
                            _parent = roi.parent
                            dpg.add_button(
                                label="X",
                                callback=lambda s, a, u=(_tag, _parent): u[1]._close_roi(u[0]),
                            )
                        img_id = dpg.add_image(
                            texture_tag=tex_id,
                            width=150,
                            height=self.ROW_HEIGHT - 22,
                        )

                    # Graph cell
                    with dpg.group():
                        dpg.add_spacer(height=10)
                        with dpg.plot(
                            height=self.ROW_HEIGHT - 10,
                            width=-1,
                            no_title=True,
                            no_menus=True,
                            no_box_select=True,
                        ):
                            plot_id = dpg.last_item()
                            x_axis_id = dpg.add_plot_axis(
                                dpg.mvXAxis, label="", no_label=True,
                                no_tick_labels=True, no_tick_marks=True,
                                auto_fit=True,
                            )
                            y_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="", no_label=True, auto_fit=True, opposite=True, tick_format="%.5g")
                            series_id = dpg.add_line_series([], [], label=roi.name, parent=y_axis_id)

                    dpg.bind_item_theme(plot_id, transparent_plot_theme)

                    self._roi_ui[roi.tag] = {
                        "texture_id": tex_id,
                        "image_id": img_id,
                        "name_text_id": name_id,
                        "plot_id": plot_id,
                        "x_axis_id": x_axis_id,
                        "y_axis_id": y_axis_id,
                        "series_id": series_id,
                    }

            # Extra row: duplicate last ROI's x-axis data with visible axis
            last_roi = rois[-1]
            last_ui = self._roi_ui[last_roi.tag]
            with dpg.table_row():
                dpg.add_spacer(height=self.XAXIS_ROW_HEIGHT)
                with dpg.plot(
                    height=self.XAXIS_ROW_HEIGHT,
                    width=-1,
                    no_title=True,
                    no_menus=True,
                    no_box_select=True,
                ):
                    xaxis_plot_id = dpg.last_item()
                    xaxis_x_id = dpg.add_plot_axis(
                        dpg.mvXAxis, label="", no_label=True,
                        auto_fit=True,
                    )
                    xaxis_y_id = dpg.add_plot_axis(dpg.mvYAxis, label="", no_label=True, no_tick_labels=True, no_tick_marks=True, opposite=True)
                    xaxis_series_id = dpg.add_line_series([], [], label="", parent=xaxis_y_id)

                dpg.bind_item_theme(xaxis_plot_id, transparent_plot_theme)

                self._xaxis_ui = {
                    "plot_id": xaxis_plot_id,
                    "x_axis_id": xaxis_x_id,
                    "series_id": xaxis_series_id,
                }

    def render(self):
        self._sync_xaxis()

    def _sync_xaxis(self):
        if not self._xaxis_ui or not self._roi_ui:
            return

        # Find global x range from all ROIs
        x_min = float('inf')
        x_max = float('-inf')
        last_x = []
        last_y = []
        for tag, ui in self._roi_ui.items():
            series_id = ui.get("series_id")
            if series_id and dpg.does_item_exist(series_id):
                data = dpg.get_value(series_id)
                if data and len(data) >= 2 and data[0]:
                    x_vals = data[0]
                    x_min = min(x_min, x_vals[0])
                    x_max = max(x_max, x_vals[-1])
                    last_x = data[0]
                    last_y = data[1]

        # Set the x-axis series to match the last ROI so axis range matches
        xaxis_series = self._xaxis_ui["series_id"]
        if dpg.does_item_exist(xaxis_series):
            dpg.set_value(xaxis_series, [last_x, [0.0] * len(last_x)])

        # Sync all ROI x-axes and the bottom axis to the same range
        if x_min < x_max:
            for ui in self._roi_ui.values():
                x_id = ui.get("x_axis_id")
                if x_id and dpg.does_item_exist(x_id):
                    dpg.set_axis_limits(x_id, x_min, x_max)
            xaxis_x = self._xaxis_ui["x_axis_id"]
            if dpg.does_item_exist(xaxis_x):
                dpg.set_axis_limits(xaxis_x, x_min, x_max)

    def render_roi(self, roi):
        ui = self._roi_ui.get(roi.tag)
        if ui is None:
            return

        with roi.data_lock:
            pending_version = roi.pending_version
            pending_shape = roi.pending_image_shape
            pending_image_rgba = roi.pending_image_rgba
            pending_plot_x, pending_plot_y = roi.pending_plot_data

        if pending_version != roi.applied_version:
            self._ensure_roi_texture_shape(roi, ui, pending_shape)
            if pending_image_rgba is not None and dpg.does_item_exist(ui["texture_id"]):
                dpg.set_value(ui["texture_id"], pending_image_rgba)
            if dpg.does_item_exist(ui["series_id"]):
                dpg.set_value(ui["series_id"], [pending_plot_x, pending_plot_y])
            if dpg.does_item_exist(ui["y_axis_id"]):
                dpg.fit_axis_data(ui["y_axis_id"])
            roi.applied_version = pending_version

    def _ensure_roi_texture_shape(self, roi, ui, image_shape):
        crop_height, crop_width = image_shape
        crop_height = max(1, int(crop_height))
        crop_width = max(1, int(crop_width))

        if crop_width == roi.image_width and crop_height == roi.image_height:
            return

        roi.image_width = crop_width
        roi.image_height = crop_height

        with dpg.texture_registry(show=False):
            new_tex = dpg.add_dynamic_texture(
                width=crop_width,
                height=crop_height,
                default_value=np.zeros((crop_width * crop_height * 4,), dtype=np.float32),
            )

        old_tex = ui["texture_id"]
        ui["texture_id"] = new_tex
        img_id = ui.get("image_id")
        if img_id and dpg.does_item_exist(img_id):
            dpg.configure_item(img_id, texture_tag=new_tex)
        if dpg.does_item_exist(old_tex):
            dpg.delete_item(old_tex)

    def _state_name(self):
        return f"{type(self).__name__}"

    def SaveState(self):
        save_state_file(
            self._state_name(),
            {"window": capture_window_state(self.window_id)},
        )

    def LoadState(self):
        state = load_state_file(self._state_name())
        if not state:
            return
        apply_window_state(self.window_id, state.get("window"))
