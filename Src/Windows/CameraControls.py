import os  
import shutil
import psutil
import time
import numpy as np
import threading
import dearpygui.dearpygui as dpg
from Drivers.Andor import Andor
from Drivers.PicoScope import SUPPORTED_AWG_WAVEFORMS
from Windows.SubWindows.CameraFeed import CameraFeedWindow
from Windows.SubWindows.GraphWindow import GraphWindow
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.utils import scale
from Utils.themes import read_only_theme, red_green_button_disabled, red_green_button_enabled
import Utils.shared_state as shared_state
from Utils.shared_state import class_objects

class CameraSystem:    

    def __init__(self):

        self.acquisition_in_progress = False
        self.acquisition_stop_requested = False
        self.acquisition_duration_seconds = 2.0
        self.acquisition_frame_rate_hz = 0.0
        self.acquisition_scope_sample_rate_hz = 1000.0
        self.acquisition_zero_on_start = False
        self.acquisition_set_awg_on_start = False
        self.acquisition_awg_waveform = "dc"
        self.acquisition_awg_dc_offset_volts = 0.0
        self.acquisition_awg_frequency_hz = 1000.0
        self.acquisition_awg_amplitude_vpp_volts = 1.0
        self.acquisition_awg_periodic_offset_volts = 0.0
        self.acquisition_awg_start_after_seconds = 0.0
        self.acquisition_target_frames = 0
        self.acquisition_scope_target_samples = 0
        self.acquisition_scope_buffer_seconds = 0.0
        self.acquisition_started_at = None
        self.acquisition_scope_duration_seconds = 0.0
        self._acquisition_thread = None
        self._acquisition_awg_thread = None
        self._acquisition_awg_stop_event = None
        self._completed_acquisition_payload = None
        self._pending_acquisition_result = None
        self._acquisition_lock = threading.Lock()
        self.preview_zero_reference_pending = False

        with dpg.window(
            label                = "Camera Controls",
            tag                  = "#CameraControls",
            width                = 300,
            height               = 620,
            pos                  = (625, 10 ),
            no_scrollbar         = True,
            no_resize            = False,
            no_scroll_with_mouse = True,
        ):

            # STARTUP
            # ################################################################
            # Set up the window and thread lock
            self.window_id      = dpg.last_item()
            self.lock           = threading.Lock()

            # Set up the camera
            self.Andor          = Andor()
            shared_state.shared_andor = self.Andor
            self.camera         = self.Andor.camera
            cam                 = self.camera   
            self.started        = False
            self.acquisition_frame_rate_hz = float(self.Andor.get_frame_rate())

            # Set up the Preview Window
            self.camera_feed   = CameraFeedWindow(
                parent      = self.window_id,
                Andor       = self.Andor
            )

            # self.mean_graph   = GraphWindow(
            #     name        = "Mean Intensity",
            #     id          = "MeanIntensityGraph",
            #     getYValues  = lambda: self.Andor.meanBuffer,
            #     getXValues  = lambda: range(len(self.Andor.meanBuffer)),
            #     xlabel      = "Acquisitions",
            #     ylabel      = "Mean Intensity",
            #     xpos        = 10,
            #     ypos        = 625
            # )

            with dpg.theme() as self.stop_button_theme:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, [70, 70, 70])
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, [140, 30, 30])
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, [110, 20, 20])

            dpg.add_text("Camera Settings")
            dpg.add_separator()

            self.settings_exposure_time = dpg.add_input_float(
                label           = "Exposure Time",
                width           = -110,
                default_value   = cam.ExposureTime,
                min_value       = 0.001,
                max_value       = 1,
                step            = 0.01,
                format          = "%.3f s",
                callback        = lambda: self.setprop("ExposureTime", self.settings_exposure_time)
            )

            
            self.settings_trigger_mode = dpg.add_combo(
                label           = "Trigger Mode",
                width           = -110,
                items           = cam.options_TriggerMode,
                default_value   = cam.TriggerMode,
                callback        = lambda: self.setprop("TriggerMode", self.settings_trigger_mode)
            )

            dpg.add_spacer(height=20)
            dpg.add_text("Frame Settings")
            dpg.add_separator()

            self.settings_pixel_binning = dpg.add_combo(
                label           = "Pixel Binning",
                width           = -110,
                items           = cam.options_AOIBinning,
                default_value   = cam.AOIBinning,
                callback        = lambda: self.setprop("AOIBinning", self.settings_pixel_binning)
            )

            self.settings_image_width = dpg.add_input_int(
                label           = "Width",
                width           = -110,
                default_value   = cam.AOIWidth,
                min_value       = 1,
                max_value       = cam.AOIWidth,
                callback        = lambda: self.setprop("AOIWidth", self.settings_image_width)
            )

            self.settings_image_height = dpg.add_input_int(
                label           = "Height",
                width           = -110,
                default_value   = cam.AOIHeight,
                min_value       = 1,
                max_value       = cam.AOIHeight,
                callback        = lambda: self.setprop("AOIHeight", self.settings_image_height),
            )

            self.settings_image_left = dpg.add_input_int(
                label           = "Left",
                width           = -110,
                default_value   = cam.AOILeft,
                min_value       = 1,
                max_value       = cam.AOILeft,
                callback        = lambda: self.setprop("AOILeft", self.settings_image_left),
            )

            self.settings_image_top = dpg.add_input_int(
                label           = "Top",
                width           = -110,
                default_value   = cam.AOITop,
                min_value       = 1,
                max_value       = cam.AOITop,
                callback        = lambda: self.setprop("AOITop", self.settings_image_top),
            )


            # Add the start/stop button
            self.start_button_id = dpg.add_button(
                label           = "Start Preview",
                width           = -1,  
                callback        = self.toggle_preview,
                tag             = "start_camera_button"
            )

            dpg.add_spacer(height=20)
            dpg.add_text("Acquisition Settings")
            dpg.add_separator()

            self.acquisition_duration_input_id = dpg.add_input_float(
                label="Seconds",
                width=-110,
                default_value=self.acquisition_duration_seconds,
                min_value=0.01,
                min_clamped=True,
                step=0.1,
            )

            self.acquisition_frame_rate_input_id = dpg.add_input_float(
                label="FPS",
                width=-110,
                default_value=self.acquisition_frame_rate_hz,
                min_value=0.1,
                min_clamped=True,
                step=1.0,
            )

            self.acquisition_scope_rate_input_id = dpg.add_input_float(
                label="Scope Hz",
                width=-110,
                default_value=self.acquisition_scope_sample_rate_hz,
                min_value=0.1,
                min_clamped=True,
                step=100.0,
            )

            self.acquisition_zero_on_start_checkbox_id = dpg.add_checkbox(
                label="Zero on Start",
                default_value=self.acquisition_zero_on_start,
            )

            self.acquisition_set_awg_on_start_checkbox_id = dpg.add_checkbox(
                label="Set AWG on Start",
                default_value=self.acquisition_set_awg_on_start,
                callback=self._on_awg_on_start_changed,
            )

            with dpg.group(show=False) as self.acquisition_awg_group_id:
                self.acquisition_awg_waveform_combo_id = dpg.add_combo(
                    label="Waveform",
                    width=-110,
                    items=[waveform.title() for waveform in SUPPORTED_AWG_WAVEFORMS],
                    default_value=self.acquisition_awg_waveform.title(),
                    callback=self._on_acquisition_awg_waveform_changed,
                )

                with dpg.group(show=True) as self.acquisition_awg_dc_group_id:
                    self.acquisition_awg_dc_offset_input_id = dpg.add_input_float(
                        label="Offset (V)",
                        width=-110,
                        default_value=self.acquisition_awg_dc_offset_volts,
                        step=0.1,
                    )

                with dpg.group(show=False) as self.acquisition_awg_periodic_group_id:
                    self.acquisition_awg_frequency_input_id = dpg.add_input_float(
                        label="Frequency (Hz)",
                        width=-110,
                        default_value=self.acquisition_awg_frequency_hz,
                        min_value=0.0,
                        min_clamped=True,
                        step=100.0,
                    )
                    self.acquisition_awg_amplitude_input_id = dpg.add_input_float(
                        label="Amplitude (Vpp)",
                        width=-110,
                        default_value=self.acquisition_awg_amplitude_vpp_volts,
                        min_value=0.0,
                        min_clamped=True,
                        step=0.1,
                    )
                    self.acquisition_awg_periodic_offset_input_id = dpg.add_input_float(
                        label="Offset (V)",
                        width=-110,
                        default_value=self.acquisition_awg_periodic_offset_volts,
                        step=0.1,
                    )

                self.acquisition_awg_start_after_input_id = dpg.add_input_float(
                    label="Start After",
                    width=-110,
                    default_value=self.acquisition_awg_start_after_seconds,
                    min_value=0.0,
                    min_clamped=True,
                    step=0.1,
                    format="%.2f s",
                )

            self.acquisition_progress_bar_id = dpg.add_progress_bar(
                width=-1,
                height=18,
                default_value=0.0,
                overlay="Idle",
            )

            self.acquire_button_id = dpg.add_button(
                label="Acquire",
                width=-1,
                height=36,
                callback=self._on_acquire_button_pressed,
            )
            dpg.bind_item_theme(self.acquire_button_id, red_green_button_enabled)

            self.save_button_id = dpg.add_button(
                label="Save",
                width=-1,
                enabled=False,
                callback=self._show_save_dialog,
            )

            with dpg.file_dialog(
                directory_selector=False,
                show=False,
                callback=self._on_save_dialog_selected,
                width=700,
                height=400,
                modal=True,
            ) as self.save_dialog_id:
                dpg.add_file_extension(".npz", color=(0, 255, 0, 255))

        self._update_preview_button_state()
        self._update_acquisition_button_state()
        self._update_acquisition_awg_visibility()
        self._set_acquisition_progress(0.0, "Idle")

    @property
    def settings(self):
        return [getattr(self, attr) for attr in vars(self) if attr.startswith('settings_')]

    def _get_scope_controller(self):
        for obj in class_objects:
            if obj.__class__.__name__ == "PicoScopeControl":
                return obj
        return None

    def _update_preview_button_state(self):
        if self.started:
            dpg.configure_item(self.start_button_id, label="Stop Preview")
            dpg.bind_item_theme(self.start_button_id, red_green_button_enabled)
        else:
            dpg.configure_item(self.start_button_id, label="Start Preview")
            dpg.bind_item_theme(self.start_button_id, red_green_button_disabled)

    def _update_acquisition_button_state(self):
        if self.acquisition_in_progress:
            dpg.configure_item(self.acquire_button_id, label="Stop")
            dpg.bind_item_theme(self.acquire_button_id, self.stop_button_theme)
        else:
            dpg.configure_item(self.acquire_button_id, label="Acquire")
            dpg.bind_item_theme(self.acquire_button_id, red_green_button_enabled)

    def _update_acquisition_awg_visibility(self):
        show_awg_controls = bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id))
        dpg.configure_item(self.acquisition_awg_group_id, show=show_awg_controls)

        waveform_name = str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower()
        is_dc = waveform_name == "dc"
        dpg.configure_item(self.acquisition_awg_dc_group_id, show=is_dc)
        dpg.configure_item(self.acquisition_awg_periodic_group_id, show=not is_dc)

    def _on_awg_on_start_changed(self, sender, app_data, user_data=None):
        self.acquisition_set_awg_on_start = bool(app_data)
        self._update_acquisition_awg_visibility()

    def _on_acquisition_awg_waveform_changed(self, sender, app_data, user_data=None):
        self.acquisition_awg_waveform = str(app_data).strip().lower()
        self._update_acquisition_awg_visibility()

    def _collect_acquisition_awg_config(self):
        waveform_type = str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower()
        if waveform_type == "dc":
            return {
                "waveform_type": waveform_type,
                "offset_volts": float(dpg.get_value(self.acquisition_awg_dc_offset_input_id)),
                "amplitude_vpp_volts": 0.0,
                "frequency_hz": 0.0,
            }

        return {
            "waveform_type": waveform_type,
            "offset_volts": float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id)),
            "amplitude_vpp_volts": float(dpg.get_value(self.acquisition_awg_amplitude_input_id)),
            "frequency_hz": float(dpg.get_value(self.acquisition_awg_frequency_input_id)),
        }

    def _set_acquisition_progress(self, progress_value, overlay_text):
        dpg.set_value(self.acquisition_progress_bar_id, max(0.0, min(1.0, float(progress_value))))
        dpg.configure_item(self.acquisition_progress_bar_id, overlay=str(overlay_text))

    def _stop_acquisition_awg_thread(self):
        if self._acquisition_awg_stop_event is not None:
            self._acquisition_awg_stop_event.set()
        if self._acquisition_awg_thread is not None and self._acquisition_awg_thread.is_alive():
            self._acquisition_awg_thread.join(timeout=0.2)
        self._acquisition_awg_thread = None
        self._acquisition_awg_stop_event = None

    def _run_acquisition_awg_thread(self, scope_controller, start_after_seconds, stop_event):
        remaining_seconds = max(0.0, float(start_after_seconds))
        deadline = time.perf_counter() + remaining_seconds

        while not stop_event.is_set():
            remaining_seconds = deadline - time.perf_counter()
            if remaining_seconds <= 0.0:
                break
            stop_event.wait(min(0.05, remaining_seconds))

        if stop_event.is_set() or not self.acquisition_in_progress:
            return

        try:
            if scope_controller.driver.is_collecting:
                scope_controller.driver.pause_collection()
            if stop_event.is_set() or not self.acquisition_in_progress:
                return
            scope_controller.driver.set_awg_enabled(True)
            if stop_event.is_set() or not self.acquisition_in_progress:
                return
            scope_controller.driver.resume_collection()
        except Exception as exc:
            print(f"Acquisition AWG enable failed: {exc}")

    def _build_completed_acquisition_payload(self, stopped_early):
        camera_snapshot = self.Andor.get_snapshot()
        scope_controller = self._get_scope_controller()
        scope_snapshot = scope_controller.driver.get_snapshot() if scope_controller is not None else None

        return {
            "camera": camera_snapshot,
            "scope": scope_snapshot,
            "stopped_early": bool(stopped_early),
            "requested_duration_seconds": float(self.acquisition_scope_duration_seconds),
            "requested_frame_rate_hz": float(self.acquisition_frame_rate_hz),
            "requested_scope_sample_rate_hz": float(self.acquisition_scope_sample_rate_hz),
            "requested_scope_sample_count": int(self.acquisition_scope_target_samples),
            "requested_scope_buffer_seconds": float(self.acquisition_scope_buffer_seconds),
            "zero_on_start": bool(self.acquisition_zero_on_start),
            "set_awg_on_start": bool(self.acquisition_set_awg_on_start),
            "awg_waveform": self.acquisition_awg_waveform,
            "awg_start_after_seconds": float(self.acquisition_awg_start_after_seconds),
        }

    def _apply_pending_acquisition_result(self):
        with self._acquisition_lock:
            payload = self._pending_acquisition_result
            self._pending_acquisition_result = None

        if payload is None:
            return

        self._completed_acquisition_payload = payload
        self.acquisition_in_progress = False
        self.acquisition_stop_requested = False
        self.acquisition_started_at = None
        self._acquisition_thread = None
        self._update_acquisition_button_state()
        dpg.configure_item(self.save_button_id, enabled=True)

        frame_count = len(payload["camera"]["acquisitions"])
        if payload["stopped_early"]:
            self._set_acquisition_progress(1.0, f"Stopped ({frame_count} frames)")
        else:
            self._set_acquisition_progress(1.0, f"Complete ({frame_count} frames)")

    def _run_acquisition(self, scope_controller, target_frames, scope_duration_seconds):
        stopped_early = False
        scope_stopped = False
        awg_enabled_for_run = bool(self.acquisition_set_awg_on_start)

        try:
            while True:
                if self.acquisition_stop_requested:
                    stopped_early = True
                    break

                camera_done = not self.Andor.is_capturing
                if camera_done and not scope_stopped:
                    scope_controller.driver.stop_collection()
                    scope_stopped = True

                if camera_done and scope_stopped:
                    break

                time.sleep(0.02)
        finally:
            self._stop_acquisition_awg_thread()
            if self.Andor.is_capturing:
                self.Andor.stop_capture()
                stopped_early = True
            if awg_enabled_for_run:
                try:
                    scope_controller.driver.set_awg_enabled(False)
                except Exception:
                    pass
            if scope_controller.driver.is_collecting:
                scope_controller.driver.stop_collection()
                scope_stopped = True

            with self._acquisition_lock:
                self._pending_acquisition_result = self._build_completed_acquisition_payload(stopped_early)

    def _on_acquire_button_pressed(self, sender=None, app_data=None, user_data=None):
        if self.acquisition_in_progress:
            self.acquisition_stop_requested = True
            if self.Andor.is_capturing:
                self.Andor.stop_capture()
            scope_controller = self._get_scope_controller()
            if scope_controller is not None and scope_controller.driver.is_collecting:
                scope_controller.driver.stop_collection()
            return

        scope_controller = self._get_scope_controller()
        if scope_controller is None:
            self._set_acquisition_progress(0.0, "PicoScope window not available")
            return
        if not scope_controller.driver.is_open:
            self._set_acquisition_progress(0.0, "Open the PicoScope first")
            return

        acquisition_seconds = max(0.01, float(dpg.get_value(self.acquisition_duration_input_id)))
        acquisition_fps = max(0.1, float(dpg.get_value(self.acquisition_frame_rate_input_id)))
        scope_sample_rate = max(0.1, float(dpg.get_value(self.acquisition_scope_rate_input_id)))
        awg_set_on_start = bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id))
        awg_start_after_seconds = max(0.0, float(dpg.get_value(self.acquisition_awg_start_after_input_id)))
        target_frames = max(1, int(round(acquisition_seconds * acquisition_fps)))
        target_scope_samples = max(1, int(round(acquisition_seconds * scope_sample_rate)))
        scope_buffer_seconds = acquisition_seconds * 1.5

        if awg_set_on_start and awg_start_after_seconds >= acquisition_seconds:
            self._set_acquisition_progress(0.0, "AWG delay must be shorter than acquisition")
            return

        try:
            if self.Andor.is_capturing:
                self.Andor.stop_capture()
            if self.started:
                self.started = False
                self._update_preview_button_state()

            if scope_controller.driver.is_collecting:
                scope_controller.driver.stop_collection()

            if bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id)):
                self.Andor.set_zero_frame(np.array(self.Andor.latest_frame, copy=True))

            self.Andor.set_frame_rate(acquisition_fps)
            self.Andor.clear_buffers(reset_frame_index=True)
            scope_controller.driver.stop_collection()
            scope_controller.driver.set_sample_capture_rate(scope_sample_rate)
            scope_controller.driver.set_history_seconds(scope_buffer_seconds)
            scope_controller.driver.clear_buffers()
            if awg_set_on_start:
                scope_controller.driver.configure_awg(**self._collect_acquisition_awg_config())
                scope_controller.driver.set_awg_enabled(False)

            self.acquisition_duration_seconds = acquisition_seconds
            self.acquisition_frame_rate_hz = acquisition_fps
            self.acquisition_scope_sample_rate_hz = scope_sample_rate
            self.acquisition_zero_on_start = bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id))
            self.acquisition_set_awg_on_start = awg_set_on_start
            self.acquisition_awg_waveform = str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower()
            self.acquisition_awg_dc_offset_volts = float(dpg.get_value(self.acquisition_awg_dc_offset_input_id))
            self.acquisition_awg_frequency_hz = float(dpg.get_value(self.acquisition_awg_frequency_input_id))
            self.acquisition_awg_amplitude_vpp_volts = float(dpg.get_value(self.acquisition_awg_amplitude_input_id))
            self.acquisition_awg_periodic_offset_volts = float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id))
            self.acquisition_awg_start_after_seconds = awg_start_after_seconds
            self.acquisition_target_frames = target_frames
            self.acquisition_scope_target_samples = target_scope_samples
            self.acquisition_scope_buffer_seconds = scope_buffer_seconds
            self.acquisition_scope_duration_seconds = acquisition_seconds
            self.acquisition_started_at = time.perf_counter()
            self.acquisition_in_progress = True
            self.acquisition_stop_requested = False

            with self._acquisition_lock:
                self._completed_acquisition_payload = None

            dpg.configure_item(self.save_button_id, enabled=False)
            self._set_acquisition_progress(0.0, f"0/{target_frames} frames")
            self._update_acquisition_button_state()
            self.camera_feed.reset_texture()
            self.camera_feed.recalculate_rois()

            self.Andor.start_capture_fixed(target_frames)
            scope_controller.driver.start_collection()
            if awg_set_on_start:
                self._acquisition_awg_stop_event = threading.Event()
                self._acquisition_awg_thread = threading.Thread(
                    target=self._run_acquisition_awg_thread,
                    args=(scope_controller, awg_start_after_seconds, self._acquisition_awg_stop_event),
                    daemon=True,
                )
                self._acquisition_awg_thread.start()
            self._acquisition_thread = threading.Thread(
                target=self._run_acquisition,
                args=(scope_controller, target_frames, acquisition_seconds),
                daemon=True,
            )
            self._acquisition_thread.start()
        except Exception as exc:
            self._stop_acquisition_awg_thread()
            self.acquisition_in_progress = False
            self.acquisition_stop_requested = False
            self.acquisition_started_at = None
            self._acquisition_thread = None
            try:
                if self.Andor.is_capturing:
                    self.Andor.stop_capture()
            except Exception:
                pass
            try:
                if scope_controller.driver.is_collecting:
                    scope_controller.driver.stop_collection()
            except Exception:
                pass
            self._update_acquisition_button_state()
            self._set_acquisition_progress(0.0, f"Error: {exc}")

    def _show_save_dialog(self, sender=None, app_data=None, user_data=None):
        if self._completed_acquisition_payload is None:
            return
        dpg.show_item(self.save_dialog_id)

    def _on_save_dialog_selected(self, sender, app_data, user_data=None):
        file_path = str(app_data.get("file_path_name") or "").strip()
        if not file_path:
            return
        if not file_path.lower().endswith(".npz"):
            file_path = f"{file_path}.npz"

        with self._acquisition_lock:
            payload = self._completed_acquisition_payload

        if payload is None:
            return

        camera = payload["camera"]
        scope = payload["scope"] or {}
        channel_payload = scope.get("channels", {})

        np.savez_compressed(
            file_path,
            camera_acquisitions=np.asarray(camera["acquisitions"]),
            camera_difference=np.asarray(camera["difference"]),
            camera_contrast=np.asarray(camera.get("contrast", [])),
            camera_timestamps=np.asarray(camera["timestamps"], dtype=np.float64),
            camera_mean_buffer=np.asarray(camera["mean_buffer"], dtype=np.float64),
            camera_zero=np.asarray(camera["zero"]),
            scope_timestamps=np.asarray(scope.get("timestamps", []), dtype=np.float64),
            scope_channel_A=np.asarray(channel_payload.get("A", [])),
            scope_channel_B=np.asarray(channel_payload.get("B", [])),
            scope_actual_sample_rate_hz=np.asarray([scope.get("actual_sample_rate_hz") or self.acquisition_scope_sample_rate_hz], dtype=np.float64),
            scope_history_seconds=np.asarray([scope.get("history_seconds") or self.acquisition_scope_duration_seconds], dtype=np.float64),
            requested_duration_seconds=np.asarray([payload["requested_duration_seconds"]], dtype=np.float64),
            requested_frame_rate_hz=np.asarray([payload["requested_frame_rate_hz"]], dtype=np.float64),
            requested_scope_sample_rate_hz=np.asarray([payload["requested_scope_sample_rate_hz"]], dtype=np.float64),
            zero_on_start=np.asarray([payload["zero_on_start"]], dtype=np.bool_),
            stopped_early=np.asarray([payload["stopped_early"]], dtype=np.bool_),
        )
        self._set_acquisition_progress(1.0, f"Saved: {os.path.basename(file_path)}")

    def toggle_preview(self):
        if self.Andor.is_capturing:
            self.Andor.stop_capture()
            self.started = False
            self.preview_zero_reference_pending = False
            self._update_preview_button_state()

        else:
            # Reset the camera feed texture size
            self.started = True
            self.camera_feed.reset_texture()
            self.camera_feed.rebuild_roi_traces()
            self.preview_zero_reference_pending = int(getattr(self.Andor, "zero_version", 0)) <= 0
            self.Andor.start_capture_continuous()
            self._update_preview_button_state()

    def setprop(self, prop, setting):
        setattr(self.camera, prop, dpg.get_value(setting))


    def render(self):
        self.camera_feed.render()

        if self.started and self.preview_zero_reference_pending and self.Andor.frameIdx > 0:
            self.preview_zero_reference_pending = not self.camera_feed.ensure_zero_reference_from_latest_frame()

        self._apply_pending_acquisition_result()

        if self.acquisition_in_progress and self.acquisition_started_at is not None:
            elapsed_seconds = max(0.0, time.perf_counter() - self.acquisition_started_at)
            camera_progress = min(1.0, self.Andor.frameIdx / max(self.acquisition_target_frames, 1))
            scope_controller = self._get_scope_controller()
            scope_sample_count = len(scope_controller.driver.get_timestamps()) if scope_controller is not None else 0
            scope_progress = min(1.0, scope_sample_count / max(self.acquisition_scope_target_samples, 1))
            progress_value = min(camera_progress, scope_progress)
            overlay = f"{self.Andor.frameIdx}/{self.acquisition_target_frames} frames | {scope_sample_count}/{self.acquisition_scope_target_samples} scope samples | {elapsed_seconds:0.1f}s"
            self._set_acquisition_progress(progress_value, overlay)

        # Disable the button if we are capturing something unrelated to experiment
        if self.Andor.is_capturing and not self.started:            
            dpg.configure_item(self.start_button_id, enabled=False)
        elif not self.Andor.is_capturing and not self.started:
            dpg.configure_item(self.start_button_id, enabled=True)

        # If capturing at all, disable the settings
        for setting in self.settings:
            if self.Andor.is_capturing:
                dpg.configure_item(setting, enabled=False)
                dpg.bind_item_theme(setting, read_only_theme)
            else:
                dpg.configure_item(setting, enabled=True)
                dpg.bind_item_theme(setting, None)

        acquisition_inputs = (
            self.acquisition_duration_input_id,
            self.acquisition_frame_rate_input_id,
            self.acquisition_scope_rate_input_id,
            self.acquisition_zero_on_start_checkbox_id,
            self.acquisition_set_awg_on_start_checkbox_id,
            self.acquisition_awg_waveform_combo_id,
            self.acquisition_awg_dc_offset_input_id,
            self.acquisition_awg_frequency_input_id,
            self.acquisition_awg_amplitude_input_id,
            self.acquisition_awg_periodic_offset_input_id,
            self.acquisition_awg_start_after_input_id,
        )
        for item_id in acquisition_inputs:
            dpg.configure_item(item_id, enabled=not self.acquisition_in_progress)

        self._update_preview_button_state()
        self._update_acquisition_button_state()
        self._update_acquisition_awg_visibility()

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "exposure_time": float(dpg.get_value(self.settings_exposure_time)),
                "trigger_mode": str(dpg.get_value(self.settings_trigger_mode)),
                "pixel_binning": str(dpg.get_value(self.settings_pixel_binning)),
                "image_width": int(dpg.get_value(self.settings_image_width)),
                "image_height": int(dpg.get_value(self.settings_image_height)),
                "image_left": int(dpg.get_value(self.settings_image_left)),
                "image_top": int(dpg.get_value(self.settings_image_top)),
                "acquisition_duration_seconds": float(dpg.get_value(self.acquisition_duration_input_id)),
                "acquisition_frame_rate_hz": float(dpg.get_value(self.acquisition_frame_rate_input_id)),
                "acquisition_scope_sample_rate_hz": float(dpg.get_value(self.acquisition_scope_rate_input_id)),
                "acquisition_zero_on_start": bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id)),
                "acquisition_set_awg_on_start": bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id)),
                "acquisition_awg_waveform": str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower(),
                "acquisition_awg_dc_offset_volts": float(dpg.get_value(self.acquisition_awg_dc_offset_input_id)),
                "acquisition_awg_frequency_hz": float(dpg.get_value(self.acquisition_awg_frequency_input_id)),
                "acquisition_awg_amplitude_vpp_volts": float(dpg.get_value(self.acquisition_awg_amplitude_input_id)),
                "acquisition_awg_periodic_offset_volts": float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id)),
                "acquisition_awg_start_after_seconds": float(dpg.get_value(self.acquisition_awg_start_after_input_id)),
            },
        )
        if hasattr(self.camera_feed, "SaveState"):
            self.camera_feed.SaveState()

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if state:
            apply_window_state(self.window_id, state.get("window"))

            property_map = (
                ("exposure_time", "ExposureTime", self.settings_exposure_time),
                ("trigger_mode", "TriggerMode", self.settings_trigger_mode),
                ("pixel_binning", "AOIBinning", self.settings_pixel_binning),
                ("image_width", "AOIWidth", self.settings_image_width),
                ("image_height", "AOIHeight", self.settings_image_height),
                ("image_left", "AOILeft", self.settings_image_left),
                ("image_top", "AOITop", self.settings_image_top),
            )
            for state_key, camera_property, widget_id in property_map:
                if state_key not in state:
                    continue
                dpg.set_value(widget_id, state[state_key])
                setattr(self.camera, camera_property, dpg.get_value(widget_id))

            if "acquisition_duration_seconds" in state:
                dpg.set_value(self.acquisition_duration_input_id, float(state["acquisition_duration_seconds"]))
            if "acquisition_frame_rate_hz" in state:
                dpg.set_value(self.acquisition_frame_rate_input_id, float(state["acquisition_frame_rate_hz"]))
            if "acquisition_scope_sample_rate_hz" in state:
                dpg.set_value(self.acquisition_scope_rate_input_id, float(state["acquisition_scope_sample_rate_hz"]))
            if "acquisition_zero_on_start" in state:
                dpg.set_value(self.acquisition_zero_on_start_checkbox_id, bool(state["acquisition_zero_on_start"]))
            if "acquisition_set_awg_on_start" in state:
                dpg.set_value(self.acquisition_set_awg_on_start_checkbox_id, bool(state["acquisition_set_awg_on_start"]))
            if "acquisition_awg_waveform" in state:
                dpg.set_value(self.acquisition_awg_waveform_combo_id, str(state["acquisition_awg_waveform"]).strip().title())
            if "acquisition_awg_dc_offset_volts" in state:
                dpg.set_value(self.acquisition_awg_dc_offset_input_id, float(state["acquisition_awg_dc_offset_volts"]))
            if "acquisition_awg_frequency_hz" in state:
                dpg.set_value(self.acquisition_awg_frequency_input_id, float(state["acquisition_awg_frequency_hz"]))
            if "acquisition_awg_amplitude_vpp_volts" in state:
                dpg.set_value(self.acquisition_awg_amplitude_input_id, float(state["acquisition_awg_amplitude_vpp_volts"]))
            if "acquisition_awg_periodic_offset_volts" in state:
                dpg.set_value(self.acquisition_awg_periodic_offset_input_id, float(state["acquisition_awg_periodic_offset_volts"]))
            if "acquisition_awg_start_after_seconds" in state:
                dpg.set_value(self.acquisition_awg_start_after_input_id, float(state["acquisition_awg_start_after_seconds"]))

            self.acquisition_duration_seconds = float(dpg.get_value(self.acquisition_duration_input_id))
            self.acquisition_frame_rate_hz = float(dpg.get_value(self.acquisition_frame_rate_input_id))
            self.acquisition_scope_sample_rate_hz = float(dpg.get_value(self.acquisition_scope_rate_input_id))
            self.acquisition_zero_on_start = bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id))
            self.acquisition_set_awg_on_start = bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id))
            self.acquisition_awg_waveform = str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower()
            self.acquisition_awg_dc_offset_volts = float(dpg.get_value(self.acquisition_awg_dc_offset_input_id))
            self.acquisition_awg_frequency_hz = float(dpg.get_value(self.acquisition_awg_frequency_input_id))
            self.acquisition_awg_amplitude_vpp_volts = float(dpg.get_value(self.acquisition_awg_amplitude_input_id))
            self.acquisition_awg_periodic_offset_volts = float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id))
            self.acquisition_awg_start_after_seconds = float(dpg.get_value(self.acquisition_awg_start_after_input_id))
            self._update_acquisition_awg_visibility()

        if hasattr(self.camera_feed, "LoadState"):
            self.camera_feed.LoadState()