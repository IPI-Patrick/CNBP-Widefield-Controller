import atexit
import os
import time
from collections import deque

from dearpygui import dearpygui as dpg
import importlib.util
from Utils.shared_state import class_objects
from Utils.utils import load_window_classes

WINDOWS_FOLDER = os.path.join(os.path.dirname(__file__), "Windows")
SOFTWARE_FPS_TIMES = deque(maxlen=60)
STATE_AUTOSAVE_INTERVAL_SECONDS = 1.0
LAST_STATE_SAVE_TIME = 0.0


def create_performance_overlay():
    with dpg.window(
        label="Performance Overlay",
        tag="SoftwarePerformanceOverlay",
        width=96,
        height=28,
        no_title_bar=True,
        no_resize=True,
        no_move=True,
        no_scrollbar=True,
        no_collapse=True,
        no_close=True,
        no_background=False,
        no_saved_settings=True,
    ):
        dpg.add_text("0.0 UI FPS", tag="SoftwarePerformanceOverlayText")


def update_performance_overlay():
    SOFTWARE_FPS_TIMES.append(time.perf_counter())
    if len(SOFTWARE_FPS_TIMES) < 2:
        fps = 0.0
    else:
        elapsed = SOFTWARE_FPS_TIMES[-1] - SOFTWARE_FPS_TIMES[0]
        fps = 0.0 if elapsed <= 0.0 else (len(SOFTWARE_FPS_TIMES) - 1) / elapsed

    label = f"{fps:0.1f} UI FPS"
    dpg.set_value("SoftwarePerformanceOverlayText", label)

    viewport_width = dpg.get_viewport_client_width()
    viewport_height = dpg.get_viewport_client_height()
    overlay_width = max(110, int(len(label) * 8.0) + 16)
    overlay_height = 30
    dpg.configure_item("SoftwarePerformanceOverlay", width=overlay_width, height=overlay_height)
    dpg.set_item_pos(
        "SoftwarePerformanceOverlay",
        (
            max(8, viewport_width - overlay_width - 16),
            max(8, viewport_height - overlay_height - 16),
        ),
    )


def save_all_states():
    global LAST_STATE_SAVE_TIME

    for cls in class_objects:
        if hasattr(cls, "SaveState"):
            try:
                cls.SaveState()
            except Exception as exc:
                print(f"Failed to save state for {type(cls).__name__}: {exc}")

    LAST_STATE_SAVE_TIME = time.perf_counter()


def autosave_state_if_needed(force=False):
    global LAST_STATE_SAVE_TIME

    current_time = time.perf_counter()
    if not force and (current_time - LAST_STATE_SAVE_TIME) < STATE_AUTOSAVE_INTERVAL_SECONDS:
        return

    save_all_states()

def setup():
    # Create the window
    global LAST_STATE_SAVE_TIME

    dpg.create_context()
    dpg.create_viewport(
        title='Widefield Controller', 
        width=1600, 
        height=1200, 
        x_pos=0, 
        y_pos=0, 
        always_on_top=True, 
        decorated=True,
        resizable=True, 
        clear_color=[0, 0, 0, 255]
    )

    dpg.setup_dearpygui()
    
    # Create the disabled theme 
    with dpg.theme() as disabled_when_disabled_theme:

        for item in [
            dpg.mvSliderInt,
            dpg.mvSliderFloat,
            dpg.mvButton,
            dpg.mvCheckbox,
            dpg.mvInputText,      
            dpg.mvInputInt,
            dpg.mvInputFloat,      
        ]:
            with dpg.theme_component(item, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Text,            [80, 80, 80])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   [30, 30, 30])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,    [30, 30, 30])
                dpg.add_theme_color(dpg.mvThemeCol_Button,          [30, 30, 30])

        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   [60, 60, 60])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,    [0, 100, 60])


    dpg.bind_theme(disabled_when_disabled_theme)

    # Dynamically load and instantiate window classes
    window_classes = load_window_classes(WINDOWS_FOLDER)
    for cls in window_classes:
        class_objects.append(cls())  # Each class should create its window in __init__
        # print(f"Failed to initialize window {cls.__name__}: {e}")

    for cls in class_objects:
        if hasattr(cls, "LoadState"):
            try:
                cls.LoadState()
            except Exception as exc:
                print(f"Failed to load state for {type(cls).__name__}: {exc}")

    LAST_STATE_SAVE_TIME = time.perf_counter()
    atexit.register(save_all_states)

    create_performance_overlay()

    dpg.show_viewport()

    try:
        # Start the Dear PyGui render loop
        while dpg.is_dearpygui_running():
            dpg.render_dearpygui_frame()
            render_loop(class_objects)
    finally:
        autosave_state_if_needed(force=True)

        # Cleanup after the loop ends
        dpg.destroy_context()

# Render loop for all the program
def render_loop(class_objects):    
    update_performance_overlay()
    autosave_state_if_needed()
    
    for cls in class_objects:
        if hasattr(cls, 'render'):
            cls.render()        



# This script sets up the Widefield Controller GUI using Dear PyGui.
if __name__ == "__main__":
    setup()