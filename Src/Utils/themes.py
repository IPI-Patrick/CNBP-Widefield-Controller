import dearpygui.dearpygui as dpg

all_items = [
    dpg.mvSliderInt,
    dpg.mvSliderFloat,
    dpg.mvButton,
    dpg.mvCheckbox,
    dpg.mvInputText,         
    dpg.mvInputInt,
    dpg.mvInputFloat,   
    dpg.mvCombo,
    dpg.mvComboHeight_Large,
    dpg.mvComboHeight_Small,
    dpg.mvComboHeight_Regular,
    dpg.mvComboHeight_Largest
]

# Create the selected button theme
with dpg.theme() as selected_theme:
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button,          [0, 124, 80])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   [0, 124, 80])

with dpg.theme() as default_theme:
    pass



with dpg.theme() as red_green_button_enabled:
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button,          [0, 124, 80])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   [0, 100, 60])

with dpg.theme() as red_green_button_disabled:
    with dpg.theme_component(dpg.mvButton):
        pass
        # dpg.add_theme_color(dpg.mvThemeCol_Button,          ?)
        # dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   [166, 0, 0])

with dpg.theme() as yellow_button:
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button,          [255, 204, 0])
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   [204, 153, 0])



with dpg.theme() as read_only_theme:
    for item in all_items:
        with dpg.theme_component(item):
            dpg.add_theme_color(dpg.mvThemeCol_Text,        [100, 100, 100])
            dpg.add_theme_color(dpg.mvThemeCol_Button,      [30, 30, 30])
        

with dpg.theme() as disabled_theme:

    for item in all_items:
        with dpg.theme_component(item):
            dpg.add_theme_color(dpg.mvThemeCol_Text,        [80, 80, 80])
            dpg.add_theme_color(dpg.mvThemeCol_Button,      [30, 30, 30])

with dpg.theme() as no_padding_theme:
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)

with dpg.theme() as no_spacing_theme:
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)

with dpg.theme() as transparent_plot_theme:
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvPlotCol_PlotBg, [0, 0, 0, 0])
        dpg.add_theme_color(dpg.mvPlotCol_FrameBg, [0, 0, 0, 0])
        dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, [0, 0, 0, 0])
    with dpg.theme_component(dpg.mvChildWindow):
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, [0, 0, 0, 0])

