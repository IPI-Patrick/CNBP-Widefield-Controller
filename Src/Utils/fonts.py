import os

import dearpygui.dearpygui as dpg


GLOBAL_FONT_REGISTRY_TAG = "__global_font_registry"
SEGMDL2_ICON_FONT_12_TAG = "__segmdl2_icon_font_12"
SEGMDL2_ICON_FONT_18_TAG = "__segmdl2_icon_font_18"


def _ensure_global_font_registry():
    if dpg.does_item_exist(GLOBAL_FONT_REGISTRY_TAG):
        return GLOBAL_FONT_REGISTRY_TAG

    with dpg.font_registry(tag=GLOBAL_FONT_REGISTRY_TAG):
        pass

    return GLOBAL_FONT_REGISTRY_TAG


def _build_segmdl2_font(size: int, tag: str):
    font_registry = _ensure_global_font_registry()
    font_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Assets", "Fonts", "SegMDL2.ttf"))
    font_id = dpg.add_font(font_path, size, parent=font_registry, tag=tag)
    return font_id


def get_segmdl2_icon_font(size=12):
    """Return the shared SegMDL2 icon font at 12 pt (default) or 18 pt."""
    if int(size) == 12:
        if dpg.does_item_exist(SEGMDL2_ICON_FONT_12_TAG):
            return SEGMDL2_ICON_FONT_12_TAG
        return _build_segmdl2_font(12, SEGMDL2_ICON_FONT_12_TAG)

    if int(size) == 18:
        if dpg.does_item_exist(SEGMDL2_ICON_FONT_18_TAG):
            return SEGMDL2_ICON_FONT_18_TAG
        return _build_segmdl2_font(18, SEGMDL2_ICON_FONT_18_TAG)

    raise ValueError(f"SegMDL2 icon font size {size} is not supported; use 12 or 18.")