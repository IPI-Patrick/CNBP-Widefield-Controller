import os
from dearpygui import dearpygui as dpg
import importlib.util
from Utils.utils import load_window_classes

WINDOWS_FOLDER = os.path.join(os.path.dirname(__file__), "Windows")

def setup():
    # Create the window
    dpg.create_context()
    dpg.create_viewport(title='Widefield Controller', width=1600, height=1080, x_pos=0, y_pos=0, always_on_top=True)
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
    class_objects  = []
    for cls in window_classes:
            class_objects.append(cls())  # Each class should create its window in __init__
            # print(f"Failed to initialize window {cls.__name__}: {e}")

    dpg.show_viewport()

    
    
    # Start the Dear PyGui render loop
    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()

        render_loop(class_objects)


    # Cleanup after the loop ends
    dpg.destroy_context()

# Render loop for all the program
def render_loop(class_objects):    
    
    for cls in class_objects:
        if hasattr(cls, 'render'):
            cls.render()        



# This script sets up the Widefield Controller GUI using Dear PyGui.
if __name__ == "__main__":
    setup()