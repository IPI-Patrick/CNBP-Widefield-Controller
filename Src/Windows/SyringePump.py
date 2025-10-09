import threading
import time
from typing import List
import serial.tools.list_ports
import dearpygui.dearpygui as dpg
from Drivers.SyringePumpDriver import SyringePumpDriver
import Utils.themes as themes


def _list_ports() -> List[str]:
    try:
        return [p.device for p in serial.tools.list_ports.comports()]
    except Exception:
        return []

class SyringePump:

    def __init__(self, default_port: str | None = None):
        self.driver = SyringePumpDriver(None, poll_interval_ms=500)
        self.last_snapshot = {}
        self.last_render_time = 0.0
        self.render_interval_s = 0.25  # throttle UI updates

        # Cached list of ports
        self.ports = _list_ports()
        if default_port and default_port not in self.ports:
            self.ports.insert(0, default_port)

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

            dpg.add_text("Connection")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                self.port_combo = dpg.add_combo(
                    width=-30,
                    items=self.ports,
                    default_value=self.ports[0] if self.ports else "",
                    callback=self._on_port_selected,
                )
                dpg.add_button(label="R", width=20, callback=self._refresh_ports)
            with dpg.group(horizontal=True):
                self.connect_button = dpg.add_button(label="Connect", width=-30, callback=self._toggle_connect)
                self.status_led     = dpg.add_button(label=" ", width=20, callback=self._toggle_connect)

            dpg.add_spacer(height=4)
            dpg.add_text("Status")
            dpg.add_separator()
            self.status_state       = dpg.add_input_text(label="State", width=-110, default_value="Unknown", readonly=True)
            self.status_pos         = dpg.add_input_text(label="Position (steps)", width=-110, default_value="0", readonly=True )


            dpg.add_spacer(height=6)
            dpg.add_text("Dose")
            dpg.add_separator()
            self.dose_volume        = dpg.add_input_float(label="Volume (mL)", width=-110, default_value=1.0, min_value=0.0)
            self.dose_time          = dpg.add_input_float(label="Time (s)", width=-110, default_value=10.0, min_value=0.1)
            self.status_time        = dpg.add_input_text(label="Elapsed (s)", width=-110, default_value="0", readonly=True, enabled=False )
            self.status_dispensed   = dpg.add_input_text(label="Dispensed (mL)", width=-110, default_value="0", readonly=True, enabled=False )
            self.dose_progress      = dpg.add_progress_bar(label="Dose Progress", width=-1, default_value=0.0)
            self.dose_button        = dpg.add_button(label="Start Dose", width=-1, callback=self._on_start_dose)


            dpg.add_spacer(height=6)
            dpg.add_text("Zero & Calibration")
            dpg.add_separator()

            self.cal_actual         = dpg.add_input_float(label="Measured (mL)", width=-110, default_value=1.0, min_value=0.0)
            self.steps_per_ml_input = dpg.add_input_text(label="steps/mL", width=-110, default_value="1000.0")
            self.set_zero_button    = dpg.add_button(label="Set Zero", width=-1, callback=lambda: self._send(f"ZERO"))
            self.cal_apply_button   = dpg.add_button(label="Calibrate", width=-1, callback=self._on_apply_actual)

            dpg.add_spacer(height=6)
            dpg.add_text("Jog / Move")
            dpg.add_separator()

            with dpg.group(horizontal=True):
                self.jog_steps = dpg.add_input_int(label="", width=100, default_value=100)
                dpg.add_button(label="- Jog", width=80, callback=lambda: self._on_jog(-1))
                dpg.add_button(label="+ Jog", width=-1, callback=lambda: self._on_jog(1))

            with dpg.group(horizontal=True):
                self.goto_steps = dpg.add_input_int(label="", width=100, default_value=100)
                dpg.add_button(label="Go", width=-1, callback=lambda: self._send(f"GOTO_STEPS {dpg.get_value(self.goto_steps)}"))
            dpg.add_button(label="Go To Zero", width=-1, callback=lambda: self._send(f"GOTO_STEPS 0"))
            dpg.add_button(label="STOP", width=-1, callback=lambda: self._send("STOP"))

        # Auto-connect if a default port exists
        if self.ports:
            self._toggle_connect(auto=True)

    # --------------- UI Callback Helpers ---------------
    def _refresh_ports(self):
        self.ports = _list_ports()
        dpg.configure_item(self.port_combo, items=self.ports)
        if self.ports:
            dpg.set_value(self.port_combo, self.ports[0])

    def _on_port_selected(self, sender, app_data):  # noqa: ARG002
        # Do nothing immediate; user must press connect
        pass

    def _toggle_connect(self, auto: bool = False):
        if self.driver and self.driver.connected:
            self.driver.disconnect()
            dpg.configure_item(self.connect_button, label="Connect")
            dpg.bind_item_theme(self.status_led, themes.red_green_button_disabled)
            return

        port = dpg.get_value(self.port_combo) if self.ports else None
        
        if not port and not auto:
            return
        try:
            dpg.configure_item(self.connect_button, label="Connecting")
            self.driver.connect(port)            
            
            if self.driver.connected:
                dpg.configure_item(self.connect_button, label="Disconnect")
                dpg.bind_item_theme(self.status_led, themes.red_green_button_enabled)
            else:
                dpg.configure_item(self.connect_button, label="Connect")

        except Exception as e:  # noqa: BLE001
            dpg.configure_item(self.status_error, default_value=f"Error: {e}")
            dpg.bind_item_theme(self.status_led, themes.red_green_button_disabled)

    # Command send wrappers
    def _send(self, cmd: str):
        if self.driver and self.driver.connected:
            self.driver.send_command(cmd)

    def _on_start_dose(self):
        if not self.driver or not self.driver.connected:
            return
        vol = dpg.get_value(self.dose_volume)
        sec = dpg.get_value(self.dose_time)
        self._send(f"DO {vol} {sec}")

    def _on_start_calibration(self):
        if not self.driver or not self.driver.connected:
            return
        vol = dpg.get_value(self.dose_volume)
        sec = dpg.get_value(self.dose_time)
        self._send(f"CAL_START {vol} {sec}")

    def _on_apply_actual(self):
        if not self.driver or not self.driver.connected:
            return
        actual = dpg.get_value(self.cal_actual)
        volume = dpg.get_value(self.dose_volume)
        time   = dpg.get_value(self.dose_time)
        self._send(f"CAL {volume} {time} {actual}")

    def _on_jog(self, direction: int):
        if not self.driver or not self.driver.connected:
            return
        steps = dpg.get_value(self.jog_steps) * direction

        print(steps)
        self._send(f"JOG {steps}")


    # --------------- Render Loop ---------------
    def render(self):
        # Throttle updates to reduce overhead
        if time.time() - self.last_render_time < self.render_interval_s:
            return
        self.last_render_time = time.time()

        if not self.driver:
            return
        snap = self.driver.snapshot()
        self.last_snapshot = snap

        # Update LED if connection changed            
        if snap.get('connected'):
            dpg.bind_item_theme(self.status_led, themes.red_green_button_enabled)
        else:
            dpg.bind_item_theme(self.status_led, themes.red_green_button_disabled)

        # Fill status fields
        dpg.set_value(self.status_state, f"State: {snap.get('state')}")


        elapsed     = snap.get('elapsed_ms') / 1000 if snap.get('elapsed_ms') is not None else 0
        target      = snap.get('target_steps')      if snap.get('target_steps') is not None else 0
        position    = snap.get('position')          if snap.get('position') is not None else 0
        dispensed   = snap.get('moved_ml')          if snap.get('moved_ml') is not None else 0.0
        moved       = target - position
        percent     = 1 - (moved / target) if target else 0.0

        dpg.set_value(self.status_pos, f"{position}")
        dpg.set_value(self.status_time, f"{elapsed:.2f}")
        dpg.set_value(self.dose_progress, percent)
        dpg.set_value(self.status_dispensed, f"{dispensed:.4f}")

        if snap.get('state') == "RUN_DOSE":
            dpg.configure_item(self.dose_button, label="Stop Dose", callback=lambda:self._send("STOP"))
            dpg.configure_item(self.status_time, enabled=True)
            dpg.configure_item(self.status_dispensed, enabled=True)
        else:
            dpg.configure_item(self.dose_button, label="Start Dose", callback=self._on_start_dose)
            dpg.configure_item(self.status_time, enabled=False)
            dpg.configure_item(self.status_dispensed, enabled=False)

        # Keep steps/mL input in sync if updated externally
        steps_per_ml    = snap.get('steps_per_ml') if snap.get('steps_per_ml') is not None else 0
        if steps_per_ml and abs(float(dpg.get_value(self.steps_per_ml_input)) - steps_per_ml) > 1e-6:
            dpg.set_value(self.steps_per_ml_input, f"{steps_per_ml:.4f}")

