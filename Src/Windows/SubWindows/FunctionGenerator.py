import dearpygui.dearpygui as dpg

from Drivers.PicoScope import SUPPORTED_AWG_WAVEFORMS
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import red_green_button_disabled, red_green_button_enabled


class FunctionGeneratorWindow:

    def __init__(self, get_driver, set_error, *, width=320, height=220, pos=(1315, 10)):
        self._get_driver = get_driver
        self._set_error = set_error
        self._awg_enabled = False

        driver = self._get_driver()

        with dpg.window(
            label="Function Generator",
            tag="#FunctionGenerator",
            width=width,
            height=height,
            pos=pos,
            no_scrollbar=False,
            no_resize=False,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()

            dpg.add_text("Function Generator")
            dpg.add_separator()

            self.awg_enabled_button_id = dpg.add_button(
                label="Disabled",
                width=-1,
                callback=self._on_awg_enabled_toggled,
            )
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
                    default_value=driver.awg_config["frequency_hz"],
                    min_value=0.0,
                    step=100.0,
                    callback=self._on_awg_setting_changed,
                )

                self.awg_amplitude_input_id = dpg.add_input_float(
                    label="Amplitude (Vpp)",
                    width=-120,
                    default_value=driver.awg_config["amplitude_vpp_volts"],
                    min_value=0.0,
                    step=0.1,
                    callback=self._on_awg_setting_changed,
                )

                self.awg_offset_input_id = dpg.add_input_float(
                    label="Offset (V)",
                    width=-120,
                    default_value=driver.awg_config["offset_volts"],
                    step=0.1,
                    callback=self._on_awg_setting_changed,
                )

        self._update_awg_enabled_button()
        self._update_awg_settings_visibility()

    def get_awg_settings(self):
        return {
            "waveform_type": dpg.get_value(self.awg_waveform_combo_id).lower(),
            "frequency_hz": float(dpg.get_value(self.awg_frequency_input_id)),
            "amplitude_vpp_volts": float(dpg.get_value(self.awg_amplitude_input_id)),
            "offset_volts": float(dpg.get_value(self.awg_offset_input_id)),
        }

    def get_awg_enabled(self):
        return bool(self._awg_enabled)

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
        self.apply_to_driver()

    def _on_awg_waveform_changed(self, sender=None, app_data=None, user_data=None):
        self._update_awg_settings_visibility()
        self.apply_to_driver()

    def _on_awg_setting_changed(self, sender=None, app_data=None, user_data=None):
        self.apply_to_driver()

    def apply_to_driver(self):
        driver = self._get_driver()
        try:
            driver.configure_awg(**self.get_awg_settings())
            driver.set_awg_enabled(self._awg_enabled)
        except Exception as exc:
            self._set_error(str(exc))

    def render(self, *, is_open, is_collecting):
        awg_config_enabled = is_open and not is_collecting
        dpg.configure_item(self.awg_enabled_button_id, enabled=awg_config_enabled)
        for item_id in (self.awg_waveform_combo_id, self.awg_frequency_input_id, self.awg_amplitude_input_id, self.awg_offset_input_id):
            dpg.configure_item(item_id, enabled=awg_config_enabled)

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "enabled": bool(self._awg_enabled),
                "waveform": dpg.get_value(self.awg_waveform_combo_id),
                "frequency_hz": float(dpg.get_value(self.awg_frequency_input_id)),
                "amplitude_vpp_volts": float(dpg.get_value(self.awg_amplitude_input_id)),
                "offset_volts": float(dpg.get_value(self.awg_offset_input_id)),
            },
        )

    def LoadState(self, legacy_state=None):
        state = load_state_file(type(self).__name__) or legacy_state or {}
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))

        waveform = str(state.get("waveform", dpg.get_value(self.awg_waveform_combo_id)))
        dpg.set_value(self.awg_waveform_combo_id, waveform)

        if "frequency_hz" in state:
            dpg.set_value(self.awg_frequency_input_id, float(state["frequency_hz"]))
        if "amplitude_vpp_volts" in state:
            dpg.set_value(self.awg_amplitude_input_id, float(state["amplitude_vpp_volts"]))
        if "offset_volts" in state:
            dpg.set_value(self.awg_offset_input_id, float(state["offset_volts"]))

        self._awg_enabled = bool(state.get("enabled", False))
        self._update_awg_enabled_button()
        self._update_awg_settings_visibility()
        self.apply_to_driver()