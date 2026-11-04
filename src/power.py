"""Software charge-cut for power measurement (X6873, TC ODM charger).

`echo 0 > /proc/tc_charger_cmd/en_power_path` cuts the charge path; the
battery then carries the full load, so `batt.power_mw` from the trace is
the whole-device power. Verified equivalent to physical unplug (residual
< measurement noise), see knowledge-wiki x6873-charging-control.

Lifecycle per run:
    wait_for_pe5_drained -> cut -> verify -> <run> -> restore -> recheck
"""
from time import sleep
import logging
import threading

from errors import Kind, Suspend
from utils.adb import adb_shell
from utils.log import status
from utils.wait import wait_until

log = logging.getLogger(__name__)

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
        on_miss=lambda elapsed: status(
            f"waiting for the PE5 session to end... "
            f"{elapsed:.0f}s/{PE5_WAIT_MAX}s"),
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
            log.info("charge cut verified: current_now = %dmA",
                     round(cur / 1000))
            return
        log.warning("cut attempt %d/%d failed: current_now = %dmA "
                    "(still charging)", attempt, WRITE_RETRIES,
                    round(cur / 1000))
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
            log.info("charging restored: current_now = %dmA",
                     round(cur / 1000))
            return
        log.warning("restore attempt %d/%d failed: current_now = %dmA",
                    attempt, WRITE_RETRIES, round(cur / 1000))
        sleep(WRITE_RETRY_GAP)
    raise RuntimeError(
        "Failed to restore charging - battery current stays <= 0; "
        "check the charger/port (a weak 5V port cannot cover the device)"
    )


def recheck_cut_state() -> bool:
    """Mid-run sanity probe: is the cut still in effect?"""
    return _read_int(CURRENT_NOW) <= CUT_VERIFY_CURRENT_MAX


class CutWatch(threading.Thread):
    """Keep the charge path cut for as long as the run lasts.

    The cut is NOT sticky. The ODM charging state machine re-evaluates on its
    own and puts the power path back once it decides charging is allowed again
    (a hot battery blocks charging, cooling unblocks it). Measured 2026-11-04:
    cut verified at -138mA, then an 11-minute game run left the gauge at 100% -
    the phone was charged through the whole measurement, so batt.power_mw was
    the charger's share, not the device's draw.

    Samples the battery current every `interval` seconds and re-cuts the moment
    it turns positive. `rewrites` = re-cuts, `lost` = the driver refused, which
    means this run's power numbers are polluted.
    """

    def __init__(self, interval: int = 15):
        super().__init__(daemon=True)
        self.interval = interval
        self.rewrites = 0
        self.lost = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(self.interval):
            self.check()

    def check(self):
        try:
            cur = _read_int(CURRENT_NOW)
            if cur <= CUT_VERIFY_CURRENT_MAX or self._stop.is_set():
                return
            adb_shell(f"echo 0 > {EN_POWER_PATH}")
            sleep(CUT_VERIFY_SETTLE)
            after = _read_int(CURRENT_NOW)
        except Suspend:
            return          # losing the device is the main loop's problem
        except Exception as e:
            log.warning("cut watchdog: %s", e)
            return
        if after <= CUT_VERIFY_CURRENT_MAX:
            self.rewrites += 1
            log.warning("charge path re-opened mid-run (current_now %dmA); "
                        "re-cut to %dmA", round(cur / 1000), round(after / 1000))
        else:
            self.lost += 1
            log.error("charge path re-opened mid-run and refuses the cut "
                      "(current_now %dmA): this run's power numbers are "
                      "polluted", round(after / 1000))
