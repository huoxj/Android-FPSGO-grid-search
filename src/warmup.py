"""Warm-up: play a replay to push the device onto its thermal plateau.

Why the game and not a CPU burn: the ready thresholds were calibrated AT
the game's own steady state (battery ~42C), and a throttled CPU-only burn
equilibrates BELOW them (measured 2026-10-17 evening: battery plateau
41.1C / tsx 45.3C after 25 min - it can never clear 42/48 because the
governor clamps the burn cores as the device heats). The game itself
(CPU+GPU+screen at 5-6W) reaches the plateau in minutes, is exactly the
workload's thermal profile, and needs no synthetic-load machinery.

Exit rule: stop as soon as every zone sits above its ready threshold
(i.e. the device has reached the plateau the thresholds were calibrated
from), bounded by warmup_secs as a safety cap. Between consecutive runs
the device is still hot, so this usually exits in ~5 min instead of
burning the full cap.

Failure mode: if the host dies mid-warmup the game keeps running until
its replay ends and idles in the menu - no device-side daemons involved,
but also no auto-cleanup; the next host run force-stops the game anyway.
"""
from time import monotonic, sleep

from config import get_config
from utils import device_utils as du
from sgame import _prepare_resources, _reenter_replay, _exit_game, _sync_timeline_0s

WARMUP_POLL = 20


# Play-session limiter ("强制休息" / anti-addiction lockouts) blocks the
# game for minutes. Retrying on the old 15s*2^n ladder burns attempts
# without giving the lockout time to expire - wait for it instead.
# Keyed exceptions would need template matching we do not have, so ANY
# entry failure inside warmup is treated as a possible lockout.
LOCKOUT_COOLDOWN = 15 * 60 + 60  # 15-min lock + margin

class _EntryFailed(Exception):
    pass

def warm_and_ready() -> None:
    """Heat the device to its plateau by playing replay card 0."""
    conf = get_config()
    if conf.power.warmup_secs <= 0:
        return

    # Screen on + full brightness: part of the workload's thermal profile
    du.toggle_screen(True)
    du.screen_birghtness(1.0)

    # Fast path: between consecutive runs the device is still on the
    # plateau - check BEFORE spending a replay entry on heating.
    thresholds = {
        0: conf.ready_temp_soc_max,
        44: conf.ready_temp_tsx,
        53: conf.ready_temp_battery,
    }
    temps = {z: du.read_temp(z) for z in thresholds}
    if all(temps[z] >= t for z, t in thresholds.items()):
        print(
            "warmup: already on plateau, skipping replay "
            + " ".join(f"z{z}={temps[z] / 1000:.1f}C" for z in sorted(temps))
        )
        return

    try:
        _prepare_resources()
        # Card 0: any replay works as a heater; using the same entry path
        # as real runs means identical, already-proven UI automation.
        # FPSGO params stay at game defaults (per-pid state died with the
        # previous run's force-stop) - we only want heat here.
        while True:
            try:
                _reenter_replay(0)
                # Lobby/lockout/event popups can all fake "entered": the
                # battle actually running is only proven by the fight-start
                # log needle (same check the measured runs rely on). A
                # frozen replay never emits it -> cooldown + retry.
                _sync_timeline_0s()
                break
            except Exception as e:
                # Entry failed (forced-rest lockout, ad, misc popup):
                # a lockout is minutes-scale, retrying faster is pointless.
                # Wait out the longest known lockout, then try again.
                print(f"warmup: entry failed ({e}); "
                      f"cooling down {LOCKOUT_COOLDOWN}s for lockout")
                sleep(LOCKOUT_COOLDOWN)
        t0 = monotonic()
        while True:
            temps = {z: du.read_temp(z) for z in thresholds}
            if all(temps[z] >= t for z, t in thresholds.items()):
                print(
                    "warmup: plateau reached after "
                    f"{monotonic() - t0:.0f}s "
                    + " ".join(f"z{z}={temps[z] / 1000:.1f}C"
                               for z in sorted(temps))
                )
                return
            if monotonic() - t0 >= conf.power.warmup_secs:
                print(
                    f"warmup: cap {conf.power.warmup_secs}s hit, "
                    "releasing to the ready gate "
                    + " ".join(f"z{z}={temps[z] / 1000:.1f}C"
                               for z in sorted(temps))
                )
                return
            print(
                "warmup " +
                " ".join(
                    f"z{z}={temps[z] / 1000:.1f}/{t / 1000:.0f}C"
                    for z, t in sorted(thresholds.items())
                ) +
                f" elapsed {monotonic() - t0:.0f}s"
            )
            sleep(WARMUP_POLL)
    finally:
        _exit_game()
