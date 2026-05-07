import time
import dearpygui.dearpygui as dpg
from Drivers.SyringePumpDriver import SyringePumpDriver
import Utils.themes as themes
from Utils.state_persistence import (
    apply_window_state, capture_window_state,
    load_state_file, save_state_file,
)


class SyringePump:

    def __init__(self):
        self.driver = SyringePumpDriver(None, poll_interval_ms=500)
        self._last_render_time = 0.0

        ports = SyringePumpDriver.list_ports()

        with dpg.window(
            label="Syringe Pump Controls",
            tag="#SyringePumpControls",
            width=300,
            height=720,
            pos=(620, 10),
            no_scrollbar=True,
            no_resize=False,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()

            with dpg.tree_node(label="Connection", default_open=True, span_full_width=True):
                with dpg.group(horizontal=True):
                    self.port_combo = dpg.add_combo(
                        width=-30,
                        items=ports,
                        default_value=ports[0] if ports else "",
                        callback=self._on_port_selected,
                    )
                    dpg.add_button(label="R", width=20, callback=self._refresh_ports)
                with dpg.group(horizontal=True):
                    self.connect_button = dpg.add_button(
                        label="Connect", width=-30, callback=self._toggle_connect,
                    )
                    self.status_led = dpg.add_button(
                        label=" ", width=20, callback=self._toggle_connect,
                    )

            dpg.add_separator()

            with dpg.tree_node(label="Status", default_open=True, span_full_width=True):
                self.status_state = dpg.add_input_text(
                    label="State", width=-110, default_value="Unknown", readonly=True,
                )
                self.status_pos = dpg.add_input_text(
                    label="Position (steps)", width=-110, default_value="0", readonly=True,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Dose", default_open=True, span_full_width=True):
                self.dose_volume = dpg.add_input_float(
                    label="Volume (mL)", width=-110, default_value=1.0, min_value=0.0,
                )
                self.dose_time = dpg.add_input_float(
                    label="Time (s)", width=-110, default_value=10.0, min_value=0.1,
                )
                self.status_time = dpg.add_input_text(
                    label="Elapsed (s)", width=-110, default_value="0", readonly=True, enabled=False,
                )
                self.status_dispensed = dpg.add_input_text(
                    label="Dispensed (mL)", width=-110, default_value="0", readonly=True, enabled=False,
                )
                self.dose_progress = dpg.add_progress_bar(
                    label="Dose Progress", width=-1, default_value=0.0,
                )
                self.dose_button = dpg.add_button(
                    label="Start Dose", width=-1, callback=self._on_start_dose,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Zero & Calibration", default_open=True, span_full_width=True):
                self.cal_actual = dpg.add_input_float(
                    label="Measured (mL)", width=-110, default_value=1.0, min_value=0.0,
                )
                self.steps_per_ml_input = dpg.add_input_text(
                    label="steps/mL", width=-110, default_value="1000.0",
                )
                dpg.add_button(
                    label="Set Zero", width=-1, callback=lambda: self.driver.set_zero(),
                )
                dpg.add_button(
                    label="Calibrate", width=-1, callback=self._on_apply_actual,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Jog / Move", default_open=True, span_full_width=True):
                with dpg.group(horizontal=True):
                    self.jog_steps = dpg.add_input_int(label="", width=100, default_value=100)
                    dpg.add_button(label="- Jog", width=80, callback=lambda: self._on_jog(-1))
                    dpg.add_button(label="+ Jog", width=-1, callback=lambda: self._on_jog(1))
                with dpg.group(horizontal=True):
                    self.goto_steps = dpg.add_input_int(label="", width=100, default_value=100)
                    dpg.add_button(label="Go", width=-1, callback=self._on_goto)
                dpg.add_button(label="Go To Zero", width=-1, callback=lambda: self.driver.goto(0))
                dpg.add_button(label="STOP", width=-1, callback=lambda: self.driver.stop())

            dpg.add_separator()

    # --- Connection ---

    def _refresh_ports(self, sender=None, app_data=None, user_data=None):
        ports = SyringePumpDriver.list_ports()
        dpg.configure_item(self.port_combo, items=ports)
        if ports:
            dpg.set_value(self.port_combo, ports[0])

    def _on_port_selected(self, sender=None, app_data=None, user_data=None):
        pass

    def _toggle_connect(self, sender=None, app_data=None, user_data=None):
        if self.driver.connected:
            self.driver.disconnect()
        else:
            port = str(dpg.get_value(self.port_combo)).strip()
            if port:
                self.driver.connect(port)
        self._refresh_connect_ui()

    def _refresh_connect_ui(self):
        connected = self.driver.connected
        dpg.configure_item(self.connect_button, label="Disconnect" if connected else "Connect")
        if connected:
            dpg.bind_item_theme(self.status_led, themes.red_green_button_enabled)
        else:
            dpg.bind_item_theme(self.status_led, themes.red_green_button_disabled)

    # --- Controls ---

    def _on_start_dose(self, sender=None, app_data=None, user_data=None):
        if not self.driver.connected:
            return
        self.driver.start_dose(dpg.get_value(self.dose_volume), dpg.get_value(self.dose_time))

    def _on_apply_actual(self, sender=None, app_data=None, user_data=None):
        if not self.driver.connected:
            return
        self.driver.calibrate(
            dpg.get_value(self.dose_volume),
            dpg.get_value(self.dose_time),
            dpg.get_value(self.cal_actual),
        )

    def _on_jog(self, direction: int):
        if not self.driver.connected:
            return
        self.driver.jog(dpg.get_value(self.jog_steps) * direction)

    def _on_goto(self, sender=None, app_data=None, user_data=None):
        if not self.driver.connected:
            return
        self.driver.goto(dpg.get_value(self.goto_steps))

    # --- Render ---

    def render(self):
        if time.time() - self._last_render_time < 0.25:
            return
        self._last_render_time = time.time()

        snap = self.driver.snapshot()
        self._refresh_connect_ui()

        dpg.set_value(self.status_state, f"State: {snap.get('state', 'Unknown')}")
        dpg.set_value(self.status_pos, str(snap.get("position") or 0))

        elapsed = (snap.get("elapsed_ms") or 0) / 1000.0
        target = snap.get("target_steps") or 0
        position = snap.get("position") or 0
        dispensed = snap.get("moved_ml") or 0.0
        moved = target - position
        percent = 1.0 - (moved / target) if target else 0.0

        dpg.set_value(self.status_time, f"{elapsed:.2f}")
        dpg.set_value(self.dose_progress, percent)
        dpg.set_value(self.status_dispensed, f"{dispensed:.4f}")

        dosing = snap.get("state") == "RUN_DOSE"
        dpg.configure_item(
            self.dose_button,
            label="Stop Dose" if dosing else "Start Dose",
            callback=(lambda: self.driver.stop()) if dosing else self._on_start_dose,
        )
        dpg.configure_item(self.status_time, enabled=dosing)
        dpg.configure_item(self.status_dispensed, enabled=dosing)

        steps_per_ml = snap.get("steps_per_ml")
        if steps_per_ml is not None:
            try:
                if abs(float(dpg.get_value(self.steps_per_ml_input)) - steps_per_ml) > 1e-6:
                    dpg.set_value(self.steps_per_ml_input, f"{steps_per_ml:.4f}")
            except ValueError:
                pass

    # --- State persistence ---

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "port": str(dpg.get_value(self.port_combo)).strip(),
                "dose_volume": float(dpg.get_value(self.dose_volume)),
                "dose_time": float(dpg.get_value(self.dose_time)),
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))

        saved_port = str(state.get("port") or "").strip()
        if saved_port:
            ports = SyringePumpDriver.list_ports()
            if saved_port not in ports:
                ports.insert(0, saved_port)
            dpg.configure_item(self.port_combo, items=ports)
            dpg.set_value(self.port_combo, saved_port)
            self.driver.port = saved_port

        if "dose_volume" in state:
            dpg.set_value(self.dose_volume, float(state["dose_volume"]))
        if "dose_time" in state:
            dpg.set_value(self.dose_time, float(state["dose_time"]))

        if self.driver.port:
            self.driver.connect(self.driver.port)
            self._refresh_connect_ui()
