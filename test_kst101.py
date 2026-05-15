"""
KST101 driver integration test.

Exercises a single stepper motor and verifies that the driver:
- continuously polls position updates
- performs jog motion and stop
- moves to an absolute target
- performs continuous motion and stop

Run from the repo root:
    python test_kst101.py

Optional environment variables:
    KST101_SERIAL=26001358
    KST101_USE_SIMULATOR=1
"""

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from Drivers.KST101 import KST101


MOVE_MM = 1.0
TOLERANCE_MM = 0.05
POLL_WINDOW_S = 2.0
POLL_SAMPLE_S = 0.1
JOG_TIME_S = 1.0
CONTINUOUS_TIME_S = 1.5
MOTION_TIMEOUT_S = 30.0
SETTLE_TIME_S = 0.5

BASE_VELOCITY_MM_S = 1.5
BASE_ACCEL_MM_S2 = 1.0
JOG_VELOCITY_MM_S = 0.25
JOG_ACCEL_MM_S2 = 0.1


def _ok(msg):
    print(f"  [PASS] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


def _info(msg):
    print(f"         {msg}")


def wait_for_motion_start(motor: KST101, label: str, timeout: float = 3.0) -> dict:
    moving_states = {"Moving", "Homing", "Jogging"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = motor.snapshot()
        if snap["state"] in moving_states:
            return snap
        time.sleep(0.05)
    snap = motor.snapshot()
    raise TimeoutError(
        f"{label}: motion never started  (state={snap['state']}, last_error={snap['last_error']})"
    )


def wait_for_idle(motor: KST101, label: str, timeout: float = MOTION_TIMEOUT_S) -> dict:
    moving_states = {"Moving", "Homing", "Jogging"}
    wait_for_motion_start(motor, label)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = motor.snapshot()
        if snap["state"] not in moving_states:
            return snap
        time.sleep(0.1)

    snap = motor.snapshot()
    raise TimeoutError(
        f"{label}: still '{snap['state']}' after {timeout:.0f} s  "
        f"(last_error={snap['last_error']})"
    )


def assert_no_error(motor: KST101, context: str) -> bool:
    snap = motor.snapshot()
    if snap["last_error"]:
        _fail(f"{context}: {snap['last_error']}")
        return False
    return True


def sample_positions(motor: KST101, duration_s: float, interval_s: float) -> list[float]:
    samples: list[float] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        pos = motor.snapshot().get("position")
        if pos is not None:
            samples.append(float(pos))
        time.sleep(interval_s)
    return samples


def check_position_polling(motor: KST101) -> bool:
    print(f"\n  Verifying position polling for {POLL_WINDOW_S:.1f} s")
    samples = sample_positions(motor, POLL_WINDOW_S, POLL_SAMPLE_S)
    if len(samples) < 3:
        _fail(f"Expected repeated position samples, got {len(samples)}")
        return False
    spread = max(samples) - min(samples)
    _info(
        f"samples={len(samples)}  first={samples[0]:.4f} mm  last={samples[-1]:.4f} mm  span={spread:.4f} mm"
    )
    _ok("Position polling is producing repeated snapshots")
    return True


def choose_motion_direction(snap: dict) -> str:
    if snap.get("ccw_limit"):
        return "+"
    if snap.get("cw_limit"):
        return "-"
    start_pos = float(snap.get("position") or 0.0)
    return "-" if start_pos > MOVE_MM else "+"


def ensure_homed(motor: KST101) -> bool:
    snap = motor.snapshot()
    if snap.get("homed"):
        _info("Device already homed")
        return True

    print("\n  Homing device before motion tests")
    _info(
        f"pre-home state={snap.get('state')} status_bits=0x{int(snap.get('status_bits') or 0):08X} "
        f"cw_limit={snap.get('cw_limit')} ccw_limit={snap.get('ccw_limit')}"
    )
    motor.home()

    try:
        snap = wait_for_idle(motor, "home", timeout=60.0)
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    if not assert_no_error(motor, "home error"):
        return False

    if not snap.get("homed"):
        _fail(
            "home completed without HOMED status: "
            f"state={snap.get('state')} status_bits=0x{int(snap.get('status_bits') or 0):08X}"
        )
        return False

    _info(
        f"post-home pos={float(snap['position']):.4f} mm status_bits=0x{int(snap.get('status_bits') or 0):08X}"
    )
    _ok("Device homed")
    return True


def print_motion_profiles(motor: KST101, label: str) -> None:
    snap = motor.snapshot()
    _info(f"{label} velocity_params={snap.get('velocity_params')}")
    _info(f"{label} jog_params={snap.get('jog_params')}")


def test_profile_readback_and_setters(motor: KST101) -> bool:
    print("\n  Reading and setting velocity/jog profiles")
    print_motion_profiles(motor, "initial")

    motor.set_velocity_params(
        max_velocity=BASE_VELOCITY_MM_S,
        acceleration=BASE_ACCEL_MM_S2,
    )
    motor.set_jog_params(
        max_velocity=JOG_VELOCITY_MM_S,
        acceleration=JOG_ACCEL_MM_S2,
    )
    time.sleep(0.3)

    snap = motor.snapshot()
    print_motion_profiles(motor, "updated")

    vp = snap.get("velocity_params", {})
    jp = snap.get("jog_params", {})
    if not vp or not jp:
        _fail("velocity/jog params missing after setter calls")
        return False

    vel_ok = abs(float(vp.get("max_velocity", 0.0)) - BASE_VELOCITY_MM_S) <= 0.01
    acc_ok = abs(float(vp.get("acceleration", 0.0)) - BASE_ACCEL_MM_S2) <= 0.01
    jog_vel_ok = abs(float(jp.get("max_velocity", 0.0)) - JOG_VELOCITY_MM_S) <= 0.01
    jog_acc_ok = abs(float(jp.get("acceleration", 0.0)) - JOG_ACCEL_MM_S2) <= 0.01
    raw_present = all(
        key in vp for key in ("raw_max_velocity", "raw_acceleration")
    ) and all(
        key in jp for key in ("raw_max_velocity", "raw_acceleration", "raw_step_size")
    )
    if vel_ok and acc_ok and jog_vel_ok and jog_acc_ok and raw_present:
        _ok("velocity and jog setters/readback are consistent")
        return True

    _fail(
        "velocity/jog setter readback mismatch: "
        f"vp={vp} jp={jp}"
    )
    return False


def test_move_to(motor: KST101, start_pos: float, direction: str) -> bool:
    target = start_pos + (MOVE_MM if direction == "+" else -MOVE_MM)
    print(f"\n  Absolute move_to: {target:.4f} mm")
    motor.move_to(target)
    time.sleep(0.3)
    if not assert_no_error(motor, "move_to error"):
        return False

    try:
        snap = wait_for_idle(motor, "move_to")
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    actual = float(snap["position"])
    delta = actual - target
    _info(f"target={target:.4f} mm  actual={actual:.4f} mm  error={delta:+.4f} mm")
    if abs(delta) <= TOLERANCE_MM:
        _ok("move_to reached target")
        return True

    _fail(f"move_to missed target by {delta:+.4f} mm")
    return False


def test_jog(motor: KST101, direction: str) -> bool:
    print(f"\n  Jog '{direction}' for {JOG_TIME_S:.1f} s then stop")
    before = float(motor.snapshot()["position"])
    motor.jog(direction)

    try:
        wait_for_motion_start(motor, "jog")
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    during = sample_positions(motor, JOG_TIME_S, POLL_SAMPLE_S)
    motor.stop(immediate=False)

    try:
        snap = wait_for_idle(motor, "jog stop", timeout=10.0)
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    if not assert_no_error(motor, "jog stop error"):
        return False

    after = float(snap["position"])
    delta = after - before
    spread = (max(during) - min(during)) if during else 0.0
    _info(
        f"before={before:.4f} mm  after={after:.4f} mm  delta={delta:+.4f} mm  in-motion span={spread:.4f} mm"
    )
    if spread > 0.05 and abs(delta) > 0.05:
        _ok("jog moved and position updated while in motion")
        return True

    _fail("jog did not show meaningful in-motion position updates")
    return False


def test_move_continuous(motor: KST101, direction: str) -> bool:
    print(f"\n  Continuous move '{direction}' for {CONTINUOUS_TIME_S:.1f} s then stop")
    before = float(motor.snapshot()["position"])
    motor.move_continuous(direction)

    try:
        wait_for_motion_start(motor, "move_continuous")
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    during = sample_positions(motor, CONTINUOUS_TIME_S, POLL_SAMPLE_S)
    motor.stop(immediate=False)

    try:
        snap = wait_for_idle(motor, "move_continuous stop", timeout=10.0)
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    if not assert_no_error(motor, "move_continuous stop error"):
        return False

    after = float(snap["position"])
    delta = after - before
    spread = (max(during) - min(during)) if during else 0.0
    _info(
        f"before={before:.4f} mm  after={after:.4f} mm  delta={delta:+.4f} mm  in-motion span={spread:.4f} mm"
    )
    if spread > 0.05 and abs(delta) > 0.05:
        _ok("move_continuous moved and position updated while in motion")
        return True

    _fail("move_continuous did not show meaningful in-motion position updates")
    return False


def return_to_start(motor: KST101, start_pos: float) -> bool:
    print(f"\n  Returning to start: {start_pos:.4f} mm")
    motor.move_to(start_pos)
    time.sleep(0.3)
    if not assert_no_error(motor, "return move_to error"):
        return False

    try:
        snap = wait_for_idle(motor, "return_to_start")
    except TimeoutError as exc:
        _fail(str(exc))
        return False

    pos = float(snap["position"])
    err = pos - start_pos
    _info(f"target={start_pos:.4f} mm  actual={pos:.4f} mm  error={err:+.4f} mm")
    if abs(err) <= TOLERANCE_MM:
        _ok("Returned to start position")
        return True

    _fail(f"Failed to return to start position by {err:+.4f} mm")
    return False


def resolve_serial() -> str:
    configured = os.environ.get("KST101_SERIAL", "").strip()
    if configured:
        return configured

    discovered = KST101.list_devices()
    print(f"Discovered devices: {discovered}")
    if not discovered:
        raise RuntimeError("No KST101 devices discovered")
    return discovered[0]


def should_use_simulator() -> bool:
    value = os.environ.get("KST101_USE_SIMULATOR", "1").strip().lower()
    return value not in {"0", "false", "no"}


def main() -> int:
    print("KST101 Driver Test")
    print(f"{'=' * 54}")

    use_simulator = should_use_simulator()
    if use_simulator:
        print("Initialising Kinesis Simulator...")
        KST101.enable_simulations()

    motor = KST101()
    try:
        serial = resolve_serial()
        print(f"\nTesting single motor  serial={serial}")

        print("  Connecting...")
        motor.connect(serial)
        snap = motor.snapshot()
        if not motor.connected:
            _fail(f"connect() failed: {snap['last_error']}")
            return 1

        if snap["position"] is None:
            _fail(f"position is None after connect  (last_error={snap['last_error']})")
            return 1

        _ok(f"Connected  state={snap['state']}  pos={float(snap['position']):.4f} mm")
        _info(
            f"status_bits=0x{int(snap.get('status_bits') or 0):08X} "
            f"homed={snap.get('homed')} cw_limit={snap.get('cw_limit')} ccw_limit={snap.get('ccw_limit')}"
        )

        if not ensure_homed(motor):
            return 1

        snap = motor.snapshot()
        start_pos = float(snap["position"])
        direction = choose_motion_direction(snap)
        _info(f"Using test motion direction '{direction}'")

        checks = [
            check_position_polling(motor),
            test_profile_readback_and_setters(motor),
            test_move_to(motor, start_pos, direction),
            test_jog(motor, direction),
            test_move_continuous(motor, direction),
            return_to_start(motor, start_pos),
        ]

        all_passed = all(checks)
        print(f"\n{'=' * 54}")
        print("Summary")
        print(f"{'=' * 54}")
        print(f"  [{'PASS' if all_passed else 'FAIL'}] serial={serial}")
        print(f"\n{'All tests passed!' if all_passed else 'Some tests FAILED.'}")
        return 0 if all_passed else 1

    except Exception:
        _fail("Unhandled exception during KST101 test")
        traceback.print_exc()
        return 1
    finally:
        if motor.connected:
            time.sleep(SETTLE_TIME_S)
            motor.disconnect()
            _ok("Disconnected cleanly")
        if use_simulator:
            KST101.disable_simulations()


if __name__ == "__main__":
    sys.exit(main())
