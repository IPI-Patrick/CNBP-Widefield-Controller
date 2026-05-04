import os

import dearpygui.dearpygui as dpg


GLOBAL_FONT_REGISTRY_TAG = "__global_font_registry"
SEGMDL2_ICON_FONT_12_TAG = "__segmdl2_icon_font_12"
SEGMDL2_SHARED_GLYPHS = [0xE117, 0xE71B, 0xE768, 0xE769, 0xE8EE, 0xE8CD]


def _ensure_global_font_registry():
    if dpg.does_item_exist(GLOBAL_FONT_REGISTRY_TAG):
        return GLOBAL_FONT_REGISTRY_TAG

    with dpg.font_registry(tag=GLOBAL_FONT_REGISTRY_TAG):
        pass

    return GLOBAL_FONT_REGISTRY_TAG


def get_segmdl2_icon_font(size=12):
    if int(size) != 12:
        raise ValueError("Only the shared 12 pt SegMDL2 icon font is currently supported")

    if dpg.does_item_exist(SEGMDL2_ICON_FONT_12_TAG):
        return SEGMDL2_ICON_FONT_12_TAG

    font_registry = _ensure_global_font_registry()
    font_path = os.path.abspath("src/Assets/Fonts/SegMDL2.ttf")
    font_id = dpg.add_font(font_path, 12, parent=font_registry, tag=SEGMDL2_ICON_FONT_12_TAG)
    dpg.add_font_chars(chars=SEGMDL2_SHARED_GLYPHS, parent=font_id)
    return font_id