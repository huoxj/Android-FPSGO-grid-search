"""Warm-up load: burn the device hot so the ready gate passes in tens of
minutes instead of hours of natural cooling from a cold start.

Why not run the game to warm up: game behavior is uncontrollable; a
burning load is slow but cheap on power and behaviorally deterministic.

Screen MUST be on at full brightness during the burn: screen-off clamps
cpu freq to 1.3GHz and the burn heat then never reaches tsx/battery
(measured 2026-10: tsx/battery saturated at ~28C vs climbing normally
with screen on). Screen/brightness are left on for the coast phase -
consistent with start_run_init, which asserts the same state per run.
"""
from time import monotonic, sleep

import subprocess

from config import get_config
from utils.adb import adb_shell
from utils import device_utils as du

# Prime cluster per cpuinfo_max_freq (0-3 little 2.2G, 4-6 big 3.2G,
# 7 prime 3.35G): burn ALL of it (cpu4-7 = bits 4-7 = 0xf0). Two cores
# alone saturate tsx/battery at ~28C - the heat never reaches the zones
# the ready gate checks (measured 2026-10).
# This device's toybox taskset only accepts a hex affinity MASK (-c is
# silently ignored, measured 2026-10).
WARM_CPUS_MASK = "f0"
BURN_SH = 'while :; do :; done'
BURN_N = 4  # one spinner per burned core

# Burn until every zone reaches its ready threshold. NO extra overshoot:
# the throttled 4-core burn's steady state sits just above the floors
# (measured 2026-10-17: battery ~42.7C vs ready 42, tsx ~48 oscillating,
# z0 84-86C thermally capped), so threshold+2K is unreachable and the
# burn would time out forever. The ready gate (below thresholds, twice)
# provides the coast-down margin instead.
BURN_OVERSHOOT_M = 0

BURN_POLL = 10
BURN_MAX = 60 * 60

# Host-held burner processes; never spawn device-side daemons here.
_burners: list[subprocess.Popen] = []


def start_burn():
    """Spawn one spinner per burned core via host-held adb sessions."""
    if _burners:
        return
    for _ in range(BURN_N):
        _burners.append(subprocess.Popen(
            ["adb", "shell", f"taskset {WARM_CPUS_MASK} sh -c '{BURN_SH}'"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))


def stop_burn():
    while _burners:
        p = _burners.pop()
        if not isinstance(p, subprocess.Popen):
            continue
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    # Belt and braces: kill any orphaned device-side spinner. The [w]hile
    # regex matches 'while' but not itself - pkill -f would otherwise kill
    # its own adb shell (cmdline carries the pattern) and drop the session.
    adb_shell("pkill -f '[w]hile :; do :; done'")


def warm_and_ready() -> None:
    """Burn until all zones reach their ready thresholds (no overshoot).

    The burn's steady state is the hot floor itself (see BURN_OVERSHOOT_M);
    after the burners die, coasting carries every zone down through its
    threshold and the ready gate passes with the run starting genuinely hot.

    Raises TimeoutError if the burn exceeds BURN_MAX (retry loop calls
    this again; the burn resumes from the already-warm state).
    """
    conf = get_config()
    thresholds = {
        0: conf.ready_temp_soc_max,
        44: conf.ready_temp_tsx,
        53: conf.ready_temp_battery,
    }
    targets = {z: t + BURN_OVERSHOOT_M for z, t in thresholds.items()}

    start_burn()
    try:
        # Screen on + full brightness: screen-off clamps the burn cores
        # to 1.3GHz and the heat never reaches tsx/battery (measured).
        # Left on for the coast/run; start_run_init asserts it anyway.
        du.toggle_screen(True)
        du.screen_birghtness(1.0)

        t0 = monotonic()
        while True:
            temps = {z: du.read_temp(z) for z in targets}
            if all(temps[z] >= targets[z] for z in targets):
                return
            if monotonic() - t0 > BURN_MAX:
                raise TimeoutError(
                    f"Burn phase exceeded {BURN_MAX}s; temps={temps}"
                )
            print(
                "warmup " +
                " ".join(
                    f"z{z}={temps[z] / 1000:.1f}/{targets[z] / 1000:.0f}C"
                    for z in sorted(targets)
                ) +
                f" elapsed {monotonic() - t0:.0f}s"
            )
            sleep(BURN_POLL)
    finally:
        stop_burn()
