import ctypes
from collections import deque
import os  
import shutil
import tempfile
import threading
import time
import zipfile
import numpy as np
import dearpygui.dearpygui as dpg
from Drivers.Andor import Andor
from Drivers.PicoScope import CHANNEL_NAMES, SUPPORTED_AWG_WAVEFORMS
from Utils.StorageDTypes import get_raw_storage_bytes
from Utils import diskspeed
from Windows.SubWindows.AcquisitionPreviewWindow import AcquisitionPreviewWindow
from Windows.SubWindows.CameraFeed import CameraFeedWindow
from Windows.SubWindows.Oscilloscope import OscilloscopeWindow
from Windows.SubWindows.ZAxisControlsWindow import ZAxisControlsWindow
from Utils.state_persistence import apply_item_open_states, apply_window_state, capture_item_open_states, capture_window_state, load_state_file, save_state_file
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
        self.calculate_frame_mean = True
        self.auto_scope_freq = False
        self.max_exposure = False
        self.acquisition_storage_dtype_name = "16"
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
        self.spool_to_disk_enabled = False
        self.spool_to_disk_directory = os.path.join(os.getcwd(), "Experiments")
        self.spool_to_disk_filename = "acquisition"
        self.spool_write_scope = True
        self.spool_write_power = False
        self._acquisition_thread = None
        self._acquisition_awg_thread = None
        self._acquisition_awg_stop_event = None
        self._completed_acquisition_payload = None
        self._pending_acquisition_result = None
        self._pending_save_result = None
        self._acquisition_scope_fallback_snapshot = None
        self._acquisition_settings_snapshot = None
        self._acquisition_lock = threading.Lock()
        self._save_thread = None
        self._save_in_progress = False
        self._save_progress_value = 0.0
        self._save_progress_display_value = 0.0
        self._save_progress_segment_start_value = 0.0
        self._save_progress_segment_end_value = 0.0
        self._save_progress_segment_key = None
        self._save_progress_overlay = "Save"
        self._save_progress_segment_started_at = time.perf_counter()
        self._acquisition_preview_window = None
        self._acquisition_scope_mean_thread = None
        self._acquisition_scope_mean_stop_event = None
        self._acquisition_scope_mean_lock = threading.Lock()
        self._acquisition_scope_pending_frame_timestamps = deque()
        self._acquisition_scope_mean_time_origin = None
        self._acquisition_scope_mean_scope_origin = 0.0
        self._scope_frame_mean_runtime = {
            "calculate_frame_mean": True,
            "frame_period_seconds": 0.0,
            "sample_period_seconds": 0.0,
            "samples_per_frame": 1.0,
            "next_center_sample_index": 0.0,
        }
        self._preview_scope_registered_frame_count = 0
        self._preview_scope_started_collection = False
        self.preview_zero_reference_pending = False
        self.last_save_directory = os.path.join(os.getcwd(), "Experiments")
        self.aoi_auto_center_enabled = False
        self.preview_max_frames = int(getattr(Andor, "max_acquisitions", 200))
        self._storage_devices = []
        self._drive_write_speed_cache = {}
        self._drive_write_speed_errors = {}
        self._hardware_requirements_signature = None
        self.section_node_ids = {}
        self._footer_padding = 8
        self.z_axis_controls = ZAxisControlsWindow()
        self.frame_scope_window = None
        self._last_frame_scope_render_frame_index = -1
        self._last_frame_scope_render_mean_count = -1
        self._frame_scope_render_snapshot = None

        with dpg.window(
            label                = "Camera Controls",
            tag                  = "#CameraControls",
            width                = 300,
            height               = 620,
            pos                  = (625, 10 ),
            no_scrollbar         = False,
            no_resize            = False,
            no_scroll_with_mouse = False,
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
            self.acquisition_storage_dtype_name = self.Andor.storage_dtype_name
            self.cooler_supported = bool(
                self.Andor.supports_sensor_cooling() or self.Andor.get_sensor_temperature_c() is not None
            )
            self.temperature_setpoint_supported = bool(self.Andor.supports_temperature_setpoint())
            self.temperature_setpoint_min_c = -10.0
            self.temperature_setpoint_max_c = 10.0
            self.temperature_setpoint_options = self._get_temperature_setpoint_items()
            self.temperature_setpoint_value = self.temperature_setpoint_options[0] if self.temperature_setpoint_options else ""

            # Set up the Preview Window
            self.camera_feed   = CameraFeedWindow(
                parent      = self.window_id,
                Andor       = self.Andor
            )
            self.frame_scope_window = OscilloscopeWindow(
                [self._make_frame_scope_trace_getter(channel_name) for channel_name in CHANNEL_NAMES],
                title="Frame Scope Means",
                channel_headers=[f"Channel {channel_name}" for channel_name in CHANNEL_NAMES],
                width=880,
                height=320,
                pos=(625, 1255),
                state_name="FrameScopeWindow",
                tag="#FrameScope",
            )

            with dpg.theme() as self.stop_button_theme:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, [70, 70, 70])
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, [140, 30, 30])
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, [110, 20, 20])

            with dpg.theme() as self.hardware_readout_theme:
                with dpg.theme_component(dpg.mvInputText):
                    dpg.add_theme_color(dpg.mvThemeCol_Text, [215, 215, 215])
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, [52, 52, 52])
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, [52, 52, 52])
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, [52, 52, 52])
                    dpg.add_theme_color(dpg.mvThemeCol_Border, [90, 90, 90])

            with dpg.child_window(border=False, width=-1, height=400, no_scrollbar=False) as self.content_area_id:
                with dpg.tree_node(label="Camera Settings", default_open=True, span_full_width=True) as camera_settings_node_id:
                    self.section_node_ids["camera_settings"] = camera_settings_node_id
                    self.settings_frame_rate = dpg.add_input_float(
                        label           = "FPS",
                        width           = -110,
                        default_value   = self.acquisition_frame_rate_hz,
                        min_value       = 0.1,
                        min_clamped     = True,
                        step            = 1.0,
                        format          = "%.0f",
                        callback        = lambda: self.setprop("FrameRate", self.settings_frame_rate)
                    )

                    self.acquisition_frame_rate_input_id = self.settings_frame_rate

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

                    self.settings_max_exposure_checkbox_id = dpg.add_checkbox(
                        label="Max Exposure",
                        default_value=self.max_exposure,
                        callback=self._on_max_exposure_changed,
                    )

                    self.settings_trigger_mode = dpg.add_combo(
                        label           = "Trigger Mode",
                        width           = -110,
                        items           = cam.options_TriggerMode,
                        default_value   = cam.TriggerMode,
                        callback        = lambda: self.setprop("TriggerMode", self.settings_trigger_mode)
                    )

                    self.settings_cooler_checkbox_id = dpg.add_checkbox(
                        label="Cooler",
                        default_value=bool(self.Andor.get_sensor_cooling_enabled() or False),
                        callback=self._on_camera_cooler_changed,
                        enabled=self.cooler_supported,
                    )

                    self.settings_temp_set_input_id = dpg.add_combo(
                        label="Temp Set",
                        width=-110,
                        items=self.temperature_setpoint_options,
                        default_value=self.temperature_setpoint_value,
                        callback=self._on_camera_temp_set_changed,
                        enabled=self.temperature_setpoint_supported,
                    )

                    self.cooler_temperature_input_id = dpg.add_input_float(
                        label="Temperature",
                        width=-110,
                        default_value=float(self.Andor.get_sensor_temperature_c() or 0.0),
                        format="%.1f C",
                        enabled=False,
                    )

                dpg.add_separator()
                with dpg.tree_node(label="Frame Settings", default_open=True, span_full_width=True) as frame_settings_node_id:
                    self.section_node_ids["frame_settings"] = frame_settings_node_id
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

                    self.settings_aoi_auto_center_checkbox_id = dpg.add_checkbox(
                        label="Auto Center",
                        default_value=self.aoi_auto_center_enabled,
                        callback=self._on_aoi_auto_center_changed,
                    )

                dpg.add_separator()
                with dpg.tree_node(label="Preview Settings", default_open=True, span_full_width=True) as preview_settings_node_id:
                    self.section_node_ids["preview_settings"] = preview_settings_node_id
                    self.settings_preview_max_frames = dpg.add_input_int(
                        label="Max Frames",
                        width=-110,
                        default_value=self.preview_max_frames,
                        min_value=1,
                        min_clamped=True,
                        step=10,
                        callback=self._on_preview_max_frames_changed,
                    )

                    self._refresh_aoi_controls_from_camera()

                dpg.add_separator()
                with dpg.tree_node(label="Acquisition Settings", default_open=True, span_full_width=True) as acquisition_settings_node_id:
                    self.section_node_ids["acquisition_settings"] = acquisition_settings_node_id
                    self.acquisition_duration_input_id = dpg.add_input_float(
                        label="Seconds",
                        width=-110,
                        default_value=self.acquisition_duration_seconds,
                        min_value=0.01,
                        min_clamped=True,
                        step=0.1,
                    )

                    self.acquisition_zero_on_start_checkbox_id = dpg.add_checkbox(
                        label="Zero on Start",
                        default_value=self.acquisition_zero_on_start,
                    )

                    self.calculate_frame_mean_checkbox_id = dpg.add_checkbox(
                        label="Calculate Frame Mean",
                        default_value=self.calculate_frame_mean,
                    )

                    self.auto_scope_freq_checkbox_id = dpg.add_checkbox(
                        label="Auto Scope Freq",
                        default_value=self.auto_scope_freq,
                        callback=self._on_auto_scope_freq_changed,
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
                                label="AWG Delay (s)",
                                width=-110,
                                default_value=self.acquisition_awg_start_after_seconds,
                                min_value=0.0,
                                min_clamped=True,
                                step=0.1,
                                format="%.2f s",
                            )

                            self.acquisition_scope_rate_input_id = dpg.add_input_float(
                                label="Scope Hz",
                                width=-110,
                                default_value=self.acquisition_scope_sample_rate_hz,
                                min_value=0.1,
                                min_clamped=True,
                                step=100.0,
                            )
                    
                dpg.add_separator()
                with dpg.tree_node(label="Hardware Reqs", default_open=True, span_full_width=True) as hardware_reqs_node_id:
                    self.section_node_ids["hardware_reqs"] = hardware_reqs_node_id
                    self.hardware_drive_combo_id = dpg.add_combo(
                        label="Drive",
                        width=-110,
                        items=[],
                        default_value="",
                        callback=self._on_storage_device_changed,
                    )

                    self.hardware_ram_value_id = dpg.add_input_text(
                        label="RAM (GB)",
                        width=-110,
                        default_value="Calculating...",
                        readonly=True,
                    )
                    dpg.bind_item_theme(self.hardware_ram_value_id, self.hardware_readout_theme)

                    self.hardware_disk_value_id = dpg.add_input_text(
                        label="Disk Space (GB)",
                        width=-110,
                        default_value="Calculating...",
                        readonly=True,
                    )
                    dpg.bind_item_theme(self.hardware_disk_value_id, self.hardware_readout_theme)

                    self.hardware_bitrate_value_id = dpg.add_input_text(
                        label="Bitrate (Mbps)",
                        width=-110,
                        default_value="Calculating...",
                        readonly=True,
                    )
                    dpg.bind_item_theme(self.hardware_bitrate_value_id, self.hardware_readout_theme)

                    self.hardware_spooling_value_id = dpg.add_input_text(
                        label="Spool to Disk",
                        width=-110,
                        default_value="Unknown",
                        readonly=True,
                    )
                    dpg.bind_item_theme(self.hardware_spooling_value_id, self.hardware_readout_theme)

                dpg.add_separator()
                with dpg.tree_node(label="Spool to Disk", default_open=True, span_full_width=True) as spool_to_disk_node_id:
                    self.section_node_ids["spool_to_disk"] = spool_to_disk_node_id
                    self.spool_to_disk_enabled_checkbox_id = dpg.add_checkbox(
                        label="Enable Spooling",
                        default_value=self.spool_to_disk_enabled,
                        callback=self._on_spool_to_disk_changed,
                    )

                    with dpg.group(horizontal=True) as self.spool_to_disk_location_row_id:
                        self.spool_to_disk_directory_input_id = dpg.add_input_text(
                            label="Location",
                            width=-145,
                            default_value=self.spool_to_disk_directory,
                            readonly=True,
                        )
                        dpg.bind_item_theme(self.spool_to_disk_directory_input_id, self.hardware_readout_theme)
                        self.spool_to_disk_browse_button_id = dpg.add_button(
                            label="Browse",
                            width=-1,
                            callback=self._show_spool_location_dialog,
                        )

                    self.spool_to_disk_filename_input_id = dpg.add_input_text(
                        label="Filename",
                        width=-110,
                        default_value=self.spool_to_disk_filename,
                    )

                    self.spool_write_scope_checkbox_id = dpg.add_checkbox(
                        label="Write Scope",
                        default_value=self.spool_write_scope,
                    )

                    self.spool_write_power_checkbox_id = dpg.add_checkbox(
                        label="Write Power",
                        default_value=self.spool_write_power,
                    )

                dpg.add_separator()

            with dpg.group() as self.bottom_controls_group_id:
                dpg.add_separator()
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

                self.start_button_id = dpg.add_button(
                    label="Start Preview",
                    width=-1,
                    height=36,
                    callback=self.toggle_preview,
                    tag="start_camera_button"
                )

                self.save_button_id = dpg.add_button(
                    label="Save",
                    width=-1,
                    height=36,
                    enabled=False,
                    callback=self._show_save_dialog,
                )

                self.save_progress_bar_id = dpg.add_progress_bar(
                    width=-1,
                    height=36,
                    default_value=0.0,
                    overlay="Saving... 0%",
                    show=False,
                )

                self.open_button_id = dpg.add_button(
                    label="Open",
                    width=-1,
                    height=36,
                    callback=self._show_open_dialog,
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

            with dpg.file_dialog(
                directory_selector=False,
                show=False,
                callback=self._on_open_dialog_selected,
                width=700,
                height=400,
                modal=True,
            ) as self.open_dialog_id:
                dpg.add_file_extension(".npz", color=(0, 255, 0, 255))

            with dpg.file_dialog(
                directory_selector=True,
                show=False,
                callback=self._on_spool_location_selected,
                width=700,
                height=400,
                modal=True,
            ) as self.spool_location_dialog_id:
                pass

        self._update_preview_button_state()
        self._update_acquisition_button_state()
        self._update_acquisition_awg_visibility()
        self._update_spool_to_disk_controls()
        self._refresh_storage_devices()
        self._set_acquisition_progress(0.0, "Idle")
        self._set_save_progress(0.0, "Save")
        self._refresh_hardware_requirements(force=True)
        self._sync_camera_cooler_readout()

    @property
    def settings(self):
        return [getattr(self, attr) for attr in vars(self) if attr.startswith('settings_')]

    def _get_scope_controller(self):
        for obj in class_objects:
            if obj.__class__.__name__ == "PicoScopeControl":
                return obj
        return None

    def _get_scope_driver(self):
        scope_controller = self._get_scope_controller()
        if scope_controller is None or not scope_controller.driver.is_open:
            return None
        return scope_controller.driver

    def _get_auto_scope_settings(self, frame_rate_hz, scope_driver):
        frame_rate_hz = max(float(frame_rate_hz), 1e-12)
        history_seconds = max(0.001, 0.5 / frame_rate_hz)
        if scope_driver is None:
            max_sample_rate_hz = float(self.acquisition_scope_sample_rate_hz)
        else:
            max_sample_rate_hz = float(scope_driver.get_max_sample_rate_hz())
        sample_rate_hz = max(1.0, 0.001 * max_sample_rate_hz)
        return {
            "history_seconds": history_seconds,
            "sample_rate_hz": sample_rate_hz,
        }

    def _apply_auto_scope_frequency_settings(self, frame_rate_hz):
        if not bool(dpg.get_value(self.auto_scope_freq_checkbox_id)):
            return None

        scope_controller = self._get_scope_controller()
        scope_driver = self._get_scope_driver()
        auto_settings = self._get_auto_scope_settings(frame_rate_hz, scope_driver)

        self.acquisition_scope_sample_rate_hz = float(auto_settings["sample_rate_hz"])
        dpg.set_value(self.acquisition_scope_rate_input_id, self.acquisition_scope_sample_rate_hz)

        if scope_controller is not None:
            dpg.set_value(scope_controller.seconds_input_id, float(auto_settings["history_seconds"]))
            dpg.set_value(scope_controller.sample_rate_input_id, float(auto_settings["sample_rate_hz"]))

        return auto_settings

    def _apply_preview_max_frames(self, frame_count):
        frame_count = max(1, int(frame_count))
        self.preview_max_frames = frame_count
        self.Andor.set_preview_max_frames(frame_count)
        self.camera_feed.set_roi_history_capacity(frame_count)
        if self.started and not self.acquisition_in_progress:
            scope_settings = self._get_scope_capture_settings()
            self.Andor.configure_scope_frame_mean_buffers(scope_settings["enabled_channels"], frame_count)

    def _get_scope_channel_color(self, channel_name):
        scope_controller = self._get_scope_controller()
        if scope_controller is not None:
            for panel in getattr(scope_controller, "channel_panels", []):
                if panel.get("source_channel") == channel_name:
                    return list(panel.get("color", [255, 255, 255, 255]))
        return [255, 255, 255, 255]

    def _make_frame_scope_trace_getter(self, channel_name):
        return lambda channel_name=channel_name: self._get_frame_scope_trace(channel_name)

    def _get_frame_scope_trace(self, channel_name):
        scope_controller = self._get_scope_controller()
        if scope_controller is not None:
            matching_panel = next(
                (panel for panel in getattr(scope_controller, "channel_panels", []) if panel.get("source_channel") == channel_name),
                None,
            )
            if matching_panel is not None and not bool(matching_panel.get("enabled")):
                return None

        snapshot = self._frame_scope_render_snapshot
        if snapshot is None:
            snapshot = self.Andor.get_scope_frame_mean_snapshot()
        scope_buffers = snapshot.get("scope_frame_mean_buffers", {})
        raw_samples = np.asarray(scope_buffers.get(channel_name, []), dtype=np.float32)
        sample_count = int(raw_samples.size)

        if sample_count <= 0:
            x_values = np.zeros((0,), dtype=np.float64)
            y_values = np.zeros((0,), dtype=np.float32)
        else:
            x_values = self.Andor.get_estimated_time_axis_values(sample_count)
            y_values = raw_samples.astype(np.float32, copy=False)

        abs_last_x = float(x_values[-1]) if x_values.size > 0 else 0.0
        return {
            "panel_id": channel_name,
            "label": f"Channel {channel_name}",
            "color": self._get_scope_channel_color(channel_name),
            "x_values": x_values,
            "y_values": y_values,
            "abs_last_x": abs_last_x,
        }

    def _on_preview_max_frames_changed(self, sender=None, app_data=None, user_data=None):
        self._apply_preview_max_frames(dpg.get_value(self.settings_preview_max_frames))
        self._refresh_hardware_requirements(force=True)

    def _on_acquisition_frame_rate_changed(self, sender=None, app_data=None, user_data=None):
        self.acquisition_frame_rate_hz = max(0.1, float(app_data or dpg.get_value(self.acquisition_frame_rate_input_id)))
        self._apply_auto_scope_frequency_settings(self.acquisition_frame_rate_hz)
        self._refresh_hardware_requirements(force=True)

    def _on_auto_scope_freq_changed(self, sender=None, app_data=None, user_data=None):
        self.auto_scope_freq = bool(app_data)
        self._apply_auto_scope_frequency_settings(float(dpg.get_value(self.acquisition_frame_rate_input_id)))
        self._refresh_hardware_requirements(force=True)

    def _clamp_temperature_setpoint_c(self, temperature_c):
        return float(np.clip(float(temperature_c), self.temperature_setpoint_min_c, self.temperature_setpoint_max_c))

    def _get_temperature_setpoint_items(self):
        filtered_items = []
        for option in self.Andor.get_temperature_setpoint_options():
            try:
                option_value_c = float(option)
            except (TypeError, ValueError):
                continue
            if self.temperature_setpoint_min_c <= option_value_c <= self.temperature_setpoint_max_c:
                filtered_items.append(str(option))
        return filtered_items

    def _sync_camera_cooler_readout(self):
        cooler_enabled = self.Andor.get_sensor_cooling_enabled()
        if cooler_enabled is not None:
            self._cooler_enabled_value = bool(cooler_enabled)

        temperature_c = self.Andor.get_sensor_temperature_c()
        if temperature_c is not None:
            self._cooler_temperature_c = float(temperature_c)

    def _on_camera_cooler_changed(self, sender, app_data, user_data=None):
        self.Andor.set_sensor_cooling_enabled(bool(app_data))
        self._cooler_enabled_value = bool(app_data)

    def _on_camera_temp_set_changed(self, sender, app_data, user_data=None):
        requested_option = str(app_data)
        if requested_option not in self.temperature_setpoint_options:
            return
        self.temperature_setpoint_value = requested_option
        dpg.set_value(self.settings_temp_set_input_id, requested_option)
        self.Andor.set_temperature_setpoint_option(requested_option)

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
        self._refresh_hardware_requirements(force=True)

    def _on_acquisition_awg_waveform_changed(self, sender, app_data, user_data=None):
        self.acquisition_awg_waveform = str(app_data).strip().lower()
        self._refresh_hardware_requirements(force=True)

    def _update_spool_to_disk_controls(self):
        controls_enabled = bool(dpg.get_value(self.spool_to_disk_enabled_checkbox_id)) and not self.acquisition_in_progress
        dpg.configure_item(self.spool_to_disk_directory_input_id, enabled=controls_enabled)
        dpg.configure_item(self.spool_to_disk_browse_button_id, enabled=controls_enabled)
        dpg.configure_item(self.spool_to_disk_filename_input_id, enabled=controls_enabled)
        dpg.configure_item(self.spool_write_scope_checkbox_id, enabled=controls_enabled)
        dpg.configure_item(self.spool_write_power_checkbox_id, enabled=controls_enabled)

    def _on_spool_to_disk_changed(self, sender=None, app_data=None, user_data=None):
        self.spool_to_disk_enabled = bool(app_data)

    def _show_spool_location_dialog(self, sender=None, app_data=None, user_data=None):
        dpg.show_item(self.spool_location_dialog_id)

    def _on_spool_location_selected(self, sender, app_data, user_data=None):
        selected_path = str(app_data.get("file_path_name") or "").strip()
        if not selected_path:
            return
        self.spool_to_disk_directory = selected_path
        dpg.set_value(self.spool_to_disk_directory_input_id, selected_path)

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

    def _on_camera_storage_dtype_changed(self, sender, app_data, user_data=None):
        self.acquisition_storage_dtype_name = self.Andor.storage_dtype_name
        self.Andor.frame_ready_event.set()
        self.camera_feed.rebuild_roi_traces()
        self._refresh_hardware_requirements(force=True)

    def _get_system_memory_available_bytes(self):
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
            return None

        if hasattr(os, "sysconf"):
            if "SC_PAGE_SIZE" in os.sysconf_names and "SC_AVPHYS_PAGES" in os.sysconf_names:
                return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES"))
        return None

    def _get_selected_drive_free_bytes(self):
        selected_device = self._get_selected_storage_device()
        if selected_device is None:
            return None
        usage = shutil.disk_usage(selected_device["root"])
        return int(usage.free)

    def _set_requirement_bar(self, item_id, value, maximum, formatter):
        maximum = float(maximum)
        value = float(value)
        ratio = 0.0 if maximum <= 0.0 else min(value / maximum, 1.0)
        overlay = f"{formatter(value)} / {formatter(maximum)}"
        dpg.set_value(item_id, ratio)
        dpg.configure_item(item_id, overlay=overlay)

    def _show_requirement_bar(self, bar_id, value_id, show_bar, fallback_text=None):
        dpg.configure_item(bar_id, show=show_bar)
        dpg.configure_item(value_id, show=not show_bar)
        if not show_bar and fallback_text is not None:
            dpg.set_value(value_id, fallback_text)

    def _enumerate_storage_devices(self):
        if os.name != "nt":
            root_path = os.path.abspath(os.sep)
            return [{"label": root_path, "root": root_path, "type": "Filesystem"}]

        drive_types = {
            2: "Removable",
            3: "Fixed",
            4: "Network",
            6: "RAM Disk",
        }
        drive_mask = int(ctypes.windll.kernel32.GetLogicalDrives())
        devices = []
        for index in range(26):
            if not (drive_mask & (1 << index)):
                continue
            drive_root = f"{chr(ord('A') + index)}:\\"
            drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_root)))
            if drive_type not in drive_types:
                continue
            devices.append(
                {
                    "label": f"{drive_root} ({drive_types[drive_type]})",
                    "root": drive_root,
                    "type": drive_types[drive_type],
                }
            )
        return devices

    def _refresh_storage_devices(self, preferred_label=None):
        self._storage_devices = self._enumerate_storage_devices()
        item_labels = [device["label"] for device in self._storage_devices]
        dpg.configure_item(self.hardware_drive_combo_id, items=item_labels)

        selected_label = preferred_label or dpg.get_value(self.hardware_drive_combo_id)
        if selected_label not in item_labels:
            selected_label = item_labels[0] if item_labels else ""
        dpg.set_value(self.hardware_drive_combo_id, selected_label)

        if selected_label:
            self._benchmark_storage_device_if_needed(selected_label)

    def _get_selected_storage_device(self):
        selected_label = str(dpg.get_value(self.hardware_drive_combo_id) or "").strip()
        for device in self._storage_devices:
            if device["label"] == selected_label:
                return device
        return None

    def _benchmark_storage_device_if_needed(self, selected_label):
        if not selected_label:
            return

        selected_device = next((device for device in self._storage_devices if device["label"] == selected_label), None)
        if selected_device is None:
            return

        if selected_label in self._drive_write_speed_cache or selected_label in self._drive_write_speed_errors:
            return

        try:
            self._drive_write_speed_cache[selected_label] = self._measure_drive_write_speed(selected_device["root"])
        except Exception as exc:
            self._drive_write_speed_errors[selected_label] = str(exc)

    def _measure_drive_write_speed(self, drive_root):
        return diskspeed.measure_write_speed(drive_root)

    def _on_storage_device_changed(self, sender=None, app_data=None, user_data=None):
        selected_label = str(app_data or dpg.get_value(self.hardware_drive_combo_id) or "").strip()
        if not selected_label:
            self._refresh_hardware_requirements(force=True)
            return

        self._benchmark_storage_device_if_needed(selected_label)
        self._refresh_hardware_requirements(force=True)

    def _format_bytes(self, byte_count):
        value = float(max(byte_count, 0.0))
        units = ("B", "KB", "MB", "GB", "TB")
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                return f"{value:0.2f} {unit}"
            value /= 1024.0
        return f"{value:0.2f} TB"

    def _format_bitrate(self, bits_per_second):
        value = float(max(bits_per_second, 0.0))
        if value >= 1_000_000_000.0:
            return value / 1_000_000_000.0, "Gbps"
        return value / 1_000_000.0, "Mbps"

    def _format_gigabytes(self, byte_count):
        value = float(max(byte_count, 0.0))
        if value >= 1024.0 ** 3:
            return value / (1024.0 ** 3), "GB"
        return value / (1024.0 ** 2), "MB"

    def _get_scope_capture_settings(self):
        scope_controller = self._get_scope_controller()
        enabled_channels = []
        if scope_controller is not None:
            enabled_channels = [
                panel["source_channel"]
                for panel in getattr(scope_controller, "channel_panels", [])
                if panel.get("enabled")
            ]
        return {
            "enabled_channels": enabled_channels,
        }

    def _get_scope_frame_mean_frame_period_seconds(self):
        if self.acquisition_in_progress and float(self.acquisition_frame_rate_hz) > 0.0:
            frame_rate_hz = float(self.acquisition_frame_rate_hz)
        else:
            frame_rate_hz = float(self.Andor.get_frame_rate())
        return 1.0 / max(frame_rate_hz, 1e-12)

    def _configure_scope_frame_mean_runtime(self, *, sample_rate_hz, frame_rate_hz, calculate_frame_mean):
        sample_rate_hz = max(float(sample_rate_hz), 1e-12)
        frame_rate_hz = max(float(frame_rate_hz), 1e-12)
        frame_period_seconds = 1.0 / frame_rate_hz
        sample_period_seconds = 1.0 / sample_rate_hz
        samples_per_frame = max(1.0, frame_period_seconds * sample_rate_hz)
        self._scope_frame_mean_runtime = {
            "calculate_frame_mean": bool(calculate_frame_mean),
            "frame_period_seconds": frame_period_seconds,
            "sample_period_seconds": sample_period_seconds,
            "samples_per_frame": samples_per_frame,
            "next_center_sample_index": 0.0,
        }

    def _get_acquisition_scope_buffer_seconds(self, acquisition_seconds=None):
        if acquisition_seconds is None:
            acquisition_seconds = float(dpg.get_value(self.acquisition_duration_input_id))
        return max(0.1, float(acquisition_seconds) * 2.0)

    def _queue_scope_frame_timestamps(self, timestamps):
        if not timestamps:
            return
        with self._acquisition_scope_mean_lock:
            for timestamp in timestamps:
                normalized_timestamp = float(timestamp)
                if self._acquisition_scope_mean_time_origin is None:
                    self._acquisition_scope_mean_time_origin = normalized_timestamp
                normalized_timestamp -= float(self._acquisition_scope_mean_time_origin)
                self._acquisition_scope_pending_frame_timestamps.append(normalized_timestamp)

    def _get_pending_scope_frame_count(self):
        with self._acquisition_scope_mean_lock:
            return len(self._acquisition_scope_pending_frame_timestamps)

    def _reduce_scope_buffer_into_frame_means(self, scope_driver, *, force=False):
        if scope_driver is None or self._get_pending_scope_frame_count() <= 0:
            return

        scope_channels = self.Andor.get_scope_frame_mean_channels()
        if not scope_channels:
            return

        scope_snapshot = scope_driver.get_buffer_snapshot(channel_names=scope_channels)
        scope_timestamps = np.asarray(scope_snapshot.get("timestamps", []), dtype=np.float64)
        scope_timestamps = scope_timestamps - float(self._acquisition_scope_mean_scope_origin)
        if scope_timestamps.size == 0:
            return

        runtime = dict(self._scope_frame_mean_runtime)
        calculate_frame_mean = bool(runtime.get("calculate_frame_mean", True))
        frame_period_seconds = float(runtime.get("frame_period_seconds") or self._get_scope_frame_mean_frame_period_seconds())
        actual_scope_rate_hz = float(scope_snapshot.get("actual_sample_rate_hz") or self.acquisition_scope_sample_rate_hz)
        sample_period_seconds = 1.0 / max(actual_scope_rate_hz, 1e-12)
        latest_scope_timestamp = float(scope_timestamps[-1])
        channel_arrays = {
            channel_name: np.asarray(scope_snapshot.get("channels", {}).get(channel_name, []), dtype=np.float32)
            for channel_name in scope_channels
        }

        frame_mean_payloads = []

        with self._acquisition_scope_mean_lock:
            while self._acquisition_scope_pending_frame_timestamps:
                frame_timestamp = float(self._acquisition_scope_pending_frame_timestamps[0])
                if not force and latest_scope_timestamp + (0.5 * sample_period_seconds) < frame_timestamp:
                    break

                self._acquisition_scope_pending_frame_timestamps.popleft()
                frame_start = frame_timestamp - frame_period_seconds
                frame_end = frame_timestamp
                frame_center_index = int(round(float(runtime.get("next_center_sample_index", 0.0))))
                if scope_timestamps.size > 0:
                    frame_center_index = int(np.clip(frame_center_index, 0, scope_timestamps.size - 1))
                runtime["next_center_sample_index"] = float(runtime.get("next_center_sample_index", 0.0)) + float(runtime.get("samples_per_frame", 1.0))

                in_frame_mask = None
                if calculate_frame_mean:
                    in_frame_mask = (scope_timestamps >= frame_start) & (scope_timestamps < frame_end)
                    if not np.any(in_frame_mask):
                        frame_center = frame_start + (0.5 * frame_period_seconds)
                        nearest_sample_index = int(np.argmin(np.abs(scope_timestamps - frame_center)))
                        in_frame_mask = np.zeros(scope_timestamps.shape, dtype=bool)
                        in_frame_mask[nearest_sample_index] = True

                frame_means = {}
                for channel_name in scope_channels:
                    raw_samples = channel_arrays.get(channel_name)
                    if raw_samples is None or raw_samples.size == 0:
                        frame_means[channel_name] = np.float16(0.0)
                        continue

                    if calculate_frame_mean:
                        window_samples = raw_samples[in_frame_mask]
                        if window_samples.size == 0:
                            frame_means[channel_name] = np.float16(0.0)
                            continue

                        voltage_samples = scope_driver.convert_samples_to_volts(channel_name, window_samples)
                        frame_means[channel_name] = np.float16(np.mean(voltage_samples, dtype=np.float64))
                    else:
                        center_sample = np.asarray([raw_samples[frame_center_index]], dtype=np.float32)
                        voltage_sample = scope_driver.convert_samples_to_volts(channel_name, center_sample)
                        frame_means[channel_name] = np.float16(float(voltage_sample[0]))

                frame_mean_payloads.append(frame_means)

        self._scope_frame_mean_runtime = runtime

        for frame_means in frame_mean_payloads:
            self.Andor.append_scope_frame_mean_values(frame_means)

    def _run_scope_mean_worker(self, scope_driver, stop_event):
        while not stop_event.is_set():
            self._reduce_scope_buffer_into_frame_means(scope_driver, force=False)
            stop_event.wait(0.01)

        self._reduce_scope_buffer_into_frame_means(scope_driver, force=True)

    def _stop_scope_mean_thread(self):
        if self._acquisition_scope_mean_stop_event is not None:
            self._acquisition_scope_mean_stop_event.set()
        if self._acquisition_scope_mean_thread is not None and self._acquisition_scope_mean_thread.is_alive():
            self._acquisition_scope_mean_thread.join(timeout=2.0)
        self._acquisition_scope_mean_thread = None
        self._acquisition_scope_mean_stop_event = None

    def _pad_remaining_scope_frame_means(self):
        with self._acquisition_scope_mean_lock:
            remaining_count = len(self._acquisition_scope_pending_frame_timestamps)
            self._acquisition_scope_pending_frame_timestamps.clear()
            self._acquisition_scope_mean_time_origin = None
        for _ in range(remaining_count):
            self.Andor.append_scope_frame_mean_values({})

    def _clear_pending_scope_frame_means(self):
        with self._acquisition_scope_mean_lock:
            self._acquisition_scope_pending_frame_timestamps.clear()
            self._acquisition_scope_mean_time_origin = None
        self._acquisition_scope_mean_scope_origin = 0.0

    def _start_scope_mean_thread(self, scope_driver):
        if scope_driver is None:
            return
        self._stop_scope_mean_thread()
        self._acquisition_scope_mean_stop_event = threading.Event()
        self._acquisition_scope_mean_thread = threading.Thread(
            target=self._run_scope_mean_worker,
            args=(scope_driver, self._acquisition_scope_mean_stop_event),
            daemon=True,
            name="ScopeFrameMeanWorker",
        )
        self._acquisition_scope_mean_thread.start()

    def _drain_preview_scope_frame_timestamps(self):
        if not self.started or self.acquisition_in_progress:
            return

        with self.Andor.frame_lock:
            if len(self.Andor.timestamps) > self._preview_scope_registered_frame_count:
                new_camera_timestamps = [
                    float(timestamp)
                    for timestamp in list(self.Andor.timestamps)[self._preview_scope_registered_frame_count:]
                ]
                self._preview_scope_registered_frame_count = len(self.Andor.timestamps)
            else:
                new_camera_timestamps = []

        if new_camera_timestamps:
            self._queue_scope_frame_timestamps(new_camera_timestamps)

    def _stop_preview_scope_means(self):
        self.Andor.set_scope_frame_mean_source(None, calculate_mean=bool(dpg.get_value(self.calculate_frame_mean_checkbox_id)))
        self._preview_scope_registered_frame_count = 0

        if self._preview_scope_started_collection:
            scope_controller = self._get_scope_controller()
            scope_driver = scope_controller.driver if scope_controller is not None else None
            if scope_driver is not None and scope_driver.is_collecting:
                try:
                    scope_driver.stop_collection()
                except Exception:
                    pass
        self._preview_scope_started_collection = False

    def _start_preview_scope_means(self):
        self._preview_scope_registered_frame_count = 0
        self._preview_scope_started_collection = False

        scope_controller = self._get_scope_controller()
        scope_driver = scope_controller.driver if scope_controller is not None and scope_controller.driver.is_open else None
        scope_settings = self._get_scope_capture_settings()
        preview_frame_rate_hz = float(self.Andor.get_frame_rate())
        auto_settings = self._apply_auto_scope_frequency_settings(preview_frame_rate_hz)

        self.Andor.configure_scope_frame_mean_buffers(scope_settings["enabled_channels"], self.preview_max_frames)
        self.Andor.set_scope_frame_mean_source(scope_driver, calculate_mean=bool(dpg.get_value(self.calculate_frame_mean_checkbox_id)))

        if scope_driver is None:
            return

        if scope_driver.is_collecting:
            scope_driver.stop_collection()
            self._preview_scope_started_collection = True

        if auto_settings is not None:
            scope_driver.set_sample_capture_rate(float(auto_settings["sample_rate_hz"]))
            scope_driver.set_history_seconds(float(auto_settings["history_seconds"]))

        if not scope_driver.is_collecting:
            scope_driver.clear_buffers()
            scope_driver.start_collection()
            self._preview_scope_started_collection = True

    def _get_hardware_requirements_signature(self):
        scope_settings = self._get_scope_capture_settings()
        return (
            float(dpg.get_value(self.acquisition_duration_input_id)),
            float(dpg.get_value(self.acquisition_frame_rate_input_id)),
            str(self.Andor.storage_dtype_name),
            bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id)),
            bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id)),
            str(dpg.get_value(self.acquisition_awg_waveform_combo_id)),
            float(dpg.get_value(self.acquisition_awg_start_after_input_id)),
            int(getattr(self.camera, "AOIWidth", 0)),
            int(getattr(self.camera, "AOIHeight", 0)),
            self.Andor.bit_depth,
            bool(getattr(self.Andor, "lp_filter_enabled", False)),
            tuple(scope_settings["enabled_channels"]),
            str(dpg.get_value(self.hardware_drive_combo_id) or ""),
        )

    def _build_acquisition_save_arrays(self, camera, scope, payload):
        channel_payload = camera.get("scope_frame_mean_buffers", {})
        settings_payload = payload.get("settings", {}) or {}

        save_arrays = {
            "camera_acquisitions": np.asarray(camera["acquisitions"]),
            "camera_timestamps": np.asarray(camera["timestamps"], dtype=np.float64),
        }

        for channel_name, samples in sorted(channel_payload.items()):
            save_arrays[f"scope_channel_{channel_name}"] = np.asarray(samples, dtype=np.float16)

        for setting_name, value in sorted(settings_payload.items()):
            save_key = f"settings_{setting_name}"
            if isinstance(value, (bool, np.bool_)):
                save_arrays[save_key] = np.asarray(value, dtype=np.bool_)
            elif isinstance(value, (int, np.integer)):
                save_arrays[save_key] = np.asarray(value, dtype=np.int64)
            elif isinstance(value, (float, np.floating)):
                save_arrays[save_key] = np.asarray(value, dtype=np.float64)
            else:
                save_arrays[save_key] = np.asarray(str(value))

        return save_arrays

    def _estimate_acquisition_requirements(self):
        acquisition_seconds = max(0.01, float(dpg.get_value(self.acquisition_duration_input_id)))
        acquisition_fps = max(0.1, float(dpg.get_value(self.acquisition_frame_rate_input_id)))
        scope_sample_rate = max(0.1, float(dpg.get_value(self.acquisition_scope_rate_input_id)))
        scope_buffer_seconds = self._get_acquisition_scope_buffer_seconds(acquisition_seconds)
        target_frames = max(1, int(round(acquisition_seconds * acquisition_fps)))
        paired_scope_samples = target_frames
        scope_buffer_samples = max(1, int(round(scope_buffer_seconds * scope_sample_rate)))

        frame_height = max(1, int(getattr(self.camera, "AOIHeight", 1)))
        frame_width = max(1, int(getattr(self.camera, "AOIWidth", 1)))
        frame_pixels = frame_height * frame_width
        sensor_frame_dtype = np.dtype(f"u{max(1, (self.Andor.bit_depth + 7) // 8)}")
        camera_frame_bytes = frame_pixels * sensor_frame_dtype.itemsize
        raw_frame_bytes = frame_pixels * get_raw_storage_bytes(self.Andor.storage_dtype_name)

        scope_settings = self._get_scope_capture_settings()
        scope_channel_count = len(scope_settings["enabled_channels"])
        scope_sample_bytes = paired_scope_samples * np.dtype(np.float16).itemsize
        scope_runtime_sample_bytes = scope_buffer_samples * np.dtype(np.float16).itemsize
        raw_history_bytes = target_frames * raw_frame_bytes

        disk_bytes = 0
        disk_bytes += raw_history_bytes
        disk_bytes += target_frames * np.dtype(np.float64).itemsize
        disk_bytes += scope_channel_count * scope_sample_bytes

        ram_bytes = 0
        ram_bytes += raw_history_bytes
        ram_bytes += target_frames * np.dtype(np.float64).itemsize
        ram_bytes += raw_frame_bytes
        ram_bytes += scope_channel_count * scope_runtime_sample_bytes

        camera_bits_per_second = raw_frame_bytes * acquisition_fps * 8.0

        total_write_bits_per_second = 0.0
        total_write_bits_per_second += raw_frame_bytes * acquisition_fps * 8.0
        total_write_bits_per_second += np.dtype(np.float64).itemsize * acquisition_fps * 8.0
        total_write_bits_per_second += scope_channel_count * acquisition_fps * np.dtype(np.float16).itemsize * 8.0

        additional_bits_per_second = max(0.0, total_write_bits_per_second - camera_bits_per_second)
        camera_bytes_per_frame = raw_frame_bytes

        selected_label = str(dpg.get_value(self.hardware_drive_combo_id) or "").strip()
        drive_speed_bps = self._drive_write_speed_cache.get(selected_label)
        drive_error = self._drive_write_speed_errors.get(selected_label)
        memory_available_bytes = self._get_system_memory_available_bytes()
        drive_free_bytes = None
        try:
            drive_free_bytes = self._get_selected_drive_free_bytes()
        except Exception:
            drive_free_bytes = None

        return {
            "ram_bytes": ram_bytes,
            "disk_bytes": disk_bytes,
            "camera_bits_per_second": camera_bits_per_second,
            "camera_bytes_per_frame": camera_bytes_per_frame,
            "acquisition_fps": acquisition_fps,
            "additional_bits_per_second": additional_bits_per_second,
            "total_bits_per_second": total_write_bits_per_second,
            "drive_speed_bps": drive_speed_bps,
            "drive_error": drive_error,
            "memory_available_bytes": memory_available_bytes,
            "drive_free_bytes": drive_free_bytes,
        }

    def _refresh_hardware_requirements(self, force=False):
        signature = self._get_hardware_requirements_signature()
        if not force and signature == self._hardware_requirements_signature:
            return
        self._hardware_requirements_signature = signature

        requirements = self._estimate_acquisition_requirements()
        memory_budget = requirements["memory_available_bytes"]
        ram_required_value, ram_required_unit = self._format_gigabytes(requirements["ram_bytes"])
        if memory_budget is not None and memory_budget > 0:
            ram_available_value, ram_available_unit = self._format_gigabytes(memory_budget)
            ram_unit = "GB" if "GB" in (ram_required_unit, ram_available_unit) else "MB"
            ram_scale = float(1024.0) if ram_unit == "GB" and ram_required_unit == "MB" else 1.0
            ram_available_scale = float(1024.0) if ram_unit == "GB" and ram_available_unit == "MB" else 1.0
            dpg.configure_item(self.hardware_ram_value_id, label=f"RAM ({ram_unit})")
            dpg.set_value(
                self.hardware_ram_value_id,
                f"{ram_required_value / ram_scale:0.2f} / {ram_available_value / ram_available_scale:0.2f}",
            )
        else:
            dpg.configure_item(self.hardware_ram_value_id, label=f"RAM ({ram_required_unit})")
            dpg.set_value(self.hardware_ram_value_id, f"{ram_required_value:0.2f} / Unknown")

        selected_label = str(dpg.get_value(self.hardware_drive_combo_id) or "").strip()
        drive_free_bytes = requirements["drive_free_bytes"]
        disk_required_value, disk_required_unit = self._format_gigabytes(requirements["disk_bytes"])
        if drive_free_bytes is not None and drive_free_bytes > 0:
            disk_available_value, disk_available_unit = self._format_gigabytes(drive_free_bytes)
            disk_unit = "GB" if "GB" in (disk_required_unit, disk_available_unit) else "MB"
            disk_scale = float(1024.0) if disk_unit == "GB" and disk_required_unit == "MB" else 1.0
            disk_available_scale = float(1024.0) if disk_unit == "GB" and disk_available_unit == "MB" else 1.0
            dpg.configure_item(self.hardware_disk_value_id, label=f"Disk Space ({disk_unit})")
            dpg.set_value(
                self.hardware_disk_value_id,
                f"{disk_required_value / disk_scale:0.2f} / {disk_available_value / disk_available_scale:0.2f}",
            )
        else:
            dpg.configure_item(self.hardware_disk_value_id, label=f"Disk Space ({disk_required_unit})")
            dpg.set_value(self.hardware_disk_value_id, f"{disk_required_value:0.2f} / Unknown")

        total_bitrate_value, total_bitrate_unit = self._format_bitrate(requirements["total_bits_per_second"])
        if requirements["drive_speed_bps"] is not None:
            spooling_text = "Available" if requirements["total_bits_per_second"] <= (requirements["drive_speed_bps"] * 8.0) else "Unavailable"
            drive_bitrate_value, drive_bitrate_unit = self._format_bitrate(requirements["drive_speed_bps"] * 8.0)
            bitrate_unit = "Gbps" if "Gbps" in (total_bitrate_unit, drive_bitrate_unit) else "Mbps"
            bitrate_scale = float(1000.0) if bitrate_unit == "Gbps" and total_bitrate_unit == "Mbps" else 1.0
            drive_bitrate_scale = float(1000.0) if bitrate_unit == "Gbps" and drive_bitrate_unit == "Mbps" else 1.0
            dpg.configure_item(self.hardware_bitrate_value_id, label=f"Bitrate ({bitrate_unit})")
            dpg.set_value(
                self.hardware_bitrate_value_id,
                f"{total_bitrate_value / bitrate_scale:0.2f} / {drive_bitrate_value / drive_bitrate_scale:0.2f}",
            )
            dpg.set_value(self.hardware_spooling_value_id, spooling_text)
        elif requirements["drive_error"]:
            dpg.configure_item(self.hardware_bitrate_value_id, label=f"Bitrate ({total_bitrate_unit})")
            dpg.set_value(self.hardware_bitrate_value_id, f"{total_bitrate_value:0.2f} / Unknown")
            dpg.set_value(self.hardware_spooling_value_id, "Unknown")
        elif selected_label:
            dpg.configure_item(self.hardware_bitrate_value_id, label=f"Bitrate ({total_bitrate_unit})")
            dpg.set_value(self.hardware_bitrate_value_id, f"{total_bitrate_value:0.2f} / Unknown")
            dpg.set_value(self.hardware_spooling_value_id, "Unknown")
        else:
            dpg.configure_item(self.hardware_bitrate_value_id, label=f"Bitrate ({total_bitrate_unit})")
            dpg.set_value(self.hardware_bitrate_value_id, f"{total_bitrate_value:0.2f} / Unknown")
            dpg.set_value(self.hardware_spooling_value_id, "Unknown")

    def _set_acquisition_progress(self, progress_value, overlay_text):
        self._acquisition_progress_value = max(0.0, min(1.0, float(progress_value)))
        self._acquisition_progress_overlay = str(overlay_text)

    def _set_save_progress(self, progress_value, overlay_text):
        self._save_progress_value = max(0.0, min(1.0, float(progress_value)))
        self._save_progress_overlay = str(overlay_text)
        if self._save_progress_value <= 0.0 or self._save_progress_value >= 1.0:
            self._save_progress_display_value = self._save_progress_value
            self._save_progress_segment_start_value = self._save_progress_value
            self._save_progress_segment_end_value = self._save_progress_value
            self._save_progress_segment_started_at = time.perf_counter()

    def _set_save_progress_segment(self, start_value, end_value, current_key=None):
        start_value = max(0.0, min(1.0, float(start_value)))
        end_value = max(start_value, min(1.0, float(end_value)))
        self._save_progress_segment_start_value = start_value
        self._save_progress_segment_end_value = end_value
        self._save_progress_segment_key = current_key
        self._save_progress_segment_started_at = time.perf_counter()
        self._save_progress_display_value = max(self._save_progress_display_value, start_value)

    def _update_save_progress(self, completed_units, total_units, current_key=None):
        total_units = max(float(total_units), 1.0)
        progress_value = max(0.0, min(1.0, float(completed_units) / total_units))
        percent = int(round(progress_value * 100.0))
        if current_key:
            overlay = f"Saving {percent}% ({current_key})"
        else:
            overlay = f"Saving {percent}%"
        self._set_save_progress(progress_value, overlay)

    def _get_save_progress_overlay(self, display_value):
        if not self._save_in_progress:
            return self._save_progress_overlay

        percent = int(round(max(0.0, min(1.0, float(display_value))) * 100.0))
        if self._save_progress_segment_key:
            return f"Saving {percent}% ({self._save_progress_segment_key})"
        return f"Saving {percent}%"

    def _get_animated_save_progress_value(self):
        if not self._save_in_progress:
            self._save_progress_display_value = self._save_progress_value
            return self._save_progress_display_value

        start_value = self._save_progress_segment_start_value
        end_value = self._save_progress_segment_end_value
        current_value = max(self._save_progress_display_value, start_value)
        if end_value <= start_value:
            self._save_progress_display_value = max(current_value, self._save_progress_value)
            return self._save_progress_display_value

        elapsed_seconds = max(0.0, time.perf_counter() - self._save_progress_segment_started_at)
        segment_span = end_value - start_value
        approach = 1.0 - np.exp(-1.2 * elapsed_seconds)
        animated_value = start_value + (segment_span * approach)

        if end_value < 1.0:
            animated_cap = max(start_value, end_value - 0.01)
            animated_value = min(animated_value, animated_cap)
        else:
            animated_value = min(animated_value, 0.99)

        self._save_progress_display_value = max(current_value, animated_value)
        return self._save_progress_display_value

    def _write_npz_with_progress(self, file_path, save_arrays):
        entries = []
        total_units = 0

        for key, value in save_arrays.items():
            array_value = np.asanyarray(value)
            entry_units = max(int(array_value.nbytes), 1)
            entries.append((key, array_value, entry_units))
            total_units += entry_units

        directory = os.path.dirname(file_path) or os.getcwd()
        file_descriptor, temp_path = tempfile.mkstemp(prefix=".saving_", suffix=".npz", dir=directory)
        os.close(file_descriptor)

        completed_units = 0
        self._update_save_progress(0, total_units)

        try:
            with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for key, array_value, entry_units in entries:
                    next_completed_units = completed_units + entry_units
                    self._set_save_progress_segment(
                        completed_units / total_units,
                        next_completed_units / total_units,
                        current_key=key,
                    )
                    with archive.open(f"{key}.npy", mode="w", force_zip64=True) as handle:
                        np.lib.format.write_array(handle, array_value, allow_pickle=True)
                    completed_units = next_completed_units
                    self._update_save_progress(completed_units, total_units, current_key=key)

            os.replace(temp_path, file_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _run_save_acquisition(self, file_path, payload):
        save_name = os.path.basename(file_path)

        try:
            camera = payload["camera"]
            scope = payload["scope"] or {}
            save_arrays = self._build_acquisition_save_arrays(camera, scope, payload)
            self._write_npz_with_progress(file_path, save_arrays)
        except Exception as exc:
            with self._acquisition_lock:
                self._pending_save_result = {
                    "success": False,
                    "overlay": f"Save failed: {exc}",
                }
            return

        with self._acquisition_lock:
            self._pending_save_result = {
                "success": True,
                "overlay": f"Saved: {save_name}",
            }

    def _apply_pending_save_result(self):
        with self._acquisition_lock:
            result = self._pending_save_result
            self._pending_save_result = None

        if result is None:
            return

        self._save_in_progress = False
        self._save_thread = None

        if result["success"]:
            self._set_save_progress(1.0, result["overlay"])
            self._set_acquisition_progress(1.0, result["overlay"])
        else:
            self._set_save_progress(0.0, result["overlay"])
            self._set_acquisition_progress(self._acquisition_progress_value, result["overlay"])

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

    def _collect_settings_snapshot(self):
        return {
            "exposure_time":                    float(dpg.get_value(self.settings_exposure_time)),
            "max_exposure":                     bool(dpg.get_value(self.settings_max_exposure_checkbox_id)),
            "trigger_mode":                     str(dpg.get_value(self.settings_trigger_mode)),
            "cooler_enabled":                   bool(dpg.get_value(self.settings_cooler_checkbox_id)),
            "temperature_setpoint":             str(dpg.get_value(self.settings_temp_set_input_id)),
            "pixel_binning":                    str(dpg.get_value(self.settings_pixel_binning)),
            "image_width":                      int(dpg.get_value(self.settings_image_width)),
            "image_height":                     int(dpg.get_value(self.settings_image_height)),
            "image_left":                       int(dpg.get_value(self.settings_image_left)),
            "image_top":                        int(dpg.get_value(self.settings_image_top)),
            "image_auto_center":                bool(dpg.get_value(self.settings_aoi_auto_center_checkbox_id)),
            "frame_storage_dtype":              str(self.Andor.storage_dtype_name),
            "preview_max_frames":               int(dpg.get_value(self.settings_preview_max_frames)),
            "acquisition_duration_seconds":     float(dpg.get_value(self.acquisition_duration_input_id)),
            "acquisition_frame_rate_hz":        float(dpg.get_value(self.acquisition_frame_rate_input_id)),
            "acquisition_scope_sample_rate_hz": float(dpg.get_value(self.acquisition_scope_rate_input_id)),
            "acquisition_zero_on_start":        bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id)),
            "calculate_frame_mean":             bool(dpg.get_value(self.calculate_frame_mean_checkbox_id)),
            "auto_scope_freq":                  bool(dpg.get_value(self.auto_scope_freq_checkbox_id)),
            "set_awg_on_start":                 bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id)),
            "awg_waveform":                     str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower(),
            "awg_dc_offset_volts":              float(dpg.get_value(self.acquisition_awg_dc_offset_input_id)),
            "awg_frequency_hz":                 float(dpg.get_value(self.acquisition_awg_frequency_input_id)),
            "awg_amplitude_vpp_volts":          float(dpg.get_value(self.acquisition_awg_amplitude_input_id)),
            "awg_periodic_offset_volts":        float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id)),
            "awg_start_after_seconds":          float(dpg.get_value(self.acquisition_awg_start_after_input_id)),
            "hardware_storage_drive":           str(dpg.get_value(self.hardware_drive_combo_id) or ""),
            "spool_to_disk_enabled":            bool(dpg.get_value(self.spool_to_disk_enabled_checkbox_id)),
            "spool_to_disk_directory":          str(dpg.get_value(self.spool_to_disk_directory_input_id) or ""),
            "spool_to_disk_filename":           str(dpg.get_value(self.spool_to_disk_filename_input_id) or ""),
            "spool_write_scope":                bool(dpg.get_value(self.spool_write_scope_checkbox_id)),
            "spool_write_power":                bool(dpg.get_value(self.spool_write_power_checkbox_id)),
        }

    def _build_completed_acquisition_payload(self, stopped_early):
        camera_snapshot = self.Andor.get_snapshot()
        scope_controller = self._get_scope_controller()
        scope_snapshot = None
        if scope_controller is not None and scope_controller.driver.is_open:
            scope_snapshot = scope_controller.driver.get_snapshot()
        elif self._acquisition_scope_fallback_snapshot is not None:
            scope_snapshot = {
                "timestamps": list(self._acquisition_scope_fallback_snapshot.get("timestamps", [])),
                "paired_camera_timestamps": list(self._acquisition_scope_fallback_snapshot.get("paired_camera_timestamps", [])),
                "unpaired_camera_timestamps": list(self._acquisition_scope_fallback_snapshot.get("unpaired_camera_timestamps", [])),
                "channels": {
                    channel_name: list(samples)
                    for channel_name, samples in self._acquisition_scope_fallback_snapshot.get("channels", {}).items()
                },
                "actual_sample_rate_hz": self._acquisition_scope_fallback_snapshot.get("actual_sample_rate_hz"),
                "data_bits": self._acquisition_scope_fallback_snapshot.get("data_bits"),
                "device_model": self._acquisition_scope_fallback_snapshot.get("device_model"),
                "active_scope_series": self._acquisition_scope_fallback_snapshot.get("active_scope_series"),
                "history_seconds": self._acquisition_scope_fallback_snapshot.get("history_seconds"),
                "buffer_capacity": self._acquisition_scope_fallback_snapshot.get("buffer_capacity"),
                "frame_pairing_enabled": bool(self._acquisition_scope_fallback_snapshot.get("frame_pairing_enabled")),
            }

        return {
            "camera": camera_snapshot,
            "scope": scope_snapshot,
            "stopped_early": bool(stopped_early),
            "requested_duration_seconds": float(self.acquisition_scope_duration_seconds),
            "requested_frame_rate_hz": float(self.acquisition_frame_rate_hz),
            "requested_scope_sample_rate_hz": float(self.acquisition_scope_sample_rate_hz),
            "requested_storage_dtype": str(self.acquisition_storage_dtype_name),
            "requested_scope_storage_dtype": "float16",
            "requested_scope_sample_count": int(self.acquisition_scope_target_samples),
            "requested_scope_buffer_seconds": float(self.acquisition_scope_buffer_seconds),
            "zero_on_start": bool(self.acquisition_zero_on_start),
            "set_awg_on_start": bool(self.acquisition_set_awg_on_start),
            "awg_waveform": self.acquisition_awg_waveform,
            "awg_start_after_seconds": float(self.acquisition_awg_start_after_seconds),
            "settings": self._acquisition_settings_snapshot or {},
        }

    def _build_scope_fallback_snapshot(self, target_frames, scope_sample_rate, scope_buffer_seconds):
        zero_samples = [0.0] * max(1, int(target_frames))
        zero_timestamps = [0.0] * max(1, int(target_frames))
        return {
            "timestamps": list(zero_timestamps),
            "paired_camera_timestamps": list(zero_timestamps),
            "unpaired_camera_timestamps": [],
            "channels": {channel_name: list(zero_samples) for channel_name in CHANNEL_NAMES},
            "actual_sample_rate_hz": float(scope_sample_rate),
            "data_bits": "float16",
            "device_model": "Unavailable",
            "active_scope_series": "Unavailable",
            "history_seconds": float(scope_buffer_seconds),
            "buffer_capacity": int(max(1, target_frames)),
            "frame_pairing_enabled": True,
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
        self._acquisition_scope_fallback_snapshot = None

        frame_count = len(payload["camera"]["acquisitions"])
        if payload["stopped_early"]:
            self._set_acquisition_progress(1.0, f"Stopped ({frame_count} frames)")
        else:
            self._set_acquisition_progress(1.0, f"Complete ({frame_count} frames)")

    def _run_acquisition(self, scope_controller):
        stopped_early = False
        scope_stopped = False
        awg_enabled_for_run = bool(self.acquisition_set_awg_on_start)
        scope_driver = scope_controller.driver if scope_controller is not None and scope_controller.driver.is_open else None

        try:
            while True:
                if self.acquisition_stop_requested:
                    stopped_early = True
                    break

                camera_done = not self.Andor.is_capturing
                if camera_done and not scope_stopped and scope_driver is not None:
                    scope_driver.stop_collection()
                    scope_stopped = True

                if camera_done and scope_stopped:
                    break
                if camera_done and scope_driver is None:
                    break

                time.sleep(0.02)
        finally:
            self._stop_acquisition_awg_thread()
            if self.Andor.is_capturing:
                self.Andor.stop_capture()
                stopped_early = True
            if awg_enabled_for_run and scope_driver is not None:
                try:
                    scope_driver.set_awg_enabled(False)
                except Exception:
                    pass
            if scope_driver is not None and scope_driver.is_collecting:
                scope_driver.stop_collection()
                scope_stopped = True
            self.Andor.set_scope_frame_mean_source(None, calculate_mean=self.calculate_frame_mean)

            with self._acquisition_lock:
                self._pending_acquisition_result = self._build_completed_acquisition_payload(stopped_early)

            if scope_driver is not None:
                try:
                    scope_driver.configure_frame_pairing(enabled=False)
                except Exception:
                    pass

    def _on_acquire_button_pressed(self, sender=None, app_data=None, user_data=None):
        if self.acquisition_in_progress:
            self.acquisition_stop_requested = True
            if self.Andor.is_capturing:
                self.Andor.stop_capture()
            scope_controller = self._get_scope_controller()
            if scope_controller is not None and scope_controller.driver.is_open and scope_controller.driver.is_collecting:
                scope_controller.driver.stop_collection()
            return

        scope_controller = self._get_scope_controller()
        scope_driver = scope_controller.driver if scope_controller is not None and scope_controller.driver.is_open else None

        acquisition_seconds = max(0.01, float(dpg.get_value(self.acquisition_duration_input_id)))
        acquisition_fps = max(0.1, float(dpg.get_value(self.acquisition_frame_rate_input_id)))
        auto_scope_settings = self._apply_auto_scope_frequency_settings(acquisition_fps)
        scope_sample_rate = float(auto_scope_settings["sample_rate_hz"]) if auto_scope_settings is not None else max(0.1, float(dpg.get_value(self.acquisition_scope_rate_input_id)))
        calculate_frame_mean = bool(dpg.get_value(self.calculate_frame_mean_checkbox_id))
        awg_set_on_start = bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id))
        awg_start_after_seconds = max(0.0, float(dpg.get_value(self.acquisition_awg_start_after_input_id)))
        target_frames = max(1, int(round(acquisition_seconds * acquisition_fps)))
        target_scope_samples = max(1, int(round(acquisition_seconds * scope_sample_rate)))
        scope_buffer_seconds = float(auto_scope_settings["history_seconds"]) if auto_scope_settings is not None else self._get_acquisition_scope_buffer_seconds(acquisition_seconds)
        scope_settings = self._get_scope_capture_settings()

        if awg_set_on_start and awg_start_after_seconds >= acquisition_seconds:
            self._set_acquisition_progress(0.0, "AWG delay must be shorter than acquisition")
            return

        try:
            if self.Andor.is_capturing:
                self.Andor.stop_capture()
            if self.started:
                self.started = False
                self._stop_preview_scope_means()
                self._update_preview_button_state()

            if scope_driver is not None and scope_driver.is_collecting:
                scope_driver.stop_collection()

            if bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id)):
                self.Andor.set_zero_frame(np.array(self.Andor.latest_frame, copy=True))

            self.Andor.set_frame_rate(acquisition_fps)
            self.Andor.configure_scope_frame_mean_buffers(scope_settings["enabled_channels"], target_frames)
            self.Andor.set_scope_frame_mean_source(scope_driver, calculate_mean=calculate_frame_mean)
            self.Andor.clear_buffers(reset_frame_index=True)
            self._acquisition_scope_fallback_snapshot = self._build_scope_fallback_snapshot(
                target_frames,
                scope_sample_rate,
                scope_buffer_seconds,
            )
            if scope_driver is not None:
                scope_driver.stop_collection()
                scope_driver.set_sample_capture_rate(scope_sample_rate)
                scope_driver.set_history_seconds(scope_buffer_seconds)
                scope_driver.configure_frame_pairing(enabled=False)
                scope_driver.clear_buffers()
                if awg_set_on_start:
                    scope_driver.configure_awg(**self._collect_acquisition_awg_config())
                    scope_driver.set_awg_enabled(False)

            self.acquisition_duration_seconds = acquisition_seconds
            self.acquisition_frame_rate_hz = acquisition_fps
            self.acquisition_scope_sample_rate_hz = scope_sample_rate
            self.calculate_frame_mean = calculate_frame_mean
            self.acquisition_storage_dtype_name = self.Andor.storage_dtype_name
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
            self._acquisition_settings_snapshot = self._collect_settings_snapshot()

            self.Andor.start_capture_fixed(target_frames)
            if scope_driver is not None:
                scope_driver.start_collection()
            if awg_set_on_start and scope_driver is not None:
                self._acquisition_awg_stop_event = threading.Event()
                self._acquisition_awg_thread = threading.Thread(
                    target=self._run_acquisition_awg_thread,
                    args=(scope_controller, awg_start_after_seconds, self._acquisition_awg_stop_event),
                    daemon=True,
                )
                self._acquisition_awg_thread.start()
            self._acquisition_thread = threading.Thread(
                target=self._run_acquisition,
                args=(scope_controller,),
                daemon=True,
            )
            self._acquisition_thread.start()
        except Exception as exc:
            self._stop_acquisition_awg_thread()
            self.acquisition_in_progress = False
            self.acquisition_stop_requested = False
            self.acquisition_started_at = None
            self._acquisition_thread = None
            self._acquisition_scope_fallback_snapshot = None
            self._acquisition_settings_snapshot = None
            self.Andor.set_scope_frame_mean_source(None, calculate_mean=calculate_frame_mean)
            try:
                if self.Andor.is_capturing:
                    self.Andor.stop_capture()
            except Exception:
                pass
            try:
                if scope_driver is not None and scope_driver.is_collecting:
                    scope_driver.stop_collection()
            except Exception:
                pass
            self._update_acquisition_button_state()
            self._set_acquisition_progress(0.0, f"Error: {exc}")

    def _show_save_dialog(self, sender=None, app_data=None, user_data=None):
        if self._completed_acquisition_payload is None:
            return
        save_directory = self._resolve_save_directory(self.last_save_directory)
        dpg.configure_item(self.save_dialog_id, default_path=save_directory)
        dpg.show_item(self.save_dialog_id)

    def _show_open_dialog(self, sender=None, app_data=None, user_data=None):
        dpg.show_item(self.open_dialog_id)

    def _open_acquisition_preview(self, file_path):
        if self._acquisition_preview_window is not None:
            self._acquisition_preview_window.close()
            self._acquisition_preview_window = None

        self._acquisition_preview_window = AcquisitionPreviewWindow(file_path)

    def _on_open_dialog_selected(self, sender, app_data, user_data=None):
        file_path = str(app_data.get("file_path_name") or "").strip()
        if not file_path:
            return
        if not file_path.lower().endswith(".npz"):
            file_path = f"{file_path}.npz"
        self._open_acquisition_preview(file_path)

    def _on_save_dialog_selected(self, sender, app_data, user_data=None):
        file_path = str(app_data.get("file_path_name") or "").strip()
        if not file_path:
            return
        if not file_path.lower().endswith(".npz"):
            file_path = f"{file_path}.npz"
        self.last_save_directory = self._resolve_save_directory(os.path.dirname(file_path))

        with self._acquisition_lock:
            payload = self._completed_acquisition_payload

        if payload is None or self._save_in_progress:
            return

        self._save_in_progress = True
        self._save_thread = threading.Thread(
            target=self._run_save_acquisition,
            args=(file_path, payload),
            name="AcquisitionSaveThread",
            daemon=True,
        )
        self._save_progress_display_value = 0.0
        self._save_progress_segment_start_value = 0.0
        self._save_progress_segment_end_value = 0.0
        self._save_progress_segment_key = None
        self._save_progress_segment_started_at = time.perf_counter()
        self._set_save_progress(0.0, "Saving 0%")
        self._save_thread.start()

    def _resolve_save_directory(self, directory):
        requested_directory = str(directory or "").strip()
        if requested_directory:
            normalized_directory = os.path.abspath(requested_directory)
            if os.path.isdir(normalized_directory):
                return normalized_directory

        spool_directory = str(getattr(self, "spool_to_disk_directory", "") or "").strip()
        if spool_directory:
            normalized_spool_directory = os.path.abspath(spool_directory)
            if os.path.isdir(normalized_spool_directory):
                return normalized_spool_directory

        experiments_directory = os.path.abspath(os.path.join(os.getcwd(), "Experiments"))
        if os.path.isdir(experiments_directory):
            return experiments_directory

        return os.getcwd()

    def toggle_preview(self):
        if self.Andor.is_capturing:
            self.Andor.stop_capture()
            self._stop_preview_scope_means()
            self.started = False
            self.preview_zero_reference_pending = False

        else:
            # Reset the camera feed texture size
            self.started = True
            self._apply_preview_max_frames(dpg.get_value(self.settings_preview_max_frames))
            self._start_preview_scope_means()
            self.Andor.clear_buffers(reset_frame_index=True)
            self.camera_feed.reset_texture()
            self.camera_feed.rebuild_roi_traces()
            self.preview_zero_reference_pending = int(getattr(self.Andor, "zero_version", 0)) <= 0
            self.Andor.start_capture_continuous()

    def _get_camera_aoi_limits(self):
        return {
            "width_min": max(1, int(getattr(self.camera, "min_AOIWidth", 1))),
            "width_max": max(1, int(getattr(self.camera, "max_AOIWidth", getattr(self.camera, "AOIWidth", 1)))),
            "height_min": max(1, int(getattr(self.camera, "min_AOIHeight", 1))),
            "height_max": max(1, int(getattr(self.camera, "max_AOIHeight", getattr(self.camera, "AOIHeight", 1)))),
            "left_min": max(1, int(getattr(self.camera, "min_AOILeft", 1))),
            "top_min": max(1, int(getattr(self.camera, "min_AOITop", 1))),
        }

    def _get_current_aoi_settings(self):
        return {
            "width": int(getattr(self.camera, "AOIWidth", 1)),
            "height": int(getattr(self.camera, "AOIHeight", 1)),
            "left": int(getattr(self.camera, "AOILeft", 1)),
            "top": int(getattr(self.camera, "AOITop", 1)),
        }

    def _get_requested_aoi_settings_from_widgets(self):
        return {
            "width": int(dpg.get_value(self.settings_image_width)),
            "height": int(dpg.get_value(self.settings_image_height)),
            "left": int(dpg.get_value(self.settings_image_left)),
            "top": int(dpg.get_value(self.settings_image_top)),
        }

    def _get_centered_aoi_position(self, width, height, limits=None):
        limits = limits or self._get_camera_aoi_limits()
        left_max = max(limits["left_min"], limits["width_max"] - width + 1)
        top_max = max(limits["top_min"], limits["height_max"] - height + 1)
        left = limits["left_min"] + max(0, int(round((left_max - limits["left_min"]) / 2.0)))
        top = limits["top_min"] + max(0, int(round((top_max - limits["top_min"]) / 2.0)))
        return int(left), int(top)

    def _normalize_aoi_settings(self, requested):
        limits = self._get_camera_aoi_limits()
        width = int(np.clip(int(requested["width"]), limits["width_min"], limits["width_max"]))
        height = int(np.clip(int(requested["height"]), limits["height_min"], limits["height_max"]))

        left_max = max(limits["left_min"], limits["width_max"] - width + 1)
        top_max = max(limits["top_min"], limits["height_max"] - height + 1)
        left = int(np.clip(int(requested["left"]), limits["left_min"], left_max))
        top = int(np.clip(int(requested["top"]), limits["top_min"], top_max))

        width_max = max(limits["width_min"], limits["width_max"] - left + 1)
        height_max = max(limits["height_min"], limits["height_max"] - top + 1)
        width = int(np.clip(width, limits["width_min"], width_max))
        height = int(np.clip(height, limits["height_min"], height_max))

        left_max = max(limits["left_min"], limits["width_max"] - width + 1)
        top_max = max(limits["top_min"], limits["height_max"] - height + 1)
        left = int(np.clip(left, limits["left_min"], left_max))
        top = int(np.clip(top, limits["top_min"], top_max))

        if self.aoi_auto_center_enabled:
            left, top = self._get_centered_aoi_position(width, height, limits)

        return {
            "width": width,
            "height": height,
            "left": left,
            "top": top,
        }

    def _update_aoi_control_state(self):
        left_top_enabled = (not self.Andor.is_capturing) and (not self.aoi_auto_center_enabled)
        dpg.configure_item(self.settings_image_left, enabled=left_top_enabled)
        dpg.configure_item(self.settings_image_top, enabled=left_top_enabled)
        dpg.bind_item_theme(self.settings_image_left, None if left_top_enabled else read_only_theme)
        dpg.bind_item_theme(self.settings_image_top, None if left_top_enabled else read_only_theme)

    def _sync_aoi_widgets(self, aoi_settings=None):
        settings = aoi_settings or self._get_current_aoi_settings()
        normalized = self._normalize_aoi_settings(settings)
        limits = self._get_camera_aoi_limits()

        width_max = max(limits["width_min"], limits["width_max"] - normalized["left"] + 1)
        height_max = max(limits["height_min"], limits["height_max"] - normalized["top"] + 1)
        left_max = max(limits["left_min"], limits["width_max"] - normalized["width"] + 1)
        top_max = max(limits["top_min"], limits["height_max"] - normalized["height"] + 1)

        dpg.configure_item(self.settings_image_width, min_value=limits["width_min"], max_value=width_max)
        dpg.configure_item(self.settings_image_height, min_value=limits["height_min"], max_value=height_max)
        dpg.configure_item(self.settings_image_left, min_value=limits["left_min"], max_value=left_max)
        dpg.configure_item(self.settings_image_top, min_value=limits["top_min"], max_value=top_max)

        dpg.set_value(self.settings_image_width, normalized["width"])
        dpg.set_value(self.settings_image_height, normalized["height"])
        dpg.set_value(self.settings_image_left, normalized["left"])
        dpg.set_value(self.settings_image_top, normalized["top"])
        dpg.set_value(self.settings_aoi_auto_center_checkbox_id, self.aoi_auto_center_enabled)
        self._update_aoi_control_state()

    def _refresh_aoi_controls_from_camera(self):
        self._sync_aoi_widgets(self._get_current_aoi_settings())

    def _apply_frame_rate_exposure_constraints(self, changed_prop):
        requested_frame_rate_hz = max(0.1, float(dpg.get_value(self.settings_frame_rate)))
        max_exposure_enabled = bool(dpg.get_value(self.settings_max_exposure_checkbox_id))
        requested_exposure_seconds = max(1e-6, float(dpg.get_value(self.settings_exposure_time)))

        if max_exposure_enabled:
            frame_rate_hz = requested_frame_rate_hz
            exposure_seconds = 1.0 / requested_frame_rate_hz
        elif changed_prop == "ExposureTime":
            frame_rate_hz = min(requested_frame_rate_hz, 1.0 / requested_exposure_seconds)
            exposure_seconds = requested_exposure_seconds
        else:
            frame_rate_hz = requested_frame_rate_hz
            exposure_seconds = min(requested_exposure_seconds, 1.0 / requested_frame_rate_hz)

        try:
            if changed_prop == "ExposureTime":
                if hasattr(self.camera, "FrameRate"):
                    self.camera.FrameRate = frame_rate_hz
                self.camera.ExposureTime = exposure_seconds
            else:
                self.camera.ExposureTime = exposure_seconds
                if hasattr(self.camera, "FrameRate"):
                    self.camera.FrameRate = frame_rate_hz
        except Exception:
            dpg.set_value(self.settings_frame_rate, float(getattr(self.camera, "FrameRate", requested_frame_rate_hz)))
            dpg.set_value(self.settings_exposure_time, float(getattr(self.camera, "ExposureTime", requested_exposure_seconds)))
            raise

        actual_frame_rate_hz = float(getattr(self.camera, "FrameRate", frame_rate_hz))
        actual_exposure_seconds = float(getattr(self.camera, "ExposureTime", exposure_seconds))
        dpg.set_value(self.settings_frame_rate, actual_frame_rate_hz)
        dpg.set_value(self.settings_exposure_time, actual_exposure_seconds)
        self.acquisition_frame_rate_hz = actual_frame_rate_hz
        self._apply_auto_scope_frequency_settings(actual_frame_rate_hz)
        self.Andor.frame_ready_event.set()

    def _on_max_exposure_changed(self, sender=None, app_data=None, user_data=None):
        self.max_exposure = bool(app_data)
        self._apply_frame_rate_exposure_constraints("FrameRate")
        self._refresh_hardware_requirements(force=True)

    def _render_frame_scope_window(self, *, force=False):
        if self.frame_scope_window is None:
            return
        if not self.frame_scope_window.is_visible():
            self._frame_scope_render_snapshot = None
            return

        current_frame_index = int(getattr(self.Andor, "frameIdx", 0))
        current_mean_count = int(self.Andor.get_scope_frame_mean_count())
        should_render = force or (
            current_frame_index != self._last_frame_scope_render_frame_index
            or current_mean_count != self._last_frame_scope_render_mean_count
        )
        if not should_render:
            return

        self._frame_scope_render_snapshot = self.Andor.get_scope_frame_mean_snapshot()
        try:
            self.frame_scope_window.render()
        finally:
            self._frame_scope_render_snapshot = None
        self._last_frame_scope_render_frame_index = current_frame_index
        self._last_frame_scope_render_mean_count = current_mean_count

    def _on_aoi_auto_center_changed(self, sender=None, app_data=None, user_data=None):
        self.aoi_auto_center_enabled = bool(dpg.get_value(self.settings_aoi_auto_center_checkbox_id))
        self._apply_aoi_settings()
        self._refresh_hardware_requirements(force=True)

    def _apply_aoi_settings(self, requested_settings=None):
        normalized = self._normalize_aoi_settings(requested_settings or self._get_requested_aoi_settings_from_widgets())
        try:
            self.camera.AOIWidth = normalized["width"]
            self.camera.AOIHeight = normalized["height"]
            self.camera.AOILeft = normalized["left"]
            self.camera.AOITop = normalized["top"]
        except Exception:
            self._refresh_aoi_controls_from_camera()
            raise

        self._refresh_aoi_controls_from_camera()
        self.Andor.clear_buffers(reset_frame_index=True)
        self.camera_feed.reset_texture()
        self.camera_feed.rebuild_roi_traces()

    def setprop(self, prop, setting):
        if prop in {"AOIWidth", "AOIHeight", "AOILeft", "AOITop"}:
            self._apply_aoi_settings()
            self._refresh_hardware_requirements(force=True)
            return

        if prop == "AOIBinning":
            requested_value = dpg.get_value(setting)
            try:
                setattr(self.camera, prop, requested_value)
            except Exception:
                dpg.set_value(setting, getattr(self.camera, prop))
                raise

            self._apply_aoi_settings()
            self._refresh_hardware_requirements(force=True)
            return

        if prop in {"ExposureTime", "FrameRate"}:
            self._apply_frame_rate_exposure_constraints(prop)
            self._refresh_hardware_requirements(force=True)
            return

        setattr(self.camera, prop, dpg.get_value(setting))
        self._refresh_hardware_requirements(force=True)

    def _update_bottom_controls_layout(self):
        if not dpg.does_item_exist(self.window_id) or not dpg.does_item_exist(self.bottom_controls_group_id):
            return

        window_width, window_height = dpg.get_item_rect_size(self.window_id)
        if window_width <= 0 or window_height <= 0:
            return

        padding = int(self._footer_padding)
        footer_width = max(1, int(window_width) - (padding * 2))
        for item_id in (
            self.acquisition_progress_bar_id,
            self.acquire_button_id,
            self.start_button_id,
            self.save_button_id,
            self.save_progress_bar_id,
            self.open_button_id,
        ):
            dpg.configure_item(item_id, width=footer_width)

        footer_height = max(1, int(dpg.get_item_rect_size(self.bottom_controls_group_id)[1]))
        footer_y = max(padding, int(window_height) - footer_height - padding)
        dpg.set_item_pos(self.bottom_controls_group_id, (padding, footer_y))

        content_height = max(1, footer_y - padding)
        dpg.configure_item(self.content_area_id, height=content_height)


    def render(self):
        self.camera_feed.render()
        self.z_axis_controls.render()
        if self._acquisition_preview_window is not None:
            if not self._acquisition_preview_window.render():
                self._acquisition_preview_window = None
        self._render_frame_scope_window(force=self.Andor.frame_ready_event.is_set())
        self._sync_camera_cooler_readout()

        if self.started and self.preview_zero_reference_pending and self.Andor.frameIdx > 0:
            self.preview_zero_reference_pending = not self.camera_feed.ensure_zero_reference_from_latest_frame()

        self._apply_pending_acquisition_result()
        self._apply_pending_save_result()
        self._refresh_hardware_requirements()

        if self.acquisition_in_progress and self.acquisition_started_at is not None:
            elapsed_seconds = max(0.0, time.perf_counter() - self.acquisition_started_at)
            camera_progress = min(1.0, self.Andor.frameIdx / max(self.acquisition_target_frames, 1))
            scope_controller = self._get_scope_controller()
            scope_sample_count = len(scope_controller.driver.get_timestamps()) if scope_controller is not None else 0
            scope_progress = min(1.0, scope_sample_count / max(self.acquisition_scope_target_samples, 1))
            progress_value = min(camera_progress, scope_progress)
            overlay = f"{self.Andor.frameIdx}/{self.acquisition_target_frames} frames | {elapsed_seconds:0.1f}s"
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

        self._update_aoi_control_state()

        dpg.configure_item(
            self.settings_cooler_checkbox_id,
            enabled=(not self.Andor.is_capturing and self.cooler_supported),
        )
        exposure_enabled = (not self.Andor.is_capturing) and (not bool(dpg.get_value(self.settings_max_exposure_checkbox_id)))
        dpg.configure_item(self.settings_exposure_time, enabled=exposure_enabled)
        dpg.bind_item_theme(self.settings_exposure_time, None if exposure_enabled else read_only_theme)
        dpg.configure_item(self.cooler_temperature_input_id, enabled=False)
        if hasattr(self, "_cooler_enabled_value"):
            dpg.set_value(self.settings_cooler_checkbox_id, bool(self._cooler_enabled_value))
        if hasattr(self, "_cooler_temperature_c"):
            dpg.set_value(self.cooler_temperature_input_id, float(self._cooler_temperature_c))

        acquisition_inputs = (
            self.acquisition_duration_input_id,
            self.acquisition_frame_rate_input_id,
            self.acquisition_scope_rate_input_id,
            self.acquisition_zero_on_start_checkbox_id,
            self.calculate_frame_mean_checkbox_id,
            self.auto_scope_freq_checkbox_id,
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
        self._update_spool_to_disk_controls()
        displayed_save_progress = self._get_animated_save_progress_value()
        save_progress_overlay = self._get_save_progress_overlay(displayed_save_progress)
        dpg.configure_item(self.save_button_id, enabled=self._completed_acquisition_payload is not None and not self._save_in_progress)
        dpg.configure_item(self.save_button_id, show=not self._save_in_progress)
        dpg.configure_item(self.save_progress_bar_id, show=self._save_in_progress)
        dpg.set_value(self.save_progress_bar_id, displayed_save_progress)
        dpg.configure_item(self.save_progress_bar_id, overlay=save_progress_overlay)
        dpg.set_value(self.acquisition_progress_bar_id, self._acquisition_progress_value)
        dpg.configure_item(self.acquisition_progress_bar_id, overlay=self._acquisition_progress_overlay)
        self._update_bottom_controls_layout()

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "window": capture_window_state(self.window_id),
                "sections": capture_item_open_states(self.section_node_ids),
                "exposure_time": float(dpg.get_value(self.settings_exposure_time)),
                "max_exposure": bool(dpg.get_value(self.settings_max_exposure_checkbox_id)),
                "trigger_mode": str(dpg.get_value(self.settings_trigger_mode)),
                "cooler_enabled": bool(dpg.get_value(self.settings_cooler_checkbox_id)),
                "temperature_setpoint": str(self.temperature_setpoint_value),
                "pixel_binning": str(dpg.get_value(self.settings_pixel_binning)),
                "image_width": int(dpg.get_value(self.settings_image_width)),
                "image_height": int(dpg.get_value(self.settings_image_height)),
                "image_left": int(dpg.get_value(self.settings_image_left)),
                "image_top": int(dpg.get_value(self.settings_image_top)),
                "image_auto_center": bool(dpg.get_value(self.settings_aoi_auto_center_checkbox_id)),
                "frame_storage_dtype": str(self.Andor.storage_dtype_name),
                "preview_max_frames": int(dpg.get_value(self.settings_preview_max_frames)),
                "acquisition_duration_seconds": float(dpg.get_value(self.acquisition_duration_input_id)),
                "acquisition_frame_rate_hz": float(dpg.get_value(self.acquisition_frame_rate_input_id)),
                "acquisition_scope_sample_rate_hz": float(dpg.get_value(self.acquisition_scope_rate_input_id)),
                "acquisition_zero_on_start": bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id)),
                "calculate_frame_mean": bool(dpg.get_value(self.calculate_frame_mean_checkbox_id)),
                "auto_scope_freq": bool(dpg.get_value(self.auto_scope_freq_checkbox_id)),
                "acquisition_set_awg_on_start": bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id)),
                "acquisition_awg_waveform": str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower(),
                "acquisition_awg_dc_offset_volts": float(dpg.get_value(self.acquisition_awg_dc_offset_input_id)),
                "acquisition_awg_frequency_hz": float(dpg.get_value(self.acquisition_awg_frequency_input_id)),
                "acquisition_awg_amplitude_vpp_volts": float(dpg.get_value(self.acquisition_awg_amplitude_input_id)),
                "acquisition_awg_periodic_offset_volts": float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id)),
                "acquisition_awg_start_after_seconds": float(dpg.get_value(self.acquisition_awg_start_after_input_id)),
                "hardware_storage_drive": str(dpg.get_value(self.hardware_drive_combo_id) or ""),
                "last_save_directory": str(self.last_save_directory or ""),
                "spool_to_disk_enabled": bool(dpg.get_value(self.spool_to_disk_enabled_checkbox_id)),
                "spool_to_disk_directory": str(dpg.get_value(self.spool_to_disk_directory_input_id) or ""),
                "spool_to_disk_filename": str(dpg.get_value(self.spool_to_disk_filename_input_id) or ""),
                "spool_write_scope": bool(dpg.get_value(self.spool_write_scope_checkbox_id)),
                "spool_write_power": bool(dpg.get_value(self.spool_write_power_checkbox_id)),
            },
        )
        if hasattr(self.z_axis_controls, "SaveState"):
            self.z_axis_controls.SaveState()
        if hasattr(self.camera_feed, "SaveState"):
            self.camera_feed.SaveState()
        if self.frame_scope_window is not None:
            self.frame_scope_window.SaveState()
        if self._acquisition_preview_window is not None and hasattr(self._acquisition_preview_window, "SaveState"):
            self._acquisition_preview_window.SaveState()

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if state:
            apply_window_state(self.window_id, state.get("window"))
            apply_item_open_states(self.section_node_ids, state.get("sections"))

            property_map = (
                ("exposure_time", "ExposureTime", self.settings_exposure_time),
                ("trigger_mode", "TriggerMode", self.settings_trigger_mode),
                ("pixel_binning", "AOIBinning", self.settings_pixel_binning),
            )
            for state_key, camera_property, widget_id in property_map:
                if state_key not in state:
                    continue
                dpg.set_value(widget_id, state[state_key])
                setattr(self.camera, camera_property, dpg.get_value(widget_id))

            self.aoi_auto_center_enabled = bool(state.get("image_auto_center", self.aoi_auto_center_enabled))
            dpg.set_value(self.settings_aoi_auto_center_checkbox_id, self.aoi_auto_center_enabled)

            requested_aoi = self._get_current_aoi_settings()
            if "image_width" in state:
                requested_aoi["width"] = int(state["image_width"])
            if "image_height" in state:
                requested_aoi["height"] = int(state["image_height"])
            if "image_left" in state:
                requested_aoi["left"] = int(state["image_left"])
            if "image_top" in state:
                requested_aoi["top"] = int(state["image_top"])
            self._apply_aoi_settings(requested_aoi)

            if "cooler_enabled" in state:
                self.Andor.set_sensor_cooling_enabled(bool(state["cooler_enabled"]))
            if "temperature_setpoint" in state:
                requested_option = str(state["temperature_setpoint"])
                if requested_option in self.temperature_setpoint_options:
                    self.temperature_setpoint_value = requested_option
                    dpg.set_value(self.settings_temp_set_input_id, requested_option)
                    self.Andor.set_temperature_setpoint_option(requested_option)
            elif "temperature_setpoint_c" in state:
                requested_option = self.Andor._resolve_temperature_setpoint_option(
                    self._clamp_temperature_setpoint_c(state["temperature_setpoint_c"])
                )
                if requested_option in self.temperature_setpoint_options:
                    self.temperature_setpoint_value = requested_option
                    dpg.set_value(self.settings_temp_set_input_id, requested_option)
                    self.Andor.set_temperature_setpoint_option(requested_option)

            if "max_exposure" in state:
                dpg.set_value(self.settings_max_exposure_checkbox_id, bool(state["max_exposure"]))
                self.max_exposure = bool(state["max_exposure"])

            self._apply_preview_max_frames(int(state.get("preview_max_frames", self.preview_max_frames)))

            if "acquisition_duration_seconds" in state:
                dpg.set_value(self.acquisition_duration_input_id, float(state["acquisition_duration_seconds"]))
            if "acquisition_frame_rate_hz" in state:
                dpg.set_value(self.acquisition_frame_rate_input_id, float(state["acquisition_frame_rate_hz"]))
            if "acquisition_scope_sample_rate_hz" in state:
                dpg.set_value(self.acquisition_scope_rate_input_id, float(state["acquisition_scope_sample_rate_hz"]))
            if "acquisition_zero_on_start" in state:
                dpg.set_value(self.acquisition_zero_on_start_checkbox_id, bool(state["acquisition_zero_on_start"]))
            if "calculate_frame_mean" in state:
                dpg.set_value(self.calculate_frame_mean_checkbox_id, bool(state["calculate_frame_mean"]))
            if "auto_scope_freq" in state:
                dpg.set_value(self.auto_scope_freq_checkbox_id, bool(state["auto_scope_freq"]))
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
            if "spool_to_disk_enabled" in state:
                dpg.set_value(self.spool_to_disk_enabled_checkbox_id, bool(state["spool_to_disk_enabled"]))
            if "spool_to_disk_directory" in state:
                dpg.set_value(self.spool_to_disk_directory_input_id, str(state["spool_to_disk_directory"]))
            if "spool_to_disk_filename" in state:
                dpg.set_value(self.spool_to_disk_filename_input_id, str(state["spool_to_disk_filename"]))
            self.spool_to_disk_directory = str(dpg.get_value(self.spool_to_disk_directory_input_id) or "")
            self.last_save_directory = self._resolve_save_directory(state.get("last_save_directory", self.spool_to_disk_directory))
            if "spool_write_scope" in state:
                dpg.set_value(self.spool_write_scope_checkbox_id, bool(state["spool_write_scope"]))
            if "spool_write_power" in state:
                dpg.set_value(self.spool_write_power_checkbox_id, bool(state["spool_write_power"]))
            self._refresh_storage_devices(state.get("hardware_storage_drive"))

            self.acquisition_duration_seconds = float(dpg.get_value(self.acquisition_duration_input_id))
            self._apply_frame_rate_exposure_constraints("FrameRate")
            self.acquisition_frame_rate_hz = float(dpg.get_value(self.acquisition_frame_rate_input_id))
            self.acquisition_scope_sample_rate_hz = float(dpg.get_value(self.acquisition_scope_rate_input_id))
            self.acquisition_storage_dtype_name = self.Andor.storage_dtype_name
            self.max_exposure = bool(dpg.get_value(self.settings_max_exposure_checkbox_id))
            self.acquisition_zero_on_start = bool(dpg.get_value(self.acquisition_zero_on_start_checkbox_id))
            self.calculate_frame_mean = bool(dpg.get_value(self.calculate_frame_mean_checkbox_id))
            self.auto_scope_freq = bool(dpg.get_value(self.auto_scope_freq_checkbox_id))
            self.acquisition_set_awg_on_start = bool(dpg.get_value(self.acquisition_set_awg_on_start_checkbox_id))
            self.acquisition_awg_waveform = str(dpg.get_value(self.acquisition_awg_waveform_combo_id)).strip().lower()
            self.acquisition_awg_dc_offset_volts = float(dpg.get_value(self.acquisition_awg_dc_offset_input_id))
            self.acquisition_awg_frequency_hz = float(dpg.get_value(self.acquisition_awg_frequency_input_id))
            self.acquisition_awg_amplitude_vpp_volts = float(dpg.get_value(self.acquisition_awg_amplitude_input_id))
            self.acquisition_awg_periodic_offset_volts = float(dpg.get_value(self.acquisition_awg_periodic_offset_input_id))
            self.acquisition_awg_start_after_seconds = float(dpg.get_value(self.acquisition_awg_start_after_input_id))
            self.spool_to_disk_enabled = bool(dpg.get_value(self.spool_to_disk_enabled_checkbox_id))
            self.spool_to_disk_directory = str(dpg.get_value(self.spool_to_disk_directory_input_id) or "")
            self.spool_to_disk_filename = str(dpg.get_value(self.spool_to_disk_filename_input_id) or "")
            self.spool_write_scope = bool(dpg.get_value(self.spool_write_scope_checkbox_id))
            self.spool_write_power = bool(dpg.get_value(self.spool_write_power_checkbox_id))
            self._sync_camera_cooler_readout()
            self._update_acquisition_awg_visibility()
            self._update_spool_to_disk_controls()
            self._refresh_hardware_requirements(force=True)

        if self.frame_scope_window is not None:
            self.frame_scope_window.LoadState()

        if hasattr(self.z_axis_controls, "LoadState"):
            self.z_axis_controls.LoadState()
        if hasattr(self.camera_feed, "LoadState"):
            self.camera_feed.LoadState()