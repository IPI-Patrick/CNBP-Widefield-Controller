"""
Stage Controls window — XYZ motorised microscope stage via three KST101 drivers.

Layout
------
  Connection      — serial-number combos + connect/disconnect per axis
  Position Map    — square XY scatter plot and vertical Z bar side-by-side
  Keypad          — 2×3 hold-to-move button grid above the position map
    Speeds          — XY/Z fast and slow continuous-jog speed presets
    Auto Focus      — Z autofocus action and search settings
  X / Y / Z Settings — per-axis velocity, jog, backlash parameters
"""

import atexit
import threading
import time

import dearpygui.dearpygui as dpg
import numpy as np

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

_Z_BAR_WIDTH  = 50
_AUTOFOCUS_PLOT_HEIGHT = 220
_CONNECT_RETRY_ATTEMPTS = 3
_CONNECT_RETRY_DELAY_S = 1.0
_AUTOFOCUS_MOVE_TIMEOUT_MIN_S = 20.0
_AUTOFOCUS_MOVE_TIMEOUT_BUFFER_S = 5.0
_AUTOFOCUS_MOVE_TIMEOUT_MAX_S = 90.0
_AUTOFOCUS_SETTLE_MIN_S = 0.10
_AUTOFOCUS_MIN_ACCEL_MM_S2 = 1.0
_AUTOFOCUS_POSITION_TOLERANCE_MM = 0.01
_AUTOFOCUS_NEAR_TARGET_TOLERANCE_MM = 0.06
_AUTOFOCUS_STABLE_POSITION_TIME_S = 0.30
_AUTOFOCUS_SURFACE_PROMINENCE_RATIO = 0.15
_AUTOFOCUS_MAX_SURFACE_MARKERS = 8

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

    _DEFAULT_AXIS_SETTINGS = {
        "max_velocity": 1.0,
        "acceleration": 5.0,
        "jog_step": 0.1,
        "jog_max_vel": 1.0,
        "jog_accel": 5.0,
        "backlash": 0.0,
    }

    _DEFAULT_SPEEDS_MM_S = {
        "xy_fast": 2.0,
        "xy_slow": 0.5,
        "z_fast": 1.0,
        "z_slow": 0.25,
    }
    _DEFAULT_AUTOFOCUS_SETTINGS = {
        "search_range_mm": 0.5,
        "coarse_step_mm": 0.2,
        "fine_step_mm": 0.05,
        "settle_time_s": 0.25,
        "frames_per_position": 2,
        "roi_fraction": 0.5,
        "focus_to_top_surface": False,
        "always_calculate_focus_level": False,
    }

    def __init__(self):
        self._motors = {
            "x": KST101(),
            "y": KST101(),
            "z": KST101(),
        }
        shared_state.shared_stage = self._motors

        self._jog_held: dict = {"x": set(), "y": set(), "z": set()}
        self._shift_held_keys: set[str] = set()
        self._speeds_mm_s: dict = dict(self._DEFAULT_SPEEDS_MM_S)
        self._autofocus_settings: dict = dict(self._DEFAULT_AUTOFOCUS_SETTINGS)
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
        self._startup_auto_connect_queued = False
        self._autofocus_thread = None
        self._autofocus_stop_event = threading.Event()
        self._autofocus_running = False
        self._autofocus_status = "Idle"
        self._autofocus_error = ""
        self._autofocus_best_score = None
        self._autofocus_best_z = None
        self._autofocus_current_focus_level = None
        self._autofocus_current_focus_frame_idx = -1
        self._autofocus_progress = 0.0
        self._autofocus_plot_state = {
            "coarse": {
                "z_values": [],
                "focus_scores": [],
                "center_z": 0.0,
                "half_range_mm": float(self._DEFAULT_AUTOFOCUS_SETTINGS["search_range_mm"]),
            },
            "fine": {
                "z_values": [],
                "focus_scores": [],
                "center_z": 0.0,
                "half_range_mm": float(self._DEFAULT_AUTOFOCUS_SETTINGS["fine_step_mm"]),
            },
        }
        self._autofocus_surface_z_values: list[float] = []

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

        with dpg.theme() as self._autofocus_marker_theme:
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
                    dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Circle,
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_MarkerSize, 4.5,
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

        with dpg.theme() as self._surface_line_theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line, [255, 140, 0, 220],
                    category=dpg.mvThemeCat_Plots,
                )

        with dpg.theme() as self._focus_target_line_theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line, [0, 180, 120, 230],
                    category=dpg.mvThemeCat_Plots,
                )

        # ── Stage settings tab ──────────────────────────────────────────
        _stage_tab = shared_state.layout_containers.get("stage_tab")
        if _stage_tab:
            self.window_id = _stage_tab
        else:
            self.window_id = dpg.add_window(
                label="Stage Controls",
                tag="#StageControls",
                width=440,
                height=900,
                pos=(935, 10),
                no_scrollbar=False,
                no_resize=False,
                no_scroll_with_mouse=False,
            )
        dpg.push_container_stack(self.window_id)
        if True:
            self._build_connection_section()
            dpg.add_separator()
            self._build_keypad_section()
            dpg.add_separator()
            self._build_speeds_section()
            dpg.add_separator()
            self._build_autofocus_section()
            dpg.add_separator()
            self._x_settings = self._build_axis_settings("x", "X")
            dpg.add_separator()
            self._y_settings = self._build_axis_settings("y", "Y")
            dpg.add_separator()
            self._z_settings = self._build_axis_settings("z", "Z")
            dpg.add_separator()
            self._build_developer_section()
        dpg.pop_container_stack()

        # ── Position map (right sidebar or fallback to stage window) ────
        _pos_map = shared_state.layout_containers.get("right_position_map")
        _pos_container = _pos_map if _pos_map else self.window_id
        dpg.push_container_stack(_pos_container)
        self._build_position_section()
        dpg.pop_container_stack()

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
            dpg.add_key_press_handler(
                key=dpg.mvKey_LShift,
                callback=self._make_shift_press_cb("left"),
            )
            dpg.add_key_press_handler(
                key=dpg.mvKey_RShift,
                callback=self._make_shift_press_cb("right"),
            )
            dpg.add_key_release_handler(
                key=dpg.mvKey_LShift,
                callback=self._make_shift_release_cb("left"),
            )
            dpg.add_key_release_handler(
                key=dpg.mvKey_RShift,
                callback=self._make_shift_release_cb("right"),
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
                        label="\uE117", width=40,
                        callback=self._scan_devices,
                    )
                    dpg.bind_item_font(refresh_btn, self.icon_font)
                    with dpg.tooltip(refresh_btn):
                        dpg.add_text("Scan for connected motors")

                    conn_btn = dpg.add_button(
                        label="\uE8CD", width=40,
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
        # Two-column table: XY plot (stretch, left) | Z bar (fixed width, right).
        # Both plots use height=-1 so they fill 100% of the section height.
        with dpg.table(
            header_row=False,
            borders_innerV=False,
            borders_outerV=False,
            borders_innerH=False,
            borders_outerH=False,
            policy=dpg.mvTable_SizingFixedFit,
            height=-1,
        ):
            dpg.add_table_column(label="XY", width_stretch=True)
            dpg.add_table_column(label="Z",  width_fixed=True, init_width_or_weight=_Z_BAR_WIDTH)

            with dpg.table_row():
                # ── XY scatter map (left) ──────────────────────────────
                with dpg.table_cell():
                    with dpg.plot(
                        label="XY Stage Map",
                        tag="#Stage_XY_Plot",
                        width=-1,
                        height=-1,
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
                        dpg.set_axis_limits(self._xy_x_axis, 0.0, _TRAVEL_MM)

                        self._xy_y_axis = dpg.add_plot_axis(
                            dpg.mvYAxis, label="",
                            no_gridlines=False,
                            no_tick_labels=True,
                            no_tick_marks=True,
                        )
                        dpg.set_axis_limits(self._xy_y_axis, 0.0, _TRAVEL_MM)

                        self._xy_h_line = dpg.add_line_series(
                            [0.0, _TRAVEL_MM], [_HALF_TRAVEL_MM, _HALF_TRAVEL_MM],
                            label="", parent=self._xy_y_axis,
                        )
                        dpg.bind_item_theme(self._xy_h_line, self._axis_line_theme)

                        self._xy_v_line = dpg.add_line_series(
                            [_HALF_TRAVEL_MM, _HALF_TRAVEL_MM], [0.0, _TRAVEL_MM],
                            label="", parent=self._xy_y_axis,
                        )
                        dpg.bind_item_theme(self._xy_v_line, self._axis_line_theme)

                        self._xy_pos_series = dpg.add_scatter_series(
                            [0.0], [0.0],
                            label="Stage XY",
                            parent=self._xy_y_axis,
                        )
                        dpg.bind_item_theme(self._xy_pos_series, self._pos_marker_theme)

                    # X/Y readout overlaid at the bottom-left of the XY cell
                    with dpg.group(horizontal=True):
                        dpg.add_text("X:")
                        self._x_readout_id = dpg.add_text("--  mm", tag="#Stage_X_Readout")
                        dpg.add_spacer(width=8)
                        dpg.add_text("Y:")
                        self._y_readout_id = dpg.add_text("--  mm", tag="#Stage_Y_Readout")

                # ── Z bar indicator (right) ────────────────────────────
                with dpg.table_cell():
                    with dpg.plot(
                        label="Z Position",
                        tag="#Stage_Z_Plot",
                        width=-1,
                        height=-1,
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
                        dpg.set_axis_limits(self._z_y_axis, 0.0, _TRAVEL_MM)

                        self._z_bar_series = dpg.add_bar_series(
                            [0.0], [0.0],
                            weight=0.9,
                            parent=self._z_y_axis,
                        )
                        dpg.bind_item_theme(self._z_bar_series, self._z_bar_theme)

                        self._z_ref_line = dpg.add_line_series(
                            [-0.6, 0.6], [_HALF_TRAVEL_MM, _HALF_TRAVEL_MM],
                            label="", parent=self._z_y_axis,
                        )
                        dpg.bind_item_theme(self._z_ref_line, self._axis_line_theme)

                        self._z_surface_lines = []
                        for index in range(_AUTOFOCUS_MAX_SURFACE_MARKERS):
                            surface_line = dpg.add_line_series(
                                [], [],
                                label=f"Surface {index + 1}",
                                parent=self._z_y_axis,
                            )
                            dpg.bind_item_theme(surface_line, self._surface_line_theme)
                            self._z_surface_lines.append(surface_line)

                        self._z_best_focus_line = dpg.add_line_series(
                            [], [],
                            label="Best Focus",
                            parent=self._z_y_axis,
                        )
                        dpg.bind_item_theme(self._z_best_focus_line, self._focus_target_line_theme)

                    # Z readout below the Z bar
                    with dpg.group(horizontal=True):
                        dpg.add_text("Z:")
                        self._z_readout_id = dpg.add_text("--  mm", tag="#Stage_Z_Readout")

    def _build_keypad_section(self):
        with dpg.tree_node(
            label="Keypad", default_open=True, span_full_width=True
        ) as node:
            self.section_node_ids["keypad"] = node

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
                self._autofocus_button_id = dpg.add_button(
                    label="Auto Focus",
                    width=-44,
                    height=34,
                    callback=self._start_autofocus,
                )
                self._autofocus_progress_id = dpg.add_progress_bar(
                    default_value=0.0,
                    width=-44,
                    height=34,
                    overlay="Auto Focus",
                    show=False,
                )
                self._autofocus_stop_btn = dpg.add_button(
                    label="Stop",
                    width=40,
                    height=34,
                    callback=self._stop_autofocus,
                    enabled=False,
                )

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                self._home_all_btn = dpg.add_button(
                    label="Home All", width=-110,
                    callback=self._home_all,
                )
                self._stop_all_btn = dpg.add_button(
                    label="Stop All", width=-1,
                    callback=self._stop_all,
                )

    def _build_autofocus_section(self):
        with dpg.tree_node(
            label="Auto Focus", default_open=False, span_full_width=True
        ) as node:
            self.section_node_ids["autofocus"] = node

            self._autofocus_widgets = {}
            self._autofocus_widgets["status"] = dpg.add_input_text(
                label="Status", width=-110,
                default_value="Idle", readonly=True,
            )
            self._autofocus_widgets["best_z"] = dpg.add_input_text(
                label="Best Z", width=-110,
                default_value="--", readonly=True,
            )
            self._autofocus_widgets["best_score"] = dpg.add_input_text(
                label="Best Score", width=-110,
                default_value="--", readonly=True,
            )
            self._autofocus_widgets["current_focus_level"] = dpg.add_input_text(
                label="Current Focus", width=-110,
                default_value="--", readonly=True,
            )
            self._autofocus_widgets["error"] = dpg.add_input_text(
                label="Error", width=-110,
                default_value="", readonly=True,
            )

            dpg.add_separator()
            self._autofocus_plot_widgets = {}
            with dpg.group():
                self._build_autofocus_phase_plot("coarse", "Rough Focus")
                dpg.add_spacer(height=4)
                self._build_autofocus_phase_plot("fine", "Fine Focus")

        dpg.add_separator()

        with dpg.tree_node(
            label="Auto Focus Settings", default_open=False, span_full_width=True
        ) as node:
            self.section_node_ids["autofocus_settings"] = node

            dpg.add_text("Tenengrad autofocus on live camera frames", color=[160, 160, 160, 220])

            self._autofocus_widgets["search_range_mm"] = dpg.add_input_float(
                label="Range +/-", width=-110,
                default_value=self._autofocus_settings["search_range_mm"],
                min_value=0.05, min_clamped=True,
                step=0.1, format="%.3f mm",
            )
            self._autofocus_widgets["coarse_step_mm"] = dpg.add_input_float(
                label="Coarse Step", width=-110,
                default_value=self._autofocus_settings["coarse_step_mm"],
                min_value=0.01, min_clamped=True,
                step=0.05, format="%.3f mm",
            )
            self._autofocus_widgets["fine_step_mm"] = dpg.add_input_float(
                label="Fine Step", width=-110,
                default_value=self._autofocus_settings["fine_step_mm"],
                min_value=0.001, min_clamped=True,
                step=0.01, format="%.3f mm",
            )
            self._autofocus_widgets["settle_time_s"] = dpg.add_input_float(
                label="Settle Time", width=-110,
                default_value=self._autofocus_settings["settle_time_s"],
                min_value=0.01, min_clamped=True,
                step=0.05, format="%.3f s",
            )
            self._autofocus_widgets["frames_per_position"] = dpg.add_input_int(
                label="Frames / Pos", width=-110,
                default_value=int(self._autofocus_settings["frames_per_position"]),
                min_value=1, min_clamped=True,
                step=1,
            )
            self._autofocus_widgets["roi_fraction"] = dpg.add_input_float(
                label="ROI Fraction", width=-110,
                default_value=self._autofocus_settings["roi_fraction"],
                min_value=0.1, max_value=1.0,
                min_clamped=True, max_clamped=True,
                step=0.1, format="%.2f",
            )
            self._autofocus_widgets["focus_to_top_surface"] = dpg.add_checkbox(
                label="Focus to Top Surface",
                default_value=bool(self._autofocus_settings["focus_to_top_surface"]),
            )
            self._autofocus_widgets["always_calculate_focus_level"] = dpg.add_checkbox(
                label="Always calculate focus level",
                default_value=bool(self._autofocus_settings["always_calculate_focus_level"]),
            )

    def _build_speeds_section(self):
        with dpg.tree_node(
            label="Speeds", default_open=True, span_full_width=True
        ) as node:
            self.section_node_ids["speeds"] = node

            self._speed_input_ids = {}
            fields = [
                ("xy_fast", "Jog X/Y Fast", self._DEFAULT_SPEEDS_MM_S["xy_fast"]),
                ("xy_slow", "X/Y Slow", self._DEFAULT_SPEEDS_MM_S["xy_slow"]),
                ("z_fast", "Z Fast", self._DEFAULT_SPEEDS_MM_S["z_fast"]),
                ("z_slow", "Z Slow", self._DEFAULT_SPEEDS_MM_S["z_slow"]),
            ]
            for key, label, default in fields:
                self._speed_input_ids[key] = dpg.add_input_float(
                    label=label,
                    width=-110,
                    default_value=default,
                    min_value=0.001,
                    min_clamped=True,
                    step=0.25,
                    format="%.3f mm/s",
                    callback=self._make_speed_changed_cb(key),
                )

    def _build_autofocus_phase_plot(self, phase: str, label: str):
        with dpg.plot(
            label=label,
            width=-1,
            height=_AUTOFOCUS_PLOT_HEIGHT,
            no_menus=True,
            no_box_select=True,
        ):
            plot_id = dpg.last_item()

            x_axis = dpg.add_plot_axis(
                dpg.mvXAxis,
                label="Z (mm)",
            )
            y_axis = dpg.add_plot_axis(
                dpg.mvYAxis,
                label="",
                no_tick_labels=True,
                no_tick_marks=True,
            )

            center_series = dpg.add_line_series(
                [0.0, 0.0], [0.0, 1.0],
                label="Center Z",
                parent=y_axis,
            )
            dpg.bind_item_theme(center_series, self._axis_line_theme)

            score_series = dpg.add_line_series(
                [], [],
                label="Focus Score",
                parent=y_axis,
            )
            score_points_series = dpg.add_scatter_series(
                [], [],
                label="Samples",
                parent=y_axis,
            )
            dpg.bind_item_theme(score_points_series, self._autofocus_marker_theme)

            fine_focus_series = dpg.add_line_series(
                [], [],
                label="Fine Focus" if phase == "coarse" else "Best Focus",
                parent=y_axis,
            )
            dpg.bind_item_theme(fine_focus_series, self._focus_target_line_theme)

            surface_lines = []
            if phase == "coarse":
                for index in range(_AUTOFOCUS_MAX_SURFACE_MARKERS):
                    surface_line = dpg.add_line_series(
                        [], [],
                        label=f"Surface {index + 1}",
                        parent=y_axis,
                    )
                    dpg.bind_item_theme(surface_line, self._surface_line_theme)
                    surface_lines.append(surface_line)

        self._autofocus_plot_widgets[phase] = {
            "plot_id": plot_id,
            "x_axis": x_axis,
            "y_axis": y_axis,
            "center_series": center_series,
            "score_series": score_series,
            "score_points_series": score_points_series,
            "fine_focus_series": fine_focus_series,
            "surface_lines": surface_lines,
        }

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

            with dpg.group(horizontal=True):
                w["apply_btn"] = dpg.add_button(
                    label=f"Apply {label} Settings", width=-76,
                    callback=self._make_apply_cb(axis),
                )
                w["reset_btn"] = dpg.add_button(
                    label="Reset", width=-1,
                    callback=self._make_reset_axis_settings_cb(axis),
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

    def _make_reset_axis_settings_cb(self, axis: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._reset_axis_settings(axis)
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

    def _make_shift_press_cb(self, key_name: str):
        def cb(sender=None, app_data=None, user_data=None):
            if key_name in self._shift_held_keys:
                return
            self._shift_held_keys.add(key_name)
            self._refresh_active_jog_speeds()
        return cb

    def _make_shift_release_cb(self, key_name: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._shift_held_keys.discard(key_name)
            self._refresh_active_jog_speeds()
        return cb

    def _make_speed_changed_cb(self, speed_key: str):
        def cb(sender=None, app_data=None, user_data=None):
            self._on_speed_changed(speed_key, app_data)
        return cb

    def _set_autofocus_result(
        self,
        *,
        status=None,
        error=None,
        best_score=None,
        best_z=None,
        running=None,
        progress=None,
        clear_best=False,
    ):
        with self._snap_lock:
            if status is not None:
                self._autofocus_status = str(status)
            if error is not None:
                self._autofocus_error = str(error)
            if clear_best:
                self._autofocus_best_score = None
                self._autofocus_best_z = None
            if best_score is not None:
                self._autofocus_best_score = float(best_score)
            if best_z is not None:
                self._autofocus_best_z = float(best_z)
            if running is not None:
                self._autofocus_running = bool(running)
            if progress is not None:
                self._autofocus_progress = min(1.0, max(0.0, float(progress)))

    def _reset_autofocus_plot(self, center_z: float, half_range_mm: float):
        with self._snap_lock:
            self._autofocus_plot_state["coarse"] = {
                "z_values": [],
                "focus_scores": [],
                "center_z": float(center_z),
                "half_range_mm": max(0.05, float(half_range_mm)),
            }
            self._autofocus_plot_state["fine"] = {
                "z_values": [],
                "focus_scores": [],
                "center_z": float(center_z),
                "half_range_mm": max(0.05, float(self._DEFAULT_AUTOFOCUS_SETTINGS["fine_step_mm"])),
            }
            self._autofocus_surface_z_values = []

    def _set_autofocus_plot_phase_window(self, phase: str, center_z: float, half_range_mm: float):
        with self._snap_lock:
            plot_state = self._autofocus_plot_state[phase]
            plot_state["center_z"] = float(center_z)
            plot_state["half_range_mm"] = max(0.05, float(half_range_mm))

    def _append_autofocus_plot_point(self, phase: str, z_value: float, focus_score: float):
        with self._snap_lock:
            plot_state = self._autofocus_plot_state[phase]
            plot_state["z_values"].append(float(z_value))
            plot_state["focus_scores"].append(float(focus_score))

    def _collect_autofocus_settings(self) -> dict:
        widgets = self._autofocus_widgets
        settings = {
            "search_range_mm": max(0.05, float(dpg.get_value(widgets["search_range_mm"]))),
            "coarse_step_mm": max(0.01, float(dpg.get_value(widgets["coarse_step_mm"]))),
            "fine_step_mm": max(0.001, float(dpg.get_value(widgets["fine_step_mm"]))),
            "settle_time_s": max(_AUTOFOCUS_SETTLE_MIN_S, float(dpg.get_value(widgets["settle_time_s"]))),
            "frames_per_position": max(1, int(dpg.get_value(widgets["frames_per_position"]))),
            "roi_fraction": min(1.0, max(0.1, float(dpg.get_value(widgets["roi_fraction"])))),
            "focus_to_top_surface": bool(dpg.get_value(widgets["focus_to_top_surface"])),
            "always_calculate_focus_level": bool(dpg.get_value(widgets["always_calculate_focus_level"])),
        }
        if settings["fine_step_mm"] > settings["coarse_step_mm"]:
            settings["fine_step_mm"] = settings["coarse_step_mm"]
        self._autofocus_settings = dict(settings)
        return settings

    def _start_autofocus(self, sender=None, app_data=None, user_data=None):
        if self._autofocus_running:
            return

        z_motor = self._motors["z"]
        z_snap = z_motor.snapshot()
        if not z_snap.get("connected", False):
            self._set_autofocus_result(status="Idle", error="Connect Z before autofocus.", running=False)
            return
        if not z_snap.get("homed", False):
            self._set_autofocus_result(status="Idle", error="Home Z before autofocus.", running=False)
            return

        andor = getattr(shared_state, "shared_andor", None)
        if andor is None:
            self._set_autofocus_result(status="Idle", error="Camera is not available.", running=False)
            return

        with andor.frame_lock:
            frame_ready = getattr(andor, "frameIdx", 0) > 0 and getattr(andor, "latest_frame", None) is not None
            capture_running = bool(getattr(andor, "is_capturing", False))
        if not frame_ready or not capture_running:
            self._set_autofocus_result(status="Idle", error="Start camera preview before autofocus.", running=False)
            return

        settings = self._collect_autofocus_settings()
        start_z = float(z_snap.get("position") or 0.0)
        self._reset_autofocus_plot(start_z, settings["search_range_mm"])
        self._autofocus_stop_event.clear()
        self._set_autofocus_result(status="Running", error="", running=True, progress=0.0, clear_best=True)

        def _worker():
            try:
                best_z, best_score = self._run_autofocus(andor, z_motor, settings)
                self._set_autofocus_result(
                    status="Complete",
                    error="",
                    best_score=best_score,
                    best_z=best_z,
                    running=False,
                    progress=1.0,
                )
            except RuntimeError as exc:
                if str(exc) == "Autofocus stopped.":
                    self._set_autofocus_result(status="Stopped", error="", running=False, progress=0.0)
                else:
                    self._set_autofocus_result(status="Idle", error=str(exc), running=False, progress=0.0)
            except Exception as exc:
                self._set_autofocus_result(status="Idle", error=str(exc), running=False, progress=0.0)

        self._autofocus_thread = threading.Thread(
            target=_worker,
            daemon=True,
            name="StageAutofocus",
        )
        self._autofocus_thread.start()

    def _stop_autofocus(self, sender=None, app_data=None, user_data=None):
        if not self._autofocus_running:
            return
        self._autofocus_stop_event.set()
        self._set_autofocus_result(status="Stopping...")

    def _run_autofocus(self, andor, z_motor, settings: dict) -> tuple[float, float]:
        start_snap = z_motor.snapshot()
        start_z = float(start_snap.get("position") or 0.0)
        original_velocity = dict(start_snap.get("velocity_params") or {})
        coarse_velocity = self._get_autofocus_motion_profile("coarse", original_velocity)
        fine_velocity = self._get_autofocus_motion_profile("fine", original_velocity, andor=andor, settings=settings)

        try:
            coarse_positions = self._build_autofocus_positions(
                center_z=start_z,
                half_range_mm=settings["search_range_mm"],
                step_mm=settings["coarse_step_mm"],
            )
            self._set_autofocus_velocity(z_motor, coarse_velocity)
            if coarse_positions:
                coarse_scan_start = float(coarse_positions[0])
                self._set_autofocus_result(
                    status=f"Moving to rough scan start Z={coarse_scan_start:.4f} mm",
                    progress=0.0,
                )
                self._move_z_and_wait(z_motor, coarse_scan_start, settings["settle_time_s"])
            coarse_results = self._scan_focus_positions(
                andor,
                z_motor,
                coarse_positions,
                settings,
                phase="coarse",
                progress_start=0.0,
                progress_span=0.7,
            )
            self._set_autofocus_surface_markers(self._identify_autofocus_surfaces(coarse_results))
            best_coarse_z, _ = self._select_coarse_focus_target(
                coarse_results,
                focus_to_top_surface=bool(settings.get("focus_to_top_surface", False)),
            )

            fine_span = max(settings["coarse_step_mm"], settings["fine_step_mm"])
            fine_positions = self._build_autofocus_positions(
                center_z=best_coarse_z,
                half_range_mm=fine_span,
                step_mm=settings["fine_step_mm"],
            )
            self._set_autofocus_plot_phase_window("fine", best_coarse_z, fine_span)
            if fine_positions:
                fine_scan_start = float(fine_positions[0])
                self._set_autofocus_result(
                    status=f"Moving to fine scan start Z={fine_scan_start:.4f} mm",
                    progress=0.7,
                )
                self._set_autofocus_velocity(z_motor, coarse_velocity)
                self._move_z_and_wait(z_motor, fine_scan_start, settings["settle_time_s"])
            self._set_autofocus_velocity(z_motor, fine_velocity)
            fine_results = self._scan_focus_positions(
                andor,
                z_motor,
                fine_positions,
                settings,
                phase="fine",
                progress_start=0.7,
                progress_span=0.25,
            )
            best_z, best_score = max(fine_results, key=lambda item: item[1])

            self._set_autofocus_result(status=f"Moving to best Z={best_z:.4f} mm", progress=0.97)
            self._move_z_to_best_focus(z_motor, best_z, settings["settle_time_s"], coarse_velocity, fine_velocity)
            return best_z, best_score
        finally:
            self._restore_autofocus_velocity(z_motor, original_velocity)

    def _build_autofocus_positions(self, center_z: float, half_range_mm: float, step_mm: float) -> list[float]:
        half_range_mm = max(0.0, float(half_range_mm))
        step_mm = max(1e-6, float(step_mm))
        if half_range_mm <= 0.0:
            return [float(center_z)]

        count_each_side = max(1, int(np.ceil(half_range_mm / step_mm)))
        centered_positions = [round(float(center_z), 6)]
        for step_index in range(1, count_each_side + 1):
            offset = round(min(half_range_mm, step_index * step_mm), 6)
            lower = round(float(center_z - offset), 6)
            upper = round(float(center_z + offset), 6)
            centered_positions.insert(0, lower)
            centered_positions.append(upper)
        return centered_positions

    def _select_coarse_focus_target(
        self,
        coarse_results: list[tuple[float, float]],
        *,
        focus_to_top_surface: bool,
    ) -> tuple[float, float]:
        if not coarse_results:
            raise RuntimeError("Autofocus rough scan returned no samples.")

        global_best = max(coarse_results, key=lambda item: item[1])
        if not focus_to_top_surface or len(coarse_results) < 3:
            return float(global_best[0]), float(global_best[1])

        detected_surfaces = self._identify_autofocus_surfaces(coarse_results)
        if detected_surfaces:
            return max(detected_surfaces, key=lambda item: item[0])

        peaks: list[tuple[float, float]] = []
        for index, (z_value, score) in enumerate(coarse_results):
            left_score = coarse_results[index - 1][1] if index > 0 else None
            right_score = coarse_results[index + 1][1] if index < (len(coarse_results) - 1) else None

            is_peak = False
            if left_score is None and right_score is not None:
                is_peak = score > right_score
            elif right_score is None and left_score is not None:
                is_peak = score >= left_score
            elif left_score is not None and right_score is not None:
                is_peak = score >= left_score and score > right_score

            if is_peak:
                peaks.append((float(z_value), float(score)))

        if not peaks:
            return float(global_best[0]), float(global_best[1])
        return max(peaks, key=lambda item: item[0])

    def _identify_autofocus_surfaces(self, coarse_results: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(coarse_results) < 3:
            return []

        sorted_results = sorted((float(z_value), float(score)) for z_value, score in coarse_results)
        z_values = [z_value for z_value, _ in sorted_results]
        score_values = [score for _, score in sorted_results]
        score_span = max(score_values) - min(score_values)
        min_prominence = max(1e-9, score_span * _AUTOFOCUS_SURFACE_PROMINENCE_RATIO)
        if score_span <= 1e-9:
            return []

        if len(z_values) > 1:
            z_steps = [abs(z_values[index] - z_values[index - 1]) for index in range(1, len(z_values))]
            positive_steps = [step for step in z_steps if step > 1e-9]
            nominal_step = float(np.median(np.asarray(positive_steps, dtype=np.float64))) if positive_steps else 0.0
        else:
            nominal_step = 0.0

        smoothing_radius = 1
        smoothed_scores: list[float] = []
        for index in range(len(score_values)):
            start = max(0, index - smoothing_radius)
            end = min(len(score_values), index + smoothing_radius + 1)
            window = score_values[start:end]
            smoothed_scores.append(float(np.mean(np.asarray(window, dtype=np.float64))))

        window_radius = max(1, min(3, len(score_values) // 4))
        candidate_indices: list[int] = []
        for index in range(1, len(smoothed_scores) - 1):
            center_score = smoothed_scores[index]
            left_window = smoothed_scores[max(0, index - window_radius):index]
            right_window = smoothed_scores[index + 1:min(len(smoothed_scores), index + window_radius + 1)]
            if not left_window or not right_window:
                continue

            if center_score < max(left_window) or center_score < max(right_window):
                continue

            local_baseline = max(min(left_window), min(right_window))
            prominence = center_score - local_baseline
            if prominence >= min_prominence:
                candidate_indices.append(index)

        if not candidate_indices:
            return []

        min_surface_separation_mm = max(1e-6, nominal_step * 2.0)
        merged_surfaces: list[tuple[float, float]] = []
        for index in candidate_indices:
            candidate = (z_values[index], score_values[index])
            if not merged_surfaces:
                merged_surfaces.append(candidate)
                continue

            previous_z, previous_score = merged_surfaces[-1]
            if abs(candidate[0] - previous_z) < min_surface_separation_mm:
                if candidate[1] > previous_score:
                    merged_surfaces[-1] = candidate
            else:
                merged_surfaces.append(candidate)

        return merged_surfaces

    def _set_autofocus_surface_markers(self, surfaces: list[tuple[float, float]]):
        with self._snap_lock:
            self._autofocus_surface_z_values = [float(z_value) for z_value, _ in surfaces[:_AUTOFOCUS_MAX_SURFACE_MARKERS]]

    def _scan_focus_positions(
        self,
        andor,
        z_motor,
        positions: list[float],
        settings: dict,
        *,
        phase: str,
        progress_start: float,
        progress_span: float,
    ) -> list[tuple[float, float]]:
        results = []
        best_score = None
        best_position = None
        total_positions = max(1, len(positions))
        last_frame_idx = None
        with andor.frame_lock:
            last_frame_idx = int(getattr(andor, "frameIdx", 0))
        for index, position in enumerate(positions):
            self._check_autofocus_stop_requested()
            self._set_autofocus_result(status=f"Scanning Z={position:.4f} mm")
            scan_position = float(position)
            frame_samples, last_frame_idx = self._move_z_and_collect_focus_samples(
                andor,
                z_motor,
                scan_position,
                settings,
                last_frame_idx=last_frame_idx,
            )
            for sample_z, score in frame_samples:
                results.append((float(sample_z), float(score)))
                self._append_autofocus_plot_point(phase, sample_z, score)
                if best_score is None or score >= best_score:
                    best_score = float(score)
                    best_position = float(sample_z)
            progress = progress_start + (progress_span * ((index + 1) / total_positions))
            self._set_autofocus_result(best_score=best_score, best_z=best_position, progress=progress)
        return results

    def _move_z_and_collect_focus_samples(
        self,
        andor,
        z_motor,
        position_mm: float,
        settings: dict,
        *,
        last_frame_idx: int | None = None,
    ) -> tuple[list[tuple[float, float]], int]:
        target = float(position_mm)
        snap = z_motor.snapshot()
        current = snap.get("position")
        current_position = float(current) if current is not None else target
        stage_samples = [(time.time(), current_position)]
        collected_samples: list[tuple[float, float]] = []

        if current is None or abs(float(current) - target) > _AUTOFOCUS_POSITION_TOLERANCE_MM:
            distance_mm = abs(float(current) - target) if current is not None else 0.0
            velocity_params = snap.get("velocity_params") or {}
            move_timeout_s = self._estimate_autofocus_move_timeout(
                distance_mm,
                float(velocity_params.get("max_velocity") or 0.0),
                settings["settle_time_s"],
            )
            z_motor.move_to(target)
            deadline = time.monotonic() + move_timeout_s
            moving_states = {"Moving", "Jogging", "Homing"}
            last_progress_time = time.monotonic()
            last_reported_position = current_position
            last_state = str(snap.get("state") or "")

            while time.monotonic() < deadline:
                self._check_autofocus_stop_requested(stop_motor=z_motor)
                snap = z_motor.snapshot()
                if snap.get("last_error"):
                    raise RuntimeError(f"Z move failed: {snap['last_error']}")

                pos = snap.get("position")
                state = str(snap.get("state") or "")
                last_state = state
                if pos is not None:
                    current_position = float(pos)
                    previous_position = last_reported_position
                    last_reported_position = current_position
                    position_changed = previous_position is None or abs(current_position - previous_position) > 5e-4
                    if not stage_samples or abs(current_position - stage_samples[-1][1]) > 1e-6:
                        stage_samples.append((time.time(), current_position))
                    if position_changed:
                        last_progress_time = time.monotonic()

                new_samples, last_frame_idx = self._consume_autofocus_frames(
                    andor,
                    settings,
                    last_frame_idx=last_frame_idx,
                    stage_samples=stage_samples,
                    fallback_position=current_position,
                )
                collected_samples.extend(new_samples)

                if pos is not None and abs(float(pos) - target) <= _AUTOFOCUS_POSITION_TOLERANCE_MM:
                    break
                if (
                    pos is not None
                    and abs(float(pos) - target) <= _AUTOFOCUS_NEAR_TARGET_TOLERANCE_MM
                    and (time.monotonic() - last_progress_time) >= _AUTOFOCUS_STABLE_POSITION_TIME_S
                ):
                    break
                if (
                    pos is not None
                    and state not in moving_states
                    and (time.monotonic() - last_progress_time) >= _AUTOFOCUS_STABLE_POSITION_TIME_S
                ):
                    break
                self._wait_with_autofocus_stop(0.01, stop_motor=z_motor)
            else:
                raise TimeoutError(
                    f"Timed out moving Z to {target:.4f} mm after {move_timeout_s:.1f} s; last position {last_reported_position:.4f} mm, state {last_state}"
                )

        settle_samples, last_frame_idx = self._collect_focus_samples_for_settle_window(
            andor,
            z_motor,
            settings,
            last_frame_idx=last_frame_idx,
            stage_samples=stage_samples,
            fallback_position=current_position,
        )
        collected_samples.extend(settle_samples)

        if not collected_samples:
            fallback_score, last_frame_idx = self._sample_focus_score(
                andor,
                settings,
                last_frame_idx=last_frame_idx,
            )
            collected_samples.append((current_position, float(fallback_score)))

        return collected_samples, int(last_frame_idx if last_frame_idx is not None else 0)

    def _collect_focus_samples_for_settle_window(
        self,
        andor,
        z_motor,
        settings: dict,
        *,
        last_frame_idx: int | None,
        stage_samples: list[tuple[float, float]],
        fallback_position: float,
    ) -> tuple[list[tuple[float, float]], int]:
        settle_duration_s = max(_AUTOFOCUS_SETTLE_MIN_S, float(settings["settle_time_s"]))
        frames_needed = max(1, int(settings["frames_per_position"]))
        settle_end_time = time.monotonic() + settle_duration_s
        deadline = time.monotonic() + max(0.5, settle_duration_s * (frames_needed + 2) * 4.0)
        settled_samples: list[tuple[float, float]] = []

        while time.monotonic() < deadline:
            self._check_autofocus_stop_requested(stop_motor=z_motor)
            snap = z_motor.snapshot()
            if snap.get("last_error"):
                raise RuntimeError(f"Z move failed: {snap['last_error']}")

            pos = snap.get("position")
            if pos is not None:
                current_position = float(pos)
                if not stage_samples or abs(current_position - stage_samples[-1][1]) > 1e-6:
                    stage_samples.append((time.time(), current_position))
                fallback_position = current_position

            new_samples, last_frame_idx = self._consume_autofocus_frames(
                andor,
                settings,
                last_frame_idx=last_frame_idx,
                stage_samples=stage_samples,
                fallback_position=fallback_position,
            )
            settled_samples.extend(new_samples)

            if time.monotonic() >= settle_end_time and len(settled_samples) >= frames_needed:
                break

            self._wait_with_autofocus_stop(0.01, stop_motor=z_motor)

        return settled_samples, int(last_frame_idx if last_frame_idx is not None else 0)

    def _consume_autofocus_frames(
        self,
        andor,
        settings: dict,
        *,
        last_frame_idx: int | None,
        stage_samples: list[tuple[float, float]],
        fallback_position: float,
    ) -> tuple[list[tuple[float, float]], int]:
        with andor.frame_lock:
            capture_running = bool(getattr(andor, "is_capturing", False))
            current_frame_idx = int(getattr(andor, "frameIdx", 0))
            frame_count = len(getattr(andor, "acquisitions", []))
            if not capture_running:
                raise RuntimeError("Camera preview stopped during autofocus.")
            if current_frame_idx <= 0 or frame_count <= 0:
                return [], int(last_frame_idx if last_frame_idx is not None else 0)

            first_available_frame_idx = max(0, current_frame_idx - frame_count) + 1
            next_frame_idx = max(int(last_frame_idx or 0) + 1, first_available_frame_idx)
            if next_frame_idx > current_frame_idx:
                return [], current_frame_idx

            start_offset = max(0, next_frame_idx - first_available_frame_idx)
            sample_count = max(0, current_frame_idx - next_frame_idx + 1)
            if sample_count <= 0:
                return [], current_frame_idx

            raw_frames = andor.acquisitions.range_array(start_offset, sample_count, copy=True)
            frame_timestamps = andor.timestamps.range_array(start_offset, sample_count, copy=True)

        samples: list[tuple[float, float]] = []
        for frame, frame_timestamp in zip(raw_frames, frame_timestamps):
            matched_position = self._match_autofocus_frame_to_stage_position(
                float(frame_timestamp),
                stage_samples,
                fallback_position,
            )
            score = self._compute_tenengrad_score(frame, settings["roi_fraction"])
            samples.append((matched_position, float(score)))

        return samples, current_frame_idx

    def _match_autofocus_frame_to_stage_position(
        self,
        frame_timestamp: float,
        stage_samples: list[tuple[float, float]],
        fallback_position: float,
    ) -> float:
        if not stage_samples:
            return float(fallback_position)
        if len(stage_samples) == 1:
            return float(stage_samples[0][1])

        first_time, first_position = stage_samples[0]
        if frame_timestamp <= first_time:
            return float(first_position)

        last_time, last_position = stage_samples[-1]
        if frame_timestamp >= last_time:
            return float(last_position)

        for index in range(1, len(stage_samples)):
            previous_time, previous_position = stage_samples[index - 1]
            next_time, next_position = stage_samples[index]
            if frame_timestamp > next_time:
                continue
            time_span = max(1e-9, next_time - previous_time)
            fraction = min(1.0, max(0.0, (frame_timestamp - previous_time) / time_span))
            return float(previous_position + ((next_position - previous_position) * fraction))

        return float(fallback_position)

    def _move_z_and_wait(self, z_motor, position_mm: float, settle_time_s: float):
        target = float(position_mm)
        snap = z_motor.snapshot()
        current = snap.get("position")
        if current is not None and abs(float(current) - target) <= _AUTOFOCUS_POSITION_TOLERANCE_MM:
            self._wait_with_autofocus_stop(max(_AUTOFOCUS_SETTLE_MIN_S, float(settle_time_s)))
            return float(current)

        distance_mm = abs(float(current) - target) if current is not None else 0.0
        velocity_params = snap.get("velocity_params") or {}
        move_timeout_s = self._estimate_autofocus_move_timeout(
            distance_mm,
            float(velocity_params.get("max_velocity") or 0.0),
            settle_time_s,
        )
        z_motor.move_to(target)
        deadline = time.monotonic() + move_timeout_s
        moving_states = {"Moving", "Jogging", "Homing"}
        last_position = float(current) if current is not None else None
        last_progress_time = time.monotonic()
        last_reported_position = last_position
        last_state = str(snap.get("state") or "")

        while time.monotonic() < deadline:
            self._check_autofocus_stop_requested(stop_motor=z_motor)
            snap = z_motor.snapshot()
            if snap.get("last_error"):
                raise RuntimeError(f"Z move failed: {snap['last_error']}")

            pos = snap.get("position")
            state = str(snap.get("state") or "")
            last_state = state
            if pos is not None:
                current_position = float(pos)
                last_reported_position = current_position
                if last_position is None or abs(current_position - last_position) > 5e-4:
                    last_position = current_position
                    last_progress_time = time.monotonic()
            if pos is not None and abs(float(pos) - target) <= _AUTOFOCUS_POSITION_TOLERANCE_MM:
                self._wait_with_autofocus_stop(max(_AUTOFOCUS_SETTLE_MIN_S, float(settle_time_s)), stop_motor=z_motor)
                return float(pos)
            if (
                pos is not None
                and abs(float(pos) - target) <= _AUTOFOCUS_NEAR_TARGET_TOLERANCE_MM
                and (time.monotonic() - last_progress_time) >= _AUTOFOCUS_STABLE_POSITION_TIME_S
            ):
                self._wait_with_autofocus_stop(max(_AUTOFOCUS_SETTLE_MIN_S, float(settle_time_s)), stop_motor=z_motor)
                return float(pos)
            if (
                pos is not None
                and state not in moving_states
                and (time.monotonic() - last_progress_time) >= _AUTOFOCUS_STABLE_POSITION_TIME_S
            ):
                self._wait_with_autofocus_stop(max(_AUTOFOCUS_SETTLE_MIN_S, float(settle_time_s)), stop_motor=z_motor)
                return float(pos)
            self._wait_with_autofocus_stop(0.02, stop_motor=z_motor)

        if last_reported_position is None:
            raise TimeoutError(f"Timed out moving Z to {target:.4f} mm after {move_timeout_s:.1f} s")
        raise TimeoutError(
            f"Timed out moving Z to {target:.4f} mm after {move_timeout_s:.1f} s; last position {last_reported_position:.4f} mm, state {last_state}"
        )

    def _move_z_to_best_focus(
        self,
        z_motor,
        best_z: float,
        settle_time_s: float,
        coarse_velocity: dict,
        fine_velocity: dict,
    ):
        target = float(best_z)
        snap = z_motor.snapshot()
        current = snap.get("position")
        current_position = None if current is None else float(current)
        final_slow_distance_mm = 0.1

        if current_position is not None:
            remaining_distance = target - current_position
            if abs(remaining_distance) > final_slow_distance_mm:
                fast_target = target - (final_slow_distance_mm if remaining_distance > 0.0 else -final_slow_distance_mm)
                self._set_autofocus_result(status=f"Approaching best Z={best_z:.4f} mm", progress=0.97)
                self._set_autofocus_velocity(z_motor, coarse_velocity)
                self._move_z_and_wait(z_motor, fast_target, 0.0)

        self._set_autofocus_result(status=f"Finalizing best Z={best_z:.4f} mm", progress=0.985)
        self._set_autofocus_velocity(z_motor, fine_velocity)
        self._move_z_and_wait(z_motor, target, settle_time_s)

    def _sample_focus_score(self, andor, settings: dict, *, last_frame_idx: int | None = None) -> tuple[float, int]:
        frames_needed = max(1, int(settings["frames_per_position"]))
        deadline = time.monotonic() + max(0.5, settings["settle_time_s"] * (frames_needed + 2) * 4.0)
        scores = []
        fallback_frame = None
        latest_frame_idx = -1 if last_frame_idx is None else int(last_frame_idx)

        while time.monotonic() < deadline and len(scores) < frames_needed:
            self._check_autofocus_stop_requested()
            with andor.frame_lock:
                frame_idx = int(getattr(andor, "frameIdx", 0))
                frame = getattr(andor, "latest_frame", None)
                capture_running = bool(getattr(andor, "is_capturing", False))
                if frame is not None:
                    fallback_frame = np.array(frame, copy=True)

            if not capture_running:
                raise RuntimeError("Camera preview stopped during autofocus.")
            if frame is None:
                self._wait_with_autofocus_stop(0.02)
                continue
            if frame_idx <= latest_frame_idx:
                self._wait_with_autofocus_stop(0.01)
                continue

            latest_frame_idx = frame_idx
            scores.append(self._compute_tenengrad_score(frame, settings["roi_fraction"]))
            self._wait_with_autofocus_stop(0.005)

        if not scores:
            if fallback_frame is None:
                raise RuntimeError("No camera frames available for autofocus.")
            scores.append(self._compute_tenengrad_score(fallback_frame, settings["roi_fraction"]))
            if latest_frame_idx < 0:
                latest_frame_idx = int(last_frame_idx or 0)

        return float(np.mean(np.asarray(scores, dtype=np.float64))), latest_frame_idx

    def _compute_tenengrad_score(self, frame, roi_fraction: float) -> float:
        image = np.asarray(frame, dtype=np.float32)
        image = np.squeeze(image)
        if image.ndim != 2 or image.size == 0:
            return 0.0

        roi = self._extract_center_roi(image, roi_fraction)
        if roi.shape[0] < 3 or roi.shape[1] < 3:
            return 0.0

        # Suppress broad background structure so the score tracks sharp detail
        # instead of diffuse fluorescence or large gaussian blobs.
        background = self._estimate_focus_background(roi)
        roi_detail = roi - background
        roi_detail -= float(np.median(roi_detail))

        detail_scale = float(np.std(roi_detail, dtype=np.float64))
        if detail_scale <= 1e-6:
            return 0.0

        normalized_detail = roi_detail / detail_scale

        gx = (
            normalized_detail[:-2, 2:] + (2.0 * normalized_detail[1:-1, 2:]) + normalized_detail[2:, 2:]
            - normalized_detail[:-2, :-2] - (2.0 * normalized_detail[1:-1, :-2]) - normalized_detail[2:, :-2]
        )
        gy = (
            normalized_detail[2:, :-2] + (2.0 * normalized_detail[2:, 1:-1]) + normalized_detail[2:, 2:]
            - normalized_detail[:-2, :-2] - (2.0 * normalized_detail[:-2, 1:-1]) - normalized_detail[:-2, 2:]
        )
        magnitude_sq = (gx * gx) + (gy * gy)
        if magnitude_sq.size <= 0:
            return 0.0

        threshold = float(np.percentile(magnitude_sq, 85.0))
        significant_gradients = magnitude_sq[magnitude_sq >= threshold]
        if significant_gradients.size <= 0:
            significant_gradients = magnitude_sq.reshape(-1)
        normalized_sharpness = float(np.mean(significant_gradients, dtype=np.float64))
        return float(1.0 / max(normalized_sharpness, 1e-12))

    def _estimate_focus_background(self, roi: np.ndarray) -> np.ndarray:
        min_dim = max(1, min(int(roi.shape[0]), int(roi.shape[1])))
        blur_radius = max(3, min(int(round(min_dim * 0.08)), 31))
        blurred = self._box_blur_axis(roi, blur_radius, axis=1)
        blurred = self._box_blur_axis(blurred, blur_radius, axis=0)
        return np.asarray(blurred, dtype=np.float32)

    def _box_blur_axis(self, image: np.ndarray, radius: int, *, axis: int) -> np.ndarray:
        radius = max(0, int(radius))
        if radius <= 0:
            return np.array(image, dtype=np.float32, copy=True)

        kernel_size = (2 * radius) + 1
        pad_width = [(0, 0)] * image.ndim
        pad_width[axis] = (radius, radius)
        padded = np.pad(np.asarray(image, dtype=np.float32), pad_width, mode="reflect")

        cumulative = np.cumsum(padded, axis=axis, dtype=np.float64)
        cumulative_pad = [(0, 0)] * image.ndim
        cumulative_pad[axis] = (1, 0)
        cumulative = np.pad(cumulative, cumulative_pad, mode="constant")

        start_slices = [slice(None)] * image.ndim
        end_slices = [slice(None)] * image.ndim
        start_slices[axis] = slice(0, image.shape[axis])
        end_slices[axis] = slice(kernel_size, kernel_size + image.shape[axis])

        summed = cumulative[tuple(end_slices)] - cumulative[tuple(start_slices)]
        return np.asarray(summed / float(kernel_size), dtype=np.float32)

    def _extract_center_roi(self, image: np.ndarray, roi_fraction: float) -> np.ndarray:
        fraction = min(1.0, max(0.1, float(roi_fraction)))
        height, width = image.shape[:2]
        roi_height = max(3, int(round(height * fraction)))
        roi_width = max(3, int(round(width * fraction)))
        top = max(0, (height - roi_height) // 2)
        left = max(0, (width - roi_width) // 2)
        return image[top:top + roi_height, left:left + roi_width]

    def _get_autofocus_motion_profile(self, phase: str, velocity_params: dict, *, andor=None, settings: dict | None = None) -> dict:
        profile = dict(velocity_params or {})
        requested_speed = float(
            self._speeds_mm_s.get(
                "z_fast" if phase == "coarse" else "z_slow",
                self._DEFAULT_SPEEDS_MM_S["z_fast" if phase == "coarse" else "z_slow"],
            )
        )
        fps_limited_speed = self._get_autofocus_sampling_speed(phase, andor, settings)
        enforce_speed_ceiling = fps_limited_speed is not None
        if fps_limited_speed is not None:
            requested_speed = min(requested_speed, fps_limited_speed)
        current_speed = float(profile.get("max_velocity") or 0.0)
        if enforce_speed_ceiling and current_speed > 0.0:
            profile["max_velocity"] = min(current_speed, requested_speed)
        elif current_speed > 0.0:
            profile["max_velocity"] = max(current_speed, requested_speed)
        else:
            profile["max_velocity"] = requested_speed

        current_accel = float(profile.get("acceleration") or 0.0)
        if current_accel > 0.0:
            profile["acceleration"] = max(current_accel, _AUTOFOCUS_MIN_ACCEL_MM_S2)
        else:
            profile["acceleration"] = _AUTOFOCUS_MIN_ACCEL_MM_S2
        return profile

    def _get_autofocus_sampling_speed(self, phase: str, andor, settings: dict | None) -> float | None:
        if andor is None or not settings:
            return None

        try:
            frame_rate_hz = max(float(andor.get_frame_rate()), 1e-6)
        except Exception:
            return None

        frames_needed = max(1, int(settings.get("frames_per_position", 1)))
        step_setting_key = "coarse_step_mm" if phase == "coarse" else "fine_step_mm"
        step_default = (
            self._DEFAULT_AUTOFOCUS_SETTINGS["coarse_step_mm"]
            if phase == "coarse"
            else self._DEFAULT_AUTOFOCUS_SETTINGS["fine_step_mm"]
        )
        step_mm = max(1e-6, float(settings.get(step_setting_key, step_default)))
        return max(1e-3, (step_mm * frame_rate_hz) / frames_needed)

    def _estimate_autofocus_move_timeout(self, distance_mm: float, velocity_mm_s: float, settle_time_s: float) -> float:
        clamped_distance = max(0.0, float(distance_mm))
        effective_velocity = max(1e-3, float(velocity_mm_s or 0.0))
        settle_allowance = max(_AUTOFOCUS_SETTLE_MIN_S, float(settle_time_s))
        estimated_timeout = (
            (clamped_distance / effective_velocity)
            + _AUTOFOCUS_MOVE_TIMEOUT_BUFFER_S
            + (2.0 * settle_allowance)
        )
        return min(_AUTOFOCUS_MOVE_TIMEOUT_MAX_S, max(_AUTOFOCUS_MOVE_TIMEOUT_MIN_S, estimated_timeout))

    def _set_autofocus_velocity(self, z_motor, velocity_params: dict):
        if not velocity_params:
            return
        kwargs = {}
        if velocity_params.get("acceleration") is not None:
            kwargs["acceleration"] = float(velocity_params["acceleration"])
        if velocity_params.get("max_velocity") is not None:
            kwargs["max_velocity"] = float(velocity_params["max_velocity"])
        if kwargs:
            z_motor.set_velocity_params(**kwargs)

    def _restore_autofocus_velocity(self, z_motor, velocity_params: dict):
        self._set_autofocus_velocity(z_motor, velocity_params)

    def _wait_with_autofocus_stop(self, duration_s: float, stop_motor=None):
        end_time = time.monotonic() + max(0.0, float(duration_s))
        while time.monotonic() < end_time:
            self._check_autofocus_stop_requested(stop_motor=stop_motor)
            remaining = end_time - time.monotonic()
            if remaining <= 0.0:
                break
            time.sleep(min(0.02, remaining))

    def _check_autofocus_stop_requested(self, stop_motor=None):
        if not self._autofocus_stop_event.is_set():
            return
        if stop_motor is not None:
            stop_motor.stop(immediate=True)
        raise RuntimeError("Autofocus stopped.")

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

    def _queue_startup_auto_connect(self):
        if self._startup_auto_connect_queued:
            return
        self._startup_auto_connect_queued = True

        saved_serials = []
        for axis in ("x", "y", "z"):
            serial = str(dpg.get_value(getattr(self, f"_{axis}_serial_combo"))).strip()
            if serial and serial != "(no devices found)":
                saved_serials.append(serial)

        if saved_serials:
            for axis in ("x", "y", "z"):
                serial = str(dpg.get_value(getattr(self, f"_{axis}_serial_combo"))).strip()
                if serial and serial != "(no devices found)":
                    self._start_connect_thread(axis, serial)
            return

        def _worker():
            serials = KST101.list_devices()
            self._auto_connect_queue = serials if serials else []

        threading.Thread(target=_worker, daemon=True, name="StageStartupAutoConnect").start()

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
            try:
                for attempt in range(_CONNECT_RETRY_ATTEMPTS):
                    if self._motors[axis].connected:
                        break
                    self._motors[axis].connect(serial)
                    if self._motors[axis].connected:
                        break
                    if attempt < (_CONNECT_RETRY_ATTEMPTS - 1):
                        time.sleep(_CONNECT_RETRY_DELAY_S)
            finally:
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
        with self._snap_lock:
            snap = dict(self._snaps.get(axis) or {})

        if not self._axis_motion_ready(snap):
            self._jog_held[axis].clear()
            return

        held = self._jog_held[axis]
        if "+" in held and "-" not in held:
            self._apply_axis_motion_speed(axis)
            motor.jog("+")
        elif "-" in held and "+" not in held:
            self._apply_axis_motion_speed(axis)
            motor.jog("-")
        else:
            motor.stop(immediate=False)

    def _is_shift_active(self) -> bool:
        return bool(self._shift_held_keys)

    def _get_active_direction(self, axis: str) -> str | None:
        held = self._jog_held[axis]
        if "+" in held and "-" not in held:
            return "+"
        if "-" in held and "+" not in held:
            return "-"
        return None

    def _get_speed_value(self, speed_key: str) -> float:
        input_id = self._speed_input_ids[speed_key]
        value = max(0.001, float(dpg.get_value(input_id)))
        self._speeds_mm_s[speed_key] = value
        return value

    def _get_axis_motion_speed(self, axis: str) -> float:
        if axis in ("x", "y"):
            speed_key = "xy_fast" if self._is_shift_active() else "xy_slow"
        else:
            speed_key = "z_fast" if self._is_shift_active() else "z_slow"
        return self._get_speed_value(speed_key)

    def _apply_axis_motion_speed(self, axis: str):
        speed = self._get_axis_motion_speed(axis)
        self._motors[axis].set_jog_params(max_velocity=speed)
        self._motors[axis].set_velocity_params(max_velocity=speed)

    def _refresh_active_jog_speeds(self):
        for axis in ("x", "y", "z"):
            direction = self._get_active_direction(axis)
            if direction is None:
                continue
            with self._snap_lock:
                snap = dict(self._snaps.get(axis) or {})
            if not self._axis_motion_ready(snap):
                continue
            self._apply_axis_motion_speed(axis)
            self._motors[axis].stop(immediate=False)
            self._motors[axis].jog(direction)

    def _on_speed_changed(self, speed_key: str, value):
        clamped = max(0.001, float(value))
        self._speeds_mm_s[speed_key] = clamped
        dpg.set_value(self._speed_input_ids[speed_key], clamped)
        self._refresh_active_jog_speeds()

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

    def _reset_axis_settings(self, axis: str):
        w = self._get_settings_widgets(axis)
        defaults = self._DEFAULT_AXIS_SETTINGS
        dpg.set_value(w["max_velocity"], float(defaults["max_velocity"]))
        dpg.set_value(w["acceleration"], float(defaults["acceleration"]))
        dpg.set_value(w["jog_step"], float(defaults["jog_step"]))
        dpg.set_value(w["jog_max_vel"], float(defaults["jog_max_vel"]))
        dpg.set_value(w["jog_accel"], float(defaults["jog_accel"]))
        dpg.set_value(w["backlash"], float(defaults["backlash"]))

        if self._motors[axis].connected:
            self._apply_axis_settings(axis)

    def _populate_settings_from_snapshot(self, axis: str, snap: dict):
        if self._settings_loading[axis]:
            return
        self._settings_loading[axis] = True
        try:
            w = self._get_settings_widgets(axis)
            vp = snap.get("velocity_params", {})
            jp = snap.get("jog_params", {})
            gp = snap.get("gen_move_params", {})

            if "max_velocity" in vp and vp["max_velocity"] is not None:
                dpg.set_value(w["max_velocity"], float(vp["max_velocity"]))
            if "acceleration" in vp and vp["acceleration"] is not None:
                dpg.set_value(w["acceleration"], float(vp["acceleration"]))
            if "step_size" in jp and jp["step_size"] is not None:
                dpg.set_value(w["jog_step"], float(jp["step_size"]))
            if "max_velocity" in jp and jp["max_velocity"] is not None:
                dpg.set_value(w["jog_max_vel"], float(jp["max_velocity"]))
            if "acceleration" in jp and jp["acceleration"] is not None:
                dpg.set_value(w["jog_accel"], float(jp["acceleration"]))
            if "backlash" in gp and gp["backlash"] is not None:
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
        self._update_keypad_state(frame)
        self._update_current_focus_level()
        self._update_autofocus_status()
        self._update_autofocus_plot()
        self._update_position_plots(frame)
        self._update_position_readouts(frame)
        self._update_settings_status(frame)
        self._update_developer_stats(frame)
        self._update_keypad_highlights()
        self._resize_plots_to_window()

    def _axis_motion_ready(self, snap: dict) -> bool:
        if not snap.get("connected", False):
            return False
        if not snap.get("homed", False):
            return False
        return str(snap.get("state") or "") in {"Ready", "Moving", "Jogging"}

    def _format_axis_state(self, snap: dict) -> str:
        state = str(snap.get("state") or "Disconnected")
        details: list[str] = []
        if snap.get("connected") and not snap.get("homed") and state == "NotHomed":
            details.append("home first")
        if snap.get("cw_limit"):
            details.append("CW limit")
        if snap.get("ccw_limit"):
            details.append("CCW limit")
        if not details:
            return state
        return f"{state} ({', '.join(details)})"

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
            homed = snap.get("homed", False)
            has_error = bool(snap.get("last_error"))

            ind   = getattr(self, f"_{axis}_indicator")
            btn   = getattr(self, f"_{axis}_conn_btn")
            tip   = getattr(self, f"_{axis}_conn_tooltip")
            combo = getattr(self, f"_{axis}_serial_combo")

            dpg.configure_item(combo, enabled=not connected and not connecting)
            dpg.configure_item(btn,   enabled=not connecting)

            if connected:
                dpg.set_item_label(btn, "")
                if has_error:
                    dpg.set_value(tip, f"Disconnect {axis.upper()} motor (driver error present)")
                    dpg.configure_item(ind, default_value=(200, 50, 50, 255))
                elif homed:
                    dpg.set_value(tip, f"Disconnect {axis.upper()} motor")
                    dpg.configure_item(ind, default_value=(0, 180, 0, 255))
                else:
                    dpg.set_value(tip, f"Disconnect {axis.upper()} motor (home before motion)")
                    dpg.configure_item(ind, default_value=(220, 170, 0, 255))
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
                params_ready = bool(
                    snap.get("velocity_params")
                    or snap.get("jog_params")
                    or snap.get("gen_move_params")
                )
                if params_ready and self._settings_populated[axis] != id(self._motors[axis]):
                    self._populate_settings_from_snapshot(axis, snap)
                    self._settings_populated[axis] = id(self._motors[axis])
            else:
                self._settings_populated[axis] = -1

    def _update_keypad_state(self, frame: dict):
        for axis, direction in (("z", "-"), ("y", "+"), ("z", "+"), ("x", "-"), ("y", "-"), ("x", "+")):
            btn_id = getattr(self, _KEYPAD_BUTTON_MAP[(axis, direction)])
            dpg.configure_item(btn_id, enabled=self._axis_motion_ready(frame[axis]))

        any_connected = any(frame[axis].get("connected", False) for axis in ("x", "y", "z"))
        autofocus_ready = self._axis_motion_ready(frame["z"])
        dpg.configure_item(
            self._autofocus_button_id,
            enabled=autofocus_ready and not self._autofocus_running,
            show=not self._autofocus_running,
        )
        dpg.configure_item(
            self._autofocus_progress_id,
            show=self._autofocus_running,
        )
        dpg.configure_item(
            self._autofocus_stop_btn,
            enabled=self._autofocus_running,
        )
        dpg.configure_item(self._home_all_btn, enabled=any_connected)
        dpg.configure_item(self._stop_all_btn, enabled=any_connected)

    def _update_autofocus_status(self):
        widgets = getattr(self, "_autofocus_widgets", None)
        if not widgets:
            return

        dpg.set_value(widgets["status"], str(self._autofocus_status))
        dpg.set_value(
            widgets["best_z"],
            "--" if self._autofocus_best_z is None else f"{float(self._autofocus_best_z):.4f} mm",
        )
        dpg.set_value(
            widgets["best_score"],
            "--" if self._autofocus_best_score is None else f"{float(self._autofocus_best_score):.3f}",
        )
        dpg.set_value(
            widgets["current_focus_level"],
            "--" if self._autofocus_current_focus_level is None else f"{float(self._autofocus_current_focus_level):.3f}",
        )
        dpg.set_value(widgets["error"], str(self._autofocus_error or ""))
        dpg.set_value(self._autofocus_progress_id, float(self._autofocus_progress))
        dpg.configure_item(
            self._autofocus_progress_id,
            overlay=str(self._autofocus_status),
        )

        editable = not self._autofocus_running
        for key in (
            "search_range_mm",
            "coarse_step_mm",
            "fine_step_mm",
            "settle_time_s",
            "frames_per_position",
            "roi_fraction",
            "focus_to_top_surface",
            "always_calculate_focus_level",
        ):
            dpg.configure_item(widgets[key], enabled=editable)

    def _update_current_focus_level(self):
        widgets = getattr(self, "_autofocus_widgets", None)
        if not widgets:
            return

        always_calculate = bool(dpg.get_value(widgets["always_calculate_focus_level"]))
        should_calculate = self._autofocus_running or always_calculate
        if not should_calculate:
            self._autofocus_current_focus_level = None
            self._autofocus_current_focus_frame_idx = -1
            return

        andor = getattr(shared_state, "shared_andor", None)
        if andor is None:
            self._autofocus_current_focus_level = None
            self._autofocus_current_focus_frame_idx = -1
            return

        with andor.frame_lock:
            capture_running = bool(getattr(andor, "is_capturing", False))
            frame_idx = int(getattr(andor, "frameIdx", 0))
            frame = getattr(andor, "latest_frame", None)
            if frame is not None:
                frame = np.array(frame, copy=True)

        if not capture_running or frame is None or frame_idx <= 0:
            self._autofocus_current_focus_level = None
            self._autofocus_current_focus_frame_idx = -1
            return

        if frame_idx == self._autofocus_current_focus_frame_idx:
            return

        roi_fraction = min(1.0, max(0.1, float(dpg.get_value(widgets["roi_fraction"]))))
        self._autofocus_current_focus_level = self._compute_tenengrad_score(frame, roi_fraction)
        self._autofocus_current_focus_frame_idx = frame_idx

    def _update_autofocus_plot(self):
        if not hasattr(self, "_autofocus_plot_widgets"):
            return

        with self._snap_lock:
            plot_state = {
                phase: {
                    "z_values": list(state["z_values"]),
                    "focus_scores": list(state["focus_scores"]),
                    "center_z": float(state["center_z"]),
                    "half_range_mm": max(0.05, float(state["half_range_mm"])),
                }
                for phase, state in self._autofocus_plot_state.items()
            }
            autofocus_best_z = None if self._autofocus_best_z is None else float(self._autofocus_best_z)
            surface_z_values = list(self._autofocus_surface_z_values)

        for phase, widgets in self._autofocus_plot_widgets.items():
            phase_state = plot_state[phase]
            z_values = phase_state["z_values"]
            focus_scores = phase_state["focus_scores"]
            center_z = phase_state["center_z"]
            half_range_mm = phase_state["half_range_mm"]

            if z_values and focus_scores:
                dpg.set_value(widgets["score_series"], [z_values, focus_scores])
                dpg.set_value(widgets["score_points_series"], [z_values, focus_scores])

                x_min = min(z_values)
                x_max = max(z_values)
                x_pad = max((x_max - x_min) * 0.05, 0.01)
                plot_x_min = x_min - x_pad
                plot_x_max = x_max + x_pad

                y_min = min(focus_scores)
                y_max = max(focus_scores)
                y_scale = max(abs(y_min), abs(y_max), 1e-6)
                y_pad = max((y_max - y_min) * 0.1, y_scale * 0.05, 1e-6)
                plot_y_min = y_min - y_pad
                plot_y_max = y_max + y_pad
            else:
                dpg.set_value(widgets["score_series"], [[], []])
                dpg.set_value(widgets["score_points_series"], [[], []])
                plot_x_min = center_z - half_range_mm
                plot_x_max = center_z + half_range_mm
                plot_y_min = 0.0
                plot_y_max = 1.0

            if plot_x_max <= plot_x_min:
                plot_x_max = plot_x_min + 0.1
            dpg.set_axis_limits(widgets["x_axis"], plot_x_min, plot_x_max)
            dpg.set_axis_limits(widgets["y_axis"], plot_y_min, plot_y_max)
            dpg.set_value(
                widgets["center_series"],
                [[center_z, center_z], [plot_y_min, plot_y_max]],
            )
            fine_focus_series = widgets.get("fine_focus_series")
            if fine_focus_series is not None:
                if autofocus_best_z is None:
                    dpg.set_value(fine_focus_series, [[], []])
                else:
                    dpg.set_value(
                        fine_focus_series,
                        [[autofocus_best_z, autofocus_best_z], [plot_y_min, plot_y_max]],
                    )
            for index, surface_line in enumerate(widgets.get("surface_lines", [])):
                if index < len(surface_z_values):
                    surface_z = float(surface_z_values[index])
                    dpg.set_value(surface_line, [[surface_z, surface_z], [plot_y_min, plot_y_max]])
                else:
                    dpg.set_value(surface_line, [[], []])

    def _update_position_plots(self, frame: dict):
        x_mm = float(frame["x"].get("position") or 0.0)
        y_mm = float(frame["y"].get("position") or 0.0)
        z_mm = float(frame["z"].get("position") or 0.0)
        with self._snap_lock:
            surface_z_values = list(self._autofocus_surface_z_values)
            autofocus_best_z = None if self._autofocus_best_z is None else float(self._autofocus_best_z)
        dpg.set_value(self._xy_pos_series, [[x_mm], [y_mm]])
        dpg.set_value(self._z_bar_series,  [[0.0],  [z_mm]])
        for index, surface_line in enumerate(self._z_surface_lines):
            if index < len(surface_z_values):
                surface_z = float(surface_z_values[index])
                dpg.set_value(surface_line, [[-0.6, 0.6], [surface_z, surface_z]])
            else:
                dpg.set_value(surface_line, [[], []])
        if autofocus_best_z is None:
            dpg.set_value(self._z_best_focus_line, [[], []])
        else:
            dpg.set_value(self._z_best_focus_line, [[-0.6, 0.6], [autofocus_best_z, autofocus_best_z]])

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

            dpg.set_value(w["state"], self._format_axis_state(snap))
            pos = snap.get("position")
            dpg.set_value(
                w["position"],
                "--" if pos is None else f"{float(pos):.4f} mm",
            )
            dpg.set_value(
                w["error"],
                str(snap.get("last_error") or ("Home axis before motion" if enabled and not snap.get("homed", False) else "")),
            )

            for wid_key in ("max_velocity", "acceleration", "jog_step",
                            "jog_max_vel", "jog_accel", "backlash", "apply_btn", "reset_btn"):
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

    def _disconnect_motor_gracefully(self, motor, *, timeout_seconds: float = 2.0):
        if not motor.connected:
            return

        try:
            motor.stop(immediate=False)
        except Exception:
            pass

        deadline = time.perf_counter() + max(0.0, timeout_seconds)
        while time.perf_counter() < deadline:
            try:
                state = str((motor.snapshot() or {}).get("state") or "").lower()
            except Exception:
                break

            if state in {"idle", "stopped", "homed", ""}:
                break
            time.sleep(0.05)

    def cleanup(self):
        """Stop the polling thread and release all hardware resources."""
        self._polling = False
        for axis in ("x", "y", "z"):
            motor = self._motors[axis]
            if motor.connected:
                self._disconnect_motor_gracefully(motor)
                try:
                    motor.disconnect()
                except Exception:
                    pass
        if shared_state.dev_mode:
            KST101.disable_simulations()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "sections": capture_item_open_states(self.section_node_ids),
                "serials": {
                    ax: str(dpg.get_value(getattr(self, f"_{ax}_serial_combo"))).strip()
                    for ax in ("x", "y", "z")
                },
                "speeds": {
                    key: float(dpg.get_value(input_id))
                    for key, input_id in self._speed_input_ids.items()
                },
                "autofocus": self._capture_autofocus_settings(),
                "x_settings":   self._capture_axis_settings("x"),
                "y_settings":   self._capture_axis_settings("y"),
                "z_settings":   self._capture_axis_settings("z"),
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            self._queue_startup_auto_connect()
            return

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

        saved_speeds = state.get("speeds")
        if isinstance(saved_speeds, dict):
            for key, input_id in self._speed_input_ids.items():
                if key not in saved_speeds:
                    continue
                value = max(0.001, float(saved_speeds[key]))
                dpg.set_value(input_id, value)
                self._speeds_mm_s[key] = value
        elif "jog_speed_mm" in state:
            value = max(0.001, float(state["jog_speed_mm"]))
            for key, input_id in self._speed_input_ids.items():
                dpg.set_value(input_id, value)
                self._speeds_mm_s[key] = value

        autofocus_state = state.get("autofocus")
        if isinstance(autofocus_state, dict):
            self._restore_autofocus_settings(autofocus_state)

        for axis in ("x", "y", "z"):
            key = f"{axis}_settings"
            if key in state:
                self._restore_axis_settings(axis, state[key])

        self._queue_startup_auto_connect()

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

    def _capture_autofocus_settings(self) -> dict:
        return dict(self._collect_autofocus_settings())

    def _restore_autofocus_settings(self, saved: dict):
        for key, input_id in (
            ("search_range_mm", self._autofocus_widgets["search_range_mm"]),
            ("coarse_step_mm", self._autofocus_widgets["coarse_step_mm"]),
            ("fine_step_mm", self._autofocus_widgets["fine_step_mm"]),
            ("settle_time_s", self._autofocus_widgets["settle_time_s"]),
            ("frames_per_position", self._autofocus_widgets["frames_per_position"]),
            ("roi_fraction", self._autofocus_widgets["roi_fraction"]),
            ("focus_to_top_surface", self._autofocus_widgets["focus_to_top_surface"]),
            ("always_calculate_focus_level", self._autofocus_widgets["always_calculate_focus_level"]),
        ):
            if key not in saved:
                continue
            value = saved[key]
            if key == "frames_per_position":
                dpg.set_value(input_id, max(1, int(value)))
            elif key in {"focus_to_top_surface", "always_calculate_focus_level"}:
                dpg.set_value(input_id, bool(value))
            elif key == "roi_fraction":
                dpg.set_value(input_id, min(1.0, max(0.1, float(value))))
            elif key == "search_range_mm":
                dpg.set_value(input_id, max(0.05, float(value)))
            elif key == "coarse_step_mm":
                dpg.set_value(input_id, max(0.01, float(value)))
            elif key == "fine_step_mm":
                dpg.set_value(input_id, max(0.001, float(value)))
            else:
                dpg.set_value(input_id, max(_AUTOFOCUS_SETTLE_MIN_S, float(value)))

        self._collect_autofocus_settings()
