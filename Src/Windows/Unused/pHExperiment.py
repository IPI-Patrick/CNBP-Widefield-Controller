import os
import time
import numpy as np
import dearpygui.dearpygui as dpg
from Drivers.pHSensor import pHSensor
from Utils.themes import read_only_theme, red_green_button_disabled, red_green_button_enabled
from Utils.shared_state import class_objects
from Utils.state_persistence import (
    apply_window_state, capture_window_state,
    load_state_file, save_state_file,
)


class pHExperiment:

    def __init__(self):
        self._camera = None
        for obj in class_objects:
            if obj.__class__.__name__ == "CameraSystem":
                self._camera = obj
                break

        self._andor = self._camera.Andor if self._camera else None
        self._ph_sensor = pHSensor()
        self._started = False
        self._finishing = False

        ports = pHSensor.list_ports()

        with dpg.window(
            label="pH Sensing Experiment",
            tag="#pHExperiment",
            width=300,
            height=340,
            pos=(1325, 10),
            no_scrollbar=True,
            no_resize=False,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()

            with dpg.tree_node(label="Settings", default_open=True, span_full_width=True):
                self.com_port_combo = dpg.add_combo(
                    label="COM port",
                    width=-110,
                    items=ports,
                    default_value=ports[0] if ports else "",
                    callback=self._on_comport_change,
                )
                self.num_frames_input = dpg.add_input_int(
                    label="Number of Frames",
                    width=-110,
                    default_value=self._andor.max_acquisitions if self._andor else 100,
                    min_value=1,
                    max_value=10000,
                    step=1,
                    callback=self._on_num_frames_changed,
                )
                self.continuous_check = dpg.add_checkbox(
                    label="Continuous Acquisition",
                    default_value=True,
                )
                self.ph_interval_input = dpg.add_input_float(
                    label="pH Interval (s)",
                    width=-110,
                    default_value=1.0,
                    min_value=0.0,
                    max_value=1000.0,
                    step=0.01,
                    callback=self._on_ph_interval_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Output", default_open=True, span_full_width=True):
                dpg.add_text("Directory")
                with dpg.group(horizontal=True):
                    self.output_dir_input = dpg.add_input_text(
                        width=-30,
                        default_value=os.path.join(os.getcwd(), "Experiments"),
                    )
                    dpg.add_button(
                        width=20, label="F", callback=self._browse_output_dir,
                    )
                dpg.add_text("File Name")
                self.file_name_input = dpg.add_input_text(
                    width=-1,
                    default_value="pH_Experiment",
                )

            dpg.add_separator()

            self.start_button = dpg.add_button(
                label="Start Experiment",
                width=-1,
                callback=self._toggle_experiment,
            )

        self._file_dialog = dpg.add_file_dialog(
            directory_selector=True,
            show=False,
            width=700,
            height=400,
            callback=self._on_dir_selected,
        )

        self._settings_items = (
            self.com_port_combo,
            self.num_frames_input,
            self.continuous_check,
            self.ph_interval_input,
            self.output_dir_input,
            self.file_name_input,
        )

    # --- Callbacks ---

    def _on_comport_change(self, sender=None, app_data=None, user_data=None):
        if app_data:
            self._ph_sensor.connect(str(app_data))

    def _on_num_frames_changed(self, sender=None, app_data=None, user_data=None):
        if self._andor is not None and app_data is not None:
            self._andor.max_acquisitions = int(app_data)

    def _on_ph_interval_changed(self, sender=None, app_data=None, user_data=None):
        if app_data is not None:
            self._ph_sensor.interval = float(app_data)

    def _browse_output_dir(self, sender=None, app_data=None, user_data=None):
        dpg.show_item(self._file_dialog)

    def _on_dir_selected(self, sender=None, app_data=None, user_data=None):
        if app_data and "file_path_name" in app_data:
            dpg.set_value(self.output_dir_input, app_data["file_path_name"])

    def _toggle_experiment(self, sender=None, app_data=None, user_data=None):
        if self._finishing or self._andor is None:
            return
        if self._started:
            self._andor.stop_capture()
            self._started = False
        else:
            if self._camera is not None:
                self._camera.camera_feed.reset_texture()
            self._started = True
            self._ph_sensor.start()
            self._andor.start_capture(
                continuous=dpg.get_value(self.continuous_check),
                callback=self._finish_experiment,
            )

    def _finish_experiment(self, _acq=None):
        self._finishing = True
        self._ph_sensor.stop()

        outdir = str(dpg.get_value(self.output_dir_input))
        os.makedirs(outdir, exist_ok=True)

        ph_state = self._ph_sensor.get_state()
        acquisitions = self._andor.acquisitions
        timestamps = self._andor.timestamps

        name = str(dpg.get_value(self.file_name_input))
        existing = [f for f in os.listdir(outdir) if f.endswith(".npz")]
        indices = [int(f.split("_")[0]) for f in existing if f.split("_")[0].isdigit()]
        idx = max(indices) + 1 if indices else 1

        np.savez_compressed(
            os.path.join(outdir, f"{idx}_{name}.npz"),
            acquisitions=np.array(acquisitions),
            timestamps=np.array(timestamps),
            ph_time=np.array(ph_state["history_timestamps"]),
            ph_values=np.array(ph_state["history_values"]),
        )

        self._started = False
        self._finishing = False

    # --- Render ---

    def render(self):
        if self._andor is None:
            return
        capturing = bool(self._andor.is_capturing)

        if self._finishing:
            dpg.bind_item_theme(self.start_button, red_green_button_disabled)
            dpg.configure_item(self.start_button, label="Finishing Experiment", enabled=False)
        elif self._started:
            dpg.bind_item_theme(self.start_button, red_green_button_enabled)
            dpg.configure_item(self.start_button, label="Stop Experiment", enabled=True)
        else:
            dpg.bind_item_theme(self.start_button, red_green_button_disabled)
            dpg.configure_item(self.start_button, label="Start Experiment", enabled=not capturing)

        for item in self._settings_items:
            dpg.configure_item(item, enabled=not capturing)
            dpg.bind_item_theme(item, read_only_theme if capturing else None)

    # --- State persistence ---

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "port": str(dpg.get_value(self.com_port_combo)).strip(),
                "output_dir": str(dpg.get_value(self.output_dir_input)),
                "file_name": str(dpg.get_value(self.file_name_input)),
                "ph_interval": float(dpg.get_value(self.ph_interval_input)),
                "num_frames": int(dpg.get_value(self.num_frames_input)),
                "continuous": bool(dpg.get_value(self.continuous_check)),
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))

        if "port" in state:
            saved_port = str(state["port"]).strip()
            ports = pHSensor.list_ports()
            if saved_port and saved_port not in ports:
                ports.insert(0, saved_port)
            dpg.configure_item(self.com_port_combo, items=ports)
            dpg.set_value(self.com_port_combo, saved_port)
            if saved_port:
                self._ph_sensor.connect(saved_port)

        if "output_dir" in state:
            dpg.set_value(self.output_dir_input, str(state["output_dir"]))
        if "file_name" in state:
            dpg.set_value(self.file_name_input, str(state["file_name"]))
        if "ph_interval" in state:
            val = float(state["ph_interval"])
            dpg.set_value(self.ph_interval_input, val)
            self._ph_sensor.interval = val
        if "num_frames" in state:
            dpg.set_value(self.num_frames_input, int(state["num_frames"]))
        if "continuous" in state:
            dpg.set_value(self.continuous_check, bool(state["continuous"]))
