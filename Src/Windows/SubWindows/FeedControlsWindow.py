import dearpygui.dearpygui as dpg
from Utils.state_persistence import apply_item_open_states, apply_window_state, capture_item_open_states, capture_window_state, load_state_file, save_state_file


class FeedControlsWindow:

    def __init__(self, parent):
        self.parent = parent
        self.section_node_ids = {}

        with dpg.window(
            label="Feed Controls",
            tag=f"{self.parent.tag}_ControlsWindow",
            width=300,
            height=420,
            pos=(10, 620),
            no_resize=False,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()
            with dpg.tree_node(label="Display Scaling", default_open=True, span_full_width=True) as display_scaling_node_id:
                self.section_node_ids["display_scaling"] = display_scaling_node_id
                self.autoscale_checkbox_id = dpg.add_checkbox(
                    label="Autoscale",
                    default_value=self.parent.autoscale_enabled,
                    callback=self.parent._on_autoscale_changed,
                )

                self.scale_min_input_id = dpg.add_input_float(
                    label="Min Z (%)",
                    width=-120,
                    default_value=self.parent.get_scale_min_percent(),
                    min_value=0.0,
                    max_value=100,
                    step=0.001,
                    format="%.3f",
                    callback=self.parent._on_scale_limits_changed,
                )

                self.scale_max_input_id = dpg.add_input_float(
                    label="Max Z (%)",
                    width=-120,
                    default_value=self.parent.get_scale_max_percent(),
                    min_value=0.0,
                    max_value=100,
                    step=0.001,
                    format="%.3f",
                    callback=self.parent._on_scale_limits_changed,
                )

                self.autoscale_grace_input_id = dpg.add_input_float(
                    label="Grace (%)",
                    width=-120,
                    default_value=self.parent.autoscale_grace_percent,
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    callback=self.parent._on_autoscale_grace_changed,
                )

                self.mirrored_difference_checkbox_id = dpg.add_checkbox(
                    label="Mirrored",
                    default_value=self.parent.mirrored_difference_scale,
                    callback=self.parent._on_mirrored_difference_changed,
                )

                self.colorbar_checkbox_id = dpg.add_checkbox(
                    label="Color Scale Bar",
                    default_value=self.parent.colorbar_enabled,
                    callback=self.parent._on_colorbar_enabled_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Zero-Referenced Display", default_open=True, span_full_width=True) as zero_referenced_display_node_id:
                self.section_node_ids["zero_referenced_display"] = zero_referenced_display_node_id
                self.display_mode_combo_id = dpg.add_combo(
                    label="Display Mode",
                    items=["Normal", "Difference", "Contrast"],
                    default_value=self.parent.display_mode,
                    width=-120,
                    callback=self.parent._on_display_mode_changed,
                )

                self.color_scale_combo_id = dpg.add_combo(
                    label="Color Scale",
                    items=self.parent.get_available_colormap_labels(),
                    default_value=self.parent.get_selected_colormap_label(),
                    width=-120,
                    callback=self.parent._on_colormap_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Signal Processing", default_open=True, span_full_width=True) as signal_processing_node_id:
                self.section_node_ids["signal_processing"] = signal_processing_node_id
                self.lp_filter_checkbox_id = dpg.add_checkbox(
                    label="LP Filter",
                    default_value=self.parent.lp_filter_enabled,
                    callback=self.parent._on_lp_filter_enabled_changed,
                )

                self.lp_filter_cutoff_input_id = dpg.add_input_float(
                    label="Cuttoff Frequency",
                    width=-120,
                    default_value=self.parent.lp_filter_cutoff_hz,
                    min_value=0.001,
                    min_clamped=True,
                    step=0.5,
                    on_enter=True,
                    callback=self.parent._on_lp_filter_cutoff_changed,
                )

                with dpg.item_handler_registry(tag=f"{self.parent.tag}_LpFilterCutoffHandler"):
                    dpg.add_item_deactivated_after_edit_handler(callback=self.parent._on_lp_filter_cutoff_changed)
                dpg.bind_item_handler_registry(self.lp_filter_cutoff_input_id, f"{self.parent.tag}_LpFilterCutoffHandler")

                self.drift_correction_checkbox_id = dpg.add_checkbox(
                    label="Drift Correction",
                    default_value=self.parent.drift_correction_enabled,
                    callback=self.parent._on_drift_correction_changed,
                )

                self.bg_removal_checkbox_id = dpg.add_checkbox(
                    label="BG Removal",
                    default_value=self.parent.bg_removal_enabled,
                    callback=self.parent._on_bg_removal_enabled_changed,
                )

                self.bg_removal_sigma_input_id = dpg.add_input_float(
                    label="BG Sigma (px)",
                    width=-120,
                    default_value=self.parent.bg_removal_sigma,
                    min_value=1.0,
                    max_value=200.0,
                    step=1.0,
                    format="%.1f",
                    on_enter=True,
                    callback=self.parent._on_bg_removal_sigma_changed,
                )

                with dpg.item_handler_registry(tag=f"{self.parent.tag}_BgRemovalSigmaHandler"):
                    dpg.add_item_deactivated_after_edit_handler(callback=self.parent._on_bg_removal_sigma_changed)
                dpg.bind_item_handler_registry(self.bg_removal_sigma_input_id, f"{self.parent.tag}_BgRemovalSigmaHandler")

                self.crop_slider_id = dpg.add_slider_float(
                    label="Crop (%)",
                    width=-120,
                    default_value=self.parent.crop_percent,
                    min_value=0.0,
                    max_value=100.0,
                    format="%.1f",
                    callback=self.parent._on_crop_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Focus Level", default_open=True, span_full_width=True) as focus_level_node_id:
                self.section_node_ids["focus_level"] = focus_level_node_id
                self._focus_bar_w = 256
                self._focus_bar_h = 18
                with dpg.drawlist(width=self._focus_bar_w, height=self._focus_bar_h):
                    self._focus_drawlist_id = dpg.last_item()
                    # Background
                    dpg.draw_rectangle([0, 0], [self._focus_bar_w, self._focus_bar_h],
                                       fill=[60, 60, 60], color=[0, 0, 0, 0])
                    # Green in-focus zone (centre ±10 %)
                    _gz_l = int(self._focus_bar_w * 0.44)
                    _gz_r = int(self._focus_bar_w * 0.56)
                    dpg.draw_rectangle([_gz_l, 1], [_gz_r, self._focus_bar_h - 1],
                                       fill=[50, 190, 80, 200], color=[0, 0, 0, 0])
                    # Centre tick
                    _cx = self._focus_bar_w // 2
                    dpg.draw_line([_cx, 0], [_cx, self._focus_bar_h],
                                  color=[180, 180, 180, 120], thickness=1)
                    # Position marker (starts at centre, updated each frame)
                    self._focus_marker_id = dpg.draw_rectangle(
                        [_cx - 2, 1], [_cx + 2, self._focus_bar_h - 1],
                        fill=[255, 255, 255], color=[0, 0, 0, 0],
                    )
                self._focus_label_id = dpg.add_text("No reference set")

            dpg.add_separator()
            dpg.add_button(label="Set Zero", width=-1, callback=self.parent._on_set_zero)
            dpg.add_button(label="Reset Zoom", width=-1, callback=self.parent._reset_zoom)

    def update_focus_indicator(self, focus_level, has_reference):
        if not dpg.does_item_exist(self._focus_marker_id):
            return
        w = self._focus_bar_w
        h = self._focus_bar_h
        x = int((float(focus_level) + 1.0) * 0.5 * w)
        x = max(2, min(w - 2, x))
        dpg.configure_item(self._focus_marker_id, pmin=[x - 2, 1], pmax=[x + 2, h - 1])
        if dpg.does_item_exist(self._focus_label_id):
            if not has_reference:
                dpg.set_value(self._focus_label_id, "No reference — press Set Zero")
            else:
                pct = int(focus_level * 100)
                dpg.set_value(self._focus_label_id, f"Focus offset: {pct:+d}%")

    def _state_name(self):
        return f"{type(self).__name__}_{self.parent.tag}"

    def SaveState(self):
        save_state_file(
            self._state_name(),
            {
                "window": capture_window_state(self.window_id),
                "sections": capture_item_open_states(self.section_node_ids),
            },
        )

    def LoadState(self):
        state = load_state_file(self._state_name())
        if state:
            apply_window_state(self.window_id, state.get("window"))
            apply_item_open_states(self.section_node_ids, state.get("sections"))
