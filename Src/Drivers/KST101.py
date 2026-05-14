"""
Driver for the Thorlabs KST101 KCube Stepper Motor.
Uses the Thorlabs Kinesis C DLL via ctypes.
All units are millimetres (mm), mm/s, or mm/s².
"""

import datetime
import os
import time
import threading
from Utils.shared_state import dev_mode
from ctypes import (
    cdll, c_char_p, c_int, c_uint, c_long, c_double, c_short, c_bool, c_ulong,
    byref, create_string_buffer,
)


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _log(serial: str, msg: str):
    sn = serial.decode() if isinstance(serial, bytes) else (serial or "?")
    print(f"[{_ts()}] KST101({sn}): {msg}")

_KINESIS_PATH = r"C:\Program Files\Thorlabs\Kinesis"
_KST101_TYPE_ID = 26

# Polling interval passed to SCC_StartPolling.
# Must be > 333 ms (simulator drain rate) to prevent queue backlog.
# 250 ms keeps queue depth ≤ 1 in typical use while giving 4 Hz cache updates.
_POLLING_INTERVAL_MS = 400 if dev_mode else 100

# MOT_TravelDirection
_DIR_FORWARDS  = 0x01
_DIR_BACKWARDS = 0x02
# MOT_JogModes / MOT_StopModes
_JOG_CONTINUOUS = 0x01   # MOT_Continuous
_STOP_IMMEDIATE = 0x01   # MOT_Immediate
# SCC_GetRealValueFromDeviceUnit / SCC_GetDeviceUnitFromRealValue unitType flags
_UNIT_DISTANCE     = 0
_UNIT_VELOCITY     = 1
_UNIT_ACCELERATION = 2
# SCC_GetStatusBits masks
_SB_MOVING_CW   = 0x00000010
_SB_MOVING_CCW  = 0x00000020
_SB_JOGGING_CW  = 0x00000040
_SB_JOGGING_CCW = 0x00000080
_SB_HOMING      = 0x00000200
_SB_HOMED       = 0x00000400

_lib = None


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


# ---------------------------------------------------------------------------
# Module-level param readers — read from the SDK's local cache only.
# SCC_Request* functions are intentionally NOT used: they trigger a blocking
# device round-trip via the simulator pipe and would serialise all subsequent
# motion commands behind them, causing multi-second button latency.
# SCC_LoadSettings / SCC_SetMotorParamsExt already populate the local cache.
# ---------------------------------------------------------------------------

def _read_velocity_params(lib, sn) -> dict:
    try:
        accel_dev = c_int()
        vel_dev   = c_int()
        lib.SCC_GetVelParams(sn, byref(accel_dev), byref(vel_dev))
        real_accel = c_double()
        real_vel   = c_double()
        lib.SCC_GetRealValueFromDeviceUnit(
            sn, c_int(accel_dev.value), byref(real_accel), c_int(_UNIT_ACCELERATION)
        )
        lib.SCC_GetRealValueFromDeviceUnit(
            sn, c_int(vel_dev.value), byref(real_vel), c_int(_UNIT_VELOCITY)
        )
        return {"max_velocity": real_vel.value, "acceleration": real_accel.value}
    except Exception:
        return {}


def _read_jog_params(lib, sn) -> dict:
    try:
        accel_dev = c_int()
        vel_dev   = c_int()
        lib.SCC_GetJogVelParams(sn, byref(accel_dev), byref(vel_dev))
        step_dev   = int(lib.SCC_GetJogStepSize(sn))
        real_accel = c_double()
        real_vel   = c_double()
        real_step  = c_double()
        lib.SCC_GetRealValueFromDeviceUnit(
            sn, c_int(accel_dev.value), byref(real_accel), c_int(_UNIT_ACCELERATION)
        )
        lib.SCC_GetRealValueFromDeviceUnit(
            sn, c_int(vel_dev.value), byref(real_vel), c_int(_UNIT_VELOCITY)
        )
        lib.SCC_GetRealValueFromDeviceUnit(
            sn, c_int(step_dev), byref(real_step), c_int(_UNIT_DISTANCE)
        )
        return {
            "step_size":    real_step.value,
            "max_velocity": real_vel.value,
            "acceleration": real_accel.value,
        }
    except Exception:
        return {}


def _read_gen_move_params(lib, sn) -> dict:
    try:
        raw     = int(lib.SCC_GetBacklash(sn))
        real_bl = c_double()
        lib.SCC_GetRealValueFromDeviceUnit(
            sn, c_int(raw), byref(real_bl), c_int(_UNIT_DISTANCE)
        )
        return {"backlash": real_bl.value}
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
        print(f"[{_ts()}] KST101: enable_simulations — calling TLI_InitializeSimulations")
        _load_lib().TLI_InitializeSimulations()
        time.sleep(1.0)  # allow simulator manager to register virtual devices
        print(f"[{_ts()}] KST101: enable_simulations — done")

    @staticmethod
    def disable_simulations():
        """Disconnect from the Kinesis Simulator Manager."""
        print(f"[{_ts()}] KST101: disable_simulations")
        _load_lib().TLI_UninitializeSimulations()

    @staticmethod
    def list_devices() -> list[str]:
        """Return serial numbers of all connected KST101 devices."""
        print(f"[{_ts()}] KST101: list_devices — scanning")
        lib = _load_lib()
        for attempt in range(5):
            try:
                lib.TLI_BuildDeviceList()
                buf = create_string_buffer(256)
                lib.TLI_GetDeviceListByTypeExt(buf, c_ulong(256), c_int(_KST101_TYPE_ID))
                raw   = buf.value.decode("utf-8").strip()
                found = [s.strip() for s in raw.split(",") if s.strip()]
                if found:
                    print(f"[{_ts()}] KST101: list_devices — found {found} (attempt {attempt + 1})")
                    return found
            except Exception as exc:
                print(f"[{_ts()}] KST101: list_devices — attempt {attempt + 1} error: {exc}")
            time.sleep(0.5)
        print(f"[{_ts()}] KST101: list_devices — no devices found after 5 attempts")
        return []

    def __init__(self):
        self._serial: bytes | None = None
        self._lib = None
        self._connected = False
        self._state = "Disconnected"
        self._position: float | None = None
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

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, serial: str):
        """
        Open the device, configure it, and read initial parameters.

        All SDK work — including the initial parameter read — is completed
        before _connected is set to True.  This ensures the snapshot polling
        thread never races with device initialisation, and that motion commands
        issued immediately after connect() returns are not serialised behind
        pending device round-trips.
        """
        try:
            lib      = _load_lib()
            sn_bytes = serial.encode() if isinstance(serial, str) else serial
            sn       = c_char_p(sn_bytes)
            _log(sn_bytes, "connect — TLI_BuildDeviceList + SCC_Open")

            lib.TLI_BuildDeviceList()
            ret = lib.SCC_Open(sn)
            if ret != 0:
                raise RuntimeError(f"SCC_Open failed (error {ret})")
            _log(sn_bytes, "connect — SCC_Open OK; starting polling + enabling channel")

            lib.SCC_StartPolling(sn, c_int(_POLLING_INTERVAL_MS))
            lib.SCC_EnableChannel(sn)
            _log(sn_bytes, "connect — SCC_SetMotorParamsExt (pre-LoadSettings)")
            # Set ZST motor params before LoadSettings so unit conversion works.
            # Re-apply after LoadSettings in case it cleared them (simulator
            # serials without a saved config file).
            lib.SCC_SetMotorParamsExt(
                sn, c_double(49152.0), c_double(0.0245), c_double(1.0)
            )
            _log(sn_bytes, "connect — SCC_LoadSettings")
            lib.SCC_LoadSettings(sn)
            _log(sn_bytes, "connect — SCC_SetMotorParamsExt (post-LoadSettings)")
            lib.SCC_SetMotorParamsExt(
                sn, c_double(49152.0), c_double(0.0245), c_double(1.0)
            )
            lib.SCC_SetJogMode(sn, c_short(_JOG_CONTINUOUS), c_short(_STOP_IMMEDIATE))
            _log(sn_bytes, "connect — sleeping 1 s for settings to propagate")
            time.sleep(1.0)  # allow settings to propagate to the polling cache

            _log(sn_bytes, "connect — reading cached params")
            # Read params from the SDK's local cache while we are still the sole
            # user of this device (_connected is still False so snapshot() is idle).
            vel_params = _read_velocity_params(lib, sn)
            jog_params = _read_jog_params(lib, sn)
            gen_params = _read_gen_move_params(lib, sn)
            _log(sn_bytes,
                 f"connect — vel={vel_params}  jog={jog_params}  gen={gen_params}")

            # Atomically expose the device to other threads.
            with self._lock:
                self._lib            = lib
                self._serial         = sn_bytes
                self._velocity_params = vel_params
                self._jog_params      = jog_params
                self._gen_move_params = gen_params
                self._connected       = True
                self._state           = "Ready"
                self._last_error      = None
            _log(sn_bytes, "connect — DONE (_connected = True)")

        except Exception as exc:
            with self._lock:
                self._connected  = False
                self._state      = "Disconnected"
                self._last_error = str(exc)
            _log(serial, f"connect — FAILED: {exc}")

    def disconnect(self):
        if not self._connected:
            return
        _log(self._serial, "disconnect")
        sn = c_char_p(self._serial)
        try:
            self._lib.SCC_StopPolling(sn)
            self._lib.SCC_Close(sn)
        except Exception as exc:
            _log(self._serial, f"disconnect — error: {exc}")
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._connected = False
                self._state     = "Disconnected"
                self._position  = None
            _log(self._serial, "disconnect — done")

    # ------------------------------------------------------------------
    # State snapshot — called at high frequency from the polling thread
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """
        Return a thread-safe copy of current driver state.
        Reads position and status from the SDK polling cache — no blocking I/O.
        """
        if self._connected and self._lib and self._serial:
            sn = c_char_p(self._serial)
            try:
                raw_pos  = self._lib.SCC_GetPositionCounter(sn)
                real_pos = c_double()
                self._lib.SCC_GetRealValueFromDeviceUnit(
                    sn, c_int(raw_pos), byref(real_pos), c_int(_UNIT_DISTANCE)
                )
                status = int(self._lib.SCC_GetStatusBits(sn))
                state  = _state_from_status(status)

                now = time.perf_counter()
                cache_changed = (
                    state != self._state
                    or abs(real_pos.value - (self._position or 0.0)) > 1e-6
                )
                if cache_changed:
                    # Measure how often the SDK cache actually delivers fresh data.
                    if self._last_cache_change_time > 0:
                        self._poll_update_ms = (now - self._last_cache_change_time) * 1000
                    self._last_cache_change_time = now

                if state != self._prev_snap_state:
                    _log(self._serial,
                         f"state {self._prev_snap_state!r} → {state!r}  pos={real_pos.value:.4f} mm")
                    # Measure latency from last _pre_command() to first observable state change.
                    if self._cmd_issue_time > 0:
                        self._cmd_latency_ms = (now - self._cmd_issue_time) * 1000
                        self._cmd_issue_time = 0.0
                    self._prev_snap_state = state

                with self._lock:
                    self._position = real_pos.value
                    self._state    = state
            except Exception:
                pass

        with self._lock:
            return {
                "connected":        self._connected,
                "state":            self._state,
                "position":         self._position,
                "last_error":       self._last_error,
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
        Stop the SDK polling loop and schedule its restart 200 ms later.

        Calling this before any SDK command that sends to the device ensures the
        command arrives at an empty pipe queue rather than queueing behind pending
        polling messages.  The 200 ms window is long enough for the command to
        flush through the pipe; polling then resumes automatically.
        """
        self._cmd_issue_time = time.perf_counter()
        sn_bytes = self._serial
        lib      = self._lib
        lib.SCC_StopPolling(c_char_p(sn_bytes))

        def _restart():
            time.sleep(0.2)
            if self._connected:
                _log(sn_bytes, "poll restart")
                lib.SCC_StartPolling(c_char_p(sn_bytes), c_int(_POLLING_INTERVAL_MS))

        threading.Thread(target=_restart, daemon=True, name="KST101PollRestart").start()

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def home(self, timeout_ms: int = 60_000):
        """Initiate homing in a daemon thread; returns immediately."""
        if not self._connected:
            return
        _log(self._serial, "home — issuing SCC_Home")
        self._pre_command()
        sn_bytes = self._serial
        lib      = self._lib

        def _run():
            sn = c_char_p(sn_bytes)
            try:
                ret = lib.SCC_Home(sn)
                if ret != 0:
                    raise RuntimeError(f"SCC_Home failed (error {ret})")
                _log(sn_bytes, "home — SCC_Home accepted, waiting for HOMED bit")
                deadline = time.monotonic() + timeout_ms / 1000.0
                while time.monotonic() < deadline:
                    status = int(lib.SCC_GetStatusBits(sn))
                    if (status & _SB_HOMED) and not (status & _SB_HOMING):
                        break
                    time.sleep(0.1)
                _log(sn_bytes, "home — complete")
            except Exception as exc:
                _log(sn_bytes, f"home — error: {exc}")
                with self._lock:
                    self._last_error = str(exc)

        threading.Thread(target=_run, daemon=True).start()

    def move_relative(self, dist_mm: float, timeout_ms: int = 60_000):
        """Move by dist_mm (signed mm) in a daemon thread; returns immediately."""
        if not self._connected:
            return
        _log(self._serial, f"move_relative {dist_mm:+.4f} mm — issuing SCC_MoveRelative")
        self._pre_command()
        sn_bytes = self._serial
        lib      = self._lib

        def _run():
            sn = c_char_p(sn_bytes)
            try:
                dev = c_int()
                ret = lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(dist_mm), byref(dev), c_int(_UNIT_DISTANCE)
                )
                if ret != 0:
                    raise RuntimeError(
                        f"SCC_GetDeviceUnitFromRealValue failed (error {ret})"
                    )
                if dev.value == 0 and dist_mm != 0.0:
                    raise RuntimeError(
                        f"SCC_GetDeviceUnitFromRealValue returned 0 device units "
                        f"for {dist_mm} mm (motor params not loaded?)"
                    )
                ret = lib.SCC_MoveRelative(sn, dev)
                if ret != 0:
                    raise RuntimeError(f"SCC_MoveRelative failed (error {ret})")
                _log(sn_bytes, f"move_relative — SCC_MoveRelative accepted ({dev.value} dev units), waiting")
                _wait_motion_complete(lib, sn, timeout_ms)
                _log(sn_bytes, "move_relative — complete")
            except Exception as exc:
                _log(sn_bytes, f"move_relative — error: {exc}")
                with self._lock:
                    self._last_error = str(exc)

        threading.Thread(target=_run, daemon=True).start()

    def move_to(self, pos_mm: float, timeout_ms: int = 60_000):
        """Move to absolute position pos_mm in a daemon thread; returns immediately."""
        if not self._connected:
            return
        _log(self._serial, f"move_to {pos_mm:.4f} mm — issuing SCC_MoveToPosition")
        self._pre_command()
        sn_bytes = self._serial
        lib      = self._lib

        def _run():
            sn = c_char_p(sn_bytes)
            try:
                dev = c_int()
                ret = lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(pos_mm), byref(dev), c_int(_UNIT_DISTANCE)
                )
                if ret != 0:
                    raise RuntimeError(
                        f"SCC_GetDeviceUnitFromRealValue failed (error {ret})"
                    )
                ret = lib.SCC_MoveToPosition(sn, dev)
                if ret != 0:
                    raise RuntimeError(f"SCC_MoveToPosition failed (error {ret})")
                _log(sn_bytes, f"move_to — SCC_MoveToPosition accepted ({dev.value} dev units), waiting")
                _wait_motion_complete(lib, sn, timeout_ms)
                _log(sn_bytes, "move_to — complete")
            except Exception as exc:
                _log(sn_bytes, f"move_to — error: {exc}")
                with self._lock:
                    self._last_error = str(exc)

        threading.Thread(target=_run, daemon=True).start()

    def jog(self, direction: str):
        """Start a jog in direction '+' or '-'. Call stop() to end motion."""
        if not self._connected:
            return
        _log(self._serial, f"jog {direction}")
        self._pre_command()
        sn      = c_char_p(self._serial)
        jog_dir = c_short(_DIR_FORWARDS if direction == "+" else _DIR_BACKWARDS)
        self._lib.SCC_MoveJog(sn, jog_dir)

    def move_continuous(self, direction: str):
        """
        Run at the current max velocity in direction '+' or '-' until stop() is called.
        Uses SCC_MoveAtVelocity (main velocity profile, not jog profile).
        """
        if not self._connected:
            return
        _log(self._serial, f"move_continuous {direction} — SCC_MoveAtVelocity")
        self._pre_command()
        sn  = c_char_p(self._serial)
        trd = c_short(_DIR_FORWARDS if direction == "+" else _DIR_BACKWARDS)
        self._lib.SCC_MoveAtVelocity(sn, trd)

    def stop(self, immediate: bool = False):
        """Stop all motion. immediate=True uses SCC_StopImmediate (may lose position)."""
        if not self._connected:
            return
        kind = "immediate" if immediate else "profiled"
        _log(self._serial, f"stop ({kind})")
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
        _log(self._serial, f"set_velocity_params max_vel={max_velocity} accel={acceleration}")
        self._pre_command()
        sn = c_char_p(self._serial)
        try:
            cur_accel = c_int()
            cur_vel   = c_int()
            self._lib.SCC_GetVelParams(sn, byref(cur_accel), byref(cur_vel))

            if acceleration is not None:
                dev = c_int()
                self._lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(acceleration), byref(dev), c_int(_UNIT_ACCELERATION)
                )
                cur_accel = dev
            if max_velocity is not None:
                dev = c_int()
                self._lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(max_velocity), byref(dev), c_int(_UNIT_VELOCITY)
                )
                cur_vel = dev

            self._lib.SCC_SetVelParams(sn, cur_accel, cur_vel)
            with self._lock:
                self._velocity_params = _read_velocity_params(self._lib, sn)
            _log(self._serial, f"set_velocity_params — done: {self._velocity_params}")
        except Exception as exc:
            _log(self._serial, f"set_velocity_params — error: {exc}")
            with self._lock:
                self._last_error = str(exc)

    def set_jog_params(
        self,
        step_size:    float | None = None,
        max_velocity: float | None = None,
        acceleration: float | None = None,
    ):
        """Set jog profile. Values in mm, mm/s, mm/s²."""
        if not self._connected:
            return
        _log(self._serial,
             f"set_jog_params step={step_size} max_vel={max_velocity} accel={acceleration}")
        self._pre_command()
        sn = c_char_p(self._serial)
        try:
            cur_accel = c_int()
            cur_vel   = c_int()
            self._lib.SCC_GetJogVelParams(sn, byref(cur_accel), byref(cur_vel))
            cur_step  = int(self._lib.SCC_GetJogStepSize(sn))

            if step_size is not None:
                dev = c_int()
                self._lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(step_size), byref(dev), c_int(_UNIT_DISTANCE)
                )
                cur_step = dev.value
            if acceleration is not None:
                dev = c_int()
                self._lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(acceleration), byref(dev), c_int(_UNIT_ACCELERATION)
                )
                cur_accel = dev
            if max_velocity is not None:
                dev = c_int()
                self._lib.SCC_GetDeviceUnitFromRealValue(
                    sn, c_double(max_velocity), byref(dev), c_int(_UNIT_VELOCITY)
                )
                cur_vel = dev

            self._lib.SCC_SetJogStepSize(sn, c_uint(cur_step))
            self._lib.SCC_SetJogVelParams(sn, cur_accel, cur_vel)
            with self._lock:
                self._jog_params = _read_jog_params(self._lib, sn)
            _log(self._serial, f"set_jog_params — done: {self._jog_params}")
        except Exception as exc:
            _log(self._serial, f"set_jog_params — error: {exc}")
            with self._lock:
                self._last_error = str(exc)

    def set_backlash(self, value_mm: float):
        """Set backlash compensation distance in mm."""
        if not self._connected:
            return
        _log(self._serial, f"set_backlash {value_mm:.4f} mm")
        self._pre_command()
        sn = c_char_p(self._serial)
        try:
            dev = c_int()
            self._lib.SCC_GetDeviceUnitFromRealValue(
                sn, c_double(value_mm), byref(dev), c_int(_UNIT_DISTANCE)
            )
            self._lib.SCC_SetBacklash(sn, c_long(dev.value))
            with self._lock:
                self._gen_move_params = _read_gen_move_params(self._lib, sn)
            _log(self._serial, f"set_backlash — done: {self._gen_move_params}")
        except Exception as exc:
            _log(self._serial, f"set_backlash — error: {exc}")
            with self._lock:
                self._last_error = str(exc)
