from dearpygui import dearpygui as dpg
import time

last_tick = time.perf_counter()
count = 0

def resize_cb(sender=None, app_data=None):
    print("resize", time.perf_counter())


dpg.create_context()
dpg.create_viewport(title="DPG Resize Probe", width=800, height=500, resizable=True)
dpg.setup_dearpygui()

with dpg.window(label="Probe", width=300, height=200):
    dpg.add_text("Counter", tag="counter")


dpg.show_viewport()
dpg.set_viewport_resize_callback(resize_cb)

while dpg.is_dearpygui_running():
    now = time.perf_counter()
    if now - last_tick >= 0.1:
        count += 1
        dpg.set_value("counter", f"ticks: {count}")
        print("tick", now)
        last_tick = now
    dpg.render_dearpygui_frame()

dpg.destroy_context()
