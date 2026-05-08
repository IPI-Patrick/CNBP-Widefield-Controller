import os
import threading
import time

import dearpygui.dearpygui as dpg
import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, to_rgb

from Utils.StorageDTypes import get_raw_storage_max_value
from Utils.fonts import get_segmdl2_icon_font
from Utils.state_persistence import apply_item_open_states, apply_window_state, capture_item_open_states, capture_window_state, load_state_file, save_state_file
from Utils.themes import read_only_theme
from Windows.SubWindows.RegionOfInterest import RegionOfInterest
from Windows.SubWindows.ROIsWindow import ROIsWindow


with dpg.theme() as acquisition_preview_window_theme:
    with dpg.theme_component(dpg.mvWindowAppItem):
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, [27, 92, 53])
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, [36, 122, 70])
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed, [21, 68, 39])


with dpg.theme() as preview_repeat_button_theme_on:
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button, [78, 78, 78])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, [92, 92, 92])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, [104, 104, 104])


with dpg.theme() as preview_playback_dock_theme:
    with dpg.theme_component(dpg.mvChildWindow):
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, [58, 58, 58])
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 10)
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button, [92, 92, 92])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, [108, 108, 108])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, [120, 120, 120])


class PreviewAndorAdapter:

    def __init__(self, parent):
        self.parent = parent
        self.storage_dtype = np.dtype(np.uint16)
        self.max_acquisitions = 0
        self.processed_frame = np.zeros((1, 1), dtype=np.float32)
        self.processed_frame_idx = -1
        self.processed_frame_condition = threading.Condition()
        self.frame_lock = threading.Lock()

    def update_from_payload(self, payload):
        acquisitions = np.asarray(payload.get("acquisitions", []))
        self.storage_dtype = acquisitions.dtype if acquisitions.size > 0 else np.dtype(np.uint16)
        self.max_acquisitions = int(payload.get("frame_count", 0))

    def get_estimated_time_axis_values(self, point_count):
        payload = self.parent.get_loaded_payload()
        if payload is None or point_count <= 0:
            return np.asarray([], dtype=np.float64)
        timestamps = np.asarray(payload.get("relative_timestamps", []), dtype=np.float64)
        return timestamps[: int(point_count)]

    def get_lp_filter_coefficients(self):
        payload = self.parent.get_loaded_payload()
        if payload is None:
            return None
        sample_rate_hz = max(float(self.parent.playback_fps), 1e-6)
        nyquist_hz = sample_rate_hz * 0.5
        cutoff_hz = float(np.clip(self.parent.lp_filter_cutoff_hz, 1e-6, max(1e-6, nyquist_hz * 0.99)))
        k = float(np.tan(np.pi * cutoff_hz / sample_rate_hz))
        norm = 1.0 / (1.0 + k)
        return (k * norm), (k * norm), ((k - 1.0) * norm)

    def apply_lp_filter_step(self, current_input, previous_input, previous_output, coefficients):
        if coefficients is None or previous_input is None or previous_output is None:
            return np.asarray(current_input, dtype=np.float32)
        b0, b1, a1 = coefficients
        filtered = b0 * current_input + b1 * previous_input - a1 * previous_output
        return np.clip(filtered, 0.0, float(self.parent.display_max)).astype(np.float32)

    def coerce_raw_frame_to_storage(self, frame):
        return np.asarray(frame, dtype=self.storage_dtype)


class AcquisitionPreviewWindow:

    single_sided_colormap_names = ("Viridis", "Plasma", "Inferno", "Cividis", "Terrain")
    double_sided_colormap_names = ("Cividis", "Berlin", "Red Blue", "Tofino", "Vanimo")
    colormap_lookup_names = {
        "Viridis": "viridis",
        "Plasma": "plasma",
        "Inferno": "inferno",
        "Cividis": "cividis",
        "Berlin": "berlin",
        "Vanimo": "vanimo",
    }
    custom_colormap_hex_scales = {
        "Red Blue": ["#00FFFF", "#2662D9", "#142952", "#000000", "#521414", "#D98026", "#FFFF00"],
        "Terrain": ["#021A4D", "#37D76C", "#FDFC97", "#8B5D50", "#FFFFFF"],
        "Tofino": ["#ded9ff", "#4a6bac", "#000000", "#3f8144", "#dbe69b"],
    }
    difference_display_limit = float(np.finfo(np.float16).max)
    autoscale_lower_percentile = 1.0
    autoscale_upper_percentile = 99.5
    state_name = "AcquisitionPreviewWindow"
    rois_state_name = "AcquisitionPreviewROIsWindow"
    play_icon = "\uE768"
    pause_icon = "\uE769"
    repeat_icon = "\uE8EE"

    def __init__(self, file_path):
        self.file_path = os.path.abspath(file_path)
        self.tag = f"Preview_{int(time.time() * 1000)}"
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._load_thread = None
        self._playback_thread = None
        self._pending_loaded_payload = None
        self._pending_load_error = None
        self._loaded_payload = None
        self._ui_initialized = False
        self._is_closed = False
        self._closing = False
        self._was_ever_shown = False
        self.icon_font = get_segmdl2_icon_font()

        self.window_id = None
        self.left_panel_id = None
        self.right_panel_id = None
        self.right_content_id = None
        self.playback_dock_id = None
        self.playback_controls_group_id = None
        self.canvas_id = None
        self.image_layer = None
        self.overlay_layer = None
        self.image_draw_id = None
        self.resize_handler_id = None
        self.mouse_handler_id = None
        self.texture_id = None
        self.loading_text_id = None
        self.playback_status_text_id = None
        self.play_button_id = None
        self.repeat_button_id = None
        self.frame_slider_id = None
        self.scale_min_input_id = None
        self.scale_max_input_id = None
        self.autoscale_checkbox_id = None
        self.autoscale_grace_input_id = None
        self.mirrored_difference_checkbox_id = None
        self.display_mode_combo_id = None
        self.color_scale_combo_id = None
        self.scope_container_id = None
        self.roi_scaling_panel_id = None
        self.rois_status_text_id = None
        self.rois_panel_id = None
        self.settings_container_id = None
        self.scope_empty_text_id = None
        self.settings_table_id = None
        self.playback_dock_height = 82

        self.width = 1320
        self.height = 860
        self.right_panel_width = 430
        self.feed_width = 860
        self.feed_height = 860
        self.image_width = 1
        self.image_height = 1
        self.image_dirty = False
        self.Andor = PreviewAndorAdapter(self)
        self.display_mode = "Normal"
        self.autoscale_enabled = True
        self.autoscale_grace_percent = 5.0
        self.mirrored_difference_scale = False
        self.lp_filter_enabled = False
        self.lp_filter_cutoff_hz = 5.0
        self._lp_filter_frames_cache = None
        self._lp_filter_cache_key = None
        self.lp_filter_checkbox_id = None
        self.lp_filter_cutoff_input_id = None
        self.drift_correction_enabled = False
        self._drift_shift_cache = {}        # {frame_index: (dy, dx)}
        self._drift_cache_key = None
        self.drift_correction_checkbox_id = None
        self.bg_removal_enabled = False
        self.bg_removal_sigma = 20.0
        self.bg_removal_checkbox_id = None
        self.bg_removal_sigma_input_id = None
        self.crop_percent = 100.0
        self.crop_slider_id = None
        self.colormap_name = "Viridis"
        self._colormap_per_mode = {
            "Normal": self.single_sided_colormap_names[0],
            "Difference": self.double_sided_colormap_names[0],
            "Contrast": self.double_sided_colormap_names[0],
        }
        self.display_max = 65535.0
        self.scale_min = 0.0
        self.scale_max = self.display_max
        self.current_frame_index = 0
        self.playback_fps = 10.0
        self.is_playing = False
        self.repeat_enabled = False
        self.scope_plot_items = {}
        self.scope_layout = ()
        self.scope_y_half_range_volts = 1.0
        self._colormap_lut_cache = {}
        self._current_zero_frame = None
        self._current_rgba = np.zeros((4,), dtype=np.float32)
        self.zero_version = 0
        self.rois = []
        self.roi_index = 0
        self.selected_roi = None
        self.hover_target = None
        self.preview_bounds = None
        self.interaction = None
        self.zoom = 1.0
        self.min_zoom = 1.0
        self.max_zoom = 16.0
        self.view_center_x = 0.5
        self.view_center_y = 0.5
        self.context_menu_roi = None
        self.min_roi_size = 8
        self.handle_half_size = 5
        self.edge_pick_threshold = 10
        self.rois_window = None
        self.section_node_ids = {}

        with dpg.window(
            label="Preview",
            tag=f"{self.tag}_Window",
            width=self.width,
            height=self.height,
            pos=(40, 40),
            no_scrollbar=True,
            no_scroll_with_mouse=True,
        ):
            self.window_id = dpg.last_item()
            dpg.bind_item_theme(self.window_id, acquisition_preview_window_theme)

            with dpg.item_handler_registry(tag=f"{self.tag}_ResizeHandler"):
                self.resize_handler_id = dpg.last_item()
                dpg.add_item_resize_handler(callback=self._on_window_resize)
                dpg.bind_item_handler_registry(self.window_id, f"{self.tag}_ResizeHandler")

            with dpg.group(horizontal=True):
                with dpg.child_window(width=860, height=-1, border=False, no_scrollbar=True, tag=f"{self.tag}_LeftPanel"):
                    self.left_panel_id = dpg.last_item()
                    with dpg.texture_registry(show=False):
                        self.texture_id = dpg.add_dynamic_texture(
                            width=1,
                            height=1,
                            default_value=np.zeros((4,), dtype=np.float32),
                        )
                    with dpg.drawlist(width=1, height=1, tag=f"{self.tag}_Canvas"):
                        self.canvas_id = dpg.last_item()
                        with dpg.draw_layer(tag=f"{self.tag}_ImageLayer"):
                            self.image_layer = dpg.last_item()
                        with dpg.draw_layer(tag=f"{self.tag}_OverlayLayer"):
                            self.overlay_layer = dpg.last_item()
                        self.image_draw_id = dpg.draw_image(
                            texture_tag=self.texture_id,
                            pmin=(0, 0),
                            pmax=(1, 1),
                            parent=self.image_layer,
                        )

                    with dpg.handler_registry(tag=f"{self.tag}_MouseHandler"):
                        self.mouse_handler_id = dpg.last_item()
                        dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Left, callback=self._on_left_mouse_down)
                        dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Middle, callback=self._on_middle_mouse_down)
                        dpg.add_mouse_move_handler(callback=self._on_mouse_move)
                        dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_release)
                        dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_release)
                        dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)
                        dpg.add_key_press_handler(key=dpg.mvKey_Delete, callback=self._on_delete_key_pressed)

                    with dpg.popup(self.canvas_id, mousebutton=dpg.mvMouseButton_Right):
                        dpg.add_button(label="Reset Zoom", width=140, callback=self._reset_zoom)

                with dpg.child_window(width=self.right_panel_width, height=-1, border=True, no_scrollbar=True, no_scroll_with_mouse=True, tag=f"{self.tag}_RightPanel"):
                    self.right_panel_id = dpg.last_item()
                    with dpg.child_window(border=False, autosize_x=True, tag=f"{self.tag}_RightContent"):
                        self.right_content_id = dpg.last_item()
                        self.loading_text_id = dpg.add_text(f"Loading {os.path.basename(self.file_path)}...")
                        dpg.add_separator()

                        with dpg.tree_node(label="Scaling", default_open=True, span_full_width=True):
                            self.section_node_ids["scaling"] = dpg.last_item()
                            self.autoscale_checkbox_id = dpg.add_checkbox(
                                label="Autoscale",
                                default_value=self.autoscale_enabled,
                                callback=self._on_autoscale_changed,
                            )
                            self.scale_min_input_id = dpg.add_input_float(
                                label="Min Z (%)",
                                width=-120,
                                default_value=0.0,
                                min_value=0.0,
                                max_value=100.0,
                                step=0.001,
                                format="%.3f",
                                callback=self._on_scale_limits_changed,
                            )
                            self.scale_max_input_id = dpg.add_input_float(
                                label="Max Z (%)",
                                width=-120,
                                default_value=100.0,
                                min_value=0.0,
                                max_value=100.0,
                                step=0.001,
                                format="%.3f",
                                callback=self._on_scale_limits_changed,
                            )
                            self.autoscale_grace_input_id = dpg.add_input_float(
                                label="Grace (%)",
                                width=-120,
                                default_value=self.autoscale_grace_percent,
                                min_value=0.0,
                                max_value=100.0,
                                step=0.5,
                                callback=self._on_autoscale_grace_changed,
                            )
                            self.mirrored_difference_checkbox_id = dpg.add_checkbox(
                                label="Mirrored",
                                default_value=self.mirrored_difference_scale,
                                callback=self._on_mirrored_difference_changed,
                            )
                            self.display_mode_combo_id = dpg.add_combo(
                                label="Display Mode",
                                items=["Normal", "Difference", "Contrast"],
                                default_value=self.display_mode,
                                width=-120,
                                callback=self._on_display_mode_changed,
                            )
                            self.color_scale_combo_id = dpg.add_combo(
                                label="Color Scale",
                                items=self.get_available_colormap_labels(),
                                default_value=self.get_selected_colormap_label(),
                                width=-120,
                                callback=self._on_colormap_changed,
                            )
                            dpg.add_button(label="Set Zero", width=-1, callback=self._on_set_zero)

                        dpg.add_separator()

                        with dpg.tree_node(label="Signal Processing", default_open=True, span_full_width=True):
                            self.section_node_ids["signal_processing"] = dpg.last_item()
                            self.lp_filter_checkbox_id = dpg.add_checkbox(
                                label="LP Filter",
                                default_value=self.lp_filter_enabled,
                                callback=self._on_lp_filter_enabled_changed,
                            )
                            self.lp_filter_cutoff_input_id = dpg.add_input_float(
                                label="Cutoff (Hz)",
                                width=-120,
                                default_value=self.lp_filter_cutoff_hz,
                                min_value=0.001,
                                min_clamped=True,
                                step=0.5,
                                on_enter=True,
                                callback=self._on_lp_filter_cutoff_changed,
                            )
                            with dpg.item_handler_registry(tag=f"{self.tag}_LpCutoffHandler"):
                                dpg.add_item_deactivated_after_edit_handler(callback=self._on_lp_filter_cutoff_changed)
                            dpg.bind_item_handler_registry(self.lp_filter_cutoff_input_id, f"{self.tag}_LpCutoffHandler")
                            self.drift_correction_checkbox_id = dpg.add_checkbox(
                                label="Drift Correction",
                                default_value=self.drift_correction_enabled,
                                callback=self._on_drift_correction_changed,
                            )
                            self.bg_removal_checkbox_id = dpg.add_checkbox(
                                label="BG Removal",
                                default_value=self.bg_removal_enabled,
                                callback=self._on_bg_removal_enabled_changed,
                            )
                            self.bg_removal_sigma_input_id = dpg.add_input_float(
                                label="BG Sigma (px)",
                                width=-120,
                                default_value=self.bg_removal_sigma,
                                min_value=1.0,
                                max_value=200.0,
                                step=1.0,
                                format="%.1f",
                                on_enter=True,
                                callback=self._on_bg_removal_sigma_changed,
                            )
                            with dpg.item_handler_registry(tag=f"{self.tag}_BgRemovalSigmaHandler"):
                                dpg.add_item_deactivated_after_edit_handler(callback=self._on_bg_removal_sigma_changed)
                            dpg.bind_item_handler_registry(self.bg_removal_sigma_input_id, f"{self.tag}_BgRemovalSigmaHandler")
                            self.crop_slider_id = dpg.add_slider_float(
                                label="Crop (%)",
                                width=-120,
                                default_value=self.crop_percent,
                                min_value=0.0,
                                max_value=100.0,
                                format="%.1f",
                                callback=self._on_crop_changed,
                            )

                        dpg.add_separator()

                        with dpg.tree_node(label="Oscilloscope", default_open=True, span_full_width=True):
                            self.section_node_ids["oscilloscope"] = dpg.last_item()
                            with dpg.child_window(height=260, border=False, autosize_x=True):
                                self.scope_container_id = dpg.last_item()
                                self.scope_empty_text_id = dpg.add_text("No oscilloscope data loaded.")

                        dpg.add_separator()

                        with dpg.tree_node(label="ROI Scaling", default_open=True, span_full_width=True):
                            self.section_node_ids["roi_scaling"] = dpg.last_item()
                            with dpg.child_window(height=180, border=False, autosize_x=True, no_scrollbar=True, no_scroll_with_mouse=True):
                                self.roi_scaling_panel_id = dpg.last_item()

                        dpg.add_separator()

                        with dpg.tree_node(label="ROIs", default_open=True, span_full_width=True):
                            self.section_node_ids["rois"] = dpg.last_item()
                            self.rois_status_text_id = dpg.add_text("0 ROIs")
                            with dpg.child_window(height=1, border=False, autosize_x=True, no_scrollbar=True, no_scroll_with_mouse=True):
                                self.rois_panel_id = dpg.last_item()

                        dpg.add_separator()

                        with dpg.tree_node(label="Settings", default_open=True, span_full_width=True):
                            self.section_node_ids["settings"] = dpg.last_item()
                            with dpg.child_window(height=260, border=False, autosize_x=True):
                                self.settings_container_id = dpg.last_item()

                    with dpg.child_window(height=self.playback_dock_height, border=False, no_scrollbar=True, tag=f"{self.tag}_PlaybackDock"):
                        self.playback_dock_id = dpg.last_item()
                        dpg.bind_item_theme(self.playback_dock_id, preview_playback_dock_theme)
                        self.frame_slider_id = dpg.add_slider_int(
                            label="",
                            width=-1,
                            min_value=0,
                            max_value=0,
                            default_value=0,
                            callback=self._on_slider_changed,
                        )
                        with dpg.group(horizontal=True, horizontal_spacing=8):
                            self.playback_controls_group_id = dpg.last_item()
                            dpg.add_button(label="|<", width=44, callback=self._on_jump_to_start)
                            dpg.add_button(label="<<", width=44, callback=self._on_rewind)
                            self.play_button_id = dpg.add_button(label=self.play_icon, width=36, callback=self._on_toggle_play)
                            self.repeat_button_id = dpg.add_button(label=self.repeat_icon, width=36, callback=self._on_toggle_repeat)
                            dpg.add_button(label=">>", width=44, callback=self._on_fast_forward)
                            dpg.add_button(label=">|", width=44, callback=self._on_jump_to_end)
                            dpg.bind_item_font(self.play_button_id, self.icon_font)
                            dpg.bind_item_font(self.repeat_button_id, self.icon_font)

        self.rois_window = ROIsWindow(
            name="Preview ROIs",
            tag=f"{self.tag}_ROIs",
            width=640,
            height=420,
            state_name=self.rois_state_name,
            controls_parent=self.roi_scaling_panel_id,
            content_parent=self.rois_panel_id,
        )
        self._reset_zoom(redraw=False)
        self.LoadState()
        self._update_settings_controls_state()
        self._update_play_button()
        self._update_repeat_button()
        self._update_layout()
        self._start_threads()

    def _start_threads(self):
        self._load_thread = threading.Thread(target=self._load_data_worker, name=f"{self.tag}_Loader", daemon=True)
        self._load_thread.start()
        self._playback_thread = threading.Thread(target=self._playback_worker, name=f"{self.tag}_Playback", daemon=True)
        self._playback_thread.start()

    def is_closed(self):
        return self._is_closed

    def get_loaded_payload(self):
        return self._loaded_payload

    def _load_data_worker(self):
        try:
            with np.load(self.file_path, allow_pickle=False) as archive:
                payload = self._build_loaded_payload(archive)
        except Exception as exc:
            with self._state_lock:
                self._pending_load_error = str(exc)
            return

        with self._state_lock:
            self._pending_loaded_payload = payload

    def _build_loaded_payload(self, archive):
        acquisitions = np.asarray(archive["camera_acquisitions"])
        timestamps = np.asarray(archive["camera_timestamps"], dtype=np.float64) if "camera_timestamps" in archive else np.arange(len(acquisitions), dtype=np.float64)
        zero_frame = None

        if not np.issubdtype(acquisitions.dtype, np.integer):
            raise ValueError("camera_acquisitions must contain integer image frames.")

        storage_dtype_name = str(acquisitions.dtype)
        display_max = float(get_raw_storage_max_value(storage_dtype_name))

        frame_count = int(acquisitions.shape[0]) if acquisitions.ndim >= 3 else 0
        normal_frames = acquisitions

        if frame_count <= 0:
            raise ValueError("Selected file does not contain camera frames.")

        if timestamps.size != frame_count:
            timestamps = np.arange(frame_count, dtype=np.float64)

        timestamps = timestamps.astype(np.float64, copy=False)
        relative_timestamps = timestamps - float(timestamps[0]) if timestamps.size > 0 else np.arange(frame_count, dtype=np.float64)
        if relative_timestamps.size > 1:
            frame_deltas = np.diff(relative_timestamps)
            frame_deltas = frame_deltas[frame_deltas > 0.0]
            playback_fps = float(1.0 / np.median(frame_deltas)) if frame_deltas.size > 0 else 10.0
        else:
            playback_fps = 10.0

        scope_channels = {}
        for key in archive.files:
            if key.startswith("scope_channel_"):
                channel_name = key[len("scope_channel_"):]
                scope_channels[channel_name] = np.asarray(archive[key], dtype=np.float32)

        scope_plot_channels = self._build_scope_plot_channels(scope_channels, relative_timestamps)
        saved_settings = self._extract_saved_settings(archive)

        if "camera_zero" in archive:
            candidate = np.asarray(archive["camera_zero"])
            if np.issubdtype(candidate.dtype, np.integer):
                zero_frame = candidate

        if zero_frame is None or tuple(np.shape(zero_frame)) != tuple(normal_frames[0].shape):
            zero_frame = np.array(normal_frames[0], copy=True)

        return {
            "file_path": self.file_path,
            "frame_count": frame_count,
            "acquisitions": np.asarray(acquisitions),
            "normal_frames": np.asarray(normal_frames),
            "timestamps": timestamps,
            "relative_timestamps": relative_timestamps,
            "playback_fps": max(1.0, playback_fps),
            "storage_dtype_name": storage_dtype_name,
            "display_max": display_max,
            "zero_frame": np.array(zero_frame, copy=True),
            "scope_channels": scope_channels,
            "scope_plot_channels": scope_plot_channels,
            "saved_settings": saved_settings,
        }

    def _extract_saved_settings(self, archive):
        saved_settings = {}
        for key in archive.files:
            if not key.startswith("settings_"):
                continue
            setting_name = key[len("settings_"):]
            raw_value = np.asarray(archive[key])
            saved_settings[setting_name] = self._coerce_saved_setting_value(raw_value)
        return saved_settings

    def _coerce_saved_setting_value(self, raw_value):
        if getattr(raw_value, "shape", None) == ():
            return raw_value.item()
        if getattr(raw_value, "size", 0) == 1:
            return raw_value.reshape(()).item()
        return raw_value.tolist()

    def _format_setting_label(self, setting_name):
        return str(setting_name).replace("_", " ").strip().title()

    def _format_setting_value(self, value):
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, float):
            return f"{value:.6g}"
        if isinstance(value, (list, tuple)):
            return ", ".join(self._format_setting_value(item) for item in value)
        return str(value)

    def _build_scope_plot_channels(self, scope_channels, relative_timestamps):
        plot_channels = {}
        timestamps = np.asarray(relative_timestamps, dtype=np.float64)
        for channel_name, samples in sorted(scope_channels.items()):
            sample_values = np.asarray(samples, dtype=np.float32).reshape(-1)
            point_count = min(timestamps.size, sample_values.size)
            plot_channels[channel_name] = {
                "x_values": np.array(timestamps[:point_count], copy=True),
                "y_values": np.array(sample_values[:point_count], copy=True),
            }
        return plot_channels

    def _apply_pending_loaded_payload(self):
        with self._state_lock:
            payload = self._pending_loaded_payload
            self._pending_loaded_payload = None
            load_error = self._pending_load_error
            self._pending_load_error = None

        if load_error is not None:
            if dpg.does_item_exist(self.loading_text_id):
                dpg.set_value(self.loading_text_id, f"Failed to load file: {load_error}")
            return

        if payload is None:
            return

        self._loaded_payload = payload
        self._invalidate_lp_cache()
        self._invalidate_drift_cache()
        self.Andor.update_from_payload(payload)
        self.current_frame_index = 0
        self.playback_fps = float(payload["playback_fps"])
        self.display_max = float(payload["display_max"])
        self.scale_min = 0.0
        self.scale_max = self.display_max
        self._current_zero_frame = np.array(payload["zero_frame"], copy=True)
        frame_height = int(payload["normal_frames"].shape[1])
        frame_width = int(payload["normal_frames"].shape[2])
        self._ensure_texture_shape(frame_height, frame_width)
        self._sync_scale_inputs_from_values()
        self._update_settings_controls_state()
        self._initialize_loaded_ui()
        self._mark_image_dirty()   # also calls _push_processed_frame

        if dpg.does_item_exist(self.loading_text_id):
            dpg.set_value(self.loading_text_id, os.path.basename(self.file_path))

        self._request_all_roi_rebuilds(clear_existing=True)

    def _initialize_loaded_ui(self):
        if self._loaded_payload is None:
            return

        self._rebuild_scope_plots()
        self._rebuild_settings_table()
        frame_count = int(self._loaded_payload["frame_count"])
        dpg.configure_item(self.frame_slider_id, min_value=0, max_value=max(0, frame_count - 1))
        dpg.set_value(self.frame_slider_id, 0)
        self._ui_initialized = True

    def _update_repeat_button(self):
        if self.repeat_button_id is not None and dpg.does_item_exist(self.repeat_button_id):
            dpg.configure_item(self.repeat_button_id, label=self.repeat_icon)
            dpg.bind_item_theme(self.repeat_button_id, preview_repeat_button_theme_on if self.repeat_enabled else None)

    def _update_play_button(self):
        if self.play_button_id is not None and dpg.does_item_exist(self.play_button_id):
            dpg.configure_item(self.play_button_id, label=self.pause_icon if self.is_playing else self.play_icon)

    def _playback_worker(self):
        last_advance_time = time.perf_counter()
        while not self._stop_event.is_set():
            if self._loaded_payload is None or not self.is_playing:
                last_advance_time = time.perf_counter()
                self._stop_event.wait(0.03)
                continue

            interval_seconds = 1.0 / max(float(self.playback_fps), 1.0)
            now = time.perf_counter()
            elapsed_seconds = max(0.0, now - last_advance_time)
            if elapsed_seconds < interval_seconds:
                self._stop_event.wait(min(0.01, interval_seconds - elapsed_seconds))
                continue

            step_count = max(1, int(elapsed_seconds / interval_seconds))
            last_advance_time = now
            if not self._advance_frame(step_count):
                self.is_playing = False
                self._update_play_button()

    def _advance_frame(self, step_count):
        if self._loaded_payload is None:
            return False

        frame_count = int(self._loaded_payload["frame_count"])
        if frame_count <= 0:
            return False

        step_count = max(1, int(step_count))
        if self.repeat_enabled and frame_count > 0:
            next_index = (self.current_frame_index + step_count) % frame_count
        else:
            next_index = min(frame_count - 1, self.current_frame_index + step_count)
        if next_index == self.current_frame_index:
            return False

        self.current_frame_index = next_index
        self._mark_image_dirty()
        if self.repeat_enabled:
            return True
        return self.current_frame_index < (frame_count - 1)

    def _mark_image_dirty(self):
        self.image_dirty = True
        self._push_processed_frame()

    def _push_processed_frame(self):
        frame = self._get_display_frame()
        if frame is None:
            return
        processed = np.asarray(frame, dtype=np.float32)
        with self.Andor.processed_frame_condition:
            self.Andor.processed_frame = processed
            self.Andor.processed_frame_idx += 1
            self.Andor.processed_frame_condition.notify_all()

    def _invalidate_lp_cache(self):
        self._lp_filter_frames_cache = None
        self._lp_filter_cache_key = None

    def _get_lp_cache_key(self):
        if self._loaded_payload is None:
            return None
        return (self.lp_filter_cutoff_hz, self.playback_fps, self._loaded_payload["acquisitions"].shape)

    def _ensure_lp_filter_cache(self):
        key = self._get_lp_cache_key()
        if key is None or key == self._lp_filter_cache_key:
            return
        acquisitions = np.asarray(self._loaded_payload["acquisitions"])
        if acquisitions.ndim < 3:
            return
        sample_rate_hz = max(float(self.playback_fps), 1e-6)
        nyquist_hz = sample_rate_hz * 0.5
        cutoff_hz = float(np.clip(self.lp_filter_cutoff_hz, 1e-6, max(1e-6, nyquist_hz * 0.99)))
        k = float(np.tan(np.pi * cutoff_hz / sample_rate_hz))
        norm = 1.0 / (1.0 + k)
        b0, b1, a1 = (k * norm), (k * norm), ((k - 1.0) * norm)
        max_value = float(self.display_max)
        out = np.empty_like(acquisitions)
        prev_in = prev_out = None
        for i in range(len(acquisitions)):
            cur_in = np.asarray(acquisitions[i], dtype=np.float32)
            if prev_in is None:
                out[i] = acquisitions[i]
            else:
                out[i] = np.clip(b0 * cur_in + b1 * prev_in - a1 * prev_out, 0.0, max_value).astype(acquisitions.dtype)
            prev_in = cur_in
            prev_out = np.asarray(out[i], dtype=np.float32)
        self._lp_filter_frames_cache = out
        self._lp_filter_cache_key = key

    def _on_lp_filter_enabled_changed(self, sender, app_data):
        self.lp_filter_enabled = bool(app_data)
        self._mark_image_dirty()
        self._request_all_roi_rebuilds()

    def _on_lp_filter_cutoff_changed(self, sender=None, app_data=None):
        if self.lp_filter_cutoff_input_id is not None and dpg.does_item_exist(self.lp_filter_cutoff_input_id):
            self.lp_filter_cutoff_hz = max(1e-3, float(dpg.get_value(self.lp_filter_cutoff_input_id)))
        self._invalidate_lp_cache()
        self._invalidate_drift_cache()   # drift is computed on LP-filtered frames
        if self.lp_filter_enabled:
            self._mark_image_dirty()
            self._request_all_roi_rebuilds()

    # ── Drift correction ──────────────────────────────────────────────────

    def _invalidate_drift_cache(self):
        self._drift_shift_cache = {}
        self._drift_cache_key = None

    def _get_drift_cache_key(self):
        if self._loaded_payload is None:
            return None
        return (
            self.lp_filter_enabled,
            self.lp_filter_cutoff_hz,
            id(self._current_zero_frame),
            id(self._loaded_payload),
        )

    def _get_drift_reference(self):
        if self._current_zero_frame is not None:
            return np.asarray(self._current_zero_frame, dtype=np.float32)
        if self._loaded_payload is not None:
            frames = self._loaded_payload["normal_frames"]
            if len(frames) > 0:
                return np.asarray(frames[0], dtype=np.float32)
        return None

    @staticmethod
    def _shift_frame_for_display(frame, dy, dx):
        if dy == 0 and dx == 0:
            return frame
        shifted = np.zeros_like(frame)
        h, w = frame.shape[:2]
        src_y = slice(max(0, -dy), h - max(0, dy))
        dst_y = slice(max(0, dy), h - max(0, -dy))
        src_x = slice(max(0, -dx), w - max(0, dx))
        dst_x = slice(max(0, dx), w - max(0, -dx))
        shifted[dst_y, dst_x] = frame[src_y, src_x]
        return shifted

    @staticmethod
    def _shift_frame_subpixel(frame, dy, dx):
        import math
        frame_f32 = np.asarray(frame, dtype=np.float32)
        dy_int = int(math.floor(dy))
        dx_int = int(math.floor(dx))
        fy = float(dy - dy_int)
        fx = float(dx - dx_int)
        s00 = AcquisitionPreviewWindow._shift_frame_for_display(frame_f32, dy_int, dx_int)
        row0 = (1.0 - fx) * s00 + fx * AcquisitionPreviewWindow._shift_frame_for_display(frame_f32, dy_int, dx_int + 1) if fx > 1e-6 else s00
        if fy > 1e-6:
            s10 = AcquisitionPreviewWindow._shift_frame_for_display(frame_f32, dy_int + 1, dx_int)
            row1 = (1.0 - fx) * s10 + fx * AcquisitionPreviewWindow._shift_frame_for_display(frame_f32, dy_int + 1, dx_int + 1) if fx > 1e-6 else s10
            return (1.0 - fy) * row0 + fy * row1
        return row0

    @staticmethod
    def _compute_drift_valid_mask(shape, dy, dx):
        import math
        h, w = shape
        mask = np.ones(shape, dtype=bool)
        top    = max(0, math.ceil(dy))
        bottom = max(0, math.ceil(-dy))
        left   = max(0, math.ceil(dx))
        right  = max(0, math.ceil(-dx))
        if top    > 0: mask[:top, :]        = False
        if bottom > 0: mask[h - bottom:, :] = False
        if left   > 0: mask[:, :left]       = False
        if right  > 0: mask[:, w - right:]  = False
        return mask

    def _get_drift_corrected_frame(self, frame, frame_index):
        """Return (corrected_frame, valid_mask | None)."""
        if not self.drift_correction_enabled:
            return frame, None
        reference = self._get_drift_reference()
        if reference is None:
            return frame, None
        # Invalidate cache when settings/reference/file change
        key = self._get_drift_cache_key()
        if key != self._drift_cache_key:
            self._drift_shift_cache = {}
            self._drift_cache_key = key
        if frame_index not in self._drift_shift_cache:
            from skimage.registration import phase_cross_correlation
            frame_f32 = np.asarray(frame, dtype=np.float32)
            shift, _, _ = phase_cross_correlation(reference, frame_f32, upsample_factor=10, normalization=None)
            max_shift = min(frame_f32.shape[0], frame_f32.shape[1]) // 4
            dy = float(np.clip(float(shift[0]), -max_shift, max_shift))
            dx = float(np.clip(float(shift[1]), -max_shift, max_shift))
            self._drift_shift_cache[frame_index] = (dy, dx)
        dy, dx = self._drift_shift_cache[frame_index]
        corrected = self._shift_frame_subpixel(np.asarray(frame, dtype=np.float32), dy, dx)
        mask = self._compute_drift_valid_mask(corrected.shape, dy, dx)
        return corrected, mask

    def _on_drift_correction_changed(self, sender, app_data):
        self.drift_correction_enabled = bool(app_data)
        self._invalidate_drift_cache()
        self._mark_image_dirty()
        self._request_all_roi_rebuilds()

    # ── Background removal ─────────────────────────────────────────────────────

    @staticmethod
    def _apply_background_removal(frame_f32, sigma):
        from scipy.ndimage import gaussian_filter
        bg = gaussian_filter(np.asarray(frame_f32, dtype=np.float32), sigma=float(sigma))
        return np.clip(frame_f32 - bg, 0.0, None)

    def _on_bg_removal_enabled_changed(self, sender, app_data):
        self.bg_removal_enabled = bool(app_data)
        enabled = self.bg_removal_enabled
        if self.bg_removal_sigma_input_id is not None and dpg.does_item_exist(self.bg_removal_sigma_input_id):
            from Utils.themes import read_only_theme
            dpg.configure_item(self.bg_removal_sigma_input_id, enabled=enabled)
            dpg.bind_item_theme(self.bg_removal_sigma_input_id, None if enabled else read_only_theme)
        self._mark_image_dirty()
        self._request_all_roi_rebuilds()

    def _on_bg_removal_sigma_changed(self, sender, app_data):
        self.bg_removal_sigma = max(1.0, float(dpg.get_value(self.bg_removal_sigma_input_id)))
        dpg.set_value(self.bg_removal_sigma_input_id, self.bg_removal_sigma)
        if self.bg_removal_enabled:
            self._mark_image_dirty()
            self._request_all_roi_rebuilds()

    def _on_crop_changed(self, sender, app_data):
        self.crop_percent = float(np.clip(float(app_data), 0.0, 100.0))
        self._mark_image_dirty()

    @staticmethod
    def _compute_crop_mask(height, width, crop_percent):
        """Return a boolean mask with True inside the centred crop region."""
        if crop_percent >= 100.0:
            return None
        frac = float(np.clip(crop_percent, 0.0, 100.0)) / 100.0
        ch = int(round(height * frac))
        cw = int(round(width * frac))
        top = (height - ch) // 2
        left = (width - cw) // 2
        mask = np.zeros((height, width), dtype=bool)
        mask[top:top + ch, left:left + cw] = True
        return mask

    def _get_frame_count(self):
        if self._loaded_payload is None:
            return 0
        return int(self._loaded_payload["frame_count"])

    def _get_normal_frame(self, frame_index=None):
        if self._loaded_payload is None:
            return None
        frame_index = self.current_frame_index if frame_index is None else int(frame_index)
        frame_index = int(np.clip(frame_index, 0, self._get_frame_count() - 1))
        if self.lp_filter_enabled:
            self._ensure_lp_filter_cache()
            if self._lp_filter_frames_cache is not None:
                return self._lp_filter_frames_cache[frame_index]
        return self._loaded_payload["normal_frames"][frame_index]

    def _compute_difference_frame(self, frame):
        zero_float = np.asarray(self._current_zero_frame, dtype=np.float32)
        if self.bg_removal_enabled:
            zero_float = self._apply_background_removal(zero_float, self.bg_removal_sigma)
        return np.asarray(frame, dtype=np.float32) - zero_float

    def _compute_contrast_frame(self, frame):
        frame_float = np.asarray(frame, dtype=np.float32)
        zero_orig = np.asarray(self._current_zero_frame, dtype=np.float32)
        difference_frame = frame_float - zero_orig
        contrast_frame = np.zeros_like(frame_float, dtype=np.float32)
        np.divide(difference_frame, zero_orig, out=contrast_frame, where=np.abs(zero_orig) > 0.0)
        contrast_frame *= 100.0
        return contrast_frame

    def _get_display_frame(self):
        frame = self._get_normal_frame()
        if frame is None:
            return None
        frame, drift_mask = self._get_drift_corrected_frame(frame, self.current_frame_index)
        # BG removal is skipped for Contrast — contrast already normalises background.
        if self.bg_removal_enabled and not self._is_contrast_mode_active():
            frame = self._apply_background_removal(np.asarray(frame, dtype=np.float32), self.bg_removal_sigma)
        if self._is_difference_mode_active():
            result = self._compute_difference_frame(frame)
        elif self._is_contrast_mode_active():
            result = self._compute_contrast_frame(frame)
        else:
            result = np.asarray(frame, dtype=np.float32) if (drift_mask is not None or self.bg_removal_enabled) else frame
        if drift_mask is not None:
            result = np.array(result, copy=True)
            result[~drift_mask] = 0.0
        # Crop: set pixels outside the centred crop region to 0 as the final display step.
        result_arr = np.asarray(result)
        crop_mask = self._compute_crop_mask(result_arr.shape[0], result_arr.shape[1], self.crop_percent)
        if crop_mask is not None:
            result = np.array(result_arr, copy=True)
            result[~crop_mask] = 0.0
        return result

    def process_analysis_frame(self, frame, zero_frame=None):
        if frame is None:
            return None
        frame_float = np.asarray(frame, dtype=np.float32)
        is_contrast = self._is_contrast_mode_active()
        if self.bg_removal_enabled and not is_contrast:
            frame_float = self._apply_background_removal(frame_float, self.bg_removal_sigma)
        if self._is_difference_mode_active():
            ref_raw = np.asarray(self._current_zero_frame if zero_frame is None else zero_frame, dtype=np.float32)
            ref = self._apply_background_removal(ref_raw, self.bg_removal_sigma) if self.bg_removal_enabled else ref_raw
            return frame_float - ref
        if is_contrast:
            ref_raw = np.asarray(self._current_zero_frame if zero_frame is None else zero_frame, dtype=np.float32)
            difference_frame = np.asarray(frame, dtype=np.float32) - ref_raw
            contrast_frame = np.zeros_like(frame_float, dtype=np.float32)
            np.divide(difference_frame, ref_raw, out=contrast_frame, where=np.abs(ref_raw) > 0.0)
            contrast_frame *= 100.0
            return contrast_frame
        return frame_float

    def extract_roi_frame(self, frame, bounds):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        if frame is None or x2 <= x1 or y2 <= y1:
            return None
        crop = np.array(np.asarray(frame)[y1:y2, x1:x2], copy=True)
        if crop.size <= 0:
            return None
        return crop

    def get_roi_processing_update(self, bounds, include_history=False, include_timestamps=False, history_start_frame_idx=None):
        _ = history_start_frame_idx
        if self._loaded_payload is None:
            return None, 0, False, None, None, None, None, 0

        x1, y1, x2, y2 = self._get_active_roi_bounds(bounds)
        if x2 <= x1 or y2 <= y1:
            return None, 0, False, None, None, None, None, 0

        current_frame_idx = int(self.current_frame_index)
        frame_count = int(self._loaded_payload["frame_count"])
        available_frame_count = frame_count if include_history else current_frame_idx + 1
        first_available_frame_idx = 0
        if include_history:
            start_frame_idx = first_available_frame_idx
        else:
            start_frame_idx = current_frame_idx

        latest_crop = None
        acquisitions = np.asarray(self._loaded_payload["acquisitions"])
        timestamps = np.asarray(self._loaded_payload["relative_timestamps"], dtype=np.float64)
        if 0 <= current_frame_idx < frame_count:
            latest_crop = np.array(acquisitions[current_frame_idx, y1:y2, x1:x2], copy=True)

        zero_crop = None
        if self._current_zero_frame is not None:
            zero_crop = np.array(np.asarray(self._current_zero_frame)[y1:y2, x1:x2], copy=True)

        state_key = (
            str(self.display_mode),
            False,
            0.0,
            int(self.zero_version),
            str(self.Andor.storage_dtype),
            (x1, y1, x2, y2),
        )

        if start_frame_idx > current_frame_idx:
            return None, current_frame_idx, False, None, None, None, None, first_available_frame_idx

        if not include_history:
            return latest_crop, current_frame_idx, False, None, None, zero_crop, state_key, first_available_frame_idx

        raw_frames = np.array(acquisitions[start_frame_idx:available_frame_count], copy=True)
        if raw_frames.size <= 0:
            return None, current_frame_idx, False, None, None, None, None, first_available_frame_idx

        raw_crops = np.array(raw_frames[:, y1:y2, x1:x2], copy=True)
        if raw_crops.size <= 0:
            return None, current_frame_idx, False, None, None, None, None, first_available_frame_idx

        timestamp_values = None
        if include_timestamps:
            timestamp_values = timestamps[start_frame_idx:available_frame_count].tolist()

        return latest_crop, current_frame_idx, True, raw_crops, timestamp_values, zero_crop, state_key, first_available_frame_idx

    def get_roi_frame(self, bounds):
        crop = self.extract_roi_frame(self._get_display_frame(), self._get_active_roi_bounds(bounds))
        return crop, int(self.current_frame_index)

    def _get_active_roi_bounds(self, bounds):
        normalized_bounds = self._normalize_bounds(bounds)
        if self.preview_bounds is None or self.interaction is None:
            return normalized_bounds
        if self.interaction.get("mode") not in ("move", "resize"):
            return normalized_bounds
        if self.selected_roi is None:
            return normalized_bounds
        selected_bounds = self._normalize_bounds(self.selected_roi.get_bounds())
        if normalized_bounds != selected_bounds:
            return normalized_bounds
        return self._normalize_bounds(self.preview_bounds)

    def _is_difference_mode_active(self):
        return self.display_mode == "Difference"

    def _is_contrast_mode_active(self):
        return self.display_mode == "Contrast"

    def _is_signed_zero_reference_mode_active(self):
        return self.display_mode in ("Difference", "Contrast")

    def _get_signed_display_limit(self):
        if self._is_contrast_mode_active():
            return 200.0
        return self.difference_display_limit

    def _get_scale_limit_for_mode(self):
        if self._is_signed_zero_reference_mode_active():
            return float(self._get_signed_display_limit())
        return float(self.display_max)

    def _get_scale_percent_bounds(self):
        if self._is_signed_zero_reference_mode_active():
            return -100.0, 100.0
        return 0.0, 100.0

    def _scale_value_to_percent(self, value):
        scale_limit = max(self._get_scale_limit_for_mode(), 1.0)
        percent = (float(value) / scale_limit) * 100.0
        min_percent, max_percent = self._get_scale_percent_bounds()
        return float(np.clip(percent, min_percent, max_percent))

    def _scale_percent_to_value(self, percent):
        min_percent, max_percent = self._get_scale_percent_bounds()
        normalized_percent = float(np.clip(float(percent), min_percent, max_percent))
        return (normalized_percent / 100.0) * self._get_scale_limit_for_mode()

    def get_scale_min_percent(self):
        return self._scale_value_to_percent(self.scale_min)

    def get_scale_max_percent(self):
        return self._scale_value_to_percent(self.scale_max)

    def _sync_scale_inputs_from_values(self):
        if dpg.does_item_exist(self.scale_min_input_id):
            dpg.set_value(self.scale_min_input_id, self.get_scale_min_percent())
        if dpg.does_item_exist(self.scale_max_input_id):
            dpg.set_value(self.scale_max_input_id, self.get_scale_max_percent())

    def _get_available_colormap_names(self):
        if self._is_signed_zero_reference_mode_active():
            return self.double_sided_colormap_names
        return self.single_sided_colormap_names

    def _get_default_colormap_name(self):
        if self._is_signed_zero_reference_mode_active():
            return self.double_sided_colormap_names[0]
        return self.single_sided_colormap_names[0]

    def _get_colormap_mode_suffix(self):
        if self._is_signed_zero_reference_mode_active():
            return " (Double-Sided)"
        return ""

    def _ensure_valid_colormap_selection(self):
        available_names = self._get_available_colormap_names()
        if self.colormap_name not in available_names:
            self.colormap_name = self._get_default_colormap_name()
        return self.colormap_name

    def get_available_colormap_labels(self):
        suffix = self._get_colormap_mode_suffix()
        return [f"{name}{suffix}" for name in self._get_available_colormap_names()]

    def get_selected_colormap_label(self):
        return f"{self._ensure_valid_colormap_selection()}{self._get_colormap_mode_suffix()}"

    def _parse_colormap_label(self, label):
        label = str(label or "").strip()
        if label.endswith(" (Double-Sided)"):
            label = label[:-15]
        if label in self.colormap_lookup_names or label in self.custom_colormap_hex_scales:
            return label
        return self._get_default_colormap_name()

    def _update_colormap_controls_state(self):
        self._ensure_valid_colormap_selection()
        labels = self.get_available_colormap_labels()
        dpg.configure_item(self.color_scale_combo_id, items=labels)
        dpg.set_value(self.color_scale_combo_id, self.get_selected_colormap_label())

    def _build_custom_colormap_lut(self, colormap_name, samples):
        if colormap_name == "Cividis":
            cividis_map = colormaps["cividis"]
            custom_map = LinearSegmentedColormap.from_list(
                "widefield_cividis_diverging_preview",
                [
                    tuple(np.asarray(cividis_map(0.0), dtype=np.float32)[:3]),
                    (0.94, 0.94, 0.91),
                    tuple(np.asarray(cividis_map(1.0), dtype=np.float32)[:3]),
                ],
            )
            return np.asarray(
                custom_map(np.linspace(0.0, 1.0, int(samples), dtype=np.float32)),
                dtype=np.float32,
            )[..., :3]

        custom_stops = [to_rgb(color) for color in self.custom_colormap_hex_scales[colormap_name]]
        custom_map = LinearSegmentedColormap.from_list(
            f"widefield_preview_{colormap_name.lower()}_diverging",
            custom_stops,
        )
        return np.asarray(
            custom_map(np.linspace(0.0, 1.0, int(samples), dtype=np.float32)),
            dtype=np.float32,
        )[..., :3]

    def _get_colormap_lut(self, *, double_sided=False, samples=512):
        selected_colormap = self._ensure_valid_colormap_selection()
        cache_key = (selected_colormap, bool(double_sided), int(samples))
        cached = self._colormap_lut_cache.get(cache_key)
        if cached is not None:
            return cached

        if selected_colormap in self.custom_colormap_hex_scales or (double_sided and selected_colormap == "Cividis"):
            lut = self._build_custom_colormap_lut(selected_colormap, samples)
        else:
            cmap_name = self.colormap_lookup_names[selected_colormap]
            lut = np.asarray(
                colormaps[cmap_name](np.linspace(0.0, 1.0, int(samples), dtype=np.float32)),
                dtype=np.float32,
            )[..., :3]

        self._colormap_lut_cache[cache_key] = lut
        return lut

    def _apply_colormap(self, normalized, *, double_sided=False):
        lut = self._get_colormap_lut(double_sided=double_sided)
        indices = np.clip((normalized * (lut.shape[0] - 1)).astype(np.int32), 0, lut.shape[0] - 1)
        rgba = np.empty((normalized.shape[0], normalized.shape[1], 4), dtype=np.float32)
        rgba[..., :3] = lut[indices]
        rgba[..., 3] = 1.0
        return rgba.flatten()

    def _normalize_double_sided_frame(self, frame, min_value, max_value):
        normalized = np.full(frame.shape, 0.5, dtype=np.float32)
        negative_extent = abs(float(min(min_value, 0.0)))
        positive_extent = max(float(max_value), 0.0)

        negative_mask = frame < 0.0
        if negative_extent > 0.0 and np.any(negative_mask):
            normalized[negative_mask] = 0.5 * (1.0 + (frame[negative_mask] / negative_extent))

        positive_mask = frame > 0.0
        if positive_extent > 0.0 and np.any(positive_mask):
            normalized[positive_mask] = 0.5 + (0.5 * (frame[positive_mask] / positive_extent))

        return np.clip(normalized, 0.0, 1.0)

    def _get_autoscale_range(self, frame, *, signed_mode=False, crop_mask=None):
        frame_array = np.asarray(frame, dtype=np.float32)
        if frame_array.size <= 0:
            if signed_mode:
                return -1.0, 1.0
            return 0.0, 1.0

        # Only consider pixels inside the crop region when a mask is provided.
        if crop_mask is not None and crop_mask.shape == frame_array.shape:
            candidate_values = frame_array[crop_mask]
        else:
            candidate_values = frame_array.ravel()

        finite_values = candidate_values[np.isfinite(candidate_values)]
        if finite_values.size <= 0:
            if signed_mode:
                return -1.0, 1.0
            return 0.0, 1.0

        data_min = float(np.min(finite_values))
        data_max = float(np.max(finite_values))
        if data_max <= data_min:
            data_max = data_min + 1.0

        lower_percentile = float(np.percentile(finite_values, self.autoscale_lower_percentile))
        upper_percentile = float(np.percentile(finite_values, self.autoscale_upper_percentile))

        if upper_percentile <= lower_percentile:
            lower_percentile = data_min
            upper_percentile = data_max

        grace_fraction = self.autoscale_grace_percent / 100.0
        padding = (upper_percentile - lower_percentile) * grace_fraction
        lower_bound = lower_percentile - padding
        upper_bound = upper_percentile + padding

        if signed_mode:
            signed_display_limit = self._get_signed_display_limit()
            lower_bound = max(-signed_display_limit, lower_bound)
            upper_bound = min(signed_display_limit, upper_bound)
        else:
            lower_bound = max(0.0, lower_bound)
            upper_bound = min(float(self.display_max), upper_bound)

        if upper_bound <= lower_bound:
            upper_bound = lower_bound + 1.0

        return float(lower_bound), float(upper_bound)

    def _compute_display_bounds(self, frame=None):
        if frame is None:
            frame = self._get_display_frame()

        # Build crop mask once so it can be forwarded to autoscale.
        crop_mask = None
        if frame is not None:
            frame_arr = np.asarray(frame)
            crop_mask = self._compute_crop_mask(frame_arr.shape[0], frame_arr.shape[1], self.crop_percent)

        if self._is_signed_zero_reference_mode_active():
            signed_display_limit = self._get_signed_display_limit()
            if frame is None:
                min_value = -1.0
                max_value = 1.0
            elif self.autoscale_enabled:
                min_value, max_value = self._get_autoscale_range(frame, signed_mode=True, crop_mask=crop_mask)
            else:
                min_value = float(np.clip(self.scale_min, -signed_display_limit, signed_display_limit))
                max_value = float(np.clip(self.scale_max, -signed_display_limit, signed_display_limit))

            if self.mirrored_difference_scale:
                amplitude = max(abs(min_value), abs(max_value))
                if amplitude <= 0.0:
                    amplitude = 1.0
                min_value = -amplitude
                max_value = amplitude

            if max_value <= min_value:
                max_value = min_value + 1.0

            return float(min_value), float(max_value)

        if frame is None:
            min_value = 0.0
            max_value = float(self.display_max)
        elif self.autoscale_enabled:
            min_value, max_value = self._get_autoscale_range(frame, signed_mode=False, crop_mask=crop_mask)
        else:
            min_value = max(0.0, float(self.scale_min))
            max_value = min(float(self.display_max), max(float(self.scale_max), 1.0))

        if max_value <= min_value:
            max_value = min_value + 1.0

        return float(min_value), float(max_value)

    def _update_settings_controls_state(self):
        is_signed_mode = self._is_signed_zero_reference_mode_active()
        input_min, input_max = self._get_scale_percent_bounds()
        dpg.configure_item(self.scale_min_input_id, min_value=input_min, max_value=input_max)
        dpg.configure_item(self.scale_max_input_id, min_value=input_min, max_value=input_max)
        self._sync_scale_inputs_from_values()

        if self.autoscale_enabled:
            dpg.configure_item(self.scale_min_input_id, enabled=False)
            dpg.configure_item(self.scale_max_input_id, enabled=False)
            dpg.bind_item_theme(self.scale_min_input_id, read_only_theme)
            dpg.bind_item_theme(self.scale_max_input_id, read_only_theme)
            dpg.configure_item(self.autoscale_grace_input_id, enabled=True)
            dpg.bind_item_theme(self.autoscale_grace_input_id, None)
        else:
            dpg.configure_item(self.scale_min_input_id, enabled=True)
            dpg.configure_item(self.scale_max_input_id, enabled=True)
            dpg.bind_item_theme(self.scale_min_input_id, None)
            dpg.bind_item_theme(self.scale_max_input_id, None)
            dpg.configure_item(self.autoscale_grace_input_id, enabled=False)
            dpg.bind_item_theme(self.autoscale_grace_input_id, read_only_theme)

        mirrored_enabled = is_signed_mode and not self.autoscale_enabled
        dpg.configure_item(self.mirrored_difference_checkbox_id, enabled=mirrored_enabled, show=is_signed_mode)
        if mirrored_enabled:
            dpg.bind_item_theme(self.mirrored_difference_checkbox_id, None)
        else:
            dpg.bind_item_theme(self.mirrored_difference_checkbox_id, read_only_theme if is_signed_mode else None)

        self._update_colormap_controls_state()

    def _frame_to_rgba(self, frame):
        if frame is None:
            return np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32)

        min_value, max_value = self._compute_display_bounds(frame)
        if self._is_signed_zero_reference_mode_active():
            signed_frame = np.asarray(frame, dtype=np.float32)
            normalized = self._normalize_double_sided_frame(signed_frame, min_value, max_value)
            return self._apply_colormap(normalized, double_sided=True)

        scaled = np.clip((np.asarray(frame, dtype=np.float32) - min_value) / max(max_value - min_value, 1e-6), 0.0, 1.0)
        return self._apply_colormap(scaled, double_sided=False)

    def frame_to_rgba(self, frame):
        return self._frame_to_rgba(frame)

    def _ensure_texture_shape(self, height, width):
        height = max(1, int(height))
        width = max(1, int(width))
        if height == self.image_height and width == self.image_width and dpg.does_item_exist(self.texture_id):
            return

        self.image_height = height
        self.image_width = width
        if dpg.does_item_exist(self.texture_id):
            dpg.delete_item(self.texture_id)

        with dpg.texture_registry(show=False):
            self.texture_id = dpg.add_dynamic_texture(
                width=self.image_width,
                height=self.image_height,
                default_value=np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32),
            )

        dpg.configure_item(self.image_draw_id, texture_tag=self.texture_id)
        self._update_image_draw_transform  ()
        self._update_layout()

    def _update_layout(self):
        if not dpg.does_item_exist(self.window_id):
            return

        window_width, window_height = dpg.get_item_rect_size(self.window_id)
        content_width = max(320, int(window_width) - 16)
        content_height = max(320, int(window_height) - 38)
        left_width = max(240, content_width - self.right_panel_width - 12)
        self.feed_width = left_width
        self.feed_height = content_height
        right_inner_width = max(1, self.right_panel_width - 16)
        dock_height = min(self.playback_dock_height, content_height)
        right_content_height = max(1, content_height - dock_height - 8)

        dpg.configure_item(self.left_panel_id, width=left_width, height=content_height)
        dpg.configure_item(self.right_panel_id, width=self.right_panel_width, height=content_height)
        if self.right_content_id is not None and dpg.does_item_exist(self.right_content_id):
            dpg.configure_item(self.right_content_id, width=right_inner_width, height=right_content_height)
        if self.playback_dock_id is not None and dpg.does_item_exist(self.playback_dock_id):
            dpg.configure_item(self.playback_dock_id, width=right_inner_width, height=dock_height)
        controls_width = 288
        controls_x = max(10, int((right_inner_width - controls_width) / 2.0))
        controls_y = max(36, dock_height - 40)
        if self.frame_slider_id is not None and dpg.does_item_exist(self.frame_slider_id):
            dpg.configure_item(self.frame_slider_id, width=controls_width)
            dpg.set_item_pos(self.frame_slider_id, (controls_x, max(8, controls_y - 28)))
        if self.playback_controls_group_id is not None and dpg.does_item_exist(self.playback_controls_group_id):
            dpg.set_item_pos(self.playback_controls_group_id, (controls_x, controls_y))
        self._update_roi_panel_height()
        dpg.configure_item(self.canvas_id, width=max(1, left_width), height=max(1, content_height))
        self._update_image_draw_transform()
        self._redraw_overlay()

    def _update_roi_panel_height(self):
        if self.rois_panel_id is None or not dpg.does_item_exist(self.rois_panel_id):
            return
        if self.rois_window is None or not hasattr(self.rois_window, "get_required_content_height"):
            return
        dpg.configure_item(self.rois_panel_id, height=max(1, int(self.rois_window.get_required_content_height())))

    def _on_window_resize(self, sender=None, app_data=None):
        self._update_layout()

    def _get_canvas_size(self):
        width, height = dpg.get_item_rect_size(self.canvas_id)
        return max(1, int(width)), max(1, int(height))

    def _get_view_size(self):
        return self.image_width / self.zoom, self.image_height / self.zoom

    def _clamp_view_center(self):
        view_width, view_height = self._get_view_size()
        half_view_width = view_width / 2.0
        half_view_height = view_height / 2.0
        self.view_center_x = float(np.clip(self.view_center_x, half_view_width, self.image_width - half_view_width))
        self.view_center_y = float(np.clip(self.view_center_y, half_view_height, self.image_height - half_view_height))

    def _get_view_bounds(self):
        self._clamp_view_center()
        view_width, view_height = self._get_view_size()
        left = self.view_center_x - (view_width / 2.0)
        top = self.view_center_y - (view_height / 2.0)
        right = left + view_width
        bottom = top + view_height
        return left, top, right, bottom

    def _update_image_draw_transform(self):
        if not dpg.does_item_exist(self.image_draw_id):
            return
        canvas_width, canvas_height = self._get_canvas_size()
        left, top, right, bottom = self._get_view_bounds()
        dpg.configure_item(
            self.image_draw_id,
            pmin=(0, 0),
            pmax=(canvas_width, canvas_height),
            uv_min=(left / max(1.0, float(self.image_width)), top / max(1.0, float(self.image_height))),
            uv_max=(right / max(1.0, float(self.image_width)), bottom / max(1.0, float(self.image_height))),
        )

    def _reset_zoom(self, sender=None, app_data=None, user_data=None, redraw=True):
        self.zoom = 1.0
        self.view_center_x = self.image_width / 2.0
        self.view_center_y = self.image_height / 2.0
        self._clamp_view_center()
        self._update_image_draw_transform()
        if redraw:
            self._redraw_overlay()

    def _get_mouse_local(self):
        if self._is_canvas_hovered_raw():
            draw_x, draw_y = dpg.get_drawing_mouse_pos()
            return float(draw_x), float(draw_y)

        mouse_x, mouse_y = dpg.get_mouse_pos()
        rect_min_x, rect_min_y = dpg.get_item_rect_min(self.canvas_id)
        return mouse_x - rect_min_x, mouse_y - rect_min_y

    def _is_canvas_hovered_raw(self):
        return dpg.is_item_hovered(self.canvas_id)

    def _point_in_canvas(self, local_point):
        local_x, local_y = local_point
        canvas_width, canvas_height = self._get_canvas_size()
        return 0 <= local_x <= canvas_width and 0 <= local_y <= canvas_height

    def _is_canvas_hovered(self):
        return self._is_canvas_hovered_raw() and self._point_in_canvas(self._get_mouse_local())

    def _display_to_image(self, local_point):
        local_x, local_y = local_point
        canvas_width, canvas_height = self._get_canvas_size()
        left, top, right, bottom = self._get_view_bounds()
        image_x = int(round(left + ((local_x / max(1.0, canvas_width)) * (right - left))))
        image_y = int(round(top + ((local_y / max(1.0, canvas_height)) * (bottom - top))))
        image_x = int(np.clip(image_x, 0, self.image_width))
        image_y = int(np.clip(image_y, 0, self.image_height))
        return image_x, image_y

    def _image_to_display(self, image_point):
        image_x, image_y = image_point
        canvas_width, canvas_height = self._get_canvas_size()
        left, top, right, bottom = self._get_view_bounds()
        display_x = ((image_x - left) / max(1e-6, (right - left))) * canvas_width
        display_y = ((image_y - top) / max(1e-6, (bottom - top))) * canvas_height
        return display_x, display_y

    def _normalize_bounds(self, bounds):
        x1, y1, x2, y2 = bounds
        x1, x2 = sorted((int(round(x1)), int(round(x2))))
        y1, y2 = sorted((int(round(y1)), int(round(y2))))
        x1 = int(np.clip(x1, 0, self.image_width))
        x2 = int(np.clip(x2, 0, self.image_width))
        y1 = int(np.clip(y1, 0, self.image_height))
        y2 = int(np.clip(y2, 0, self.image_height))
        return x1, y1, x2, y2

    def _bounds_to_display(self, bounds):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        display_x1, display_y1 = self._image_to_display((x1, y1))
        display_x2, display_y2 = self._image_to_display((x2, y2))
        return display_x1, display_y1, display_x2, display_y2

    def _is_valid_bounds(self, bounds):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        return (x2 - x1) >= self.min_roi_size and (y2 - y1) >= self.min_roi_size

    def _is_square_constraint_active(self):
        return dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)

    def _is_center_resize_active(self):
        return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)

    def _clamp_square_side(self, anchor_x, anchor_y, sign_x, sign_y, desired_side):
        max_side_x = self.image_width if sign_x == 0 else (self.image_width - anchor_x if sign_x > 0 else anchor_x)
        max_side_y = self.image_height if sign_y == 0 else (self.image_height - anchor_y if sign_y > 0 else anchor_y)
        return int(max(self.min_roi_size, min(desired_side, max_side_x, max_side_y)))

    def _square_bounds_from_anchor(self, anchor_point, current_point):
        anchor_x, anchor_y = anchor_point
        current_x, current_y = current_point
        delta_x = current_x - anchor_x
        delta_y = current_y - anchor_y
        if delta_x == 0 and delta_y == 0:
            return anchor_x, anchor_y, anchor_x, anchor_y
        sign_x = 1 if delta_x >= 0 else -1
        sign_y = 1 if delta_y >= 0 else -1
        desired_side = int(max(abs(delta_x), abs(delta_y), self.min_roi_size))
        side = self._clamp_square_side(anchor_x, anchor_y, sign_x, sign_y, desired_side)
        return self._normalize_bounds((anchor_x, anchor_y, anchor_x + (sign_x * side), anchor_y + (sign_y * side)))

    def _resize_bounds(self, bounds, current_point, handle):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        current_x, current_y = current_point
        if "w" in handle:
            x1 = int(np.clip(current_x, 0, x2 - self.min_roi_size))
        if "e" in handle:
            x2 = int(np.clip(current_x, x1 + self.min_roi_size, self.image_width))
        if "n" in handle:
            y1 = int(np.clip(current_y, 0, y2 - self.min_roi_size))
        if "s" in handle:
            y2 = int(np.clip(current_y, y1 + self.min_roi_size, self.image_height))
        return x1, y1, x2, y2

    def _square_bounds_from_resize(self, bounds, current_point, handle):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        current_x, current_y = current_point
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0

        if handle == "nw":
            return self._square_bounds_from_anchor((x2, y2), current_point)
        if handle == "ne":
            anchor_x, anchor_y = x1, y2
            desired_side = int(max(abs(current_x - anchor_x), abs(current_y - anchor_y), self.min_roi_size))
            side = self._clamp_square_side(anchor_x, anchor_y, 1, -1, desired_side)
            return self._normalize_bounds((anchor_x, anchor_y, anchor_x + side, anchor_y - side))
        if handle == "sw":
            anchor_x, anchor_y = x2, y1
            desired_side = int(max(abs(current_x - anchor_x), abs(current_y - anchor_y), self.min_roi_size))
            side = self._clamp_square_side(anchor_x, anchor_y, -1, 1, desired_side)
            return self._normalize_bounds((anchor_x, anchor_y, anchor_x - side, anchor_y + side))
        if handle == "se":
            return self._square_bounds_from_anchor((x1, y1), current_point)

        if handle in ("e", "w"):
            new_width = max(self.min_roi_size, abs(current_x - (x1 if handle == "e" else x2)))
            max_width = self.image_width - x1 if handle == "e" else x2
            side = int(min(new_width, max_width))
            top = int(round(center_y - side / 2.0))
            bottom = top + side
            if top < 0:
                top = 0
                bottom = side
            if bottom > self.image_height:
                bottom = self.image_height
                top = bottom - side
            if handle == "e":
                return self._normalize_bounds((x1, top, x1 + side, bottom))
            return self._normalize_bounds((x2 - side, top, x2, bottom))

        if handle in ("n", "s"):
            new_height = max(self.min_roi_size, abs(current_y - (y2 if handle == "n" else y1)))
            max_height = y2 if handle == "n" else self.image_height - y1
            side = int(min(new_height, max_height))
            left = int(round(center_x - side / 2.0))
            right = left + side
            if left < 0:
                left = 0
                right = side
            if right > self.image_width:
                right = self.image_width
                left = right - side
            if handle == "n":
                return self._normalize_bounds((left, y2 - side, right, y2))
            return self._normalize_bounds((left, y1, right, y1 + side))

        return self._resize_bounds(bounds, current_point, handle)

    def _move_bounds(self, bounds, delta_x, delta_y):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        width = x2 - x1
        height = y2 - y1
        new_x1 = int(np.clip(x1 + delta_x, 0, self.image_width - width))
        new_y1 = int(np.clip(y1 + delta_y, 0, self.image_height - height))
        return new_x1, new_y1, new_x1 + width, new_y1 + height

    def _center_resize_bounds(self, bounds, current_point, handle, square=False):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        current_x, current_y = current_point
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        current_half_width = (x2 - x1) / 2.0
        current_half_height = (y2 - y1) / 2.0
        min_half_size = self.min_roi_size / 2.0
        max_half_width = min(center_x, self.image_width - center_x)
        max_half_height = min(center_y, self.image_height - center_y)

        if square:
            if handle in ("e", "w"):
                desired_half_side = abs(current_x - center_x)
            elif handle in ("n", "s"):
                desired_half_side = abs(current_y - center_y)
            else:
                desired_half_side = max(abs(current_x - center_x), abs(current_y - center_y))
            half_side = min(max(desired_half_side, min_half_size), max_half_width, max_half_height)
            half_width = half_side
            half_height = half_side
        else:
            if handle in ("e", "w"):
                half_width = min(max(abs(current_x - center_x), min_half_size), max_half_width)
                half_height = min(current_half_height, max_half_height)
            elif handle in ("n", "s"):
                half_width = min(current_half_width, max_half_width)
                half_height = min(max(abs(current_y - center_y), min_half_size), max_half_height)
            else:
                half_width = min(max(abs(current_x - center_x), min_half_size), max_half_width)
                half_height = min(max(abs(current_y - center_y), min_half_size), max_half_height)

        return self._normalize_bounds((
            int(round(center_x - half_width)),
            int(round(center_y - half_height)),
            int(round(center_x + half_width)),
            int(round(center_y + half_height)),
        ))

    def _pan_view(self, delta_local_x, delta_local_y, anchor_center):
        canvas_width, canvas_height = self._get_canvas_size()
        view_width, view_height = self._get_view_size()
        self.view_center_x = anchor_center[0] - ((delta_local_x / max(1.0, canvas_width)) * view_width)
        self.view_center_y = anchor_center[1] - ((delta_local_y / max(1.0, canvas_height)) * view_height)
        self._clamp_view_center()
        self._update_image_draw_transform()

    def _set_zoom_at_point(self, zoom_delta, local_point):
        if not self._point_in_canvas(local_point):
            return
        old_zoom = self.zoom
        if zoom_delta > 0:
            new_zoom = min(self.max_zoom, self.zoom * (1.15 ** zoom_delta))
        else:
            new_zoom = max(self.min_zoom, self.zoom / (1.15 ** abs(zoom_delta)))
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        canvas_width, canvas_height = self._get_canvas_size()
        old_left, old_top, old_right, old_bottom = self._get_view_bounds()
        mouse_image_x = old_left + ((local_point[0] / max(1.0, canvas_width)) * (old_right - old_left))
        mouse_image_y = old_top + ((local_point[1] / max(1.0, canvas_height)) * (old_bottom - old_top))
        self.zoom = new_zoom
        new_view_width, new_view_height = self._get_view_size()
        new_left = mouse_image_x - ((local_point[0] / max(1.0, canvas_width)) * new_view_width)
        new_top = mouse_image_y - ((local_point[1] / max(1.0, canvas_height)) * new_view_height)
        self.view_center_x = new_left + (new_view_width / 2.0)
        self.view_center_y = new_top + (new_view_height / 2.0)
        self._clamp_view_center()
        self._update_image_draw_transform()
        self._redraw_overlay()

    def _get_handle_positions(self, display_bounds):
        x1, y1, x2, y2 = display_bounds
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        return {
            "nw": (x1, y1),
            "n": (center_x, y1),
            "ne": (x2, y1),
            "e": (x2, center_y),
            "se": (x2, y2),
            "s": (center_x, y2),
            "sw": (x1, y2),
            "w": (x1, center_y),
        }

    def _hit_test(self, local_point):
        local_x, local_y = local_point
        for roi in reversed(self.rois):
            display_bounds = self._bounds_to_display(roi.get_bounds())
            x1, y1, x2, y2 = display_bounds
            if x2 <= x1 or y2 <= y1:
                continue
            for handle_name, handle_pos in self._get_handle_positions(display_bounds).items():
                handle_x, handle_y = handle_pos
                if abs(local_x - handle_x) <= self.handle_half_size and abs(local_y - handle_y) <= self.handle_half_size:
                    return roi, handle_name
            inside_x = x1 <= local_x <= x2
            inside_y = y1 <= local_y <= y2
            if inside_y and abs(local_x - x1) <= self.edge_pick_threshold:
                return roi, "w"
            if inside_y and abs(local_x - x2) <= self.edge_pick_threshold:
                return roi, "e"
            if inside_x and abs(local_y - y1) <= self.edge_pick_threshold:
                return roi, "n"
            if inside_x and abs(local_y - y2) <= self.edge_pick_threshold:
                return roi, "s"
            if inside_x and inside_y:
                return roi, "body"
        return None

    def _create_roi(self, bounds):
        normalized_bounds = self._normalize_bounds(bounds)
        if not self._is_valid_bounds(normalized_bounds):
            return None
        roi_tag = f"{self.tag}_ROI_{self.roi_index}"
        roi_name = f"ROI {self.roi_index + 1}"
        roi = RegionOfInterest(name=roi_name, tag=roi_tag, parent=self, rois_window=self.rois_window, bounds=normalized_bounds)
        self.roi_index += 1
        self.rois.append(roi)
        self.selected_roi = roi
        self.rois_window.invalidate_autoscale_cache(pending_tags=[roi.tag])
        roi.request_trace_rebuild(clear_existing=True)
        self.rois_window.rebuild_layout(self.rois)
        return roi

    def _close_roi(self, tag):
        roi_to_remove = next((roi for roi in self.rois if roi.tag == tag), None)
        if roi_to_remove is None:
            return
        roi_to_remove.close()
        self.rois.remove(roi_to_remove)
        self.rois_window.rebuild_layout(self.rois)
        self.rois_window.invalidate_autoscale_cache()
        if self.selected_roi is roi_to_remove:
            self.selected_roi = None
        if self.hover_target is not None and self.hover_target[0] is roi_to_remove:
            self.hover_target = None

    def close_roi(self, tag):
        self._close_roi(tag)
        self._redraw_overlay()

    def _request_all_roi_rebuilds(self, clear_existing=False):
        if self.rois_window is None:
            return
        self.rois_window.invalidate_autoscale_cache(pending_tags=[roi.tag for roi in self.rois])
        for roi in self.rois:
            roi.request_trace_rebuild(clear_existing=clear_existing)

    def get_current_roi_marker_x(self):
        payload = self._loaded_payload
        if payload is None:
            return None
        timestamps = np.asarray(payload.get("relative_timestamps", []), dtype=np.float64)
        if timestamps.size <= 0:
            return float(self.current_frame_index)
        marker_index = int(np.clip(self.current_frame_index, 0, timestamps.size - 1))
        return float(timestamps[marker_index])

    def _redraw_overlay(self):
        if self.overlay_layer is None or not dpg.does_item_exist(self.overlay_layer):
            return
        dpg.delete_item(self.overlay_layer, children_only=True)
        for roi in self.rois:
            bounds = roi.get_bounds()
            if (
                self.preview_bounds is not None
                and self.interaction is not None
                and self.interaction.get("mode") in ("move", "resize")
                and roi is self.selected_roi
            ):
                bounds = self.preview_bounds
            display_bounds = self._bounds_to_display(bounds)
            x1, y1, x2, y2 = display_bounds
            is_selected = roi is self.selected_roi
            is_hovered = self.hover_target is not None and self.hover_target[0] is roi
            border_color = (0, 220, 140, 255) if is_selected else (255, 190, 40, 220)
            fill_color = (0, 220, 140, 35) if is_selected else (255, 190, 40, 25)
            dpg.draw_rectangle((x1, y1), (x2, y2), color=border_color, fill=fill_color, thickness=2, parent=self.overlay_layer)
            dpg.draw_text((x1 + 6, max(0, y1 - 18)), roi.name, color=border_color, size=14, parent=self.overlay_layer)
            if is_selected or is_hovered:
                for handle_x, handle_y in self._get_handle_positions(display_bounds).values():
                    dpg.draw_rectangle(
                        (handle_x - self.handle_half_size, handle_y - self.handle_half_size),
                        (handle_x + self.handle_half_size, handle_y + self.handle_half_size),
                        color=(20, 20, 20, 255),
                        fill=(245, 245, 245, 255),
                        thickness=1,
                        parent=self.overlay_layer,
                    )
        if self.preview_bounds is not None and self._is_valid_bounds(self.preview_bounds):
            x1, y1, x2, y2 = self._bounds_to_display(self.preview_bounds)
            dpg.draw_rectangle((x1, y1), (x2, y2), color=(80, 170, 255, 255), fill=(80, 170, 255, 25), thickness=2, parent=self.overlay_layer)

    def _on_left_mouse_down(self, sender, app_data):
        self._on_mouse_down(dpg.mvMouseButton_Left)

    def _on_middle_mouse_down(self, sender, app_data):
        self._on_mouse_down(dpg.mvMouseButton_Middle)

    def _on_mouse_down(self, mouse_button):
        if self.interaction is not None or not self._is_canvas_hovered():
            return
        is_left_button = mouse_button == dpg.mvMouseButton_Left
        is_middle_button = mouse_button == dpg.mvMouseButton_Middle
        local_point = self._get_mouse_local()
        hit = self._hit_test(local_point)
        image_point = self._display_to_image(local_point)
        wants_pan = is_middle_button or (is_left_button and self._is_center_resize_active())
        if hit is None and wants_pan and self.zoom > self.min_zoom:
            self.interaction = {
                "mode": "pan",
                "start_local": local_point,
                "anchor_center": (self.view_center_x, self.view_center_y),
            }
            self._redraw_overlay()
            return
        if not is_left_button:
            return
        if hit is None:
            self.selected_roi = None
            self.preview_bounds = (image_point[0], image_point[1], image_point[0], image_point[1])
            self.interaction = {"mode": "create", "start_image": image_point}
        else:
            roi, handle = hit
            self.selected_roi = roi
            self.preview_bounds = roi.get_bounds()
            self.interaction = {
                "mode": "move" if handle == "body" else "resize",
                "roi": roi,
                "handle": handle,
                "anchor_bounds": roi.get_bounds(),
                "start_image": image_point,
            }
        self._redraw_overlay()

    def _on_mouse_move(self, sender, app_data):
        local_point = self._get_mouse_local()
        if self.interaction is None:
            next_hover_target = self._hit_test(local_point) if self._point_in_canvas(local_point) else None
            if next_hover_target != self.hover_target:
                self.hover_target = next_hover_target
                self._redraw_overlay()
            return

        current_image = self._display_to_image(local_point)
        mode = self.interaction["mode"]
        if mode == "create":
            start_x, start_y = self.interaction["start_image"]
            if self._is_square_constraint_active():
                self.preview_bounds = self._square_bounds_from_anchor((start_x, start_y), current_image)
            else:
                self.preview_bounds = self._normalize_bounds((start_x, start_y, current_image[0], current_image[1]))
        elif mode == "move":
            start_x, start_y = self.interaction["start_image"]
            delta_x = current_image[0] - start_x
            delta_y = current_image[1] - start_y
            self.preview_bounds = self._move_bounds(self.interaction["anchor_bounds"], delta_x, delta_y)
        elif mode == "pan":
            start_local_x, start_local_y = self.interaction["start_local"]
            self._pan_view(local_point[0] - start_local_x, local_point[1] - start_local_y, self.interaction["anchor_center"])
        elif mode == "resize":
            if self._is_center_resize_active() and self._is_square_constraint_active():
                new_bounds = self._center_resize_bounds(self.interaction["anchor_bounds"], current_image, self.interaction["handle"], square=True)
            elif self._is_center_resize_active():
                new_bounds = self._center_resize_bounds(self.interaction["anchor_bounds"], current_image, self.interaction["handle"], square=False)
            elif self._is_square_constraint_active():
                new_bounds = self._square_bounds_from_resize(self.interaction["anchor_bounds"], current_image, self.interaction["handle"])
            else:
                new_bounds = self._resize_bounds(self.interaction["anchor_bounds"], current_image, self.interaction["handle"])
            self.preview_bounds = new_bounds
        self._redraw_overlay()

    def _on_mouse_release(self, sender, app_data):
        if self.interaction is None:
            return
        if self.interaction["mode"] == "create" and self.preview_bounds is not None:
            self._create_roi(self.preview_bounds)
            self.preview_bounds = None
        elif self.interaction["mode"] in ("move", "resize"):
            if self.preview_bounds is not None and self._is_valid_bounds(self.preview_bounds):
                self.interaction["roi"].set_bounds(self.preview_bounds)
                self.interaction["roi"].request_trace_rebuild(clear_existing=True)
            self.preview_bounds = None
        elif self.interaction["mode"] == "pan":
            self.preview_bounds = None
        self.interaction = None
        self._redraw_overlay()

    def _on_mouse_wheel(self, sender, app_data):
        if not self._is_canvas_hovered():
            return
        self._set_zoom_at_point(app_data, self._get_mouse_local())

    def _on_delete_key_pressed(self, sender, app_data):
        if self.selected_roi is None:
            return
        if not dpg.is_item_focused(self.window_id):
            return
        self._close_roi(self.selected_roi.tag)
        self._redraw_overlay()

    def _set_current_frame_index(self, frame_index):
        frame_count = self._get_frame_count()
        if frame_count <= 0:
            return
        next_index = int(np.clip(int(frame_index), 0, frame_count - 1))
        if next_index == self.current_frame_index:
            return
        self.current_frame_index = next_index
        self._mark_image_dirty()

    def _on_slider_changed(self, sender, app_data, user_data=None):
        self.is_playing = False
        self._update_play_button()
        self._set_current_frame_index(app_data)

    def _on_autoscale_changed(self, sender, app_data):
        self.autoscale_enabled = bool(app_data)
        self._update_settings_controls_state()
        self._mark_image_dirty()

    def _on_scale_limits_changed(self, sender, app_data):
        scale_min = float(self._scale_percent_to_value(dpg.get_value(self.scale_min_input_id)))
        scale_max = float(self._scale_percent_to_value(dpg.get_value(self.scale_max_input_id)))

        if self._is_signed_zero_reference_mode_active():
            signed_display_limit = self._get_signed_display_limit()
            scale_min = float(np.clip(scale_min, -signed_display_limit, signed_display_limit))
            scale_max = float(np.clip(scale_max, -signed_display_limit, signed_display_limit))
            if self.mirrored_difference_scale:
                if sender == self.scale_min_input_id:
                    scale_max = -scale_min
                else:
                    scale_min = -scale_max

        self.scale_min = scale_min
        self.scale_max = scale_max
        self._sync_scale_inputs_from_values()
        self._mark_image_dirty()

    def _on_autoscale_grace_changed(self, sender, app_data):
        self.autoscale_grace_percent = max(0.0, float(app_data))
        self._mark_image_dirty()

    def _on_mirrored_difference_changed(self, sender, app_data):
        self.mirrored_difference_scale = bool(app_data)
        if self.mirrored_difference_scale:
            amplitude = min(abs(self.scale_min), abs(self.scale_max))
            if amplitude <= 0.0:
                amplitude = 1.0
            self.scale_min = -amplitude
            self.scale_max = amplitude
            self._sync_scale_inputs_from_values()
        self._mark_image_dirty()

    def _on_display_mode_changed(self, sender, app_data):
        self.display_mode = str(app_data)
        if self._is_signed_zero_reference_mode_active():
            self.mirrored_difference_scale = True
            dpg.set_value(self.mirrored_difference_checkbox_id, True)
        else:
            self.mirrored_difference_scale = False
            dpg.set_value(self.mirrored_difference_checkbox_id, False)

        self.colormap_name = self._colormap_per_mode.get(self.display_mode, self._get_default_colormap_name())
        self._ensure_valid_colormap_selection()
        self.scale_min, self.scale_max = self._compute_display_bounds()
        self._update_settings_controls_state()
        self._request_all_roi_rebuilds(clear_existing=True)
        self._mark_image_dirty()

    def _on_colormap_changed(self, sender, app_data):
        self.colormap_name = self._parse_colormap_label(app_data)
        self._ensure_valid_colormap_selection()
        self._colormap_per_mode[self.display_mode] = self.colormap_name
        self._update_colormap_controls_state()
        self._mark_image_dirty()

    def _on_set_zero(self, sender=None, app_data=None, user_data=None):
        frame = self._get_normal_frame()
        if frame is None:
            return
        self._current_zero_frame = np.array(frame, copy=True)
        self.zero_version += 1
        self._request_all_roi_rebuilds(clear_existing=True)
        self._mark_image_dirty()

    def _on_toggle_play(self, sender=None, app_data=None, user_data=None):
        if self._get_frame_count() <= 0:
            return
        if self.current_frame_index >= (self._get_frame_count() - 1):
            self.current_frame_index = 0
        self.is_playing = not self.is_playing
        self._update_play_button()

    def _on_toggle_repeat(self, sender=None, app_data=None, user_data=None):
        self.repeat_enabled = not self.repeat_enabled
        self._update_repeat_button()

    def _on_jump_to_start(self, sender=None, app_data=None, user_data=None):
        self.is_playing = False
        self._update_play_button()
        self._set_current_frame_index(0)

    def _on_jump_to_end(self, sender=None, app_data=None, user_data=None):
        self.is_playing = False
        self._update_play_button()
        self._set_current_frame_index(max(0, self._get_frame_count() - 1))

    def _on_rewind(self, sender=None, app_data=None, user_data=None):
        self.is_playing = False
        self._update_play_button()
        self._set_current_frame_index(self.current_frame_index - max(1, int(round(self.playback_fps))))

    def _on_fast_forward(self, sender=None, app_data=None, user_data=None):
        self.is_playing = False
        self._update_play_button()
        self._set_current_frame_index(self.current_frame_index + max(1, int(round(self.playback_fps))))

    def _rebuild_scope_plots(self):
        dpg.delete_item(self.scope_container_id, children_only=True)
        self.scope_plot_items = {}

        if self._loaded_payload is None:
            self.scope_empty_text_id = dpg.add_text("No oscilloscope data loaded.", parent=self.scope_container_id)
            return

        scope_plot_channels = self._loaded_payload.get("scope_plot_channels", {})
        if not scope_plot_channels:
            self.scope_empty_text_id = dpg.add_text("No oscilloscope data loaded.", parent=self.scope_container_id)
            return

        channel_names = sorted(scope_plot_channels)
        non_empty_channel_names = [
            channel_name for channel_name in channel_names if np.asarray(scope_plot_channels[channel_name]["y_values"], dtype=np.float32).size > 0
        ]
        if not non_empty_channel_names:
            self.scope_empty_text_id = dpg.add_text("No per-frame oscilloscope data stored in this file.", parent=self.scope_container_id)
            return

        self.scope_y_half_range_volts = 1.0
        for channel_name in non_empty_channel_names:
            samples = np.asarray(scope_plot_channels[channel_name]["y_values"], dtype=np.float32)
            if samples.size > 0:
                self.scope_y_half_range_volts = max(self.scope_y_half_range_volts, float(np.max(np.abs(samples))) * 1.1)
        self.scope_y_half_range_volts = max(self.scope_y_half_range_volts, 0.1)

        with dpg.subplots(
            rows=len(non_empty_channel_names),
            columns=1,
            parent=self.scope_container_id,
            width=-1,
            height=-1,
            link_all_x=True,
            row_ratios=[1.0] * len(non_empty_channel_names),
            no_title=True,
            no_menus=True,
        ):
            for index, channel_name in enumerate(non_empty_channel_names):
                x_values = np.asarray(scope_plot_channels[channel_name]["x_values"], dtype=np.float64)
                y_values = np.asarray(scope_plot_channels[channel_name]["y_values"], dtype=np.float32)
                point_count = min(x_values.size, y_values.size)

                with dpg.plot(width=-1, height=110, no_title=True, no_menus=True):
                    plot_id = dpg.last_item()
                    x_axis_id = dpg.add_plot_axis(
                        dpg.mvXAxis,
                        label="",
                        no_label=True,
                        no_tick_labels=index < (len(non_empty_channel_names) - 1),
                        no_tick_marks=index < (len(non_empty_channel_names) - 1),
                    )
                    y_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label=f"{channel_name}", no_initial_fit=True)
                    dpg.add_line_series(x_values.tolist(), y_values.tolist(), parent=y_axis_id, label=f"Channel {channel_name}")
                    marker_series_id = dpg.add_line_series([], [], parent=y_axis_id, label="Marker")
                    with dpg.theme() as marker_theme:
                        with dpg.theme_component(dpg.mvLineSeries):
                            dpg.add_theme_color(dpg.mvPlotCol_Line, [220, 40, 40, 255], category=dpg.mvThemeCat_Plots)
                    dpg.bind_item_theme(marker_series_id, marker_theme)
                    self.scope_plot_items[channel_name] = {
                        "plot_id": plot_id,
                        "x_axis_id": x_axis_id,
                        "y_axis_id": y_axis_id,
                        "marker_series_id": marker_series_id,
                    }
                    if point_count > 1:
                        dpg.set_axis_limits(x_axis_id, float(x_values[0]), float(x_values[-1]))
                    else:
                        dpg.set_axis_limits(x_axis_id, 0.0, 1.0)
                    dpg.set_axis_limits(y_axis_id, -self.scope_y_half_range_volts, self.scope_y_half_range_volts)

    def _update_scope_marker(self):
        if self._loaded_payload is None or not self.scope_plot_items:
            return

        timestamps = np.asarray(self._loaded_payload["relative_timestamps"], dtype=np.float64)
        if timestamps.size <= 0:
            marker_x = float(self.current_frame_index)
        else:
            marker_index = int(np.clip(self.current_frame_index, 0, timestamps.size - 1))
            marker_x = float(timestamps[marker_index])

        marker_y = [-self.scope_y_half_range_volts, self.scope_y_half_range_volts]
        marker_x_values = [marker_x, marker_x]
        for items in self.scope_plot_items.values():
            dpg.set_value(items["marker_series_id"], [marker_x_values, marker_y])
            dpg.set_axis_limits(items["y_axis_id"], -self.scope_y_half_range_volts, self.scope_y_half_range_volts)

    def _rebuild_settings_table(self):
        dpg.delete_item(self.settings_container_id, children_only=True)
        if self._loaded_payload is None:
            dpg.add_text("No acquisition settings loaded.", parent=self.settings_container_id, wrap=360)
            return

        saved_settings = self._loaded_payload.get("saved_settings", {})
        if not saved_settings:
            dpg.add_text("This file format does not store acquisition settings.", parent=self.settings_container_id, wrap=360)
            return

        with dpg.table(
            parent=self.settings_container_id,
            header_row=False,
            resizable=False,
            borders_innerV=False,
            borders_outerV=False,
            borders_innerH=False,
            borders_outerH=False,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(init_width_or_weight=0.46)
            dpg.add_table_column(init_width_or_weight=0.54)
            for setting_name, value in sorted(saved_settings.items()):
                with dpg.table_row():
                    dpg.add_text(self._format_setting_label(setting_name))
                    dpg.add_text(self._format_setting_value(value), wrap=180)

    def _update_frame_display(self):
        if self._loaded_payload is None:
            return

        if dpg.does_item_exist(self.frame_slider_id):
            dpg.set_value(self.frame_slider_id, self.current_frame_index)
        if self.rois_status_text_id is not None and dpg.does_item_exist(self.rois_status_text_id):
            roi_count = len(self.rois)
            dpg.set_value(self.rois_status_text_id, f"{roi_count} ROI{'s' if roi_count != 1 else ''}")

    def _update_image_texture(self):
        if not self.image_dirty or self._loaded_payload is None:
            return

        frame = self._get_display_frame()
        rgba = self._frame_to_rgba(frame)
        dpg.set_value(self.texture_id, rgba)
        self._current_rgba = rgba
        self.image_dirty = False

    def SaveState(self):
        save_state_file(
            self.state_name,
            {
                "window": capture_window_state(self.window_id),
                "sections": capture_item_open_states(self.section_node_ids),
                "display_mode": self.display_mode,
                "autoscale_enabled": self.autoscale_enabled,
                "autoscale_grace_percent": self.autoscale_grace_percent,
                "mirrored_difference_scale": self.mirrored_difference_scale,
                "scale_min": self.scale_min,
                "scale_max": self.scale_max,
                "repeat_enabled": self.repeat_enabled,
                "zoom": self.zoom,
                "view_center_x": self.view_center_x,
                "view_center_y": self.view_center_y,
                "colormap_per_mode": dict(self._colormap_per_mode),
                "crop_percent": float(self.crop_percent),
            },
        )
        if self.rois_window is not None and hasattr(self.rois_window, "SaveState"):
            self.rois_window.SaveState()

    def LoadState(self):
        state = load_state_file(self.state_name)
        if self.rois_window is not None and hasattr(self.rois_window, "LoadState"):
            self.rois_window.LoadState()
        if not state:
            if self.window_id is not None and dpg.does_item_exist(self.window_id):
                dpg.show_item(self.window_id)
            return

        apply_window_state(self.window_id, state.get("window"))
        if self.window_id is not None and dpg.does_item_exist(self.window_id):
            dpg.show_item(self.window_id)
        apply_item_open_states(self.section_node_ids, state.get("sections"))

        saved_colormaps = state.get("colormap_per_mode")
        if isinstance(saved_colormaps, dict):
            for mode_name, colormap_name in saved_colormaps.items():
                if mode_name in self._colormap_per_mode:
                    self._colormap_per_mode[mode_name] = str(colormap_name)

        saved_display_mode = str(state.get("display_mode", self.display_mode))
        if saved_display_mode in ("Normal", "Difference", "Contrast"):
            self.display_mode = saved_display_mode
        self.autoscale_enabled = bool(state.get("autoscale_enabled", self.autoscale_enabled))
        self.autoscale_grace_percent = max(0.0, float(state.get("autoscale_grace_percent", self.autoscale_grace_percent)))
        self.mirrored_difference_scale = bool(state.get("mirrored_difference_scale", self.mirrored_difference_scale))
        self.scale_min = float(state.get("scale_min", self.scale_min))
        self.scale_max = float(state.get("scale_max", self.scale_max))
        self.repeat_enabled = bool(state.get("repeat_enabled", self.repeat_enabled))
        self.zoom = float(np.clip(state.get("zoom", self.zoom), self.min_zoom, self.max_zoom))
        self.view_center_x = float(state.get("view_center_x", self.view_center_x))
        self.view_center_y = float(state.get("view_center_y", self.view_center_y))
        self.colormap_name = self._colormap_per_mode.get(self.display_mode, self.colormap_name)
        self.crop_percent = float(np.clip(state.get("crop_percent", self.crop_percent), 0.0, 100.0))
        self.is_playing = False

        if self.display_mode_combo_id is not None and dpg.does_item_exist(self.display_mode_combo_id):
            dpg.set_value(self.display_mode_combo_id, self.display_mode)
        if self.autoscale_checkbox_id is not None and dpg.does_item_exist(self.autoscale_checkbox_id):
            dpg.set_value(self.autoscale_checkbox_id, self.autoscale_enabled)
        if self.autoscale_grace_input_id is not None and dpg.does_item_exist(self.autoscale_grace_input_id):
            dpg.set_value(self.autoscale_grace_input_id, self.autoscale_grace_percent)
        if self.mirrored_difference_checkbox_id is not None and dpg.does_item_exist(self.mirrored_difference_checkbox_id):
            dpg.set_value(self.mirrored_difference_checkbox_id, self.mirrored_difference_scale)
        if self.crop_slider_id is not None and dpg.does_item_exist(self.crop_slider_id):
            dpg.set_value(self.crop_slider_id, self.crop_percent)

        self._update_play_button()
        self._update_repeat_button()
        self._update_settings_controls_state()
        self._sync_scale_inputs_from_values()

    def close(self):
        if self._closing:
            return
        self._closing = True
        self.SaveState()
        self._stop_event.set()
        self.is_playing = False
        self._update_play_button()

        if self._load_thread is not None and self._load_thread.is_alive():
            self._load_thread.join(timeout=0.2)
        if self._playback_thread is not None and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=0.2)

        self._loaded_payload = None
        self._pending_loaded_payload = None
        self._current_zero_frame = None
        self._current_rgba = None
        self.scope_plot_items = {}
        for roi in list(self.rois):
            roi.close()
        self.rois = []

        if self.rois_window is not None and dpg.does_item_exist(self.rois_window.window_id):
            dpg.delete_item(self.rois_window.window_id)
        if self.mouse_handler_id is not None and dpg.does_item_exist(self.mouse_handler_id):
            dpg.delete_item(self.mouse_handler_id)
        if self.resize_handler_id is not None and dpg.does_item_exist(self.resize_handler_id):
            dpg.delete_item(self.resize_handler_id)

        if self.texture_id is not None and dpg.does_item_exist(self.texture_id):
            dpg.delete_item(self.texture_id)
        if self.window_id is not None and dpg.does_item_exist(self.window_id):
            dpg.delete_item(self.window_id)

        self._is_closed = True

    def render(self):
        if self._is_closed:
            return False
        if not dpg.does_item_exist(self.window_id):
            self._is_closed = True
            return False
        is_window_shown = dpg.is_item_shown(self.window_id)
        if is_window_shown:
            self._was_ever_shown = True
        elif self._was_ever_shown:
            self.close()
            return False

        self._apply_pending_loaded_payload()
        if self.rois_window is not None:
            self.rois_window.rebuild_layout(self.rois)
            self.rois_window.render()
        for roi in self.rois:
            roi.render()
        self._update_layout()
        self._update_frame_display()
        self._update_image_texture()
        self._update_scope_marker()
        return True
