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


MODEL_NAME = "4824A"
MAX_SAMPLE_RATE_HZ = 80_000_000.0
CHANNEL_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H")
CHANNEL_ENUM = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5, "G": 6, "H": 7}
DEFAULT_HISTORY_SECONDS = 1.0
CAPTURE_BLOCK_DURATION_SECONDS = 0.1
MAX_ADC_VALUE = 32767.0
SUPPORTED_AWG_WAVEFORMS = ("dc", "sine", "square", "triangle")
SCOPE_STORAGE_DTYPE = np.dtype(np.float16)
SCOPE_STORAGE_DTYPE_NAME = SCOPE_STORAGE_DTYPE.name
SUPPORTED_COUPLINGS = ("AC", "DC")
PICO_STATUS_OK = 0x00000000
PICO_POWER_SUPPLY_NOT_CONNECTED = 0x0000011E
PICO_USB3_0_DEVICE_NON_USB3_0_PORT = 0x0000011A
PICO_VARIANT_INFO = 0x00000003


def _ensure_picosdk_import_path():
    api_root = Path(__file__).resolve().parents[1] / "APIs" / "picosdk"
    api_root_str = str(api_root)
    if api_root.exists() and api_root_str not in sys.path:
        sys.path.insert(0, api_root_str)


_ensure_picosdk_import_path()

pico_functions = importlib.import_module("picosdk.functions")
assert_pico_ok = pico_functions.assert_pico_ok
ps4000a = importlib.import_module("picosdk.ps4000a").ps4000a

# Build voltage range mappings from the SDK (PICO_X1_PROBE_* entries)
PS4000A_VOLTAGE_RANGES = {
    name: value
    for name, value in ps4000a.PICO_CONNECT_PROBE_RANGE.items()
    if isinstance(name, str) and name.startswith("PICO_X1_PROBE_") and name.endswith("V") and "RANGES" not in name
}
SUPPORTED_VOLTAGE_RANGES = tuple(PS4000A_VOLTAGE_RANGES.keys())
PS4000A_RANGE_MILLIVOLTS = {
    name: ps4000a.PICO_VOLTAGE_RANGE[enum_val] * 1000.0
    for name, enum_val in PS4000A_VOLTAGE_RANGES.items()
    if enum_val in ps4000a.PICO_VOLTAGE_RANGE
}
PS4000A_TIME_UNIT_TO_SECONDS = {
    ps4000a.PS4000A_TIME_UNITS["PS4000A_FS"]: 1e-15,
    ps4000a.PS4000A_TIME_UNITS["PS4000A_PS"]: 1e-12,
    ps4000a.PS4000A_TIME_UNITS["PS4000A_NS"]: 1e-9,
    ps4000a.PS4000A_TIME_UNITS["PS4000A_US"]: 1e-6,
    ps4000a.PS4000A_TIME_UNITS["PS4000A_MS"]: 1e-3,
    ps4000a.PS4000A_TIME_UNITS["PS4000A_S"]: 1.0,
}


def _normalize_voltage_range(range_name):
    """Convert various range name formats to PICO_X1_PROBE_* format."""
    range_text = str(range_name).upper().strip()
    if range_text in PS4000A_VOLTAGE_RANGES:
        return range_text
    for prefix in ("PICO_X1_PROBE_", "PS4000A_", "PS2000A_", "PS2000_"):
        if range_text.startswith(prefix):
            voltage_part = range_text[len(prefix):]
            candidate = f"PICO_X1_PROBE_{voltage_part}"
            if candidate in PS4000A_VOLTAGE_RANGES:
                return candidate
    candidate = f"PICO_X1_PROBE_{range_text}"
    if candidate in PS4000A_VOLTAGE_RANGES:
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
    "dc":       ps4000a.PS4000A_WAVE_TYPE["PS4000A_DC_VOLTAGE"],
    "sine":     ps4000a.PS4000A_WAVE_TYPE["PS4000A_SINE"],
    "square":   ps4000a.PS4000A_WAVE_TYPE["PS4000A_SQUARE"],
    "triangle": ps4000a.PS4000A_WAVE_TYPE["PS4000A_TRIANGLE"],
}


def _apply_awg_output_to_device(handle, awg_config):
    """Configure the 4824A built-in signal generator."""
    waveform_type = str(awg_config.get("waveform_type", "dc")).lower()
    wave_enum = _AWG_WAVEFORM_MAP.get(waveform_type)
    if wave_enum is None:
        raise ValueError(f"Unsupported AWG waveform type: {waveform_type}")

    offset_uv = int(float(awg_config.get("offset_volts", 0.0)) * 1_000_000)
    pk_to_pk_uv = int(float(awg_config.get("amplitude_vpp_volts", 1.0)) * 1_000_000)
    frequency_hz = float(awg_config.get("frequency_hz", 1000.0))

    status = ps4000a.ps4000aSetSigGenBuiltIn(
        ctypes.c_int16(handle),
        ctypes.c_int32(offset_uv),                                              # offsetVoltage (µV)
        ctypes.c_uint32(pk_to_pk_uv),                                           # pkToPk (µV)
        ctypes.c_int32(wave_enum),                                               # waveType
        ctypes.c_double(frequency_hz),                                           # startFrequency
        ctypes.c_double(frequency_hz),                                           # stopFrequency (no sweep)
        ctypes.c_double(0.0),                                                    # increment (no sweep)
        ctypes.c_double(0.0),                                                    # dwellTime (no sweep)
        ctypes.c_int32(ps4000a.PS4000A_SWEEP_TYPE["PS4000A_UP"]),                # sweepType
        ctypes.c_int32(ps4000a.PS4000A_EXTRA_OPERATIONS["PS4000A_ES_OFF"]),      # operation
        ctypes.c_uint32(0),                                                      # shots (0 = continuous)
        ctypes.c_uint32(0),                                                      # sweeps (0 = continuous)
        ctypes.c_int32(ps4000a.PS4000A_SIGGEN_TRIG_TYPE["PS4000A_SIGGEN_RISING"]),    # triggerType
        ctypes.c_int32(ps4000a.PS4000A_SIGGEN_TRIG_SOURCE["PS4000A_SIGGEN_NONE"]),    # triggerSource
        ctypes.c_int16(0),                                                       # extInThreshold
    )
    _assert_ps4000a_call_ok(status, "ps4000aSetSigGenBuiltIn")


def _disable_awg_output_on_device(handle):
    """Turn off the signal generator by setting 0V DC output."""
    status = ps4000a.ps4000aSetSigGenBuiltIn(
        ctypes.c_int16(handle),
        ctypes.c_int32(0),                                                       # offsetVoltage
        ctypes.c_uint32(0),                                                      # pkToPk
        ctypes.c_int32(ps4000a.PS4000A_WAVE_TYPE["PS4000A_DC_VOLTAGE"]),         # waveType
        ctypes.c_double(0.0),                                                    # startFrequency
        ctypes.c_double(0.0),                                                    # stopFrequency
        ctypes.c_double(0.0),                                                    # increment
        ctypes.c_double(0.0),                                                    # dwellTime
        ctypes.c_int32(ps4000a.PS4000A_SWEEP_TYPE["PS4000A_UP"]),                # sweepType
        ctypes.c_int32(ps4000a.PS4000A_EXTRA_OPERATIONS["PS4000A_ES_OFF"]),      # operation
        ctypes.c_uint32(0),                                                      # shots
        ctypes.c_uint32(0),                                                      # sweeps
        ctypes.c_int32(ps4000a.PS4000A_SIGGEN_TRIG_TYPE["PS4000A_SIGGEN_RISING"]),    # triggerType
        ctypes.c_int32(ps4000a.PS4000A_SIGGEN_TRIG_SOURCE["PS4000A_SIGGEN_NONE"]),    # triggerSource
        ctypes.c_int16(0),                                                       # extInThreshold
    )
    _assert_ps4000a_call_ok(status, "ps4000aSetSigGenBuiltIn (disable)")


def _assert_ps4000a_call_ok(status, action_name):
    try:
        assert_pico_ok(status)
    except Exception as exc:
        msg = f"{action_name} failed in ps4000a API with status {status} ({_status_name(status)})."
        print(f"[PicoScope] {msg}", file=sys.stderr)
        raise RuntimeError(msg) from exc


def _status_name(status_code):
    """Return the SDK constant name for a status code, or a hex string if unknown."""
    try:
        from picosdk.constants import PICO_STATUS_LOOKUP
        return PICO_STATUS_LOOKUP.get(status_code, f"0x{status_code:08X}")
    except Exception:
        return f"0x{status_code:08X}"


def _list_available_devices():
    """Enumerate connected ps4000a-series devices using the SDK."""
    count = ctypes.c_int16(0)
    serials = ctypes.create_string_buffer(4096)
    serial_lth = ctypes.c_int16(4096)
    try:
        status = ps4000a.ps4000aEnumerateUnits(
            ctypes.byref(count),
            serials,
            ctypes.byref(serial_lth),
        )
    except Exception as exc:
        msg = f"Failed to call ps4000aEnumerateUnits: {exc}"
        print(f"[PicoScope] {msg}", file=sys.stderr)
        raise RuntimeError(msg) from exc

    if status != PICO_STATUS_OK and count.value == 0:
        msg = (
            f"ps4000aEnumerateUnits returned {_status_name(status)}. "
            "If this is PICO_HARDWARE_VERSION_NOT_SUPPORTED or PICO_INTERNAL_ERROR, "
            "update PicoScope SDK / PicoScope software to the latest version."
        )
        print(f"[PicoScope] {msg}", file=sys.stderr)
        raise RuntimeError(msg)

    if count.value == 0:
        return []

    serial_string = serials.value.decode("utf-8", errors="ignore").strip()
    if not serial_string:
        return []

    serial_list = [s.strip() for s in serial_string.split(",") if s.strip()]
    devices = []
    for serial in serial_list:
        devices.append(
            {
                "model": MODEL_NAME,
                "serial": serial,
                "has_verified_serial": True,
                "variant": MODEL_NAME,
                "instance_id": serial,
                "instance_tail": serial,
                "label": f"{MODEL_NAME} | {serial}",
            }
        )
    return devices


def _select_streaming_interval(sample_rate_hz):
    """Pick the best ps4000a time unit and interval value for the desired sample rate."""
    interval_seconds = 1.0 / max(float(sample_rate_hz), 1e-12)
    unit_candidates = (
        ("PS4000A_NS", 1e-9),
        ("PS4000A_US", 1e-6),
        ("PS4000A_MS", 1e-3),
        ("PS4000A_S", 1.0),
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
            best_units = ps4000a.PS4000A_TIME_UNITS[unit_name]
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


def _validate_4824a_device(handle):
    info_buffer = ctypes.create_string_buffer(255)
    required_size = ctypes.c_int16(0)
    status = ps4000a.ps4000aGetUnitInfo(
        ctypes.c_int16(handle),
        info_buffer,
        ctypes.c_int16(255),
        ctypes.byref(required_size),
        ctypes.c_uint32(PICO_VARIANT_INFO),
    )
    if status != PICO_STATUS_OK:
        raise RuntimeError("Failed to query PicoScope variant info.")
    variant_text = info_buffer.value.decode("utf-8", errors="ignore").strip() or MODEL_NAME
    if "4824" not in variant_text.upper():
        raise RuntimeError(f"Detected PicoScope model '{variant_text}'. This driver requires a 4824A.")
    return variant_text


def _run_4824a_capture(handle, config, output_queue, stop_event, control_queue, api_lock):
    status = {}
    enabled_channels = [name for name in CHANNEL_NAMES if config["channels"][name]["enabled"]]
    if not enabled_channels:
        raise RuntimeError("At least one 4824A channel must be enabled before starting collection.")

    overview_buffer_size = 50000
    max_samples = 100000
    ratio_mode_none = ps4000a.PS4000A_RATIO_MODE["PS4000A_RATIO_MODE_NONE"]

    try:
        _validate_4824a_device(handle)

        channel_buffers = {}
        with api_lock:
            # Configure all channels (enable requested ones, disable the rest)
            for channel_name in CHANNEL_NAMES:
                channel_config = config["channels"][channel_name]
                range_name = _normalize_voltage_range(channel_config["range"])
                status[f"set_channel_{channel_name}"] = ps4000a.ps4000aSetChannel(
                    ctypes.c_int16(handle),
                    ctypes.c_int32(CHANNEL_ENUM[channel_name]),
                    ctypes.c_int16(1 if channel_config["enabled"] else 0),
                    ctypes.c_int32(ps4000a.PICO_COUPLING[channel_config["coupling"]]),
                    ctypes.c_int32(PS4000A_VOLTAGE_RANGES[range_name]),
                    ctypes.c_float(0.0),
                )
                _assert_ps4000a_call_ok(
                    status[f"set_channel_{channel_name}"],
                    f"ps4000aSetChannel({channel_name})",
                )

            # Register data buffers for each enabled channel
            for channel_name in enabled_channels:
                buf = np.zeros(overview_buffer_size, dtype=np.int16)
                channel_buffers[channel_name] = buf
                status[f"set_data_buffers_{channel_name}"] = ps4000a.ps4000aSetDataBuffers(
                    ctypes.c_int16(handle),
                    ctypes.c_int32(CHANNEL_ENUM[channel_name]),
                    buf.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
                    None,
                    ctypes.c_int32(overview_buffer_size),
                    ctypes.c_uint32(0),
                    ctypes.c_int32(ratio_mode_none),
                )
                _assert_ps4000a_call_ok(
                    status[f"set_data_buffers_{channel_name}"],
                    f"ps4000aSetDataBuffers({channel_name})",
                )

            # Calculate and start streaming
            interval_value, interval_units, unit_seconds = _select_streaming_interval(config["sample_rate_hz"])
            sample_interval = ctypes.c_uint32(interval_value)
            status["run_streaming"] = ps4000a.ps4000aRunStreaming(
                ctypes.c_int16(handle),
                ctypes.byref(sample_interval),
                ctypes.c_int32(interval_units),
                ctypes.c_uint32(0),
                ctypes.c_uint32(max_samples),
                ctypes.c_int16(0),
                ctypes.c_uint32(1),
                ctypes.c_int32(ratio_mode_none),
                ctypes.c_uint32(overview_buffer_size),
            )
            _assert_ps4000a_call_ok(status["run_streaming"], "ps4000aRunStreaming")

        actual_interval = sample_interval.value
        interval_seconds = actual_interval * unit_seconds # type: ignore
        actual_rate_hz = 1.0 / max(interval_seconds, 1e-12)

        output_queue.put(
            {
                "kind": "meta",
                "actual_sample_rate_hz": actual_rate_hz,
                "enabled_channels": list(enabled_channels),
                "active_scope_series": MODEL_NAME,
                "streaming_mode": "ps4000a",
            }
        )

        next_sample_time = float(config.get("time_offset_seconds", 0.0))

        def streaming_callback(cb_handle, noOfSamples, startIndex, overflow, triggerAt, triggered, autoStop, pParameter):
            nonlocal next_sample_time
            n = int(noOfSamples)
            if n <= 0:
                return
            start = int(startIndex)
            timestamps = next_sample_time + (np.arange(n, dtype=np.float64) * interval_seconds)
            next_sample_time = float(timestamps[-1] + interval_seconds)
            channel_arrays = {}
            for ch_name in enabled_channels:
                src = channel_buffers[ch_name]
                channel_arrays[ch_name] = np.array(src[start:start + n], dtype=np.int16, copy=True)
            _emit_payload(output_queue, timestamps, channel_arrays, config["data_bits"])

        callback = ps4000a.StreamingReadyType(streaming_callback)

        while not stop_event.is_set():
            if control_queue is not None:
                while True:
                    try:
                        control_queue.get_nowait()
                    except queue.Empty:
                        break
                    # AWG commands are silently ignored; the 4824A has no signal generator.

            with api_lock:
                ps4000a.ps4000aGetStreamingLatestValues(ctypes.c_int16(handle), callback, None)
            time.sleep(0.01)
    finally:
        try:
            with api_lock:
                ps4000a.ps4000aStop(ctypes.c_int16(handle))
        except Exception:
            pass


def _picoscope_worker(handle, config, output_queue, stop_event, control_queue, api_lock):
    try:
        _run_4824a_capture(handle, config, output_queue, stop_event, control_queue, api_lock)
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
        return PS4000A_RANGE_MILLIVOLTS[range_name] / 1000.0

    def convert_samples_to_volts(self, channel_name, samples):
        raw_values = np.asarray(samples, dtype=np.float32)
        return (raw_values * self.get_channel_input_range_volts(channel_name)) / MAX_ADC_VALUE

    def list_available_devices(self):
        try:
            return _list_available_devices()
        except Exception as exc:
            print(f"[PicoScope] Device enumeration failed: {exc}", file=sys.stderr)
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
            raise ValueError(f"Unsupported 4824A channel '{channel_name}'. Expected one of: {', '.join(CHANNEL_NAMES)}")
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
                raise ValueError(f"Unsupported range '{voltage_range}' for 4824A hardware.")
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
            chandle = ctypes.c_int16()
            serial = self.serial_number.encode("utf-8") if self.serial_number else None
            status = ps4000a.ps4000aOpenUnit(ctypes.byref(chandle), serial)
            if status in (PICO_POWER_SUPPLY_NOT_CONNECTED, PICO_USB3_0_DEVICE_NON_USB3_0_PORT):
                _assert_ps4000a_call_ok(
                    ps4000a.ps4000aChangePowerSource(chandle, status),
                    "ps4000aChangePowerSource",
                )
            elif status != PICO_STATUS_OK:
                _assert_ps4000a_call_ok(status, "ps4000aOpenUnit")
            self._handle = chandle.value
            if self._handle < 1:
                self._handle = None
                msg = "Failed to open PicoScope 4824A. No device found."
                print(f"[PicoScope] {msg}", file=sys.stderr)
                raise RuntimeError(msg)
            _validate_4824a_device(self._handle)
        self._apply_awg_state()

    def _start_collection_internal(self, *, reset_buffers):
        if self.is_collecting:
            return
        if not self.is_open:
            raise RuntimeError("Open the 4824A before starting capture.")

        if not any(config["enabled"] for config in self.channels.values()):
            raise RuntimeError("At least one 4824A channel must be enabled before starting collection.")

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
            name="PicoScope4824AWorker",
            daemon=True,
            args=(self._handle, self._build_worker_config(), self._output_queue, self._stop_event, self._control_queue, self._device_api_lock),
        )
        self._worker_thread.start()

        self._listener_thread = threading.Thread(target=self._listener_loop, name="PicoScope4824AListener", daemon=True)
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
                    ps4000a.ps4000aCloseUnit(ctypes.c_int16(self._handle))
            except Exception:
                pass
        self._handle = None
        self.actual_sample_rate_hz = None

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
