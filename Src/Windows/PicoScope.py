import os
import dearpygui.dearpygui as dpg
import numpy as np
import threading

from Drivers.PicoScope import CHANNEL_NAMES, PicoScope as PicoScopeDriver, SUPPORTED_AWG_WAVEFORMS, SUPPORTED_DATA_BITS
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import red_green_button_disabled, red_green_button_enabled
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


class PicoScopeControl:

    def __init__(self):
        self.driver = PicoScopeDriver()
        self.channel_panels = []
        self.status_message = "Idle"
        self._last_error_message = None
        self._loaded_device_serial = ""
        self.available_devices = []
        self._device_refresh_thread = None
        self._device_refresh_pending = None
        self._device_refresh_error = None
        self._device_refresh_in_progress = False
        self._device_refresh_requested = False
        self.oscilloscope_window = None

        with dpg.font_registry():
            mdl_font = os.path.abspath("src/Assets/Fonts/SegMDL2.ttf")
            mdl = dpg.add_font(mdl_font, 12)
            dpg.add_font_chars(chars=[0xE117], parent=mdl)

        with dpg.theme() as self.channel_header_theme:
            with dpg.theme_component(dpg.mvInputText):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, [0, 0, 0, 0])
                dpg.add_theme_color(dpg.mvThemeCol_Border, [0, 0, 0, 0])
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)

        with dpg.window(
            label="PicoScope",
            tag="#PicoScope",
            width=360,
            height=550,
            pos=(945, 10),
            no_scrollbar=False,
            no_resize=False,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()

            dpg.add_text("Connection Settings")
            dpg.add_separator()

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

            dpg.add_spacer(height=10)
            dpg.add_text("Sample Settings")
            dpg.add_separator()

            self.sample_rate_input_id = dpg.add_input_float(
                label="Sample Rate",
                width=-120,
                default_value=self.driver.sample_rate_hz,
                min_value=0.1,
                step=100.0,
                callback=self._on_sample_rate_changed,
            )

            self.seconds_input_id = dpg.add_input_float(
                label="Seconds",
                width=-120,
                default_value=self.driver.history_seconds,
                min_value=0.01,
                step=0.1,
                callback=self._on_history_seconds_changed,
            )

            self.data_bits_combo_id = dpg.add_combo(
                label="Data Bits",
                width=-120,
                items=list(SUPPORTED_DATA_BITS.keys()),
                default_value=self.driver.data_bits,
                callback=self._on_data_bits_changed,
            )

            dpg.add_spacer(height=10)

            with dpg.child_window(border=False, autosize_x=True, autosize_y=True):
                self.channels_container_id = dpg.last_item()

        for channel_spec in CHANNEL_PANEL_SPECS:
            self._create_channel_panel(channel_spec)

        self._create_awg_panel()

        self.oscilloscope_window = OscilloscopeWindow(self._get_oscilloscope_traces)
        self._refresh_available_devices()
        self._sync_driver_channels()
        self._refresh_status_labels()

    def _set_status(self, message, error=None):
        self.status_message = message
        self._last_error_message = error
        self._refresh_status_labels()

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
        try:
            self._device_refresh_pending = self.driver.list_available_devices()
        except Exception as exc:
            self._device_refresh_error = str(exc)

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
                if device["serial"] == self.driver.serial_number:
                    selected_label = device["label"]
                    break

        if selected_label is None and self._loaded_device_serial:
            for device in self.available_devices:
                if device["serial"] == self._loaded_device_serial:
                    selected_label = device["label"]
                    self.driver.set_serial_number(device.get("serial", "") if device.get("has_verified_serial") else "")
                    break

        if selected_label is None:
            selected_label = items[0]
            if self.available_devices:
                selected_device = self.available_devices[0]
                self.driver.set_serial_number(selected_device.get("serial", "") if selected_device.get("has_verified_serial") else "")

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
        enabled_channels = {channel_name: False for channel_name in CHANNEL_NAMES}
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

    def _create_awg_panel(self):
        with dpg.group(parent=self.channels_container_id):
            dpg.add_text("Signal Generator (AWG)")
            dpg.add_separator()

            self.awg_enabled_button_id = dpg.add_button(
                label="Disabled",
                width=-1,
                callback=self._on_awg_enabled_toggled,
            )
            self._awg_enabled = False
            dpg.bind_item_theme(self.awg_enabled_button_id, red_green_button_disabled)

            with dpg.group() as self.awg_settings_group_id:
                self.awg_waveform_combo_id = dpg.add_combo(
                    label="Waveform",
                    width=-120,
                    items=[w.title() for w in SUPPORTED_AWG_WAVEFORMS],
                    default_value="Dc",
                    callback=self._on_awg_waveform_changed,
                )

                self.awg_frequency_input_id = dpg.add_input_float(
                    label="Frequency (Hz)",
                    width=-120,
                    default_value=self.driver.awg_config["frequency_hz"],
                    min_value=0.0,
                    step=100.0,
                    callback=self._on_awg_setting_changed,
                )

                self.awg_amplitude_input_id = dpg.add_input_float(
                    label="Amplitude (Vpp)",
                    width=-120,
                    default_value=self.driver.awg_config["amplitude_vpp_volts"],
                    min_value=0.0,
                    step=0.1,
                    callback=self._on_awg_setting_changed,
                )

                self.awg_offset_input_id = dpg.add_input_float(
                    label="Offset (V)",
                    width=-120,
                    default_value=self.driver.awg_config["offset_volts"],
                    step=0.1,
                    callback=self._on_awg_setting_changed,
                )

            self._update_awg_settings_visibility()

            dpg.add_separator()
            dpg.add_spacer(height=6)

    def _update_awg_enabled_button(self):
        label = "Enabled" if self._awg_enabled else "Disabled"
        theme = red_green_button_enabled if self._awg_enabled else red_green_button_disabled
        dpg.configure_item(self.awg_enabled_button_id, label=label)
        dpg.bind_item_theme(self.awg_enabled_button_id, theme)

    def _update_awg_settings_visibility(self):
        waveform = dpg.get_value(self.awg_waveform_combo_id).lower()
        is_periodic = waveform in ("sine", "square", "triangle")
        dpg.configure_item(self.awg_frequency_input_id, show=is_periodic)
        dpg.configure_item(self.awg_amplitude_input_id, show=is_periodic or waveform == "dc")
        dpg.configure_item(self.awg_offset_input_id, show=True)

    def _on_awg_enabled_toggled(self, sender=None, app_data=None, user_data=None):
        self._awg_enabled = not self._awg_enabled
        self._update_awg_enabled_button()
        self._apply_awg_to_driver()

    def _on_awg_waveform_changed(self, sender=None, app_data=None, user_data=None):
        self._update_awg_settings_visibility()
        self._apply_awg_to_driver()

    def _on_awg_setting_changed(self, sender=None, app_data=None, user_data=None):
        self._apply_awg_to_driver()

    def _apply_awg_to_driver(self):
        waveform = dpg.get_value(self.awg_waveform_combo_id).lower()
        frequency = dpg.get_value(self.awg_frequency_input_id)
        amplitude = dpg.get_value(self.awg_amplitude_input_id)
        offset = dpg.get_value(self.awg_offset_input_id)

        try:
            self.driver.configure_awg(
                waveform_type=waveform,
                frequency_hz=frequency,
                amplitude_vpp_volts=amplitude,
                offset_volts=offset,
            )
            self.driver.set_awg_enabled(self._awg_enabled)
        except Exception as exc:
            self._set_status(self.status_message, error=str(exc))

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
            self.driver.set_serial_number(selected_device.get("serial", "") if selected_device.get("has_verified_serial") else "")

        if not self._apply_stopped_configuration(apply_selection):
            self._refresh_available_devices()
            return

        self._sync_driver_channels()
        self._refresh_status_labels()

    def _on_sample_rate_changed(self, sender, app_data, user_data):
        if not self._apply_stopped_configuration(lambda: self.driver.set_sample_capture_rate(app_data)):
            dpg.set_value(self.sample_rate_input_id, self.driver.sample_rate_hz)

    def _on_history_seconds_changed(self, sender, app_data, user_data):
        if not self._apply_stopped_configuration(lambda: self.driver.set_history_seconds(app_data)):
            dpg.set_value(self.seconds_input_id, self.driver.history_seconds)

    def _on_data_bits_changed(self, sender, app_data, user_data):
        if not self._apply_stopped_configuration(lambda: self.driver.set_data_bits(app_data)):
            dpg.set_value(self.data_bits_combo_id, self.driver.data_bits)

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

    def _get_oscilloscope_traces(self):
        snapshot = self.driver.get_snapshot()
        timestamps = snapshot["timestamps"]
        traces = []

        for panel in self.channel_panels:
            if not panel["enabled"]:
                continue

            channel_name = panel["source_channel"]
            raw_samples = snapshot["channels"].get(channel_name, [])
            if not raw_samples:
                x_values = []
                y_values = []
            else:
                sample_count = len(raw_samples)
                if len(timestamps) >= sample_count:
                    x_array = np.asarray(timestamps[-sample_count:], dtype=np.float64)
                    x_values = (x_array - x_array[0]).tolist()
                else:
                    x_values = list(range(sample_count))

                y_values = self.driver.convert_samples_to_volts(channel_name, raw_samples).astype(np.float32, copy=False).tolist()

            traces.append(
                {
                    "panel_id": panel["id"],
                    "label": panel["display_name"],
                    "color": list(panel["color"]),
                    "x_values": x_values,
                    "y_values": y_values,
                }
            )

        return traces

    def _start_collection(self, sender=None, app_data=None, user_data=None):
        try:
            self.driver.set_sample_capture_rate(dpg.get_value(self.sample_rate_input_id))
            self.driver.set_history_seconds(dpg.get_value(self.seconds_input_id))
            self.driver.set_data_bits(dpg.get_value(self.data_bits_combo_id))
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
            selected_label = dpg.get_value(self.device_combo_id)
            selected_device = next((device for device in self.available_devices if device["label"] == selected_label), None)
            if selected_device is not None:
                self.driver.set_serial_number(selected_device.get("serial", "") if selected_device.get("has_verified_serial") else "")
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
        for item_id in (self.sample_rate_input_id, self.seconds_input_id, self.data_bits_combo_id):
            dpg.configure_item(item_id, enabled=config_enabled)

        device_controls_enabled = config_enabled and not self._device_refresh_in_progress and not is_open
        for item_id in (self.device_combo_id, self.refresh_devices_button_id):
            dpg.configure_item(item_id, enabled=device_controls_enabled)

        for panel in self.channel_panels:
            for item_id in (panel["name_input_id"], panel["enabled_button_id"], panel["color_edit_id"]):
                dpg.configure_item(item_id, enabled=config_enabled)

        awg_config_enabled = is_open and not is_collecting
        dpg.configure_item(self.awg_enabled_button_id, enabled=awg_config_enabled)
        for item_id in (self.awg_waveform_combo_id, self.awg_frequency_input_id, self.awg_amplitude_input_id, self.awg_offset_input_id):
            dpg.configure_item(item_id, enabled=awg_config_enabled)

        if self.driver.last_error is not None:
            self._last_error_message = self.driver.last_error.get("message", "Unknown PicoScope error")

        if self.oscilloscope_window is not None:
            self.oscilloscope_window.render()

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
                "window": capture_window_state(self.window_id),
                "device_serial": self.driver.serial_number,
                "sample_rate_hz": float(dpg.get_value(self.sample_rate_input_id)),
                "history_seconds": float(dpg.get_value(self.seconds_input_id)),
                "data_bits": str(dpg.get_value(self.data_bits_combo_id)),
                "panels": panel_states,
                "awg": {
                    "enabled": self._awg_enabled,
                    "waveform": dpg.get_value(self.awg_waveform_combo_id),
                    "frequency_hz": float(dpg.get_value(self.awg_frequency_input_id)),
                    "amplitude_vpp_volts": float(dpg.get_value(self.awg_amplitude_input_id)),
                    "offset_volts": float(dpg.get_value(self.awg_offset_input_id)),
                },
            },
        )
        if self.oscilloscope_window is not None:
            self.oscilloscope_window.SaveState()

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))

        sample_rate_hz = state.get("sample_rate_hz")
        if sample_rate_hz is not None:
            dpg.set_value(self.sample_rate_input_id, float(sample_rate_hz))
            self.driver.set_sample_capture_rate(float(sample_rate_hz))

        history_seconds = state.get("history_seconds")
        if history_seconds is not None:
            dpg.set_value(self.seconds_input_id, float(history_seconds))
            self.driver.set_history_seconds(float(history_seconds))

        data_bits = state.get("data_bits")
        if data_bits is not None:
            dpg.set_value(self.data_bits_combo_id, str(data_bits))
            self.driver.set_data_bits(str(data_bits))

        saved_serial = str(state.get("device_serial") or "").strip()
        if saved_serial:
            self._loaded_device_serial = saved_serial
            self.driver.set_serial_number(saved_serial)

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

        awg_state = state.get("awg", {})
        if awg_state:
            waveform = str(awg_state.get("waveform", "Dc"))
            dpg.set_value(self.awg_waveform_combo_id, waveform)

            if "frequency_hz" in awg_state:
                dpg.set_value(self.awg_frequency_input_id, float(awg_state["frequency_hz"]))
            if "amplitude_vpp_volts" in awg_state:
                dpg.set_value(self.awg_amplitude_input_id, float(awg_state["amplitude_vpp_volts"]))
            if "offset_volts" in awg_state:
                dpg.set_value(self.awg_offset_input_id, float(awg_state["offset_volts"]))

            self._awg_enabled = bool(awg_state.get("enabled", False))
            self._update_awg_enabled_button()
            self._update_awg_settings_visibility()
            self._apply_awg_to_driver()

        self._sync_driver_channels()
        self._refresh_status_labels()
        if self.oscilloscope_window is not None:
            self.oscilloscope_window.LoadState()
