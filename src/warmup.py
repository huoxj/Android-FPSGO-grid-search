"""Warm-up: replay loop until every thermal zone is above its threshold.

Why the replay and not a CPU burn: the replay is the workload's own thermal
profile (CPU+GPU+screen). A CPU-only burn concentrates the heat in the SoC
sensor and never lifts the battery/tsx zones - measured 2026-10-31 with
stress-ng matrixprod on 8 cores, screen on: z0 74-81C while z44/z53 stayed
flat at 44.5/38.9C (the game reaches 65-68 / 47-49 / 43-44C).

One replay is often not enough heat, so the loop restarts the game and
re-enters the replay after run_duration of playback and keeps going until
every zone has been ABOVE its ready threshold for 3 consecutive samples
(10s apart), bounded by warmup_secs.

No side effects: nothing is written to the device (params belong to measured
runs) and the game is force-stopped on the way out - also on Ctrl-C or any
exception, so the loop can be interrupted at any moment.
"""
from time import monotonic, sleep

from config import get_config
import utils.device_utils as du
from sgame import _prepare_resources, _reenter_replay, _exit_game, _sync_timeline_0s

WARMUP_POLL = 10      # seconds between temperature samples
CONFIRM_SAMPLES = 3   # consecutive above-threshold samples == hot enough

# Play-session limiter ("强制休息" / anti-addiction lockouts) blocks the
# game for minutes. Retrying on the old 15s*2^n ladder burns attempts
# without giving the lockout time to expire - wait for it instead.
# Keyed exceptions would need template matching we do not have, so ANY
# entry failure inside warmup is treated as a possible lockout.
LOCKOUT_COOLDOWN = 15 * 60 + 60  # 15-min lock + margin


def warm_and_ready() -> None:
    """Loop replays until every thermal zone is above its ready threshold."""
    conf = get_config()
    if conf.power.warmup_secs <= 0:
        return

    # Screen on + full brightness: part of the workload's thermal profile
    du.toggle_screen(True)
    du.screen_birghtness(1.0)

    thresholds = {
        0: conf.ready_temp_soc_max,
        44: conf.ready_temp_tsx,
        53: conf.ready_temp_battery,
    }

    def temps_now() -> dict[int, int]:
        return {z: du.read_temp(z) for z in thresholds}

    def above_all(temps: dict[int, int]) -> bool:
        return all(temps[z] > t for z, t in thresholds.items())

    def report(temps: dict[int, int], elapsed: float, above: int) -> None:
        print(
            "warmup "
            + " ".join(
                f"z{z}={temps[z] / 1000:.1f}/{t / 1000:.1f}C"
                for z, t in sorted(thresholds.items())
            )
            + f" elapsed {elapsed:.0f}s above={above}/{CONFIRM_SAMPLES}"
        )

    # Fast path: back-to-back runs still sit on the plateau - no replay needed
    temps = temps_now()
    if above_all(temps):
        print(
            "warmup: already hot enough, skipping replay "
            + " ".join(f"z{z}={temps[z] / 1000:.1f}C" for z in sorted(temps))
        )
        return

    _prepare_resources()
    t0 = monotonic()
    above = 0
    try:
        while True:
            try:
                # Card 0: any replay works as a heater
                _reenter_replay(0)
                # Popups can fake "entered"; playback running is only proven
                # by the fight-start log needle the measured runs rely on.
                _sync_timeline_0s()
            except Exception as e:
                print(f"warmup: entry failed ({e}); "
                      f"cooling down {LOCKOUT_COOLDOWN}s for lockout")
                sleep(LOCKOUT_COOLDOWN)
                continue

            # Watch this replay for one run's worth of playback, then restart
            # into a fresh one - a single replay is often not enough heat.
            played = monotonic()
            while monotonic() - played < conf.run_duration:
                temps = temps_now()
                above = above + 1 if above_all(temps) else 0
                report(temps, monotonic() - t0, above)
                if above >= CONFIRM_SAMPLES:
                    print(f"warmup: hot enough after {monotonic() - t0:.0f}s")
                    return
                if monotonic() - t0 >= conf.power.warmup_secs:
                    print(
                        f"warmup: cap {conf.power.warmup_secs}s hit, "
                        "releasing to the ready gate "
                        + " ".join(f"z{z}={temps[z] / 1000:.1f}C"
                                   for z in sorted(temps))
                    )
                    return
                sleep(WARMUP_POLL)

            print(f"warmup: replay played {conf.run_duration}s, "
                  "restarting into a fresh one")
            _exit_game()
    finally:
        _exit_game()
