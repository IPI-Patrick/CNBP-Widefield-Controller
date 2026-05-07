import dearpygui.dearpygui as dpg
import numpy as np

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import no_spacing_theme, transparent_plot_theme


class ROIsWindow:

    ROW_HEIGHT = 120
    XAXIS_ROW_HEIGHT = 30
    XAXIS_DEBUG_VISIBLE_HEIGHT = 40
    ROI_IMAGE_SIZE = ROW_HEIGHT
    ROI_IMAGE_VERTICAL_OFFSET = 5
    ROI_IMAGE_DISPLAY_SIZE = ROW_HEIGHT - (ROI_IMAGE_VERTICAL_OFFSET * 2) - 9
    OVERLAY_SCROLLBAR_GUTTER = 18
    OVERLAY_WHEEL_SCROLL_STEP = 24
    ROI_CLOSE_BUTTON_SIZE = 20
    TRACE_METRIC_OPTIONS = ("Mean", "Max", "Max & Min")
    DEFAULT_Y_SCALE_GRACE_PERCENT = 5.0
    EMPTY_CONTENT_HEIGHT = 1
    CONTENT_BOTTOM_PADDING = 8
    

    def __init__(self, *, name="ROIs", tag="ROIsWindow", width=640, height=400, pos=(935, 10), state_name=None, parent=None, controls_parent=None, content_parent=None):
        self.width = int(width)
        self.height = int(height)
        self.name = str(name)
        self.tag = str(tag)
        self._state_name_value = str(state_name or type(self).__name__)
        self.parent_id = parent
        self.controls_parent_id = controls_parent
        self.content_parent_id = content_parent
        self.is_split_embedded = controls_parent is not None or content_parent is not None
        self.is_embedded = parent is not None or self.is_split_embedded
        self._current_roi_tags = []
        self._rois = []
        self._roi_ui = {}
        self._xaxis_ui = None
        self.y_scale_min = 0.0
        self.y_scale_max = 1.0
        self.y_scale_auto = True
        self.y_scale_mirrored = False
        self.y_scale_grace_percent = self.DEFAULT_Y_SCALE_GRACE_PERCENT
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

        with dpg.theme() as self._xaxis_debug_y_axis_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvPlotCol_AxisText, [255, 255, 255, 24], category=dpg.mvThemeCat_Plots)

        with dpg.theme() as self._marker_series_theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, [220, 40, 40, 255], category=dpg.mvThemeCat_Plots)

        if self.is_split_embedded:
            if self.controls_parent_id is not None:
                self._build_controls(parent=self.controls_parent_id)
            with dpg.child_window(
                parent=self.content_parent_id,
                tag=f"{self.tag}_Embedded",
                width=-1,
                height=self.height,
                border=False,
                no_scrollbar=True,
                no_scroll_with_mouse=True,
            ):
                self.window_id = dpg.last_item()
                self._build_content_ui()
        elif self.is_embedded:
            with dpg.child_window(
                parent=self.parent_id,
                tag=f"{self.tag}_Embedded",
                width=-1,
                height=self.height,
                border=False,
                no_scrollbar=True,
                no_scroll_with_mouse=True,
            ):
                self.window_id = dpg.last_item()
                self._build_controls()
                dpg.add_separator()
                self._build_content_ui()
        else:
            with dpg.window(
                label=self.name,
                tag=f"{self.tag}_Window",
                width=self.width,
                height=self.height,
                pos=pos,
                no_scrollbar=True,
                no_scroll_with_mouse=True,
                no_resize=False,
            ):
                self.window_id = dpg.last_item()
                self._build_controls()
                dpg.add_separator()
                self._build_content_ui()

        self._sync_y_scale_inputs()

    def _build_controls(self, parent=None):
        group_kwargs = {"parent": parent} if parent is not None else {}
        with dpg.group(**group_kwargs):
            dpg.add_text("Y-Axis Scaling")
            self.trace_metric_combo_id = dpg.add_combo(
                items=list(self.TRACE_METRIC_OPTIONS),
                label="Metric",
                width=-60,
                default_value=self.trace_metric,
                callback=self._on_trace_metric_changed,
            )
            self.y_scale_min_input_id = dpg.add_input_float(
                label="Min",
                width=-60,
                default_value=self.y_scale_min,
                callback=self._on_y_scale_min_changed,
            )
            self.y_scale_max_input_id = dpg.add_input_float(
                label="Max",
                width=-60,
                default_value=self.y_scale_max,
                callback=self._on_y_scale_max_changed,
            )
            self.y_scale_auto_checkbox_id = dpg.add_checkbox(
                label="Auto",
                default_value=self.y_scale_auto,
                callback=self._on_y_scale_auto_changed,
            )
            self.y_scale_grace_input_id = dpg.add_input_float(
                label="Grace (%)",
                width=-60,
                default_value=self.y_scale_grace_percent,
                callback=self._on_y_scale_grace_changed,
            )
            self.y_scale_mirrored_checkbox_id = dpg.add_checkbox(
                label="Mirrored",
                default_value=self.y_scale_mirrored,
                callback=self._on_y_scale_mirrored_changed,
            )

    def _build_content_ui(self):
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

    def get_required_content_height(self):
        if not self._rois:
            return self.EMPTY_CONTENT_HEIGHT
        return int((len(self._rois) * self.ROW_HEIGHT) + self.XAXIS_DEBUG_VISIBLE_HEIGHT + self.CONTENT_BOTTOM_PADDING)

    def is_visible(self):
        return dpg.does_item_exist(self.window_id) and dpg.is_item_shown(self.window_id)

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
        self.y_scale_max = float(app_data)
        if self.y_scale_max < self.y_scale_min:
            self.y_scale_min = self.y_scale_max
            dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
        self._mark_y_axis_limits_dirty()

    def _on_y_scale_grace_changed(self, sender, app_data, user_data=None):
        self.y_scale_grace_percent = max(float(app_data), 0.0)
        dpg.set_value(self.y_scale_grace_input_id, self.y_scale_grace_percent)
        if self.y_scale_auto:
            self.invalidate_autoscale_cache()
            self._recalculate_y_scale()

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
        min_enabled = not self.y_scale_auto
        max_enabled = not self.y_scale_auto
        grace_enabled = self.y_scale_auto
        dpg.configure_item(self.y_scale_min_input_id, enabled=min_enabled)
        dpg.configure_item(self.y_scale_max_input_id, enabled=max_enabled)
        dpg.configure_item(self.y_scale_grace_input_id, enabled=grace_enabled)

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

        self._mark_y_axis_limits_dirty()

    def _compute_global_trace_limits(self):
        trace_min_value = None
        trace_max_value = None
        for roi in self._rois:
            with roi.data_lock:
                roi_trace_min = float(roi.trace_min_value)
                roi_trace_max = float(roi.trace_max_value)
            trace_min_value = roi_trace_min if trace_min_value is None else min(trace_min_value, roi_trace_min)
            trace_max_value = roi_trace_max if trace_max_value is None else max(trace_max_value, roi_trace_max)

        if trace_min_value is None or trace_max_value is None:
            return 0.0, 1.0

        return trace_min_value, trace_max_value

    def _set_y_scale_auto_limits(self, scale_min, scale_max):
        scale_min = float(scale_min)
        scale_max = float(scale_max)
        grace_fraction = max(self.y_scale_grace_percent, 0.0) / 100.0

        if scale_max < scale_min:
            scale_min, scale_max = scale_max, scale_min

        data_range = scale_max - scale_min
        if data_range == 0.0:
            baseline = abs(scale_min)
            data_range = baseline if baseline > 0.0 else 1.0

        padding = data_range * grace_fraction

        if self.y_scale_mirrored:
            midpoint = (scale_min + scale_max) * 0.5
            half_range = max(scale_max - midpoint, midpoint - scale_min)
            if half_range == 0.0:
                half_range = data_range * 0.5
            half_range += padding
            self.y_scale_min = midpoint - half_range
            self.y_scale_max = midpoint + half_range
        else:
            self.y_scale_min = scale_min - padding
            self.y_scale_max = scale_max + padding

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

        y_min = self.y_scale_min
        y_max = self.y_scale_max
        if y_max <= y_min:
            y_max = y_min + 1.0

        for ui in self._roi_ui.values():
            y_axis_id = ui.get("y_axis_id")
            if y_axis_id and dpg.does_item_exist(y_axis_id):
                dpg.set_axis_limits(y_axis_id, y_min, y_max)

        if self._xaxis_ui:
            xaxis_y = self._xaxis_ui.get("y_axis_id")
            if xaxis_y and dpg.does_item_exist(xaxis_y):
                dpg.set_axis_limits(xaxis_y, y_min, y_max)

        self._y_axis_limits_dirty = False

    def _get_xaxis_alignment_value(self):
        trace_min_value, trace_max_value = self._compute_global_trace_limits()
        return max(abs(float(trace_min_value)), abs(float(trace_max_value)), 1.0)

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
                            marker_series_id = dpg.add_line_series([], [], label="", parent=y_axis_id)
                            dpg.bind_item_theme(marker_series_id, self._marker_series_theme)

                    dpg.bind_item_theme(plot_id, transparent_plot_theme)

                    self._roi_ui[roi.tag].update({
                        "texture_id": tex_id,
                        "image_id": img_id,
                        "plot_id": plot_id,
                        "x_axis_id": x_axis_id,
                        "y_axis_id": y_axis_id,
                        "series_id": series_id,
                        "marker_series_id": marker_series_id,
                        "x_range": None,
                    })

            # Extra row: duplicate last ROI's x-axis data with visible axis
            with dpg.table_row():
                dpg.add_spacer(height=self.XAXIS_ROW_HEIGHT)
                with dpg.plot(
                    height=self.XAXIS_DEBUG_VISIBLE_HEIGHT,
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
                    xaxis_y_id = dpg.add_plot_axis(dpg.mvYAxis, label="", no_label=True, opposite=True)
                    xaxis_series_id = dpg.add_line_series([], [], label="", parent=xaxis_y_id)
                    dpg.bind_item_theme(xaxis_y_id, self._xaxis_debug_y_axis_theme)

                dpg.bind_item_theme(xaxis_plot_id, transparent_plot_theme)

                self._xaxis_ui = {
                    "plot_id": xaxis_plot_id,
                    "x_axis_id": xaxis_x_id,
                    "y_axis_id": xaxis_y_id,
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
        if not self.is_visible():
            return
        self._refresh_autoscale_if_ready()
        self._apply_shared_y_axis_limits()
        self._update_roi_markers()
        self._update_overlay_positions()
        self._sync_xaxis()

    def _get_current_roi_marker_x(self):
        if not self._rois:
            return None
        parent = getattr(self._rois[0], "parent", None)
        if parent is None or not hasattr(parent, "get_current_roi_marker_x"):
            return None
        return parent.get_current_roi_marker_x()

    def _update_roi_markers(self):
        marker_x = self._get_current_roi_marker_x()
        if marker_x is None:
            for ui in self._roi_ui.values():
                marker_series_id = ui.get("marker_series_id")
                if marker_series_id and dpg.does_item_exist(marker_series_id):
                    dpg.set_value(marker_series_id, [[], []])
            return

        marker_x_values = [float(marker_x), float(marker_x)]
        marker_y_values = [float(self.y_scale_min), float(self.y_scale_max)]
        for ui in self._roi_ui.values():
            marker_series_id = ui.get("marker_series_id")
            if marker_series_id and dpg.does_item_exist(marker_series_id):
                dpg.set_value(marker_series_id, [marker_x_values, marker_y_values])

    def _sync_xaxis(self):
        if not self._xaxis_ui or not self._roi_ui:
            return

        # Find global x range from all ROIs
        x_min = float('inf')
        x_max = float('-inf')
        have_points = False
        for ui in self._roi_ui.values():
            x_range = ui.get("x_range")
            if x_range is None:
                continue
            x_min = min(x_min, float(x_range[0]))
            x_max = max(x_max, float(x_range[1]))
            have_points = True

        # Set the x-axis series to match the last ROI so axis range matches
        xaxis_series = self._xaxis_ui["series_id"]
        if have_points and dpg.does_item_exist(xaxis_series):
            alignment_value = self._get_xaxis_alignment_value()
            if x_min == x_max:
                xaxis_values = [x_min, x_min + 1.0]
            else:
                xaxis_values = [x_min, x_max]
            dpg.set_value(xaxis_series, [xaxis_values, [alignment_value, alignment_value]])

        if have_points:
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
                if pending_plot_x:
                    ui["x_range"] = (float(pending_plot_x[0]), float(pending_plot_x[-1]))
                else:
                    ui["x_range"] = None
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
        return self._state_name_value

    def SaveState(self):
        state = {
            "trace_metric": self.trace_metric,
            "y_scale_min": self.y_scale_min,
            "y_scale_max": self.y_scale_max,
            "y_scale_auto": self.y_scale_auto,
            "y_scale_mirrored": self.y_scale_mirrored,
            "y_scale_grace_percent": self.y_scale_grace_percent,
        }
        if not self.is_embedded:
            state["window"] = capture_window_state(self.window_id)
        save_state_file(self._state_name(), state)

    def LoadState(self):
        state = load_state_file(self._state_name())
        if not state:
            return
        self.trace_metric = str(state.get("trace_metric", self.trace_metric))
        if self.trace_metric not in self.TRACE_METRIC_OPTIONS:
            self.trace_metric = self.TRACE_METRIC_OPTIONS[0]
        self.y_scale_min = float(state.get("y_scale_min", self.y_scale_min))
        self.y_scale_max = float(state.get("y_scale_max", self.y_scale_max))
        self.y_scale_auto = bool(state.get("y_scale_auto", self.y_scale_auto))
        self.y_scale_mirrored = bool(state.get("y_scale_mirrored", self.y_scale_mirrored))
        self.y_scale_grace_percent = max(
            float(state.get("y_scale_grace_percent", self.y_scale_grace_percent)),
            0.0,
        )
        dpg.set_value(self.trace_metric_combo_id, self.trace_metric)
        dpg.set_value(self.y_scale_min_input_id, self.y_scale_min)
        dpg.set_value(self.y_scale_max_input_id, self.y_scale_max)
        dpg.set_value(self.y_scale_auto_checkbox_id, self.y_scale_auto)
        dpg.set_value(self.y_scale_grace_input_id, self.y_scale_grace_percent)
        dpg.set_value(self.y_scale_mirrored_checkbox_id, self.y_scale_mirrored)
        self._sync_y_scale_inputs()
        self._mark_y_axis_limits_dirty()
        if not self.is_embedded:
            apply_window_state(self.window_id, state.get("window"))
