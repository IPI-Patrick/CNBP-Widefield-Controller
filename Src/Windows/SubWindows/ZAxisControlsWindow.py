import dearpygui.dearpygui as dpg

from Drivers.ZAxisDriver import ZAxisDriver
from Utils.fonts import get_segmdl2_icon_font
from Utils.state_persistence import apply_item_open_states, apply_window_state, capture_item_open_states, capture_window_state, load_state_file, save_state_file
from Utils.themes import selected_theme


class ZAxisControlsWindow:

    def __init__(self, *, tag_prefix="CameraControls_ZAxis", label="Z Axis Controls", pos=(935, 10)):
        self.tag_prefix = str(tag_prefix)
        self.driver = ZAxisDriver()
        self._button_up_active = False
        self._button_down_active = False
        self._key_up_active = False
        self._key_down_active = False
        self._active_motion_direction = 0
        self._syncing_jog_inputs = False
        self.section_node_ids = {}

        initial_ports = self.driver.list_ports()
        initial_port = initial_ports[0] if initial_ports else ""
        self.icon_font = get_segmdl2_icon_font()

        with dpg.window(
            label=label,
            tag=f"{self.tag_prefix}_Window",
            width=300,
            height=325,
            pos=pos,
            no_resize=False,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()

            with dpg.tree_node(label="Connection", default_open=True, span_full_width=True) as connection_node_id:
                self.section_node_ids["connection"] = connection_node_id
                with dpg.group(horizontal=True):
                    self.port_combo_id = dpg.add_combo(
                        label="Port",
                        width=-130,
                        items=initial_ports,
                        default_value=initial_port,
                        callback=self._on_port_selected,
                    )
                    self.refresh_button_id = dpg.add_button(
                        label="\uE117",
                        width=40,
                        callback=self._refresh_ports,
                    )

                    with dpg.tooltip(self.refresh_button_id):
                        dpg.add_text("Refresh COM ports")

                    self.connect_button_id = dpg.add_button(
                        label="\uE8CD" if self.driver.connected else "\uE71B",
                        width=40,
                        callback=self._toggle_connection,
                    )

                    with dpg.tooltip(self.connect_button_id):
                        self.connect_button_tooltip_id = dpg.add_text(
                            "Disconnect z axis" if self.driver.connected else "Connect z axis"
                        )

                    dpg.bind_item_font(self.refresh_button_id, self.icon_font)
                    dpg.bind_item_font(self.connect_button_id, self.icon_font)

            dpg.add_separator()

            with dpg.tree_node(label="Status", default_open=True, span_full_width=True) as status_node_id:
                self.section_node_ids["status"] = status_node_id
                self.state_input_id = dpg.add_input_text(
                    label="State",
                    width=-110,
                    default_value="Disconnected",
                    readonly=True,
                )
                self.position_input_id = dpg.add_input_text(
                    label="Position",
                    width=-110,
                    default_value="--",
                    readonly=True,
                )
                self.speed_status_input_id = dpg.add_input_text(
                    label="Speed",
                    width=-110,
                    default_value="--",
                    readonly=True,
                )
                self.error_input_id = dpg.add_input_text(
                    label="Error",
                    width=-110,
                    default_value="",
                    readonly=True,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Motion", default_open=True, span_full_width=True) as motion_node_id:
                self.section_node_ids["motion"] = motion_node_id
                self.jog_steps_input_id = dpg.add_input_int(
                    label="Jog Steps",
                    width=-110,
                    default_value=100,
                    min_value=1,
                    min_clamped=True,
                    step=10,
                    callback=self._on_jog_steps_changed,
                )
                self.jog_revs_input_id = dpg.add_input_float(
                    label="Jog Revs",
                    width=-110,
                    default_value=100.0 / float(self.driver.STEPS_PER_OUTPUT_REV),
                    min_value=1.0 / float(self.driver.STEPS_PER_OUTPUT_REV),
                    min_clamped=True,
                    step=0.001,
                    format="%.6f rev",
                    callback=self._on_jog_revs_changed,
                )
                self.motion_speed_input_id = dpg.add_input_float(
                    label="Move Speed",
                    width=-110,
                    default_value=0.01,
                    min_value=0.0001,
                    min_clamped=True,
                    step=0.001,
                    format="%.4f rev/s",
                )
                self.motion_acceleration_input_id = dpg.add_input_float(
                    label="Acceleration",
                    width=-110,
                    default_value=28.125,
                    min_value=0.0001,
                    min_clamped=True,
                    step=1.0,
                    format="%.3f deg/s^2",
                    callback=self._on_motion_profile_changed,
                )

                with dpg.group(horizontal=True):
                    self.negative_jog_button_id = dpg.add_button(
                        label="- Jog",
                        width=85,
                        callback=lambda: self._on_jog(-1),
                    )
                    self.set_zero_button_id = dpg.add_button(
                        label="Set Zero",
                        width=85,
                        callback=self._set_zero,
                    )
                    self.positive_jog_button_id = dpg.add_button(
                        label="+ Jog",
                        width=-1,
                        callback=lambda: self._on_jog(1),
                    )

                with dpg.group(horizontal=True):
                    self.negative_move_button_id = dpg.add_button(
                        label="Move -",
                        width=85,
                    )
                    self.stop_button_id = dpg.add_button(
                        label="Stop",
                        width=85,
                        callback=self._stop_motion,
                    )
                    self.positive_move_button_id = dpg.add_button(
                        label="Move +",
                        width=-1,
                    )

            dpg.add_separator()

        with dpg.item_handler_registry(tag=f"{self.tag_prefix}_MoveDownHandlers"):
            dpg.add_item_activated_handler(callback=lambda: self._on_move_button_pressed(-1))
            dpg.add_item_deactivated_handler(callback=lambda: self._on_move_button_released(-1))
        dpg.bind_item_handler_registry(self.negative_move_button_id, f"{self.tag_prefix}_MoveDownHandlers")

        with dpg.item_handler_registry(tag=f"{self.tag_prefix}_MoveUpHandlers"):
            dpg.add_item_activated_handler(callback=lambda: self._on_move_button_pressed(1))
            dpg.add_item_deactivated_handler(callback=lambda: self._on_move_button_released(1))
        dpg.bind_item_handler_registry(self.positive_move_button_id, f"{self.tag_prefix}_MoveUpHandlers")

        self._update_controls()
        self._sync_jog_inputs_from_steps(int(dpg.get_value(self.jog_steps_input_id)))

    def _state_name(self):
        return type(self).__name__

    def _refresh_ports(self, sender=None, app_data=None, user_data=None):
        ports = self.driver.list_ports()
        current_value = str(dpg.get_value(self.port_combo_id)).strip()
        selected_port = current_value if current_value in ports else (ports[0] if ports else "")
        dpg.configure_item(self.port_combo_id, items=ports)
        dpg.set_value(self.port_combo_id, selected_port)

    def _on_port_selected(self, sender, app_data, user_data=None):
        self.driver.port = str(app_data).strip() or None

    def _toggle_connection(self, sender=None, app_data=None, user_data=None):
        if self.driver.connected:
            self.driver.disconnect()
            self._update_controls()
            return

        selected_port = str(dpg.get_value(self.port_combo_id)).strip()
        if not selected_port:
            self.driver.last_error = "No COM port selected"
            self._update_controls()
            return

        self.driver.connect(selected_port)
        self._update_controls()

    def _attempt_auto_connect(self):
        if self.driver.connected:
            return

        selected_port = str(dpg.get_value(self.port_combo_id)).strip()
        if not selected_port:
            return

        self.driver.connect(selected_port)
        self._apply_motion_profile_to_driver()
        self._update_controls()

    def _apply_motion_profile_to_driver(self):
        if not self.driver.connected:
            return

        move_speed = max(0.0001, float(dpg.get_value(self.motion_speed_input_id)))
        acceleration = max(0.0001, float(dpg.get_value(self.motion_acceleration_input_id)))
        self.driver.set_speed_revs_per_second(move_speed)
        self.driver.set_acceleration_degrees_per_second_squared(acceleration)

    def _on_motion_profile_changed(self, sender=None, app_data=None, user_data=None):
        self._apply_motion_profile_to_driver()

    def _steps_to_revs(self, steps: int) -> float:
        return float(steps) / float(self.driver.STEPS_PER_OUTPUT_REV)

    def _revs_to_steps(self, revs: float) -> int:
        return max(1, int(round(float(revs) * float(self.driver.STEPS_PER_OUTPUT_REV))))

    def _sync_jog_inputs_from_steps(self, steps: int):
        if self._syncing_jog_inputs:
            return

        self._syncing_jog_inputs = True
        try:
            clamped_steps = max(1, int(steps))
            dpg.set_value(self.jog_steps_input_id, clamped_steps)
            dpg.set_value(self.jog_revs_input_id, self._steps_to_revs(clamped_steps))
        finally:
            self._syncing_jog_inputs = False

    def _sync_jog_inputs_from_revs(self, revs: float):
        if self._syncing_jog_inputs:
            return

        self._syncing_jog_inputs = True
        try:
            clamped_revs = max(1.0 / float(self.driver.STEPS_PER_OUTPUT_REV), float(revs))
            dpg.set_value(self.jog_revs_input_id, clamped_revs)
            dpg.set_value(self.jog_steps_input_id, self._revs_to_steps(clamped_revs))
        finally:
            self._syncing_jog_inputs = False

    def _on_jog_steps_changed(self, sender=None, app_data=None, user_data=None):
        self._sync_jog_inputs_from_steps(int(app_data))

    def _on_jog_revs_changed(self, sender=None, app_data=None, user_data=None):
        self._sync_jog_inputs_from_revs(float(app_data))

    def _on_jog(self, direction):
        if not self.driver.connected:
            return

        jog_steps = max(1, int(dpg.get_value(self.jog_steps_input_id)))
        move_speed = max(0.0001, float(dpg.get_value(self.motion_speed_input_id)))
        self.driver.jog_with_revs_per_second(jog_steps * int(direction), move_speed)

    def _on_move_button_pressed(self, direction):
        if direction > 0:
            self._button_up_active = True
        else:
            self._button_down_active = True
        self._apply_requested_motion()

    def _on_move_button_released(self, direction):
        if direction > 0:
            self._button_up_active = False
        else:
            self._button_down_active = False
        self._apply_requested_motion()

    def _apply_requested_motion(self):
        up_active = self._button_up_active or self._key_up_active
        down_active = self._button_down_active or self._key_down_active

        requested_direction = 0
        if up_active and not down_active:
            requested_direction = 1
        elif down_active and not up_active:
            requested_direction = -1
        elif up_active and down_active:
            requested_direction = self._active_motion_direction if self._active_motion_direction in (-1, 1) else 1

        if not self.driver.connected:
            requested_direction = 0

        if requested_direction == self._active_motion_direction:
            self._update_motion_button_highlight()
            return

        if requested_direction == 0:
            if self.driver.connected and self._active_motion_direction != 0:
                self.driver.stop_motion()
        else:
            move_speed = max(0.0001, float(dpg.get_value(self.motion_speed_input_id)))
            self.driver.start_continuous_revs_per_second(requested_direction, move_speed)

        self._active_motion_direction = requested_direction
        self._update_motion_button_highlight()

    def _clear_requested_motion(self):
        self._button_up_active = False
        self._button_down_active = False
        self._key_up_active = False
        self._key_down_active = False
        self._active_motion_direction = 0
        self._update_motion_button_highlight()

    def _update_motion_button_highlight(self):
        dpg.bind_item_theme(self.negative_move_button_id, selected_theme if self._active_motion_direction < 0 else None)
        dpg.bind_item_theme(self.positive_move_button_id, selected_theme if self._active_motion_direction > 0 else None)

    def _stop_motion(self, sender=None, app_data=None, user_data=None):
        self._button_up_active = False
        self._button_down_active = False
        self._key_up_active = False
        self._key_down_active = False
        if self.driver.connected:
            self.driver.stop_motion()
        self._active_motion_direction = 0
        self._update_motion_button_highlight()

    def _set_zero(self, sender=None, app_data=None, user_data=None):
        if self.driver.connected:
            self.driver.set_zero()

    def _update_controls(self):
        snapshot = self.driver.snapshot()
        connected = bool(snapshot.get("connected"))

        if not connected and self._active_motion_direction != 0:
            self._clear_requested_motion()

        dpg.configure_item(self.port_combo_id, enabled=not connected)
        dpg.configure_item(self.refresh_button_id, enabled=not connected)
        dpg.set_item_label(self.connect_button_id, "\uE8CD" if connected else "\uE71B")
        dpg.set_value(self.connect_button_tooltip_id, "Disconnect z axis" if connected else "Connect z axis")

        motion_item_ids = (
            self.jog_steps_input_id,
            self.jog_revs_input_id,
            self.motion_speed_input_id,
            self.motion_acceleration_input_id,
            self.negative_jog_button_id,
            self.positive_jog_button_id,
            self.negative_move_button_id,
            self.stop_button_id,
            self.positive_move_button_id,
            self.set_zero_button_id,
        )
        for item_id in motion_item_ids:
            dpg.configure_item(item_id, enabled=connected)

        state_text = str(snapshot.get("state") or "Disconnected")
        position_steps = snapshot.get("position_steps")
        speed_revs_per_s = snapshot.get("speed_revs_per_s")
        last_error = str(snapshot.get("last_error") or "")

        dpg.set_value(self.state_input_id, state_text)
        dpg.set_value(self.position_input_id, "--" if position_steps is None else str(position_steps))
        dpg.set_value(
            self.speed_status_input_id,
            "--" if speed_revs_per_s is None else f"{float(speed_revs_per_s):.4f} rev/s",
        )
        dpg.set_value(self.error_input_id, last_error)
        self._update_motion_button_highlight()

    def render(self):
        self._update_controls()

    def SaveState(self):
        save_state_file(
            self._state_name(),
            {
                "window": capture_window_state(self.window_id),
                "sections": capture_item_open_states(self.section_node_ids),
                "port": str(dpg.get_value(self.port_combo_id)).strip(),
                "jog_steps": int(dpg.get_value(self.jog_steps_input_id)),
                "jog_revs": float(dpg.get_value(self.jog_revs_input_id)),
                "speed_revs_per_second": float(dpg.get_value(self.motion_speed_input_id)),
                "acceleration_deg_per_s2": float(dpg.get_value(self.motion_acceleration_input_id)),
            },
        )

    def LoadState(self):
        state = load_state_file(self._state_name())
        if not state:
            self._attempt_auto_connect()
            return

        apply_window_state(self.window_id, state.get("window"))
        apply_item_open_states(self.section_node_ids, state.get("sections"))

        if "port" in state:
            saved_port = str(state["port"]).strip()
            available_ports = list(self.driver.list_ports())
            if saved_port and saved_port not in available_ports:
                available_ports.insert(0, saved_port)
            dpg.configure_item(self.port_combo_id, items=available_ports)
            dpg.set_value(self.port_combo_id, saved_port)
            self.driver.port = saved_port or None

        if "jog_steps" in state:
            self._sync_jog_inputs_from_steps(max(1, int(state["jog_steps"])))
        elif "jog_revs" in state:
            self._sync_jog_inputs_from_revs(max(1.0 / float(self.driver.STEPS_PER_OUTPUT_REV), float(state["jog_revs"])))

        if "speed_revs_per_second" in state:
            dpg.set_value(self.motion_speed_input_id, max(0.0001, float(state["speed_revs_per_second"])))
        elif "speed_steps_per_second" in state:
            legacy_speed = self.driver.steps_per_second_to_revs_per_second(float(state["speed_steps_per_second"]))
            if legacy_speed is not None:
                dpg.set_value(self.motion_speed_input_id, max(0.0001, float(legacy_speed)))

        if "acceleration_deg_per_s2" in state:
            dpg.set_value(self.motion_acceleration_input_id, max(0.0001, float(state["acceleration_deg_per_s2"])))
        elif "acceleration_steps_per_s2" in state:
            legacy_accel = self.driver.steps_per_second_squared_to_degrees_per_second_squared(float(state["acceleration_steps_per_s2"]))
            if legacy_accel is not None:
                dpg.set_value(self.motion_acceleration_input_id, max(0.0001, float(legacy_accel)))

        self._attempt_auto_connect()