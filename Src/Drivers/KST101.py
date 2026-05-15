"""
Driver for the Thorlabs KST101 KCube Stepper Motor.
Uses the Thorlabs Kinesis C DLL via ctypes.
All units are millimetres (mm), mm/s, or mm/s².
"""

import os
import math
import time
import threading
from Utils.shared_state import dev_mode
from ctypes import (
    cdll, c_char_p, c_int, c_uint, c_long, c_double, c_short, c_bool, c_ulong,
    byref, create_string_buffer,
)
_KINESIS_PATH = r"C:\Program Files\Thorlabs\Kinesis"
_KST101_TYPE_ID = 26

# Polling interval passed to SCC_StartPolling.
# Must be > 333 ms (simulator drain rate) to prevent queue backlog.
# 250 ms keeps queue depth ≤ 1 in typical use while giving 4 Hz cache updates.
_POLLING_INTERVAL_MS = 400 if dev_mode else 200

# MOT_TravelDirection
_DIR_FORWARDS  = 0x01
_DIR_BACKWARDS = 0x02
# MOT_JogModes / MOT_StopModes
_JOG_CONTINUOUS = 0x01   # MOT_Continuous
_JOG_SINGLE_STEP = 0x02  # MOT_SingleStep
_STOP_IMMEDIATE = 0x01   # MOT_Immediate
_STOP_PROFILED = 0x02    # MOT_Profiled
# SCC_GetRealValueFromDeviceUnit / SCC_GetDeviceUnitFromRealValue unitType flags
_UNIT_DISTANCE     = 0
_UNIT_VELOCITY     = 1
_UNIT_ACCELERATION = 2
_DEFAULT_STEPS_PER_REV = 49152.0
# The Kinesis SDK examples pass the inverse gear ratio here.
# ZFS25B uses an approx. 400:9 reduction head, so use 9/400.
_DEFAULT_GEARBOX_RATIO = 9.0 / 400.0
_DEFAULT_PITCH_MM = 1.0
_ZST25_VELOCITY_DEVICE_UNITS_PER_MM_S = 117281240.0
_ZST25_ACCEL_DEVICE_UNITS_PER_MM_S2 = 24032.0
# SCC_GetStatusBits masks
_SB_CW_HARDWARE_LIMIT = 0x00000001
_SB_CCW_HARDWARE_LIMIT = 0x00000002
_SB_MOVING_CW   = 0x00000010
_SB_MOVING_CCW  = 0x00000020
_SB_JOGGING_CW  = 0x00000040
_SB_JOGGING_CCW = 0x00000080
_SB_HOMING      = 0x00000200
_SB_HOMED       = 0x00000400

_lib = None
_LOG_LOCK = threading.Lock()


def _load_lib():
    global _lib
    if _lib is not None:
        return _lib
    os.add_dll_directory(_KINESIS_PATH)
    lib = cdll.LoadLibrary(r"Thorlabs.MotionControl.KCube.StepperMotor.dll")
    lib.SCC_GetStatusBits.restype      = c_ulong
    lib.SCC_GetPositionCounter.restype = c_long
    lib.SCC_GetJogStepSize.restype     = c_uint
    lib.SCC_GetBacklash.restype        = c_long
    lib.SCC_StartPolling.restype       = c_bool
    lib.SCC_LoadSettings.restype       = c_bool
    _lib = lib
    return _lib


def _state_from_status(status: int) -> str:
    if status & _SB_HOMING:
        return "Homing"
    if status & (_SB_MOVING_CW | _SB_MOVING_CCW):
        return "Moving"
    if status & (_SB_JOGGING_CW | _SB_JOGGING_CCW):
        return "Jogging"
    if not (status & _SB_HOMED):
        return "NotHomed"
    return "Ready"


def _wait_motion_complete(lib, sn: c_char_p, timeout_ms: int):
    """
    Poll until the moving flags clear.
    Waits up to 3 s for motion to start first (simulator has ~1 s propagation delay).
    """
    start_dl = time.monotonic() + 3.0
    while time.monotonic() < start_dl:
        if int(lib.SCC_GetStatusBits(sn)) & (_SB_MOVING_CW | _SB_MOVING_CCW):
            break
        time.sleep(0.05)

    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if not (int(lib.SCC_GetStatusBits(sn)) & (_SB_MOVING_CW | _SB_MOVING_CCW)):
            break
        time.sleep(0.05)


def _distance_scale_from_params(params: dict) -> float:
    steps_per_rev = float(params.get("steps_per_rev") or _DEFAULT_STEPS_PER_REV)
    gearbox_ratio = float(params.get("gearbox_ratio") or _DEFAULT_GEARBOX_RATIO)
    pitch_mm = float(params.get("pitch_mm") or _DEFAULT_PITCH_MM)
    if steps_per_rev <= 0 or gearbox_ratio <= 0 or pitch_mm <= 0:
        raise RuntimeError(f"Invalid motor params for distance conversion: {params}")
    return (gearbox_ratio * pitch_mm) / steps_per_rev


def _device_distance_to_mm(device_units: int, params: dict) -> float:
    return float(device_units) * _distance_scale_from_params(params)


def _mm_to_device_distance(mm_value: float, params: dict) -> int:
    return int(round(float(mm_value) / _distance_scale_from_params(params)))


def _sdk_device_distance_to_mm(lib, sn, device_units: int, params: dict) -> float:
    real_value = c_double()
    ret = lib.SCC_GetRealValueFromDeviceUnit(
        sn,
        c_int(int(device_units)),
        byref(real_value),
        c_int(_UNIT_DISTANCE),
    )
    if ret == 0 and math.isfinite(real_value.value):
        return float(real_value.value)
    return _device_distance_to_mm(device_units, params)


def _sdk_mm_to_device_distance(lib, sn, mm_value: float, params: dict) -> int:
    device_value = c_int()
    ret = lib.SCC_GetDeviceUnitFromRealValue(
        sn,
        c_double(float(mm_value)),
        byref(device_value),
        c_int(_UNIT_DISTANCE),
    )
    if ret == 0:
        return int(device_value.value)
    return _mm_to_device_distance(mm_value, params)


def _device_velocity_to_mm_per_s(device_units: int) -> float:
    return float(device_units) / _ZST25_VELOCITY_DEVICE_UNITS_PER_MM_S


def _mm_per_s_to_device_velocity(mm_per_s: float) -> int:
    return int(round(float(mm_per_s) * _ZST25_VELOCITY_DEVICE_UNITS_PER_MM_S))


def _device_accel_to_mm_per_s2(device_units: int) -> float:
    return float(device_units) / _ZST25_ACCEL_DEVICE_UNITS_PER_MM_S2


def _mm_per_s2_to_device_accel(mm_per_s2: float) -> int:
    return int(round(float(mm_per_s2) * _ZST25_ACCEL_DEVICE_UNITS_PER_MM_S2))


def _sdk_device_velocity_to_mm_per_s(lib, sn, device_units: int) -> float:
    real_value = c_double()
    ret = lib.SCC_GetRealValueFromDeviceUnit(
        sn,
        c_int(int(device_units)),
        byref(real_value),
        c_int(_UNIT_VELOCITY),
    )
    if ret == 0:
        return float(real_value.value)
    return _device_velocity_to_mm_per_s(device_units)


def _sdk_mm_per_s_to_device_velocity(lib, sn, mm_per_s: float) -> int:
    device_value = c_int()
    ret = lib.SCC_GetDeviceUnitFromRealValue(
        sn,
        c_double(float(mm_per_s)),
        byref(device_value),
        c_int(_UNIT_VELOCITY),
    )
    if ret == 0:
        return int(device_value.value)
    return _mm_per_s_to_device_velocity(mm_per_s)


def _sdk_device_accel_to_mm_per_s2(lib, sn, device_units: int) -> float:
    real_value = c_double()
    ret = lib.SCC_GetRealValueFromDeviceUnit(
        sn,
        c_int(int(device_units)),
        byref(real_value),
        c_int(_UNIT_ACCELERATION),
    )
    if ret == 0:
        return float(real_value.value)
    return _device_accel_to_mm_per_s2(device_units)


def _sdk_mm_per_s2_to_device_accel(lib, sn, mm_per_s2: float) -> int:
    device_value = c_int()
    ret = lib.SCC_GetDeviceUnitFromRealValue(
        sn,
        c_double(float(mm_per_s2)),
        byref(device_value),
        c_int(_UNIT_ACCELERATION),
    )
    if ret == 0:
        return int(device_value.value)
    return _mm_per_s2_to_device_accel(mm_per_s2)


def _read_position_mm(lib, sn, params: dict) -> float:
    raw_pos = int(lib.SCC_GetPositionCounter(sn))
    return _sdk_device_distance_to_mm(lib, sn, raw_pos, params)


def _read_motor_params(lib, sn) -> dict:
    steps_per_rev = c_double()
    gearbox_ratio = c_double()
    pitch_mm = c_double()
    ret = lib.SCC_GetMotorParamsExt(
        sn,
        byref(steps_per_rev),
        byref(gearbox_ratio),
        byref(pitch_mm),
    )
    if ret != 0:
        raise RuntimeError(f"SCC_GetMotorParamsExt failed (error {ret})")
    return {
        "steps_per_rev": steps_per_rev.value,
        "gearbox_ratio": gearbox_ratio.value,
        "pitch_mm": pitch_mm.value,
    }


# ---------------------------------------------------------------------------
# Module-level param readers — read from the SDK's local cache only.
# SCC_Request* functions are intentionally NOT used: they trigger a blocking
# device round-trip via the simulator pipe and would serialise all subsequent
# motion commands behind them, causing multi-second button latency.
# SCC_LoadSettings populates the local cache.
# ---------------------------------------------------------------------------

def _read_velocity_params(lib, sn) -> dict:
    try:
        accel_dev = c_int()
        vel_dev   = c_int()
        lib.SCC_GetVelParams(sn, byref(accel_dev), byref(vel_dev))
        return {
            "max_velocity": _sdk_device_velocity_to_mm_per_s(lib, sn, vel_dev.value),
            "acceleration": _sdk_device_accel_to_mm_per_s2(lib, sn, accel_dev.value),
            "raw_max_velocity": vel_dev.value,
            "raw_acceleration": accel_dev.value,
        }
    except Exception:
        return {}


def _read_jog_params(lib, sn, motor_params: dict | None = None) -> dict:
    try:
        params = motor_params or {
            "steps_per_rev": _DEFAULT_STEPS_PER_REV,
            "gearbox_ratio": _DEFAULT_GEARBOX_RATIO,
            "pitch_mm": _DEFAULT_PITCH_MM,
        }
        jog_mode = c_short()
        stop_mode = c_short()
        accel_dev = c_int()
        vel_dev   = c_int()
        lib.SCC_GetJogMode(sn, byref(jog_mode), byref(stop_mode))
        lib.SCC_GetJogVelParams(sn, byref(accel_dev), byref(vel_dev))
        step_dev   = int(lib.SCC_GetJogStepSize(sn))
        return {
            "mode": jog_mode.value,
            "stop_mode": stop_mode.value,
            "step_size":    _sdk_device_distance_to_mm(lib, sn, step_dev, params),
            "max_velocity": _sdk_device_velocity_to_mm_per_s(lib, sn, vel_dev.value),
            "acceleration": _sdk_device_accel_to_mm_per_s2(lib, sn, accel_dev.value),
            "raw_step_size": step_dev,
            "raw_max_velocity": vel_dev.value,
            "raw_acceleration": accel_dev.value,
        }
    except Exception:
        return {}


def _read_gen_move_params(lib, sn, motor_params: dict | None = None) -> dict:
    try:
        params = motor_params or {
            "steps_per_rev": _DEFAULT_STEPS_PER_REV,
            "gearbox_ratio": _DEFAULT_GEARBOX_RATIO,
            "pitch_mm": _DEFAULT_PITCH_MM,
        }
        raw     = int(lib.SCC_GetBacklash(sn))
        return {
            "backlash": _sdk_device_distance_to_mm(lib, sn, raw, params),
            "raw_backlash": raw,
        }
    except Exception:
        return {}


class KST101:
    """
    Driver for a single KST101 axis.

    Instantiate one per physical axis.  Call connect(serial) to open the
    device; all motion and parameter methods are no-ops while disconnected.
    snapshot() is safe to call from any thread at high frequency.

    Simulator usage
    ---------------
    Call KST101.enable_simulations() once before any connect/list_devices calls
    and KST101.disable_simulations() at shutdown.
    """

    @staticmethod
    def enable_simulations():
        """Connect to the Kinesis Simulator Manager (no-op if not running)."""
        _load_lib().TLI_InitializeSimulations()
        time.sleep(1.0)  # allow simulator manager to register virtual devices

    @staticmethod
    def disable_simulations():
        """Disconnect from the Kinesis Simulator Manager."""
        _load_lib().TLI_UninitializeSimulations()

    @staticmethod
    def list_devices() -> list[str]:
        """Return serial numbers of all connected KST101 devices."""
        lib = _load_lib()
        for _ in range(5):
            try:
                lib.TLI_BuildDeviceList()
                buf = create_string_buffer(256)
                lib.TLI_GetDeviceListByTypeExt(buf, c_ulong(256), c_int(_KST101_TYPE_ID))
                raw   = buf.value.decode("utf-8").strip()
                found = [s.strip() for s in raw.split(",") if s.strip()]
                if found:
                    return found
            except Exception:
                pass
            time.sleep(0.5)
        return []

    def __init__(self):
        self._serial: bytes | None = None
        self._lib = None
        self._connected = False
        self._state = "Disconnected"
        self._position: float | None = None
        self._status_bits: int = 0
        self._motor_params: dict = {
            "steps_per_rev": _DEFAULT_STEPS_PER_REV,
            "gearbox_ratio": _DEFAULT_GEARBOX_RATIO,
            "pitch_mm": _DEFAULT_PITCH_MM,
        }
        self._velocity_params: dict = {}
        self._jog_params: dict = {}
        self._gen_move_params: dict = {}
        self._last_error: str | None = None
        self._lock = threading.Lock()

        # Developer metrics — written by snapshot() and _pre_command(), read by snapshot()
        self._cmd_issue_time: float = 0.0          # perf_counter when _pre_command last ran
        self._cmd_latency_ms: float | None = None  # ms from _pre_command to first state change
        self._prev_snap_state: str = "Disconnected"
        self._last_cache_change_time: float = 0.0  # perf_counter of last position/status change
        self._poll_update_ms: float | None = None  # measured ms between cache updates
        self._last_device_poll_request_time: float = 0.0

    def _serial_label(self, serial_bytes: bytes | None = None) -> str:
        serial_value = self._serial if serial_bytes is None else serial_bytes
        if isinstance(serial_value, bytes):
            try:
                return serial_value.decode("utf-8", errors="replace")
            except Exception:
                return repr(serial_value)
        if isinstance(serial_value, str):
            return serial_value
        return "unbound"

    def _log(self, level: str, message: str, *, serial_bytes: bytes | None = None):
        with _LOG_LOCK:
            print(f"[KST101:{self._serial_label(serial_bytes)}] {level}: {message}", flush=True)

    def _set_error(self, message: str, *, context: str | None = None, serial_bytes: bytes | None = None):
        full_message = f"{context}: {message}" if context else str(message)
        with self._lock:
            previous_error = self._last_error
            self._last_error = full_message
        if full_message != previous_error:
            self._log("ERROR", full_message, serial_bytes=serial_bytes)

    def _clear_error(self):
        with self._lock:
            self._last_error = None

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, serial: str):
        """
        Open the device and read initial parameters.

        All SDK work — including the initial parameter read — is completed
        before _connected is set to True.  This ensures the snapshot polling
        thread never races with device initialisation, and that motion commands
        issued immediately after connect() returns are not serialised behind
        pending device round-trips.
        """
        polling_started = False
        device_open = False
        sn_bytes = serial.encode() if isinstance(serial, str) else serial
        sn = c_char_p(sn_bytes)
        try:
            lib      = _load_lib()

            lib.TLI_BuildDeviceList()
            ret = lib.SCC_Open(sn)
            if ret != 0:
                raise RuntimeError(f"SCC_Open failed (error {ret})")
            device_open = True

            time.sleep(0.15)

            if not bool(lib.SCC_StartPolling(sn, c_int(_POLLING_INTERVAL_MS))):
                raise RuntimeError(f"SCC_StartPolling failed (interval {_POLLING_INTERVAL_MS} ms)")
            polling_started = True

            lib.SCC_EnableChannel(sn)
            time.sleep(0.15)

            lib.SCC_LoadSettings(sn)

            lib.SCC_RequestSettings(sn)
            lib.SCC_RequestVelParams(sn)
            lib.SCC_RequestJogParams(sn)
            lib.SCC_RequestBacklash(sn)
            lib.SCC_RequestStatusBits(sn)
            lib.SCC_RequestPosition(sn)
            time.sleep(0.5)  # allow settings to propagate to the polling cache

            motor_params = _read_motor_params(lib, sn)
            # Read params from the SDK's local cache while we are still the sole
            # user of this device (_connected is still False so snapshot() is idle).
            vel_params = _read_velocity_params(lib, sn)
            jog_params = _read_jog_params(lib, sn, motor_params)
            gen_params = _read_gen_move_params(lib, sn, motor_params)
            initial_position = None
            status_bits = 0
            state = "Disconnected"
            try:
                time.sleep(0.1)
                status_bits = int(lib.SCC_GetStatusBits(sn))
                state = _state_from_status(status_bits)
                initial_position = _read_position_mm(lib, sn, motor_params)
            except Exception as exc:
                raise RuntimeError(f"initial cache read failed: {exc}") from exc

            # Atomically expose the device to other threads.
            with self._lock:
                self._lib            = lib
                self._serial         = sn_bytes
                self._motor_params   = motor_params
                self._velocity_params = vel_params
                self._jog_params      = jog_params
                self._gen_move_params = gen_params
                self._connected       = True
                self._state           = state
                self._position        = initial_position
                self._status_bits     = status_bits
                self._prev_snap_state = state
                self._cmd_latency_ms  = None
                self._poll_update_ms  = None
                self._last_cache_change_time = time.perf_counter()
                self._last_device_poll_request_time = 0.0

            self._clear_error()
            if initial_position is None or not math.isfinite(initial_position):
                position_label = "unknown"
            else:
                position_label = f"{initial_position:.4f}"
            self._log("INFO", f"Connected; state={state} position={position_label}", serial_bytes=sn_bytes)

        except Exception as exc:
            if polling_started:
                try:
                    lib.SCC_StopPolling(sn)
                except Exception:
                    pass
            if device_open:
                try:
                    lib.SCC_Close(sn)
                except Exception:
                    pass
            with self._lock:
                self._lib        = None
                self._serial     = None
                self._connected  = False
                self._state      = "Disconnected"
                self._position   = None
                self._status_bits = 0
                self._velocity_params = {}
                self._jog_params = {}
                self._gen_move_params = {}
                self._motor_params = {
                    "steps_per_rev": _DEFAULT_STEPS_PER_REV,
                    "gearbox_ratio": _DEFAULT_GEARBOX_RATIO,
                    "pitch_mm": _DEFAULT_PITCH_MM,
                }
                self._prev_snap_state = "Disconnected"
                self._cmd_latency_ms = None
                self._poll_update_ms = None
                self._last_cache_change_time = 0.0
                self._last_device_poll_request_time = 0.0
            self._set_error(str(exc), context="connect", serial_bytes=sn_bytes)

    def disconnect(self):
        if not self._connected:
            return
        sn = c_char_p(self._serial)
        try:
            self._lib.SCC_StopPolling(sn)
            self._lib.SCC_Close(sn)
        except Exception as exc:
            self._set_error(str(exc), context="disconnect")
        finally:
            with self._lock:
                self._lib = None
                self._serial = None
                self._connected = False
                self._state     = "Disconnected"
                self._position  = None
                self._status_bits = 0
                self._velocity_params = {}
                self._jog_params = {}
                self._gen_move_params = {}
                self._prev_snap_state = "Disconnected"
                self._cmd_latency_ms = None
                self._poll_update_ms = None
                self._last_cache_change_time = 0.0
                self._last_device_poll_request_time = 0.0
                self._motor_params = {
                    "steps_per_rev": _DEFAULT_STEPS_PER_REV,
                    "gearbox_ratio": _DEFAULT_GEARBOX_RATIO,
                    "pitch_mm": _DEFAULT_PITCH_MM,
                }

    # ------------------------------------------------------------------
    # State snapshot — called at high frequency from the polling thread
    # ------------------------------------------------------------------

    def _poll_device_cache_if_due(self, sn):
        now = time.perf_counter()
        if (now - self._last_device_poll_request_time) * 1000 < _POLLING_INTERVAL_MS:
            return
        self._lib.SCC_RequestStatusBits(sn)
        self._lib.SCC_RequestPosition(sn)
        self._last_device_poll_request_time = now

    def snapshot(self) -> dict:
        """
        Return a thread-safe copy of current driver state.
        Reads position and status from the SDK polling cache — no blocking I/O.
        """
        if self._connected and self._lib and self._serial:
            sn = c_char_p(self._serial)
            try:
                self._poll_device_cache_if_due(sn)
                pos_mm = _read_position_mm(self._lib, sn, self._motor_params)
                status = int(self._lib.SCC_GetStatusBits(sn))
                state  = _state_from_status(status)

                now = time.perf_counter()
                cache_changed = (
                    state != self._state
                    or abs(pos_mm - (self._position or 0.0)) > 1e-6
                )
                if cache_changed:
                    # Measure how often the SDK cache actually delivers fresh data.
                    if self._last_cache_change_time > 0:
                        self._poll_update_ms = (now - self._last_cache_change_time) * 1000
                    self._last_cache_change_time = now

                if state != self._prev_snap_state:
                    # Measure latency from last _pre_command() to first observable state change.
                    if self._cmd_issue_time > 0:
                        self._cmd_latency_ms = (now - self._cmd_issue_time) * 1000
                        self._cmd_issue_time = 0.0
                    self._prev_snap_state = state

                with self._lock:
                    self._position = pos_mm
                    self._state    = state
                    self._status_bits = status
                    self._last_error = None
            except Exception as exc:
                self._set_error(str(exc), context="snapshot")

        with self._lock:
            status_bits = getattr(self, "_status_bits", 0)
            return {
                "connected":        self._connected,
                "state":            self._state,
                "position":         self._position,
                "last_error":       self._last_error,
                "status_bits":      status_bits,
                "homed":            bool(status_bits & _SB_HOMED),
                "cw_limit":         bool(status_bits & _SB_CW_HARDWARE_LIMIT),
                "ccw_limit":        bool(status_bits & _SB_CCW_HARDWARE_LIMIT),
                "motor_params":     dict(self._motor_params),
                "velocity_params":  dict(self._velocity_params),
                "jog_params":       dict(self._jog_params),
                "gen_move_params":  dict(self._gen_move_params),
                "cmd_latency_ms":   self._cmd_latency_ms,
                "poll_update_ms":   self._poll_update_ms,
                "poll_interval_ms": _POLLING_INTERVAL_MS,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pre_command(self):
        """
        Record command timing without interrupting the SDK polling loop.

        Keeping polling active allows the device cache to continue receiving
        position and status updates while a command is in flight, which is what
        the UI and snapshot() rely on for live readback.
        """
        self._cmd_issue_time = time.perf_counter()
        with self._lock:
            self._last_error = None

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def home(self, timeout_ms: int = 60_000):
        """Initiate homing in a daemon thread; returns immediately."""
        if not self._connected:
            return
        self._pre_command()
        sn_bytes = self._serial
        lib      = self._lib

        def _run():
            sn = c_char_p(sn_bytes)
            try:
                ret = lib.SCC_Home(sn)
                if ret != 0:
                    raise RuntimeError(f"SCC_Home failed (error {ret})")
                deadline = time.monotonic() + timeout_ms / 1000.0
                while time.monotonic() < deadline:
                    status = int(lib.SCC_GetStatusBits(sn))
                    if (status & _SB_HOMED) and not (status & _SB_HOMING):
                        break
                    time.sleep(0.1)
            except Exception as exc:
                self._set_error(str(exc), context="home")

        threading.Thread(target=_run, daemon=True).start()

    def move_relative(self, dist_mm: float, timeout_ms: int = 60_000):
        """Move by dist_mm (signed mm) in a daemon thread; returns immediately."""
        if not self._connected:
            return
        self._pre_command()
        sn_bytes = self._serial
        lib      = self._lib

        def _run():
            sn = c_char_p(sn_bytes)
            try:
                dev = c_int(_sdk_mm_to_device_distance(lib, sn, dist_mm, self._motor_params))
                if dev.value == 0 and dist_mm != 0.0:
                    raise RuntimeError(
                        f"distance conversion returned 0 device units "
                        f"for {dist_mm} mm (motor params={self._motor_params})"
                    )
                ret = lib.SCC_MoveRelative(sn, dev)
                if ret != 0:
                    raise RuntimeError(f"SCC_MoveRelative failed (error {ret})")
                _wait_motion_complete(lib, sn, timeout_ms)
            except Exception as exc:
                self._set_error(str(exc), context="move_relative")

        threading.Thread(target=_run, daemon=True).start()

    def move_to(self, pos_mm: float, timeout_ms: int = 60_000):
        """Move to absolute position pos_mm in a daemon thread; returns immediately."""
        if not self._connected:
            return
        self._pre_command()
        sn_bytes = self._serial
        lib      = self._lib

        def _run():
            sn = c_char_p(sn_bytes)
            try:
                dev = c_int(_sdk_mm_to_device_distance(lib, sn, pos_mm, self._motor_params))
                ret = lib.SCC_MoveToPosition(sn, dev)
                if ret != 0:
                    raise RuntimeError(f"SCC_MoveToPosition failed (error {ret})")
                _wait_motion_complete(lib, sn, timeout_ms)
            except Exception as exc:
                self._set_error(str(exc), context="move_to")

        threading.Thread(target=_run, daemon=True).start()

    def _move_jog_continuous(self, direction: str):
        """Start continuous motion using the device's jog profile via SCC_MoveJog."""
        if not self._connected:
            return
        self._pre_command()
        sn = c_char_p(self._serial)
        jog_dir = c_short(_DIR_FORWARDS if direction == "+" else _DIR_BACKWARDS)
        try:
            self._lib.SCC_SetJogMode(sn, c_short(_JOG_CONTINUOUS), c_short(_STOP_PROFILED))
            ret = self._lib.SCC_MoveJog(sn, jog_dir)
            if ret != 0:
                raise RuntimeError(f"SCC_MoveJog failed (error {ret})")
        except Exception as exc:
            self._set_error(str(exc), context="jog")

    def jog(self, direction: str):
        """Start a jog in direction '+' or '-'. Call stop() to end motion."""
        self._move_jog_continuous(direction)

    def move_continuous(self, direction: str):
        """
        Run continuously in direction '+' or '-' until stop() is called.
        Uses SCC_MoveJog with the current continuous jog profile.
        """
        self._move_jog_continuous(direction)

    def stop(self, immediate: bool = False):
        """Stop all motion. immediate=True uses SCC_StopImmediate (may lose position)."""
        if not self._connected:
            return
        self._pre_command()
        sn = c_char_p(self._serial)
        if immediate:
            self._lib.SCC_StopImmediate(sn)
        else:
            self._lib.SCC_StopProfiled(sn)

    # ------------------------------------------------------------------
    # Parameter setters
    # ------------------------------------------------------------------

    def set_velocity_params(
        self,
        max_velocity: float | None = None,
        acceleration: float | None = None,
    ):
        """Set main velocity profile. Values in mm/s and mm/s²."""
        if not self._connected:
            return
        self._pre_command()
        sn = c_char_p(self._serial)
        try:
            cur_accel = c_int()
            cur_vel   = c_int()
            self._lib.SCC_GetVelParams(sn, byref(cur_accel), byref(cur_vel))

            if acceleration is not None:
                cur_accel = c_int(_sdk_mm_per_s2_to_device_accel(self._lib, sn, acceleration))
            if max_velocity is not None:
                cur_vel = c_int(_sdk_mm_per_s_to_device_velocity(self._lib, sn, max_velocity))

            self._lib.SCC_SetVelParams(sn, cur_accel, cur_vel)
            with self._lock:
                self._velocity_params = _read_velocity_params(self._lib, sn)
        except Exception as exc:
            self._set_error(str(exc), context="set_velocity_params")

    def set_jog_params(
        self,
        step_size:    float | None = None,
        max_velocity: float | None = None,
        acceleration: float | None = None,
    ):
        """Set jog profile. Values in mm, mm/s, mm/s²."""
        if not self._connected:
            return
        self._pre_command()
        sn = c_char_p(self._serial)
        try:
            cur_accel = c_int()
            cur_vel   = c_int()
            self._lib.SCC_GetJogVelParams(sn, byref(cur_accel), byref(cur_vel))
            cur_step  = int(self._lib.SCC_GetJogStepSize(sn))

            if step_size is not None:
                cur_step = _sdk_mm_to_device_distance(self._lib, sn, step_size, self._motor_params)
            if acceleration is not None:
                cur_accel = c_int(_sdk_mm_per_s2_to_device_accel(self._lib, sn, acceleration))
            if max_velocity is not None:
                cur_vel = c_int(_sdk_mm_per_s_to_device_velocity(self._lib, sn, max_velocity))

            self._lib.SCC_SetJogMode(sn, c_short(_JOG_CONTINUOUS), c_short(_STOP_PROFILED))
            self._lib.SCC_SetJogStepSize(sn, c_uint(cur_step))
            self._lib.SCC_SetJogVelParams(sn, cur_accel, cur_vel)
            with self._lock:
                self._jog_params = _read_jog_params(self._lib, sn, self._motor_params)
        except Exception as exc:
            self._set_error(str(exc), context="set_jog_params")

    def set_backlash(self, value_mm: float):
        """Set backlash compensation distance in mm."""
        if not self._connected:
            return
        self._pre_command()
        sn = c_char_p(self._serial)
        try:
            dev = c_int()
            self._lib.SCC_GetDeviceUnitFromRealValue(
                sn, c_double(value_mm), byref(dev), c_int(_UNIT_DISTANCE)
            )
            self._lib.SCC_SetBacklash(sn, c_long(dev.value))
            with self._lock:
                self._gen_move_params = _read_gen_move_params(self._lib, sn, self._motor_params)
        except Exception as exc:
            self._set_error(str(exc), context="set_backlash")
