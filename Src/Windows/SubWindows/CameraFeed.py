import threading
import time

import dearpygui.dearpygui as dpg
import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from skimage.registration import phase_cross_correlation

from Utils.StorageDTypes import get_raw_storage_max_value
from Utils.state_persistence import apply_window_state, capture_window_state, delete_state_file, list_state_files, load_state_file, save_state_file
from Utils.themes import no_padding_theme, read_only_theme
from Utils.shared_state import class_objects
from Windows.SubWindows.FeedControlsWindow import FeedControlsWindow
from Windows.SubWindows.ImageWindow import ImageWindow
from Windows.SubWindows.RegionOfInterest import RegionOfInterest
from Windows.SubWindows.ROIsWindow import ROIsWindow

class CameraFeedWindow:

    padding = 16
    min_roi_size = 8
    handle_half_size = 5
    edge_pick_threshold = 10
    difference_display_limit = float(np.finfo(np.float16).max)
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

    def __init__(self, parent, Andor):

        self.parent = parent
        self.Andor = Andor

        self.width = 600
        self.feed_height = 600
        self.height = self.feed_height

        self.name = "Camera Feed"
        self.tag = "CameraFeed"
        self.display_max = self._get_normal_display_max()
        self.scale_min = 0.0
        self.scale_max = float(self.display_max)
        self.autoscale_enabled = True
        self.autoscale_grace_percent = 5.0
        self.display_mode = "Normal"
        self.mirrored_difference_scale = False
        self._scale_state_per_mode = {}
        self.colormap_name = "Viridis"
        self._colormap_per_mode = {
            "Normal": self.single_sided_colormap_names[0],
            "Difference": self.double_sided_colormap_names[0],
            "Contrast": self.double_sided_colormap_names[0],
        }
        self.lp_filter_enabled = bool(getattr(self.Andor, "lp_filter_enabled", False))
        self.lp_filter_cutoff_hz = float(getattr(self.Andor, "lp_filter_cutoff_hz", 10.0))
        self.drift_correction_enabled = False
        self.bg_removal_enabled = False
        self.bg_removal_sigma = 20.0
        self.crop_percent = 100.0
        self._drift_reference_frame = None
        self._drift_accumulated_shift = (0, 0)
        self._drift_smoothed_shift = np.array([0.0, 0.0])
        self._drift_valid_mask = None
        self._colormap_lut_cache = {}
        self._focus_reference_score = None
        self._focus_level = 0.0

        self.image_width = int(self.Andor.camera.AOIWidth)
        self.image_height = int(self.Andor.camera.AOIHeight)
        self.imageArray = self._process_frame()

        self.rois = []
        self.roi_index = 0
        self.selected_roi = None
        self.hover_target = None
        self.preview_bounds = None
        self.interaction = None
        self.zoom = 1.0
        self.min_zoom = 1.0
        self.max_zoom = 16.0
        self.view_center_x = self.image_width / 2.0
        self.view_center_y = self.image_height / 2.0
        self.image_dirty = False
        self._image_state_lock = threading.Lock()
        self.zero_window_refresh_requested = True
        self.zero_window_state_key = None
        self.zero_preview_max_dimension = 512
        self.context_menu_roi = None
        self.controls_window = None
        self.zero_reference_window = None
        self.texture_id = None
        self.image_draw_id = None
        self.displayed_feed_fps = 0.0
        self._display_fps_window_started_at = time.perf_counter()
        self._display_fps_window_frame_count = 0
        self._lp_filter_previous_input  = None
        self._lp_filter_previous_output = None
        self._processing_stop_event = threading.Event()
        self.colorbar_enabled = False
        self._colorbar_last_min = None
        self._colorbar_last_max = None
        self._last_frame_display_min = 0.0
        self._last_frame_display_max = 1.0

        with dpg.window(
            label=self.name,
            tag=f"{self.tag}_Window",
            width=self.width,
            height=self.height,
            pos=(10, 10),
            no_scrollbar=True,
            no_resize=False,
            no_scroll_with_mouse = True,
        ):

            self.window_id = dpg.last_item()

            dpg.bind_item_theme(self.window_id, no_padding_theme)

            with dpg.item_handler_registry(tag=f"{self.tag}_ResizeHandler"):
                dpg.add_item_resize_handler(callback=self._on_window_resize)
                dpg.bind_item_handler_registry(self.window_id, f"{self.tag}_ResizeHandler")

            with dpg.drawlist(width=self.width, height=self.feed_height, parent=self.window_id, tag=f"{self.tag}_Canvas"):
                self.canvas_id = dpg.last_item()
                with dpg.draw_layer(tag=f"{self.tag}_ImageLayer"):
                    self.image_layer = dpg.last_item()
                with dpg.draw_layer(tag=f"{self.tag}_OverlayLayer"):
                    self.overlay_layer = dpg.last_item()
                with dpg.draw_layer(tag=f"{self.tag}_ColorbarLayer"):
                    self.colorbar_layer = dpg.last_item()

            with dpg.handler_registry(tag=f"{self.tag}_MouseHandler"):
                dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Left, callback=self._on_left_mouse_down)
                dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Middle, callback=self._on_middle_mouse_down)
                dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Right, callback=self._on_right_mouse_down)
                dpg.add_mouse_move_handler(callback=self._on_mouse_move)
                dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_release)
                dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_release)
                dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)
                dpg.add_key_press_handler(key=dpg.mvKey_Delete, callback=self._on_delete_key_pressed)

            with dpg.popup(self.canvas_id, mousebutton=dpg.mvMouseButton_Right):
                self.canvas_popup_id = dpg.last_item()
                self.set_frame_to_roi_button_id = dpg.add_button(label="Set Frame to ROI", width=140, callback=self._on_set_frame_to_roi)
                dpg.add_button(label="Reset Zoom", width=140, callback=self._reset_zoom)

        self.controls_window = FeedControlsWindow(self)
        self.zero_reference_window = ImageWindow(
            tag_prefix=f"{self.tag}_Zero",
            label="Zero Reference",
            width=440,
            height=440,
            pos=(320, 620),
            show=False,
            image_size=(self.image_height, self.image_width),
        )
        self.rois_window = ROIsWindow()

        self.reset_texture()

        self._update_settings_controls_state()
        threading.Thread(target=self._process_camera_feed, daemon=True).start()
        threading.Thread(target=self._processing_loop, daemon=True).start()

    def _get_scale_limit_for_mode(self):
        if self._is_signed_zero_reference_mode_active():
            return float(self._get_signed_display_limit())
        return float(self.display_max)

    def _get_scale_percent_bounds(self):
        if self._is_signed_zero_reference_mode_active():
            return -100, 100
        return 0, 100

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
        dpg.set_value(self.controls_window.scale_min_input_id, self.get_scale_min_percent())
        dpg.set_value(self.controls_window.scale_max_input_id, self.get_scale_max_percent())

    def _save_scale_state_for_mode(self, mode):
        self._scale_state_per_mode[str(mode)] = {
            "scale_min": self.scale_min,
            "scale_max": self.scale_max,
            "autoscale_enabled": self.autoscale_enabled,
            "autoscale_grace_percent": self.autoscale_grace_percent,
            "mirrored_difference_scale": self.mirrored_difference_scale,
        }

    def _restore_or_default_scale_for_mode(self, mode):
        saved = self._scale_state_per_mode.get(str(mode))
        if saved is not None:
            self.scale_min = saved["scale_min"]
            self.scale_max = saved["scale_max"]
            self.autoscale_enabled = saved["autoscale_enabled"]
            self.autoscale_grace_percent = saved["autoscale_grace_percent"]
            self.mirrored_difference_scale = saved["mirrored_difference_scale"]
        else:
            self.autoscale_enabled = True
            self.autoscale_grace_percent = 5.0
            if mode in ("Difference", "Contrast"):
                self.mirrored_difference_scale = True
                signed_display_limit = self._get_signed_display_limit()
                self.scale_min = -signed_display_limit
                self.scale_max = signed_display_limit
            else:
                self.mirrored_difference_scale = False
                self.scale_min = 0.0
                self.scale_max = float(self.display_max)

    def reset_texture(self):
        if self.controls_window is not None:
            preserved_scale_min_percent = self.get_scale_min_percent()
            preserved_scale_max_percent = self.get_scale_max_percent()
        else:
            preserved_scale_min_percent = None
            preserved_scale_max_percent = None

        self.image_width = int(self.Andor.camera.AOIWidth)
        self.image_height = int(self.Andor.camera.AOIHeight)
        self.display_max = self._get_normal_display_max()
        if preserved_scale_min_percent is not None and preserved_scale_max_percent is not None:
            self.scale_min = float(self._scale_percent_to_value(preserved_scale_min_percent))
            self.scale_max = float(self._scale_percent_to_value(preserved_scale_max_percent))
        else:
            self.scale_min = 0.0
            self.scale_max = float(self.display_max)
        self.imageArray = self._process_frame()
        self._reset_zoom(redraw=False)

        if hasattr(self, "texture_id") and dpg.does_item_exist(self.texture_id):
            dpg.delete_item(self.texture_id)

        with dpg.texture_registry(show=False):
            self.texture_id = dpg.add_dynamic_texture(
                width=self.image_width,
                height=self.image_height,
                default_value=self.imageArray,
            )

        if hasattr(self, "image_draw_id") and dpg.does_item_exist(self.image_draw_id):
            dpg.configure_item(
                self.image_draw_id,
                texture_tag=self.texture_id,
                pmin=(0, 0),
                pmax=(self.width, self.feed_height),
            )
        else:
            dpg.delete_item(self.image_layer, children_only=True)
            self.image_draw_id = dpg.draw_image(
                texture_tag=self.texture_id,
                pmin=(0, 0),
                pmax=(self.width, self.feed_height),
                parent=self.image_layer,
            )

        self._update_image_draw_transform()
        self._update_zero_window_texture_binding()
        if self.controls_window is not None:
            self._update_settings_controls_state()
        self._redraw_overlay()

    def reset_displayed_feed_fps(self):
        self.displayed_feed_fps = 0.0
        self._display_fps_window_started_at = time.perf_counter()
        self._display_fps_window_frame_count = 0

    def _on_window_resize(self, sender=None, app_data=None):
        new_width, new_height = dpg.get_item_rect_size(self.window_id)
        self.width = max(1, int(new_width))
        self.height = max(1, int(new_height))
        self.feed_height = max(160, self.height)
        dpg.configure_item(self.canvas_id, width=self.width, height=self.feed_height)

        if hasattr(self, "image_draw_id") and dpg.does_item_exist(self.image_draw_id):
            dpg.configure_item(self.image_draw_id, pmin=(0, 0), pmax=(self.width, self.feed_height))

        self._clamp_view_center()
        self._update_image_draw_transform()
        self._redraw_overlay()
        self._redraw_colorbar()

    def _update_settings_controls_state(self):
        is_signed_zero_reference_mode = self._is_signed_zero_reference_mode_active()
        input_min, input_max = self._get_scale_percent_bounds()
        dpg.configure_item(self.controls_window.scale_min_input_id, min_value=input_min, max_value=input_max)
        dpg.configure_item(self.controls_window.scale_max_input_id, min_value=input_min, max_value=input_max)
        self._sync_scale_inputs_from_values()

        if self.autoscale_enabled:
            dpg.configure_item(self.controls_window.scale_min_input_id, enabled=False)
            dpg.configure_item(self.controls_window.scale_max_input_id, enabled=False)
            dpg.bind_item_theme(self.controls_window.scale_min_input_id, read_only_theme)
            dpg.bind_item_theme(self.controls_window.scale_max_input_id, read_only_theme)
            dpg.configure_item(self.controls_window.autoscale_grace_input_id, enabled=True)
            dpg.bind_item_theme(self.controls_window.autoscale_grace_input_id, None)
        else:
            dpg.configure_item(self.controls_window.scale_min_input_id, enabled=True)
            dpg.configure_item(self.controls_window.scale_max_input_id, enabled=True)
            dpg.bind_item_theme(self.controls_window.scale_min_input_id, None)
            dpg.bind_item_theme(self.controls_window.scale_max_input_id, None)
            dpg.configure_item(self.controls_window.autoscale_grace_input_id, enabled=False)
            dpg.bind_item_theme(self.controls_window.autoscale_grace_input_id, read_only_theme)

        mirrored_enabled = is_signed_zero_reference_mode and not self.autoscale_enabled
        dpg.configure_item(self.controls_window.mirrored_difference_checkbox_id, enabled=mirrored_enabled, show=is_signed_zero_reference_mode)
        if mirrored_enabled:
            dpg.bind_item_theme(self.controls_window.mirrored_difference_checkbox_id, None)
        else:
            dpg.bind_item_theme(self.controls_window.mirrored_difference_checkbox_id, read_only_theme if is_signed_zero_reference_mode else None)

        self._update_colormap_controls_state()

        dpg.configure_item(self.controls_window.lp_filter_cutoff_input_id, enabled=self.lp_filter_enabled)
        dpg.bind_item_theme(self.controls_window.lp_filter_cutoff_input_id, None if self.lp_filter_enabled else read_only_theme)

        dpg.configure_item(self.controls_window.bg_removal_sigma_input_id, enabled=self.bg_removal_enabled)
        dpg.bind_item_theme(self.controls_window.bg_removal_sigma_input_id, None if self.bg_removal_enabled else read_only_theme)

    def _on_autoscale_changed(self, sender, app_data):
        self.autoscale_enabled = bool(app_data)
        self._update_settings_controls_state()
        self._refresh_display_image()

    def _on_scale_limits_changed(self, sender, app_data):
        scale_min = float(self._scale_percent_to_value(dpg.get_value(self.controls_window.scale_min_input_id)))
        scale_max = float(self._scale_percent_to_value(dpg.get_value(self.controls_window.scale_max_input_id)))

        if self._is_signed_zero_reference_mode_active():
            signed_display_limit = self._get_signed_display_limit()
            scale_min = float(np.clip(scale_min, -signed_display_limit, signed_display_limit))
            scale_max = float(np.clip(scale_max, -signed_display_limit, signed_display_limit))
            if self.mirrored_difference_scale:
                if sender == self.controls_window.scale_min_input_id:
                    scale_max = -scale_min
                else:
                    scale_min = -scale_max

        self.scale_min = scale_min
        self.scale_max = scale_max
        self._sync_scale_inputs_from_values()
        self._refresh_display_image()
        self._redraw_colorbar()

    def _on_autoscale_grace_changed(self, sender, app_data):
        self.autoscale_grace_percent = max(0.0, float(app_data))
        self._refresh_display_image()

    def _on_mirrored_difference_changed(self, sender, app_data):
        self.mirrored_difference_scale = bool(app_data)
        if self.mirrored_difference_scale:
            amplitude = min(abs(self.scale_min), abs(self.scale_max))
            if amplitude <= 0.0:
                amplitude = 1.0
            self.scale_min = -amplitude
            self.scale_max = amplitude
            self._sync_scale_inputs_from_values()
        self._refresh_display_image()

    def _on_lp_filter_enabled_changed(self, sender, app_data):
        self.lp_filter_enabled = bool(app_data)
        self.Andor.set_lp_filter_enabled(self.lp_filter_enabled)
        self._reset_processing_state()
        self._sync_scale_state_to_active_frame()
        self._update_settings_controls_state()
        self._refresh_display_image()
        self._request_all_roi_rebuilds(clear_existing=True)

    def _on_lp_filter_cutoff_changed(self, sender, app_data):
        self.lp_filter_cutoff_hz = max(1e-3, float(dpg.get_value(self.controls_window.lp_filter_cutoff_input_id)))
        dpg.set_value(self.controls_window.lp_filter_cutoff_input_id, self.lp_filter_cutoff_hz)
        self.Andor.set_lp_filter_cutoff_hz(self.lp_filter_cutoff_hz)
        self._reset_processing_state()
        self._sync_scale_state_to_active_frame()
        self._refresh_display_image()
        self._request_all_roi_rebuilds(clear_existing=True)

    def _is_difference_mode_active(self):
        return self.display_mode == "Difference"

    def _is_contrast_mode_active(self):
        return self.display_mode == "Contrast"

    def _is_signed_zero_reference_mode_active(self):
        return self.display_mode in ("Difference", "Contrast")

    def _get_colormap_mode_suffix(self):
        if self._is_signed_zero_reference_mode_active():
            return " (Double-Sided)"
        return ""

    def _get_available_colormap_names(self):
        if self._is_signed_zero_reference_mode_active():
            return self.double_sided_colormap_names
        return self.single_sided_colormap_names

    def _get_default_colormap_name(self):
        if self._is_signed_zero_reference_mode_active():
            return self.double_sided_colormap_names[0]
        return self.single_sided_colormap_names[0]

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
            label = label[: -len(" (Double-Sided)")]
        if label in self.colormap_lookup_names or label in self.custom_colormap_hex_scales:
            return label
        return self._get_default_colormap_name()

    def _update_colormap_controls_state(self):
        self._ensure_valid_colormap_selection()
        labels = self.get_available_colormap_labels()
        dpg.configure_item(self.controls_window.color_scale_combo_id, items=labels)
        dpg.set_value(self.controls_window.color_scale_combo_id, self.get_selected_colormap_label())

    def _get_signed_display_limit(self):
        if self._is_contrast_mode_active():
            return 200.0
        return self.difference_display_limit

    def _get_normal_display_max(self):
        return get_raw_storage_max_value(self.Andor.storage_dtype_name)

    def _build_custom_colormap_lut(self, colormap_name, samples):
        if colormap_name == "Cividis":
            cividis_map = colormaps["cividis"]
            custom_map = LinearSegmentedColormap.from_list(
                "widefield_cividis_diverging",
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
            f"widefield_{colormap_name.lower()}_diverging",
            custom_stops,
        )
        return np.asarray(
            custom_map(np.linspace(0.0, 1.0, int(samples), dtype=np.float32)),
            dtype=np.float32,
        )[..., :3]

    def _get_colormap_lut(self, double_sided=False, samples=512):
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

    def _apply_colormap(self, normalized, double_sided=False):
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

    def _request_all_roi_rebuilds(self, clear_existing=False):
        self.rois_window.invalidate_autoscale_cache(pending_tags=[roi.tag for roi in self.rois])
        for roi in self.rois:
            roi.request_trace_rebuild(clear_existing=clear_existing)

    def rebuild_roi_traces(self):
        self._request_all_roi_rebuilds(clear_existing=True)

    def set_roi_history_capacity(self, max_points):
        max_points = max(1, int(max_points))
        updated_tags = []
        for roi in self.rois:
            with roi.data_lock:
                if roi.max_points == max_points:
                    continue
                roi.max_points = max_points
                updated_tags.append(roi.tag)

        if updated_tags:
            self.rois_window.invalidate_autoscale_cache(pending_tags=updated_tags)
            for roi in self.rois:
                if roi.tag in updated_tags:
                    roi.request_trace_rebuild(clear_existing=True)

    def recalculate_rois(self):
        self._request_all_roi_rebuilds()
        self._redraw_overlay()

    def _compute_display_bounds(self, frame=None):
        if frame is None:
            with self.Andor.processed_frame_condition:
                frame = self.Andor.processed_frame

        if self._is_signed_zero_reference_mode_active():
            signed_display_limit = self._get_signed_display_limit()
            if frame is None:
                min_value = -1.0
                max_value = 1.0
            elif self.autoscale_enabled:
                signed_frame = frame.astype(np.float32, copy=False)
                data_min = float(np.min(signed_frame))
                data_max = float(np.max(signed_frame))
                if data_max <= data_min:
                    data_max = data_min + 1.0
                grace_fraction = self.autoscale_grace_percent / 100.0
                padding = (data_max - data_min) * grace_fraction
                min_value = max(-signed_display_limit, data_min - padding)
                max_value = min(signed_display_limit, data_max + padding)
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
            normal_frame = frame.astype(np.float32, copy=False)
            data_min = float(np.min(normal_frame))
            data_max = float(np.max(normal_frame))
            if data_max <= data_min:
                data_max = data_min + 1.0
            grace_fraction = self.autoscale_grace_percent / 100.0
            padding = (data_max - data_min) * grace_fraction
            min_value = max(0.0, data_min - padding)
            max_value = min(float(self.display_max), data_max + padding)
        else:
            min_value = max(0.0, self.scale_min)
            max_value = min(float(self.display_max), max(self.scale_max, 1.0))

        if max_value <= min_value:
            max_value = min_value + 1.0

        return float(min_value), float(max_value)

    def _sync_scale_state_to_active_frame(self):
        self.scale_min, self.scale_max = self._compute_display_bounds()
        self._sync_scale_inputs_from_values()

    def _process_analysis_frame(self, frame, zero_frame=None):
        if frame is None:
            return None

        if self._is_difference_mode_active():
            reference_frame = self.Andor.zero if zero_frame is None else zero_frame
            difference_frame = np.asarray(frame, dtype=np.float32) - np.asarray(reference_frame, dtype=np.float32)
            return np.array(self.Andor.coerce_signed_frame_to_storage(difference_frame), copy=True)

        if self._is_contrast_mode_active():
            reference_frame = self.Andor.zero if zero_frame is None else zero_frame
            frame_float = np.asarray(frame, dtype=np.float32)
            zero_float = np.asarray(reference_frame, dtype=np.float32)
            difference_frame = frame_float - zero_float
            contrast_frame = (difference_frame / (zero_float + 1.0)) * 100.0
            return np.array(self.Andor.coerce_signed_frame_to_storage(contrast_frame), copy=True)

        return np.array(frame, copy=True)

    def process_analysis_frame(self, frame, zero_frame=None):
        return self._process_analysis_frame(frame, zero_frame=zero_frame)

    def _set_zero_reference_frame(self, frame):
        if frame is None:
            return False

        self.Andor.set_zero_frame(frame)
        self.zero_reference_window.show()
        self._request_zero_window_refresh()
        self._refresh_display_image()
        self._update_zero_window_texture_binding()
        self._update_zero_window()
        self._request_all_roi_rebuilds()
        return True

    @staticmethod
    def _apply_background_removal(frame_f32, sigma):
        from scipy.ndimage import gaussian_filter
        bg = gaussian_filter(np.asarray(frame_f32, dtype=np.float32), sigma=float(sigma))
        return np.clip(frame_f32 - bg, 0.0, None)

    @staticmethod
    def _compute_focus_score(frame):
        f = np.asarray(frame, dtype=np.float32)
        if f.size < 9:
            return 0.0
        gx = f[:, 2:] - f[:, :-2]
        gy = f[2:, :] - f[:-2, :]
        h = min(gx.shape[0], gy.shape[0])
        w = min(gx.shape[1], gy.shape[1])
        mean_sq = max(float(np.mean(f)) ** 2, 1.0)
        return float(np.mean(gx[:h, :w] ** 2 + gy[:h, :w] ** 2)) / mean_sq

    def ensure_zero_reference_from_latest_frame(self):
        with self.Andor.frame_lock:
            if not len(self.Andor.acquisitions):
                return False
            latest_frame = np.array(self.Andor.latest_frame, copy=True)
        self._focus_reference_score = self._compute_focus_score(latest_frame)
        return self._set_zero_reference_frame(latest_frame)

    def _get_active_frame_locked(self):
        latest_frame, _, has_frames, _ = self.Andor.get_processed_frame_view_locked(
            display_mode=self.display_mode,
            lp_filter_enabled=self.lp_filter_enabled,
            include_history=False,
        )
        if not has_frames:
            return None
        return latest_frame

    def _refresh_display_image(self):
        with self.Andor.processed_frame_condition:
            frame_ref = self.Andor.processed_frame
        rgba = self._frame_to_rgba(frame_ref)
        with self._image_state_lock:
            self.imageArray = rgba
            self.image_dirty = True

    def _request_zero_window_refresh(self):
        self.zero_window_refresh_requested = True

    def _get_zero_window_texture_tag(self):
        return self.zero_reference_window.get_texture_tag()

    def _update_zero_window_texture_binding(self):
        if self.zero_reference_window is None:
            return
        self.zero_reference_window.update_texture_binding()

    def _get_zero_window_image_shape(self):
        return self.zero_reference_window.get_image_shape()

    def _zero_frame_to_rgba(self, frame):
        if frame is None:
            zero_image_height, zero_image_width = self._get_zero_window_image_shape()
            return np.zeros((zero_image_height * zero_image_width * 4,), dtype=np.float32)

        zero_frame = frame.astype(np.float32, copy=False)
        data_min = float(np.min(zero_frame))
        data_max = float(np.max(zero_frame))
        if data_max <= data_min:
            data_max = data_min + 1.0

        grace_fraction = self.autoscale_grace_percent / 100.0
        padding = (data_max - data_min) * grace_fraction
        min_value = max(0.0, data_min - padding)
        max_value = min(float(self.display_max), data_max + padding)
        if max_value <= min_value:
            max_value = min_value + 1.0

        scaled = np.clip((zero_frame - min_value) / (max_value - min_value), 0.0, 1.0)
        return self._apply_colormap(scaled, double_sided=False)

    def _downsample_preview_frame(self, frame):
        if frame is None:
            return None

        height, width = frame.shape[:2]
        max_dimension = max(height, width)
        if max_dimension <= self.zero_preview_max_dimension:
            return np.array(frame, copy=True)

        step = int(np.ceil(max_dimension / float(self.zero_preview_max_dimension)))
        return np.array(frame[::step, ::step], copy=True)

    def _on_set_zero(self, sender=None, app_data=None, user_data=None):
        self.ensure_zero_reference_from_latest_frame()

    def _on_display_mode_changed(self, sender, app_data):
        old_mode = self.display_mode
        new_mode = str(app_data)

        self._save_scale_state_for_mode(old_mode)
        self.display_mode = new_mode
        self._reset_processing_state()

        self._restore_or_default_scale_for_mode(new_mode)
        dpg.set_value(self.controls_window.autoscale_checkbox_id, self.autoscale_enabled)
        dpg.set_value(self.controls_window.autoscale_grace_input_id, self.autoscale_grace_percent)
        dpg.set_value(self.controls_window.mirrored_difference_checkbox_id, self.mirrored_difference_scale)
        self._sync_scale_inputs_from_values()

        self.colormap_name = self._colormap_per_mode.get(self.display_mode, self._get_default_colormap_name())
        self._ensure_valid_colormap_selection()
        self._request_zero_window_refresh()
        self._refresh_display_image()
        self._update_zero_window_texture_binding()
        self._update_settings_controls_state()
        self._request_all_roi_rebuilds()
        self._update_zero_window()
        self._redraw_colorbar()

    def _on_colormap_changed(self, sender, app_data):
        self.colormap_name = self._parse_colormap_label(app_data)
        self._ensure_valid_colormap_selection()
        self._colormap_per_mode[self.display_mode] = self.colormap_name
        self._update_colormap_controls_state()
        self._request_zero_window_refresh()
        self._refresh_display_image()
        self._request_all_roi_rebuilds()
        self._update_zero_window()
        self._redraw_colorbar()

    def _prepare_analysis_frame(self, frame):
        if frame is None:
            return None

        return frame

    def _reset_drift_state(self):
        self._drift_reference_frame = None
        self._drift_accumulated_shift = (0.0, 0.0)
        self._drift_smoothed_shift = np.array([0.0, 0.0])
        self._drift_valid_mask = None

    def _estimate_drift_shift(self, frame_f32, reference_frame):
        ref_max = float(reference_frame.max())
        if ref_max <= 0.0:
            return 0.0, 0.0
        ref_norm = reference_frame / ref_max
        # phase_cross_correlation with normalization="phase" is undefined for flat images
        if float(ref_norm.std()) < 1e-6:
            return 0.0, 0.0
        shift, _, _ = phase_cross_correlation(
            ref_norm,
            frame_f32 / ref_max,
            upsample_factor=10,
            normalization="phase",
        )
        return float(shift[0]), float(shift[1])

    def _shift_frame_for_display(self, frame, dy, dx):
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

    def _shift_frame_subpixel(self, frame, dy, dx):
        frame_f32 = np.asarray(frame, dtype=np.float32)
        dy_int = int(np.floor(dy))
        dx_int = int(np.floor(dx))
        fy = float(dy - dy_int)
        fx = float(dx - dx_int)
        s00 = self._shift_frame_for_display(frame_f32, dy_int, dx_int)
        if fx > 1e-6:
            s01 = self._shift_frame_for_display(frame_f32, dy_int, dx_int + 1)
            row0 = (1.0 - fx) * s00 + fx * s01
        else:
            row0 = s00
        if fy > 1e-6:
            s10 = self._shift_frame_for_display(frame_f32, dy_int + 1, dx_int)
            if fx > 1e-6:
                s11 = self._shift_frame_for_display(frame_f32, dy_int + 1, dx_int + 1)
                row1 = (1.0 - fx) * s10 + fx * s11
            else:
                row1 = s10
            return (1.0 - fy) * row0 + fy * row1
        return row0

    def _get_drift_reference(self):
        """Return (reference_f32, is_from_zero) — prefers the explicit zero reference."""
        with self.Andor.frame_lock:
            zero_version = int(self.Andor.zero_version)
            if zero_version > 0:
                return np.asarray(self.Andor.zero, dtype=np.float32), True
        return self._drift_reference_frame, False

    def _compute_drift_valid_mask(self, shape, dy, dx):
        import math
        h, w = shape
        mask = np.ones(shape, dtype=bool)
        top    = max(0, math.ceil(dy))
        bottom = max(0, math.ceil(-dy))
        left   = max(0, math.ceil(dx))
        right  = max(0, math.ceil(-dx))
        if top    > 0: mask[:top, :]      = False
        if bottom > 0: mask[h - bottom:, :] = False
        if left   > 0: mask[:, :left]     = False
        if right  > 0: mask[:, w - right:] = False
        return mask

    def _apply_drift_correction_for_display(self, frame, estimate_from=None):
        frame_f32 = np.asarray(frame, dtype=np.float32)
        # Use a separate (typically raw) frame for shift estimation when provided so that
        # a preceding LP filter's temporal lag does not corrupt the drift estimate.
        shift_source = np.asarray(estimate_from, dtype=np.float32) if estimate_from is not None else frame_f32
        reference, _ = self._get_drift_reference()

        if reference is None:
            # No zero reference and no fallback captured yet — use this frame as fallback.
            self._drift_reference_frame = np.array(shift_source, copy=True)
            self._drift_accumulated_shift = (0.0, 0.0)
            self._drift_smoothed_shift = np.array([0.0, 0.0])
            self._drift_valid_mask = None
            return frame_f32

        dy_raw, dx_raw = self._estimate_drift_shift(shift_source, reference)
        max_shift = min(frame_f32.shape[0], frame_f32.shape[1]) // 4
        dy_s = float(np.clip(dy_raw, -max_shift, max_shift))
        dx_s = float(np.clip(dx_raw, -max_shift, max_shift))
        self._drift_smoothed_shift = np.array([dy_s, dx_s])
        self._drift_accumulated_shift = (dy_s, dx_s)
        self._drift_valid_mask = self._compute_drift_valid_mask(frame_f32.shape, dy_s, dx_s)
        return self._shift_frame_subpixel(frame_f32, dy_s, dx_s)

    def make_display_rgba(self, frame, *, is_crop=False):
        return self._frame_to_rgba(frame)

    def _on_drift_correction_changed(self, sender, app_data):
        self.drift_correction_enabled = bool(app_data)
        if self.drift_correction_enabled:
            self._reset_drift_state()
        self._refresh_display_image()
        self._request_all_roi_rebuilds()

    def _on_bg_removal_enabled_changed(self, sender, app_data):
        self.bg_removal_enabled = bool(app_data)
        self._update_settings_controls_state()
        self._reset_processing_state()
        self._sync_scale_state_to_active_frame()
        self._refresh_display_image()
        self._request_all_roi_rebuilds(clear_existing=True)

    def _on_bg_removal_sigma_changed(self, sender, app_data):
        self.bg_removal_sigma = max(1.0, float(dpg.get_value(self.controls_window.bg_removal_sigma_input_id)))
        dpg.set_value(self.controls_window.bg_removal_sigma_input_id, self.bg_removal_sigma)
        if self.bg_removal_enabled:
            self._reset_processing_state()
            self._sync_scale_state_to_active_frame()
            self._refresh_display_image()
            self._request_all_roi_rebuilds(clear_existing=True)

    def _on_crop_changed(self, sender, app_data):
        self.crop_percent = float(np.clip(float(app_data), 0.0, 100.0))
        self._refresh_display_image()

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

    def extract_roi_frame(self, frame, bounds):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        if frame is None or x2 <= x1 or y2 <= y1:
            return None

        analysis_frame = self._prepare_analysis_frame(frame)
        if analysis_frame is None:
            return None

        crop = np.array(analysis_frame[y1:y2, x1:x2], copy=True)
        if crop.size == 0:
            return None
        return crop

    def _update_zero_window(self):
        if self.zero_reference_window is None:
            return
        if not self.zero_reference_window.is_visible():
            return

        self._update_zero_window_texture_binding()
        self.zero_reference_window.update_image_size()

        with self.Andor.frame_lock:
            zero_version = int(getattr(self.Andor, "zero_version", 0))
            display_state_key = (
                zero_version,
                self.zero_preview_max_dimension,
            )
            if not self.zero_window_refresh_requested and display_state_key == self.zero_window_state_key:
                return

            zero_frame = np.array(self.Andor.zero, copy=True)
            display_frame = zero_frame

        if display_frame is None:
            display_frame = np.zeros_like(zero_frame, dtype=np.float32)

        display_frame = self._downsample_preview_frame(display_frame)
        self.zero_reference_window.ensure_texture_shape(display_frame.shape[0], display_frame.shape[1])
        dpg.set_value(self.zero_reference_window.texture_id, self._zero_frame_to_rgba(display_frame))
        self.zero_window_state_key = display_state_key
        self.zero_window_refresh_requested = False

    def _frame_to_rgba(self, frame, min_value=None, max_value=None):
        if frame is None:
            return np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32)

        # Determine the valid-pixel mask (crop excludes the blacked-out border from scaling).
        crop_mask = self._compute_crop_mask(frame.shape[0], frame.shape[1], self.crop_percent)

        if self._is_signed_zero_reference_mode_active():
            signed_frame = frame.astype(np.float32, copy=False)
            signed_display_limit = self._get_signed_display_limit()

            if self.autoscale_enabled:
                valid_pixels = signed_frame[crop_mask] if crop_mask is not None else signed_frame.ravel()
                if valid_pixels.size > 0:
                    data_min = float(np.min(valid_pixels))
                    data_max = float(np.max(valid_pixels))
                else:
                    data_min, data_max = 0.0, 1.0
                if data_max <= data_min:
                    data_max = data_min + 1.0
                grace_fraction = self.autoscale_grace_percent / 100.0
                padding = (data_max - data_min) * grace_fraction
                min_value = max(-signed_display_limit, data_min - padding)
                max_value = min(signed_display_limit, data_max + padding)
                if self.mirrored_difference_scale:
                    amplitude = max(abs(min_value), abs(max_value))
                    min_value = -amplitude
                    max_value = amplitude
            else:
                if min_value is None:
                    min_value = self.scale_min
                if max_value is None:
                    max_value = self.scale_max
                min_value = float(np.clip(min_value, -signed_display_limit, signed_display_limit))
                max_value = float(np.clip(max_value, -signed_display_limit, signed_display_limit))
                if self.mirrored_difference_scale:
                    amplitude = max(abs(min_value), abs(max_value))
                    min_value = -amplitude
                    max_value = amplitude

            if max_value <= min_value:
                max_value = min_value + 1.0

            normalized = self._normalize_double_sided_frame(signed_frame, min_value, max_value)
            self._last_frame_display_min = min_value
            self._last_frame_display_max = max_value
            return self._apply_colormap(normalized, double_sided=True)

        if self.autoscale_enabled:
            valid_pixels = frame[crop_mask] if crop_mask is not None else frame.ravel()
            if valid_pixels.size > 0:
                data_min = float(np.min(valid_pixels))
                data_max = float(np.max(valid_pixels))
            else:
                data_min, data_max = 0.0, float(self.display_max)
            if data_max <= data_min:
                data_max = data_min + 1.0
            grace_fraction = self.autoscale_grace_percent / 100.0
            padding = (data_max - data_min) * grace_fraction
            min_value = max(0.0, data_min - padding)
            max_value = min(float(self.display_max), data_max + padding)
        else:
            if min_value is None:
                min_value = self.scale_min
            if max_value is None:
                max_value = self.scale_max

        if max_value <= min_value:
            max_value = min_value + 1

        scaled = np.clip((frame.astype(np.float32) - min_value) / (max_value - min_value), 0.0, 1.0)
        self._last_frame_display_min = min_value
        self._last_frame_display_max = max_value
        return self._apply_colormap(scaled, double_sided=False)

    def frame_to_rgba(self, frame):
        return self._frame_to_rgba(frame)

    def _process_frame(self, frame=None):
        if frame is None:
            with self.Andor.processed_frame_condition:
                frame = self.Andor.processed_frame
        return self.make_display_rgba(frame)

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
        if not hasattr(self, "image_draw_id") or not dpg.does_item_exist(self.image_draw_id):
            return

        left, top, right, bottom = self._get_view_bounds()
        dpg.configure_item(
            self.image_draw_id,
            pmin=(0, 0),
            pmax=(self.width, self.feed_height),
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
        image_x = int(round(left + ((local_x / canvas_width) * (right - left))))
        image_y = int(round(top + ((local_y / canvas_height) * (bottom - top))))
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

        return self._normalize_bounds(
            (
                int(round(center_x - half_width)),
                int(round(center_y - half_height)),
                int(round(center_x + half_width)),
                int(round(center_y + half_height)),
            )
        )

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
        roi.request_trace_rebuild()
        self.rois_window.rebuild_layout(self.rois)
        return roi

    def _roi_state_prefix(self):
        return f"RegionOfInterest_{self.tag}_ROI_"

    def _prune_stale_roi_state_files(self, active_state_names):
        active_state_names = set(active_state_names)
        for state_name in list_state_files(prefix=self._roi_state_prefix()):
            if state_name in active_state_names:
                continue
            delete_state_file(state_name)

    def _close_roi(self, tag, delete_state=True):
        roi_to_remove = None
        for roi in self.rois:
            if roi.tag == tag:
                roi_to_remove = roi
                break

        if roi_to_remove is None:
            return

        if delete_state:
            delete_state_file(roi_to_remove.state_name())
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

    def _on_colorbar_enabled_changed(self, sender, app_data):
        self.colorbar_enabled = bool(app_data)
        self._redraw_colorbar()

    def _format_colorbar_value(self, value):
        abs_val = abs(value)
        if abs_val == 0.0:
            return "0"
        elif abs_val >= 10000:
            return f"{value:.0f}"
        elif abs_val >= 1000:
            return f"{value:.1f}"
        elif abs_val >= 10:
            return f"{value:.2f}"
        else:
            return f"{value:.3f}"

    def _redraw_colorbar(self):
        if not hasattr(self, "colorbar_layer") or not dpg.does_item_exist(self.colorbar_layer):
            return
        dpg.delete_item(self.colorbar_layer, children_only=True)
        if not self.colorbar_enabled:
            return

        canvas_w, canvas_h = self._get_canvas_size()
        bar_w = 18
        margin_r = 10
        num_segments = 64
        label_gap = 5
        num_ticks = 5
        bg_pad = 5

        bar_h = max(40, min(220, canvas_h - 50))
        bar_y1 = 20
        bar_y2 = bar_y1 + bar_h

        label_w = 58
        bar_x2 = canvas_w - margin_r - label_w - label_gap
        bar_x1 = bar_x2 - bar_w

        if bar_x1 < 0:
            return

        is_double_sided = self._is_signed_zero_reference_mode_active()
        lut = self._get_colormap_lut(double_sided=is_double_sided, samples=num_segments)

        dpg.draw_rectangle(
            (bar_x1 - bg_pad, bar_y1 - bg_pad - 8),
            (bar_x2 + label_gap + label_w + bg_pad, bar_y2 + bg_pad + 8),
            fill=(15, 15, 15, 160),
            color=(0, 0, 0, 0),
            parent=self.colorbar_layer,
        )

        seg_h = bar_h / num_segments
        for i in range(num_segments):
            lut_idx = num_segments - 1 - i
            r, g, b = lut[lut_idx]
            fill_color = (int(r * 255), int(g * 255), int(b * 255), 255)
            dpg.draw_rectangle(
                (bar_x1, bar_y1 + i * seg_h),
                (bar_x2, bar_y1 + (i + 1) * seg_h),
                fill=fill_color,
                color=(0, 0, 0, 0),
                parent=self.colorbar_layer,
            )

        dpg.draw_rectangle(
            (bar_x1, bar_y1), (bar_x2, bar_y2),
            color=(180, 180, 180, 180),
            fill=(0, 0, 0, 0),
            thickness=1,
            parent=self.colorbar_layer,
        )

        scale_min = self._last_frame_display_min
        scale_max = self._last_frame_display_max

        for i in range(num_ticks):
            t = i / (num_ticks - 1)
            tick_y = bar_y1 + t * bar_h
            value = scale_max + t * (scale_min - scale_max)
            dpg.draw_line(
                (bar_x2, tick_y), (bar_x2 + label_gap, tick_y),
                color=(180, 180, 180, 200),
                thickness=1,
                parent=self.colorbar_layer,
            )
            dpg.draw_text(
                (bar_x2 + label_gap + 2, tick_y - 6),
                self._format_colorbar_value(value),
                color=(230, 230, 230, 220),
                size=12,
                parent=self.colorbar_layer,
            )

    def _redraw_overlay(self):
        if not hasattr(self, "overlay_layer") or not dpg.does_item_exist(self.overlay_layer):
            return

        dpg.delete_item(self.overlay_layer, children_only=True)

        for roi in self.rois:
            display_bounds = self._bounds_to_display(roi.get_bounds())
            x1, y1, x2, y2 = display_bounds
            is_selected = roi is self.selected_roi
            is_hovered = self.hover_target is not None and self.hover_target[0] is roi

            border_color = (0, 220, 140, 255) if is_selected else (255, 190, 40, 220)
            fill_color = (0, 220, 140, 35) if is_selected else (255, 190, 40, 25)
            dpg.draw_rectangle(
                (x1, y1),
                (x2, y2),
                color=border_color,
                fill=fill_color,
                thickness=2,
                parent=self.overlay_layer,
            )
            dpg.draw_text(
                (x1 + 6, max(0, y1 - 18)),
                roi.name,
                color=border_color,
                size=14,
                parent=self.overlay_layer,
            )

            if is_selected or is_hovered:
                for handle_pos in self._get_handle_positions(display_bounds).values():
                    handle_x, handle_y = handle_pos
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
            dpg.draw_rectangle(
                (x1, y1),
                (x2, y2),
                color=(80, 170, 255, 255),
                fill=(80, 170, 255, 25),
                thickness=2,
                parent=self.overlay_layer,
            )


    def _process_camera_feed(self):
        rendered_idx = -1
        while not self._processing_stop_event.is_set():
            try:
                with self.Andor.processed_frame_condition:
                    if self.Andor.processed_frame_idx == rendered_idx:
                        self.Andor.processed_frame_condition.wait(timeout=0.016)
                        continue
                    rendered_idx = self.Andor.processed_frame_idx
                    frame_ref = self.Andor.processed_frame

                rgba = self._frame_to_rgba(frame_ref)

                with self._image_state_lock:
                    self.imageArray = rgba
                    self.image_dirty = True

                self._request_zero_window_refresh()

            except Exception as e:
                print("Error updating camera feed:")
                print(e)
                print()

    def _processing_loop(self):
        while not self._processing_stop_event.is_set():
            fired = self.Andor.frame_ready_event.wait(timeout=0.1)
            if not fired:
                continue
            self.Andor.frame_ready_event.clear()

            with self.Andor.frame_lock:
                current_idx = int(self.Andor.frameIdx)
                raw_frame = np.array(self.Andor.latest_frame, copy=True)

            processed = self.process_frame(raw_frame)

            with self.Andor.processed_frame_condition:
                self.Andor.processed_frame = processed
                self.Andor.processed_frame_idx = current_idx
                self.Andor.processed_frame_condition.notify_all()

    def process_frame(self, raw_frame):
        frame_f32 = np.asarray(raw_frame, dtype=np.float32)

        # LP filter
        if self.lp_filter_enabled:
            coefficients = self.Andor.get_lp_filter_coefficients()
            filtered_f32 = self.Andor.apply_lp_filter_step(
                frame_f32,
                self._lp_filter_previous_input,
                self._lp_filter_previous_output,
                coefficients,
            )
            self._lp_filter_previous_input = np.array(frame_f32, copy=True)
            self._lp_filter_previous_output = np.array(filtered_f32, copy=True)
            source_frame = np.array(self.Andor.coerce_raw_frame_to_storage(filtered_f32), copy=True)
        else:
            source_frame = np.array(raw_frame, copy=True)
            self._lp_filter_previous_input = None
            self._lp_filter_previous_output = None

        # Drift correction — shift is always estimated from the raw frame so that LP filter
        # temporal lag does not corrupt the estimate; the correction is applied to source_frame.
        if self.drift_correction_enabled:
            source_frame = self._apply_drift_correction_for_display(
                np.asarray(source_frame, dtype=np.float32),
                estimate_from=frame_f32,
            )
        else:
            self._drift_valid_mask = None

        # Background removal (after drift, before zero reference).
        # Applied in all modes including Difference and Contrast so that the static
        # illumination profile is removed from both the current frame and the zero
        # reference before computing the difference or contrast ratio.
        bg_removal_active = self.bg_removal_enabled
        if bg_removal_active:
            source_frame = self._apply_background_removal(
                np.asarray(source_frame, dtype=np.float32), self.bg_removal_sigma
            )

        # Zero reference (Difference / Contrast modes)
        with self.Andor.frame_lock:
            zero_frame_raw = np.array(self.Andor.zero, copy=True) if self._is_signed_zero_reference_mode_active() else None

        if zero_frame_raw is not None and bg_removal_active:
            zero_frame = self._apply_background_removal(
                np.asarray(zero_frame_raw, dtype=np.float32), self.bg_removal_sigma
            )
        else:
            zero_frame = zero_frame_raw

        result = np.asarray(
            self._process_analysis_frame(source_frame, zero_frame=zero_frame),
            dtype=np.float32,
        )

        # Zero out the drift-shifted border so it doesn't contaminate Difference/Contrast scaling.
        if self._drift_valid_mask is not None:
            result = np.array(result, copy=True)
            result[~self._drift_valid_mask] = 0.0

        # Crop: set pixels outside the centred crop region to 0 as the final display step.
        crop_mask = self._compute_crop_mask(result.shape[0], result.shape[1], self.crop_percent)
        if crop_mask is not None:
            result = np.array(result, copy=True)
            result[~crop_mask] = 0.0

        return result

    def _reset_processing_state(self):
        self._lp_filter_previous_input  = None
        self._lp_filter_previous_output = None

    def _on_left_mouse_down(self, sender, app_data):
        self._on_mouse_down(sender, app_data, dpg.mvMouseButton_Left)

    def _on_middle_mouse_down(self, sender, app_data):
        self._on_mouse_down(sender, app_data, dpg.mvMouseButton_Middle)

    def _on_mouse_down(self, sender, app_data, mouse_button):
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
            self.interaction = {
                "mode": "create",
                "start_image": image_point,
            }
        else:
            roi, handle = hit
            self.selected_roi = roi
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
            previous_hover_target = self.hover_target
            if self._point_in_canvas(local_point):
                self.hover_target = self._hit_test(local_point)
            else:
                self.hover_target = None
            if self.hover_target != previous_hover_target:
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
            new_bounds = self._move_bounds(self.interaction["anchor_bounds"], delta_x, delta_y)
            self.interaction["roi"].set_bounds(new_bounds)
        elif mode == "pan":
            start_local_x, start_local_y = self.interaction["start_local"]
            delta_local_x = local_point[0] - start_local_x
            delta_local_y = local_point[1] - start_local_y
            self._pan_view(delta_local_x, delta_local_y, self.interaction["anchor_center"])
        elif mode == "resize":
            if self._is_center_resize_active() and self._is_square_constraint_active():
                new_bounds = self._center_resize_bounds(
                    self.interaction["anchor_bounds"],
                    current_image,
                    self.interaction["handle"],
                    square=True,
                )
            elif self._is_center_resize_active():
                new_bounds = self._center_resize_bounds(
                    self.interaction["anchor_bounds"],
                    current_image,
                    self.interaction["handle"],
                    square=False,
                )
            elif self._is_square_constraint_active():
                new_bounds = self._square_bounds_from_resize(
                    self.interaction["anchor_bounds"],
                    current_image,
                    self.interaction["handle"],
                )
            else:
                new_bounds = self._resize_bounds(
                    self.interaction["anchor_bounds"],
                    current_image,
                    self.interaction["handle"],
                )
            self.interaction["roi"].set_bounds(new_bounds)

        self._redraw_overlay()

    def _on_mouse_release(self, sender, app_data):
        if self.interaction is None:
            return

        if self.interaction["mode"] == "create" and self.preview_bounds is not None:
            self._create_roi(self.preview_bounds)
            self.preview_bounds = None
        elif self.interaction["mode"] in ("move", "resize"):
            # Use nan_pad=True so the existing trace data is preserved but
            # marked as invalid (nan gaps) instead of being reset to x=0.
            self.interaction["roi"].request_trace_rebuild(nan_pad=True)

        self.interaction = None
        self._redraw_overlay()

    def _on_mouse_wheel(self, sender, app_data):
        if not self._is_canvas_hovered():
            return

        self._set_zoom_at_point(app_data, self._get_mouse_local())

    def _on_right_mouse_down(self, sender, app_data):
        if not self._is_canvas_hovered():
            self.context_menu_roi = None
            if hasattr(self, "set_frame_to_roi_button_id") and dpg.does_item_exist(self.set_frame_to_roi_button_id):
                dpg.configure_item(self.set_frame_to_roi_button_id, show=False)
            return

        hit = self._hit_test(self._get_mouse_local())
        self.context_menu_roi = hit[0] if hit is not None else None
        if hasattr(self, "set_frame_to_roi_button_id") and dpg.does_item_exist(self.set_frame_to_roi_button_id):
            dpg.configure_item(self.set_frame_to_roi_button_id, show=self.context_menu_roi is not None)

    def _get_camera_system_controller(self):
        for obj in class_objects:
            if getattr(obj, "camera_feed", None) is self:
                return obj
        for obj in class_objects:
            if getattr(obj, "Andor", None) is self.Andor and hasattr(obj, "_apply_aoi_settings"):
                return obj
        return None

    def _on_set_frame_to_roi(self, sender=None, app_data=None, user_data=None):
        roi = self.context_menu_roi
        if roi is None:
            return

        controller = self._get_camera_system_controller()
        if controller is None:
            return

        x1, y1, x2, y2 = roi.get_bounds()
        requested_aoi = {
            "width": max(1, int(x2 - x1)),
            "height": max(1, int(y2 - y1)),
            "left": int(x1) + 1,
            "top": int(y1) + 1,
        }

        controller.aoi_auto_center_enabled = False
        dpg.set_value(controller.settings_aoi_auto_center_checkbox_id, False)
        controller._apply_aoi_settings(requested_aoi)
        controller._refresh_hardware_requirements(force=True)
        self.context_menu_roi = None

    def _on_delete_key_pressed(self, sender, app_data):
        if self.selected_roi is None:
            return
        if not dpg.is_item_focused(self.window_id):
            return

        self._close_roi(self.selected_roi.tag)
        self._redraw_overlay()

    def _state_name(self):
        return f"{type(self).__name__}_{self.tag}"

    def SaveState(self):
        roi_state_names = []
        for roi in self.rois:
            roi.SaveState()
            roi_state_names.append(roi.state_name())

        if self.controls_window is not None:
            self.controls_window.SaveState()
        if self.zero_reference_window is not None:
            self.zero_reference_window.SaveState()
        if self.rois_window is not None:
            self.rois_window.SaveState()

        save_state_file(
            self._state_name(),
            {
                "window": capture_window_state(self.window_id),
                "autoscale_enabled": bool(self.autoscale_enabled),
                "colorbar_enabled": bool(self.colorbar_enabled),
                "scale_min": float(self.scale_min),
                "scale_max": float(self.scale_max),
                "scale_min_percent": float(self.get_scale_min_percent()),
                "scale_max_percent": float(self.get_scale_max_percent()),
                "autoscale_grace_percent": float(self.autoscale_grace_percent),
                "display_mode": self.display_mode,
                "mirrored_difference_scale": bool(self.mirrored_difference_scale),
                "scale_state_per_mode": self._scale_state_per_mode,
                "colormap_name": self.colormap_name,
                "colormap_per_mode": dict(self._colormap_per_mode),
                "lp_filter_enabled": bool(self.lp_filter_enabled),
                "lp_filter_cutoff_hz": float(self.lp_filter_cutoff_hz),
                "drift_correction_enabled": bool(self.drift_correction_enabled),
                "bg_removal_enabled": bool(self.bg_removal_enabled),
                "bg_removal_sigma": float(self.bg_removal_sigma),
                "crop_percent": float(self.crop_percent),
                "zoom": float(self.zoom),
                "view_center_x": float(self.view_center_x),
                "view_center_y": float(self.view_center_y),
                "roi_index": int(self.roi_index),
                "roi_state_names": roi_state_names,
            },
        )

    def LoadState(self):
        state = load_state_file(self._state_name())

        if self.controls_window is not None:
            self.controls_window.LoadState()
        if self.zero_reference_window is not None:
            self.zero_reference_window.LoadState()
        if self.rois_window is not None:
            self.rois_window.LoadState()

        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))
        self._on_window_resize()

        self.autoscale_enabled = bool(state.get("autoscale_enabled", self.autoscale_enabled))
        self.colorbar_enabled = bool(state.get("colorbar_enabled", self.colorbar_enabled))
        self.autoscale_grace_percent = float(state.get("autoscale_grace_percent", self.autoscale_grace_percent))
        self.display_mode = str(state.get("display_mode", self.display_mode))
        self.mirrored_difference_scale = bool(state.get("mirrored_difference_scale", self.mirrored_difference_scale))
        self.colormap_name = self._parse_colormap_label(state.get("colormap_name", self.colormap_name))
        saved_per_mode = state.get("colormap_per_mode", {})
        if isinstance(saved_per_mode, dict):
            for mode, name in saved_per_mode.items():
                parsed = self._parse_colormap_label(name)
                if parsed:
                    self._colormap_per_mode[mode] = parsed
        self._colormap_per_mode[self.display_mode] = self.colormap_name
        self._ensure_valid_colormap_selection()

        if "scale_min_percent" in state and "scale_max_percent" in state:
            self.scale_min = float(self._scale_percent_to_value(state.get("scale_min_percent", self.get_scale_min_percent())))
            self.scale_max = float(self._scale_percent_to_value(state.get("scale_max_percent", self.get_scale_max_percent())))
        else:
            self.scale_min = float(state.get("scale_min", self.scale_min))
            self.scale_max = float(state.get("scale_max", self.scale_max))

        # Seed the current mode's entry so switching away and back restores these values.
        self._scale_state_per_mode[self.display_mode] = {
            "scale_min": self.scale_min,
            "scale_max": self.scale_max,
            "autoscale_enabled": self.autoscale_enabled,
            "autoscale_grace_percent": self.autoscale_grace_percent,
            "mirrored_difference_scale": self.mirrored_difference_scale,
        }
        saved_scale_per_mode = state.get("scale_state_per_mode", {})
        if isinstance(saved_scale_per_mode, dict):
            for _mode, _ms in saved_scale_per_mode.items():
                if isinstance(_ms, dict) and str(_mode) != self.display_mode:
                    self._scale_state_per_mode[str(_mode)] = {
                        "scale_min": float(_ms.get("scale_min", 0.0)),
                        "scale_max": float(_ms.get("scale_max", float(self.display_max))),
                        "autoscale_enabled": bool(_ms.get("autoscale_enabled", True)),
                        "autoscale_grace_percent": float(_ms.get("autoscale_grace_percent", 5.0)),
                        "mirrored_difference_scale": bool(_ms.get("mirrored_difference_scale", False)),
                    }

        self.lp_filter_enabled = bool(state.get("lp_filter_enabled", self.lp_filter_enabled))
        self.lp_filter_cutoff_hz = float(state.get("lp_filter_cutoff_hz", self.lp_filter_cutoff_hz))
        self.drift_correction_enabled = bool(state.get("drift_correction_enabled", self.drift_correction_enabled))
        self.bg_removal_enabled = bool(state.get("bg_removal_enabled", self.bg_removal_enabled))
        self.bg_removal_sigma = float(state.get("bg_removal_sigma", self.bg_removal_sigma))
        self.crop_percent = float(state.get("crop_percent", self.crop_percent))
        self.zoom = float(state.get("zoom", self.zoom))
        self.view_center_x = float(state.get("view_center_x", self.view_center_x))
        self.view_center_y = float(state.get("view_center_y", self.view_center_y))
        self.roi_index = int(state.get("roi_index", self.roi_index))

        self.Andor.set_lp_filter_cutoff_hz(self.lp_filter_cutoff_hz)
        self.Andor.set_lp_filter_enabled(self.lp_filter_enabled)

        dpg.set_value(self.controls_window.autoscale_checkbox_id, self.autoscale_enabled)
        dpg.set_value(self.controls_window.colorbar_checkbox_id, self.colorbar_enabled)
        self._sync_scale_inputs_from_values()
        dpg.set_value(self.controls_window.autoscale_grace_input_id, self.autoscale_grace_percent)
        dpg.set_value(self.controls_window.display_mode_combo_id, self.display_mode)
        dpg.set_value(self.controls_window.mirrored_difference_checkbox_id, self.mirrored_difference_scale)
        dpg.set_value(self.controls_window.color_scale_combo_id, self.get_selected_colormap_label())
        dpg.set_value(self.controls_window.lp_filter_checkbox_id, self.lp_filter_enabled)
        dpg.set_value(self.controls_window.lp_filter_cutoff_input_id, self.lp_filter_cutoff_hz)
        dpg.set_value(self.controls_window.drift_correction_checkbox_id, self.drift_correction_enabled)
        dpg.set_value(self.controls_window.bg_removal_checkbox_id, self.bg_removal_enabled)
        dpg.set_value(self.controls_window.bg_removal_sigma_input_id, self.bg_removal_sigma)
        dpg.configure_item(self.controls_window.bg_removal_sigma_input_id, enabled=self.bg_removal_enabled)
        dpg.set_value(self.controls_window.crop_slider_id, self.crop_percent)

        self._clamp_view_center()
        self._update_image_draw_transform()
        self._update_settings_controls_state()
        self._refresh_display_image()
        self._redraw_colorbar()
        self._update_zero_window_texture_binding()
        self._update_zero_window()

        for roi in list(self.rois):
            self._close_roi(roi.tag, delete_state=False)

        active_roi_state_names = []
        for roi_state_name in state.get("roi_state_names", []):
            roi_state = load_state_file(roi_state_name)
            bounds = roi_state.get("bounds") if isinstance(roi_state, dict) else None
            if not bounds:
                continue
            roi = self._create_roi(bounds)
            if roi is not None:
                roi.LoadState(roi_state_name)
                active_roi_state_names.append(roi_state_name)

        self._prune_stale_roi_state_files(active_roi_state_names)

        self._redraw_overlay()


    def render(self):
        self.zero_reference_window.render()
        self.rois_window.rebuild_layout(self.rois)
        self.rois_window.render()
        self._update_zero_window()
        rois_window_visible = self.rois_window.is_visible()

        pending_image = None
        with self._image_state_lock:
            if self.image_dirty:
                pending_image = self.imageArray
                self.image_dirty = False

        if pending_image is not None and hasattr(self, "texture_id") and dpg.does_item_exist(self.texture_id):
            dpg.set_value(self.texture_id, pending_image)
            self._display_fps_window_frame_count += 1
            elapsed_seconds = time.perf_counter() - self._display_fps_window_started_at
            if elapsed_seconds >= 0.5:
                self.displayed_feed_fps = self._display_fps_window_frame_count / elapsed_seconds
                self._display_fps_window_started_at = time.perf_counter()
                self._display_fps_window_frame_count = 0

            if self.colorbar_enabled:
                curr_min = self._last_frame_display_min
                curr_max = self._last_frame_display_max
                if curr_min != self._colorbar_last_min or curr_max != self._colorbar_last_max:
                    self._colorbar_last_min = curr_min
                    self._colorbar_last_max = curr_max
                    self._redraw_colorbar()

        if rois_window_visible:
            for roi in list(self.rois):
                roi.render()

        self._update_focus_indicator()

    def _update_focus_indicator(self):
        if self.controls_window is None:
            return
        with self.Andor.frame_lock:
            latest_frame = self.Andor.latest_frame
        if latest_frame is None:
            return
        current_score = self._compute_focus_score(latest_frame)
        ref = self._focus_reference_score
        if ref is None or ref <= 0.0:
            self._focus_level = 0.0
            self.controls_window.update_focus_indicator(0.0, has_reference=False)
            return
        raw = (current_score - ref) / (ref + 1e-12)
        self._focus_level = float(np.clip(raw, -1.0, 1.0))
        self.controls_window.update_focus_indicator(self._focus_level, has_reference=True)



