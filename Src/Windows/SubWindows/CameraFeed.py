import threading
import time

import dearpygui.dearpygui as dpg
import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, to_rgb

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
        self.colormap_name = "Viridis"
        self._colormap_per_mode = {
            "Normal": self.single_sided_colormap_names[0],
            "Difference": self.double_sided_colormap_names[0],
            "Contrast": self.double_sided_colormap_names[0],
        }
        self.lp_filter_enabled = bool(getattr(self.Andor, "lp_filter_enabled", False))
        self.lp_filter_cutoff_hz = float(getattr(self.Andor, "lp_filter_cutoff_hz", 10.0))
        self._colormap_lut_cache = {}

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
        self._display_processing_last_frame_idx = -1
        self._display_processing_state_key = None
        self._display_filter_previous_input = None
        self._display_filter_previous_output = None

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
        self._sync_scale_state_to_active_frame()
        self._update_settings_controls_state()
        self._refresh_display_image()
        self._request_all_roi_rebuilds(clear_existing=True)

    def _on_lp_filter_cutoff_changed(self, sender, app_data):
        self.lp_filter_cutoff_hz = max(1e-3, float(dpg.get_value(self.controls_window.lp_filter_cutoff_input_id)))
        dpg.set_value(self.controls_window.lp_filter_cutoff_input_id, self.lp_filter_cutoff_hz)
        self.Andor.set_lp_filter_cutoff_hz(self.lp_filter_cutoff_hz)
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

    def _get_active_source_frame_locked(self):
        latest_frame, _, has_frames, _ = self.Andor.get_processed_frame_view_locked(
            display_mode="Normal",
            lp_filter_enabled=self.lp_filter_enabled,
            include_history=False,
        )
        if not has_frames:
            return None
        return latest_frame

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
            frame, _, _, _, _ = self.get_analysis_snapshot(include_history=False)

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

    def _get_display_processing_state_key_locked(self):
        return (
            str(self.display_mode),
            bool(self.lp_filter_enabled),
            float(self.lp_filter_cutoff_hz),
            int(getattr(self.Andor, "zero_version", 0)),
            str(self.Andor.storage_dtype_name),
        )

    def _reset_display_processing_state(self):
        self._display_processing_last_frame_idx = -1
        self._display_processing_state_key = None
        self._display_filter_previous_input = None
        self._display_filter_previous_output = None

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
            contrast_frame = np.zeros_like(frame_float, dtype=np.float32)
            np.divide(
                difference_frame,
                zero_float,
                out=contrast_frame,
                where=np.abs(zero_float) > 0.0,
            )
            contrast_frame *= 100.0
            return np.array(self.Andor.coerce_signed_frame_to_storage(contrast_frame), copy=True)

        return np.array(frame, copy=True)

    def process_analysis_frame(self, frame, zero_frame=None):
        return self._process_analysis_frame(frame, zero_frame=zero_frame)

    def _get_display_frame_updates_locked(self, force_rebuild=False):
        current_frame_idx = int(self.Andor.frameIdx)
        frame_count = len(self.Andor.acquisitions)
        if frame_count <= 0:
            return None, current_frame_idx, None, False

        first_available_frame_idx = max(0, current_frame_idx - frame_count) + 1
        state_key = self._get_display_processing_state_key_locked()
        needs_reset = bool(force_rebuild) or self._display_processing_state_key != state_key
        if self._display_processing_last_frame_idx < (first_available_frame_idx - 1):
            needs_reset = True

        if needs_reset:
            if self.lp_filter_enabled:
                window_size = self.Andor.get_display_filter_window_size()
                start_frame_idx = max(first_available_frame_idx, current_frame_idx - window_size + 1)
            else:
                start_frame_idx = current_frame_idx
        else:
            start_frame_idx = max(int(self._display_processing_last_frame_idx) + 1, first_available_frame_idx)

        if start_frame_idx > current_frame_idx:
            return None, current_frame_idx, state_key, needs_reset

        start_offset = max(0, start_frame_idx - first_available_frame_idx)
        sample_count = max(0, frame_count - start_offset)
        raw_frames = self.Andor.acquisitions.range_array(start_offset, sample_count, copy=True)
        if raw_frames.size <= 0:
            return None, current_frame_idx, state_key, needs_reset

        zero_frame = None
        if self._is_signed_zero_reference_mode_active():
            zero_frame = np.array(self.Andor.zero, copy=True)

        return (raw_frames, zero_frame), current_frame_idx, state_key, needs_reset

    def _process_display_frame_updates(self, raw_frames, zero_frame=None, reset_state=False):
        if raw_frames is None or len(raw_frames) <= 0:
            return None

        if reset_state:
            self._display_filter_previous_input = None
            self._display_filter_previous_output = None

        coefficients = self.Andor.get_lp_filter_coefficients() if self.lp_filter_enabled else None
        latest_source_frame = None

        for raw_frame in raw_frames:
            if self.lp_filter_enabled:
                current_input = np.asarray(raw_frame, dtype=np.float32)
                filtered_output = self.Andor.apply_lp_filter_step(
                    current_input,
                    self._display_filter_previous_input,
                    self._display_filter_previous_output,
                    coefficients,
                )
                self._display_filter_previous_input = np.array(current_input, copy=True)
                self._display_filter_previous_output = np.array(filtered_output, copy=True)
                latest_source_frame = np.array(self.Andor.coerce_raw_frame_to_storage(filtered_output), copy=True)
            else:
                latest_source_frame = np.array(raw_frame, copy=True)

        if not self.lp_filter_enabled:
            self._display_filter_previous_input = None
            self._display_filter_previous_output = None

        return self._process_analysis_frame(latest_source_frame, zero_frame=zero_frame)

    def _get_latest_display_frame(self, force_rebuild=False):
        with self.Andor.frame_lock:
            update_payload, current_frame_idx, state_key, needs_reset = self._get_display_frame_updates_locked(force_rebuild=force_rebuild)

        if update_payload is None:
            return None

        raw_frames, zero_frame = update_payload
        processed_frame = self._process_display_frame_updates(raw_frames, zero_frame=zero_frame, reset_state=needs_reset)
        self._display_processing_last_frame_idx = current_frame_idx
        self._display_processing_state_key = state_key
        return processed_frame

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

    def ensure_zero_reference_from_latest_frame(self):
        with self.Andor.frame_lock:
            active_frame = self._get_active_source_frame_locked()
            latest_frame = None if active_frame is None else np.array(active_frame, copy=True)

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
        latest_frame = self._get_latest_display_frame(force_rebuild=True)
        if latest_frame is None:
            self.imageArray = self._process_frame(None)
        else:
            self.imageArray = self._frame_to_rgba(self._prepare_analysis_frame(latest_frame))
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

    def get_analysis_snapshot(self, include_history=False, include_timestamps=False, history_start_frame_idx=None):
        with self.Andor.frame_lock:
            latest_frame, current_frame_idx, has_frames, active_history = self.Andor.get_processed_frame_view_locked(
                display_mode=self.display_mode,
                lp_filter_enabled=self.lp_filter_enabled,
                include_history=include_history,
                history_start_frame_idx=history_start_frame_idx,
            )

            timestamps = None
            if include_timestamps:
                if include_history:
                    timestamp_count = len(self.Andor.timestamps)
                    timestamp_start_offset = 0
                    if history_start_frame_idx is not None:
                        first_available_timestamp_frame_idx = max(0, current_frame_idx - timestamp_count) + 1
                        next_timestamp_frame_idx = max(int(history_start_frame_idx) + 1, first_available_timestamp_frame_idx)
                        timestamp_start_offset = max(0, next_timestamp_frame_idx - first_available_timestamp_frame_idx)
                    timestamps = self.Andor.timestamps[timestamp_start_offset:]
                elif len(self.Andor.timestamps) > 0:
                    timestamps = [float(self.Andor.timestamps[-1])]
                else:
                    timestamps = []
            acquisitions = active_history if include_history else None

        return latest_frame, current_frame_idx, has_frames, acquisitions, timestamps

    def get_roi_processing_update(self, bounds, include_history=False, include_timestamps=False, history_start_frame_idx=None):
        x1, y1, x2, y2 = self._normalize_bounds(bounds)
        if x2 <= x1 or y2 <= y1:
            return None, 0, False, None, None, None, None, 0

        with self.Andor.frame_lock:
            current_frame_idx = int(self.Andor.frameIdx)
            frame_count = len(self.Andor.acquisitions)
            if frame_count <= 0:
                return None, current_frame_idx, False, None, None, None, None, 0

            first_available_frame_idx = max(0, current_frame_idx - frame_count) + 1
            if include_history:
                if history_start_frame_idx is None:
                    start_frame_idx = first_available_frame_idx
                else:
                    start_frame_idx = max(int(history_start_frame_idx) + 1, first_available_frame_idx)
            else:
                start_frame_idx = current_frame_idx

            if start_frame_idx > current_frame_idx:
                return None, current_frame_idx, False, None, None, None, None, first_available_frame_idx

            start_offset = max(0, start_frame_idx - first_available_frame_idx)
            sample_count = max(0, frame_count - start_offset)
            raw_frames = self.Andor.acquisitions.range_array(start_offset, sample_count, copy=True)
            if raw_frames.size <= 0:
                return None, current_frame_idx, False, None, None, None, None, first_available_frame_idx

            raw_crops = np.array(raw_frames[:, y1:y2, x1:x2], copy=True)
            if raw_crops.size <= 0:
                return None, current_frame_idx, False, None, None, None, None, first_available_frame_idx

            timestamps = None
            if include_timestamps:
                timestamp_start_offset = start_offset
                timestamp_values = self.Andor.timestamps.range_array(timestamp_start_offset, sample_count, copy=True)
                timestamps = timestamp_values.tolist()

            zero_crop = np.array(self.Andor.zero[y1:y2, x1:x2], copy=True)
            state_key = (
                str(self.display_mode),
                bool(self.lp_filter_enabled),
                float(self.lp_filter_cutoff_hz),
                int(getattr(self.Andor, "zero_version", 0)),
                str(self.Andor.storage_dtype_name),
                (x1, y1, x2, y2),
            )

        latest_crop = np.array(raw_crops[-1], copy=True)
        return latest_crop, current_frame_idx, True, raw_crops, timestamps, zero_crop, state_key, first_available_frame_idx

    def _on_set_zero(self, sender=None, app_data=None, user_data=None):
        self.ensure_zero_reference_from_latest_frame()

    def _on_display_mode_changed(self, sender, app_data):
        self.display_mode = str(app_data)
        if self._is_signed_zero_reference_mode_active():
            self.mirrored_difference_scale = True
            dpg.set_value(self.controls_window.mirrored_difference_checkbox_id, True)
        else:
            self.mirrored_difference_scale = False
            dpg.set_value(self.controls_window.mirrored_difference_checkbox_id, False)

        self.colormap_name = self._colormap_per_mode.get(self.display_mode, self._get_default_colormap_name())
        self._ensure_valid_colormap_selection()
        self._sync_scale_state_to_active_frame()
        self._request_zero_window_refresh()
        self._refresh_display_image()
        self._update_zero_window_texture_binding()
        self._update_settings_controls_state()
        self._request_all_roi_rebuilds()
        self._update_zero_window()

    def _on_colormap_changed(self, sender, app_data):
        self.colormap_name = self._parse_colormap_label(app_data)
        self._ensure_valid_colormap_selection()
        self._colormap_per_mode[self.display_mode] = self.colormap_name
        self._update_colormap_controls_state()
        self._request_zero_window_refresh()
        self._refresh_display_image()
        self._request_all_roi_rebuilds()
        self._update_zero_window()

    def _prepare_analysis_frame(self, frame):
        if frame is None:
            return None

        return frame

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

        if self._is_signed_zero_reference_mode_active():
            signed_frame = frame.astype(np.float32, copy=False)
            signed_display_limit = self._get_signed_display_limit()

            if self.autoscale_enabled:
                data_min = float(np.min(signed_frame))
                data_max = float(np.max(signed_frame))
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
            return self._apply_colormap(normalized, double_sided=True)

        if self.autoscale_enabled:
            data_min = float(np.min(frame))
            data_max = float(np.max(frame))
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
        return self._apply_colormap(scaled, double_sided=False)

    def frame_to_rgba(self, frame):
        return self._frame_to_rgba(frame)

    def _process_frame(self, frame=None):
        latest_frame = frame
        if latest_frame is None:
            latest_frame, _, _, _, _ = self.get_analysis_snapshot(include_history=False)
        return self._frame_to_rgba(self._prepare_analysis_frame(latest_frame))

    def get_roi_frame(self, bounds):
        crop, frame_idx, has_frames, _, _, zero_crop, state_key, first_available_frame_idx = self.get_roi_processing_update(
            bounds,
            include_history=False,
            include_timestamps=False,
        )
        if not has_frames or crop is None:
            return None, frame_idx

        raw_crops = np.expand_dims(crop, axis=0)
        lp_filter_enabled = bool(state_key[1]) if state_key is not None else False
        if lp_filter_enabled:
            window_size = self.Andor.get_display_filter_window_size()
            if window_size > 1:
                history_start_frame_idx = max(first_available_frame_idx - 1, frame_idx - window_size)
                _, _, has_frames, raw_crops, _, zero_crop, state_key, _ = self.get_roi_processing_update(
                    bounds,
                    include_history=True,
                    include_timestamps=False,
                    history_start_frame_idx=history_start_frame_idx,
                )
                if not has_frames or raw_crops is None:
                    return None, frame_idx

        latest_processed_crop = None
        filter_previous_input = None
        filter_previous_output = None
        coefficients = self.Andor.get_lp_filter_coefficients() if lp_filter_enabled else None
        for raw_crop in raw_crops:
            if lp_filter_enabled:
                current_input = np.asarray(raw_crop, dtype=np.float32)
                filtered_output = self.Andor.apply_lp_filter_step(current_input, filter_previous_input, filter_previous_output, coefficients)
                filter_previous_input = np.array(current_input, copy=True)
                filter_previous_output = np.array(filtered_output, copy=True)
                source_crop = np.array(self.Andor.coerce_raw_frame_to_storage(filtered_output), copy=True)
            else:
                source_crop = np.array(raw_crop, copy=True)
            latest_processed_crop = self._process_analysis_frame(source_crop, zero_frame=zero_crop)

        return latest_processed_crop, frame_idx

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
        while True:

            try:
                if self.Andor.frame_ready_event.wait(0.016):
                    self.Andor.frame_ready_event.clear()
                    latest_frame = self._get_latest_display_frame(force_rebuild=False)
                    processed_image = self._frame_to_rgba(self._prepare_analysis_frame(latest_frame)) if latest_frame is not None else self._process_frame()
                    with self._image_state_lock:
                        self.imageArray = processed_image
                        self.image_dirty = True
                    self._request_zero_window_refresh()

            except Exception as e:
                print("Error updating camera feed:")
                print(e)
                print()

            time.sleep(0.001)

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
            self.interaction["roi"].request_trace_rebuild()

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
                "scale_min": float(self.scale_min),
                "scale_max": float(self.scale_max),
                "scale_min_percent": float(self.get_scale_min_percent()),
                "scale_max_percent": float(self.get_scale_max_percent()),
                "autoscale_grace_percent": float(self.autoscale_grace_percent),
                "display_mode": self.display_mode,
                "mirrored_difference_scale": bool(self.mirrored_difference_scale),
                "colormap_name": self.colormap_name,
                "colormap_per_mode": dict(self._colormap_per_mode),
                "lp_filter_enabled": bool(self.lp_filter_enabled),
                "lp_filter_cutoff_hz": float(self.lp_filter_cutoff_hz),
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

        self.lp_filter_enabled = bool(state.get("lp_filter_enabled", self.lp_filter_enabled))
        self.lp_filter_cutoff_hz = float(state.get("lp_filter_cutoff_hz", self.lp_filter_cutoff_hz))
        self.zoom = float(state.get("zoom", self.zoom))
        self.view_center_x = float(state.get("view_center_x", self.view_center_x))
        self.view_center_y = float(state.get("view_center_y", self.view_center_y))
        self.roi_index = int(state.get("roi_index", self.roi_index))

        self.Andor.set_lp_filter_cutoff_hz(self.lp_filter_cutoff_hz)
        self.Andor.set_lp_filter_enabled(self.lp_filter_enabled)

        dpg.set_value(self.controls_window.autoscale_checkbox_id, self.autoscale_enabled)
        self._sync_scale_inputs_from_values()
        dpg.set_value(self.controls_window.autoscale_grace_input_id, self.autoscale_grace_percent)
        dpg.set_value(self.controls_window.display_mode_combo_id, self.display_mode)
        dpg.set_value(self.controls_window.mirrored_difference_checkbox_id, self.mirrored_difference_scale)
        dpg.set_value(self.controls_window.color_scale_combo_id, self.get_selected_colormap_label())
        dpg.set_value(self.controls_window.lp_filter_checkbox_id, self.lp_filter_enabled)
        dpg.set_value(self.controls_window.lp_filter_cutoff_input_id, self.lp_filter_cutoff_hz)

        self._clamp_view_center()
        self._update_image_draw_transform()
        self._update_settings_controls_state()
        self._refresh_display_image()
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

        if rois_window_visible:
            for roi in list(self.rois):
                roi.render()



