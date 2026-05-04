import json
from pathlib import Path

import dearpygui.dearpygui as dpg


STATE_DIRECTORY = Path(__file__).resolve().parents[2] / "AppState"
INIT_FILE_PATH = STATE_DIRECTORY / "dpg_layout.ini"
DEFAULT_VIEWPORT_STATE = {
    "width": 1920,
    "height": 1080,
    "pos": [0, 0],
    "maximized": False,
}
INVALID_VIEWPORT_COORD_THRESHOLD = 30000


def _ensure_state_directory():
    STATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return STATE_DIRECTORY


def get_state_path(state_name):
    safe_name = str(state_name).strip() or "state"
    return _ensure_state_directory() / f"{safe_name}.json"


def get_init_file_path():
    _ensure_state_directory()
    return INIT_FILE_PATH


def capture_window_layout(item_id):
    if item_id is None or not dpg.does_item_exist(item_id):
        return {}

    width, height = dpg.get_item_rect_size(item_id)
    position = dpg.get_item_pos(item_id)
    return {
        "pos": [int(position[0]), int(position[1])],
        "width": int(width),
        "height": int(height),
    }


def apply_window_layout(item_id, layout):
    if not layout or item_id is None or not dpg.does_item_exist(item_id):
        return

    configure_kwargs = {}
    width = layout.get("width")
    height = layout.get("height")
    if width is not None:
        configure_kwargs["width"] = int(width)
    if height is not None:
        configure_kwargs["height"] = int(height)
    if configure_kwargs:
        dpg.configure_item(item_id, **configure_kwargs)

    position = layout.get("pos")
    if isinstance(position, (list, tuple)) and len(position) == 2:
        dpg.set_item_pos(item_id, (int(position[0]), int(position[1])))


def load_state_file(state_name, default=None):
    state_path = get_state_path(state_name)
    if not state_path.exists():
        return {} if default is None else default

    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def save_state_file(state_name, payload):
    state_path = get_state_path(state_name)
    state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def delete_state_file(state_name):
    state_path = get_state_path(state_name)
    try:
        state_path.unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def list_state_files(prefix=None):
    state_directory = _ensure_state_directory()
    file_prefix = str(prefix or "")
    state_names = []
    for state_path in state_directory.glob("*.json"):
        state_name = state_path.stem
        if file_prefix and not state_name.startswith(file_prefix):
            continue
        state_names.append(state_name)
    return sorted(state_names)


def _is_valid_viewport_position(position):
    if not isinstance(position, (list, tuple)) or len(position) != 2:
        return False

    try:
        x_pos = int(position[0])
        y_pos = int(position[1])
    except (TypeError, ValueError):
        return False

    return abs(x_pos) < INVALID_VIEWPORT_COORD_THRESHOLD and abs(y_pos) < INVALID_VIEWPORT_COORD_THRESHOLD


def normalize_viewport_state(state, fallback_state=None):
    normalized = dict(DEFAULT_VIEWPORT_STATE)

    if isinstance(fallback_state, dict):
        fallback_width = fallback_state.get("width")
        fallback_height = fallback_state.get("height")
        fallback_pos = fallback_state.get("pos")
        normalized["maximized"] = bool(fallback_state.get("maximized", normalized["maximized"]))

        if fallback_width is not None:
            normalized["width"] = max(1, int(fallback_width))
        if fallback_height is not None:
            normalized["height"] = max(1, int(fallback_height))
        if _is_valid_viewport_position(fallback_pos):
            normalized["pos"] = [int(fallback_pos[0]), int(fallback_pos[1])]

    if not isinstance(state, dict):
        return dict(normalized)

    width = state.get("width")
    height = state.get("height")
    position = state.get("pos")

    if width is not None:
        normalized["width"] = max(1, int(width))
    if height is not None:
        normalized["height"] = max(1, int(height))
    if _is_valid_viewport_position(position):
        normalized["pos"] = [int(position[0]), int(position[1])]

    normalized["maximized"] = bool(state.get("maximized", normalized["maximized"]))
    return dict(normalized)


def capture_window_state(item_id):
    if item_id is None or not dpg.does_item_exist(item_id):
        return {}

    return {
        "show": bool(dpg.is_item_shown(item_id)),
    }


def apply_window_state(item_id, state):
    if not state or item_id is None or not dpg.does_item_exist(item_id):
        return

    if state.get("show", True):
        dpg.show_item(item_id)
    else:
        dpg.hide_item(item_id)


def capture_item_open_states(item_ids):
    if not isinstance(item_ids, dict):
        return {}

    open_states = {}
    for state_key, item_id in item_ids.items():
        if item_id is None or not dpg.does_item_exist(item_id):
            continue
        open_states[str(state_key)] = bool(dpg.get_value(item_id))
    return open_states


def apply_item_open_states(item_ids, state):
    if not isinstance(item_ids, dict) or not isinstance(state, dict):
        return

    for state_key, is_open in state.items():
        item_id = item_ids.get(state_key)
        if item_id is None or not dpg.does_item_exist(item_id):
            continue
        dpg.set_value(item_id, bool(is_open))


def capture_viewport_state(fallback_state=None):
    state = {}

    try:
        state["width"] = int(dpg.get_viewport_width())
    except Exception:
        pass

    try:
        state["height"] = int(dpg.get_viewport_height())
    except Exception:
        pass

    try:
        position = dpg.get_viewport_pos()
        state["pos"] = [int(position[0]), int(position[1])]
    except Exception:
        pass

    try:
        configuration = dpg.get_viewport_configuration(item=0)
        state["maximized"] = bool(configuration.get("maximized", False))
    except Exception:
        if state:
            state["maximized"] = False

    return normalize_viewport_state(state, fallback_state=fallback_state)


def apply_viewport_state(state, fallback_state=None):
    normalized = normalize_viewport_state(state, fallback_state=fallback_state)

    width = normalized.get("width")
    height = normalized.get("height")
    position = normalized.get("pos")

    if width is not None:
        dpg.set_viewport_width(max(1, int(width)))
    if height is not None:
        dpg.set_viewport_height(max(1, int(height)))
    if isinstance(position, (list, tuple)) and len(position) == 2:
        dpg.set_viewport_pos((int(position[0]), int(position[1])))

    if bool(normalized.get("maximized", False)):
        dpg.maximize_viewport()

    return True
