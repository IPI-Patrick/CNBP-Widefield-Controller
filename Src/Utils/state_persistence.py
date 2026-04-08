import json
from pathlib import Path

import dearpygui.dearpygui as dpg


STATE_DIRECTORY = Path(__file__).resolve().parents[2] / "AppState"


def _ensure_state_directory():
    STATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return STATE_DIRECTORY


def get_state_path(state_name):
    safe_name = str(state_name).strip() or "state"
    return _ensure_state_directory() / f"{safe_name}.json"


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


def capture_window_state(item_id):
    if item_id is None or not dpg.does_item_exist(item_id):
        return {}

    width, height = dpg.get_item_rect_size(item_id)
    position = dpg.get_item_pos(item_id)
    return {
        "pos": [int(position[0]), int(position[1])],
        "width": int(width),
        "height": int(height),
        "show": bool(dpg.is_item_shown(item_id)),
    }


def apply_window_state(item_id, state):
    if not state or item_id is None or not dpg.does_item_exist(item_id):
        return

    configure_kwargs = {}
    width = state.get("width")
    height = state.get("height")
    if width is not None:
        configure_kwargs["width"] = int(width)
    if height is not None:
        configure_kwargs["height"] = int(height)
    if configure_kwargs:
        dpg.configure_item(item_id, **configure_kwargs)

    position = state.get("pos")
    if isinstance(position, (list, tuple)) and len(position) == 2:
        dpg.set_item_pos(item_id, (int(position[0]), int(position[1])))

    if state.get("show", True):
        dpg.show_item(item_id)
    else:
        dpg.hide_item(item_id)
