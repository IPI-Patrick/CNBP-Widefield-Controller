"""
Stage Controls window — XYZ motorised microscope stage via three KST101 drivers.

Layout
------
  Connection      — serial-number combos + connect/disconnect per axis
  Position Map    — square XY scatter plot and vertical Z bar side-by-side
  Keypad          — 2×3 hold-to-move button grid above the position map
  X / Y / Z Settings — per-axis velocity, jog, backlash parameters
"""

import atexit
import threading
import time

import dearpygui.dearpygui as dpg

import Utils.shared_state as shared_state
from Drivers.KST101 import KST101
from Utils.fonts import get_segmdl2_icon_font
from Utils.state_persistence import (
    apply_item_open_states,
    apply_window_state,
    capture_item_open_states,
    capture_window_state,
    load_state_file,
    save_state_file,
)
from Utils.themes import selected_theme

_TRAVEL_MM      = 25.0
_HALF_TRAVEL_MM = _TRAVEL_MM / 2.0

_XY_PLOT_SIZE = 260
_Z_BAR_WIDTH  = 50

_KEYPAD_BUTTON_MAP = {
    ("z", "-"): "_kp_neg_z",
    ("y", "+"): "_kp_pos_y",
    ("z", "+"): "_kp_pos_z",
    ("x", "-"): "_kp_neg_x",
    ("y", "-"): "_kp_neg_y",
    ("x", "+"): "_kp_pos_x",
}

# WASD + QE keyboard shortcuts: (dpg key constant, axis, direction)
_KEY_AXIS_MAP = [
    ("mvKey_W", "y", "+"),
    ("mvKey_S", "y", "-"),
    ("mvKey_D", "x", "+"),
    ("mvKey_A", "x", "-"),
    ("mvKey_E", "z", "+"),
    ("mvKey_Q", "z", "-"),
]


class StageControls:

    def __init__(self):
        self._motors = {
            "x": KST101(),
            "y": KST101(),
            "z": KST101(),
        }
        shared_state.shared_stage = self._motors

        self._jog_held: dict = {"x": set(), "y": set(), "z": set()}
        self._jog_speed = 2.0
        self._last_window_width = 0

        self._settings_loading: dict = {"x": False, "y": False, "z": False}
        self._settings_populated: dict = {"x": -1, "y": -1, "z": -1}

        # Per-axis non-blocking connect state (written by worker threads, read by render)
        self._connecting: dict = {"x": False, "y": False, "z": False}

        # Background snapshot cache — polling thread writes, render thread reads
        self._snaps: dict = {"x": {}, "y": {}, "z": {}}
        self._snap_lock = threading.Lock()
        self._polling = True

        # Async device scan: worker thread writes list, render thread consumes
        self._scan_result: list | None = None

        # Dev-mode auto-connect: worker thread writes serial list, render thread fires connects
        self._auto_connect_queue: list | None = None

        self.section_node_ids: dict = {}
        self.icon_font = get_segmdl2_icon_font()

        # ── Themes ──────────────────────────────────────────────────────
        with dpg.theme() as self._pos_marker_theme:
            with dpg.theme_component(dpg.mvScatterSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_MarkerFill, [220, 40, 40, 255],
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_color(
                    dpg.mvPlotCol_MarkerOutline, [200, 20, 20, 255],
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Square,
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_MarkerSize, 10.0,
                    category=dpg.mvThemeCat_Plots,
                )

        with dpg.theme() as self._z_bar_theme:
            with dpg.theme_component(dpg.mvBarSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Fill, [80, 160, 230, 200],
                    category=dpg.mvThemeCat_Plots,
                )

        with dpg.theme() as self._axis_line_theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line, [100, 100, 100, 140],
                    category=dpg.mvThemeCat_Plots,
                )

        # ── Window ──────────────────────────────────────────────────────
        with dpg.window(
            label="Stage Controls",
            tag="#StageControls",
            width=440,
            height=900,
            pos=(935, 10),
            no_scrollbar=False,
            no_resize=False,
            no_scroll_with_mouse=False,
        ):
            self.window_id = dpg.last_item()

            self._build_connection_section()
            dpg.add_separator()
            self._build_position_section()
            dpg.add_separator()
            self._build_keypad_section()
            dpg.add_separator()
            self._x_settings = self._build_axis_settings("x", "X")
            dpg.add_separator()
            self._y_settings = self._build_axis_settings("y", "Y")
            dpg.add_separator()
            self._z_settings = self._build_axis_settings("z", "Z")
            dpg.add_separator()
            self._build_developer_section()

        # ── Keypad item-handler registries (press-and-hold) ─────────────
        _keypad_btns = [
            ("_kp_neg_z", "z", "-"),
            ("_kp_pos_y", "y", "+"),
            ("_kp_pos_z", "z", "+"),
            ("_kp_neg_x", "x", "-"),
            ("_kp_neg_y", "y", "-"),
            ("_kp_pos_x", "x", "+"),
        ]
        for attr, axis, direction in _keypad_btns:
            btn_id = getattr(self, attr)
            tag = f"#StageKP_{attr}"
            with dpg.item_handler_registry(tag=tag):
                dpg.add_item_activated_handler(
                    callback=self._make_press_cb(axis, direction)
                )
                dpg.add_item_deactivated_handler(
                    callback=self._make_release_cb(axis, direction)
                )
            dpg.bind_item_handler_registry(btn_id, tag)

        # ── Global keyboard handlers (WASD + QE hold-to-move) ───────────
        with dpg.handler_registry(tag="#StageKeyHandlers"):
            for key_name, axis, direction in _KEY_AXIS_MAP:
                key = getattr(dpg, key_name)
                dpg.add_key_press_handler(
                    key=key,
                    callback=self._make_key_press_cb(axis, direction),
                )
                dpg.add_key_release_handler(
                    key=key,
                    callback=self._make_key_release_cb(axis, direction),
                )

        # ── Background snapshot polling thread ───────────────────────────
        threading.Thread(
            target=self._polling_loop, daemon=True, name="StageSnapshotPoll"
        ).start()

        atexit.register(self.cleanup)

        if shared_state.dev_mode:
            self._auto_connect_dev_mode()

    # ------------------------------------------------------------------
    # Background snapshot polling — runs on its own daemon thread
    # ------------------------------------------------------------------

    def _polling_loop(self):
        while self._polling:
            snaps = {ax: self._motors[ax].snapshot() for ax in ("x", "y", "z")}
            with self._snap_lock:
                self._snaps = snaps
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_connection_section(self):
        with dpg.tree_node(
            label="Connection", default_open=True, span_full_width=True
        ) as node:
            self.section_node_ids["connection"] = node

            for axis in ("x", "y", "z"):
                with dpg.group(horizontal=True):
                    dpg.add_text(f"{axis.upper()}:", indent=4)
                    combo = dpg.add_combo(
                        label="",
                        width=-112,
                        items=[],
                        default_value="",
                        tag=f"#Stage_{axis}_serial_combo",
                        callback=self._make_serial_selected_cb(axis),
                    )
                    setattr(self, f"_{axis}_serial_combo", combo)

                    refresh_btn = dpg.add_button(
                        label="", width=40,
                        callback=self._scan_devices,
                    )
                    dpg.bind_item_font(refresh_btn, self.icon_font)
                    with dpg.tooltip(refresh_btn):
                        dpg.add_text("Scan for connected motors")

                    conn_btn = dpg.add_button(
                        label="", width=40,
                        callback=self._make_toggle_cb(axis),
                    )
                    dpg.bind_item_font(conn_btn, self.icon_font)
                    setattr(self, f"_{axis}_conn_btn", conn_btn)
                    with dpg.tooltip(conn_btn):
                        setattr(
                            self, f"_{axis}_conn_tooltip",
                            dpg.add_text(f"Connect {axis.upper()} motor"),
                        )

                    ind = dpg.add_color_button(
                        label="",
                        default_value=(40, 40, 40, 255),
                        width=16, height=16,
                        enabled=False,
                        tag=f"#Stage_{axis}_indicator",
                    )
                    setattr(self, f"_{axis}_indicator", ind)

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Connect All", width=-110,
                    callback=self._connect_all,
                )
                dpg.add_button(
                    label="Disconnect All", width=-1,
                    callback=self._disconnect_all,
                )

    def _build_position_section(self):
        with dpg.tree_node(
            label="Position Map", default_open=True, span_full_width=True
        ) as node:
            self.section_node_ids["position_map"] = node

            r = _HALF_TRAVEL_MM

            with dpg.group(horizontal=True):
                # ── XY scatter map ─────────────────────────────────────
                with dpg.plot(
                    label="XY Stage Map",
                    tag="#Stage_XY_Plot",
                    width=_XY_PLOT_SIZE,
                    height=_XY_PLOT_SIZE,
                    no_title=True,
                    no_menus=True,
                    no_box_select=True,
                    equal_aspects=True,
                ):
                    self.xy_plot_id = dpg.last_item()

                    self._xy_x_axis = dpg.add_plot_axis(
                        dpg.mvXAxis, label="",
                        no_gridlines=False,
                        no_tick_labels=True,
                        no_tick_marks=True,
                    )
                    dpg.set_axis_limits(self._xy_x_axis, -r, r)

                    self._xy_y_axis = dpg.add_plot_axis(
                        dpg.mvYAxis, label="",
                        no_gridlines=False,
                        no_tick_labels=True,
                        no_tick_marks=True,
                    )
                    dpg.set_axis_limits(self._xy_y_axis, -r, r)

                    self._xy_h_line = dpg.add_line_series(
                        [-r, r], [0.0, 0.0],
                        label="", parent=self._xy_y_axis,
                    )
                    dpg.bind_item_theme(self._xy_h_line, self._axis_line_theme)

                    self._xy_v_line = dpg.add_line_series(
                        [0.0, 0.0], [-r, r],
                        label="", parent=self._xy_y_axis,
                    )
                    dpg.bind_item_theme(self._xy_v_line, self._axis_line_theme)

                    self._xy_pos_series = dpg.add_scatter_series(
                        [0.0], [0.0],
                        label="Stage XY",
                        parent=self._xy_y_axis,
                    )
                    dpg.bind_item_theme(self._xy_pos_series, self._pos_marker_theme)

                dpg.add_spacer(width=4)

                # ── Z bar indicator ────────────────────────────────────
                with dpg.plot(
                    label="Z Position",
                    tag="#Stage_Z_Plot",
                    width=_Z_BAR_WIDTH,
                    height=_XY_PLOT_SIZE,
                    no_title=True,
                    no_menus=True,
                    no_box_select=True,
                ):
                    self.z_plot_id = dpg.last_item()

                    self._z_x_axis = dpg.add_plot_axis(
                        dpg.mvXAxis, label="",
                        no_gridlines=True,
                        no_tick_labels=True,
                        no_tick_marks=True,
                    )
                    dpg.set_axis_limits(self._z_x_axis, -0.6, 0.6)

                    self._z_y_axis = dpg.add_plot_axis(
                        dpg.mvYAxis, label="",
                        no_tick_labels=True,
                        no_tick_marks=True,
                    )
                    dpg.set_axis_limits(self._z_y_axis, -r, r)

                    self._z_bar_series = dpg.add_bar_series(
                        [0.0], [0.0],
                        weight=0.9,
                        parent=self._z_y_axis,
                    )
                    dpg.bind_item_theme(self._z_bar_series, self._z_bar_theme)

                    self._z_ref_line = dpg.add_line_series(
                        [-0.6, 0.6], [0.0, 0.0],
                        label="", parent=self._z_y_axis,
                    )
                    dpg.bind_item_theme(self._z_ref_line, self._axis_line_theme)

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("X:")
                self._x_readout_id = dpg.add_text("--  mm", tag="#Stage_X_Readout")
                dpg.add_spacer(width=12)
                dpg.add_text("Y:")
                self._y_readout_id = dpg.add_text("--  mm", tag="#Stage_Y_Readout")
                dpg.add_spacer(width=12)
                dpg.add_text("Z:")
                self._z_readout_id = dpg.add_text("--  mm", tag="#Stage_Z_Readout")

    def _build_keypad_section(self):
        with dpg.tree_node(
            label="Keypad", default_open=True, span_full_width=True
        ) as node:
            self.section_node_ids["keypad"] = node

            with dpg.group(horizontal=True):
                dpg.add_text("Jog Speed")
                self._jog_speed_id = dpg.add_input_float(
                    label="mm/s", width=-1,
                    default_value=self._jog_speed,
                    min_value=0.001, min_clamped=True,
                    step=0.5, format="%.3f",
                    callback=self._on_jog_speed_changed,
                )

            dpg.add_spacer(height=4)

            btn_w = 82

            with dpg.group(horizontal=True):
                self._kp_neg_z = dpg.add_button(label="-Z", width=btn_w, height=34)
                dpg.add_spacer(width=4)
                self._kp_pos_y = dpg.add_button(label="+Y", width=btn_w, height=34)
                dpg.add_spacer(width=4)
                self._kp_pos_z = dpg.add_button(label="+Z", width=btn_w, height=34)

            dpg.add_spacer(height=2)

            with dpg.group(horizontal=True):
                self._kp_neg_x = dpg.add_button(label="-X", width=btn_w, height=34)
                dpg.add_spacer(width=4)
                self._kp_neg_y = dpg.add_button(label="-Y", width=btn_w, height=34)
                dpg.add_spacer(width=4)
                self._kp_pos_x = dpg.add_button(label="+X", width=btn_w, height=34)

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Home All", width=-110,
                    callback=self._home_all,
                )
                dpg.add_button(
                    label="Stop All", width=-1,
                    callback=self._stop_all,
                )

    def _build_axis_settings(self, axis: str, label: str) -> dict:
        w: dict = {}
        with dpg.tree_node(
            label=f"{label} Settings", default_open=False, span_full_width=True
        ) as node:
            self.section_node_ids[f"{axis}_settings"] = node

            w["state"] = dpg.add_input_text(
                label="State", width=-110,
                default_value="Disconnected", readonly=True,
            )
            w["position"] = dpg.add_input_text(
                label="Position", width=-110,
                default_value="--", readonly=True,
            )
            w["error"] = dpg.add_input_text(
                label="Error", width=-110,
                default_value="", readonly=True,
            )

            dpg.add_separator()
            dpg.add_text("Velocity Profile", color=[160, 160, 160, 220])

            w["max_velocity"] = dpg.add_input_float(
                label="Max Velocity", width=-110,
                default_value=5.0, min_value=0.001, min_clamped=True,
                step=0.5, format="%.3f mm/s",
            )
            w["acceleration"] = dpg.add_input_float(
                label="Acceleration", width=-110,
                default_value=5.0, min_value=0.001, min_clamped=True,
                step=0.5, format="%.3f mm/s\xb2",
            )

            dpg.add_separator()
            dpg.add_text("Jog Profile", color=[160, 160, 160, 220])

            w["jog_step"] = dpg.add_input_float(
                label="Step Size", width=-110,
                default_value=0.5, min_value=0.001, min_clamped=True,
                step=0.1, format="%.3f mm",
            )
            w["jog_max_vel"] = dpg.add_input_float(
                label="Jog Max Vel", width=-110,
                default_value=2.0, min_value=0.001, min_clamped=True,
                step=0.5, format="%.3f mm/s",
            )
            w["jog_accel"] = dpg.add_input_float(
                label="Jog Accel", width=-110,
                default_value=5.0, min_value=0.001, min_clamped=True,
                step=0.5, format="%.3f mm/s\xb2",
            )

            dpg.add_separator()
            dpg.add_text("Backlash Compensation", color=[160, 160, 160, 220])

            w["backlash"] = dpg.add_input_float(
                label="Backlash", width=-110,
                default_value=0.0, min_value=0.0, min_clamped=True,
                step=0.01, format="%.4f mm",
            )

            dpg.add_spacer(height=2)

            w["apply_btn"] = dpg.add_button(
                label=f"Apply {label} Settings", width=-1,
                callback=self._make_apply_cb(axis),
            )

        return w

    def _build_developer_section(self):
        with dpg.tree_node(
            label="Developer", default_open=False, span_full_width=True
        ) as node:
            self.section_node_ids["developer"] = node

            self._dev_widgets: dict = {}
            for axis in ("x", "y", "z"):
                dpg.add_text(f"{axis.upper()} axis", color=[160, 160, 160, 220])
                w = {}
                w["cmd_latency"] = dpg.add_input_text(
                    label=f"Cmd latency##{axis}", width=-110,
                    default_value="--", readonly=True,
                )
                w["poll_update"] = dpg.add_input_text(
                    label=f"Poll update##{axis}", width=-110,
                    default_value="--", readonly=True,
                )
                w["queue_est"] = dpg.add_input_text(
                    label=f"Queue depth##{axis}", width=-110,
                    default_value="--", readonly=True,
                )
                self._dev_widgets[axis] = w
                if axis != "z":
                    dpg.add_spacer(height=2)

    # ------------------------------------------------------------------
    # Callback factories
    # ------------------------------------------------------------------

    def _make_press_cb(self, axis: str, direction: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._on_keypad_press(axis, direction)
        return cb

    def _make_release_cb(self, axis: str, direction: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._on_keypad_release(axis, direction)
        return cb

    def _make_toggle_cb(self, axis: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._toggle_connection(axis)
        return cb

    def _make_serial_selected_cb(self, axis: str):
        def cb(sender, app_data, user_data=None):
            pass
        return cb

    def _make_apply_cb(self, axis: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._apply_axis_settings(axis)
        return cb

    def _input_is_focused(self) -> bool:
        """Return True if a text/numeric input widget currently has keyboard focus."""
        focused = dpg.get_focused_item()
        if focused <= 0:
            return False
        try:
            item_type = dpg.get_item_type(focused)
            return any(t in item_type for t in ("InputText", "InputFloat", "InputInt", "Combo"))
        except Exception:
            return False

    def _make_key_press_cb(self, axis: str, direction: str):
        def cb(sender=None, app_data=None, user_data=None):
            if self._input_is_focused():
                return
            if direction in self._jog_held[axis]:
                return  # key-repeat fired by DPG; already moving, ignore
            self._on_keypad_press(axis, direction)
        return cb

    def _make_key_release_cb(self, axis: str, direction: str):
        # Always process release so a key held while focus changes doesn't lock the motor.
        def cb(sender=None, app_data=None, user_data=None):
            self._on_keypad_release(axis, direction)
        return cb

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _auto_connect_dev_mode(self):
        """Enable simulator, discover devices, and connect all axes — off the render thread."""
        def _worker():
            KST101.enable_simulations()
            devices = KST101.list_devices()
            self._auto_connect_queue = devices
        threading.Thread(target=_worker, daemon=True, name="StageAutoConnect").start()

    def _scan_devices(self, sender=None, app_data=None, user_data=None):
        """Kick off a background device scan; the render loop populates combos when done."""
        def _worker():
            serials = KST101.list_devices()
            self._scan_result = serials if serials else []
        threading.Thread(target=_worker, daemon=True, name="StageDeviceScan").start()

    def _start_connect_thread(self, axis: str, serial: str):
        if self._connecting[axis] or self._motors[axis].connected:
            return
        self._connecting[axis] = True
        def _worker():
            self._motors[axis].connect(serial)
            self._connecting[axis] = False
        threading.Thread(
            target=_worker, daemon=True, name=f"StageConnect_{axis}"
        ).start()

    def _toggle_connection(self, axis: str):
        motor = self._motors[axis]
        if motor.connected:
            motor.disconnect()
        elif not self._connecting[axis]:
            serial = str(dpg.get_value(getattr(self, f"_{axis}_serial_combo"))).strip()
            if serial and serial != "(no devices found)":
                self._start_connect_thread(axis, serial)

    def _connect_all(self, sender=None, app_data=None, user_data=None):
        for axis in ("x", "y", "z"):
            if not self._motors[axis].connected and not self._connecting[axis]:
                serial = str(dpg.get_value(getattr(self, f"_{axis}_serial_combo"))).strip()
                if serial and serial != "(no devices found)":
                    self._start_connect_thread(axis, serial)

    def _disconnect_all(self, sender=None, app_data=None, user_data=None):
        for axis in ("x", "y", "z"):
            self._motors[axis].disconnect()

    # ------------------------------------------------------------------
    # Keypad
    # ------------------------------------------------------------------

    def _on_keypad_press(self, axis: str, direction: str):
        self._jog_held[axis].add(direction)
        self._apply_continuous_move(axis)

    def _on_keypad_release(self, axis: str, direction: str):
        self._jog_held[axis].discard(direction)
        self._apply_continuous_move(axis)

    def _apply_continuous_move(self, axis: str):
        motor = self._motors[axis]
        if not motor.connected:
            self._jog_held[axis].clear()
            return
        held = self._jog_held[axis]
        if "+" in held and "-" not in held:
            motor.move_continuous("+")
        elif "-" in held and "+" not in held:
            motor.move_continuous("-")
        else:
            motor.stop(immediate=False)

    def _on_jog_speed_changed(self, sender, app_data, user_data=None):
        self._jog_speed = max(0.001, float(app_data))
        for axis in ("x", "y", "z"):
            self._motors[axis].set_velocity_params(max_velocity=self._jog_speed)

    # ------------------------------------------------------------------
    # Global motion commands
    # ------------------------------------------------------------------

    def _home_all(self, sender=None, app_data=None, user_data=None):
        for axis in ("x", "y", "z"):
            if self._motors[axis].connected:
                self._motors[axis].home()

    def _stop_all(self, sender=None, app_data=None, user_data=None):
        for axis in ("x", "y", "z"):
            self._jog_held[axis].clear()
            if self._motors[axis].connected:
                self._motors[axis].stop(immediate=True)

    # ------------------------------------------------------------------
    # Axis settings
    # ------------------------------------------------------------------

    def _apply_axis_settings(self, axis: str):
        motor = self._motors[axis]
        if not motor.connected:
            return
        w = self._get_settings_widgets(axis)
        motor.set_velocity_params(
            acceleration=float(dpg.get_value(w["acceleration"])),
            max_velocity=float(dpg.get_value(w["max_velocity"])),
        )
        motor.set_jog_params(
            step_size=float(dpg.get_value(w["jog_step"])),
            acceleration=float(dpg.get_value(w["jog_accel"])),
            max_velocity=float(dpg.get_value(w["jog_max_vel"])),
        )
        motor.set_backlash(float(dpg.get_value(w["backlash"])))

    def _populate_settings_from_snapshot(self, axis: str, snap: dict):
        if self._settings_loading[axis]:
            return
        self._settings_loading[axis] = True
        try:
            w = self._get_settings_widgets(axis)
            vp = snap.get("velocity_params", {})
            jp = snap.get("jog_params", {})
            gp = snap.get("gen_move_params", {})

            if "max_velocity" in vp and vp["max_velocity"]:
                dpg.set_value(w["max_velocity"], float(vp["max_velocity"]))
            if "acceleration" in vp and vp["acceleration"]:
                dpg.set_value(w["acceleration"], float(vp["acceleration"]))
            if "step_size" in jp and jp["step_size"]:
                dpg.set_value(w["jog_step"], float(jp["step_size"]))
            if "max_velocity" in jp and jp["max_velocity"]:
                dpg.set_value(w["jog_max_vel"], float(jp["max_velocity"]))
            if "acceleration" in jp and jp["acceleration"]:
                dpg.set_value(w["jog_accel"], float(jp["acceleration"]))
            if "backlash" in gp:
                dpg.set_value(w["backlash"], float(gp["backlash"]))
        finally:
            self._settings_loading[axis] = False

    def _get_settings_widgets(self, axis: str) -> dict:
        return {"x": self._x_settings, "y": self._y_settings, "z": self._z_settings}[axis]

    # ------------------------------------------------------------------
    # Render loop — no SDK calls here, only DPG updates from cached data
    # ------------------------------------------------------------------

    def render(self):
        with self._snap_lock:
            frame = {ax: dict(self._snaps[ax]) for ax in ("x", "y", "z")}
        self._update_connection_ui(frame)
        self._update_position_plots(frame)
        self._update_position_readouts(frame)
        self._update_settings_status(frame)
        self._update_developer_stats(frame)
        self._update_keypad_highlights()
        self._resize_plots_to_window()

    def _update_developer_stats(self, frame: dict):
        if not hasattr(self, "_dev_widgets"):
            return
        for axis in ("x", "y", "z"):
            snap = frame[axis]
            w    = self._dev_widgets[axis]

            cmd_ms  = snap.get("cmd_latency_ms")
            poll_ms = snap.get("poll_update_ms")
            p_int   = snap.get("poll_interval_ms") or 1

            dpg.set_value(
                w["cmd_latency"],
                "--" if cmd_ms is None else f"{cmd_ms:.0f} ms",
            )
            dpg.set_value(
                w["poll_update"],
                "--" if poll_ms is None else f"{poll_ms:.0f} ms",
            )
            if poll_ms is not None:
                queue_est = max(0.0, poll_ms / p_int - 1.0)
                dpg.set_value(w["queue_est"], f"{queue_est:.1f}")
            else:
                dpg.set_value(w["queue_est"], "--")

    def _update_keypad_highlights(self):
        for (axis, direction), attr in _KEYPAD_BUTTON_MAP.items():
            btn_id = getattr(self, attr)
            held = direction in self._jog_held[axis]
            dpg.bind_item_theme(btn_id, selected_theme if held else None)

    def _update_connection_ui(self, frame: dict):
        # Consume async scan result (written by _scan_devices worker thread)
        scan = self._scan_result
        if scan is not None:
            self._scan_result = None
            items = scan if scan else ["(no devices found)"]
            for axis in ("x", "y", "z"):
                combo = getattr(self, f"_{axis}_serial_combo")
                current = str(dpg.get_value(combo)).strip()
                dpg.configure_item(combo, items=items)
                if current not in scan:
                    dpg.set_value(combo, items[0])

        # Consume dev-mode auto-connect queue (written by _auto_connect_dev_mode worker)
        queue = self._auto_connect_queue
        if queue is not None:
            self._auto_connect_queue = None
            items = queue if queue else []
            for i, axis in enumerate(("x", "y", "z")):
                combo = getattr(self, f"_{axis}_serial_combo")
                dpg.configure_item(combo, items=items)
                if i < len(queue):
                    dpg.set_value(combo, queue[i])
                    self._start_connect_thread(axis, queue[i])

        for axis in ("x", "y", "z"):
            snap      = frame[axis]
            connected  = snap.get("connected", False)
            connecting = self._connecting[axis]

            ind   = getattr(self, f"_{axis}_indicator")
            btn   = getattr(self, f"_{axis}_conn_btn")
            tip   = getattr(self, f"_{axis}_conn_tooltip")
            combo = getattr(self, f"_{axis}_serial_combo")

            dpg.configure_item(combo, enabled=not connected and not connecting)
            dpg.configure_item(btn,   enabled=not connecting)

            if connected:
                dpg.set_item_label(btn, "")
                dpg.set_value(tip, f"Disconnect {axis.upper()} motor")
                dpg.configure_item(ind, default_value=(0, 180, 0, 255))
            elif connecting:
                dpg.set_item_label(btn, "")
                dpg.set_value(tip, f"Connecting {axis.upper()}...")
                dpg.configure_item(ind, default_value=(200, 150, 0, 255))
            else:
                dpg.set_item_label(btn, "")
                dpg.set_value(tip, f"Connect {axis.upper()} motor")
                dpg.configure_item(ind, default_value=(40, 40, 40, 255))

            # Populate settings widgets once when params arrive after connect
            if connected:
                params_ready = bool(snap.get("velocity_params"))
                if params_ready and self._settings_populated[axis] != id(self._motors[axis]):
                    self._populate_settings_from_snapshot(axis, snap)
                    self._settings_populated[axis] = id(self._motors[axis])
            else:
                self._settings_populated[axis] = -1

    def _update_position_plots(self, frame: dict):
        x_mm = float(frame["x"].get("position") or 0.0)
        y_mm = float(frame["y"].get("position") or 0.0)
        z_mm = float(frame["z"].get("position") or 0.0)
        dpg.set_value(self._xy_pos_series, [[x_mm], [y_mm]])
        dpg.set_value(self._z_bar_series,  [[0.0],  [z_mm]])

    def _update_position_readouts(self, frame: dict):
        for axis, attr in (
            ("x", "_x_readout_id"),
            ("y", "_y_readout_id"),
            ("z", "_z_readout_id"),
        ):
            snap = frame[axis]
            pos  = snap.get("position")
            text = "--  mm" if (pos is None or not snap.get("connected")) else f"{float(pos):.4f} mm"
            dpg.set_value(getattr(self, attr), text)

    def _update_settings_status(self, frame: dict):
        for axis in ("x", "y", "z"):
            snap    = frame[axis]
            w       = self._get_settings_widgets(axis)
            enabled = snap.get("connected", False)

            dpg.set_value(w["state"], str(snap.get("state") or "Disconnected"))
            pos = snap.get("position")
            dpg.set_value(
                w["position"],
                "--" if pos is None else f"{float(pos):.4f} mm",
            )
            dpg.set_value(w["error"], str(snap.get("last_error") or ""))

            for wid_key in ("max_velocity", "acceleration", "jog_step",
                            "jog_max_vel", "jog_accel", "backlash", "apply_btn"):
                dpg.configure_item(w[wid_key], enabled=enabled)

    def _resize_plots_to_window(self):
        w = dpg.get_item_width(self.window_id)
        if w == self._last_window_width or w <= 0:
            return
        self._last_window_width = w
        available = w - 8 - 4 - _Z_BAR_WIDTH - 8
        xy_size = min(400, max(160, available))
        dpg.configure_item(self.xy_plot_id, width=xy_size, height=xy_size)
        dpg.configure_item(self.z_plot_id,  height=xy_size)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self):
        """Stop the polling thread and release all hardware resources."""
        self._polling = False
        for axis in ("x", "y", "z"):
            motor = self._motors[axis]
            if motor.connected:
                motor.stop(immediate=True)
                motor.disconnect()
        if shared_state.dev_mode:
            KST101.disable_simulations()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window":   capture_window_state(self.window_id),
                "sections": capture_item_open_states(self.section_node_ids),
                "serials": {
                    ax: str(dpg.get_value(getattr(self, f"_{ax}_serial_combo"))).strip()
                    for ax in ("x", "y", "z")
                },
                "jog_speed_mm": float(dpg.get_value(self._jog_speed_id)),
                "x_settings":   self._capture_axis_settings("x"),
                "y_settings":   self._capture_axis_settings("y"),
                "z_settings":   self._capture_axis_settings("z"),
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))
        apply_item_open_states(self.section_node_ids, state.get("sections"))

        # Restore saved serial numbers without scanning hardware at load time
        serials = state.get("serials", {})
        if serials:
            for axis in ("x", "y", "z"):
                saved = str(serials.get(axis, "")).strip()
                combo = getattr(self, f"_{axis}_serial_combo")
                if saved:
                    dpg.configure_item(combo, items=[saved])
                    dpg.set_value(combo, saved)

        if "jog_speed_mm" in state:
            v = max(0.001, float(state["jog_speed_mm"]))
            dpg.set_value(self._jog_speed_id, v)
            self._jog_speed = v

        for axis in ("x", "y", "z"):
            key = f"{axis}_settings"
            if key in state:
                self._restore_axis_settings(axis, state[key])

    # ------------------------------------------------------------------
    # Settings persistence helpers
    # ------------------------------------------------------------------

    def _capture_axis_settings(self, axis: str) -> dict:
        w = self._get_settings_widgets(axis)
        return {
            "max_velocity": float(dpg.get_value(w["max_velocity"])),
            "acceleration": float(dpg.get_value(w["acceleration"])),
            "jog_step":     float(dpg.get_value(w["jog_step"])),
            "jog_max_vel":  float(dpg.get_value(w["jog_max_vel"])),
            "jog_accel":    float(dpg.get_value(w["jog_accel"])),
            "backlash":     float(dpg.get_value(w["backlash"])),
        }

    def _restore_axis_settings(self, axis: str, saved: dict):
        w = self._get_settings_widgets(axis)
        if "max_velocity" in saved:
            dpg.set_value(w["max_velocity"], max(0.001, float(saved["max_velocity"])))
        if "acceleration" in saved:
            dpg.set_value(w["acceleration"], max(0.001, float(saved["acceleration"])))
        if "jog_step" in saved:
            dpg.set_value(w["jog_step"], max(0.001, float(saved["jog_step"])))
        if "jog_max_vel" in saved:
            dpg.set_value(w["jog_max_vel"], max(0.001, float(saved["jog_max_vel"])))
        if "jog_accel" in saved:
            dpg.set_value(w["jog_accel"], max(0.001, float(saved["jog_accel"])))
        if "backlash" in saved:
            dpg.set_value(w["backlash"], max(0.0, float(saved["backlash"])))
