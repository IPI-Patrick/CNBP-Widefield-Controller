import math

import dearpygui.dearpygui as dpg
import numpy as np

from Utils.themes import no_padding_theme, read_only_theme


class CalibrationModal:

    def __init__(self, *, tag_prefix, on_accept):
        self.tag_prefix = str(tag_prefix)
        self.on_accept = on_accept

        self.image_width = 1
        self.image_height = 1
        self.zoom = 1.0
        self.min_zoom = 1.0
        self.max_zoom = 16.0
        self.view_center_x = 0.5
        self.view_center_y = 0.5
        self.points = []
        self.hovered_point_index = None
        self.interaction = None
        self.point_hit_radius = 10.0
        self.point_draw_radius = 6.0
        self.preview_aspect_ratio = 1.0
        self._layout_dirty = True
        self._objectives = []
        self._current_objective_name = ""
        self.add_new_checkbox_id = None
        self.new_name_input_id = None
        self.existing_combo_id = None

        with dpg.window(
            label="Calibration",
            tag=f"{self.tag_prefix}_Window",
            width=1080,
            height=720,
            pos=(120, 80),
            modal=True,
            show=False,
            no_resize=False,
            no_collapse=True,
            no_close=True,
        ):
            self.window_id = dpg.last_item()
            with dpg.item_handler_registry(tag=f"{self.tag_prefix}_ResizeHandler"):
                dpg.add_item_resize_handler(callback=self._on_window_resize)
                dpg.bind_item_handler_registry(self.window_id, f"{self.tag_prefix}_ResizeHandler")

            with dpg.group(horizontal=True):
                with dpg.child_window(
                    border=True,
                    width=760,
                    height=620,
                    no_scrollbar=True,
                    no_scroll_with_mouse=True,
                ) as self.canvas_container_id:
                    dpg.bind_item_theme(self.canvas_container_id, no_padding_theme)
                    with dpg.drawlist(width=760, height=620, tag=f"{self.tag_prefix}_Canvas"):
                        self.canvas_id = dpg.last_item()
                        with dpg.draw_layer(tag=f"{self.tag_prefix}_ImageLayer"):
                            self.image_layer = dpg.last_item()
                        with dpg.draw_layer(tag=f"{self.tag_prefix}_OverlayLayer"):
                            self.overlay_layer = dpg.last_item()

                with dpg.child_window(border=False, width=-1, height=620) as self.settings_container_id:
                    dpg.add_text("Click twice to place the calibration points.")
                    dpg.add_text("Drag points to refine them.")
                    dpg.add_spacer(height=6)
                    dpg.add_text("Mouse wheel zooms. Middle-drag or Ctrl+left-drag pans.")
                    dpg.add_separator()

                    dpg.add_text("Objective", color=[180, 180, 180])
                    self.add_new_checkbox_id = dpg.add_checkbox(
                        label="Add new objective",
                        default_value=False,
                        callback=self._on_add_new_changed,
                    )
                    self.new_name_input_id = dpg.add_input_text(
                        label="Name",
                        width=-1,
                        hint="e.g. 40x, 100x oil",
                        default_value="",
                        show=False,
                    )
                    self.existing_combo_id = dpg.add_combo(
                        label="Update",
                        items=[],
                        default_value="",
                        width=-1,
                        show=False,
                    )
                    dpg.add_separator()

                    self.real_distance_input_id = dpg.add_input_float(
                        label="Real Distance Between Points",
                        width=-1,
                        default_value=100.0,
                        min_value=1e-3,
                        min_clamped=True,
                        step=1.0,
                        format="%.3f um",
                        on_enter=True,
                        callback=self._on_real_distance_changed,
                    )
                    with dpg.item_handler_registry(tag=f"{self.tag_prefix}_DistanceHandler"):
                        dpg.add_item_deactivated_after_edit_handler(callback=self._on_real_distance_changed)
                    dpg.bind_item_handler_registry(self.real_distance_input_id, f"{self.tag_prefix}_DistanceHandler")

                    self.pixel_distance_input_id = dpg.add_input_text(
                        label="Pixel Distance",
                        width=-1,
                        default_value="",
                        readonly=True,
                    )
                    dpg.bind_item_theme(self.pixel_distance_input_id, read_only_theme)

                    self.mm_per_pixel_input_id = dpg.add_input_text(
                        label="um / px",
                        width=-1,
                        default_value="",
                        readonly=True,
                    )
                    dpg.bind_item_theme(self.mm_per_pixel_input_id, read_only_theme)

            dpg.add_separator()
            with dpg.group(horizontal=True):
                self.ok_button_id = dpg.add_button(label="Okay", width=120, callback=self._on_ok_pressed, enabled=False)
                dpg.add_button(label="Cancel", width=120, callback=self.close)

        with dpg.texture_registry(show=False):
            self.texture_id = dpg.add_dynamic_texture(
                width=self.image_width,
                height=self.image_height,
                default_value=np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32),
            )

        self.image_draw_id = dpg.draw_image(
            self.texture_id,
            pmin=(0, 0),
            pmax=(760, 620),
            parent=self.image_layer,
        )

        with dpg.handler_registry(tag=f"{self.tag_prefix}_MouseHandler"):
            dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Left, callback=self._on_left_mouse_down)
            dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Middle, callback=self._on_middle_mouse_down)
            dpg.add_mouse_move_handler(callback=self._on_mouse_move)
            dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_release)
            dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_release)
            dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)

    def is_visible(self):
        return dpg.does_item_exist(self.window_id) and dpg.is_item_shown(self.window_id)

    def open(self, *, rgba, image_width, image_height, preview_aspect_ratio=None,
             objectives=None, current_objective_name=""):
        self._ensure_texture_shape(image_height, image_width)
        self.preview_aspect_ratio = (
            float(preview_aspect_ratio)
            if preview_aspect_ratio is not None and float(preview_aspect_ratio) > 0.0
            else (float(self.image_width) / max(1.0, float(self.image_height)))
        )
        dpg.set_value(self.texture_id, np.asarray(rgba, dtype=np.float32))
        self.points = []
        self.hovered_point_index = None
        self.interaction = None
        self._objectives = list(objectives or [])
        self._current_objective_name = str(current_objective_name or "")
        self._reset_zoom(redraw=False)
        self._layout_dirty = True

        # Reset objective section
        dpg.set_value(self.add_new_checkbox_id, False)
        dpg.set_value(self.new_name_input_id, "")
        dpg.configure_item(self.existing_combo_id, items=self._objectives)
        dpg.set_value(self.existing_combo_id, self._current_objective_name if self._current_objective_name in self._objectives else (self._objectives[0] if self._objectives else ""))
        dpg.hide_item(self.new_name_input_id)
        dpg.show_item(self.existing_combo_id) if self._objectives else dpg.hide_item(self.existing_combo_id)

        self._update_measurement_readout()
        self._redraw_overlay()
        dpg.show_item(self.window_id)
        dpg.focus_item(self.window_id)

    def close(self, sender=None, app_data=None, user_data=None):
        self.points = []
        self.hovered_point_index = None
        self.interaction = None
        self._layout_dirty = True
        self._update_measurement_readout()
        self._redraw_overlay()
        dpg.hide_item(self.window_id)

    def render(self):
        if not self.is_visible():
            return
        if self._layout_dirty:
            self._update_layout()
            self._layout_dirty = False
        self._update_ok_button_state()

    def _ensure_texture_shape(self, image_height, image_width):
        image_height = max(1, int(image_height))
        image_width = max(1, int(image_width))
        if image_height == self.image_height and image_width == self.image_width:
            return

        self.image_height = image_height
        self.image_width = image_width
        if dpg.does_item_exist(self.texture_id):
            dpg.delete_item(self.texture_id)

        with dpg.texture_registry(show=False):
            self.texture_id = dpg.add_dynamic_texture(
                width=self.image_width,
                height=self.image_height,
                default_value=np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32),
            )

        dpg.configure_item(self.image_draw_id, texture_tag=self.texture_id)

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
        return left, top, left + view_width, top + view_height

    def _update_image_draw_transform(self):
        left, top, right, bottom = self._get_view_bounds()
        canvas_width, canvas_height = self._get_canvas_size()
        dpg.configure_item(
            self.image_draw_id,
            pmin=(0, 0),
            pmax=(canvas_width, canvas_height),
            uv_min=(left / max(1.0, float(self.image_width)), top / max(1.0, float(self.image_height))),
            uv_max=(right / max(1.0, float(self.image_width)), bottom / max(1.0, float(self.image_height))),
        )

    def _reset_zoom(self, redraw=True):
        self.zoom = 1.0
        self.view_center_x = self.image_width / 2.0
        self.view_center_y = self.image_height / 2.0
        self._clamp_view_center()
        self._update_image_draw_transform()
        if redraw:
            self._redraw_overlay()

    def _point_in_canvas(self, local_point):
        local_x, local_y = local_point
        canvas_width, canvas_height = self._get_canvas_size()
        return 0 <= local_x <= canvas_width and 0 <= local_y <= canvas_height

    def _is_canvas_hovered_raw(self):
        return dpg.is_item_hovered(self.canvas_id)

    def _is_canvas_hovered(self):
        return self._is_canvas_hovered_raw() and self._point_in_canvas(self._get_mouse_local())

    def _get_mouse_local(self):
        if self._is_canvas_hovered_raw():
            draw_x, draw_y = dpg.get_drawing_mouse_pos()
            return float(draw_x), float(draw_y)

        mouse_x, mouse_y = dpg.get_mouse_pos()
        rect_min_x, rect_min_y = dpg.get_item_rect_min(self.canvas_id)
        return mouse_x - rect_min_x, mouse_y - rect_min_y

    def _display_to_image(self, local_point):
        local_x, local_y = local_point
        canvas_width, canvas_height = self._get_canvas_size()
        left, top, right, bottom = self._get_view_bounds()
        image_x = left + ((local_x / max(1.0, canvas_width)) * (right - left))
        image_y = top + ((local_y / max(1.0, canvas_height)) * (bottom - top))
        image_x = float(np.clip(image_x, 0.0, float(self.image_width - 1)))
        image_y = float(np.clip(image_y, 0.0, float(self.image_height - 1)))
        return image_x, image_y

    def _image_to_display(self, image_point):
        image_x, image_y = image_point
        canvas_width, canvas_height = self._get_canvas_size()
        left, top, right, bottom = self._get_view_bounds()
        display_x = ((image_x - left) / max(1e-6, (right - left))) * canvas_width
        display_y = ((image_y - top) / max(1e-6, (bottom - top))) * canvas_height
        return float(display_x), float(display_y)

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

    def _get_pixel_distance(self):
        if len(self.points) != 2:
            return None
        return float(math.dist(self.points[0], self.points[1]))

    def _get_mm_per_pixel(self):
        pixel_distance = self._get_pixel_distance()
        if pixel_distance is None or pixel_distance <= 0.0:
            return None
        real_distance_um = max(0.0, float(dpg.get_value(self.real_distance_input_id)))
        if real_distance_um <= 0.0:
            return None
        return (real_distance_um / 1000.0) / pixel_distance

    def _format_float(self, value):
        if value is None:
            return ""
        abs_value = abs(float(value))
        if abs_value >= 1000.0:
            return f"{value:.1f}"
        if abs_value >= 10.0:
            return f"{value:.3f}"
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _update_measurement_readout(self):
        pixel_distance = self._get_pixel_distance()
        mm_per_pixel = self._get_mm_per_pixel()
        um_per_pixel = None if mm_per_pixel is None else mm_per_pixel * 1000.0
        dpg.set_value(self.pixel_distance_input_id, "" if pixel_distance is None else f"{self._format_float(pixel_distance)} px")
        dpg.set_value(self.mm_per_pixel_input_id, "" if um_per_pixel is None else f"{self._format_float(um_per_pixel)} um/px")
        self._update_ok_button_state()

    def _update_ok_button_state(self):
        pixel_distance = self._get_pixel_distance()
        real_distance_um = max(0.0, float(dpg.get_value(self.real_distance_input_id)))
        is_valid = pixel_distance is not None and pixel_distance > 0.0 and real_distance_um > 0.0
        dpg.configure_item(self.ok_button_id, enabled=is_valid)

    def _hit_test_point(self, local_point):
        local_x, local_y = local_point
        best_index = None
        best_distance = None
        for index, point in enumerate(self.points):
            point_x, point_y = self._image_to_display(point)
            distance = math.hypot(local_x - point_x, local_y - point_y)
            if distance > self.point_hit_radius:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = index
        return best_index

    def _is_pan_modifier_active(self):
        return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)

    def _redraw_overlay(self):
        if not dpg.does_item_exist(self.overlay_layer):
            return

        dpg.delete_item(self.overlay_layer, children_only=True)

        if len(self.points) == 2:
            point_1 = self._image_to_display(self.points[0])
            point_2 = self._image_to_display(self.points[1])
            dpg.draw_line(point_1, point_2, color=(90, 205, 255, 255), thickness=2, parent=self.overlay_layer)

        for index, point in enumerate(self.points):
            display_x, display_y = self._image_to_display(point)
            is_hovered = index == self.hovered_point_index
            is_dragged = self.interaction is not None and self.interaction.get("index") == index
            fill_color = (255, 225, 90, 255)
            if is_hovered or is_dragged:
                fill_color = (255, 120, 80, 255)
            dpg.draw_circle(
                (display_x, display_y),
                self.point_draw_radius,
                color=(20, 20, 20, 255),
                fill=fill_color,
                thickness=2,
                parent=self.overlay_layer,
            )
            dpg.draw_text(
                (display_x + 10, display_y - 10),
                str(index + 1),
                color=(255, 255, 255, 255),
                size=16,
                parent=self.overlay_layer,
            )

    def _on_window_resize(self, sender=None, app_data=None):
        self._layout_dirty = True

    def _update_layout(self):
        if not dpg.does_item_exist(self.window_id):
            return

        window_width, window_height = dpg.get_item_rect_size(self.window_id)
        if window_width <= 0 or window_height <= 0:
            return

        horizontal_padding = 28.0
        vertical_padding = 108.0
        spacing = 12.0
        available_width = max(420.0, float(window_width) - horizontal_padding)
        available_height = max(220.0, float(window_height) - vertical_padding)

        settings_width = min(320.0, max(240.0, available_width * 0.30))
        image_available_width = max(140.0, available_width - settings_width - spacing)
        image_available_height = max(140.0, available_height)

        aspect_ratio = max(1e-6, float(self.preview_aspect_ratio))
        canvas_width = image_available_width
        canvas_height = canvas_width / aspect_ratio
        if canvas_height > image_available_height:
            canvas_height = image_available_height
            canvas_width = canvas_height * aspect_ratio

        settings_width = max(180.0, available_width - canvas_width - spacing)

        dpg.configure_item(self.canvas_container_id, width=int(round(canvas_width)), height=int(round(canvas_height)))
        dpg.configure_item(self.canvas_id, width=int(round(canvas_width)), height=int(round(canvas_height)))
        dpg.configure_item(self.settings_container_id, width=int(round(settings_width)), height=int(round(available_height)))
        self._update_image_draw_transform()
        self._redraw_overlay()

    def _on_real_distance_changed(self, sender=None, app_data=None, user_data=None):
        real_distance_um = max(1e-3, float(dpg.get_value(self.real_distance_input_id)))
        dpg.set_value(self.real_distance_input_id, real_distance_um)
        self._update_measurement_readout()

    def _on_left_mouse_down(self, sender, app_data):
        self._on_mouse_down(dpg.mvMouseButton_Left)

    def _on_middle_mouse_down(self, sender, app_data):
        self._on_mouse_down(dpg.mvMouseButton_Middle)

    def _on_mouse_down(self, mouse_button):
        if not self.is_visible() or self.interaction is not None or not self._is_canvas_hovered():
            return

        local_point = self._get_mouse_local()
        if mouse_button == dpg.mvMouseButton_Middle or (mouse_button == dpg.mvMouseButton_Left and self._is_pan_modifier_active()):
            if self.zoom > self.min_zoom:
                self.interaction = {
                    "mode": "pan",
                    "start_local": local_point,
                    "anchor_center": (self.view_center_x, self.view_center_y),
                }
            return

        point_index = self._hit_test_point(local_point)
        if point_index is not None:
            self.interaction = {
                "mode": "move-point",
                "index": point_index,
            }
            self.hovered_point_index = point_index
            self._redraw_overlay()
            return

        if len(self.points) >= 2:
            return

        image_point = self._display_to_image(local_point)
        self.points.append([image_point[0], image_point[1]])
        self.hovered_point_index = len(self.points) - 1
        self._update_measurement_readout()
        self._redraw_overlay()

    def _on_mouse_move(self, sender, app_data):
        if not self.is_visible():
            return

        local_point = self._get_mouse_local()
        if self.interaction is None:
            previous_hover = self.hovered_point_index
            self.hovered_point_index = self._hit_test_point(local_point) if self._point_in_canvas(local_point) else None
            if self.hovered_point_index != previous_hover:
                self._redraw_overlay()
            return

        if self.interaction["mode"] == "pan":
            start_local_x, start_local_y = self.interaction["start_local"]
            delta_local_x = local_point[0] - start_local_x
            delta_local_y = local_point[1] - start_local_y
            self._pan_view(delta_local_x, delta_local_y, self.interaction["anchor_center"])
        elif self.interaction["mode"] == "move-point":
            image_x, image_y = self._display_to_image(local_point)
            self.points[self.interaction["index"]] = [image_x, image_y]
            self._update_measurement_readout()

        self._redraw_overlay()

    def _on_mouse_release(self, sender, app_data):
        if not self.is_visible():
            return
        self.interaction = None
        self._redraw_overlay()

    def _on_mouse_wheel(self, sender, app_data):
        if not self.is_visible() or not self._is_canvas_hovered():
            return
        self._set_zoom_at_point(app_data, self._get_mouse_local())

    def _on_add_new_changed(self, sender=None, app_data=None, user_data=None):
        is_new = bool(dpg.get_value(self.add_new_checkbox_id))
        dpg.show_item(self.new_name_input_id) if is_new else dpg.hide_item(self.new_name_input_id)
        if self._objectives:
            dpg.hide_item(self.existing_combo_id) if is_new else dpg.show_item(self.existing_combo_id)

    def _on_ok_pressed(self, sender=None, app_data=None, user_data=None):
        mm_per_pixel = self._get_mm_per_pixel()
        if mm_per_pixel is None:
            return

        is_new = bool(dpg.get_value(self.add_new_checkbox_id))
        if is_new:
            objective_name = str(dpg.get_value(self.new_name_input_id) or "").strip()
        else:
            objective_name = str(dpg.get_value(self.existing_combo_id) or "").strip()
            if not objective_name:
                objective_name = self._current_objective_name

        payload = {
            "mm_per_pixel": float(mm_per_pixel),
            "image_width_mm": float(self.image_width) * float(mm_per_pixel),
            "image_height_mm": float(self.image_height) * float(mm_per_pixel),
            "pixel_distance": float(self._get_pixel_distance()),
            "real_distance_um": float(dpg.get_value(self.real_distance_input_id)),
            "points": [tuple(point) for point in self.points],
            "image_width_px": int(self.image_width),
            "image_height_px": int(self.image_height),
            "is_new_objective": is_new,
            "objective_name": objective_name,
        }
        self.close()
        if callable(self.on_accept):
            self.on_accept(payload)
