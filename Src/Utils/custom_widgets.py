import dearpygui.dearpygui as dpg
import Utils.shared_state as shared_state


def add_input_float(**kwargs):
    callback = kwargs.pop("callback", None)
    kwargs.pop("on_enter", None)   # we control on_enter internally
    kwargs["on_enter"] = False     # no revert-on-focus-loss behaviour

    ret = dpg.add_input_float(**kwargs)

    tag = kwargs.get("tag", None)
    handler_tag = f"{tag}_Handler" if tag else f"dh_{ret}"

    def _on_activated(*_):
        shared_state.currently_editing = True

    def _on_deactivated(**args):
        shared_state.currently_editing = False
        if callback:
            callback(**args)

    with dpg.item_handler_registry(tag=handler_tag):
        dpg.add_item_activated_handler(callback=_on_activated)
        dpg.add_item_deactivated_handler(callback=_on_deactivated)
    dpg.bind_item_handler_registry(ret, handler_tag)

    return ret


def add_input_int(**kwargs):
    callback = kwargs.pop("callback", None)
    kwargs.pop("on_enter", None)
    kwargs["on_enter"] = False

    ret = dpg.add_input_int(**kwargs)

    tag = kwargs.get("tag", None)
    handler_tag = f"{tag}_Handler" if tag else f"dh_{ret}"

    def _on_activated(*_):
        shared_state.currently_editing = True

    def _on_deactivated(**args):
        shared_state.currently_editing = False
        if callback:
            callback(**args)

    with dpg.item_handler_registry(tag=handler_tag):
        dpg.add_item_activated_handler(callback=_on_activated)
        dpg.add_item_deactivated_handler(callback=_on_deactivated)
    dpg.bind_item_handler_registry(ret, handler_tag)

    return ret


def add_input_text(**kwargs):
    callback = kwargs.pop("callback", None)
    kwargs.pop("on_enter", None)
    kwargs["on_enter"] = False

    ret = dpg.add_input_text(**kwargs)

    tag = kwargs.get("tag", None)
    handler_tag = f"{tag}_Handler" if tag else f"dh_{ret}"

    def _on_activated(*_):
        shared_state.currently_editing = True

    def _on_deactivated(**args):
        shared_state.currently_editing = False
        if callback:
            callback(**args)

    with dpg.item_handler_registry(tag=handler_tag):
        dpg.add_item_activated_handler(callback=_on_activated)
        dpg.add_item_deactivated_handler(callback=_on_deactivated)
    dpg.bind_item_handler_registry(ret, handler_tag)

    return ret
