from time import monotonic, sleep
import logging
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

from config import get_config
from errors import ConfigError, Kind, PreconditionError
from fpsgo_params_grid import get_grid_samples as get_grid
from models.fpsgo_params import FpsgoParams
from checkpoint import CheckpointMgr
from device_state import DeviceMgr
from sgame import sgame_run, RestLockout
from power import CutWatch, cut_for_measurement, restore_charging
from warmup import warm_and_ready
from utils.adb import AdbOffline, adb_shell
from utils.log import setup as setup_logging, stage, status
from utils.wait import wait_until
# Top-level so PyArmor's dependency scan sees it (a function-level import
# could be missed and the frozen exe would die on `module webui`).
from webui.server import serve

log = logging.getLogger(__name__)

# Missing templates / malformed config: keep the traceback, it names the file.
_NON_RETRYABLE = (FileNotFoundError, KeyError)

# Health-system lockout duration + margin
REST_COOLDOWN_SECS = 15 * 60 + 60

def _wait_for_device():
    """Block until adb answers again. A disconnected device is not a run
    failure - 8 fast retries used to burn every attempt and skip the rest
    of the batch; instead we wait (nobody is watching at 4am anyway)."""
    t0 = monotonic()
    while True:
        try:
            adb_shell("echo ok")
            log.info("device back online after %.0fs", monotonic() - t0)
            return
        except AdbOffline:
            status(f"device offline, waiting... {monotonic() - t0:.0f}s")
            sleep(15)

def _fmt_params(params: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in params.items())

def build_run_queue(grid: list[dict], repeats: int, seed: int) -> list[dict]:
    # Run order = `repeats` groups, each group being the whole grid
    # independently shuffled. One occurrence of every combo per group
    # guarantees repeats land in different groups and are separated in
    # wall-clock time by a full grid pass. Each group's shuffle is seeded
    # independently with `seed + g` (g = 0-based group index), so a given
    # seed reproduces the whole order. Items carry the params and the
    # 0-based group index (= occurrence index).
    queue = []
    for g in range(repeats):
        rng = random.Random(seed + g)
        group = list(grid)
        rng.shuffle(group)
        for params in group:
            queue.append({"params": params, "group": g})
    return queue

def main():

    config = get_config()

    grid = get_grid()

    # Logging first: checkpoint loading warnings should use the same format.
    log_file = Path(config.output_dir) / "grid_search.log"
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)
    setup_logging(config.log_level, str(log_file))

    ckpt_manager = CheckpointMgr(config.output_dir)
    device_mgr = DeviceMgr()

    # None = random: draw a fresh seed and log it, so a random run's
    # order can be reproduced afterwards with shuffle_seed.
    seed = config.shuffle_seed
    if seed is None:
        seed = random.randrange(2**32)

    run_queue = build_run_queue(grid, config.repeats, seed)
    # Fail before charge/warmup: an out-of-range combo would only surface
    # inside the measured run, and every retry burns another full warmup.
    for p in grid:
        FpsgoParams(**p)

    log.info("%d combos x %d groups = %d runs (shuffle seed %d)",
             len(grid), config.repeats, len(run_queue), seed)
    log.info("output %s | existing runs %d | log %s",
             config.output_dir, ckpt_manager.existing_runs_num(), log_file)
    log.info("per run: %ds, charge to %d%%, warmup %s, charge cut %s",
             config.run_duration, config.power.charge_target,
             "on" if config.power.warmup else "off",
             "on" if config.power.enable else "off")

    completed_runs = 0
    skipped_runs = 0
    for i, item in enumerate(run_queue):
        params = item["params"]
        g = item["group"]
        log.info("[g%d/%d] [%d/%d] %s", g + 1, config.repeats, i,
                 len(run_queue), _fmt_params(params))

        # Occurrence index is the group index: with k existing runs, the
        # occurrences in groups 0..k-1 are already covered and skipped;
        # later groups still run.
        searched = ckpt_manager.searched_count(params)
        if g < searched:
            log.info("[g%d/%d] skip combo (already ran %d/%d)",
                     g + 1, config.repeats, searched, config.repeats)
            skipped_runs += 1
            continue

        # Run with backoff retry
        attempt = 0
        backoff = config.backoff_initial_secs
        while True:
            attempt += 1
            if attempt > 1 and ckpt_manager.searched_count(params) > g:
                # The previous attempt recorded the run and then failed on the
                # way out. Rerunning would duplicate the combo - and after a
                # resume the duplicate inflates searched_count, which silently
                # skips a later group of the same combo. Commit, never roll
                # a finished run back.
                log.info("[g%d] run already recorded, not rerunning", g + 1)
                completed_runs += 1
                break
            try:
                replay_idx = resolve_replay_index(g)
                single_run(device_mgr, ckpt_manager, params, replay_idx)
                completed_runs += 1
                break
            except _NON_RETRYABLE:
                raise
            except ConfigError as e:
                log.error("cannot continue: %s", e)
                raise SystemExit(2) from e
            except PreconditionError as e:
                # The environment cannot satisfy the wait (battery will not
                # charge, device will not cool). Retrying this combo cannot
                # help and used to cost hours per attempt, so stop the batch:
                # the operator fixes the device and restarts, and the
                # checkpoint resumes where this stopped.
                log.error("stopping the batch: %s", e)
                raise SystemExit(2) from e
            except AdbOffline as e:
                # Device gone (USB drop / adbd death): not an attempt. Wait for
                # it, then repair what the interrupted run may have left
                # behind - the retry assumes a clean device.
                log.warning("device offline (%s): waiting, then repairing", e)
                _wait_for_device()
                _repair(device_mgr, config)
                continue
            except RestLockout:
                # Health-system rest banner: the lockout is minutes-scale
                # and retrying faster is pointless. Not an attempt - wait
                # it out, then rerun this run from scratch.
                log.warning("rest lockout: cooling down %ds, then rerunning",
                            REST_COOLDOWN_SECS)
                sleep(REST_COOLDOWN_SECS)
                continue
            except Exception as e:
                # Backoff retry
                if attempt >= config.run_max_attempts:
                    log.error("run failed after %d attempts: %s", attempt, e)
                    skipped_runs += 1
                    break
                log.warning("run attempt %d/%d failed: %s (retry in %ds)",
                            attempt, config.run_max_attempts, e, backoff)
                sleep(backoff)
                backoff *= 2

    log.info("batch finished: completed %d, skipped %d, total %d",
             completed_runs, skipped_runs, len(run_queue))

    # Launch the result UI in-process and block on it, so the console stays
    # alive until the operator closes the browser tab / Ctrl-C. (In the
    # PyInstaller build sys.executable is the exe itself, so spawning
    # `python -m src.webui` would re-run the whole search.)
    serve(str(Path(config.output_dir).resolve()), config.webui_port)

def resolve_replay_index(group: int) -> int:
    """Which replay card this run plays.

    'group' mode: group g plays card g - each combo is validated across
    different replays (scenario robustness) instead of repeating one.
    'fixed' mode: always the configured index.
    """
    mode = get_config().replay_index_mode
    idx = get_config().replay_index if mode == "fixed" else group
    log.info("replay card %d (mode=%s)", idx, mode)
    return idx

def single_run(
    device_mgr: DeviceMgr,
    ckpt_manager: CheckpointMgr,
    params: dict,
    replay_index: int,
):
    config = get_config()

    # 1. Charge first, screen OFF (charging is slowed by a hot, lit screen;
    #    a full battery also gives every run the same headroom). The charge
    #    stays ON through warmup and the ready wait - only the RUN is
    #    measured battery-only.
    stage("charge")
    # The cut lives in kernel state, so a previous run that died before
    # teardown leaves the charge path closed and the battery never fills.
    if config.power.enable:
        try:
            restore_charging()
        except Exception as e:
            log.warning("could not restore charging before the charge wait: %s", e)
    device_mgr.wait_for_charge(
        config.power.charge_target, config.power.charge_wait_max
    )

    # 2. Warm-up burn (screen on at full brightness inside warm_and_ready):
    #    burn the device into the hot band, then coast down through the
    #    ready thresholds; the ready gate below passes right after. A timeout
    #    (burn budget exhausted) is retryable: the burn resumes from the
    #    already-warm state. Charging continues meanwhile (topping off what
    #    the burn draws).
    if config.power.warmup:
        stage("warmup")
        warm_and_ready()

    # 3. Ready gate: screen off, all zones below thresholds, twice.
    #    Charging still on. Bounded: a device that cannot cool used to spin
    #    here forever, and retrying cannot fix the environment.
    stage("ready")
    wait_until(
        "device ready to start a run",
        device_mgr.ready_for_start_run,
        config.power.ready_wait_max,
        interval=5,
        on_miss=lambda elapsed: status(
            f"cooling for the ready gate... {elapsed:.0f}s/"
            f"{config.power.ready_wait_max}s"),
        kind=Kind.PRECONDITION,
    )
    log.info("device ready")

    cut_watch: CutWatch | None = None
    try:
        # 4. The only battery-only window: cut the charge path right before the
        #    run, INSIDE the try. A cut that lands and then fails to verify must
        #    still reach the teardown, or the path stays cut (kernel state, it
        #    outlives this process) until someone writes it back by hand.
        if config.power.enable:
            stage("cut")
            cut_for_measurement()
            # The cut is not sticky (the ODM re-opens the path on its own), so
            # nothing else keeps the run battery-only.
            cut_watch = CutWatch()
            cut_watch.start()

        start_temps = device_mgr.read_temps()
        run_start_time = datetime.now()

        # Pre-run check and init
        stage("run")
        device_mgr.start_run_init(config.sgame.package_name)
        log.info("start temps %s", start_temps)

        # Game specific run process
        trace_tmp_path, early_stop_fps, early_stopped = sgame_run(
            params, replay_index
        )

        # Perfetto stopped. Save run metadata. An early-stopped run has no
        # trace (sgame_run deleted the incomplete one), but its meta still
        # goes to disk so the webui can list it.
        trace_path = ""
        if not early_stopped:
            trace_path = config.output_dir + "/" + trace_tmp_path.name
            shutil.copy(trace_tmp_path, trace_path)
        ckpt_manager.save_run(
            params=params,
            trace_path= trace_path,
            run_start_time=run_start_time,
            start_temps=start_temps,
            replay_index=replay_index,
            early_stopped=early_stopped,
            early_stop_fps=early_stop_fps,
            cut_rewrites=cut_watch.rewrites if cut_watch else 0,
            cut_lost=bool(cut_watch and cut_watch.lost),
        )
        log.info("run saved: %s%s", run_start_time.strftime("%Y%m%d_%H%M%S"),
                 " (early stop, trace dropped)" if early_stopped else "")
        if cut_watch is not None and (cut_watch.rewrites or cut_watch.lost):
            log.warning("cut had to be re-applied %d time(s) during this run%s",
                        cut_watch.rewrites,
                        "; power numbers are polluted" if cut_watch.lost else "")
    finally:
        if cut_watch is not None:
            # Stop before restoring: a check in flight would cut the path right
            # after restore writes it back.
            cut_watch.stop()
            cut_watch.join(20)
            if cut_watch.is_alive():
                log.warning("cut watchdog did not stop; restore may race it")
        stage("teardown")
        _teardown(device_mgr, config)


def _teardown(device_mgr: DeviceMgr, config) -> None:
    """Screen off and charge restore, neither depending on the other.

    A charge path left cut keeps the phone from charging until someone writes
    it back, and a lit screen throttles the next charge wait - so each step
    runs even when the other one is already failing. Teardown failures are
    logged, never raised over the exception already on its way out.
    """
    in_flight = sys.exc_info()[0] is not None
    errors: list[Exception] = []
    try:
        device_mgr.finish_run_cleanup()
    except Exception as e:
        errors.append(e)
    if config.power.enable:
        try:
            restore_charging()
        except Exception as e:
            errors.append(e)
    for e in errors:
        log.warning("teardown step failed: %s", e)
    if errors and not in_flight:
        raise errors[0]


def _repair(device_mgr: DeviceMgr, config) -> None:
    """Re-establish what a dropped device leaves broken between attempts.

    An adb drop in the middle of a run can leave the charge path cut, the
    screen lit and the game running. The retry assumes a clean device, so put
    it back before retrying. Best effort: the retry's own waits will complain
    if the repair did not take.
    """
    try:
        device_mgr.finish_run_cleanup()
    except Exception as e:
        log.warning("repair could not turn the screen off: %s", e)
    if config.power.enable:
        try:
            restore_charging()
        except Exception as e:
            log.warning("repair could not restore charging: %s", e)

if __name__ == "__main__":
    main()
