import dearpygui.dearpygui as dpg

import Utils.shared_state as shared_state
from Utils.shared_state import class_objects
from Utils.fonts import get_segmdl2_icon_font
from Utils.state_persistence import load_state_file, save_state_file
from Utils.themes import selected_theme, no_padding_theme

# Layout constants
TOOLBAR_H = 52
LEFT_SIDEBAR_W = 285
RIGHT_SIDEBAR_W = 285
RIGHT_POSITION_MAP_H = 290
DRAG_HANDLE_H = 6
DRAG_HANDLE_COLOR = [55, 58, 60, 255]

DEFAULT_CENTER_FEED_H = 520
DEFAULT_RIGHT_ROIS_H = 300
DEFAULT_RIGHT_SCOPE_H = 200


class AppLayout:

    def __init__(self):
        self._center_feed_h = DEFAULT_CENTER_FEED_H
        self._right_rois_h = DEFAULT_RIGHT_ROIS_H
        self._right_scope_h = DEFAULT_RIGHT_SCOPE_H

        self._dragging_handle = None
        self._last_mouse_y = 0.0
        self._last_viewport_h = 0
        self._skip_height_recalc = False

        self._wired = False
        self._cam = None
        self._laser = None
        self._feed = None

        icon_font_small = get_segmdl2_icon_font(12)
        icon_font = get_segmdl2_icon_font(18)

        with dpg.theme() as self._drag_handle_theme:
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, DRAG_HANDLE_COLOR)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)

        with dpg.theme() as self._toolbar_theme:
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, [30, 30, 30, 255])
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 10)

        with dpg.theme() as self._active_btn_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, [0, 124, 80])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, [0, 150, 96])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, [0, 100, 65])

        with dpg.theme() as self._icon_btn_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)

        with dpg.theme() as self._toolbar_label_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,        [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   [0, 0, 0, 0])
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 10)

        with dpg.theme() as self._toolbar_ctrl_theme:
            with dpg.theme_component(dpg.mvSliderFloat):
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 11)
            with dpg.theme_component(dpg.mvInputFloat):
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 11)

        with dpg.handler_registry(tag="AppLayoutKeyHandlers"):
            dpg.add_key_press_handler(key=dpg.mvKey_Spacebar, callback=self._on_spacebar)

        # MainWindow gets a window-only zero-padding theme so it doesn't cascade
        # to child windows and override the toolbar's own padding theme.
        with dpg.theme() as self._main_window_theme:
            with dpg.theme_component(dpg.mvWindowAppItem):
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)

        with dpg.window(tag="MainWindow"):
            dpg.bind_item_theme(dpg.last_item(), self._main_window_theme)
            self._build_toolbar(icon_font)
            self._build_content_area()
        dpg.set_primary_window("MainWindow", True)

        # Publish container tags for other window classes to use
        shared_state.layout_containers = {
            # Left sidebar — each value is the scrollable child_window inside the tab
            "camera_tab":            "LeftContent_Camera",
            "feed_tab":              "LeftContent_Feed",
            "laser_tab":             "LeftContent_Laser",
            "picoscope_tab":         "LeftContent_PicoScope",
            "stage_tab":             "LeftContent_Stage",
            "mock_camera_tab":       "LeftContent_MockCamera",
            "preview_settings_tab":  "LeftContent_PreviewSettings",
            # Tab item tags (used by visibility logic to enable/disable)
            "_tab_camera":           "LeftTab_Camera",
            "_tab_feed":             "LeftTab_Feed",
            "_tab_laser":            "LeftTab_Laser",
            "_tab_picoscope":        "LeftTab_PicoScope",
            "_tab_stage":            "LeftTab_Stage",
            "_tab_mock_camera":      "LeftTab_MockCamera",
            "_tab_preview_settings": "LeftTab_PreviewSettings",
            "_left_tab_bar":         "LeftTabBar",
            # Center
            "center_tab_bar":        "CenterTabBar",
            "center_live_tab":       "CenterLiveFeedContainer",
            "center_bottom_tab_bar": "CenterBottomTabBar",
            "console_tab":           "CenterBottomContent_Console",
            "files_tab":             "CenterBottomContent_Files",
            # Right sidebar
            "right_rois":            "RightROIs",
            "right_scope":           "RightScope",
            "right_position_map":    "RightPositionMap",
        }

    # ------------------------------------------------------------------
    # Layout building
    # ------------------------------------------------------------------

    def _build_toolbar(self, icon_font):
        with dpg.child_window(
            height=TOOLBAR_H,
            tag="MainToolbar",
            no_scrollbar=True,
            border=False,
        ):
            dpg.bind_item_theme(dpg.last_item(), self._toolbar_theme)

            with dpg.group(horizontal=True):
                # Preview / Stop Preview
                self._preview_btn = dpg.add_button(
                    label=chr(0xE768),
                    tag="Toolbar_Preview",
                    width=36, height=36,
                    callback=self._on_preview,
                )
                dpg.bind_item_font(self._preview_btn, icon_font)
                dpg.bind_item_theme(self._preview_btn, self._icon_btn_theme)
                with dpg.tooltip(self._preview_btn):
                    dpg.add_text("Start / Stop Preview")

                # Acquire / Stop Acquire
                self._acquire_btn = dpg.add_button(
                    label=chr(0xE7C8),
                    tag="Toolbar_Acquire",
                    width=36, height=36,
                    callback=self._on_acquire,
                )
                dpg.bind_item_font(self._acquire_btn, icon_font)
                dpg.bind_item_theme(self._acquire_btn, self._icon_btn_theme)
                with dpg.tooltip(self._acquire_btn):
                    dpg.add_text("Start / Stop Acquisition")

                # Snapshot
                self._snapshot_btn = dpg.add_button(
                    label=chr(0xE722),
                    tag="Toolbar_Snapshot",
                    width=36, height=36,
                    callback=self._on_snapshot,
                )
                dpg.bind_item_font(self._snapshot_btn, icon_font)
                dpg.bind_item_theme(self._snapshot_btn, self._icon_btn_theme)
                with dpg.tooltip(self._snapshot_btn):
                    dpg.add_text("Snapshot")

                # Save (next to other acquisition buttons)
                self._save_btn = dpg.add_button(
                    label=chr(0xE74E),
                    tag="Toolbar_Save",
                    width=36, height=36,
                    callback=self._on_save,
                )
                dpg.bind_item_font(self._save_btn, icon_font)
                dpg.bind_item_theme(self._save_btn, self._icon_btn_theme)
                with dpg.tooltip(self._save_btn):
                    dpg.add_text("Save Acquisition")

                self._toolbar_separator()

                # Laser toggle
                self._laser_btn = dpg.add_button(
                    label=chr(0xE945),
                    tag="Toolbar_Laser",
                    width=36, height=36,
                    callback=self._on_laser_toggle,
                )
                dpg.bind_item_font(self._laser_btn, icon_font)
                dpg.bind_item_theme(self._laser_btn, self._icon_btn_theme)
                with dpg.tooltip(self._laser_btn):
                    dpg.add_text("Laser On / Off")

                # Laser power slider
                _lbl = dpg.add_button(label="Power", enabled=False)
                dpg.bind_item_theme(_lbl, self._toolbar_label_theme)
                self._laser_power_slider = dpg.add_slider_float(
                    tag="Toolbar_LaserPower",
                    width=160,
                    min_value=0.0,
                    max_value=200.0,
                    format="%.1f mW",
                    callback=self._on_laser_power,
                )
                dpg.bind_item_theme(self._laser_power_slider, self._toolbar_ctrl_theme)

                self._toolbar_separator()

                # Exposure time
                _lbl2 = dpg.add_button(label="Exp", enabled=False)
                dpg.bind_item_theme(_lbl2, self._toolbar_label_theme)
                self._exposure_input = dpg.add_input_float(
                    tag="Toolbar_Exposure",
                    width=160,
                    min_value=0.001,
                    max_value=1.0,
                    step=0.001,
                    format="%.3f s",
                    on_enter=True,
                    callback=self._on_exposure,
                )
                dpg.bind_item_theme(self._exposure_input, self._toolbar_ctrl_theme)

                self._toolbar_separator()

                # AutoScale toggle
                self._autoscale_btn = dpg.add_button(
                    label=chr(0xE81E),
                    tag="Toolbar_AutoScale",
                    width=36, height=36,
                    callback=self._on_autoscale_toggle,
                )
                dpg.bind_item_font(self._autoscale_btn, icon_font)
                dpg.bind_item_theme(self._autoscale_btn, self._icon_btn_theme)
                with dpg.tooltip(self._autoscale_btn):
                    dpg.add_text("Auto Scale On / Off")

                # Scale min
                _lbl3 = dpg.add_button(label="Min", enabled=False)
                dpg.bind_item_theme(_lbl3, self._toolbar_label_theme)
                self._scale_min_slider = dpg.add_slider_float(
                    tag="Toolbar_ScaleMin",
                    width=160,
                    min_value=0.0,
                    max_value=100.0,
                    format="%.1f%%",
                    callback=self._on_scale_min,
                )
                dpg.bind_item_theme(self._scale_min_slider, self._toolbar_ctrl_theme)

                # Scale max
                _lbl4 = dpg.add_button(label="Max", enabled=False)
                dpg.bind_item_theme(_lbl4, self._toolbar_label_theme)
                self._scale_max_slider = dpg.add_slider_float(
                    tag="Toolbar_ScaleMax",
                    width=160,
                    min_value=0.0,
                    max_value=100.0,
                    default_value=100.0,
                    format="%.1f%%",
                    callback=self._on_scale_max,
                )
                dpg.bind_item_theme(self._scale_max_slider, self._toolbar_ctrl_theme)


    def _toolbar_separator(self):
        dpg.add_spacer(width=6)
        with dpg.drawlist(width=1, height=36):
            dpg.draw_line([0, 4], [0, 32], color=[80, 80, 80, 200], thickness=1)
        dpg.add_spacer(width=6)

    def _build_content_area(self):
        with dpg.child_window(height=-1, tag="ContentArea", no_scrollbar=True, no_scroll_with_mouse=True, border=False) as _ca:
            dpg.bind_item_theme(_ca, no_padding_theme)
            with dpg.table(
                header_row=False,
                resizable=True,
                borders_innerV=True,
                tag="MainContentTable",
                height=-1,
                policy=dpg.mvTable_SizingFixedFit,
            ):
                dpg.add_table_column(
                    label="Left",
                    init_width_or_weight=LEFT_SIDEBAR_W,
                    width_fixed=True,
                )
                dpg.add_table_column(label="Center", width_stretch=True)
                dpg.add_table_column(
                    label="Right",
                    init_width_or_weight=RIGHT_SIDEBAR_W,
                    width_fixed=True,
                )

                with dpg.table_row():
                    self._build_left_sidebar()
                    self._build_center()
                    self._build_right_sidebar()

    def _build_left_sidebar(self):
        with dpg.table_cell():
            with dpg.child_window(height=-1, tag="LeftSidebar", border=False, no_scrollbar=True, no_scroll_with_mouse=True):
                with dpg.tab_bar(tag="LeftTabBar"):
                    self._left_tab("Camera",   "LeftTab_Camera",   "LeftContent_Camera")
                    self._left_tab("Feed",     "LeftTab_Feed",     "LeftContent_Feed")
                    self._left_tab("Laser",    "LeftTab_Laser",    "LeftContent_Laser")
                    self._left_tab("PicoScope","LeftTab_PicoScope","LeftContent_PicoScope")
                    self._left_tab("Stage",    "LeftTab_Stage",    "LeftContent_Stage")

                    if shared_state.dev_mode:
                        self._left_tab("Mock Cam", "LeftTab_MockCamera", "LeftContent_MockCamera")
                    else:
                        with dpg.tab(label="Mock Cam", tag="LeftTab_MockCamera", show=False):
                            with dpg.child_window(tag="LeftContent_MockCamera", height=-1, border=False):
                                pass

                    with dpg.tab(label="Preview", tag="LeftTab_PreviewSettings", show=False):
                        with dpg.child_window(tag="LeftContent_PreviewSettings", height=-1, border=False):
                            pass

    def _left_tab(self, label, tab_tag, content_tag):
        with dpg.tab(label=label, tag=tab_tag):
            with dpg.child_window(tag=content_tag, height=-1, border=False):
                pass

    def _build_center(self):
        with dpg.table_cell():
            # Top: live feed + file tabs
            with dpg.child_window(
                height=self._center_feed_h,
                tag="CenterTopSection",
                no_scrollbar=True,
                no_scroll_with_mouse=True,
                border=False,
            ):
                with dpg.tab_bar(tag="CenterTabBar"):
                    with dpg.tab(label="Live Feed", tag="CenterTab_Live", closable=False):
                        with dpg.child_window(
                            tag="CenterLiveFeedContainer",
                            height=-1,
                            border=False,
                            no_scrollbar=True,
                        ):
                            pass

            # Drag handle
            self._make_drag_handle("CenterDragHandle")

            # Bottom: console + files
            with dpg.child_window(
                height=self._center_feed_h,  # corrected in first render
                tag="CenterBottomSection",
                no_scrollbar=True,
                no_scroll_with_mouse=True,
                border=False,
            ):
                with dpg.tab_bar(tag="CenterBottomTabBar"):
                    with dpg.tab(label="Console", tag="CenterBottomTab_Console"):
                        with dpg.child_window(
                            tag="CenterBottomContent_Console",
                            height=-1,
                            border=False,
                        ):
                            pass
                    with dpg.tab(label="Files", tag="CenterBottomTab_Files"):
                        with dpg.child_window(
                            tag="CenterBottomContent_Files",
                            height=-1,
                            border=False,
                        ):
                            pass

    def _build_right_sidebar(self):
        with dpg.table_cell():
            with dpg.child_window(height=-1, tag="RightSidebar", border=False, no_scrollbar=True, no_scroll_with_mouse=True):
                with dpg.child_window(height=self._right_rois_h, tag="RightROIs", border=True):
                    pass
                self._make_drag_handle("RightDragHandle1")
                with dpg.child_window(height=self._right_scope_h, tag="RightScope", border=True):
                    pass
                self._make_drag_handle("RightDragHandle2")
                with dpg.child_window(height=-1, tag="RightPositionMap", border=True):
                    pass

    def _make_drag_handle(self, tag):
        with dpg.child_window(height=DRAG_HANDLE_H, tag=tag, no_scrollbar=True, no_scroll_with_mouse=True, border=False):
            dpg.bind_item_theme(dpg.last_item(), self._drag_handle_theme)

    # ------------------------------------------------------------------
    # Render loop
    # ------------------------------------------------------------------

    def render(self):
        self._update_panel_heights()
        self._update_drag_handles()
        self._update_tab_visibility()
        if not self._wired:
            self._wire_toolbar()

    def _update_panel_heights(self):
        """Keep derived panel heights correct when the viewport is resized."""
        vh = dpg.get_viewport_client_height()
        if vh == self._last_viewport_h:
            return
        self._last_viewport_h = vh

        available = max(100, vh - TOOLBAR_H - 4)

        # On the first render after LoadState restored heights, skip proportional
        # rescale so the restored values aren't overwritten — but still apply
        # derived heights (bottom section, scope) using the now-valid viewport size.
        if self._skip_height_recalc:
            self._skip_height_recalc = False
            self._refresh_derived_heights(available)
            return

        # Scale center split proportionally
        ratio = self._center_feed_h / max(1, self._center_feed_h + self._center_bottom_h())
        new_feed_h = max(80, int(available * ratio))
        self._center_feed_h = new_feed_h
        dpg.configure_item("CenterTopSection", height=self._center_feed_h)

        # Recompute scope so right sidebar fills correctly
        self._recalc_right_scope_h(available)

        self._refresh_derived_heights(available)

    def _center_bottom_h(self):
        vh = dpg.get_viewport_client_height()
        return max(60, vh - TOOLBAR_H - 4 - self._center_feed_h - DRAG_HANDLE_H)

    def _recalc_right_scope_h(self, available):
        # Leave 150 px minimum for the position map (which fills remaining space via height=-1)
        scope_h = available - self._right_rois_h - DRAG_HANDLE_H * 2 - 150
        self._right_scope_h = max(60, scope_h)

    def _refresh_derived_heights(self, available=None):
        if available is None:
            available = max(100, dpg.get_viewport_client_height() - TOOLBAR_H - 4)

        bottom_h = max(60, available - self._center_feed_h - DRAG_HANDLE_H)
        if dpg.does_item_exist("CenterBottomSection"):
            dpg.configure_item("CenterBottomSection", height=bottom_h)

        scope_h = max(60, available - self._right_rois_h - DRAG_HANDLE_H * 2 - 150)
        self._right_scope_h = scope_h
        if dpg.does_item_exist("RightScope"):
            dpg.configure_item("RightScope", height=scope_h)
        # RightPositionMap uses height=-1 and fills remaining space automatically

    def _update_drag_handles(self):
        mouse_pos = dpg.get_mouse_pos(local=False)
        mouse_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        dy = mouse_pos[1] - self._last_mouse_y
        self._last_mouse_y = mouse_pos[1]

        if not mouse_down:
            self._dragging_handle = None
            return

        # Start a new drag only if no handle is currently being dragged
        if self._dragging_handle is None:
            for handle in ("CenterDragHandle", "RightDragHandle1", "RightDragHandle2"):
                if dpg.does_item_exist(handle) and dpg.is_item_hovered(handle):
                    self._dragging_handle = handle
                    break

        if self._dragging_handle is None or dy == 0:
            return

        available = max(100, dpg.get_viewport_client_height() - TOOLBAR_H - 4)

        if self._dragging_handle == "CenterDragHandle":
            self._center_feed_h = max(80, min(available - DRAG_HANDLE_H - 60, self._center_feed_h + int(dy)))
            dpg.configure_item("CenterTopSection", height=self._center_feed_h)
            self._refresh_derived_heights(available)

        elif self._dragging_handle == "RightDragHandle1":
            self._right_rois_h = max(60, self._right_rois_h + int(dy))
            dpg.configure_item("RightROIs", height=self._right_rois_h)
            self._refresh_derived_heights(available)

        elif self._dragging_handle == "RightDragHandle2":
            self._right_scope_h = max(60, self._right_scope_h + int(dy))
            dpg.configure_item("RightScope", height=self._right_scope_h)

    def _update_tab_visibility(self):
        if not dpg.does_item_exist("CenterTabBar"):
            return

        active = dpg.get_value("CenterTabBar")
        is_file = isinstance(active, str) and active.startswith("CenterFileTab_")

        control_tabs = [
            "LeftTab_Camera", "LeftTab_Feed", "LeftTab_Laser",
            "LeftTab_PicoScope", "LeftTab_Stage", "LeftTab_MockCamera",
        ]
        for tab in control_tabs:
            if dpg.does_item_exist(tab):
                dpg.configure_item(tab, show=not is_file)

        if dpg.does_item_exist("LeftTab_PreviewSettings"):
            dpg.configure_item("LeftTab_PreviewSettings", show=is_file)
            if is_file and dpg.does_item_exist("LeftTabBar"):
                dpg.set_value("LeftTabBar", "LeftTab_PreviewSettings")

        if not is_file and dpg.does_item_exist("LeftTabBar"):
            current_left = dpg.get_value("LeftTabBar")
            if current_left == "LeftTab_PreviewSettings" and dpg.does_item_exist("LeftTab_Camera"):
                dpg.set_value("LeftTabBar", "LeftTab_Camera")

        # Keep toolbar sync
        self._sync_toolbar_state()

    def _sync_toolbar_state(self):
        if not self._wired:
            return

        cam = self._cam
        if cam is None:
            return

        previewing = bool(getattr(cam, "started", False))
        acquiring = bool(getattr(cam, "acquisition_in_progress", False))

        if dpg.does_item_exist(self._preview_btn):
            if previewing:
                dpg.bind_item_theme(self._preview_btn, self._active_btn_theme)
            else:
                dpg.bind_item_theme(self._preview_btn, self._icon_btn_theme)

        if dpg.does_item_exist(self._acquire_btn):
            if acquiring:
                dpg.bind_item_theme(self._acquire_btn, self._active_btn_theme)
            else:
                dpg.bind_item_theme(self._acquire_btn, self._icon_btn_theme)

        laser = self._laser
        if laser is not None:
            emission = False
            try:
                emission = bool(laser.laser.get_state().get("emission_enabled", False))
            except Exception:
                pass
            if dpg.does_item_exist(self._laser_btn):
                if emission:
                    dpg.bind_item_theme(self._laser_btn, self._active_btn_theme)
                else:
                    dpg.bind_item_theme(self._laser_btn, self._icon_btn_theme)

        feed = self._feed
        if feed is None and self._cam:
            feed = getattr(self._cam, "camera_feed", None)
        if feed is not None and dpg.does_item_exist(self._autoscale_btn):
            autoscale = bool(getattr(feed, "autoscale_enabled", False))
            dpg.bind_item_theme(
                self._autoscale_btn,
                self._active_btn_theme if autoscale else self._icon_btn_theme,
            )

    # ------------------------------------------------------------------
    # Toolbar wiring (called once on first render when class_objects ready)
    # ------------------------------------------------------------------

    def _wire_toolbar(self):
        if not class_objects:
            return

        self._cam = self._find("CameraSystem")
        self._laser = self._find("LaserControls")
        self._feed = None

        cam = self._cam
        if cam and hasattr(cam, "camera_feed"):
            self._feed = cam.camera_feed

        if cam:
            try:
                exposure = float(getattr(cam.Andor.camera, "ExposureTime", 0.01))
                dpg.set_value("Toolbar_Exposure", exposure)
            except Exception:
                pass

        laser = self._laser
        if laser:
            try:
                state = laser.laser.get_state()
                dpg.set_value("Toolbar_LaserPower", float(state.get("target_power_mw", 0.0)))
            except Exception:
                pass

        feed = self._feed
        if feed:
            try:
                dpg.set_value("Toolbar_AutoScale", feed.autoscale_enabled)
                dpg.set_value("Toolbar_ScaleMin", feed.get_scale_min_percent())
                dpg.set_value("Toolbar_ScaleMax", feed.get_scale_max_percent())
            except Exception:
                pass

        self._wired = True

    def _find(self, classname):
        return next((o for o in class_objects if type(o).__name__ == classname), None)

    # ------------------------------------------------------------------
    # Toolbar callbacks
    # ------------------------------------------------------------------

    def _on_spacebar(self, sender=None, app_data=None):
        self._on_preview()

    def _on_preview(self):
        cam = self._cam or self._find("CameraSystem")
        if cam and hasattr(cam, "toggle_preview"):
            cam.toggle_preview()

    def _on_acquire(self):
        cam = self._cam or self._find("CameraSystem")
        if cam:
            if getattr(cam, "acquisition_in_progress", False):
                cam.acquisition_stop_requested = True
            elif hasattr(cam, "_on_acquire_button_pressed"):
                cam._on_acquire_button_pressed()

    def _on_snapshot(self):
        cam = self._cam or self._find("CameraSystem")
        if cam and hasattr(cam, "_on_snapshot_pressed"):
            cam._on_snapshot_pressed()

    def _on_laser_toggle(self):
        laser = self._laser or self._find("LaserControls")
        if laser and hasattr(laser, "_toggle_emission"):
            laser._toggle_emission()

    def _on_laser_power(self, sender, value):
        laser = self._laser or self._find("LaserControls")
        if laser:
            try:
                dpg.set_value(laser.target_power_source, float(value))
                if hasattr(laser, "_on_power_changed"):
                    laser._on_power_changed(sender, float(value), None)
            except Exception:
                pass

    def _on_exposure(self, sender, value):
        cam = self._cam or self._find("CameraSystem")
        if cam and hasattr(cam, "settings_exposure_time"):
            try:
                dpg.set_value(cam.settings_exposure_time, float(value))
                cam.setprop("ExposureTime", cam.settings_exposure_time)
            except Exception:
                pass

    def _on_autoscale_toggle(self):
        feed = self._feed
        if feed is None:
            cam = self._cam or self._find("CameraSystem")
            if cam and hasattr(cam, "camera_feed"):
                feed = cam.camera_feed
        if feed:
            try:
                new_val = not feed.autoscale_enabled
                feed.autoscale_enabled = new_val
                cw = getattr(feed, "controls_window", None)
                if cw and hasattr(cw, "autoscale_checkbox_id"):
                    dpg.set_value(cw.autoscale_checkbox_id, new_val)
                    feed._on_autoscale_changed()
            except Exception:
                pass

    def _on_scale_min(self, sender, value):
        feed = self._feed
        if feed is None:
            cam = self._cam or self._find("CameraSystem")
            if cam and hasattr(cam, "camera_feed"):
                feed = cam.camera_feed
        if feed:
            try:
                cw = getattr(feed, "controls_window", None)
                if cw and hasattr(cw, "scale_min_input_id"):
                    dpg.set_value(cw.scale_min_input_id, float(value))
                    feed._on_scale_limits_changed(cw.scale_min_input_id, float(value))
            except Exception:
                pass

    def _on_scale_max(self, sender, value):
        feed = self._feed
        if feed is None:
            cam = self._cam or self._find("CameraSystem")
            if cam and hasattr(cam, "camera_feed"):
                feed = cam.camera_feed
        if feed:
            try:
                cw = getattr(feed, "controls_window", None)
                if cw and hasattr(cw, "scale_max_input_id"):
                    dpg.set_value(cw.scale_max_input_id, float(value))
                    feed._on_scale_limits_changed(cw.scale_max_input_id, float(value))
            except Exception:
                pass

    def _on_save(self):
        cam = self._cam or self._find("CameraSystem")
        if cam and hasattr(cam, "_show_save_dialog"):
            cam._show_save_dialog()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def SaveState(self):
        save_state_file("AppLayout", {
            "center_feed_h": self._center_feed_h,
            "right_rois_h":  self._right_rois_h,
            "right_scope_h": self._right_scope_h,
        })

    def LoadState(self):
        state = load_state_file("AppLayout")
        if not state:
            return
        self._center_feed_h = int(state.get("center_feed_h", DEFAULT_CENTER_FEED_H))
        self._right_rois_h  = int(state.get("right_rois_h",  DEFAULT_RIGHT_ROIS_H))
        self._right_scope_h = int(state.get("right_scope_h", DEFAULT_RIGHT_SCOPE_H))
        if dpg.does_item_exist("CenterTopSection"):
            dpg.configure_item("CenterTopSection", height=self._center_feed_h)
        if dpg.does_item_exist("RightROIs"):
            dpg.configure_item("RightROIs", height=self._right_rois_h)
        if dpg.does_item_exist("RightScope"):
            dpg.configure_item("RightScope", height=self._right_scope_h)
        # Derived heights (bottom section, scope fill) are applied on first render
        # once the viewport has a valid size — see _update_panel_heights.
        self._skip_height_recalc = True
