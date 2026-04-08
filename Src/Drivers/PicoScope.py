import ctypes
from collections import deque
import importlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback

import numpy as np


MODEL_NAME = "2204A"
CHANNEL_NAMES = ("A", "B")
DEFAULT_HISTORY_SECONDS = 1.0
CAPTURE_BLOCK_DURATION_SECONDS = 0.1
MAX_ADC_VALUE = 32767.0
SUPPORTED_AWG_WAVEFORMS = ("dc", "sine", "square", "triangle")
PS2000_AWG_WAVE_TYPES = {
    "dc": "PS2000_DC_VOLTAGE",
    "sine": "PS2000_SINE",
    "square": "PS2000_SQUARE",
    "triangle": "PS2000_TRIANGLE",
}
SUPPORTED_DATA_BITS = {
    "uint8": np.uint8,
    "uint16": np.uint16,
    "float16": np.float16,
    "float32": np.float32,
}
SUPPORTED_COUPLINGS = ("AC", "DC")
PICO_USB_VENDOR_ID = "VID_0CE9"
PICO_2204A_PRODUCT_IDS = ("PID_1007",)


def _ensure_picosdk_import_path():
    api_root = Path(__file__).resolve().parents[1] / "APIs" / "picosdk"
    api_root_str = str(api_root)
    if api_root.exists() and api_root_str not in sys.path:
        sys.path.insert(0, api_root_str)


_ensure_picosdk_import_path()

pico_functions = importlib.import_module("picosdk.functions")
assert_pico2000_ok = pico_functions.assert_pico2000_ok
ps2000 = importlib.import_module("picosdk.ps2000").ps2000
ctypes_wrapper = importlib.import_module("picosdk.ctypes_wrapper")
C_CALLBACK_FUNCTION_FACTORY = ctypes_wrapper.C_CALLBACK_FUNCTION_FACTORY

SUPPORTED_VOLTAGE_RANGES = tuple(ps2000.PS2000_VOLTAGE_RANGE.keys())
PS2000_RANGE_MILLIVOLTS = {
    name: float([10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000][enum_value])
    for name, enum_value in ps2000.PS2000_VOLTAGE_RANGE.items()
}
PS2000_TIME_UNIT_TO_SECONDS = {
    ps2000.PS2000_TIME_UNITS["PS2000_FS"]: 1e-15,
    ps2000.PS2000_TIME_UNITS["PS2000_PS"]: 1e-12,
    ps2000.PS2000_TIME_UNITS["PS2000_NS"]: 1e-9,
    ps2000.PS2000_TIME_UNITS["PS2000_US"]: 1e-6,
    ps2000.PS2000_TIME_UNITS["PS2000_MS"]: 1e-3,
    ps2000.PS2000_TIME_UNITS["PS2000_S"]: 1.0,
}
STREAMING_CALLBACK = C_CALLBACK_FUNCTION_FACTORY(None, ctypes.POINTER(ctypes.POINTER(ctypes.c_int16)), ctypes.c_int16, ctypes.c_uint32, ctypes.c_int16, ctypes.c_int16, ctypes.c_uint32)


def _canonical_dtype_name(data_bits):
    dtype_name = np.dtype(data_bits).name
    if dtype_name not in SUPPORTED_DATA_BITS:
        supported = ", ".join(sorted(SUPPORTED_DATA_BITS))
        raise ValueError(f"Unsupported data-bits '{data_bits}'. Supported values: {supported}")
    return dtype_name


def _normalize_voltage_range(range_name):
    range_text = str(range_name).upper()
    if range_text.startswith("PS2000_"):
        return range_text
    if range_text.startswith("PS2000A_"):
        return f"PS2000_{range_text[len('PS2000A_'):]}"
    return f"PS2000_{range_text}"


def _convert_samples(raw_samples, dtype_name):
    target_dtype = SUPPORTED_DATA_BITS[dtype_name]
    raw_array = np.asarray(raw_samples)

    if np.issubdtype(target_dtype, np.unsignedinteger):
        limits = np.iinfo(target_dtype)
        clipped = np.clip(raw_array, 0, limits.max)
        return clipped.astype(target_dtype, copy=False)

    return raw_array.astype(target_dtype, copy=False)


def _emit_payload(output_queue, timestamps, channel_arrays, dtype_name):
    payload = {
        "kind": "data",
        "timestamps": timestamps.tolist(),
        "channels": {},
    }

    for channel_name, raw_samples in channel_arrays.items():
        converted = _convert_samples(raw_samples, dtype_name)
        payload["channels"][channel_name] = converted.tolist()

    try:
        output_queue.put_nowait(payload)
    except queue.Full:
        pass


def _apply_awg_output_to_device(device, awg_config):
    waveform_type = awg_config["waveform_type"]
    offset_microvolts = int(round(float(awg_config["offset_volts"]) * 1_000_000.0))
    if waveform_type == "dc":
        pk_to_pk_microvolts = 0
        frequency_hz = 0.0
    else:
        pk_to_pk_microvolts = int(round(float(awg_config["amplitude_vpp_volts"]) * 1_000_000.0))
        frequency_hz = float(awg_config["frequency_hz"])


    ps2000.ps2000_set_sig_gen_built_in(
        device.handle,
        offset_microvolts,
        pk_to_pk_microvolts,
        ps2000.PS2000_WAVE_TYPE[PS2000_AWG_WAVE_TYPES[waveform_type]],
        float(frequency_hz),
        float(frequency_hz),
        0.0,
        0.0,
        ps2000.PS2000_SWEEP_TYPE["PS2000_UP"],
        0,
    )


def _disable_awg_output_on_device(device):
    ps2000.ps2000_set_sig_gen_built_in(
        device.handle,
        0,
        0,
        ps2000.PS2000_WAVE_TYPE[PS2000_AWG_WAVE_TYPES["dc"]],
        0.0,
        0.0,
        0.0,
        0.0,
        ps2000.PS2000_SWEEP_TYPE["PS2000_UP"],
        0,
    )


def _assert_ps2000_call_ok(status, action_name):
    try:
        assert_pico2000_ok(status)
    except Exception as exc:
        raise RuntimeError(f"{action_name} failed in ps2000 API with status {status}.") from exc


def _extract_instance_tail(instance_id):
    instance_text = str(instance_id or "").strip()
    if not instance_text:
        return ""

    instance_tail = instance_text.split("\\")[-1].strip()
    if not instance_tail or instance_tail.startswith("{"):
        return ""
    return instance_tail


def _list_available_devices_windows():
    powershell_script = """
    $devices = Get-CimInstance Win32_PnPEntity | Where-Object {
        ($_.PNPDeviceID -like '*__PICO_USB_VENDOR_ID__*') -or
        ($_.Name -like '*PicoScope*') -or
        ($_.Description -like '*PicoScope*') -or
        ($_.Manufacturer -like '*Pico Technology*')
    } | Select-Object Name, Description, Manufacturer, PNPDeviceID
    $devices | ConvertTo-Json -Compress
    """.replace("__PICO_USB_VENDOR_ID__", PICO_USB_VENDOR_ID)

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            powershell_script,
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=creation_flags,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to enumerate PicoScope devices from Windows device manager.")

    stdout = result.stdout.strip()
    if not stdout:
        return []

    raw_devices = json.loads(stdout)
    if isinstance(raw_devices, dict):
        raw_devices = [raw_devices]

    devices = []
    seen_ids = set()
    for device_info in raw_devices:
        instance_id = str(device_info.get("PNPDeviceID") or "").strip()
        if not instance_id or instance_id in seen_ids:
            continue

        instance_id_upper = instance_id.upper()
        if not any(product_id in instance_id_upper for product_id in PICO_2204A_PRODUCT_IDS):
            continue

        seen_ids.add(instance_id)
        name = str(device_info.get("Name") or device_info.get("Description") or MODEL_NAME).strip()
        instance_tail = _extract_instance_tail(instance_id)
        label_parts = [MODEL_NAME, name]
        if instance_tail:
            label_parts.append(instance_tail)

        devices.append(
            {
                "model": MODEL_NAME,
                "serial": "",
                "has_verified_serial": False,
                "variant": MODEL_NAME,
                "instance_id": instance_id,
                "instance_tail": instance_tail,
                "label": " | ".join(label_parts),
            }
        )

    return devices


def _find_timebase(handle, sample_count, target_interval_ns):
    timebase = 1
    oversample = ctypes.c_int16(1)

    while True:
        time_interval = ctypes.c_int32()
        time_units = ctypes.c_int16()
        max_samples = ctypes.c_int32()
        status = ps2000.ps2000_get_timebase(
            handle,
            timebase,
            sample_count,
            ctypes.byref(time_interval),
            ctypes.byref(time_units),
            oversample,
            ctypes.byref(max_samples),
        )
        if status > 0 and time_interval.value >= target_interval_ns:
            return timebase, float(time_interval.value), int(time_units.value), oversample

        timebase += 1
        if timebase > 65535:
            raise RuntimeError("Could not find a valid 2204A timebase for the requested sample rate.")


def _select_supported_streaming_interval(handle, sample_rate_hz, sample_count):
    target_interval_ns = max(1.0, 1e9 / max(float(sample_rate_hz), 1e-12))
    _timebase, time_interval_ns, _time_units, _oversample = _find_timebase(handle, sample_count, target_interval_ns)
    interval_seconds = max(1e-12, float(time_interval_ns) * 1e-9)
    interval_value, interval_units, rounded_interval_seconds = _select_streaming_interval(1.0 / interval_seconds)
    return interval_value, interval_units, rounded_interval_seconds


def _compute_buffer_capacity(sample_rate_hz, history_seconds):
    return max(1, int(round(float(sample_rate_hz) * float(history_seconds))))


def _compute_capture_block_samples(sample_rate_hz):
    return max(32, int(round(float(sample_rate_hz) * CAPTURE_BLOCK_DURATION_SECONDS)))


def _select_streaming_interval(sample_rate_hz):
    interval_seconds = 1.0 / max(float(sample_rate_hz), 1e-12)
    unit_candidates = (
        ("PS2000_NS", 1e-9),
        ("PS2000_US", 1e-6),
        ("PS2000_MS", 1e-3),
        ("PS2000_S", 1.0),
    )

    best_interval = None
    best_units = None
    best_error = None
    for unit_name, unit_seconds in unit_candidates:
        raw_value = interval_seconds / unit_seconds
        interval_value = max(1, int(round(raw_value)))
        if interval_value > 0xFFFFFFFF:
            continue

        actual_interval_seconds = interval_value * unit_seconds
        error = abs(actual_interval_seconds - interval_seconds)
        if best_error is None or error < best_error:
            best_interval = interval_value
            best_units = ps2000.PS2000_TIME_UNITS[unit_name]
            best_error = error

    if best_interval is None or best_units is None:
        raise RuntimeError("Could not determine a valid streaming interval for the requested sample rate.")

    return best_interval, best_units, best_interval * PS2000_TIME_UNIT_TO_SECONDS[best_units]


def _select_streaming_interval_ms(sample_rate_hz):
    interval_ms = max(1, int(round(1000.0 / max(float(sample_rate_hz), 1e-12))))
    interval_seconds = interval_ms * 1e-3
    return interval_ms, interval_seconds


def _normalize_awg_waveform_type(waveform_type):
    waveform_text = str(waveform_type).strip().lower()
    if waveform_text not in SUPPORTED_AWG_WAVEFORMS:
        raise ValueError(f"Unsupported AWG waveform '{waveform_type}'. Expected one of: {', '.join(SUPPORTED_AWG_WAVEFORMS)}")
    return waveform_text


def _validate_2204a_device(device):
    variant_value = device.info.variant
    if isinstance(variant_value, bytes):
        variant_value = variant_value.decode("utf-8", errors="ignore")
    variant_text = str(variant_value).strip() or MODEL_NAME
    if variant_text.upper() != MODEL_NAME:
        raise RuntimeError(f"Detected unsupported PicoScope model '{variant_text}'. This driver only supports {MODEL_NAME}.")
    return variant_text


def _run_2204a_capture(device, config, output_queue, stop_event, control_queue, api_lock):
    status = {}
    enabled_channels = [name for name in CHANNEL_NAMES if config["channels"][name]["enabled"]]
    if not enabled_channels:
        raise RuntimeError("At least one 2204A channel must be enabled before starting collection.")

    sample_interval_value, sample_interval_units, interval_seconds = _select_supported_streaming_interval(
        device.handle,
        config["sample_rate_hz"],
        max(32, int(config.get("capture_block_samples") or 32)),
    )
    actual_rate_hz = 1.0 / max(interval_seconds, 1e-12)
    max_samples = 100000
    overview_buffer_size = 50000
    buffer_indices = {channel_name: index for index, channel_name in enumerate(enabled_channels)}

    try:
        _validate_2204a_device(device)

        with api_lock:
            for channel_name in CHANNEL_NAMES:
                channel_config = config["channels"][channel_name]
                range_name = _normalize_voltage_range(channel_config["range"])
                status[f"set_channel_{channel_name}"] = ps2000.ps2000_set_channel(
                    device.handle,
                    ps2000.PICO_CHANNEL[channel_name],
                    1 if channel_config["enabled"] else 0,
                    ps2000.PICO_COUPLING[channel_config["coupling"]],
                    ps2000.PS2000_VOLTAGE_RANGE[range_name],
                )
                _assert_ps2000_call_ok(status[f"set_channel_{channel_name}"], f"ps2000_set_channel({channel_name})")

            status["run_streaming_ns"] = ps2000.ps2000_run_streaming_ns(
                device.handle,
                sample_interval_value,
                sample_interval_units,
                max_samples,
                0,
                1,
                overview_buffer_size,
            )
            _assert_ps2000_call_ok(status["run_streaming_ns"], "ps2000_run_streaming_ns")

        output_queue.put(
            {
                "kind": "meta",
                "actual_sample_rate_hz": actual_rate_hz,
                "enabled_channels": list(enabled_channels),
                "active_scope_series": MODEL_NAME,
                "streaming_mode": "ns",
            }
        )

        next_sample_time = float(config.get("time_offset_seconds", 0.0))

        def get_overview_buffers(buffers, _overflow, _triggered_at, _triggered, _auto_stop, n_values):
            nonlocal next_sample_time
            n_values = int(n_values)
            if n_values <= 0:
                return

            timestamps = next_sample_time + (np.arange(n_values, dtype=np.float64) * interval_seconds)
            next_sample_time = float(timestamps[-1] + interval_seconds)
            channel_arrays = {}
            for channel_name, buffer_index in buffer_indices.items():
                channel_arrays[channel_name] = np.array(buffers[buffer_index][0:n_values], dtype=np.int16, copy=True)
            _emit_payload(output_queue, timestamps, channel_arrays, config["data_bits"])

        callback = STREAMING_CALLBACK(get_overview_buffers)

        while not stop_event.is_set():
            if control_queue is not None:
                while True:
                    try:
                        command = control_queue.get_nowait()
                    except queue.Empty:
                        break

                    if command.get("kind") == "set_awg_enabled":
                        enabled = bool(command.get("enabled"))
                        awg_config = dict(command.get("awg_config") or config["awg_config"])
                        config["awg_config"] = awg_config
                        with api_lock:
                            if enabled:
                                _apply_awg_output_to_device(device, awg_config)
                            else:
                                _disable_awg_output_on_device(device)

            with api_lock:
                status["get_streaming_last_values"] = ps2000.ps2000_get_streaming_last_values(device.handle, callback)
            if status["get_streaming_last_values"] < 0:
                _assert_ps2000_call_ok(status["get_streaming_last_values"], "ps2000_get_streaming_last_values")
            time.sleep(0.01)
    finally:
        try:
            with api_lock:
                ps2000.ps2000_stop(device.handle)
        except Exception:
            pass


def _picoscope_worker(device, config, output_queue, stop_event, control_queue, api_lock):
    try:
        _run_2204a_capture(device, config, output_queue, stop_event, control_queue, api_lock)
    except Exception as exc:
        print(f"PicoScope worker error: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        try:
            output_queue.put(
                {
                    "kind": "error",
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
            )
        except Exception:
            pass

    try:
        output_queue.put({"kind": "stopped"})
    except Exception:
        pass


class PicoScope:

    def __init__(self, sample_rate_hz=1000.0, history_seconds=DEFAULT_HISTORY_SECONDS, data_bits="float32"):
        self.sample_rate_hz = float(sample_rate_hz)
        self.history_seconds = float(history_seconds)
        self.data_bits = _canonical_dtype_name(data_bits)
        self.device_model = MODEL_NAME
        self.active_scope_series = MODEL_NAME
        self.serial_number = ""

        self.channels = {
            "A": {"enabled": True, "coupling": "DC", "range": "PS2000_2V"},
            "B": {"enabled": False, "coupling": "DC", "range": "PS2000_2V"},
        }
        self.awg_enabled = False
        self.awg_config = {
            "waveform_type": "dc",
            "offset_volts": 0.0,
            "amplitude_vpp_volts": 1.0,
            "frequency_hz": 1000.0,
        }

        self.buffer_capacity = _compute_buffer_capacity(self.sample_rate_hz, self.history_seconds)
        self.data_lock = threading.Lock()
        self.channel_data = {channel_name: deque(maxlen=self.buffer_capacity) for channel_name in CHANNEL_NAMES}
        self.timestamps = deque(maxlen=self.buffer_capacity)
        self.actual_sample_rate_hz = None
        self.last_error = None

        self._output_queue = None
        self._stop_event = None
        self._worker_thread = None
        self._listener_thread = None
        self._listener_stop_event = threading.Event()
        self._control_queue = None
        self._device = None
        self._device_api_lock = threading.RLock()

    @property
    def available_channels(self):
        return CHANNEL_NAMES

    @property
    def supported_couplings(self):
        return SUPPORTED_COUPLINGS

    @property
    def supported_voltage_ranges(self):
        return SUPPORTED_VOLTAGE_RANGES

    @property
    def supported_awg_waveforms(self):
        return SUPPORTED_AWG_WAVEFORMS

    def get_channel_input_range_volts(self, channel_name):
        normalized_name = str(channel_name).upper()
        if normalized_name not in self.channels:
            raise ValueError(f"Unsupported channel '{channel_name}'.")

        range_name = _normalize_voltage_range(self.channels[normalized_name]["range"])
        return PS2000_RANGE_MILLIVOLTS[range_name] / 1000.0

    def convert_samples_to_volts(self, channel_name, samples):
        raw_values = np.asarray(samples, dtype=np.float32)
        return (raw_values * self.get_channel_input_range_volts(channel_name)) / MAX_ADC_VALUE

    def list_available_devices(self):
        if sys.platform == "win32":
            try:
                return _list_available_devices_windows()
            except Exception:
                return []
        return []

    def _reset_buffers(self):
        self.buffer_capacity = _compute_buffer_capacity(self.actual_sample_rate_hz or self.sample_rate_hz, self.history_seconds)
        with self.data_lock:
            self.channel_data = {channel_name: deque(maxlen=self.buffer_capacity) for channel_name in CHANNEL_NAMES}
            self.timestamps = deque(maxlen=self.buffer_capacity)

    def _resize_history_buffers(self, sample_rate_hz):
        new_capacity = _compute_buffer_capacity(sample_rate_hz, self.history_seconds)
        if new_capacity == self.buffer_capacity:
            return

        self.buffer_capacity = new_capacity
        with self.data_lock:
            self.timestamps = deque(self.timestamps, maxlen=new_capacity)
            self.channel_data = {
                channel_name: deque(samples, maxlen=new_capacity)
                for channel_name, samples in self.channel_data.items()
            }

    def _build_worker_config(self):
        with self.data_lock:
            if self.timestamps:
                sample_rate_hz = self.actual_sample_rate_hz or self.sample_rate_hz
                time_offset_seconds = float(self.timestamps[-1]) + (1.0 / max(float(sample_rate_hz), 1e-12))
            else:
                time_offset_seconds = 0.0

        return {
            "sample_rate_hz": self.sample_rate_hz,
            "capture_block_samples": _compute_capture_block_samples(self.sample_rate_hz),
            "data_bits": self.data_bits,
            "channels": {name: dict(config) for name, config in self.channels.items()},
            "awg_config": dict(self.awg_config),
            "time_offset_seconds": time_offset_seconds,
        }

    def _listener_loop(self):
        while not self._listener_stop_event.is_set():
            if self._output_queue is None:
                break

            try:
                message = self._output_queue.get(timeout=0.1)
            except queue.Empty:
                if self._worker_thread is None or not self._worker_thread.is_alive():
                    break
                continue

            kind = message.get("kind")
            if kind == "meta":
                self.actual_sample_rate_hz = float(message["actual_sample_rate_hz"])
                self.active_scope_series = message.get("active_scope_series", MODEL_NAME)
                self._resize_history_buffers(self.actual_sample_rate_hz)
            elif kind == "data":
                with self.data_lock:
                    self.timestamps.extend(message.get("timestamps", []))
                    for channel_name, samples in message.get("channels", {}).items():
                        if channel_name in self.channel_data:
                            self.channel_data[channel_name].extend(samples)
            elif kind == "error":
                self.last_error = message
            elif kind == "stopped":
                break

    @property
    def is_collecting(self):
        return self._worker_thread is not None and self._worker_thread.is_alive()

    @property
    def is_open(self):
        return self._device is not None

    def configure_channel(self, channel_name, *, enabled=None, coupling=None, voltage_range=None):
        normalized_name = str(channel_name).upper()
        if normalized_name not in self.channels:
            raise ValueError(f"Unsupported 2204A channel '{channel_name}'. Expected one of: {', '.join(CHANNEL_NAMES)}")
        if self.is_collecting:
            raise RuntimeError("Stop collection before reconfiguring channels.")

        channel_config = self.channels[normalized_name]
        if enabled is not None:
            channel_config["enabled"] = bool(enabled)
        if coupling is not None:
            coupling_name = str(coupling).upper()
            if coupling_name not in self.supported_couplings:
                raise ValueError(f"Unsupported coupling '{coupling}'. Expected one of: {', '.join(self.supported_couplings)}")
            channel_config["coupling"] = coupling_name
        if voltage_range is not None:
            range_name = _normalize_voltage_range(voltage_range)
            if range_name not in self.supported_voltage_ranges:
                raise ValueError(f"Unsupported range '{voltage_range}' for 2204A hardware.")
            channel_config["range"] = range_name

    def set_sample_capture_rate(self, sample_rate_hz):
        if self.is_collecting:
            raise RuntimeError("Stop collection before changing the sample capture rate.")
        sample_rate_hz = float(sample_rate_hz)
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        self.sample_rate_hz = sample_rate_hz

    def set_history_seconds(self, history_seconds):
        if self.is_collecting:
            raise RuntimeError("Stop collection before changing the history duration.")
        history_seconds = float(history_seconds)
        if history_seconds <= 0:
            raise ValueError("history_seconds must be > 0")
        self.history_seconds = history_seconds
        self._reset_buffers()

    def set_max_samples(self, max_samples):
        # Backward-compatible alias: interpret the value as a sample-count history target.
        self.set_history_seconds(float(max_samples) / max(self.sample_rate_hz, 1e-12))

    def set_data_bits(self, data_bits):
        if self.is_collecting:
            raise RuntimeError("Stop collection before changing data_bits.")
        self.data_bits = _canonical_dtype_name(data_bits)

    def configure_awg(self, *, waveform_type=None, offset_volts=None, amplitude_vpp_volts=None, frequency_hz=None):
        if waveform_type is not None:
            self.awg_config["waveform_type"] = _normalize_awg_waveform_type(waveform_type)
        if offset_volts is not None:
            self.awg_config["offset_volts"] = float(offset_volts)
        if amplitude_vpp_volts is not None:
            amplitude_vpp_volts = float(amplitude_vpp_volts)
            if amplitude_vpp_volts < 0:
                raise ValueError("amplitude_vpp_volts must be >= 0")
            self.awg_config["amplitude_vpp_volts"] = amplitude_vpp_volts
        if frequency_hz is not None:
            frequency_hz = float(frequency_hz)
            if frequency_hz < 0:
                raise ValueError("frequency_hz must be >= 0")
            self.awg_config["frequency_hz"] = frequency_hz

        if self.is_open and self.awg_enabled:
            self._apply_awg_state()

    def set_awg_enabled(self, enabled):
        self.awg_enabled = bool(enabled)
        if self.is_open:
            self._apply_awg_state()

    def _apply_awg_state(self):
        if not self.is_open:
            return
        with self._device_api_lock:
            if self.awg_enabled:
                self._apply_awg_output()
            else:
                self._disable_awg_output()

    def _apply_awg_output(self):
        _apply_awg_output_to_device(self._device, self.awg_config)

    def _disable_awg_output(self):
        _disable_awg_output_on_device(self._device)

    def get_awg_preview(self, *, duration_seconds=None, sample_count=512):
        duration_seconds = float(self.history_seconds if duration_seconds is None else duration_seconds)
        duration_seconds = max(duration_seconds, 1e-3)
        sample_count = max(2, int(sample_count))
        timestamps = np.linspace(0.0, duration_seconds, sample_count, endpoint=False, dtype=np.float32)

        waveform_type = self.awg_config["waveform_type"]
        offset_volts = float(self.awg_config["offset_volts"])
        amplitude_volts = 0.5 * float(self.awg_config["amplitude_vpp_volts"])
        frequency_hz = float(self.awg_config["frequency_hz"])

        if waveform_type == "dc":
            voltages = np.full(sample_count, offset_volts, dtype=np.float32)
        else:
            phase = (2.0 * np.pi * frequency_hz * timestamps).astype(np.float32, copy=False)
            if waveform_type == "sine":
                shape = np.sin(phase).astype(np.float32, copy=False)
            elif waveform_type == "square":
                shape = np.where(np.sin(phase) >= 0.0, 1.0, -1.0).astype(np.float32)
            else:
                fractional = np.mod(frequency_hz * timestamps, 1.0).astype(np.float32, copy=False)
                shape = (2.0 * np.abs((2.0 * fractional) - 1.0) - 1.0).astype(np.float32, copy=False)
            voltages = (offset_volts + (amplitude_volts * shape)).astype(np.float32, copy=False)

        return {
            "timestamps": timestamps.tolist(),
            "voltages": voltages.tolist(),
        }

    def set_serial_number(self, serial_number):
        if self.is_open:
            raise RuntimeError("Stop collection before changing the device selection.")
        self.serial_number = str(serial_number or "").strip()

    def open_device(self):
        if self.is_open:
            return
        self.last_error = None
        self.actual_sample_rate_hz = None
        self.active_scope_series = MODEL_NAME
        with self._device_api_lock:
            self._device = ps2000.open_unit()
            _validate_2204a_device(self._device)
        self._apply_awg_state()

    def _start_collection_internal(self, *, reset_buffers):
        if self.is_collecting:
            return
        if not self.is_open:
            raise RuntimeError("Open the 2204A before starting capture.")

        if not any(config["enabled"] for config in self.channels.values()):
            raise RuntimeError("At least one 2204A channel must be enabled before starting collection.")

        self.last_error = None
        if reset_buffers:
            self.actual_sample_rate_hz = None
            self.active_scope_series = MODEL_NAME
            self._reset_buffers()
        self._listener_stop_event.clear()

        self._output_queue = queue.Queue(maxsize=16)
        self._control_queue = queue.Queue(maxsize=16)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=_picoscope_worker,
            name="PicoScope2204AWorker",
            daemon=True,
            args=(self._device, self._build_worker_config(), self._output_queue, self._stop_event, self._control_queue, self._device_api_lock),
        )
        self._worker_thread.start()

        self._listener_thread = threading.Thread(target=self._listener_loop, name="PicoScope2204AListener", daemon=True)
        self._listener_thread.start()

    def _stop_collection_internal(self):
        if self._stop_event is not None:
            self._stop_event.set()

        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)

        if self._listener_thread is not None and self._listener_thread.is_alive():
            self._listener_stop_event.set()
            self._listener_thread.join(timeout=2.0)
        if self.is_open:
            self._apply_awg_state()

        self._worker_thread = None
        self._stop_event = None
        self._output_queue = None
        self._control_queue = None
        self._listener_thread = None
        self._listener_stop_event.clear()

    def start_collection(self):
        self._start_collection_internal(reset_buffers=True)

    def pause_collection(self):
        self._stop_collection_internal()

    def resume_collection(self):
        self._start_collection_internal(reset_buffers=False)

    def stop_collection(self):
        self._stop_collection_internal()

    def close_device(self):
        self.stop_collection()
        if self._device is not None:
            try:
                self._disable_awg_output()
            except Exception:
                pass
            try:
                with self._device_api_lock:
                    self._device.close()
            except Exception:
                pass
        self._device = None
        self.actual_sample_rate_hz = None

    def get_channel_samples(self, channel_name):
        normalized_name = str(channel_name).upper()
        if normalized_name not in self.channel_data:
            raise ValueError(f"Unsupported channel '{channel_name}'.")
        with self.data_lock:
            return list(self.channel_data[normalized_name])

    def get_timestamps(self):
        with self.data_lock:
            return list(self.timestamps)

    def clear_buffers(self):
        self._reset_buffers()

    def get_snapshot(self):
        with self.data_lock:
            return {
                "timestamps": list(self.timestamps),
                "channels": {channel_name: list(samples) for channel_name, samples in self.channel_data.items()},
                "actual_sample_rate_hz": self.actual_sample_rate_hz,
                "data_bits": self.data_bits,
                "device_model": self.device_model,
                "active_scope_series": self.active_scope_series,
                "history_seconds": self.history_seconds,
                "buffer_capacity": self.buffer_capacity,
            }

    def close(self):
        self.close_device()
