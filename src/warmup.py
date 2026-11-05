"""Warm-up: replay loop until every thermal zone is above its threshold.

Why the replay and not a CPU burn: the replay is the workload's own thermal
profile (CPU+GPU+screen). A CPU-only burn concentrates the heat in the SoC
sensor and never lifts the battery/tsx zones - measured 2026-10-31 with
stress-ng matrixprod on 8 cores, screen on: z0 74-81C while z44/z53 stayed
flat at 44.5/38.9C (the game reaches 65-68 / 47-49 / 43-44C).

The burn is a fixed WARMUP_TIMELINE (600s of replay content) at 4x, NOT a
scaled copy of run_duration: warming up is a physical warm-up, it does not get
shorter because the measured run does.

One replay is not enough heat, and the old loop burned 40% of its budget on
cold starts (force-stop + game load + lobby + 6 taps = 73-85s of zero load,
measured 2026-11-05 from grid_search.log: 3-4 restarts per 13-minute warm-up).
The loop now rewinds INSIDE the running fight: five taps on the "back 60s"
button jump ~5 minutes of content back, the play speed is re-armed, and the
replay keeps rolling. A cold restart happens only when the fight is really
lost - the game process is gone, or nothing has warmed up for 3 loops (the
blind taps are not landing: popup, result screen, paused picker).

Termination is guaranteed on every path. The cap is checked before every entry
attempt and inside every play window - the rewind loop never runs without it.
A fight that is lost silently (popup, paused picker) is caught by the
temperature-stall strikes, and a dead process by pidof; both fall back to the
cold restart instead of tapping a screen that no longer heats anything.

Side effects are the screen and the game: the game is force-stopped and the
screen turned off on the way out - also on Ctrl-C or any exception. Leaving the
screen lit makes the ready gate cool a lit, charging device and throttles the
next charge wait.
"""
from time import monotonic, sleep
import logging

from config import get_config
from errors import Suspend, TransientError
import utils.device_utils as du
from utils import ui
from utils.adb import adb_shell
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

# First segment: play a fresh replay up to timeline WARMUP_TIMELINE (content
# seconds), i.e. ENTRY_PLAY_SECS of wall time at 4x.
WARMUP_TIMELINE = 600
ENTRY_PLAY_SECS = WARMUP_TIMELINE // PLAYBACK_RATE

# In-fight loop: 5 taps on "back 60s" jump ~5 min of content back, then 4x
# plays that stretch again. Rewinding costs REWIND_TAP_GAP per tap, during
# which the replay keeps advancing at PLAYBACK_RATE - both come off the
# window, or every cycle drifts further back through the match.
BACK_60S = (900, 1150)
REWIND_STEPS = 5
REWIND_STEP_SECS = 60
REWIND_TAP_GAP = 2
LOOP_MARGIN = 5
LOOP_PLAY_SECS = (REWIND_STEPS * REWIND_STEP_SECS // PLAYBACK_RATE
                  - REWIND_STEPS * REWIND_TAP_GAP - LOOP_MARGIN)

# A loop that cannot raise any zone by STALL_DELTA means the taps are landing
# somewhere else (popup, result screen, paused speed picker) - the device is
# cooling while we tap. STALL_STRIKES in a row = cold restart.
STALL_DELTA = 100   # mC
STALL_STRIKES = 3

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


def _set_playback_4x(first: bool) -> None:
    """Blind taps: speed button -> picker -> 4x.

    The button only appears once the fight has rolled ~10s, hence the delay
    before the first tap. Inside the rewind loop it is already up, and the
    10s would be 14% of a 70s cycle.
    """
    sleep(SPEED_MENU_DELAY if first else 1)
    ui.tap(*SPEED_BUTTON)
    sleep(1)
    ui.tap(*SPEED_4X)


def _game_alive() -> bool:
    # pidof exits 1 with empty output when the process is gone; the `|| echo`
    # keeps adb_shell from reading that as a dropped device (AdbOffline).
    return adb_shell(
        f"pidof {get_config().sgame.package_name} || echo none"
    ).strip() != "none"


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

    def over_cap() -> bool:
        return monotonic() - t0 >= conf.power.warmup_secs

    def sample() -> bool:
        """One temperature sample; True once the hot streak is complete."""
        nonlocal above
        temps = temps_now()
        above = above + 1 if above_all(temps) else 0
        report(temps, monotonic() - t0, above)
        return above >= CONFIRM_SAMPLES

    def play(secs: float) -> str:
        """Watch the running replay for <secs>, sampling heat every poll.

        'hot' / 'cap' end the burn; 'gone' means the fight is lost and needs a
        cold restart; 'ok' = the window played out.
        """
        deadline = monotonic() + secs
        while monotonic() < deadline:
            if sample():
                log.info("warmup: hot enough after %.0fs", monotonic() - t0)
                return "hot"
            if over_cap():
                log.info("warmup: cap %ds hit, releasing to the ready gate",
                         conf.power.warmup_secs)
                return "cap"
            if not _game_alive():
                log.warning("warmup: the game process is gone; leaving the fight")
                return "gone"
            sleep(WARMUP_POLL)
        return "ok"

    while True:
        if over_cap():
            log.info("warmup: cap %ds hit (before an entry), "
                     "releasing to the ready gate", conf.power.warmup_secs)
            return
        if entry_failures >= MAX_ENTRY_FAILURES:
            raise TransientError(
                f"warmup: {entry_failures} entry failures in a row "
                f"after {monotonic() - t0:.0f}s; this does not look like a lockout"
            )
        try:
            # Card 0: any replay works as a heater
            _reenter_replay(0)
            # Popups can fake "entered"; playback running is only proven
            # by the fight-start log needle the measured runs rely on.
            _sync_timeline_0s()
            _set_playback_4x(first=True)
        except RestLockout as e:
            # Health-system rest banner: a minutes-scale block, so waiting it
            # out is right. Everything else is a missed step, not a lockout.
            entry_failures += 1
            wait = min(LOCKOUT_COOLDOWN,
                       max(0, conf.power.warmup_secs - (monotonic() - t0)))
            log.warning("warmup: rest lockout (%s); cooling down %.0fs",
                        e, wait)
            sleep(wait)
            continue
        except _NO_COOLDOWN:
            raise
        except Suspend:
            raise
        except Exception as e:
            # A warm restart that lands back on the start screen, a missed
            # tap, a popup the templates do not know: retry the entry right
            # away instead of burning the warmup budget on a 11-minute nap.
            entry_failures += 1
            log.warning("warmup: entry failed (%s); retrying the entry", e)
            continue

        # First segment: 4x up to timeline WARMUP_TIMELINE.
        state = play(ENTRY_PLAY_SECS)
        if state in ("hot", "cap"):
            return
        if state == "gone":
            entry_failures += 1
            log.warning("warmup: the game was already gone after the entry "
                        "(%d/%d)", entry_failures, MAX_ENTRY_FAILURES)
            continue
        log.info("warmup: replay at timeline %ds; looping inside the fight "
                 "(rewind %ds, play %.0fs at %dx)",
                 WARMUP_TIMELINE, REWIND_STEPS * REWIND_STEP_SECS,
                 LOOP_PLAY_SECS, PLAYBACK_RATE)

        # No more cold starts: rewind and keep the same fight rolling.
        stalls = 0
        while state == "ok":
            t_before = max(temps_now().values())
            try:
                for _ in range(REWIND_STEPS):
                    ui.tap(*BACK_60S)
                    sleep(REWIND_TAP_GAP)
                _set_playback_4x(first=False)
                state = play(LOOP_PLAY_SECS)
            except RestLockout as e:
                log.warning("warmup: rest lockout inside the loop (%s)", e)
                state = "gone"
            except Suspend:
                raise
            except Exception as e:
                log.warning("warmup: rewind failed (%s); leaving the fight", e)
                state = "gone"
            if state != "ok":
                break
            if max(temps_now().values()) - t_before >= STALL_DELTA:
                stalls = 0
                entry_failures = 0   # the fight is alive and heating: healthy
            else:
                stalls += 1
                if stalls >= STALL_STRIKES:
                    log.warning("warmup: no temperature rise over %d loops; the "
                                "taps are not landing, leaving the fight", stalls)
                    state = "gone"

        if state in ("hot", "cap"):
            return
        # A fight we cannot keep alive is an entry failure, not something to
        # retry until the whole warm-up budget burns on game loads.
        entry_failures += 1
        log.warning("warmup: leaving the fight for a cold restart (%d/%d) "
                    "after %.0fs", entry_failures, MAX_ENTRY_FAILURES,
                    monotonic() - t0)
        _exit_game()
