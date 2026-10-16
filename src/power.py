"""Software charge-cut for power measurement (X6873, TC ODM charger).

`echo 0 > /proc/tc_charger_cmd/en_power_path` cuts the charge path; the
battery then carries the full load, so `batt.power_mw` from the trace is
the whole-device power. Verified equivalent to physical unplug (residual
< measurement noise), see knowledge-wiki x6873-charging-control.

Lifecycle per run:
    wait_for_pe5_drained -> cut -> verify -> <run> -> restore -> recheck
"""
from time import monotonic, sleep

from utils.adb import adb_shell

EN_POWER_PATH = "/proc/tc_charger_cmd/en_power_path"
CURRENT_NOW = "/sys/class/power_supply/battery/current_now"

# PE5 fast-charge sessions refuse en_power_path writes; must drain to 0.
# While draining, writes fail loudly (dmesg spams), so 30s of silence is
# enough to confirm the session has ended.
PE5_WAIT_MAX = 180
PE5_SETTLE = 30
PE5_RECHECK_GAP = 10

WRITE_RETRIES = 3
WRITE_RETRY_GAP = 10

# After restore-write, the HAL may need a moment to bring charging back up.
RESTORE_RECHECK_MAX = 60

# Negative current => battery discharging => charge path is cut for real.
CUT_VERIFY_CURRENT_MAX = 0
CUT_VERIFY_SETTLE = 5


def _read_int(path: str) -> int:
    raw = adb_shell(f"cat {path}").strip()
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"Cannot read {path}: {raw!r} (adb alive?)") from e


def _pe5_sessions() -> int:
    return int(adb_shell('dmesg | grep -c "pe5 is runing"').strip() or 0)


def _wait_for_pe5_drained():
    t0 = monotonic()
    while monotonic() - t0 < PE5_WAIT_MAX:
        if _pe5_sessions() == 0:
            # dmesg keeps PE5 lines for the session lifetime; a short
            # silence window guards against a session ending mid-check.
            sleep(PE5_SETTLE)
            if _pe5_sessions() == 0:
                return
        print(f"PE5 session active, waiting... "
              f"({monotonic() - t0:.0f}s/{PE5_WAIT_MAX}s)")
        sleep(PE5_RECHECK_GAP)
    raise TimeoutError(f"PE5 session did not drain within {PE5_WAIT_MAX}s")


def cut_for_measurement():
    """Block until the charge path is cut and verified. Raises on failure."""
    _wait_for_pe5_drained()
    for attempt in range(1, WRITE_RETRIES + 1):
        adb_shell(f"echo 0 > {EN_POWER_PATH}")
        sleep(CUT_VERIFY_SETTLE)
        cur = _read_int(CURRENT_NOW)
        if cur <= CUT_VERIFY_CURRENT_MAX:
            print(f"Charge cut verified: current_now = {cur}")
            return
        print(
            f"Cut attempt {attempt}/{WRITE_RETRIES} failed: "
            f"current_now = {cur} (not discharging)"
        )
        sleep(WRITE_RETRY_GAP)
    raise RuntimeError(
        f"Charge cut failed after {WRITE_RETRIES} attempts; "
        "power measurement would be garbage"
    )


def restore_charging():
    """Restore charging; raises if the HAL refuses."""
    for attempt in range(1, WRITE_RETRIES + 1):
        adb_shell(f"echo 1 > {EN_POWER_PATH}")
        sleep(2)
        if _read_int(CURRENT_NOW) > 0:
            print("Charging restored")
            return
        print(f"Restore attempt {attempt}/{WRITE_RETRIES} failed")
        sleep(WRITE_RETRY_GAP)
    raise RuntimeError("Failed to restore charging - check the device")


def recheck_cut_state() -> bool:
    """Mid-run sanity probe: is the cut still in effect?"""
    return _read_int(CURRENT_NOW) <= CUT_VERIFY_CURRENT_MAX
