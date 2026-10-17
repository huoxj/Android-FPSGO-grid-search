"""Warm-up: play a replay to push the device onto its thermal plateau.

Why the game and not a CPU burn: the ready thresholds were calibrated AT
the game's own steady state (battery ~42C), and a throttled CPU-only burn
equilibrates BELOW them (measured 2026-10-17 evening: battery plateau
41.1C / tsx 45.3C after 25 min - it can never clear 42/48 because the
governor clamps the burn cores as the device heats). The game itself
(CPU+GPU+screen at 5-6W) reaches the plateau in minutes, is exactly the
workload's thermal profile, and needs no synthetic-load machinery.

Fixed warmup duration rather than "until thresholds exceeded": the game's
tsx/battery equilibria only barely clear the thresholds, so a
threshold-based exit would be fragile in the same way. The ready gate
after the coast does the fine positioning.

Failure mode: if the host dies mid-warmup the game keeps running until
its replay ends and idles in the menu - no device-side daemons involved,
but also no auto-cleanup; the next host run force-stops the game anyway.
"""
from time import sleep

from config import get_config
from utils import device_utils as du
from sgame import _prepare_resources, _reenter_replay, _exit_game


def warm_and_ready() -> None:
    """Heat the device to its plateau by playing replay card 0."""
    conf = get_config()
    if conf.power.warmup_secs <= 0:
        return

    # Screen on + full brightness: part of the workload's thermal profile
    du.toggle_screen(True)
    du.screen_birghtness(1.0)

    try:
        _prepare_resources()
        # Card 0: any replay works as a heater; using the same entry path
        # as real runs means identical, already-proven UI automation.
        # FPSGO params stay at game defaults (per-pid state died with the
        # previous run's force-stop) - we only want heat here.
        _reenter_replay(0)
        print(f"warmup: playing replay 0 for {conf.power.warmup_secs}s")
        sleep(conf.power.warmup_secs)
    finally:
        _exit_game()
