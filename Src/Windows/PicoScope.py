import dearpygui.dearpygui as dpg
from Utils.custom_widgets import add_input_float
import numpy as np
import threading

from Drivers.PicoScope import PicoScope as PicoScope4000Driver
from Drivers.PicoScope2000 import PicoScope2000
from Utils.fonts import get_segmdl2_icon_font
import Utils.shared_state as shared_state
from Utils.state_persistence import apply_item_open_states, apply_window_state, capture_item_open_states, capture_window_state, load_state_file, save_state_file
from Utils.themes import red_green_button_enabled, red_green_button_disabled
from Windows.SubWindows.FunctionGenerator import FunctionGeneratorWindow
from Windows.SubWindows.Oscilloscope import OscilloscopeWindow


CHANNEL_PANEL_SPECS = (
    {"panel_id": "A", "title": "Channel 1", "source_channel": "A", "default_enabled": True, "default_color": [86, 180, 233, 255]},
    {"panel_id": "B", "title": "Channel 2", "source_channel": "B", "default_enabled": False, "default_color": [230, 159, 0, 255]},
    {"panel_id": "C", "title": "Channel 3", "source_channel": "C", "default_enabled": False, "default_color": [0, 158, 115, 255]},
    {"panel_id": "D", "title": "Channel 4", "source_channel": "D", "default_enabled": False, "default_color": [204, 121, 167, 255]},
    {"panel_id": "E", "title": "Channel 5", "source_channel": "E", "default_enabled": False, "default_color": [213, 94, 0, 255]},
    {"panel_id": "F", "title": "Channel 6", "source_channel": "F", "default_enabled": False, "default_color": [0, 114, 178, 255]},
    {"panel_id": "G", "title": "Channel 7", "source_channel": "G", "default_enabled": False, "default_color": [240, 228, 66, 255]},
    {"panel_id": "H", "title": "Channel 8", "source_channel": "H", "default_enabled": False, "default_color": [128, 128, 128, 255]},
)

DRIVER_FACTORIES = {
    "ps4000a": PicoScope4000Driver,
    "ps2000": PicoScope2000,
}


class PicoScopeControl:

    def __init__(self):
        self.driver_family = "ps4000a"
        self.driver = self._create_driver(self.driver_family)
        self.channel_panels = []
        self.status_message = "Idle"
        self._last_error_message = None
        self._loaded_device_serial = ""
        self._loaded_device_driver_family = self.driver_family
        self.available_devices = []
        self._device_refresh_thread = None
        self._device_refresh_pending = None
        self._device_refresh_error = None
        self._device_refresh_in_progress = False
        self._device_refresh_requested = False
        self.oscilloscope_window = None
        self.function_generator_window = None
        self._oscilloscope_render_snapshot = None
        self._scope_samples_axis = np.zeros((0,), dtype=np.float64)
        self._scope_estimated_time_axis = np.zeros((0,), dtype=np.float64)
        self.section_node_ids = {}

        mdl = get_segmdl2_icon_font()

        with dpg.theme() as self.channel_header_theme:
            with dpg.theme_component(dpg.mvInputText):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_Border, [0, 0, 0, 0])
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)

        _picoscope_tab = shared_state.layout_containers.get("picoscope_tab")
        if _picoscope_tab:
            self.window_id = _picoscope_tab
        else:
            self.window_id = dpg.add_window(
                label="PicoScope",
                tag="#PicoScope",
                width=360,
                height=550,
                pos=(945, 10),
                no_scrollbar=False,
                no_resize=False,
                no_scroll_with_mouse=True,
            )
        dpg.push_container_stack(self.window_id)
        if True:

            with dpg.tree_node(label="Connection Settings", default_open=True, span_full_width=True) as connection_settings_node_id:
                self.section_node_ids["connection_settings"] = connection_settings_node_id
                with dpg.group(horizontal=True):
                    self.device_combo_id = dpg.add_combo(
                        label="Device",
                        width=-120,
                        items=[],
                        default_value="No devices found",
                        callback=self._on_device_selected,
                    )
                    self.refresh_devices_button_id = dpg.add_button(
                        label="\uE117",
                        width=40,
                        callback=self._refresh_available_devices,
                    )

                    with dpg.tooltip(self.refresh_devices_button_id):
                        dpg.add_text("Refresh devices")

                    dpg.bind_item_font(self.refresh_devices_button_id, mdl)

                with dpg.group(horizontal=True):
                    self.open_button_id = dpg.add_button(label="Open", width=-180, callback=self._open_device)
                    self.close_button_id = dpg.add_button(label="Close", width=-1, callback=self._close_device)

                with dpg.group(horizontal=True):
                    self.start_button_id = dpg.add_button(label="Start", width=-180, callback=self._start_collection)
                    self.stop_button_id = dpg.add_button(label="Stop", width=-1, callback=self._stop_collection)

                self.connection_status_id = dpg.add_text("Status: Idle")

            dpg.add_separator()

            with dpg.tree_node(label="Sample Settings", default_open=True, span_full_width=True) as sample_settings_node_id:
                self.section_node_ids["sample_settings"] = sample_settings_node_id
                self.sample_rate_input_id = add_input_float(
                    label="Sample Rate",
                    width=-120,
                    default_value=max(1000.0, self.driver.sample_rate_hz),
                    min_value=1000.0,
                    step=100.0,
                    callback=self._on_sample_rate_changed,
                )
                self.seconds_input_id = add_input_float(
                    label="Seconds",
                    width=-120,
                    default_value=self.driver.history_seconds,
                    min_value=0.01,
                    step=0.1,
                    callback=self._on_history_seconds_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Channels", default_open=True, span_full_width=True) as channels_node_id:
                self.section_node_ids["channels"] = channels_node_id
                with dpg.child_window(border=False, autosize_x=True, autosize_y=True):
                    self.channels_container_id = dpg.last_item()

            dpg.add_separator()

            with dpg.tree_node(label="Function Generator", default_open=True, span_full_width=True) as function_generator_node_id:
                self.section_node_ids["function_generator"] = function_generator_node_id
                self.function_generator_window = FunctionGeneratorWindow(
                    lambda: self.driver,
                    lambda error_message: self._set_status(self.status_message, error=error_message),
                    parent=dpg.last_container(),
                    embedded=True,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Oscilloscope Buffer", default_open=True, span_full_width=True) as oscilloscope_node_id:
                self.section_node_ids["oscilloscope_buffer"] = oscilloscope_node_id
                with dpg.group() as self.oscilloscope_container_id:
                    pass

            dpg.add_separator()

        dpg.pop_container_stack()

        for channel_spec in CHANNEL_PANEL_SPECS:
            self._create_channel_panel(channel_spec)
        self.oscilloscope_window = OscilloscopeWindow(
            [self._make_oscilloscope_trace_getter(panel["id"]) for panel in self.channel_panels],
            channel_headers=[panel["display_name"] for panel in self.channel_panels],
            state_name="OscilloscopeWindow",
            parent=self.oscilloscope_container_id,
            embedded=True,
            height=320,
        )
        self._configure_scope_plot_axes()
        self._refresh_available_devices()
        self._sync_driver_channels()
        self._refresh_status_labels()

    def _create_driver(self, driver_family):
        driver_class = DRIVER_FACTORIES.get(driver_family, PicoScope4000Driver)
        return driver_class()

    def _current_awg_settings(self):
        if self.function_generator_window is not None:
            return self.function_generator_window.get_awg_settings()

        return {
            "waveform_type": self.driver.awg_config["waveform_type"],
            "frequency_hz": float(self.driver.awg_config["frequency_hz"]),
            "amplitude_vpp_volts": float(self.driver.awg_config["amplitude_vpp_volts"]),
            "offset_volts": float(self.driver.awg_config["offset_volts"]),
        }

    def _get_selected_device(self):
        selected_label = dpg.get_value(self.device_combo_id)
        return next((device for device in self.available_devices if device["label"] == selected_label), None)

    def _swap_driver(self, driver_family, serial_number=""):
        requested_family = str(driver_family or "ps4000a").strip().lower()
        if requested_family not in DRIVER_FACTORIES:
            requested_family = "ps4000a"

        if self.driver.is_open or self.driver.is_collecting:
            raise RuntimeError("Close the PicoScope before changing the device family.")

        sample_rate_hz = float(dpg.get_value(self.sample_rate_input_id)) if hasattr(self, "sample_rate_input_id") else self.driver.sample_rate_hz
        history_seconds = float(dpg.get_value(self.seconds_input_id)) if hasattr(self, "seconds_input_id") else self.driver.history_seconds
        awg_settings = self._current_awg_settings()
        awg_enabled = self.function_generator_window.get_awg_enabled() if self.function_generator_window is not None else False

        new_driver = self._create_driver(requested_family)
        new_driver.set_settings(sample_rate_hz=sample_rate_hz, history_seconds=history_seconds)
        new_driver.configure_awg(**awg_settings, enabled=awg_enabled)
        new_driver.serial_number = serial_number

        self.driver = new_driver
        self.driver_family = requested_family
        self._configure_scope_plot_axes(sample_rate_hz, history_seconds)
        self._sync_driver_channels()

    def _configure_scope_plot_axes(self, sample_rate_hz=None, history_seconds=None):
        sample_rate_hz = max(float(self.driver.sample_rate_hz if sample_rate_hz is None else sample_rate_hz), 1e-12)
        history_seconds = max(float(self.driver.history_seconds if history_seconds is None else history_seconds), 0.0)
        sample_count = max(1, int(getattr(self.driver, "buffer_capacity", 0) or round(sample_rate_hz * history_seconds) or 1))
        self._scope_samples_axis = np.arange(sample_count, dtype=np.float64)
        self._scope_estimated_time_axis = self._scope_samples_axis / sample_rate_hz

    def _get_scope_estimated_time_axis(self, sample_count, total_samples_received):
        sample_count = max(0, int(sample_count))
        if sample_count <= 0:
            return np.zeros((0,), dtype=np.float64)

        axis_values = self._scope_estimated_time_axis
        if axis_values.size <= 0:
            return np.zeros((0,), dtype=np.float64)

        sample_count = min(sample_count, int(axis_values.size))
        if int(total_samples_received) >= int(axis_values.size):
            return np.array(axis_values[-sample_count:], copy=True)
        return np.array(axis_values[:sample_count], copy=True)

    def _set_status(self, message, error=None):
        self.status_message = message
        self._last_error_message = error

    def _refresh_status_labels(self):
        status_text = self.status_message
        if self.driver.is_collecting:
            status_text = "Collecting"
        elif self.driver.is_open:
            status_text = "Open"
        if self._last_error_message:
            status_text += f"\nError: {self._last_error_message}"
        dpg.set_value(self.connection_status_id, f"Status: {status_text}")

    def _refresh_available_devices(self, sender=None, app_data=None, user_data=None):
        if self._device_refresh_in_progress:
            self._device_refresh_requested = True
            return

        self._device_refresh_pending = None
        self._device_refresh_error = None
        self._device_refresh_requested = False
        self._device_refresh_in_progress = True
        self._set_status("Refreshing devices...", error=None)
        self._device_refresh_thread = threading.Thread(target=self._refresh_available_devices_worker, name="PicoScopeRefresh", daemon=True)
        self._device_refresh_thread.start()

    def _refresh_available_devices_worker(self):
        devices = []
        errors = []

        for driver_family, driver_class in DRIVER_FACTORIES.items():
            probe_driver = driver_class(
                sample_rate_hz=self.driver.sample_rate_hz,
                history_seconds=self.driver.history_seconds,
            )
            try:
                family_devices = probe_driver.list_available_devices()
                for device in family_devices:
                    candidate = dict(device)
                    candidate["driver_family"] = driver_family
                    devices.append(candidate)
            except Exception as exc:
                errors.append(f"{driver_family}: {exc}")

        self._device_refresh_pending = devices
        if errors and not devices:
            self._device_refresh_error = "\n".join(errors)

    def _apply_available_devices(self, devices):
        self.available_devices = devices
        items = [device["label"] for device in self.available_devices]
        if not items:
            items = ["No devices found"]

        current_label = dpg.get_value(self.device_combo_id)
        dpg.configure_item(self.device_combo_id, items=items)

        selected_label = None
        if current_label in items:
            selected_label = current_label

        if self.driver.serial_number:
            for device in self.available_devices:
                if device.get("driver_family") == self.driver_family and device["serial"] == self.driver.serial_number:
                    selected_label = device["label"]
                    break

        if selected_label is None and self._loaded_device_serial:
            for device in self.available_devices:
                if device.get("driver_family") == self._loaded_device_driver_family and device["serial"] == self._loaded_device_serial:
                    selected_label = device["label"]
                    self._swap_driver(device.get("driver_family", self.driver_family), device.get("serial", "") if device.get("has_verified_serial") else "")
                    break

        if selected_label is None:
            selected_label = items[0]
            if self.available_devices:
                selected_device = self.available_devices[0]
                self._swap_driver(
                    selected_device.get("driver_family", self.driver_family),
                    selected_device.get("serial", "") if selected_device.get("has_verified_serial") else "",
                )

        dpg.set_value(self.device_combo_id, selected_label)
        self._refresh_status_labels()

    def _poll_device_refresh(self):
        if self._device_refresh_thread is not None and self._device_refresh_thread.is_alive():
            return

        if not self._device_refresh_in_progress:
            return

        self._device_refresh_in_progress = False
        self._device_refresh_thread = None

        refresh_error = self._device_refresh_error
        refresh_result = self._device_refresh_pending or []
        self._device_refresh_error = None
        self._device_refresh_pending = None

        if refresh_error:
            self._set_status("Idle", error=refresh_error)
        else:
            self._apply_available_devices(refresh_result)
            self._set_status("Idle", error=None)

        if self._device_refresh_requested:
            self._refresh_available_devices()

    def _sync_driver_channels(self):
        enabled_channels = {channel_name: False for channel_name in self.driver.available_channels}
        for panel in self.channel_panels:
            source_channel = panel["source_channel"]
            if source_channel in enabled_channels and panel["enabled"]:
                enabled_channels[source_channel] = True

        for channel_name, enabled in enabled_channels.items():
            self.driver.configure_channel(channel_name, enabled=enabled)

    def _create_channel_panel(self, channel_spec):
        panel_id = channel_spec["panel_id"]
        display_name = channel_spec["title"]

        with dpg.group(parent=self.channels_container_id):
            with dpg.group(horizontal=True):
                name_input_id = dpg.add_input_text(
                    label="",
                    width=-50,
                    default_value=display_name,
                    callback=self._on_panel_name_changed,
                    user_data=panel_id,
                )
                dpg.bind_item_theme(name_input_id, self.channel_header_theme)

                color_edit_id = dpg.add_color_edit(
                    label="",
                    width=36,
                    default_value=channel_spec["default_color"],
                    no_alpha=False,
                    no_inputs=True,
                    no_label=True,
                    callback=self._on_panel_color_changed,
                    user_data=panel_id,
                )
            dpg.add_separator()

            enabled_button_id = dpg.add_button(
                label="Enabled" if channel_spec["default_enabled"] else "Disabled",
                width=-1,
                callback=self._on_panel_enabled_toggled,
                user_data=panel_id,
            )

            dpg.add_separator()
            dpg.add_spacer(height=6)

        panel = {
            "id": panel_id,
            "name_input_id": name_input_id,
            "enabled_button_id": enabled_button_id,
            "color_edit_id": color_edit_id,
            "title": channel_spec["title"],
            "display_name": display_name,
            "source_channel": channel_spec["source_channel"],
            "enabled": bool(channel_spec["default_enabled"]),
            "color": list(channel_spec["default_color"]),
        }
        self.channel_panels.append(panel)
        self._update_panel_enabled_button(panel)
        self._sync_driver_channels()

    def _get_panel(self, panel_id):
        for panel in self.channel_panels:
            if panel["id"] == panel_id:
                return panel
        raise KeyError(f"Unknown PicoScope panel id {panel_id}")

    def _apply_stopped_configuration(self, action):
        if self.driver.is_collecting:
            self._set_status(self.status_message, error="Stop collection before changing PicoScope settings.")
            return False

        try:
            action()
            self.driver.last_error = None
            self._sync_driver_channels()
            self._refresh_status_labels()
            return True
        except Exception as exc:
            self._set_status(self.status_message, error=str(exc))
            return False

    def _on_device_selected(self, sender, app_data, user_data):
        label = str(app_data)
        selected_device = next((device for device in self.available_devices if device["label"] == label), None)
        if selected_device is None:
            return

        def apply_selection():
            self._swap_driver(
                selected_device.get("driver_family", self.driver_family),
                selected_device.get("serial", "") if selected_device.get("has_verified_serial") else "",
            )

        if not self._apply_stopped_configuration(apply_selection):
            self._refresh_available_devices()
            return

        self._sync_driver_channels()
        self._refresh_status_labels()

    def _on_sample_rate_changed(self, sender, app_data, user_data=None):
        rate = app_data if app_data is not None else float(dpg.get_value(self.sample_rate_input_id))
        if not self._apply_stopped_configuration(lambda: self.driver.set_settings(sample_rate_hz=rate)):
            dpg.set_value(self.sample_rate_input_id, self.driver.sample_rate_hz)
        self._configure_scope_plot_axes()

    def _on_history_seconds_changed(self, sender, app_data, user_data=None):
        seconds = app_data if app_data is not None else float(dpg.get_value(self.seconds_input_id))
        if not self._apply_stopped_configuration(lambda: self.driver.set_settings(history_seconds=seconds)):
            dpg.set_value(self.seconds_input_id, self.driver.history_seconds)
        self._configure_scope_plot_axes()

    def _on_panel_name_changed(self, sender, app_data, panel_id):
        panel = self._get_panel(panel_id)
        panel["display_name"] = str(app_data).strip() or panel["title"]

    def _update_panel_enabled_button(self, panel):
        dpg.configure_item(panel["enabled_button_id"], label="Enabled" if panel["enabled"] else "Disabled")
        dpg.bind_item_theme(panel["enabled_button_id"], red_green_button_enabled if panel["enabled"] else red_green_button_disabled)

    def _on_panel_enabled_toggled(self, sender, app_data, panel_id):
        panel = self._get_panel(panel_id)
        previous_enabled = panel["enabled"]
        panel["enabled"] = not panel["enabled"]
        if not self._apply_stopped_configuration(lambda: None):
            panel["enabled"] = previous_enabled
        self._update_panel_enabled_button(panel)

    def _on_panel_color_changed(self, sender, app_data, panel_id):
        panel = self._get_panel(panel_id)
        color_values = list(app_data)
        if len(color_values) < 4:
            color_values = color_values[:3] + [255]
        panel["color"] = [int(round(value)) for value in color_values[:4]]
        dpg.set_value(panel["color_edit_id"], panel["color"])

    def _make_oscilloscope_trace_getter(self, panel_id):
        return lambda panel_id=panel_id: self._get_oscilloscope_trace(panel_id)

    def _get_oscilloscope_trace(self, panel_id):
        panel = self._get_panel(panel_id)
        if not panel["enabled"]:
            return None

        channel_name = panel["source_channel"]
        if channel_name not in self.driver.available_channels:
            return None

        snapshot = self._oscilloscope_render_snapshot
        if snapshot is None:
            snapshot = self.driver.get_buffer_snapshot(channel_names=(channel_name,))

        raw_samples = np.asarray(snapshot.get("channels", {}).get(channel_name, []), dtype=np.float32)
        if raw_samples.size <= 0:
            x_values = np.zeros((0,), dtype=np.float64)
            y_values = np.zeros((0,), dtype=np.float32)
        else:
            sample_count = int(raw_samples.size)
            axis_capacity = int(self._scope_estimated_time_axis.size)
            if axis_capacity <= 0 or axis_capacity != int(getattr(self.driver, "buffer_capacity", axis_capacity)):
                self._configure_scope_plot_axes(
                    snapshot.get("actual_sample_rate_hz", self.driver.sample_rate_hz),
                    snapshot.get("history_seconds", self.driver.history_seconds),
                )
            total_samples_received = int(snapshot.get("total_samples_received", sample_count))
            x_values = self._get_scope_estimated_time_axis(sample_count, total_samples_received)

            y_values = self.driver.convert_samples_to_volts(channel_name, raw_samples).astype(np.float32, copy=False)

        abs_last_x = float(x_values[-1]) if x_values.size > 0 else 0.0
        return {
            "panel_id": panel["id"],
            "label": panel["display_name"],
            "color": list(panel["color"]),
            "x_values": x_values,
            "y_values": y_values,
            "abs_last_x": abs_last_x,
        }

    def _start_collection(self, sender=None, app_data=None, user_data=None):
        try:
            self.driver.set_settings(
                sample_rate_hz=dpg.get_value(self.sample_rate_input_id),
                history_seconds=dpg.get_value(self.seconds_input_id),
            )
            self._configure_scope_plot_axes()
            self._sync_driver_channels()
            self.driver.start_collection()
            self._set_status("Collecting", error=None)
        except Exception as exc:
            self._set_status("Open" if self.driver.is_open else "Idle", error=str(exc))

    def _stop_collection(self, sender=None, app_data=None, user_data=None):
        self.driver.stop_collection()
        self._set_status("Open" if self.driver.is_open else "Idle", error=None)

    def _open_device(self, sender=None, app_data=None, user_data=None):
        try:
            selected_device = self._get_selected_device()
            if selected_device is not None:
                self._swap_driver(
                    selected_device.get("driver_family", self.driver_family),
                    selected_device.get("serial", "") if selected_device.get("has_verified_serial") else "",
                )
            self.driver.open_device()
            self._set_status("Open", error=None)
        except Exception as exc:
            self._set_status("Idle", error=str(exc))

    def _close_device(self, sender=None, app_data=None, user_data=None):
        try:
            self.driver.close_device()
            self._set_status("Idle", error=None)
        except Exception as exc:
            self._set_status("Idle", error=str(exc))

    def render(self):
        is_collecting = self.driver.is_collecting
        is_open = self.driver.is_open
        self._poll_device_refresh()

        dpg.configure_item(self.open_button_id, enabled=not is_open)
        dpg.configure_item(self.close_button_id, enabled=is_open)
        dpg.configure_item(self.start_button_id, enabled=is_open and not is_collecting)
        dpg.configure_item(self.stop_button_id, enabled=is_collecting)

        config_enabled = not is_collecting
        for item_id in (self.sample_rate_input_id, self.seconds_input_id):
            dpg.configure_item(item_id, enabled=config_enabled)

        device_controls_enabled = config_enabled and not self._device_refresh_in_progress and not is_open
        for item_id in (self.device_combo_id, self.refresh_devices_button_id):
            dpg.configure_item(item_id, enabled=device_controls_enabled)

        for panel in self.channel_panels:
            panel_enabled = config_enabled and panel["source_channel"] in self.driver.available_channels
            for item_id in (panel["name_input_id"], panel["enabled_button_id"], panel["color_edit_id"]):
                dpg.configure_item(item_id, enabled=panel_enabled)

        if self.function_generator_window is not None:
            self.function_generator_window.render(is_open=is_open, is_collecting=is_collecting)

        if self.driver.last_error is not None:
            self._last_error_message = self.driver.last_error.get("message", "Unknown PicoScope error")

        if self.oscilloscope_window is not None and self.oscilloscope_window.is_visible():
            enabled_channels = tuple(
                panel["source_channel"]
                for panel in self.channel_panels
                if panel["enabled"] and panel["source_channel"] in self.driver.available_channels
            )
            self._oscilloscope_render_snapshot = self.driver.get_buffer_snapshot(channel_names=enabled_channels) if enabled_channels else None
            try:
                self.oscilloscope_window.render()
            finally:
                self._oscilloscope_render_snapshot = None

        self._refresh_status_labels()

    def SaveState(self):
        panel_states = []
        for panel in self.channel_panels:
            panel_state = {
                "id": panel["id"],
                "display_name": panel["display_name"],
                "enabled": bool(panel["enabled"]),
                "color": list(panel["color"]),
            }
            panel_states.append(panel_state)

        save_state_file(
            type(self).__name__,
            {
                "sections": capture_item_open_states(self.section_node_ids),
                "device_driver_family": self.driver_family,
                "device_serial": self.driver.serial_number,
                "sample_rate_hz": float(dpg.get_value(self.sample_rate_input_id)),
                "history_seconds": float(dpg.get_value(self.seconds_input_id)),
                "panels": panel_states,
            },
        )
        if self.oscilloscope_window is not None:
            self.oscilloscope_window.SaveState()
        if self.function_generator_window is not None:
            self.function_generator_window.SaveState()

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_item_open_states(self.section_node_ids, state.get("sections"))

        saved_driver_family = str(state.get("device_driver_family") or self.driver_family).strip().lower()
        if saved_driver_family in DRIVER_FACTORIES:
            self._swap_driver(saved_driver_family)
            self._loaded_device_driver_family = saved_driver_family

        sample_rate_hz = state.get("sample_rate_hz")
        if sample_rate_hz is not None:
            sample_rate_hz = max(1000.0, float(sample_rate_hz))
            dpg.set_value(self.sample_rate_input_id, sample_rate_hz)
            self.driver.set_settings(sample_rate_hz=sample_rate_hz)

        history_seconds = state.get("history_seconds")
        if history_seconds is not None:
            dpg.set_value(self.seconds_input_id, float(history_seconds))
            self.driver.set_settings(history_seconds=float(history_seconds))

        self._configure_scope_plot_axes()

        saved_serial = str(state.get("device_serial") or "").strip()
        if saved_serial:
            self._loaded_device_serial = saved_serial
            self.driver.serial_number = saved_serial

        for panel_state in state.get("panels", []):
            try:
                panel = self._get_panel(panel_state["id"])
            except Exception:
                continue

            if "display_name" in panel_state:
                panel["display_name"] = str(panel_state["display_name"]).strip() or panel["title"]
                dpg.set_value(panel["name_input_id"], panel["display_name"])

            if "color" in panel_state:
                color_values = list(panel_state["color"])
                if len(color_values) < 4:
                    color_values = color_values[:3] + [255]
                panel["color"] = [int(round(value)) for value in color_values[:4]]
                dpg.set_value(panel["color_edit_id"], panel["color"])

            if "enabled" in panel_state:
                panel["enabled"] = bool(panel_state["enabled"])
                self._update_panel_enabled_button(panel)

        self._sync_driver_channels()
        self._refresh_status_labels()
        if self.oscilloscope_window is not None:
            self.oscilloscope_window.LoadState()
        if self.function_generator_window is not None:
            self.function_generator_window.LoadState(state.get("awg"))
