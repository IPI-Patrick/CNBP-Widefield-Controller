import ctypes
from collections import deque
import importlib
from pathlib import Path
import queue
import sys
import threading
import time
import traceback

import numpy as np


MODEL_NAME = "PS2000"
MAX_SAMPLE_RATE_HZ = 100_000_000.0
CHANNEL_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H")
AVAILABLE_CHANNEL_NAMES = ("A", "B")
CHANNEL_ENUM = {"A": 0, "B": 1}
DEFAULT_HISTORY_SECONDS = 1.0
CAPTURE_BLOCK_DURATION_SECONDS = 0.1
MAX_ADC_VALUE = 32767.0
SUPPORTED_AWG_WAVEFORMS = ("dc", "sine", "square", "triangle")
SCOPE_STORAGE_DTYPE = np.dtype(np.float16)
SCOPE_STORAGE_DTYPE_NAME = SCOPE_STORAGE_DTYPE.name
SUPPORTED_COUPLINGS = ("AC", "DC")


def _ensure_picosdk_import_path():
    api_root = Path(__file__).resolve().parents[1] / "APIs" / "picosdk"
    api_root_str = str(api_root)
    if api_root.exists() and api_root_str not in sys.path:
        sys.path.insert(0, api_root_str)


_ensure_picosdk_import_path()

pico_functions = importlib.import_module("picosdk.functions")
assert_pico2000_ok = pico_functions.assert_pico2000_ok
ps2000 = importlib.import_module("picosdk.ps2000").ps2000


PS2000_VOLTAGE_RANGES = {
    "PICO_X1_PROBE_20MV": ps2000.PS2000_VOLTAGE_RANGE["PS2000_20MV"],
    "PICO_X1_PROBE_50MV": ps2000.PS2000_VOLTAGE_RANGE["PS2000_50MV"],
    "PICO_X1_PROBE_100MV": ps2000.PS2000_VOLTAGE_RANGE["PS2000_100MV"],
    "PICO_X1_PROBE_200MV": ps2000.PS2000_VOLTAGE_RANGE["PS2000_200MV"],
    "PICO_X1_PROBE_500MV": ps2000.PS2000_VOLTAGE_RANGE["PS2000_500MV"],
    "PICO_X1_PROBE_1V": ps2000.PS2000_VOLTAGE_RANGE["PS2000_1V"],
    "PICO_X1_PROBE_2V": ps2000.PS2000_VOLTAGE_RANGE["PS2000_2V"],
    "PICO_X1_PROBE_5V": ps2000.PS2000_VOLTAGE_RANGE["PS2000_5V"],
    "PICO_X1_PROBE_10V": ps2000.PS2000_VOLTAGE_RANGE["PS2000_10V"],
    "PICO_X1_PROBE_20V": ps2000.PS2000_VOLTAGE_RANGE["PS2000_20V"],
}
SUPPORTED_VOLTAGE_RANGES = tuple(PS2000_VOLTAGE_RANGES.keys())
PS2000_RANGE_MILLIVOLTS = {
    name: ps2000.PICO_VOLTAGE_RANGE[enum_val] * 1000.0
    for name, enum_val in PS2000_VOLTAGE_RANGES.items()
}
PS2000_TIME_UNIT_TO_SECONDS = {
    ps2000.PS2000_TIME_UNITS["PS2000_FS"]: 1e-15,
    ps2000.PS2000_TIME_UNITS["PS2000_PS"]: 1e-12,
    ps2000.PS2000_TIME_UNITS["PS2000_NS"]: 1e-9,
    ps2000.PS2000_TIME_UNITS["PS2000_US"]: 1e-6,
    ps2000.PS2000_TIME_UNITS["PS2000_MS"]: 1e-3,
    ps2000.PS2000_TIME_UNITS["PS2000_S"]: 1.0,
}


def _normalize_voltage_range(range_name):
    range_text = str(range_name).upper().strip()
    if range_text in PS2000_VOLTAGE_RANGES:
        return range_text
    for prefix in ("PICO_X1_PROBE_", "PS4000A_", "PS2000A_", "PS2000_"):
        if range_text.startswith(prefix):
            voltage_part = range_text[len(prefix):]
            candidate = f"PICO_X1_PROBE_{voltage_part}"
            if candidate in PS2000_VOLTAGE_RANGES:
                return candidate
    candidate = f"PICO_X1_PROBE_{range_text}"
    if candidate in PS2000_VOLTAGE_RANGES:
        return candidate
    raise ValueError(f"Unrecognized voltage range '{range_name}'.")


def _convert_samples(raw_samples, _dtype_name):
    return np.asarray(raw_samples, dtype=SCOPE_STORAGE_DTYPE)


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


_AWG_WAVEFORM_MAP = {
    "dc": ps2000.PS2000_WAVE_TYPE["PS2000_DC_VOLTAGE"],
    "sine": ps2000.PS2000_WAVE_TYPE["PS2000_SINE"],
    "square": ps2000.PS2000_WAVE_TYPE["PS2000_SQUARE"],
    "triangle": ps2000.PS2000_WAVE_TYPE["PS2000_TRIANGLE"],
}


def _status_name(status_code):
    try:
        status_lookup = importlib.import_module("picosdk.constants").PICO_STATUS_LOOKUP
        return status_lookup.get(status_code, str(int(status_code)))
    except Exception:
        return str(int(status_code))


def _assert_ps2000_call_ok(status, action_name):
    try:
        assert_pico2000_ok(status)
    except Exception as exc:
        msg = f"{action_name} failed in ps2000 API with status {status} ({_status_name(status)})."
        print(f"[PicoScope2000] {msg}", file=sys.stderr)
        raise RuntimeError(msg) from exc


def _assert_ps2000_sig_gen_call_ok(status, action_name):
    if int(status) < 0:
        msg = f"{action_name} failed in ps2000 API with status {status}."
        print(f"[PicoScope2000] {msg}", file=sys.stderr)
        raise RuntimeError(msg)


def _assert_ps2000_streaming_poll_ok(status, action_name):
    # Legacy ps2000 polling uses 0 when no new overview buffer is ready yet.
    if int(status) < 0:
        msg = f"{action_name} failed in ps2000 API with status {status}."
        print(f"[PicoScope2000] {msg}", file=sys.stderr)
        raise RuntimeError(msg)


def _assert_ps2000_streaming_start_ok(status, action_name):
    # Legacy ps2000 streaming start can also report success as 0.
    if int(status) < 0:
        msg = f"{action_name} failed in ps2000 API with status {status}."
        print(f"[PicoScope2000] {msg}", file=sys.stderr)
        raise RuntimeError(msg)


def _get_unit_info_text(handle, info_key):
    info_buffer = ctypes.create_string_buffer(255)
    info_code = ps2000.PICO_INFO[info_key]
    info_length = ps2000.ps2000_get_unit_info(
        ctypes.c_int16(handle),
        info_buffer,
        ctypes.c_int16(len(info_buffer)),
        ctypes.c_int16(info_code),
    )
    if int(info_length) <= 0:
        return ""
    return info_buffer.value.decode("utf-8", errors="ignore").strip()


def _describe_device(handle):
    variant = _get_unit_info_text(handle, "PICO_VARIANT_INFO") or MODEL_NAME
    serial = _get_unit_info_text(handle, "PICO_BATCH_AND_SERIAL")
    return variant, serial


def _list_available_devices():
    devices = []
    handles_to_close = []
    seen = set()
    try:
        while True:
            handle = int(ps2000.ps2000_open_unit())
            if handle <= 0:
                break
            handles_to_close.append(handle)

            variant, serial = _describe_device(handle)
            identity = (variant, serial)
            if identity in seen:
                continue
            seen.add(identity)

            serial_tail = serial[-8:] if serial else variant
            label_suffix = f" | {serial}" if serial else ""
            devices.append(
                {
                    "model": variant,
                    "serial": serial,
                    "has_verified_serial": bool(serial),
                    "variant": variant,
                    "instance_id": serial or variant,
                    "instance_tail": serial_tail,
                    "label": f"{variant}{label_suffix}",
                }
            )
    finally:
        for handle in handles_to_close:
            try:
                ps2000.ps2000_close_unit(ctypes.c_int16(handle))
            except Exception:
                pass

    return devices


def _open_matching_device(serial_number):
    requested_serial = str(serial_number or "").strip()
    handles_to_close = []
    try:
        while True:
            handle = int(ps2000.ps2000_open_unit())
            if handle <= 0:
                break

            variant, serial = _describe_device(handle)
            if not requested_serial or serial == requested_serial:
                for other_handle in handles_to_close:
                    try:
                        ps2000.ps2000_close_unit(ctypes.c_int16(other_handle))
                    except Exception:
                        pass
                return handle, variant, serial

            handles_to_close.append(handle)
    finally:
        if requested_serial:
            for handle in handles_to_close:
                try:
                    ps2000.ps2000_close_unit(ctypes.c_int16(handle))
                except Exception:
                    pass

    if requested_serial:
        raise RuntimeError(f"No PS2000 device found matching serial '{requested_serial}'.")
    raise RuntimeError("Failed to open PS2000 device. No device found.")


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
    best_unit_seconds = None
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
            best_unit_seconds = unit_seconds
            best_error = error

    if best_interval is None or best_units is None:
        raise RuntimeError("Could not determine a valid streaming interval for the requested sample rate.")

    return best_interval, best_units, best_unit_seconds


def _compute_buffer_capacity(sample_rate_hz, history_seconds):
    return max(1, int(round(float(sample_rate_hz) * float(history_seconds))))


def _compute_capture_block_samples(sample_rate_hz):
    return max(32, int(round(float(sample_rate_hz) * CAPTURE_BLOCK_DURATION_SECONDS)))


def _sample_at_index(channel_payload, sample_index):
    return {
        channel_name: samples[sample_index]
        for channel_name, samples in channel_payload.items()
        if sample_index < len(samples)
    }


def _normalize_awg_waveform_type(waveform_type):
    waveform_text = str(waveform_type).strip().lower()
    if waveform_text not in SUPPORTED_AWG_WAVEFORMS:
        raise ValueError(f"Unsupported AWG waveform '{waveform_type}'. Expected one of: {', '.join(SUPPORTED_AWG_WAVEFORMS)}")
    return waveform_text


def _apply_awg_output_to_device(handle, awg_config):
    waveform_type = str(awg_config.get("waveform_type", "dc")).lower()
    wave_enum = _AWG_WAVEFORM_MAP.get(waveform_type)
    if wave_enum is None:
        raise ValueError(f"Unsupported AWG waveform type: {waveform_type}")

    offset_uv = int(float(awg_config.get("offset_volts", 0.0)) * 1_000_000)
    pk_to_pk_uv = int(float(awg_config.get("amplitude_vpp_volts", 1.0)) * 1_000_000)
    frequency_hz = float(awg_config.get("frequency_hz", 1000.0))

    status = ps2000.ps2000_set_sig_gen_built_in(
        ctypes.c_int16(handle),
        ctypes.c_int32(offset_uv),
        ctypes.c_uint32(pk_to_pk_uv),
        ctypes.c_int32(wave_enum),
        ctypes.c_float(frequency_hz),
        ctypes.c_float(frequency_hz),
        ctypes.c_float(0.0),
        ctypes.c_float(0.0),
        ctypes.c_int32(ps2000.PS2000_SWEEP_TYPE["PS2000_UP"]),
        ctypes.c_uint32(0),
    )
    _assert_ps2000_sig_gen_call_ok(status, "ps2000_set_sig_gen_built_in")


def _disable_awg_output_on_device(handle):
    status = ps2000.ps2000_set_sig_gen_built_in(
        ctypes.c_int16(handle),
        ctypes.c_int32(0),
        ctypes.c_uint32(0),
        ctypes.c_int32(ps2000.PS2000_WAVE_TYPE["PS2000_DC_VOLTAGE"]),
        ctypes.c_float(0.0),
        ctypes.c_float(0.0),
        ctypes.c_float(0.0),
        ctypes.c_float(0.0),
        ctypes.c_int32(ps2000.PS2000_SWEEP_TYPE["PS2000_UP"]),
        ctypes.c_uint32(0),
    )
    _assert_ps2000_sig_gen_call_ok(status, "ps2000_set_sig_gen_built_in (disable)")


def _run_ps2000_capture(handle, config, output_queue, stop_event, control_queue, api_lock, device_variant):
    enabled_channels = [name for name in AVAILABLE_CHANNEL_NAMES if config["channels"][name]["enabled"]]
    if not enabled_channels:
        raise RuntimeError("At least one PS2000 channel must be enabled before starting collection.")

    overview_buffer_size = 50000
    max_samples = 100000

    try:
        with api_lock:
            for channel_name in AVAILABLE_CHANNEL_NAMES:
                channel_config = config["channels"][channel_name]
                range_name = _normalize_voltage_range(channel_config["range"])
                status = ps2000.ps2000_set_channel(
                    ctypes.c_int16(handle),
                    ctypes.c_int16(CHANNEL_ENUM[channel_name]),
                    ctypes.c_int16(1 if channel_config["enabled"] else 0),
                    ctypes.c_int16(ps2000.PICO_COUPLING[channel_config["coupling"]]),
                    ctypes.c_int16(PS2000_VOLTAGE_RANGES[range_name]),
                )
                _assert_ps2000_call_ok(status, f"ps2000_set_channel({channel_name})")

            interval_value, interval_units, unit_seconds = _select_streaming_interval(config["sample_rate_hz"])
            status = ps2000.ps2000_run_streaming_ns(
                ctypes.c_int16(handle),
                ctypes.c_uint32(interval_value),
                ctypes.c_int32(interval_units),
                ctypes.c_uint32(max_samples),
                ctypes.c_int16(0),
                ctypes.c_uint32(1),
                ctypes.c_uint32(overview_buffer_size),
            )
            _assert_ps2000_streaming_start_ok(status, "ps2000_run_streaming_ns")

        interval_seconds = interval_value * unit_seconds
        actual_rate_hz = 1.0 / max(interval_seconds, 1e-12)
        output_queue.put(
            {
                "kind": "meta",
                "actual_sample_rate_hz": actual_rate_hz,
                "enabled_channels": list(enabled_channels),
                "active_scope_series": device_variant or MODEL_NAME,
                "streaming_mode": "ps2000",
            }
        )

        next_sample_time = float(config.get("time_offset_seconds", 0.0))

        def streaming_callback(buffers, _overflow, _trigger_at, _triggered, _auto_stop, n_values):
            nonlocal next_sample_time
            sample_count = int(n_values)
            if sample_count <= 0:
                return

            timestamps = next_sample_time + (np.arange(sample_count, dtype=np.float64) * interval_seconds)
            next_sample_time = float(timestamps[-1] + interval_seconds)

            channel_arrays = {}
            buffer_index_map = {
                "A": 0,
                "B": 2,
            }
            for channel_name in enabled_channels:
                buffer_index = buffer_index_map[channel_name]
                source_buffer = buffers[buffer_index]
                channel_arrays[channel_name] = np.array(source_buffer[0:sample_count], dtype=np.int16, copy=True)

            _emit_payload(output_queue, timestamps, channel_arrays, config["data_bits"])

        callback = ps2000.GetOverviewBuffersType(streaming_callback)

        while not stop_event.is_set():
            if control_queue is not None:
                while True:
                    try:
                        control_queue.get_nowait()
                    except queue.Empty:
                        break

            with api_lock:
                status = ps2000.ps2000_get_streaming_last_values(ctypes.c_int16(handle), callback)
            _assert_ps2000_streaming_poll_ok(status, "ps2000_get_streaming_last_values")
            time.sleep(0.01)
    finally:
        try:
            with api_lock:
                ps2000.ps2000_stop(ctypes.c_int16(handle))
        except Exception:
            pass


def _picoscope_worker(handle, config, output_queue, stop_event, control_queue, api_lock, device_variant):
    try:
        _run_ps2000_capture(handle, config, output_queue, stop_event, control_queue, api_lock, device_variant)
    except Exception as exc:
        print(f"PicoScope2000 worker error: {type(exc).__name__}: {exc}", file=sys.stderr)
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


class PicoScope2000:

    def __init__(self, sample_rate_hz=1000.0, history_seconds=DEFAULT_HISTORY_SECONDS, data_bits="float16"):
        _ = data_bits
        self.sample_rate_hz = float(sample_rate_hz)
        self.history_seconds = float(history_seconds)
        self.data_bits = SCOPE_STORAGE_DTYPE_NAME
        self.device_model = MODEL_NAME
        self.active_scope_series = MODEL_NAME
        self.serial_number = ""

        self.channels = {
            channel_name: {
                "enabled": channel_name == "A",
                "coupling": "DC",
                "range": "PICO_X1_PROBE_2V",
            }
            for channel_name in CHANNEL_NAMES
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
        self.paired_camera_timestamps = deque(maxlen=self.buffer_capacity)
        self.actual_sample_rate_hz = None
        self.last_error = None
        self.frame_pairing_enabled = False
        self.total_samples_received = 0
        self._pending_camera_timestamps = deque()
        self._last_scope_sample = None

        self._output_queue = None
        self._stop_event = None
        self._worker_thread = None
        self._listener_thread = None
        self._listener_stop_event = threading.Event()
        self._control_queue = None
        self._handle = None
        self._device_variant = MODEL_NAME
        self._device_api_lock = threading.RLock()

    @property
    def available_channels(self):
        return AVAILABLE_CHANNEL_NAMES

    @property
    def supported_couplings(self):
        return SUPPORTED_COUPLINGS

    @property
    def supported_voltage_ranges(self):
        return SUPPORTED_VOLTAGE_RANGES

    @property
    def supported_awg_waveforms(self):
        return SUPPORTED_AWG_WAVEFORMS

    def get_max_sample_rate_hz(self):
        return float(MAX_SAMPLE_RATE_HZ)

    def get_state(self) -> dict:
        return {
            "connected": self.is_open,
            "collecting": self.is_collecting,
            "actual_sample_rate_hz": self.actual_sample_rate_hz,
            "active_scope_series": self.active_scope_series,
            "last_error": self.last_error,
            "frame_pairing_enabled": self.frame_pairing_enabled,
            "awg_enabled": self.awg_enabled,
        }

    def get_settings(self) -> dict:
        return {
            "sample_rate_hz": self.sample_rate_hz,
            "history_seconds": self.history_seconds,
            "data_bits": self.data_bits,
            "channel_configs": {name: dict(cfg) for name, cfg in self.channels.items()},
            "awg_config": dict(self.awg_config),
        }

    def set_settings(self, **kwargs):
        if self.is_collecting:
            raise RuntimeError("Stop collection before changing settings.")
        if "sample_rate_hz" in kwargs:
            hz = float(kwargs["sample_rate_hz"])
            if hz <= 0:
                raise ValueError("sample_rate_hz must be > 0")
            self.sample_rate_hz = hz
        if "history_seconds" in kwargs:
            secs = float(kwargs["history_seconds"])
            if secs <= 0:
                raise ValueError("history_seconds must be > 0")
            self.history_seconds = secs
            self._reset_buffers()

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
        try:
            return _list_available_devices()
        except Exception as exc:
            print(f"[PicoScope2000] Device enumeration failed: {exc}", file=sys.stderr)
            raise

    def _reset_buffers(self):
        if self.frame_pairing_enabled:
            capacity = self.buffer_capacity
        else:
            capacity = _compute_buffer_capacity(self.actual_sample_rate_hz or self.sample_rate_hz, self.history_seconds)
            self.buffer_capacity = capacity
        with self.data_lock:
            self.channel_data = {channel_name: deque(maxlen=capacity) for channel_name in CHANNEL_NAMES}
            self.timestamps = deque(maxlen=capacity)
            self.paired_camera_timestamps = deque(maxlen=capacity)
            self.total_samples_received = 0
            self._pending_camera_timestamps = deque()
            self._last_scope_sample = None

    def _resize_history_buffers(self, sample_rate_hz):
        if self.frame_pairing_enabled:
            new_capacity = self.buffer_capacity
        else:
            new_capacity = _compute_buffer_capacity(sample_rate_hz, self.history_seconds)
        if new_capacity == self.buffer_capacity:
            return

        self.buffer_capacity = new_capacity
        with self.data_lock:
            self.timestamps = deque(self.timestamps, maxlen=new_capacity)
            self.paired_camera_timestamps = deque(self.paired_camera_timestamps, maxlen=new_capacity)
            self.channel_data = {
                channel_name: deque(samples, maxlen=new_capacity)
                for channel_name, samples in self.channel_data.items()
            }

    def configure_frame_pairing(self, *, enabled, frame_buffer_size=None):
        if self.is_collecting:
            raise RuntimeError("Stop collection before changing scope frame pairing.")

        with self.data_lock:
            self.frame_pairing_enabled = bool(enabled)
            if self.frame_pairing_enabled:
                if frame_buffer_size is None:
                    raise ValueError("frame_buffer_size is required when enabling scope frame pairing.")
                self.buffer_capacity = max(1, int(frame_buffer_size))
            else:
                self.buffer_capacity = _compute_buffer_capacity(self.actual_sample_rate_hz or self.sample_rate_hz, self.history_seconds)

            self.channel_data = {
                channel_name: deque(self.channel_data.get(channel_name, ()), maxlen=self.buffer_capacity)
                for channel_name in CHANNEL_NAMES
            }
            self.timestamps = deque(self.timestamps, maxlen=self.buffer_capacity)
            self.paired_camera_timestamps = deque(self.paired_camera_timestamps, maxlen=self.buffer_capacity)
            self._pending_camera_timestamps = deque()
            self._last_scope_sample = None

    def register_camera_frame_timestamps(self, timestamps):
        if not timestamps:
            return

        with self.data_lock:
            if not self.frame_pairing_enabled:
                return

            for timestamp in timestamps:
                self._pending_camera_timestamps.append(float(timestamp))

    def _append_paired_sample_locked(self, camera_timestamp, scope_timestamp, sample_values):
        self.paired_camera_timestamps.append(float(camera_timestamp))
        self.timestamps.append(float(scope_timestamp))
        for channel_name in CHANNEL_NAMES:
            if channel_name in sample_values:
                self.channel_data[channel_name].append(sample_values[channel_name])

    def _pair_pending_frames_with_last_sample_locked(self):
        if self._last_scope_sample is None:
            return

        scope_timestamp, sample_values = self._last_scope_sample
        while self._pending_camera_timestamps:
            camera_timestamp = self._pending_camera_timestamps.popleft()
            self._append_paired_sample_locked(camera_timestamp, scope_timestamp, sample_values)

    def _handle_frame_pairing_payload_locked(self, timestamps, channel_payload):
        for sample_index, scope_timestamp in enumerate(timestamps):
            current_sample_values = _sample_at_index(channel_payload, sample_index)

            if self._last_scope_sample is None:
                self._last_scope_sample = (float(scope_timestamp), current_sample_values)
                while self._pending_camera_timestamps and self._pending_camera_timestamps[0] <= scope_timestamp:
                    camera_timestamp = self._pending_camera_timestamps.popleft()
                    self._append_paired_sample_locked(camera_timestamp, scope_timestamp, current_sample_values)
                continue

            previous_scope_timestamp, previous_sample_values = self._last_scope_sample
            while self._pending_camera_timestamps and self._pending_camera_timestamps[0] <= scope_timestamp:
                camera_timestamp = self._pending_camera_timestamps.popleft()
                if abs(previous_scope_timestamp - camera_timestamp) <= abs(float(scope_timestamp) - camera_timestamp):
                    self._append_paired_sample_locked(camera_timestamp, previous_scope_timestamp, previous_sample_values)
                else:
                    self._append_paired_sample_locked(camera_timestamp, scope_timestamp, current_sample_values)

            self._last_scope_sample = (float(scope_timestamp), current_sample_values)

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
                    timestamps = [float(timestamp) for timestamp in message.get("timestamps", [])]
                    channel_payload = message.get("channels", {})
                    if self.frame_pairing_enabled:
                        self._handle_frame_pairing_payload_locked(timestamps, channel_payload)
                    else:
                        self.timestamps.extend(timestamps)
                        self.total_samples_received += len(timestamps)
                        for channel_name, samples in channel_payload.items():
                            if channel_name in self.channel_data:
                                self.channel_data[channel_name].extend(samples)
            elif kind == "error":
                self.last_error = message
            elif kind == "stopped":
                with self.data_lock:
                    if self.frame_pairing_enabled:
                        self._pair_pending_frames_with_last_sample_locked()
                break

    @property
    def is_collecting(self):
        return self._worker_thread is not None and self._worker_thread.is_alive()

    @property
    def is_open(self):
        return self._handle is not None

    def configure_channel(self, channel_name, *, enabled=None, coupling=None, voltage_range=None):
        normalized_name = str(channel_name).upper()
        if normalized_name not in self.channels:
            raise ValueError(f"Unsupported PS2000 channel '{channel_name}'. Expected one of: {', '.join(CHANNEL_NAMES)}")
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
                raise ValueError(f"Unsupported range '{voltage_range}' for PS2000 hardware.")
            channel_config["range"] = range_name

    def configure_awg(self, *, waveform_type=None, offset_volts=None, amplitude_vpp_volts=None, frequency_hz=None, enabled=None):
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
        if enabled is not None:
            self.awg_enabled = bool(enabled)
        if self.is_open and (self.awg_enabled or enabled is not None):
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
        _apply_awg_output_to_device(self._handle, self.awg_config)

    def _disable_awg_output(self):
        _disable_awg_output_on_device(self._handle)

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

    def open_device(self):
        if self.is_open:
            return
        self.last_error = None
        self.actual_sample_rate_hz = None
        self.active_scope_series = MODEL_NAME
        with self._device_api_lock:
            handle, variant, serial = _open_matching_device(self.serial_number)
            self._handle = handle
            self._device_variant = variant or MODEL_NAME
            if serial:
                self.serial_number = serial
        self._apply_awg_state()

    def _start_collection_internal(self, *, reset_buffers):
        if self.is_collecting:
            return
        if not self.is_open:
            raise RuntimeError("Open the PS2000 before starting capture.")

        if not any(self.channels[channel_name]["enabled"] for channel_name in AVAILABLE_CHANNEL_NAMES):
            raise RuntimeError("At least one PS2000 channel must be enabled before starting collection.")

        self.last_error = None
        if reset_buffers:
            self.actual_sample_rate_hz = None
            self.active_scope_series = self._device_variant or MODEL_NAME
            self._reset_buffers()
        self._listener_stop_event.clear()

        self._output_queue = queue.Queue(maxsize=16)
        self._control_queue = queue.Queue(maxsize=16)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=_picoscope_worker,
            name="PicoScope2000Worker",
            daemon=True,
            args=(
                self._handle,
                self._build_worker_config(),
                self._output_queue,
                self._stop_event,
                self._control_queue,
                self._device_api_lock,
                self._device_variant,
            ),
        )
        self._worker_thread.start()

        self._listener_thread = threading.Thread(target=self._listener_loop, name="PicoScope2000Listener", daemon=True)
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
        if self._handle is not None:
            try:
                self._disable_awg_output()
            except Exception:
                pass
            try:
                with self._device_api_lock:
                    ps2000.ps2000_close_unit(ctypes.c_int16(self._handle))
            except Exception:
                pass
        self._handle = None
        self.actual_sample_rate_hz = None
        self.active_scope_series = MODEL_NAME

    def get_buffer_snapshot(self, channel_names=None):
        with self.data_lock:
            if channel_names is None:
                selected_channels = tuple(self.channel_data.keys())
            else:
                selected_channels = tuple(
                    channel_name
                    for channel_name in channel_names
                    if channel_name in self.channel_data
                )

            return {
                "timestamps": np.asarray(self.timestamps, dtype=np.float64),
                "channels": {
                    channel_name: np.asarray(self.channel_data[channel_name], dtype=SCOPE_STORAGE_DTYPE)
                    for channel_name in selected_channels
                },
                "actual_sample_rate_hz": self.actual_sample_rate_hz,
                "history_seconds": self.history_seconds,
                "total_samples_received": int(self.total_samples_received),
            }

    def get_snapshot(self):
        with self.data_lock:
            return {
                "timestamps": list(self.timestamps),
                "paired_camera_timestamps": list(self.paired_camera_timestamps),
                "unpaired_camera_timestamps": list(self._pending_camera_timestamps),
                "channels": {channel_name: list(samples) for channel_name, samples in self.channel_data.items()},
                "actual_sample_rate_hz": self.actual_sample_rate_hz,
                "data_bits": self.data_bits,
                "device_model": self.device_model,
                "active_scope_series": self.active_scope_series,
                "history_seconds": self.history_seconds,
                "buffer_capacity": self.buffer_capacity,
                "frame_pairing_enabled": bool(self.frame_pairing_enabled),
            }