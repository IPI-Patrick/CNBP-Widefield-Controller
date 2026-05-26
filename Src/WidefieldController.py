import atexit
import os
import sys
import time
from collections import deque

from dearpygui import dearpygui as dpg
from Utils.shared_state import class_objects
import Utils.shared_state as shared_state
from Utils.console_capture import install_console_capture
from Utils.state_persistence import apply_viewport_state, capture_viewport_state, get_init_file_path, load_state_file, normalize_viewport_state, save_state_file
from Utils.utils import load_window_classes

WINDOWS_FOLDER = os.path.join(os.path.dirname(__file__), "Windows")
SOFTWARE_FPS_TIMES = deque(maxlen=60)
STATE_AUTOSAVE_INTERVAL_SECONDS = 1.0
LAST_STATE_SAVE_TIME = 0.0
VIEWPORT_STATE_NAME = "WidefieldController"
VIEWPORT_SAVE_DEBOUNCE_SECONDS = 0.25
VIEWPORT_STATE_DIRTY = False
LAST_VIEWPORT_RESIZE_TIME = 0.0
RESET_VIEWPORT_STATE = {
    "width": 1920,
    "height": 1080,
    "pos": [0, 0],
    "maximized": False,
}
_CLEANUP_RAN = False



def save_viewport_state():
    saved_state = load_state_file(VIEWPORT_STATE_NAME).get("viewport")
    viewport_state = capture_viewport_state(fallback_state=saved_state)
    if viewport_state:
        save_state_file(VIEWPORT_STATE_NAME, {"viewport": viewport_state})


def reset_viewport_state():
    global VIEWPORT_STATE_DIRTY, LAST_VIEWPORT_RESIZE_TIME

    apply_viewport_state(RESET_VIEWPORT_STATE)
    save_state_file(VIEWPORT_STATE_NAME, {"viewport": dict(RESET_VIEWPORT_STATE)})
    VIEWPORT_STATE_DIRTY = False
    LAST_VIEWPORT_RESIZE_TIME = time.perf_counter()


def request_viewport_state_save():
    global VIEWPORT_STATE_DIRTY, LAST_VIEWPORT_RESIZE_TIME

    VIEWPORT_STATE_DIRTY = True
    LAST_VIEWPORT_RESIZE_TIME = time.perf_counter()


def update_performance_overlay():
    SOFTWARE_FPS_TIMES.append(time.perf_counter())
    if len(SOFTWARE_FPS_TIMES) < 2:
        ui_fps = 0.0
    else:
        elapsed = SOFTWARE_FPS_TIMES[-1] - SOFTWARE_FPS_TIMES[0]
        ui_fps = 0.0 if elapsed <= 0.0 else (len(SOFTWARE_FPS_TIMES) - 1) / elapsed

    capture_fps = 0.0
    shared_andor = getattr(shared_state, "shared_andor", None)
    if shared_andor is not None and hasattr(shared_andor, "get_capture_loop_fps"):
        try:
            capture_fps = float(shared_andor.get_capture_loop_fps())
        except Exception:
            capture_fps = 0.0

    label = f"{int(ui_fps)} UI FPS | {int(capture_fps)} Capture FPS"
    if dpg.does_item_exist("SoftwarePerformanceOverlayText"):
        dpg.set_value("SoftwarePerformanceOverlayText", label)
        label_w = max(200, int(len(label) * 7))
        toolbar_w = dpg.get_viewport_client_width()
        x = max(8, toolbar_w - label_w - 12)
        dpg.set_item_pos("SoftwarePerformanceOverlayText", (x, 20))


def save_all_states():
    global LAST_STATE_SAVE_TIME

    save_viewport_state()
    dpg.save_init_file(str(get_init_file_path()))

    for cls in class_objects:
        if hasattr(cls, "SaveState"):
            try:
                cls.SaveState()
            except Exception as exc:
                print(f"Failed to save state for {type(cls).__name__}: {exc}")

    LAST_STATE_SAVE_TIME = time.perf_counter()


def cleanup_all_windows():
    global _CLEANUP_RAN

    if _CLEANUP_RAN:
        return

    _CLEANUP_RAN = True
    for cls in reversed(class_objects):
        if hasattr(cls, "cleanup"):
            try:
                cls.cleanup()
            except Exception as exc:
                print(f"Failed to cleanup {type(cls).__name__}: {exc}")


def autosave_state_if_needed(force=False):
    current_time = time.perf_counter()
    if not force and (current_time - LAST_STATE_SAVE_TIME) < STATE_AUTOSAVE_INTERVAL_SECONDS:
        return

    save_all_states()


def on_viewport_resized(sender=None, app_data=None):
    request_viewport_state_save()


def on_reset_viewport_shortcut(sender=None, app_data=None):
    if not (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
        return

    reset_viewport_state()


def flush_pending_viewport_state_save():
    global VIEWPORT_STATE_DIRTY

    if not VIEWPORT_STATE_DIRTY:
        return

    if (time.perf_counter() - LAST_VIEWPORT_RESIZE_TIME) < VIEWPORT_SAVE_DEBOUNCE_SECONDS:
        return

    save_viewport_state()
    VIEWPORT_STATE_DIRTY = False

def _register_viewport_drop_callback(window_objects):
    def _on_viewport_drop(data, keys):
        paths = data if isinstance(data, list) else [data]
        file_browser = next(
            (obj for obj in window_objects if type(obj).__name__ == "FileBrowser"),
            None,
        )
        if file_browser is None:
            return
        for path in paths:
            if isinstance(path, str) and path.lower().endswith(".npz"):
                file_browser._open_preview_window(None, None, user_data=path)
                break



def setup():
    # Create the window
    global LAST_STATE_SAVE_TIME
    install_console_capture(max_lines=100)
    loaded_viewport_state = load_state_file(VIEWPORT_STATE_NAME).get("viewport")
    viewport_state = normalize_viewport_state(loaded_viewport_state)
    if loaded_viewport_state != viewport_state:
        save_state_file(VIEWPORT_STATE_NAME, {"viewport": viewport_state})

    dpg.create_context()
    dpg.configure_app(init_file=str(get_init_file_path()))
    dpg.create_viewport(
        title='Widefield Controller', 
        width=1920,
        height=1080,
        x_pos=0,
        y_pos=0,
        # width=int(viewport_state.get("width", 1920)) if viewport_state else 1920,
        # height=int(viewport_state.get("height", 1080)) if viewport_state else 1080,
        # x_pos=int(viewport_state.get("pos", [0, 0])[0]) if viewport_state else 0,
        # y_pos=int(viewport_state.get("pos", [0, 0])[1]) if viewport_state else 0,
        always_on_top=False, 
        decorated=True,
        resizable=True, 
        clear_color=[0, 0, 0, 255]
    )

    
    dpg.setup_dearpygui()
    apply_viewport_state(viewport_state)
    
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
    for cls in window_classes: # type: ignore
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
    atexit.register(cleanup_all_windows)

    with dpg.handler_registry(tag="WidefieldControllerKeyHandlers"):
        dpg.add_key_press_handler(key=dpg.mvKey_R, callback=on_reset_viewport_shortcut)


    dpg.show_viewport()
    apply_viewport_state(viewport_state)
    dpg.set_viewport_resize_callback(on_viewport_resized)
    _register_viewport_drop_callback(class_objects)

    try:
        # Start the Dear PyGui render loop
        while dpg.is_dearpygui_running():
            dpg.render_dearpygui_frame()
            render_loop(class_objects)
    finally:
        autosave_state_if_needed(force=True)
        cleanup_all_windows()

        # Cleanup after the loop ends
        dpg.destroy_context()

# Render loop for all the program
def render_loop(window_objects):
    update_performance_overlay()
    flush_pending_viewport_state_save()
    autosave_state_if_needed()
    
    for cls in window_objects:
        if hasattr(cls, 'render'):
            cls.render()        



# This script sets up the Widefield Controller GUI using Dear PyGui.
if __name__ == "__main__":
    dev_mode = "-dev" in sys.argv
    
    # Check for dev=True in .env file
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith("dev=True"):
                    dev_mode = True
                    break
    
    if dev_mode:
        import Utils.shared_state as _shared_state
        _shared_state.dev_mode = True
        print("Running in development mode — hardware drivers will use mocks where available.")
    setup()