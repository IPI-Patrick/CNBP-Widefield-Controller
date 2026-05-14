"""
KST101 driver integration test.
Connects to X/Y/Z motors, verifies relative moves via position readback,
and tests continuous movement + stop.

Run from the repo root:
    python test_kst101.py
"""

import sys
import os
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from Drivers.KST101 import KST101

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AXES = {
    "X": "26000004",
    "Y": "26000005",
    "Z": "26000006",
}
MOVE_MM            = 1.0    # relative move distance (mm)
TOLERANCE_MM       = 0.05   # acceptable position error after move
CONTINUOUS_TIME_S  = 2.0    # how long to run move_continuous before stopping
MOTION_TIMEOUT_S   = 30.0   # max time to wait for a move to complete


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(msg):
    print(f"  [PASS] {msg}")

def _fail(msg):
    print(f"  [FAIL] {msg}")

def _info(msg):
    print(f"         {msg}")


def wait_for_idle(motor: KST101, label: str, timeout: float = MOTION_TIMEOUT_S) -> dict:
    """
    Poll snapshot() until the motor leaves a moving/homing state.
    First waits up to 1 s for motion to start (guards against SDK propagation delay),
    then waits until it stops.
    """
    moving_states = {"Moving", "Homing", "Jogging"}

    # Wait for motion to start (simulator can have ~1 s propagation delay)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0:
        if motor.snapshot()["state"] in moving_states:
            break
        time.sleep(0.05)

    # Wait for motion to finish
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


# ---------------------------------------------------------------------------
# Per-axis test
# ---------------------------------------------------------------------------

def test_axis(label: str, serial: str) -> bool:
    print(f"\n{'-'*54}")
    print(f"  {label} axis  (serial {serial})")
    print(f"{'-'*54}")

    motor = KST101()

    # ── Connect ────────────────────────────────────────────────
    print("  Connecting...")
    motor.connect(serial)
    snap = motor.snapshot()
    if not motor.connected:
        _fail(f"connect() failed: {snap['last_error']}")
        return False
    _ok(f"Connected  state={snap['state']}  pos={snap['position']}")

    pos_initial = snap["position"]
    if pos_initial is None:
        _fail("position is None after connect")
        motor.disconnect()
        return False

    # ── Relative move forward ───────────────────────────────────
    print(f"\n  Relative move: +{MOVE_MM} mm")
    motor.move_relative(MOVE_MM)
    time.sleep(0.3)   # give daemon thread time to start and capture any immediate error
    if motor.snapshot()["last_error"]:
        _fail(f"error after move_relative: {motor.snapshot()['last_error']}")
        motor.disconnect()
        return False
    try:
        snap = wait_for_idle(motor, label)
    except TimeoutError as e:
        _fail(str(e))
        motor.disconnect()
        return False

    pos_after = snap["position"]
    delta = pos_after - pos_initial
    _info(f"before={pos_initial:.4f} mm  after={pos_after:.4f} mm  delta={delta:+.4f} mm")

    if abs(delta - MOVE_MM) <= TOLERANCE_MM:
        _ok(f"Moved {delta:+.4f} mm  (expected +{MOVE_MM}, tol +/-{TOLERANCE_MM})")
    else:
        _fail(f"Moved {delta:+.4f} mm  (expected +{MOVE_MM}, tol +/-{TOLERANCE_MM})")
        motor.disconnect()
        return False

    # ── Relative move back ─────────────────────────────────────
    print(f"\n  Relative move: -{MOVE_MM} mm (return)")
    motor.move_relative(-MOVE_MM)
    time.sleep(0.3)
    if motor.snapshot()["last_error"]:
        _fail(f"error on return move: {motor.snapshot()['last_error']}")
        motor.disconnect()
        return False
    try:
        snap = wait_for_idle(motor, label)
    except TimeoutError as e:
        _fail(str(e))
        motor.disconnect()
        return False
    _info(f"Returned to {snap['position']:.4f} mm")

    # ── Continuous move ────────────────────────────────────────
    print(f"\n  Continuous move '+' for {CONTINUOUS_TIME_S} s then stop")
    time.sleep(0.5)   # let simulator fully settle after return move before issuing next command
    pos_before_cont = motor.snapshot()["position"]
    motor.move_continuous("+")
    time.sleep(CONTINUOUS_TIME_S)
    motor.stop(immediate=True)

    # Wait for the motor to actually stop before reading position or disconnecting.
    # If we disconnect while the simulator is still moving, the motor runs to the
    # travel limit and subsequent runs fail with error 38.
    try:
        snap = wait_for_idle(motor, label, timeout=10.0)
    except TimeoutError as e:
        _fail(str(e))
        motor.disconnect()
        return False

    pos_after_cont = snap["position"]
    delta_cont = pos_after_cont - pos_before_cont
    _info(f"before={pos_before_cont:.4f} mm  after={pos_after_cont:.4f} mm  delta={delta_cont:+.4f} mm")

    if abs(delta_cont) > 0.05:
        _ok(f"Continuous: moved {delta_cont:+.4f} mm and stopped")
    else:
        _fail(f"Continuous: motor barely moved ({delta_cont:.4f} mm)")
        motor.disconnect()
        return False

    # ── Disconnect ─────────────────────────────────────────────
    motor.disconnect()
    _ok("Disconnected cleanly")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("KST101 Driver Test")
    print(f"{'='*54}")

    print("Initialising Kinesis Simulator...")
    KST101.enable_simulations()

    discovered = KST101.list_devices()
    print(f"Discovered devices: {discovered}")
    missing = [s for s in AXES.values() if s not in discovered]
    if missing:
        print(f"[WARN] Not found in device list: {missing}")
        print("       (will still attempt to connect)\n")

    results: dict[str, bool] = {}
    for label, serial in AXES.items():
        try:
            results[label] = test_axis(label, serial)
        except Exception:
            _fail(f"{label}: unhandled exception")
            traceback.print_exc()
            results[label] = False

    print(f"\n{'='*54}")
    print("Summary")
    print(f"{'='*54}")
    for label, passed in results.items():
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {label}  ({AXES[label]})")

    all_passed = all(results.values())
    print(f"\n{'All tests passed!' if all_passed else 'Some tests FAILED.'}")

    KST101.disable_simulations()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
