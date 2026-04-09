import threading
import time

import dearpygui.dearpygui as dpg
import numpy as np

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import no_padding_theme, read_only_theme
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

    def __init__(self, parent, Andor):

        self.parent = parent
        self.Andor = Andor

        self.width = 600
        self.feed_height = 600
        self.height = self.feed_height

        self.name = "Camera Feed"
        self.tag = "CameraFeed"
        self.display_max = (2 ** int(getattr(self.Andor.camera, "BitDepth", 16))) - 1
        self.scale_min = 0.0
        self.scale_max = float(self.display_max)
        self.autoscale_enabled = True
        self.autoscale_grace_percent = 5.0
        self.display_mode = "Normal"
        self.mirrored_difference_scale = False
        self.positive_difference_color = np.array([255.0, 0.0, 0.0], dtype=np.float32)
        self.negative_difference_color = np.array([0.0, 0.0, 255.0], dtype=np.float32)
        self.lp_filter_enabled = bool(getattr(self.Andor, "lp_filter_enabled", False))
        self.lp_filter_cutoff_hz = float(getattr(self.Andor, "lp_filter_cutoff_hz", 10.0))

        self.image_width = int(self.Andor.camera.AOIWidth)
        self.image_height = int(self.Andor.camera.AOIHeight)
        self.imageArray = self._process_frame(self.Andor.latest_frame)

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
        self.zero_window_refresh_requested = True
        self.zero_window_state_key = None
        self.zero_preview_max_dimension = 512
        self.controls_window = None
        self.zero_reference_window = None

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
                dpg.add_mouse_move_handler(callback=self._on_mouse_move)
                dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_release)
                dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_release)
                dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)

            with dpg.popup(self.canvas_id, mousebutton=dpg.mvMouseButton_Right):
                dpg.add_button(label="Reset Zoom", width=120, callback=self._reset_zoom)

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

    def reset_texture(self):
        self.image_width = int(self.Andor.camera.AOIWidth)
        self.image_height = int(self.Andor.camera.AOIHeight)
        self.display_max = (2 ** int(getattr(self.Andor.camera, "BitDepth", 16))) - 1
        self.scale_max = float(self.display_max)
        self.imageArray = self._process_frame(self.Andor.latest_frame)
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
        self._redraw_overlay()

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
        signed_display_limit = self._get_signed_display_limit()

        slider_min = -signed_display_limit if is_signed_zero_reference_mode else 0.0
        slider_max = signed_display_limit if is_signed_zero_reference_mode else float(self.display_max)
        dpg.configure_item(self.controls_window.scale_min_input_id, min_value=slider_min, max_value=slider_max)
        dpg.configure_item(self.controls_window.scale_max_input_id, min_value=slider_min, max_value=slider_max)

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

        dpg.configure_item(self.controls_window.lp_filter_cutoff_input_id, enabled=self.lp_filter_enabled)
        dpg.bind_item_theme(self.controls_window.lp_filter_cutoff_input_id, None if self.lp_filter_enabled else read_only_theme)

    def _on_autoscale_changed(self, sender, app_data):
        self.autoscale_enabled = bool(app_data)
        self._update_settings_controls_state()
        self._refresh_display_image()

    def _on_scale_limits_changed(self, sender, app_data):
        scale_min = float(dpg.get_value(self.controls_window.scale_min_input_id))
        scale_max = float(dpg.get_value(self.controls_window.scale_max_input_id))

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
        dpg.set_value(self.controls_window.scale_min_input_id, self.scale_min)
        dpg.set_value(self.controls_window.scale_max_input_id, self.scale_max)
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
            dpg.set_value(self.controls_window.scale_min_input_id, self.scale_min)
            dpg.set_value(self.controls_window.scale_max_input_id, self.scale_max)
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

    def _get_signed_display_limit(self):
        if self._is_contrast_mode_active():
            return 200.0
        return self.difference_display_limit

    def _get_active_source_frame_locked(self):
        if self.lp_filter_enabled:
            if len(self.Andor.filtered) == 0:
                return self.Andor.latest_filtered
            return self.Andor.filtered[-1]

        if self.Andor.latest_frame is None:
            return None
        return self.Andor.latest_frame

    def _normalize_color_value(self, value):
        color = np.array(value[:3], dtype=np.float32)
        if np.max(color) <= 1.0:
            color *= 255.0
        return np.clip(color, 0.0, 255.0)

    def _request_all_roi_rebuilds(self, clear_existing=False):
        for roi in self.rois:
            roi.request_trace_rebuild(clear_existing=clear_existing)

    def rebuild_roi_traces(self):
        self._request_all_roi_rebuilds(clear_existing=True)

    def recalculate_rois(self):
        self._request_all_roi_rebuilds()
        self._redraw_overlay()

    def _compute_display_bounds(self, frame=None):
        if frame is None:
            frame, _, _, _ = self.get_analysis_snapshot(include_history=False)

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
        dpg.set_value(self.controls_window.scale_min_input_id, self.scale_min)
        dpg.set_value(self.controls_window.scale_max_input_id, self.scale_max)

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
        if self._is_difference_mode_active():
            if len(self.Andor.difference) == 0:
                return self.Andor.latest_difference
            return self.Andor.difference[-1]
        if self._is_contrast_mode_active():
            if len(self.Andor.contrast) == 0:
                return self.Andor.latest_contrast
            return self.Andor.contrast[-1]
        return self._get_active_source_frame_locked()

    def _refresh_display_image(self):
        self.imageArray = self._process_frame(None)
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
        rgba = np.empty((frame.shape[0], frame.shape[1], 4), dtype=np.float32)
        rgba[..., 0] = scaled
        rgba[..., 1] = scaled
        rgba[..., 2] = scaled
        rgba[..., 3] = 1.0
        return rgba.flatten()

    def _downsample_preview_frame(self, frame):
        if frame is None:
            return None

        height, width = frame.shape[:2]
        max_dimension = max(height, width)
        if max_dimension <= self.zero_preview_max_dimension:
            return np.array(frame, copy=True)

        step = int(np.ceil(max_dimension / float(self.zero_preview_max_dimension)))
        return np.array(frame[::step, ::step], copy=True)

    def get_analysis_snapshot(self, include_history=False):
        with self.Andor.frame_lock:
            latest_frame = self._get_active_frame_locked()
            current_frame_idx = int(self.Andor.frameIdx)
            if self._is_difference_mode_active():
                has_frames = len(self.Andor.difference) > 0
                acquisitions = [np.array(frame, copy=True) for frame in self.Andor.difference] if include_history else None
            elif self._is_contrast_mode_active():
                has_frames = len(self.Andor.contrast) > 0
                acquisitions = [np.array(frame, copy=True) for frame in self.Andor.contrast] if include_history else None
            else:
                if self.lp_filter_enabled:
                    has_frames = len(self.Andor.filtered) > 0
                    acquisitions = [np.array(frame, copy=True) for frame in self.Andor.filtered] if include_history else None
                else:
                    has_frames = len(self.Andor.acquisitions) > 0
                    acquisitions = [np.array(frame, copy=True) for frame in self.Andor.acquisitions] if include_history else None

        return latest_frame, current_frame_idx, has_frames, acquisitions

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

        self._sync_scale_state_to_active_frame()
        self._request_zero_window_refresh()
        self._refresh_display_image()
        self._update_zero_window_texture_binding()
        self._update_settings_controls_state()
        self._request_all_roi_rebuilds()
        self._update_zero_window()

    def _on_difference_colors_changed(self, sender, app_data):
        self.positive_difference_color = self._normalize_color_value(dpg.get_value(self.controls_window.positive_difference_color_id))
        self.negative_difference_color = self._normalize_color_value(dpg.get_value(self.controls_window.negative_difference_color_id))
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

            normalized = ((signed_frame - min_value) / (max_value - min_value)) * 2.0 - 1.0
            normalized = np.clip(normalized, -1.0, 1.0)
            rgba = np.zeros((frame.shape[0], frame.shape[1], 4), dtype=np.float32)
            positive = np.clip(normalized, 0.0, 1.0)
            negative = np.clip(-normalized, 0.0, 1.0)
            rgba[..., :3] += positive[..., None] * (self.positive_difference_color / 255.0)
            rgba[..., :3] += negative[..., None] * (self.negative_difference_color / 255.0)
            rgba[..., 3] = 1.0
            return rgba.flatten()

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
        rgba = np.empty((frame.shape[0], frame.shape[1], 4), dtype=np.float32)
        rgba[..., 0] = scaled
        rgba[..., 1] = scaled
        rgba[..., 2] = scaled
        rgba[..., 3] = 1.0
        return rgba.flatten()

    def _process_frame(self, frame):
        latest_frame, _, _, _ = self.get_analysis_snapshot(include_history=False)
        return self._frame_to_rgba(self._prepare_analysis_frame(latest_frame))

    def get_roi_frame(self, bounds):
        latest_frame, frame_idx, _, _ = self.get_analysis_snapshot(include_history=False)
        crop = self.extract_roi_frame(latest_frame, bounds)

        return crop, frame_idx

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
        width = x2 - x1
        height = y2 - y1
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
        roi.request_trace_rebuild()
        self.rois_window.rebuild_layout(self.rois)
        return roi

    def _close_roi(self, tag):
        roi_to_remove = None
        for roi in self.rois:
            if roi.tag == tag:
                roi_to_remove = roi
                break

        if roi_to_remove is None:
            return

        roi_to_remove.close()
        self.rois.remove(roi_to_remove)
        self.rois_window.rebuild_layout(self.rois)
        if self.selected_roi is roi_to_remove:
            self.selected_roi = None
        if self.hover_target is not None and self.hover_target[0] is roi_to_remove:
            self.hover_target = None
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
                if self.Andor.frame_ready_event.is_set():
                    self.imageArray = self._process_frame(self.Andor.latest_frame)
                    self.image_dirty = True
                    self._request_zero_window_refresh()
                    self.Andor.frame_ready_event.clear()

                if self.image_dirty and hasattr(self, "texture_id") and dpg.does_item_exist(self.texture_id):
                    dpg.set_value(self.texture_id, self.imageArray)
                    self.image_dirty = False

            except Exception as e:
                print("Error updating camera feed:")
                print(e)
                print()

            time.sleep(0.016)

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

    def _state_name(self):
        return f"{type(self).__name__}_{self.tag}"

    def SaveState(self):
        roi_state_names = []
        for roi in self.rois:
            roi.SaveState()
            roi_state_names.append(roi._state_name())

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
                "autoscale_grace_percent": float(self.autoscale_grace_percent),
                "display_mode": self.display_mode,
                "mirrored_difference_scale": bool(self.mirrored_difference_scale),
                "positive_difference_color": self.positive_difference_color.tolist(),
                "negative_difference_color": self.negative_difference_color.tolist(),
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
        self.scale_min = float(state.get("scale_min", self.scale_min))
        self.scale_max = float(state.get("scale_max", self.scale_max))
        self.autoscale_grace_percent = float(state.get("autoscale_grace_percent", self.autoscale_grace_percent))
        self.display_mode = str(state.get("display_mode", self.display_mode))
        self.mirrored_difference_scale = bool(state.get("mirrored_difference_scale", self.mirrored_difference_scale))
        self.positive_difference_color = self._normalize_color_value(state.get("positive_difference_color", self.positive_difference_color.tolist()))
        self.negative_difference_color = self._normalize_color_value(state.get("negative_difference_color", self.negative_difference_color.tolist()))
        self.lp_filter_enabled = bool(state.get("lp_filter_enabled", self.lp_filter_enabled))
        self.lp_filter_cutoff_hz = float(state.get("lp_filter_cutoff_hz", self.lp_filter_cutoff_hz))
        self.zoom = float(state.get("zoom", self.zoom))
        self.view_center_x = float(state.get("view_center_x", self.view_center_x))
        self.view_center_y = float(state.get("view_center_y", self.view_center_y))
        self.roi_index = int(state.get("roi_index", self.roi_index))

        self.Andor.set_lp_filter_cutoff_hz(self.lp_filter_cutoff_hz)
        self.Andor.set_lp_filter_enabled(self.lp_filter_enabled)

        dpg.set_value(self.controls_window.autoscale_checkbox_id, self.autoscale_enabled)
        dpg.set_value(self.controls_window.scale_min_input_id, self.scale_min)
        dpg.set_value(self.controls_window.scale_max_input_id, self.scale_max)
        dpg.set_value(self.controls_window.autoscale_grace_input_id, self.autoscale_grace_percent)
        dpg.set_value(self.controls_window.display_mode_combo_id, self.display_mode)
        dpg.set_value(self.controls_window.mirrored_difference_checkbox_id, self.mirrored_difference_scale)
        dpg.set_value(self.controls_window.positive_difference_color_id, self.positive_difference_color.tolist())
        dpg.set_value(self.controls_window.negative_difference_color_id, self.negative_difference_color.tolist())
        dpg.set_value(self.controls_window.lp_filter_checkbox_id, self.lp_filter_enabled)
        dpg.set_value(self.controls_window.lp_filter_cutoff_input_id, self.lp_filter_cutoff_hz)

        self._clamp_view_center()
        self._update_image_draw_transform()
        self._update_settings_controls_state()
        self._refresh_display_image()
        self._update_zero_window_texture_binding()
        self._update_zero_window()

        for roi in list(self.rois):
            self._close_roi(roi.tag)

        for roi_state_name in state.get("roi_state_names", []):
            roi_state = load_state_file(roi_state_name)
            bounds = roi_state.get("bounds") if isinstance(roi_state, dict) else None
            if not bounds:
                continue
            roi = self._create_roi(bounds)
            if roi is not None:
                roi.LoadState(roi_state_name)

        self._redraw_overlay()


    def render(self):
        dpg.set_item_height(self.window_id, self.width)
        self.zero_reference_window.render()
        self.rois_window.rebuild_layout(self.rois)
        self.rois_window.render()
        self._update_zero_window()

        for roi in list(self.rois):
            roi.render()



