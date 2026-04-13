import atexit
import os
import time
from collections import deque

from dearpygui import dearpygui as dpg
from Utils.shared_state import class_objects
from Utils.console_capture import install_console_capture
from Utils.state_persistence import apply_viewport_state, apply_window_layout, capture_viewport_state, capture_window_layout, get_init_file_path, load_state_file, normalize_viewport_state, save_state_file
from Utils.utils import load_window_classes

WINDOWS_FOLDER = os.path.join(os.path.dirname(__file__), "Windows")
SOFTWARE_FPS_TIMES = deque(maxlen=60)
STATE_AUTOSAVE_INTERVAL_SECONDS = 1.0
LAST_STATE_SAVE_TIME = 0.0
VIEWPORT_STATE_NAME = "WidefieldController"
VIEWPORT_SAVE_DEBOUNCE_SECONDS = 0.25
VIEWPORT_STATE_DIRTY = False
LAST_VIEWPORT_RESIZE_TIME = 0.0
WINDOW_LAYOUT_DEFAULTS_STATE_NAME = "WindowLayoutDefaults"
RESET_VIEWPORT_STATE = {
    "width": 1920,
    "height": 1080,
    "pos": [0, 0],
    "maximized": False,
}
DEFAULT_WINDOW_LAYOUTS = {}


def _get_window_layout_storage_key(item_id):
    if item_id is None or not dpg.does_item_exist(item_id):
        return None

    alias = dpg.get_item_alias(item_id)
    if isinstance(alias, str) and alias.strip():
        return alias
    return None


def _iter_window_owners(root_objects):
    stack = list(root_objects)
    seen = set()

    while stack:
        current = stack.pop()
        if current is None:
            continue

        object_id = id(current)
        if object_id in seen:
            continue
        seen.add(object_id)

        if hasattr(current, "window_id"):
            yield current

        if not hasattr(current, "__dict__"):
            continue

        for value in vars(current).values():
            if value is None:
                continue

            if isinstance(value, dict):
                stack.extend(v for v in value.values() if hasattr(v, "__dict__") or hasattr(v, "window_id"))
                continue

            if isinstance(value, (list, tuple, set)):
                stack.extend(v for v in value if hasattr(v, "__dict__") or hasattr(v, "window_id"))
                continue

            if hasattr(value, "window_id") or hasattr(value, "SaveState") or hasattr(value, "LoadState"):
                stack.append(value)


def capture_default_window_layouts():
    saved_layouts = load_state_file(WINDOW_LAYOUT_DEFAULTS_STATE_NAME).get("layouts", {})
    DEFAULT_WINDOW_LAYOUTS.clear()
    for owner in _iter_window_owners(class_objects):
        window_id = getattr(owner, "window_id", None)
        layout = capture_window_layout(window_id)
        if not layout:
            continue

        storage_key = _get_window_layout_storage_key(window_id)
        if storage_key and isinstance(saved_layouts, dict) and isinstance(saved_layouts.get(storage_key), dict):
            DEFAULT_WINDOW_LAYOUTS[window_id] = dict(saved_layouts[storage_key])
        else:
            DEFAULT_WINDOW_LAYOUTS[window_id] = layout


def reset_all_window_layouts():
    for window_id, layout in DEFAULT_WINDOW_LAYOUTS.items():
        apply_window_layout(window_id, layout)
    dpg.save_init_file(str(get_init_file_path()))
    print("Default window layout restored.")


def save_current_layout_as_default():
    saved_layouts = {}
    for owner in _iter_window_owners(class_objects):
        window_id = getattr(owner, "window_id", None)
        layout = capture_window_layout(window_id)
        if not layout:
            continue

        DEFAULT_WINDOW_LAYOUTS[window_id] = layout
        storage_key = _get_window_layout_storage_key(window_id)
        if storage_key:
            saved_layouts[storage_key] = layout

    save_state_file(WINDOW_LAYOUT_DEFAULTS_STATE_NAME, {"layouts": saved_layouts})
    dpg.save_init_file(str(get_init_file_path()))
    print("Current window layout saved as default.")


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
        ), # type: ignore
    )


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


def on_reset_window_layout_shortcut(sender=None, app_data=None):
    if not (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
        return

    reset_all_window_layouts()


def on_save_window_layout_default_shortcut(sender=None, app_data=None):
    if not (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
        return

    save_current_layout_as_default()


def flush_pending_viewport_state_save():
    global VIEWPORT_STATE_DIRTY

    if not VIEWPORT_STATE_DIRTY:
        return

    if (time.perf_counter() - LAST_VIEWPORT_RESIZE_TIME) < VIEWPORT_SAVE_DEBOUNCE_SECONDS:
        return

    save_viewport_state()
    VIEWPORT_STATE_DIRTY = False

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

    capture_default_window_layouts()

    for cls in class_objects:
        if hasattr(cls, "LoadState"):
            try:
                cls.LoadState()
            except Exception as exc:
                print(f"Failed to load state for {type(cls).__name__}: {exc}")

    LAST_STATE_SAVE_TIME = time.perf_counter()
    atexit.register(save_all_states)

    create_performance_overlay()

    with dpg.handler_registry(tag="WidefieldControllerKeyHandlers"):
        dpg.add_key_press_handler(key=dpg.mvKey_R, callback=on_reset_viewport_shortcut)
        dpg.add_key_press_handler(key=dpg.mvKey_B, callback=on_reset_window_layout_shortcut)
        dpg.add_key_press_handler(key=dpg.mvKey_N, callback=on_save_window_layout_default_shortcut)

    dpg.show_viewport()
    apply_viewport_state(viewport_state)
    dpg.set_viewport_resize_callback(on_viewport_resized)

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
def render_loop(window_objects):
    update_performance_overlay()
    flush_pending_viewport_state_save()
    autosave_state_if_needed()
    
    for cls in window_objects:
        if hasattr(cls, 'render'):
            cls.render()        



# This script sets up the Widefield Controller GUI using Dear PyGui.
if __name__ == "__main__":
    setup()