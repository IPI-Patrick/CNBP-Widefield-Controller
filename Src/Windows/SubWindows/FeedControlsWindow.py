import dearpygui.dearpygui as dpg
from Utils.custom_widgets import add_input_float, add_input_int
from Utils.state_persistence import apply_item_open_states, apply_window_state, capture_item_open_states, capture_window_state, load_state_file, save_state_file


class FeedControlsWindow:

    def __init__(self, parent, dpg_parent=None):
        self.parent = parent
        self.section_node_ids = {}

        if dpg_parent is not None:
            self.window_id = dpg_parent
            dpg.push_container_stack(dpg_parent)
            self._build_controls()
            dpg.pop_container_stack()
        else:
            with dpg.window(
                label="Feed Controls",
                tag=f"{self.parent.tag}_ControlsWindow",
                width=300,
                height=700,
                pos=(10, 620),
                no_resize=False,
                no_scrollbar=True,
                no_scroll_with_mouse=True,
            ):
                self.window_id = dpg.last_item()
                self._build_controls()

    def _build_controls(self):
        with dpg.tree_node(label="Display Scaling", default_open=True, span_full_width=True) as display_scaling_node_id:
            self.section_node_ids["display_scaling"] = display_scaling_node_id
            self.autoscale_checkbox_id = dpg.add_checkbox(
                label="Autoscale",
                default_value=self.parent.autoscale_enabled,
                callback=self.parent._on_autoscale_changed,
            )

            self.scale_min_input_id = add_input_float(
                label="Min Z (%)",
                width=-120,
                default_value=self.parent.get_scale_min_percent(),
                min_value=0.0,
                max_value=100,
                step=0.001,
                format="%.3f",
                callback=self.parent._on_scale_limits_changed,
            )                            


            self.scale_max_input_id = add_input_float(
                label="Max Z (%)",
                width=-120,
                default_value=self.parent.get_scale_max_percent(),
                min_value=0.0,
                max_value=100,
                step=0.001,
                format="%.3f",
                callback=self.parent._on_scale_limits_changed,
            )

            self.autoscale_grace_input_id = add_input_float(
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

        with dpg.tree_node(label="Scale Bar", default_open=True, span_full_width=True) as scale_bar_node_id:
            self.section_node_ids["scale_bar"] = scale_bar_node_id
            self.scale_bar_enabled_checkbox_id = dpg.add_checkbox(
                label="Enabled",
                default_value=self.parent.scale_bar_enabled,
                callback=self.parent._on_scale_bar_enabled_changed,
            )

            self.scale_bar_auto_width_checkbox_id = dpg.add_checkbox(
                label="Auto Width",
                default_value=self.parent.scale_bar_auto_width,
                callback=self.parent._on_scale_bar_auto_width_changed,
            )

            self.scale_bar_width_input_id = add_input_float(
                label="Width",
                width=-120,
                default_value=self.parent.scale_bar_width_um,
                min_value=0.001,
                min_clamped=True,
                step=10.0,
                format="%.3f um",
                callback=self.parent._on_scale_bar_width_changed,
            )


            self.scale_bar_size_input_id = add_input_float(
                label="Size",
                width=-120,
                default_value=self.parent.scale_bar_size,
                min_value=0.1,
                min_clamped=True,
                step=0.1,
                format="%.2f",
                callback=self.parent._on_scale_bar_size_changed,
            )

            self.scale_bar_position_combo_id = dpg.add_combo(
                label="Position",
                items=["Bottom-Left", "Bottom-Right", "Top-Left", "Top-Right"],
                default_value=self.parent.scale_bar_position,
                width=-120,
                callback=self.parent._on_scale_bar_position_changed,
            )

            self.scale_bar_x_offset_input_id = add_input_int(
                label="X-Offset",
                width=-120,
                default_value=self.parent.scale_bar_x_offset,
                step=1,
                callback=self.parent._on_scale_bar_x_offset_changed,
            )

            self.scale_bar_y_offset_input_id = add_input_int(
                label="Y-Offset",
                width=-120,
                default_value=self.parent.scale_bar_y_offset,
                step=1,
                callback=self.parent._on_scale_bar_y_offset_changed,
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

            self.lp_filter_cutoff_input_id = add_input_float(
                label="Cuttoff Frequency",
                width=-120,
                default_value=self.parent.lp_filter_cutoff_hz,
                min_value=0.001,
                min_clamped=True,
                step=0.5,
                callback=self.parent._on_lp_filter_cutoff_changed,
            )


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

            self.bg_removal_sigma_input_id = add_input_float(
                label="BG Sigma (px)",
                width=-120,
                default_value=self.parent.bg_removal_sigma,
                min_value=1.0,
                max_value=200.0,
                step=1.0,
                format="%.1f",
                callback=self.parent._on_bg_removal_sigma_changed,
            )

            # Background model: Spatial (exact uniform_filter) or Temporal
            # (fast fused EMA kernel — the >1000 fps path).
            self.bg_mode_combo_id = dpg.add_combo(
                label="BG Mode",
                items=["Spatial", "Temporal"],
                default_value="Temporal" if self.parent.bg_mode == "temporal" else "Spatial",
                width=-120,
                callback=self.parent._on_bg_mode_changed,
            )

            self.bg_temporal_alpha_input_id = add_input_float(
                label="EMA Alpha",
                width=-120,
                default_value=self.parent.bg_temporal_alpha,
                min_value=0.0001,
                max_value=1.0,
                step=0.01,
                format="%.4f",
                callback=self.parent._on_bg_temporal_alpha_changed,
            )

            # Recompute drift every N frames, reuse the shift between (1 = every frame).
            self.phase_every_input_id = add_input_int(
                label="Drift Every N",
                width=-120,
                default_value=self.parent.phase_every,
                min_value=1,
                max_value=240,
                step=1,
                callback=self.parent._on_phase_every_changed,
            )

            # GIL-free C++/CUDA backend for the temporal path (requires the
            # compiled fastproc extension; only active when BG Mode = Temporal).
            self.use_cpp_backend_checkbox_id = dpg.add_checkbox(
                label="C++ Backend (temporal)",
                default_value=self.parent.use_cpp_backend,
                callback=self.parent._on_cpp_backend_changed,
            )

            # Full GIL-free C++/CUDA acquisition engine (fastacq). Takes effect on
            # the next preview start; the engine owns the whole capture->process loop.
            self.use_acquisition_engine_checkbox_id = dpg.add_checkbox(
                label="Acquisition Engine (GIL-free)",
                default_value=self.parent.use_acquisition_engine,
                callback=self.parent._on_acquisition_engine_changed,
            )


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

        with dpg.tree_node(label="Crosshair", default_open=False, span_full_width=True) as crosshair_node_id:
            self.section_node_ids["crosshair"] = crosshair_node_id
            self.crosshair_enabled_id = dpg.add_checkbox(
                label="Enabled",
                default_value=self.parent.crosshair_enabled,
                callback=self.parent._on_crosshair_enabled_changed,
            )
            self.crosshair_radius_id = dpg.add_slider_float(
                label="Radius (%)",
                width=-120,
                default_value=self.parent.crosshair_radius_percent,
                min_value=1.0,
                max_value=100.0,
                format="%.1f",
                callback=self.parent._on_crosshair_radius_changed,
            )

        dpg.add_separator()

        with dpg.tree_node(label="Zero Reference", default_open=True, span_full_width=True) as zero_ref_node_id:
            self.section_node_ids["zero_reference"] = zero_ref_node_id
            dpg.add_button(label="Set Zero", width=-1, callback=self.parent._on_set_zero)
            self._zero_ref_no_ref_text_id = dpg.add_text("No reference — press Set Zero")
            with dpg.texture_registry(show=False):
                self._zero_ref_placeholder_tex = dpg.add_dynamic_texture(
                    width=1, height=1, default_value=[0.0, 0.0, 0.0, 0.0],
                )
            self._zero_ref_image_id = dpg.add_image(
                self._zero_ref_placeholder_tex, width=1, height=1, show=False,
            )

        dpg.add_separator()
        dpg.add_button(label="Reset Zoom", width=-1, callback=self.parent._reset_zoom)

    def update_zero_reference(self, texture_id, img_width, img_height):
        """Show the zero reference image in the Feed sidebar section."""
        if not dpg.does_item_exist(self._zero_ref_image_id):
            return
        available_w = 256
        try:
            parent_rect = dpg.get_item_rect_size(self._zero_ref_image_id)
            if parent_rect and parent_rect[0] > 10:
                available_w = int(parent_rect[0])
        except Exception:
            pass
        aspect = max(1e-6, img_width / max(1, img_height))
        display_w = available_w
        display_h = max(1, int(display_w / aspect))
        dpg.configure_item(self._zero_ref_image_id, texture_tag=texture_id,
                           width=display_w, height=display_h, show=True)
        if dpg.does_item_exist(self._zero_ref_no_ref_text_id):
            dpg.hide_item(self._zero_ref_no_ref_text_id)

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
                "sections": capture_item_open_states(self.section_node_ids),
            },
        )

    def LoadState(self):
        state = load_state_file(self._state_name())
        if state:
            apply_item_open_states(self.section_node_ids, state.get("sections"))
