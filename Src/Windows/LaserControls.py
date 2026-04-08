import os
import Drivers.LaserDriver as LaserDriverModule
import dearpygui.dearpygui as dpg
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file

class LaserControls:

    def __init__(self):
        self.laser                  = LaserDriverModule.LaserDriver()
        self.calibrating_mode       = False
        self.history_capacity       = 120
        self.history_x              = list(range(self.history_capacity))
        self.history_y              = [0.0] * self.history_capacity
        self._last_history_update   = 0.0

        # Set up value sources for laser power to enable real-time updates in the UI
        with dpg.value_registry():
            self.target_power_source = dpg.add_float_value(default_value=self.laser.get_target_power())
            self.actual_power_source = dpg.add_float_value(default_value=self.laser.get_laser_power())


        # Add custom font for icons
        with dpg.font_registry():
            mdl_font = os.path.abspath("src/Assets/Fonts/SegMDL2.ttf")
            mdl = dpg.add_font(mdl_font, 12)
            dpg.add_font_chars(chars=[0xE117, 0xE71B, 0xE8CD], parent=mdl)


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

            dpg.add_text("Connection")
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
                    label = "\uE71B" if self.laser.is_connected() else "\uE8CD",
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

            with dpg.table(
                header_row = False,
                row_background = True,
                borders_innerH = True,
                borders_outerH = True,
                borders_innerV = True,
                borders_outerV = True,
                policy = dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(init_width_or_weight=0.45)
                dpg.add_table_column(init_width_or_weight=0.55)

                with dpg.table_row():
                    dpg.add_text("Port")
                    self.port_status_id = dpg.add_text(self.laser.COMPort or "Not Found")

                with dpg.table_row():
                    dpg.add_text("Connected")
                    self.connection_status_id = dpg.add_text("Yes" if self.laser.is_connected() else "No")

                with dpg.table_row():
                    dpg.add_text("Emission")
                    self.emission_status_id = dpg.add_text("Enabled" if self.laser.get_laser_state() else "Disabled")

                with dpg.table_row():
                    dpg.add_text("Actual Power")
                    self.actual_power_text_id = dpg.add_text(f"{self.laser.get_laser_power():.2f} mW")

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

            self.laser_actual_id = dpg.add_slider_float(
                label = "Measured",
                source = self.actual_power_source,
                min_value = 0.0,
                max_value = self.laser.max_power_mw,
                format = "%.2f mW",
                enabled = False,
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
                self.power_history_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="")
                dpg.set_axis_limits(self.power_history_axis_id, 0.0, self.laser.max_power_mw)
                self.power_history_series_id = dpg.add_line_series(
                    self.history_x,
                    self.history_y,
                    label="Actual Power",
                    parent=self.power_history_axis_id,
                )

        self._sync_ui_with_driver()

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

        dpg.set_value(self.target_power_source, status["target_power_mw"])
        dpg.set_value(self.actual_power_source, status["actual_power_mw"])

        dpg.set_value(self.port_status_id, status["port"])
        dpg.set_value(self.connection_status_id, "Yes" if status["connected"] else "No")
        dpg.set_value(self.emission_status_id, "Enabled" if status["emission_enabled"] else "Disabled")
        dpg.set_value(self.actual_power_text_id, f"{status['actual_power_mw']:.2f} mW")

        dpg.set_item_label(self.connection_button_id, "\uE71B" if status["connected"] else "\uE8CD")
        dpg.set_value(self.connection_button_tooltip_id, "Disconnect laser" if status["connected"] else "Connect laser")
        dpg.set_item_label(self.laser_button_id, "Disable Emission" if status["emission_enabled"] else "Enable Emission")

        dpg.configure_item(self.laser_button_id, enabled=status["connected"])
        dpg.configure_item(self.laser_power_id, enabled=status["connected"])
        dpg.configure_item(self.laser_power_input_id, enabled=status["connected"])

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

    def render(self):
        self._sync_ui_with_driver()
        self._update_power_history(self.laser.get_laser_power())

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

        self._sync_ui_with_driver()


    def checkLaserState(self):
        return self.laser.get_laser_state()