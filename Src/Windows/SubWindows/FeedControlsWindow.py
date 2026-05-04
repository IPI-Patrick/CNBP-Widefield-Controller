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

            dpg.add_separator()
            dpg.add_button(label="Set Zero", width=-1, callback=self.parent._on_set_zero)
            dpg.add_button(label="Reset Zoom", width=-1, callback=self.parent._reset_zoom)

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
