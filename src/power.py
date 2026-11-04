"""Software charge-cut for power measurement (X6873, TC ODM charger).

`echo 0 > /proc/tc_charger_cmd/en_power_path` cuts the charge path; the
battery then carries the full load, so `batt.power_mw` from the trace is
the whole-device power. Verified equivalent to physical unplug (residual
< measurement noise), see knowledge-wiki x6873-charging-control.

Lifecycle per run:
    wait_for_pe5_drained -> cut -> verify -> <run> -> restore -> recheck
"""
from time import sleep

from errors import Kind
from utils.adb import adb_shell
from utils.wait import wait_until

EN_POWER_PATH = "/proc/tc_charger_cmd/en_power_path"
CURRENT_NOW = "/sys/class/power_supply/battery/current_now"

# PE5 fast-charge sessions refuse en_power_path writes, so the cut must wait
# for the session to end. The driver logs the refusal while a session is live,
# so a stretch of quiet polls means it is over (4 polls 10s apart ~= the 30s
# silence window this used to sleep for).
PE5_WAIT_MAX = 180
PE5_RECHECK_GAP = 10
PE5_QUIET_POLLS = 4

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
    wait_until(
        "PE5 fast-charge session to end",
        lambda: _pe5_sessions() == 0,
        PE5_WAIT_MAX,
        interval=PE5_RECHECK_GAP,
        consecutive=PE5_QUIET_POLLS,
        on_miss=lambda elapsed: print(
            f"PE5 session active, waiting... "
            f"({elapsed:.0f}s/{PE5_WAIT_MAX}s)"),
        kind=Kind.TRANSIENT,
    )


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
    """Restore charging; raises if the battery still is not taking current.

    Runs with the screen off (main turns it off before calling this): a lit
    screen draws more than a 5V/0.5A port supplies, so the battery current
    stays <= 0 and a restored path looks like a failure.
    """
    for attempt in range(1, WRITE_RETRIES + 1):
        adb_shell(f"echo 1 > {EN_POWER_PATH}")
        sleep(2)
        cur = _read_int(CURRENT_NOW)
        if cur > CUT_VERIFY_CURRENT_MAX:
            print(f"Charging restored: current_now = {round(cur / 1000)}mA")
            return
        print(f"Restore attempt {attempt}/{WRITE_RETRIES} failed: "
              f"current_now = {round(cur / 1000)}mA")
        sleep(WRITE_RETRY_GAP)
    raise RuntimeError(
        "Failed to restore charging - battery current stays <= 0; "
        "check the charger/port (a weak 5V port cannot cover the device)"
    )


def recheck_cut_state() -> bool:
    """Mid-run sanity probe: is the cut still in effect?"""
    return _read_int(CURRENT_NOW) <= CUT_VERIFY_CURRENT_MAX
