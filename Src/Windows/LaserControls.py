import Drivers.LaserDriver as LaserDriverModule
from Drivers.PM1000 import PM1000
import dearpygui.dearpygui as dpg
from Utils.fonts import get_segmdl2_icon_font
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file

class LaserControls:

    def __init__(self):
        self.laser                  = LaserDriverModule.LaserDriver()
        self.calibrating_mode       = False
        self.history_capacity       = 120
        self.history_x              = list(range(self.history_capacity))
        self.history_y              = [0.0] * self.history_capacity
        self._last_history_update   = 0.0

        self.pm1000                     = PM1000()
        self.pm1000_connected           = False
        self.pm1000_history_y           = [0.0] * self.history_capacity
        self._last_pm1000_history_update = 0.0
        self._pm1000_selected_index     = 0
        self._pm1000_selected_device_name = ""

        # Set up value sources for laser power to enable real-time updates in the UI
        with dpg.value_registry():
            self.target_power_source = dpg.add_float_value(default_value=self.laser.get_target_power())
            self.actual_power_source = dpg.add_float_value(default_value=self.laser.get_laser_power())
            self.pm1000_power_source = dpg.add_float_value(default_value=0.0)

        mdl = get_segmdl2_icon_font()


        with dpg.window(
            label = "Laser Controls",
            tag = "#LaserControls",
            width = 300,
            height = 500,
            pos = (625, 325),
            no_scrollbar = False,
            no_resize = False,
            no_scroll_with_mouse = True,
        ):
            self.window_id = dpg.last_item()

            dpg.add_text("Laser Connection")
            dpg.add_separator()

            with dpg.group(horizontal=True):
                self.laser_com_port_id = dpg.add_combo(
                    label = "Port",
                    width = -130,
                    items = self.laser.refresh_ports(),
                    default_value = self.laser.COMPort if self.laser.COMPort else "Not Found",
                    callback = self._on_port_selected,
                )

                self.refresh_ports_button_id = dpg.add_button(
                    label = "\uE117",
                    width = 40,
                    callback = self._refresh_ports,
                )

                with dpg.tooltip(self.refresh_ports_button_id):
                    dpg.add_text("Refresh COM ports")

                self.connection_button_id = dpg.add_button(
                    label = "\uE8CD" if self.laser.is_connected() else "\uE71B",
                    width = 40,
                    callback = self._toggle_connection,
                )

                with dpg.tooltip(self.connection_button_id):
                    self.connection_button_tooltip_id = dpg.add_text(
                        "Disconnect laser" if self.laser.is_connected() else "Connect laser"
                    )

                dpg.bind_item_font(self.refresh_ports_button_id, mdl)
                dpg.bind_item_font(self.connection_button_id, mdl)

            dpg.add_spacer(height=6)
            dpg.add_text("Power Meter Connection")
            dpg.add_separator()

            with dpg.group(horizontal=True):
                self.pm1000_device_combo_id = dpg.add_combo(
                    label = " Dev",
                    width = -130,
                    items = [],
                    default_value = "No devices",
                    callback = self._on_pm1000_device_selected,
                )

                self.pm1000_refresh_button_id = dpg.add_button(
                    label = "\uE117",
                    width = 40,
                    callback = self._pm1000_refresh_devices,
                )

                with dpg.tooltip(self.pm1000_refresh_button_id):
                    dpg.add_text("Refresh power meter devices")

                self.pm1000_connect_button_id = dpg.add_button(
                    label = "\uE71B",
                    width = 40,
                    callback = self._pm1000_toggle_connection,
                )

                with dpg.tooltip(self.pm1000_connect_button_id):
                    self.pm1000_connect_tooltip_id = dpg.add_text("Connect power meter")

                dpg.bind_item_font(self.pm1000_refresh_button_id, mdl)
                dpg.bind_item_font(self.pm1000_connect_button_id, mdl)

            dpg.add_spacer(height=10)
            dpg.add_text("Power Control")
            dpg.add_separator()

            with dpg.group(horizontal=True):
                self.laser_indicator_id = dpg.add_color_button(
                    label = "",
                    width = 19,
                    height = 19,
                    enabled = False,
                    tag = "laser_indicator",
                )

                self.laser_button_id = dpg.add_button(
                    label = "Enable Emission",
                    width = -1,
                    callback = self._toggle_emission,
                )

            self.laser_power_id = dpg.add_slider_float(
                label = "Target Power",
                source = self.target_power_source,
                min_value = 0.0,
                max_value = self.laser.max_power_mw,
                format = "%.2f mW",
                callback = self.request_laser_power,
            )

            self.laser_power_input_id = dpg.add_input_float(
                label = "Target Entry",
                source = self.target_power_source,
                min_value = 0.0,
                max_value = self.laser.max_power_mw,
                min_clamped = True,
                max_clamped = True,
                step = 0.5,
                format = "%.2f mW",
                callback = self.request_laser_power,
            )

            self.laser_actual_id = dpg.add_progress_bar(
                label = "Actual",
                default_value = 0.0,
                overlay = "0.00 mW",
            )

            self.pm1000_reading_bar_id = dpg.add_progress_bar(
                label = "Measured",
                default_value = 0.0,
                overlay = "0.0000 mW",
            )

            dpg.add_spacer(height=10)

            with dpg.plot(
                label="Laser Output",
                height=-1,
                width=-1,
                no_title=True,
                no_menus=True,
                no_box_select=True,
            ):
                dpg.add_plot_legend()
                self.power_history_x_axis_id = dpg.add_plot_axis(
                    dpg.mvXAxis,
                    label="",
                    no_gridlines=True,
                    no_tick_labels=True,
                    no_tick_marks=True,
                )
                self.power_history_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="mW")
                dpg.set_axis_limits(self.power_history_axis_id, 0.0, self.laser.max_power_mw)
                self.power_history_series_id = dpg.add_line_series(
                    self.history_x, # type: ignore
                    self.history_y,
                    label="Actual Power",
                    parent=self.power_history_axis_id,
                )
                self.pm1000_history_series_id = dpg.add_line_series(
                    self.history_x, # type: ignore
                    self.pm1000_history_y,
                    label="Measured Power",
                    parent=self.power_history_axis_id,
                )

        self._sync_ui_with_driver()
        self._sync_pm1000_ui()
        self._pm1000_auto_connect()

    def _set_pm1000_device_selection(self, devices, preferred_device=None):
        device_list = list(devices or [])
        combo_items = device_list or ["No devices"]
        dpg.configure_item(self.pm1000_device_combo_id, items=combo_items)

        selected_device = str(preferred_device or "").strip()
        if selected_device not in device_list:
            current_value = str(dpg.get_value(self.pm1000_device_combo_id) or "").strip()
            if current_value in device_list:
                selected_device = current_value

        if selected_device in device_list:
            self._pm1000_selected_index = device_list.index(selected_device)
            self._pm1000_selected_device_name = selected_device
            dpg.set_value(self.pm1000_device_combo_id, selected_device)
            return

        self._pm1000_selected_index = 0
        self._pm1000_selected_device_name = device_list[0] if device_list else ""
        dpg.set_value(self.pm1000_device_combo_id, combo_items[0])

    def _connect_pm1000_selected_device(self):
        self.pm1000.connect(self._pm1000_selected_index)
        self.pm1000_connected = True
        self.pm1000.start_continuous()

    def _pm1000_auto_connect(self):
        try:
            devices = self.pm1000.list_devices()
            self._set_pm1000_device_selection(devices, self._pm1000_selected_device_name)
            if devices:
                self._connect_pm1000_selected_device()
        except Exception:
            self.pm1000_connected = False
        self._sync_pm1000_ui()

    def _pm1000_refresh_devices(self, sender=None, app_data=None, user_data=None):
        try:
            devices = self.pm1000.list_devices()
            self._set_pm1000_device_selection(devices, self._pm1000_selected_device_name)
        except Exception:
            self._set_pm1000_device_selection([], None)

    def _on_pm1000_device_selected(self, sender, app_data, user_data):
        devices = self.pm1000.get_device_names()
        if app_data in devices:
            self._pm1000_selected_index = devices.index(app_data)
            self._pm1000_selected_device_name = str(app_data)

    def _pm1000_toggle_connection(self, sender=None, app_data=None, user_data=None):
        if self.pm1000_connected:
            self.pm1000.disconnect()
            self.pm1000_connected = False
        else:
            try:
                self._connect_pm1000_selected_device()
            except Exception:
                self.pm1000_connected = False
        self._sync_pm1000_ui()

    def _sync_pm1000_ui(self):
        connected = self.pm1000_connected
        dpg.configure_item(self.pm1000_device_combo_id, enabled=not connected)
        dpg.configure_item(self.pm1000_refresh_button_id, enabled=not connected)
        dpg.set_item_label(self.pm1000_connect_button_id, "\uE8CD" if connected else "\uE71B")
        dpg.set_value(self.pm1000_connect_tooltip_id, "Disconnect power meter" if connected else "Connect power meter")
        if not connected:
            dpg.set_value(self.pm1000_power_source, 0.0)

    def _on_port_selected(self, sender, app_data, user_data):
        self.laser.COMPort = app_data
        self._sync_ui_with_driver()

    def _refresh_ports(self, sender=None, app_data=None, user_data=None):
        ports = self.laser.refresh_ports()
        dpg.configure_item(self.laser_com_port_id, items=ports or ["Not Found"])
        dpg.set_value(self.laser_com_port_id, self.laser.COMPort if self.laser.COMPort else "Not Found")
        self._sync_ui_with_driver()

    def _toggle_connection(self, sender=None, app_data=None, user_data=None):
        if self.laser.is_connected():
            self.laser.disconnect()
        else:
            self.laser.connect(dpg.get_value(self.laser_com_port_id))
        self._sync_ui_with_driver()

    def _toggle_emission(self, sender=None, app_data=None, user_data=None):
        self.laser.set_laser_state(not self.laser.get_laser_state())
        self._sync_ui_with_driver()

    def _sync_ui_with_driver(self):
        status = self.laser.get_status()
        connected = status["connected"]

        dpg.set_value(self.target_power_source, status["target_power_mw"])
        dpg.set_value(self.actual_power_source, status["actual_power_mw"])

        actual_mw = status["actual_power_mw"]
        max_mw = self.laser.max_power_mw
        fraction = actual_mw / max_mw if max_mw > 0 else 0.0
        dpg.set_value(self.laser_actual_id, fraction)
        dpg.configure_item(self.laser_actual_id, overlay=f"{actual_mw:.2f} mW")

        dpg.set_item_label(self.connection_button_id, "\uE8CD" if connected else "\uE71B")
        dpg.set_value(self.connection_button_tooltip_id, "Disconnect laser" if connected else "Connect laser")
        dpg.set_item_label(self.laser_button_id, "Disable Emission" if status["emission_enabled"] else "Enable Emission")

        dpg.configure_item(self.laser_com_port_id, enabled=not connected)
        dpg.configure_item(self.refresh_ports_button_id, enabled=not connected)

        dpg.configure_item(self.laser_button_id, enabled=connected)
        dpg.configure_item(self.laser_power_id, enabled=connected)
        dpg.configure_item(self.laser_power_input_id, enabled=connected)

        indicator_color = (0, 200, 0, 255) if status["emission_enabled"] else (40, 40, 40, 255)
        dpg.configure_item(self.laser_indicator_id, default_value=indicator_color)

    def _update_power_history(self, actual_power):
        current_time = dpg.get_total_time()
        if current_time - self._last_history_update < 0.2:
            return

        self._last_history_update = current_time
        self.history_y.append(actual_power)
        if len(self.history_y) > self.history_capacity:
            self.history_y.pop(0)

        dpg.set_value(self.power_history_series_id, [self.history_x[:len(self.history_y)], self.history_y])

    def _update_pm1000_history(self):
        if not self.pm1000_connected:
            return

        current_time = dpg.get_total_time()
        if current_time - self._last_pm1000_history_update < 0.2:
            return
        self._last_pm1000_history_update = current_time

        reading, _unit = self.pm1000.get_latest_reading()

        if reading is None:
            return

        # Convert W to mW for the shared axis
        reading_mw = reading * 1000.0

        dpg.set_value(self.pm1000_power_source, reading_mw)

        max_mw = self.laser.max_power_mw
        fraction = reading_mw / max_mw if max_mw > 0 else 0.0
        dpg.set_value(self.pm1000_reading_bar_id, fraction)
        dpg.configure_item(self.pm1000_reading_bar_id, overlay=f"{reading_mw:.4f} mW")

        self.pm1000_history_y.append(reading_mw)
        if len(self.pm1000_history_y) > self.history_capacity:
            self.pm1000_history_y.pop(0)

        dpg.set_value(self.pm1000_history_series_id, [self.history_x[:len(self.pm1000_history_y)], self.pm1000_history_y])

    def render(self):
        self._sync_ui_with_driver()
        self._update_power_history(self.laser.get_laser_power())
        self._update_pm1000_history()

    def request_laser_power(self, sender, app_data, user_data):
        self.laser.set_laser_power(app_data)
        self._sync_ui_with_driver()

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "laser_com_port": str(dpg.get_value(self.laser_com_port_id)),
                "target_power_mw": float(dpg.get_value(self.target_power_source)),
                "pm1000_device": self._pm1000_selected_device_name,
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))

        saved_port = str(state.get("laser_com_port") or "").strip()
        if saved_port:
            available_ports = self.laser.refresh_ports()
            dpg.configure_item(self.laser_com_port_id, items=available_ports or ["Not Found"])
            if saved_port in available_ports:
                dpg.set_value(self.laser_com_port_id, saved_port)
            self.laser.COMPort = "" if saved_port == "Not Found" else saved_port

        target_power = state.get("target_power_mw")
        if target_power is not None:
            dpg.set_value(self.target_power_source, float(target_power))
            self.laser.set_laser_power(float(target_power))

        saved_pm1000_device = str(state.get("pm1000_device") or "").strip()
        if saved_pm1000_device:
            previous_device = str(dpg.get_value(self.pm1000_device_combo_id) or "").strip()
            self._pm1000_selected_device_name = saved_pm1000_device
            available_devices = self.pm1000.get_device_names()
            self._set_pm1000_device_selection(available_devices, saved_pm1000_device)
            if self.pm1000_connected and previous_device != self._pm1000_selected_device_name:
                try:
                    self.pm1000.disconnect()
                    self.pm1000_connected = False
                    self._connect_pm1000_selected_device()
                except Exception:
                    self.pm1000_connected = False
                self._sync_pm1000_ui()

        self._sync_ui_with_driver()

    def checkLaserState(self):
        return self.laser.get_laser_state()