import dearpygui.dearpygui as dpg
import numpy as np
import time

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import no_padding_theme


class ImageWindow:

    def __init__(self, *, tag_prefix, label, width, height, pos, show=False, image_size=(1, 1)):
        self.tag_prefix = tag_prefix
        image_height, image_width = image_size
        self.image_height = max(1, int(image_height))
        self.image_width = max(1, int(image_width))
        self.aspect_ratio = self.image_width / max(1, self.image_height)
        self._is_enforcing_resize = False
        self._pending_snap = False
        self._last_resize_time = 0.0
        self._snap_delay_seconds = 0.12

        with dpg.window(
            label=label,
            tag=f"{self.tag_prefix}_Window",
            width=width,
            height=height,
            pos=pos,
            no_resize=False,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
            show=show,
        ):
            self.window_id = dpg.last_item()
            dpg.bind_item_theme(self.window_id, no_padding_theme)

            with dpg.item_handler_registry(tag=f"{self.tag_prefix}_ResizeHandler"):
                dpg.add_item_resize_handler(callback=self._on_window_resize)
                dpg.bind_item_handler_registry(self.window_id, f"{self.tag_prefix}_ResizeHandler")

            with dpg.texture_registry(show=False):
                self.texture_id = dpg.add_dynamic_texture(
                    width=self.image_width,
                    height=self.image_height,
                    default_value=np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32),
                )

            self.image_id = dpg.add_image(
                self.texture_id,
                width=self.image_width,
                height=self.image_height,
            )

    def get_texture_tag(self):
        return self.texture_id

    def get_image_shape(self):
        return self.image_height, self.image_width

    def _get_target_window_size(self, requested_width, requested_height):
        requested_width = max(1, int(round(requested_width)))
        requested_height = max(1, int(round(requested_height)))

        if requested_width >= requested_height:
            return requested_width, max(1, int(round(requested_width / max(self.aspect_ratio, 1e-6))))
        return max(1, int(round(requested_height * self.aspect_ratio))), requested_height

    def show(self):
        dpg.show_item(self.window_id)

    def is_visible(self):
        return dpg.does_item_exist(self.window_id) and dpg.is_item_shown(self.window_id)

    def update_texture_binding(self):
        if dpg.does_item_exist(self.image_id):
            dpg.configure_item(self.image_id, texture_tag=self.get_texture_tag())

    def update_image_size(self):
        if not dpg.does_item_exist(self.window_id) or not dpg.does_item_exist(self.image_id):
            return

        window_width, window_height = dpg.get_item_rect_size(self.window_id)
        available_width = max(1, int(window_width))
        available_height = max(1, int(window_height))

        dpg.set_item_pos(self.image_id, (0, 0))
        dpg.configure_item(self.image_id, width=available_width, height=available_height)

    def ensure_texture_shape(self, height, width):
        height = max(1, int(height))
        width = max(1, int(width))
        if height == self.image_height and width == self.image_width:
            return

        self.image_height = height
        self.image_width = width
        self.aspect_ratio = self.image_width / max(1, self.image_height)
        if dpg.does_item_exist(self.texture_id):
            dpg.delete_item(self.texture_id)

        with dpg.texture_registry(show=False):
            self.texture_id = dpg.add_dynamic_texture(
                width=self.image_width,
                height=self.image_height,
                default_value=np.zeros((self.image_height * self.image_width * 4,), dtype=np.float32),
            )

        dpg.configure_item(self.image_id, texture_tag=self.texture_id)
        self.update_image_size()

    def render(self):
        if not self.is_visible():
            return

        self.update_image_size()

        if not self._pending_snap or self._is_enforcing_resize:
            return

        if (time.perf_counter() - self._last_resize_time) < self._snap_delay_seconds:
            return

        requested_width, requested_height = dpg.get_item_rect_size(self.window_id)
        target_width, target_height = self._get_target_window_size(requested_width, requested_height)

        if int(round(requested_width)) != target_width or int(round(requested_height)) != target_height:
            self._is_enforcing_resize = True
            dpg.configure_item(self.window_id, width=target_width, height=target_height)
            self._is_enforcing_resize = False
            self.update_image_size()

        self._pending_snap = False

    def _on_window_resize(self, sender=None, app_data=None):
        if self._is_enforcing_resize:
            return

        self._last_resize_time = time.perf_counter()
        self._pending_snap = True
        self.update_image_size()

    def _state_name(self):
        return f"{type(self).__name__}_{self.tag_prefix}"

    def SaveState(self):
        save_state_file(self._state_name(), {"window": capture_window_state(self.window_id)})

    def LoadState(self):
        state = load_state_file(self._state_name())
        if not state:
            return

        apply_window_state(self.window_id, state.get("window"))
        self.update_image_size()