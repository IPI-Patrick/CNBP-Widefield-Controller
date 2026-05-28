import dearpygui.dearpygui as dpg

from Utils.console_capture import get_console_capture
import Utils.shared_state as shared_state
from Utils.state_persistence import load_state_file, save_state_file


class ConsoleWindow:
    def __init__(self):
        self.state_name = "ConsoleWindow"
        self.console_capture = get_console_capture()
        self._last_rendered_version = -1

        _console_tab = shared_state.layout_containers.get("console_tab")
        if _console_tab:
            self.window_id = _console_tab
        else:
            self.window_id = dpg.add_window(
                label="Console",
                tag="#ConsoleWindow",
                width=720,
                height=320,
                pos=(20, 720),
                no_scrollbar=False,
                no_resize=False,
                no_scroll_with_mouse=False,
            )
        dpg.push_container_stack(self.window_id)
        # Scroll-area child_window so we can programmatically scroll to bottom.
        with dpg.child_window(height=-24, border=False, no_scrollbar=False) as self._scroll_area:
            self.console_text_id = dpg.add_input_text(
                multiline=True,
                readonly=True,
                width=-1,
                height=100,  # grown dynamically in render()
                tab_input=False,
                default_value="",
            )
        dpg.add_spacer(height=8)
        dpg.pop_container_stack()

    def render(self):
        if not dpg.does_item_exist(self.window_id):
            return

        version, lines = self.console_capture.get_snapshot()
        if version == self._last_rendered_version:
            return

        text = "\n".join(lines)
        dpg.set_value(self.console_text_id, text)
        self._last_rendered_version = version
        # Resize input_text to match content so the scroll_area can scroll to bottom
        line_count = max(1, text.count("\n") + 1)
        dpg.configure_item(self.console_text_id, height=max(100, line_count * 15))
        if dpg.does_item_exist(self._scroll_area):
            dpg.set_y_scroll(self._scroll_area, dpg.get_y_scroll_max(self._scroll_area))

    def SaveState(self):
        pass

    def LoadState(self):
        pass