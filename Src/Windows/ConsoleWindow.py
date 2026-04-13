import dearpygui.dearpygui as dpg

from Utils.console_capture import get_console_capture
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file


class ConsoleWindow:
    def __init__(self):
        self.state_name = "ConsoleWindow"
        self.console_capture = get_console_capture()
        self._last_rendered_version = -1

        with dpg.window(
            label="Console",
            tag="#ConsoleWindow",
            width=720,
            height=320,
            pos=(20, 720),
            no_scrollbar=False,
            no_resize=False,
            no_scroll_with_mouse=False,
        ):
            self.window_id = dpg.last_item()
            self.console_text_id = dpg.add_input_text(
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
                tab_input=False,
                default_value="",
            )

    def render(self):
        if not dpg.does_item_exist(self.window_id):
            return

        version, lines = self.console_capture.get_snapshot()
        if version == self._last_rendered_version:
            return

        dpg.set_value(self.console_text_id, "\n".join(lines))
        self._last_rendered_version = version

    def SaveState(self):
        save_state_file(self.state_name, capture_window_state(self.window_id))

    def LoadState(self):
        apply_window_state(self.window_id, load_state_file(self.state_name))