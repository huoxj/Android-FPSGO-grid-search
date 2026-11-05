"""Warm-up: replay loop until every thermal zone is above its threshold.

Why the replay and not a CPU burn: the replay is the workload's own thermal
profile (CPU+GPU+screen). A CPU-only burn concentrates the heat in the SoC
sensor and never lifts the battery/tsx zones - measured 2026-10-31 with
stress-ng matrixprod on 8 cores, screen on: z0 74-81C while z44/z53 stayed
flat at 44.5/38.9C (the game reaches 65-68 / 47-49 / 43-44C).

One replay is often not enough heat, so the loop restarts the game and
re-enters the replay after run_duration/4 of playback at 4x speed (two blind
taps on the in-fight speed button) and keeps going until every zone has been
ABOVE its ready threshold for 3 consecutive samples (10s apart), bounded by
warmup_secs.

Termination is guaranteed on both paths. The cap is checked before every entry
attempt; before, it was only checked inside the playback loop that a failed
entry never reached, so a persistent entry failure slept 960s forever.

Side effects are the screen and the game: the game is force-stopped and the
screen turned off on the way out - also on Ctrl-C or any exception. Leaving the
screen lit makes the ready gate cool a lit, charging device and throttles the
next charge wait.
"""
from time import monotonic, sleep
import logging

from config import get_config
from errors import TransientError
import utils.device_utils as du
from utils import ui
from utils.log import STATUS_LOG_EVERY, status
from sgame import (
    RestLockout, _prepare_resources, _reenter_replay, _exit_game, _sync_timeline_0s
)

log = logging.getLogger(__name__)

WARMUP_POLL = 10      # seconds between temperature samples
CONFIRM_SAMPLES = 3   # consecutive above-threshold samples == hot enough

# 4x playback: the speed button only shows once the fight has rolled a bit,
# its picker then needs one more tap on the 4x entry. Coordinates are the
# measured ones on the 2720x1224 device, like every other fixed tap.
PLAYBACK_RATE = 4
SPEED_MENU_DELAY = 10         # secs of playback before the button is up
SPEED_BUTTON = (1880, 1150)   # opens the speed picker
SPEED_4X = (1880, 1090)       # picks 4x

# Play-session limiter ("强制休息" / anti-addiction lockouts) blocks the game for
# minutes. Retrying on a 15s*2^n ladder burns attempts without giving the
# lockout time to expire - wait one cooldown out instead. Keyed exceptions
# would need banner matching we do not have, so ANY entry failure counts as a
# possible lockout; a second one in a row is not a lockout, so warmup gives the
# run up instead of cooling down forever.
LOCKOUT_COOLDOWN = 15 * 60 + 60  # 15-min lock + margin
MAX_ENTRY_FAILURES = 2

# Entry failures a cooldown cannot fix: missing template, replay card index
# outside the visible grid, malformed config, and programming errors - a
# TypeError/AttributeError is never a play-session lockout. Swallowing those as
# "assume lockout" hides a real bug behind a 16-minute sleep.
_NO_COOLDOWN = (FileNotFoundError, KeyError, ValueError,
                TypeError, AttributeError, NameError)


def warm_and_ready() -> None:
    """Loop replays until every thermal zone is above its ready threshold."""
    conf = get_config()
    if conf.power.warmup_secs <= 0:
        return

    # Screen on + full brightness: part of the workload's thermal profile
    du.toggle_screen(True)
    du.screen_birghtness(1.0)
    try:
        _burn(conf)
    finally:
        _exit_game()
        du.toggle_screen(False)


def _burn(conf) -> None:
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
        nonlocal last_logged
        line = (
            "warmup "
            + " ".join(
                f"z{z}={temps[z] / 1000:.1f}/{t / 1000:.1f}C"
                for z, t in sorted(thresholds.items())
            )
            + f" above={above}/{CONFIRM_SAMPLES} {elapsed:.0f}s"
        )
        status(line)
        if elapsed - last_logged >= STATUS_LOG_EVERY:
            last_logged = elapsed
            log.info("%s", line)

    # Fast path: back-to-back runs still sit on the plateau - no replay needed
    temps = temps_now()
    if above_all(temps):
        log.info("warmup: already hot enough, skipping replay %s",
                 " ".join(f"z{z}={temps[z] / 1000:.1f}C" for z in sorted(temps)))
        return

    _prepare_resources()
    t0 = monotonic()
    above = 0
    entry_failures = 0
    last_logged = -STATUS_LOG_EVERY    # log the first sample as well
    while True:
        elapsed = monotonic() - t0
        if elapsed >= conf.power.warmup_secs:
            log.info("warmup: cap %ds hit (before an entry), "
                     "releasing to the ready gate", conf.power.warmup_secs)
            return
        if entry_failures >= MAX_ENTRY_FAILURES:
            raise TransientError(
                f"warmup: {entry_failures} entry failures in a row "
                f"after {elapsed:.0f}s; this does not look like a lockout"
            )
        try:
            # Card 0: any replay works as a heater
            _reenter_replay(0)
            # Popups can fake "entered"; playback running is only proven
            # by the fight-start log needle the measured runs rely on.
            _sync_timeline_0s()
            # 4x playback: hotter load and a quarter of the wall time.
            sleep(SPEED_MENU_DELAY)
            ui.tap(*SPEED_BUTTON)
            sleep(1)
            ui.tap(*SPEED_4X)
        except RestLockout as e:
            # Health-system rest banner: a minutes-scale block, so waiting it
            # out is right. Everything else is a missed step, not a lockout.
            entry_failures += 1
            wait = min(LOCKOUT_COOLDOWN,
                       max(0, conf.power.warmup_secs - elapsed))
            log.warning("warmup: rest lockout (%s); cooling down %.0fs",
                        e, wait)
            sleep(wait)
            continue
        except _NO_COOLDOWN:
            raise
        except Exception as e:
            # A warm restart that lands back on the start screen, a missed
            # tap, a popup the templates do not know: retry the entry right
            # away instead of burning the warmup budget on a 11-minute nap.
            entry_failures += 1
            log.warning("warmup: entry failed (%s); retrying the entry", e)
            continue

        # Watch this replay for one run's worth of playback at 4x, then
        # restart into a fresh one - a single replay is often not enough
        # heat.
        burn_secs = conf.run_duration / PLAYBACK_RATE
        played = monotonic()
        while monotonic() - played < burn_secs:
            temps = temps_now()
            above = above + 1 if above_all(temps) else 0
            report(temps, monotonic() - t0, above)
            if above >= CONFIRM_SAMPLES:
                log.info("warmup: hot enough after %.0fs", monotonic() - t0)
                return
            if monotonic() - t0 >= conf.power.warmup_secs:
                log.info("warmup: cap %ds hit, releasing to the ready gate %s",
                         conf.power.warmup_secs,
                         " ".join(f"z{z}={temps[z] / 1000:.1f}C"
                                  for z in sorted(temps)))
                return
            sleep(WARMUP_POLL)

        log.info("warmup: replay played %.0fs at %dx, "
                 "restarting into a fresh one", burn_secs, PLAYBACK_RATE)
        _exit_game()
