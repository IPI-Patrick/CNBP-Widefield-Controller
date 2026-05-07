import threading
import time

import numpy as np
import dearpygui.dearpygui as dpg

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file

class GraphWindow:

    def __init__(self, name, item_id=None, getYValues=None, getXValues=None, xlabel="", ylabel="", xpos=0, ypos=0, **kwargs):
        if item_id is None:
            item_id = kwargs.pop("id")

        with dpg.window(
            label=name,
            tag=f"#{item_id}",
            width=600,
            height=250,
            pos=(xpos, ypos),
            no_scrollbar=True,
            no_resize=False,
            no_scroll_with_mouse=True,
        ):
            self.lock = threading.Lock()
            self._stop_event = threading.Event()
            self.window_id = dpg.last_item()
            self.getYAxis = getYValues
            self.getXAxis = getXValues

            self.plot_id = dpg.add_plot(label=name, width=-1, height=-1, parent=self.window_id)
            self.x_axis = dpg.add_plot_axis(dpg.mvXAxis, label=xlabel, parent=self.plot_id, auto_fit=True)
            self.y_axis = dpg.add_plot_axis(dpg.mvYAxis, label=ylabel, parent=self.plot_id, auto_fit=True)
            self.line_series = dpg.add_line_series(
                x=np.array(self.getXAxis()).astype(float).tolist(),
                y=np.array(self.getYAxis()).astype(float).tolist(),
                parent=self.y_axis,
            )
            threading.Thread(target=self._process_graph, daemon=True).start()

    def _process_graph(self):
        while not self._stop_event.is_set():
            new_y = np.array(self.getYAxis()).astype(float).tolist()
            new_x = np.array(self.getXAxis()).astype(float).tolist()
            dpg.set_value(self.line_series, [new_x, new_y])
            self._stop_event.wait(0.016)

    def stop(self):
        self._stop_event.set()

    def _state_name(self):
        return f"{type(self).__name__}_{self.window_id}"

    def SaveState(self):
        save_state_file(self._state_name(), {"window": capture_window_state(self.window_id)})

    def LoadState(self):
        state = load_state_file(self._state_name())
        if state:
            apply_window_state(self.window_id, state.get("window"))
