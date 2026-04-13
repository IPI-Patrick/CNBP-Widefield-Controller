import dearpygui.dearpygui as dpg
import numpy as np

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import no_spacing_theme, transparent_plot_theme


class ROIsWindow:

    ROW_HEIGHT = 120
    XAXIS_ROW_HEIGHT = 30
    ROI_IMAGE_SIZE = ROW_HEIGHT
    ROI_IMAGE_VERTICAL_OFFSET = 5
    ROI_IMAGE_DISPLAY_SIZE = ROW_HEIGHT - (ROI_IMAGE_VERTICAL_OFFSET * 2) - 9
    OVERLAY_SCROLLBAR_GUTTER = 18
    OVERLAY_WHEEL_SCROLL_STEP = 24
    ROI_CLOSE_BUTTON_SIZE = 20
    TRACE_METRIC_OPTIONS = ("Mean", "Max", "Max & Min")
    

    def __init__(self):
        self.width = 640
        self.height = 400
        self.name = "ROIs"
        self.tag = "ROIsWindow"
        self._current_roi_tags = []
        self._rois = []
        self._roi_ui = {}
        self._xaxis_ui = None
        self.y_scale_min = 0.0
        self.y_scale_max = 1.0
        self.y_scale_auto = True
        self.y_scale_mirrored = False
        self.trace_metric = "Mean"
        self._y_axis_limits_dirty = True
        self._autoscale_dirty = True
        self._autoscale_pending_tags = set()

        with dpg.theme() as self._table_theme:
            with dpg.theme_component(dpg.mvTable):
                dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 0, 0)

        with dpg.theme() as self._close_button_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 2, 1)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ButtonTextAlign, 0.5, 0.5)

        with dpg.window(
            label=self.name,
            tag=f"{self.tag}_Window",
            width=self.width,
            height=self.height,
            pos=(935, 10),
            no_scrollbar=True,
            no_scroll_with_mouse=True,
            no_resize=False,
        ):
            self.window_id = dpg.last_item()

            with dpg.group():
                dpg.add_text("Y-Axis Scaling")
                self.trace_metric_combo_id = dpg.add_combo(
                    items=list(self.TRACE_METRIC_OPTIONS),
                    label="Metric",
                    width=-30,
                    default_value=self.trace_metric,
                    callback=self._on_trace_metric_changed,
                )
                self.y_scale_min_input_id = dpg.add_input_float(
                    label="Min",
                    width=-30,
                    default_value=self.y_scale_min,
                    callback=self._on_y_scale_min_changed,
                )
                self.y_scale_max_input_id = dpg.add_input_float(
                    label="Max",
                    width=-30,
                    default_value=self.y_scale_max,
                    callback=self._on_y_scale_max_changed,
                )
                self.y_scale_auto_checkbox_id = dpg.add_checkbox(
                    label="Auto",
                    default_value=self.y_scale_auto,
                    callback=self._on_y_scale_auto_changed,
                )
                self.y_scale_mirrored_checkbox_id = dpg.add_checkbox(
                    label="Mirrored",
                    default_value=self.y_scale_mirrored,
                    callback=self._on_y_scale_mirrored_changed,
                )

            dpg.add_separator()

            with dpg.child_window(border=False, width=-1, height=-1):
                self.content_id = dpg.last_item()

            with dpg.child_window(
                border=False,
                width=1,
                height=1,
                pos=(0, 0),
                no_scrollbar=True,
                no_scroll_with_mouse=True,
            ):
                self.overlay_id = dpg.last_item()
                dpg.bind_item_theme(self.overlay_id, transparent_plot_theme)

            with dpg.handler_registry():
                dpg.add_mouse_wheel_handler(callback=self._on_overlay_mouse_wheel)

        self._sync_y_scale_inputs()

    def _on_close_roi_clicked(self, sender, app_data, user_data):
        tag, parent = user_data
        parent.close_roi(tag)

    def _on_overlay_mouse_wheel(self, sender, app_data):
        if not dpg.does_item_exist(self.overlay_id) or not dpg.is_item_hovered(self.overlay_id):
            return
        if not dpg.does_item_exist(self.content_id):
            return

        current_scroll = dpg.get_y_scroll(self.content_id)
        max_scroll = dpg.get_y_scroll_max(self.content_id)
        next_scroll = current_scroll - (float(app_data) * self.OVERLAY_WHEEL_SCROLL_STEP)
        dpg.set_y_scroll(self.content_id, float(np.clip(next_scroll, 0.0, max_scroll)))

    def _on_trace_metric_changed(self, sender, app_data, user_data=None):
        selected_metric = str(app_data)
        if selected_metric not in self.TRACE_METRIC_OPTIONS:
            selected_metric = self.TRACE_METRIC_OPTIONS[0]
        self.trace_metric = selected_metric
        self.invalidate_autoscale_cache(pending_tags=[roi.tag for roi in self._rois])
        for roi in self._rois:
            roi.request_trace_rebuild(clear_existing=True)

    def compute_trace_value(self, crop):
        if crop is None or crop.size == 0:
            return 0.0
        if self.trace_metric == "Max":
            return float(np.max(crop))
        if self.trace_metric == "Max & Min":
            max_value = float(np.max(crop))
            min_value = float(np.min(crop))
            return min_value if abs(min_value) > abs(max_value) else max_value
        return float(np.mean(crop, dtype=np.float64))

    def _on_y_scale_min_changed(self, sender, app_data, user_data=None):
        self.y_scale_min = float(app_data)
        if self.y_scale_min > self.y_scale_max:
            self.y_scale_max = self.y_scale_min
            dpg.set_value(self.y_scale_max_input_id, self.y_scale_max)
        self._mark_y_axis_limits_dirty()

    def _on_y_scale_max_changed(self, sender, app_data, user_data=None):
        self.y_scale_max = max(float(app_data), 0.0)
        if self.y_scale_mirrored:
            self.y_scale_min = -self.y_scale_max
            dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
        elif self.y_scale_max < self.y_scale_min:
            self.y_scale_min = self.y_scale_max
            dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
        self._mark_y_axis_limits_dirty()

    def _on_y_scale_auto_changed(self, sender, app_data, user_data=None):
        self.y_scale_auto = bool(app_data)
        if self.y_scale_auto:
            self.invalidate_autoscale_cache()
        self._sync_y_scale_inputs()
        self._recalculate_y_scale()

    def _on_y_scale_mirrored_changed(self, sender, app_data, user_data=None):
        self.y_scale_mirrored = bool(app_data)
        self._sync_y_scale_inputs()
        self._recalculate_y_scale()

    def _sync_y_scale_inputs(self):
        min_enabled = not self.y_scale_auto and not self.y_scale_mirrored
        max_enabled = not self.y_scale_auto
        dpg.configure_item(self.y_scale_min_input_id, enabled=min_enabled)
        dpg.configure_item(self.y_scale_max_input_id, enabled=max_enabled)

    def _mark_y_axis_limits_dirty(self):
        self._y_axis_limits_dirty = True

    def invalidate_autoscale_cache(self, pending_tags=None):
        self._autoscale_dirty = True
        self._autoscale_pending_tags = set(pending_tags or [])
        self._mark_y_axis_limits_dirty()

    def _recalculate_y_scale(self):
        if self.y_scale_auto:
            self._autoscale_pending_tags = set()
            self._set_y_scale_auto_limits(*self._compute_global_trace_limits())
            self._autoscale_dirty = False
            return

        if self.y_scale_mirrored:
            mirrored_limit = max(abs(self.y_scale_min), abs(self.y_scale_max), 1.0)
            self.y_scale_min = -mirrored_limit
            self.y_scale_max = mirrored_limit
            dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
            dpg.set_value(self.y_scale_max_input_id, self.y_scale_max)

        self._mark_y_axis_limits_dirty()

    def _compute_global_trace_limits(self):
        trace_min_value = 0.0
        trace_max_value = 0.0
        for roi in self._rois:
            with roi.data_lock:
                trace_min_value = min(trace_min_value, float(roi.trace_min_value))
                trace_max_value = max(trace_max_value, float(roi.trace_max_value))
        return trace_min_value, trace_max_value

    def _set_y_scale_auto_limits(self, scale_min, scale_max):
        scale_min = float(scale_min)
        scale_max = float(scale_max)
        if self.y_scale_mirrored:
            mirrored_limit = max(abs(scale_min), abs(scale_max), 1.0)
            self.y_scale_min = -mirrored_limit
            self.y_scale_max = mirrored_limit
        else:
            if scale_min == scale_max:
                if scale_min == 0.0:
                    scale_max = 1.0
                else:
                    padding = max(abs(scale_min) * 0.05, 1.0)
                    scale_min -= padding
                    scale_max += padding
            self.y_scale_min = scale_min
            self.y_scale_max = scale_max

        dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
        dpg.set_value(self.y_scale_max_input_id, self.y_scale_max)
        self._mark_y_axis_limits_dirty()

    def _refresh_autoscale_if_ready(self):
        if not self.y_scale_auto:
            return
        if self._autoscale_pending_tags:
            return
        if self._autoscale_dirty:
            self._set_y_scale_auto_limits(*self._compute_global_trace_limits())
            self._autoscale_dirty = False

    def _apply_shared_y_axis_limits(self):
        if not self._y_axis_limits_dirty:
            return

        y_min = -self.y_scale_max if self.y_scale_mirrored else self.y_scale_min
        y_max = self.y_scale_max
        if y_max <= y_min:
            y_max = y_min + 1.0

        for ui in self._roi_ui.values():
            y_axis_id = ui.get("y_axis_id")
            if y_axis_id and dpg.does_item_exist(y_axis_id):
                dpg.set_axis_limits(y_axis_id, y_min, y_max)

        self._y_axis_limits_dirty = False

    def _get_item_rects(self, item_id):
        if not item_id or not dpg.does_item_exist(item_id):
            return None

        item_state = dpg.get_item_state(item_id)
        rect_min = item_state.get("rect_min")
        rect_max = item_state.get("rect_max")
        rect_size = item_state.get("rect_size")

        if rect_size is None:
            rect_size = dpg.get_item_rect_size(item_id)
        if rect_min is None:
            rect_min = self._get_item_viewport_pos(item_id)
        if rect_max is None and rect_min is not None and rect_size is not None:
            rect_max = [rect_min[0] + rect_size[0], rect_min[1] + rect_size[1]]

        if rect_min is None or rect_max is None or rect_size is None:
            return None

        return rect_min, rect_max, rect_size

    def _get_item_viewport_pos(self, item_id):
        if not item_id or not dpg.does_item_exist(item_id):
            return None

        local_pos = dpg.get_item_pos(item_id)
        if local_pos is None:
            return None

        parent_id = dpg.get_item_parent(item_id)
        if not parent_id or not dpg.does_item_exist(parent_id):
            return list(local_pos)

        parent_rects = self._get_item_rects(parent_id)
        if parent_rects is not None:
            parent_rect_min, _, _ = parent_rects
            return [parent_rect_min[0] + local_pos[0], parent_rect_min[1] + local_pos[1]]

        parent_viewport_pos = self._get_item_viewport_pos(parent_id)
        if parent_viewport_pos is None:
            return list(local_pos)

        return [parent_viewport_pos[0] + local_pos[0], parent_viewport_pos[1] + local_pos[1]]

    def _get_item_rects_in_content_space(self, item_id):
        content_rects = self._get_item_rects(self.content_id)
        item_rects = self._get_item_rects(item_id)
        if content_rects is None or item_rects is None:
            return None

        content_rect_min, _, _ = content_rects
        item_rect_min, item_rect_max, item_rect_size = item_rects

        local_rect_min = [
            item_rect_min[0] - content_rect_min[0],
            item_rect_min[1] - content_rect_min[1],
        ]
        local_rect_max = [
            item_rect_max[0] - content_rect_min[0],
            item_rect_max[1] - content_rect_min[1],
        ]
        return local_rect_min, local_rect_max, item_rect_size

    def _sync_overlay_window(self):
        content_rects = self._get_item_rects(self.content_id)
        if content_rects is None or not dpg.does_item_exist(self.overlay_id):
            return False

        content_pos = dpg.get_item_pos(self.content_id)
        _, _, content_size = content_rects
        dpg.configure_item(
            self.overlay_id,
            pos=content_pos,
            width=max(1, int(content_size[0]) - self.OVERLAY_SCROLLBAR_GUTTER),
            height=max(1, int(content_size[1])),
            show=True,
        )
        return True

    def _update_overlay_positions(self):
        if not dpg.does_item_exist(self.content_id):
            return

        if not self._sync_overlay_window():
            return

        for ui in self._roi_ui.values():
            plot_id = ui.get("plot_id")
            name_id = ui.get("name_text_id")
            close_button_id = ui.get("close_button_id")
            if not plot_id or not name_id or not close_button_id:
                continue

            plot_rects = self._get_item_rects_in_content_space(plot_id)
            button_rects = self._get_item_rects(close_button_id)
            if plot_rects is None or button_rects is None:
                continue
            
            plot_rect_min, plot_rect_max, _ = plot_rects
            _, _, button_rect_size = button_rects
            button_width, _ = button_rect_size

            name_x = int(plot_rect_min[0] + 13)
            name_y = int(plot_rect_min[1] + 12)
            dpg.set_item_pos(name_id, (name_x, name_y))
            dpg.set_item_pos(
                close_button_id,
                (
                    int(plot_rect_max[0] - button_width - 14),
                    int(plot_rect_min[1] + 10),
                ),
            )

    def _get_roi_image_uv_bounds(self, image_shape):
        crop_height, crop_width = image_shape
        crop_height = max(1, int(crop_height))
        crop_width = max(1, int(crop_width))

        if crop_width > crop_height:
            visible_width = crop_height / crop_width
            margin = (1.0 - visible_width) / 2.0
            return (margin, 0.0), (1.0 - margin, 1.0)

        if crop_height > crop_width:
            visible_height = crop_width / crop_height
            margin = (1.0 - visible_height) / 2.0
            return (0.0, margin), (1.0, 1.0 - margin)

        return (0.0, 0.0), (1.0, 1.0)

    def rebuild_layout(self, rois):
        self._rois = list(rois)
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
        if dpg.does_item_exist(self.overlay_id):
            dpg.delete_item(self.overlay_id, children_only=True)

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
            dpg.bind_item_theme(dpg.last_item(), no_spacing_theme)
            dpg.bind_item_theme(dpg.last_item(), self._table_theme)
            dpg.add_table_column(label="Image", width_fixed=True, init_width_or_weight=self.ROI_IMAGE_DISPLAY_SIZE, no_resize=True)
            dpg.add_table_column(label="Graph", width_stretch=True)


            for roi in rois:
                self._roi_ui[roi.tag] = {}

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
                        dpg.add_spacer(height=self.ROI_IMAGE_VERTICAL_OFFSET)
                        uv_min, uv_max = self._get_roi_image_uv_bounds((roi.image_height, roi.image_width))
                        img_id = dpg.add_image(
                            texture_tag=tex_id,
                            width=self.ROI_IMAGE_DISPLAY_SIZE,
                            height=self.ROI_IMAGE_DISPLAY_SIZE,
                            uv_min=uv_min,
                            uv_max=uv_max,
                        )

                    # Graph cell
                    with dpg.group():
                        with dpg.plot(
                            height=self.ROW_HEIGHT,
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
                            y_axis_id = dpg.add_plot_axis(
                                dpg.mvYAxis,
                                label="",
                                opposite=True,
                            )
                            series_id = dpg.add_line_series([], [], label=roi.name, parent=y_axis_id)

                    dpg.bind_item_theme(plot_id, transparent_plot_theme)

                    self._roi_ui[roi.tag].update({
                        "texture_id": tex_id,
                        "image_id": img_id,
                        "plot_id": plot_id,
                        "x_axis_id": x_axis_id,
                        "y_axis_id": y_axis_id,
                        "series_id": series_id,
                    })

            # Extra row: duplicate last ROI's x-axis data with visible axis
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

        
        # Add in roi labels and close buttons on top of the images
        for roi in rois:
            name_id = dpg.add_text(roi.name, parent=self.overlay_id, pos=(0, 0))
            close_button_id = dpg.add_button(
                label="X",
                width=self.ROI_CLOSE_BUTTON_SIZE,
                height=self.ROI_CLOSE_BUTTON_SIZE,
                callback=self._on_close_roi_clicked,
                user_data=(roi.tag, roi.parent),
                parent=self.overlay_id,
                pos=(0, 0),
            )
            dpg.bind_item_theme(close_button_id, self._close_button_theme)
            self._roi_ui[roi.tag].update({
                "name_text_id": name_id,
                "close_button_id": close_button_id,
            })

        self._mark_y_axis_limits_dirty()
        self._update_overlay_positions()

    def render(self):
        self._refresh_autoscale_if_ready()
        self._apply_shared_y_axis_limits()
        self._update_overlay_positions()
        self._sync_xaxis()

    def _sync_xaxis(self):
        if not self._xaxis_ui or not self._roi_ui:
            return

        # Find global x range from all ROIs
        x_min = float('inf')
        x_max = float('-inf')
        last_x = []
        for ui in self._roi_ui.values():
            series_id = ui.get("series_id")
            if series_id and dpg.does_item_exist(series_id):
                data = dpg.get_value(series_id)
                if data and len(data) >= 2 and data[0]:
                    x_vals = data[0]
                    x_min = min(x_min, x_vals[0])
                    x_max = max(x_max, x_vals[-1])
                    last_x = data[0]

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
            trace_min_value = float(roi.trace_min_value)
            trace_max_value = float(roi.trace_max_value)

        if pending_version != roi.applied_version:
            self._ensure_roi_texture_shape(roi, ui, pending_shape)
            if pending_image_rgba is not None and dpg.does_item_exist(ui["texture_id"]):
                dpg.set_value(ui["texture_id"], pending_image_rgba)
            if dpg.does_item_exist(ui["image_id"]):
                uv_min, uv_max = self._get_roi_image_uv_bounds(pending_shape)
                dpg.configure_item(ui["image_id"], uv_min=uv_min, uv_max=uv_max)
            if dpg.does_item_exist(ui["series_id"]):
                dpg.set_value(ui["series_id"], [pending_plot_x, pending_plot_y])
            if roi.tag in self._autoscale_pending_tags:
                self._autoscale_pending_tags.discard(roi.tag)
            if self.y_scale_auto and not self._autoscale_dirty:
                if self.y_scale_mirrored:
                    if max(abs(trace_min_value), abs(trace_max_value)) > self.y_scale_max:
                        self._set_y_scale_auto_limits(*self._compute_global_trace_limits())
                elif trace_min_value < self.y_scale_min or trace_max_value > self.y_scale_max:
                    self._set_y_scale_auto_limits(*self._compute_global_trace_limits())
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
            {
                "window": capture_window_state(self.window_id),
                "trace_metric": self.trace_metric,
                "y_scale_min": self.y_scale_min,
                "y_scale_max": self.y_scale_max,
                "y_scale_auto": self.y_scale_auto,
                "y_scale_mirrored": self.y_scale_mirrored,
            },
        )

    def LoadState(self):
        state = load_state_file(self._state_name())
        if not state:
            return
        self.trace_metric = str(state.get("trace_metric", self.trace_metric))
        if self.trace_metric not in self.TRACE_METRIC_OPTIONS:
            self.trace_metric = self.TRACE_METRIC_OPTIONS[0]
        self.y_scale_min = float(state.get("y_scale_min", self.y_scale_min))
        self.y_scale_max = max(float(state.get("y_scale_max", self.y_scale_max)), 1.0)
        self.y_scale_auto = bool(state.get("y_scale_auto", self.y_scale_auto))
        self.y_scale_mirrored = bool(state.get("y_scale_mirrored", self.y_scale_mirrored))
        if self.y_scale_mirrored:
            self.y_scale_min = -abs(self.y_scale_max)
        dpg.set_value(self.trace_metric_combo_id, self.trace_metric)
        dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
        dpg.set_value(self.y_scale_max_input_id, self.y_scale_max)
        dpg.set_value(self.y_scale_auto_checkbox_id, self.y_scale_auto)
        dpg.set_value(self.y_scale_mirrored_checkbox_id, self.y_scale_mirrored)
        self._sync_y_scale_inputs()
        self._mark_y_axis_limits_dirty()
        apply_window_state(self.window_id, state.get("window"))
