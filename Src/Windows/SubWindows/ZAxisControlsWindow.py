import dearpygui.dearpygui as dpg

from Drivers.ZAxisDriver import ZAxisDriver
from Utils.fonts import get_segmdl2_icon_font
from Utils.state_persistence import (
    apply_item_open_states, apply_window_state,
    capture_item_open_states, capture_window_state,
    load_state_file, save_state_file,
)
from Utils.themes import selected_theme

_STEPS_PER_REV = ZAxisDriver.STEPS_PER_OUTPUT_REV


def _revs_to_steps(revs: float) -> int:
    return max(1, int(round(float(revs) * _STEPS_PER_REV)))


def _steps_to_revs(steps) -> float:
    return float(steps) / _STEPS_PER_REV if steps is not None else 0.0


def _revs_s_to_steps_s(revs_s: float) -> float:
    return max(1.0, float(revs_s) * _STEPS_PER_REV)


def _deg_s2_to_steps_s2(deg_s2: float) -> float:
    return max(1.0, (float(deg_s2) * _STEPS_PER_REV) / 360.0)


class ZAxisControlsWindow:

    def __init__(self, *, tag_prefix="CameraControls_ZAxis", label="Z Axis Controls", pos=(935, 10)):
        self.tag_prefix = str(tag_prefix)
        self.driver = ZAxisDriver()
        self._active_direction = 0
        self._button_up = False
        self._button_down = False
        self._syncing_jog = False
        self.section_node_ids = {}

        initial_ports = self.driver.list_ports()
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

            with dpg.tree_node(label="Connection", default_open=True, span_full_width=True) as conn_node:
                self.section_node_ids["connection"] = conn_node
                with dpg.group(horizontal=True):
                    self.port_combo_id = dpg.add_combo(
                        label="Port", width=-130,
                        items=initial_ports,
                        default_value=initial_ports[0] if initial_ports else "",
                        callback=self._on_port_selected,
                    )
                    self.refresh_button_id = dpg.add_button(
                        label="\uE117", width=40, callback=self._refresh_ports,
                    )
                    with dpg.tooltip(self.refresh_button_id):
                        dpg.add_text("Refresh COM ports")
                    self.connect_button_id = dpg.add_button(
                        label="\uE8CD" if self.driver.connected else "\uE71B",
                        width=40, callback=self._toggle_connection,
                    )
                    with dpg.tooltip(self.connect_button_id):
                        self.connect_tooltip_id = dpg.add_text(
                            "Disconnect z axis" if self.driver.connected else "Connect z axis"
                        )
                    dpg.bind_item_font(self.refresh_button_id, self.icon_font)
                    dpg.bind_item_font(self.connect_button_id, self.icon_font)

            dpg.add_separator()

            with dpg.tree_node(label="Status", default_open=True, span_full_width=True) as status_node:
                self.section_node_ids["status"] = status_node
                self.state_input_id = dpg.add_input_text(
                    label="State", width=-110, default_value="Disconnected", readonly=True,
                )
                self.position_input_id = dpg.add_input_text(
                    label="Position", width=-110, default_value="--", readonly=True,
                )
                self.speed_input_id = dpg.add_input_text(
                    label="Speed", width=-110, default_value="--", readonly=True,
                )
                self.error_input_id = dpg.add_input_text(
                    label="Error", width=-110, default_value="", readonly=True,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Motion", default_open=True, span_full_width=True) as motion_node:
                self.section_node_ids["motion"] = motion_node
                self.jog_steps_id = dpg.add_input_int(
                    label="Jog Steps", width=-110, default_value=100,
                    min_value=1, min_clamped=True, step=10,
                    callback=self._on_jog_steps_changed,
                )
                self.jog_revs_id = dpg.add_input_float(
                    label="Jog Revs", width=-110,
                    default_value=_steps_to_revs(100),
                    min_value=_steps_to_revs(1), min_clamped=True,
                    step=0.001, format="%.6f rev",
                    callback=self._on_jog_revs_changed,
                )
                self.speed_id = dpg.add_input_float(
                    label="Move Speed", width=-110, default_value=0.01,
                    min_value=0.0001, min_clamped=True,
                    step=0.001, format="%.4f rev/s",
                    callback=self._on_motion_profile_changed,
                )
                self.accel_id = dpg.add_input_float(
                    label="Acceleration", width=-110, default_value=28.125,
                    min_value=0.0001, min_clamped=True,
                    step=1.0, format="%.3f deg/s^2",
                    callback=self._on_motion_profile_changed,
                )

                with dpg.group(horizontal=True):
                    self.neg_jog_id = dpg.add_button(
                        label="- Jog", width=85, callback=lambda: self._on_jog(-1),
                    )
                    self.set_zero_id = dpg.add_button(
                        label="Set Zero", width=85, callback=self._set_zero,
                    )
                    self.pos_jog_id = dpg.add_button(
                        label="+ Jog", width=-1, callback=lambda: self._on_jog(1),
                    )

                with dpg.group(horizontal=True):
                    self.neg_move_id = dpg.add_button(label="Move -", width=85)
                    self.stop_id = dpg.add_button(
                        label="Stop", width=85, callback=self._stop,
                    )
                    self.pos_move_id = dpg.add_button(label="Move +", width=-1)

            dpg.add_separator()

        with dpg.item_handler_registry(tag=f"{self.tag_prefix}_MoveDownHandlers"):
            dpg.add_item_activated_handler(callback=lambda: self._on_move_pressed(-1))
            dpg.add_item_deactivated_handler(callback=lambda: self._on_move_released(-1))
        dpg.bind_item_handler_registry(self.neg_move_id, f"{self.tag_prefix}_MoveDownHandlers")

        with dpg.item_handler_registry(tag=f"{self.tag_prefix}_MoveUpHandlers"):
            dpg.add_item_activated_handler(callback=lambda: self._on_move_pressed(1))
            dpg.add_item_deactivated_handler(callback=lambda: self._on_move_released(1))
        dpg.bind_item_handler_registry(self.pos_move_id, f"{self.tag_prefix}_MoveUpHandlers")

        self._refresh_ui()
        self._sync_jog_from_steps(int(dpg.get_value(self.jog_steps_id)))

    def _state_name(self):
        return type(self).__name__

    # --- Connection ---

    def _refresh_ports(self, sender=None, app_data=None, user_data=None):
        ports = self.driver.list_ports()
        current = str(dpg.get_value(self.port_combo_id)).strip()
        selected = current if current in ports else (ports[0] if ports else "")
        dpg.configure_item(self.port_combo_id, items=ports)
        dpg.set_value(self.port_combo_id, selected)

    def _on_port_selected(self, sender, app_data, user_data=None):
        self.driver.port = str(app_data).strip() or None

    def _toggle_connection(self, sender=None, app_data=None, user_data=None):
        if self.driver.connected:
            self.driver.disconnect()
        else:
            port = str(dpg.get_value(self.port_combo_id)).strip()
            if not port:
                self.driver.last_error = "No COM port selected"
            else:
                self.driver.connect(port)
                self._apply_motion_profile()
        self._refresh_ui()

    def _attempt_auto_connect(self):
        if self.driver.connected:
            return
        port = str(dpg.get_value(self.port_combo_id)).strip()
        if port:
            self.driver.connect(port)
            self._apply_motion_profile()
            self._refresh_ui()

    # --- Motion profile ---

    def _on_motion_profile_changed(self, sender=None, app_data=None, user_data=None):
        self._apply_motion_profile()

    def _apply_motion_profile(self):
        if not self.driver.connected:
            return
        speed_revs = max(0.0001, float(dpg.get_value(self.speed_id)))
        accel_deg = max(0.0001, float(dpg.get_value(self.accel_id)))
        self.driver.set_settings(
            speed_steps_per_s=_revs_s_to_steps_s(speed_revs),
            accel_steps_per_s2=_deg_s2_to_steps_s2(accel_deg),
        )

    # --- Jog input sync ---

    def _sync_jog_from_steps(self, steps: int):
        if self._syncing_jog:
            return
        self._syncing_jog = True
        try:
            steps = max(1, int(steps))
            dpg.set_value(self.jog_steps_id, steps)
            dpg.set_value(self.jog_revs_id, _steps_to_revs(steps))
        finally:
            self._syncing_jog = False

    def _sync_jog_from_revs(self, revs: float):
        if self._syncing_jog:
            return
        self._syncing_jog = True
        try:
            revs = max(_steps_to_revs(1), float(revs))
            dpg.set_value(self.jog_revs_id, revs)
            dpg.set_value(self.jog_steps_id, _revs_to_steps(revs))
        finally:
            self._syncing_jog = False

    def _on_jog_steps_changed(self, sender=None, app_data=None, user_data=None):
        self._sync_jog_from_steps(int(app_data))

    def _on_jog_revs_changed(self, sender=None, app_data=None, user_data=None):
        self._sync_jog_from_revs(float(app_data))

    # --- Motion commands ---

    def _on_jog(self, direction):
        if not self.driver.connected:
            return
        steps = max(1, int(dpg.get_value(self.jog_steps_id))) * int(direction)
        speed_revs = max(0.0001, float(dpg.get_value(self.speed_id)))
        self.driver.jog(steps, speed_steps_per_s=_revs_s_to_steps_s(speed_revs))

    def _on_move_pressed(self, direction):
        if direction > 0:
            self._button_up = True
        else:
            self._button_down = True
        self._apply_motion()

    def _on_move_released(self, direction):
        if direction > 0:
            self._button_up = False
        else:
            self._button_down = False
        self._apply_motion()

    def _apply_motion(self):
        up = self._button_up
        down = self._button_down
        if up and not down:
            direction = 1
        elif down and not up:
            direction = -1
        elif up and down:
            direction = self._active_direction if self._active_direction in (-1, 1) else 1
        else:
            direction = 0

        if not self.driver.connected:
            direction = 0

        if direction == self._active_direction:
            self._update_move_highlights()
            return

        if direction == 0:
            if self.driver.connected and self._active_direction != 0:
                self.driver.stop()
        else:
            speed_revs = max(0.0001, float(dpg.get_value(self.speed_id)))
            self.driver.move_continuous(direction, _revs_s_to_steps_s(speed_revs))

        self._active_direction = direction
        self._update_move_highlights()

    def _stop(self, sender=None, app_data=None, user_data=None):
        self._button_up = False
        self._button_down = False
        self._active_direction = 0
        if self.driver.connected:
            self.driver.stop()
        self._update_move_highlights()

    def _set_zero(self, sender=None, app_data=None, user_data=None):
        if self.driver.connected:
            self.driver.set_zero()

    # --- UI refresh ---

    def _update_move_highlights(self):
        dpg.bind_item_theme(self.neg_move_id, selected_theme if self._active_direction < 0 else None)
        dpg.bind_item_theme(self.pos_move_id, selected_theme if self._active_direction > 0 else None)

    def _refresh_ui(self):
        snap = self.driver.snapshot()
        connected = bool(snap.get("connected"))

        if not connected and self._active_direction != 0:
            self._button_up = False
            self._button_down = False
            self._active_direction = 0

        dpg.configure_item(self.port_combo_id, enabled=not connected)
        dpg.configure_item(self.refresh_button_id, enabled=not connected)
        dpg.set_item_label(self.connect_button_id, "\uE8CD" if connected else "\uE71B")
        dpg.set_value(self.connect_tooltip_id, "Disconnect z axis" if connected else "Connect z axis")

        motion_items = (
            self.jog_steps_id, self.jog_revs_id, self.speed_id, self.accel_id,
            self.neg_jog_id, self.pos_jog_id, self.neg_move_id,
            self.stop_id, self.pos_move_id, self.set_zero_id,
        )
        for item in motion_items:
            dpg.configure_item(item, enabled=connected)

        dpg.set_value(self.state_input_id, str(snap.get("state") or "Disconnected"))
        pos = snap.get("position_steps")
        dpg.set_value(self.position_input_id, "--" if pos is None else str(pos))
        speed = snap.get("speed_revs_per_s")
        dpg.set_value(self.speed_input_id, "--" if speed is None else f"{float(speed):.4f} rev/s")
        dpg.set_value(self.error_input_id, str(snap.get("last_error") or ""))
        self._update_move_highlights()

    def render(self):
        self._refresh_ui()

    # --- State persistence ---

    def SaveState(self):
        save_state_file(
            self._state_name(),
            {
                "window": capture_window_state(self.window_id),
                "sections": capture_item_open_states(self.section_node_ids),
                "port": str(dpg.get_value(self.port_combo_id)).strip(),
                "jog_steps": int(dpg.get_value(self.jog_steps_id)),
                "speed_revs_per_second": float(dpg.get_value(self.speed_id)),
                "acceleration_deg_per_s2": float(dpg.get_value(self.accel_id)),
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
            ports = list(self.driver.list_ports())
            if saved_port and saved_port not in ports:
                ports.insert(0, saved_port)
            dpg.configure_item(self.port_combo_id, items=ports)
            dpg.set_value(self.port_combo_id, saved_port)
            self.driver.port = saved_port or None

        if "jog_steps" in state:
            self._sync_jog_from_steps(max(1, int(state["jog_steps"])))
        elif "jog_revs" in state:
            self._sync_jog_from_revs(max(_steps_to_revs(1), float(state["jog_revs"])))

        if "speed_revs_per_second" in state:
            dpg.set_value(self.speed_id, max(0.0001, float(state["speed_revs_per_second"])))
        elif "speed_steps_per_second" in state:
            revs = float(state["speed_steps_per_second"]) / _STEPS_PER_REV
            dpg.set_value(self.speed_id, max(0.0001, revs))

        if "acceleration_deg_per_s2" in state:
            dpg.set_value(self.accel_id, max(0.0001, float(state["acceleration_deg_per_s2"])))
        elif "acceleration_steps_per_s2" in state:
            deg = (float(state["acceleration_steps_per_s2"]) * 360.0) / _STEPS_PER_REV
            dpg.set_value(self.accel_id, max(0.0001, deg))

        self._attempt_auto_connect()
