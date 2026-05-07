import os

import dearpygui.dearpygui as dpg
import numpy as np

from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file
from Utils.themes import no_padding_theme


class OscilloscopeWindow:

	def __init__(self, trace_getters, *, title="Oscilloscope", channel_headers=None, width=880, height=420, pos=(625, 825), state_name=None, tag=None, parent=None, embedded=False):
		self._title = str(title)
		self._trace_getters = list(trace_getters)
		self._channel_headers = list(channel_headers or [])
		self._state_name = str(state_name or type(self).__name__)
		self._tag = str(tag or f"#{self._state_name}")
		self._embedded = bool(embedded)
		self._trace_items = {}
		self._trace_layout = ()
		self._subplots_id = None
		self._empty_plot_id = None
		self._y_half_range_volts = 5.0
		self._min_half_range_volts = 0.05
		self._max_half_range_volts = 100.0
		self._x_view_limits = None
		self._x_data_limits = (0.0, 1.0)
		self._min_x_range_seconds = 1e-6
		self._x_pan_state = None
		self.window_id = None
		self.root_container_id = None

		with dpg.font_registry():
			label_font_path = os.path.abspath("src/Assets/Fonts/arial.ttf")
			self.channel_label_font = dpg.add_font(label_font_path, 18)

		if self._embedded:
			with dpg.group(parent=parent) as self.root_container_id:
				with dpg.child_window(border=False, width=-1, height=height, no_scrollbar=True, no_scroll_with_mouse=True):
					self.content_container_id = dpg.last_item()
		else:
			with dpg.window(
				label=self._title,
				tag=self._tag,
				width=width,
				height=height,
				pos=pos,
				no_scrollbar=True,
				no_resize=False,
				no_scroll_with_mouse=True,
			):
				self.window_id = dpg.last_item()
				self.root_container_id = self.window_id
				dpg.bind_item_theme(self.window_id, no_padding_theme)

				with dpg.child_window(border=False, autosize_x=True, autosize_y=True):
					self.content_container_id = dpg.last_item()

		handler_tag = f"{self._state_name}_MouseHandlers"
		with dpg.handler_registry(tag=handler_tag):
			dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Middle, callback=self._on_middle_mouse_down)
			dpg.add_mouse_move_handler(callback=self._on_mouse_move)
			dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_release)
			dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)

		self._rebuild_layout([])

	def _default_trace_color(self, index):
		palette = (
			(86, 180, 233, 255),
			(230, 159, 0, 255),
			(0, 158, 115, 255),
			(204, 121, 167, 255),
			(213, 94, 0, 255),
			(0, 114, 178, 255),
			(240, 228, 66, 255),
			(128, 128, 128, 255),
		)
		return list(palette[index % len(palette)])

	def _normalize_trace_payload(self, payload, index):
		if payload is None:
			payload = {}

		panel_id = str(payload.get("panel_id", index)) 
		label = payload.get(
			"label",
			self._channel_headers[index] if index < len(self._channel_headers) else f"Channel {index + 1}",
		)
		x_values = np.asarray(payload.get("x_values", []), dtype=np.float64)
		y_values = np.asarray(payload.get("y_values", []), dtype=np.float32)
		color = list(payload.get("color", self._default_trace_color(index)))
		abs_last_x = float(payload.get("abs_last_x", x_values[-1] if x_values.size > 0 else 0.0))
		return {
			"panel_id": panel_id,
			"label": str(label),
			"x_values": x_values,
			"y_values": y_values,
			"color": color,
			"abs_last_x": abs_last_x,
		}

	def is_visible(self):
		container_id = self.root_container_id or self.window_id
		return container_id is not None and dpg.does_item_exist(container_id) and dpg.is_item_shown(container_id)

	def _get_traces(self):
		traces = []
		for index, getter in enumerate(self._trace_getters):
			if getter is None:
				continue
			payload = getter()
			if payload is None:
				continue
			traces.append(self._normalize_trace_payload(payload, index))
		return traces

	def _apply_y_limits(self):
		for trace_items in self._trace_items.values():
			y_axis_id = trace_items["y_axis_id"]
			if dpg.does_item_exist(y_axis_id):
				dpg.set_axis_limits(y_axis_id, -self._y_half_range_volts, self._y_half_range_volts)

	def _normalize_color(self, color):
		color_values = list(color)
		if len(color_values) < 4:
			color_values = color_values[:3] + [255]
		return tuple(int(round(value)) for value in color_values[:4])

	def _apply_trace_color(self, trace_items, color):
		normalized_color = self._normalize_color(color)
		if trace_items.get("color") == normalized_color:
			return

		theme_id = trace_items.get("theme_id")
		if theme_id is not None and dpg.does_item_exist(theme_id):
			dpg.delete_item(theme_id)

		with dpg.theme() as theme_id:
			with dpg.theme_component(dpg.mvLineSeries):
				dpg.add_theme_color(dpg.mvPlotCol_Line, normalized_color, category=dpg.mvThemeCat_Plots)

		trace_items["theme_id"] = theme_id
		trace_items["color"] = normalized_color
		dpg.bind_item_theme(trace_items["line_series_id"], theme_id)
		if dpg.does_item_exist(trace_items["label_id"]):
			dpg.configure_item(trace_items["label_id"], color=normalized_color)

	def _update_trace_label(self, trace_items, trace):
		trace_items["label_text"] = trace["label"]
		if dpg.does_item_exist(trace_items["label_id"]):
			dpg.set_value(trace_items["label_id"], trace["label"])
			plot_pos = dpg.get_item_pos(trace_items["plot_id"])
			dpg.set_item_pos(trace_items["label_id"], (int(plot_pos[0]) + 50, int(plot_pos[1]) + 8))

	def _ensure_trace_items(self, trace):
		panel_id = trace["panel_id"]
		if panel_id in self._trace_items:
			trace_items = self._trace_items[panel_id]
			dpg.configure_item(trace_items["line_series_id"], label=trace["label"])
			return trace_items

		raise KeyError(f"Missing oscilloscope subplot for panel {panel_id}")

	def _rebuild_layout(self, traces):
		self._trace_layout = tuple(trace["panel_id"] for trace in traces)
		for trace_items in self._trace_items.values():
			theme_id = trace_items.get("theme_id")
			if theme_id is not None and dpg.does_item_exist(theme_id):
				dpg.delete_item(theme_id)
		self._trace_items = {}
		if dpg.does_item_exist(self.content_container_id):
			dpg.delete_item(self.content_container_id, children_only=True)

		if not traces:
			with dpg.plot(parent=self.content_container_id, width=-1, height=-1, no_menus=True, no_title=True):
				self._empty_plot_id = dpg.last_item()
				x_axis_id = dpg.add_plot_axis(dpg.mvXAxis, label="", no_label=True)
				y_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="", no_label=True, no_initial_fit=True)
				dpg.set_axis_limits(x_axis_id, 0.0, 1.0)
				dpg.set_axis_limits(y_axis_id, -self._y_half_range_volts, self._y_half_range_volts)
			return

		self._empty_plot_id = None
		with dpg.subplots(
			len(traces),
			1,
			parent=self.content_container_id,
			width=-1,
			height=-1,
			link_all_x=True,
			row_ratios=[1.0] * len(traces),
			no_title=True,
			no_menus=True,
		) as self._subplots_id:
			for index, trace in enumerate(traces):
				with dpg.plot(width=-1, height=-1, no_menus=True, no_box_select=True, no_title=True):
					plot_id = dpg.last_item()
					x_axis_id = dpg.add_plot_axis(
						dpg.mvXAxis,
						label="",
						no_label=True,
						no_tick_labels=index < (len(traces) - 1),
						no_tick_marks=index < (len(traces) - 1),
					)
					y_axis_id = dpg.add_plot_axis(dpg.mvYAxis, label="", no_label=True, no_initial_fit=True)
					line_series_id = dpg.add_line_series([], [], label=trace["label"], parent=y_axis_id)
					label_id = dpg.add_text(trace["label"], parent=self.content_container_id, color=self._normalize_color(trace["color"]), pos=(10, 0))

					dpg.bind_item_font(label_id, self.channel_label_font)
					self._trace_items[trace["panel_id"]] = {
						"plot_id": plot_id,
						"x_axis_id": x_axis_id,
						"y_axis_id": y_axis_id,
						"line_series_id": line_series_id,
						"label_id": label_id,
						"theme_id": None,
						"color": None,
						"label_text": trace["label"],
						"data_signature": None,
					}
					self._apply_trace_color(self._trace_items[trace["panel_id"]], trace["color"])

		self._apply_y_limits()

	def _remove_trace_items(self, active_panel_ids):
		for panel_id in list(self._trace_items):
			if panel_id not in active_panel_ids:
				self._trace_layout = ()
				break

	def _get_hovered_trace_items(self):
		for trace_items in self._trace_items.values():
			if dpg.does_item_exist(trace_items["plot_id"]) and dpg.is_item_hovered(trace_items["plot_id"]):
				return trace_items
		return None

	def _clamp_x_view_limits(self, view_min, view_max):
		data_min, data_max = self._x_data_limits
		data_min = float(data_min)
		data_max = float(data_max)
		full_range = max(self._min_x_range_seconds, data_max - data_min)
		view_range = max(self._min_x_range_seconds, float(view_max) - float(view_min))

		if view_range >= full_range * 0.999:
			return None

		if view_min < data_min:
			view_max = min(data_max, float(view_max) + (data_min - float(view_min)))
			view_min = data_min
		if view_max > data_max:
			view_min = max(data_min, float(view_min) - (float(view_max) - data_max))
			view_max = data_max

		return (float(view_min), float(view_max))

	def _on_middle_mouse_down(self, sender, app_data):
		trace_items = self._get_hovered_trace_items()
		if trace_items is None:
			return

		data_min, data_max = self._x_data_limits
		current_limits = self._x_view_limits if self._x_view_limits is not None else (float(data_min), float(data_max))
		current_range = max(self._min_x_range_seconds, float(current_limits[1]) - float(current_limits[0]))
		full_range = max(self._min_x_range_seconds, float(data_max) - float(data_min))
		if current_range >= full_range * 0.999:
			return

		plot_width = max(1.0, float(dpg.get_item_rect_size(trace_items["plot_id"])[0]))
		self._x_pan_state = {
			"start_mouse_x": float(dpg.get_mouse_pos(local=False)[0]),
			"start_limits": (float(current_limits[0]), float(current_limits[1])),
			"plot_width": plot_width,
		}

	def _on_mouse_move(self, sender, app_data):
		if self._x_pan_state is None:
			return

		current_mouse_x = float(dpg.get_mouse_pos(local=False)[0])
		start_mouse_x = self._x_pan_state["start_mouse_x"]
		start_min, start_max = self._x_pan_state["start_limits"]
		plot_width = max(1.0, float(self._x_pan_state["plot_width"]))
		view_range = max(self._min_x_range_seconds, float(start_max) - float(start_min))
		delta_seconds = -((current_mouse_x - start_mouse_x) / plot_width) * view_range
		new_limits = self._clamp_x_view_limits(start_min + delta_seconds, start_max + delta_seconds)
		self._x_view_limits = new_limits

	def _on_mouse_release(self, sender, app_data):
		self._x_pan_state = None

	def _on_mouse_wheel(self, sender, app_data):
		if self._get_hovered_trace_items() is None:
			return

		wheel_delta = float(app_data)
		if wheel_delta == 0.0:
			return

		if dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift):
			data_min, data_max = self._x_data_limits
			full_range = max(self._min_x_range_seconds, float(data_max) - float(data_min))
			current_min, current_max = self._x_view_limits if self._x_view_limits is not None else (float(data_min), float(data_max))
			current_range = max(self._min_x_range_seconds, float(current_max) - float(current_min))
			zoom_factor = 1.15 ** wheel_delta
			new_range = max(self._min_x_range_seconds, min(full_range, current_range / zoom_factor))

			if new_range >= full_range * 0.999:
				self._x_view_limits = None
				return

			center = (float(current_min) + float(current_max)) * 0.5
			half_range = new_range * 0.5
			new_min = center - half_range
			new_max = center + half_range

			if new_min < data_min:
				new_max = min(float(data_max), new_max + (float(data_min) - new_min))
				new_min = float(data_min)
			if new_max > data_max:
				new_min = max(float(data_min), new_min - (new_max - float(data_max)))
				new_max = float(data_max)

			self._x_view_limits = self._clamp_x_view_limits(new_min, new_max)
			return

		zoom_factor = 1.15 ** wheel_delta
		self._y_half_range_volts = min(
			self._max_half_range_volts,
			max(self._min_half_range_volts, self._y_half_range_volts / zoom_factor),
		)
		self._apply_y_limits()

	def render(self):
		if not self.is_visible():
			return

		traces = self._get_traces()
		active_panel_ids = {trace["panel_id"] for trace in traces}
		self._remove_trace_items(active_panel_ids)
		trace_layout = tuple(trace["panel_id"] for trace in traces)
		if trace_layout != self._trace_layout:
			self._rebuild_layout(traces)

		x_min = None
		x_max = None

		for trace in traces:
			trace_items = self._ensure_trace_items(trace)
			x_values = trace["x_values"]
			y_values = trace["y_values"]
			point_count = int(x_values.size)
			if point_count > 0:
				data_signature = (
					point_count,
					float(trace["abs_last_x"]),
					float(y_values[-1]),
				)
			else:
				data_signature = (0,)
			if data_signature != trace_items.get("data_signature"):
				dpg.set_value(trace_items["line_series_id"], [x_values.tolist(), y_values.tolist()])
				trace_items["data_signature"] = data_signature
			self._apply_trace_color(trace_items, trace["color"])

			if point_count > 0:
				current_min = float(x_values[0])
				current_max = float(x_values[-1])
				x_min = current_min if x_min is None else min(x_min, current_min)
				x_max = current_max if x_max is None else max(x_max, current_max)

		if x_min is None or x_max is None:
			self._x_data_limits = (0.0, 1.0)
			self._x_view_limits = None
		else:
			self._x_data_limits = (float(x_min), float(x_max))

		if not traces and self._empty_plot_id is not None and dpg.does_item_exist(self._empty_plot_id):
			return

		view_limits = self._x_view_limits
		if x_min is not None and x_max is not None and view_limits is not None:
			self._x_view_limits = self._clamp_x_view_limits(float(view_limits[0]), float(view_limits[1]))
			view_limits = self._x_view_limits

		for trace_items in self._trace_items.values():
			x_axis_id = trace_items["x_axis_id"]
			if view_limits is not None:
				dpg.set_axis_limits(x_axis_id, float(view_limits[0]), float(view_limits[1]))
			elif x_min is None or x_max is None:
				dpg.set_axis_limits(x_axis_id, 0.0, 1.0)
			elif x_min == x_max:
				dpg.set_axis_limits(x_axis_id, x_min, x_min + 1.0)
			else:
				dpg.set_axis_limits(x_axis_id, x_min, x_max)

		for trace in traces:
			self._update_trace_label(self._trace_items[trace["panel_id"]], trace)

		self._apply_y_limits()

	def SaveState(self):
		payload = {
			"y_half_range_volts": float(self._y_half_range_volts),
			"x_view_limits": list(self._x_view_limits) if self._x_view_limits is not None else None,
		}
		if not self._embedded:
			payload["window"] = capture_window_state(self.window_id)
		save_state_file(self._state_name, payload)

	def LoadState(self):
		state = load_state_file(self._state_name)
		if not state:
			return

		if not self._embedded:
			apply_window_state(self.window_id, state.get("window"))
		if "y_half_range_volts" in state:
			self._y_half_range_volts = min(
				self._max_half_range_volts,
				max(self._min_half_range_volts, float(state["y_half_range_volts"])),
			)
		x_view_limits = state.get("x_view_limits")
		if x_view_limits is None:
			self._x_view_limits = None
		elif len(x_view_limits) == 2:
			self._x_view_limits = (float(x_view_limits[0]), float(x_view_limits[1]))
		self._apply_y_limits()
