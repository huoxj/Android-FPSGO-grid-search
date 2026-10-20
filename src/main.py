from time import monotonic, sleep
import random
import shutil
from datetime import datetime

from config import get_config
from fpsgo_params_grid import get_grid_samples as get_grid
from checkpoint import CheckpointMgr
from device_state import DeviceMgr
from sgame import sgame_run, RestLockout
from power import cut_for_measurement, restore_charging
from warmup import warm_and_ready
from utils.adb import AdbOffline, adb_shell

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
            print(f"\nDevice back online after {monotonic() - t0:.0f}s")
            return
        except AdbOffline:
            print(f"\rDevice offline, waiting... "
                  f"{monotonic() - t0:.0f}s", end="", flush=True)
            sleep(15)

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
    ckpt_manager = CheckpointMgr(config.output_dir)
    device_mgr = DeviceMgr()

    # None = random: draw a fresh seed and print it, so a random run's
    # order can be reproduced afterwards with shuffle_seed.
    seed = config.shuffle_seed
    if seed is None:
        seed = random.randrange(2**32)
    print("Shuffle seed:", seed)

    run_queue = build_run_queue(grid, config.repeats, seed)

    print("Total grid size:", len(grid))
    print("Existing runs:", ckpt_manager.existing_runs_num())
    print("Total runs:", len(run_queue))

    completed_runs = 0
    skipped_runs = 0
    for i, item in enumerate(run_queue):
        params = item["params"]
        g = item["group"]
        print(f"[g{g + 1}/{config.repeats}] [{i}/{len(run_queue)}] {params}")

        # Occurrence index is the group index: with k existing runs, the
        # occurrences in groups 0..k-1 are already covered and skipped;
        # later groups still run.
        searched = ckpt_manager.searched_count(params)
        if g < searched:
            print(
                f"[g{g + 1}/{config.repeats}] skip combo "
                f"(already ran {searched}/{config.repeats})"
            )
            skipped_runs += 1
            continue

        # Run with backoff retry
        attempt = 0
        backoff = config.backoff_initial_secs
        while True:
            attempt += 1
            try:
                replay_idx = resolve_replay_index(g)
                single_run(device_mgr, ckpt_manager, params, replay_idx)
                completed_runs += 1
                break
            except _NON_RETRYABLE:
                raise
            except AdbOffline:
                # Device gone (USB drop / adbd death): not an attempt,
                # just wait for it to come back and retry this run.
                print(f"\nDevice offline during run: waiting instead of retrying")
                _wait_for_device()
                continue
            except RestLockout:
                # Health-system rest banner: the lockout is minutes-scale
                # and retrying faster is pointless. Not an attempt - wait
                # it out, then rerun this run from scratch.
                print(f"\nRest lockout detected: cooling down "
                      f"{REST_COOLDOWN_SECS}s, then rerunning this run")
                sleep(REST_COOLDOWN_SECS)
                continue
            except Exception as e:
                # Backoff retry
                if attempt >= config.run_max_attempts:
                    print(f"Run failed after {attempt} attempts: {e}")
                    skipped_runs += 1
                    break
                print(
                    f"Run attempt {attempt}/{config.run_max_attempts} "
                    f"failed: {e}.\n"
                    f"Retrying in {backoff}s..."
                )
                sleep(backoff)
                backoff *= 2

    print("Grid search finish. "
          "Runs completed/skipped/total "
          f"{completed_runs}/{skipped_runs}/{len(run_queue)}"
    )

    # Launch the result UI and block on it, so the console stays alive
    # until the operator closes the browser tab / Ctrl-C.
    import subprocess
    import sys
    from pathlib import Path

    ui_cmd = [
        sys.executable, "-m", "src.webui",
        "--output", str(Path(config.output_dir).resolve()),
        "--port", str(config.webui_port),
    ]
    print("Starting webui:", " ".join(ui_cmd))
    try:
        subprocess.run(ui_cmd, check=True)
    except KeyboardInterrupt:
        pass

def resolve_replay_index(group: int) -> int:
    """Which replay card this run plays.

    'group' mode: group g plays card g - each combo is validated across
    different replays (scenario robustness) instead of repeating one.
    'fixed' mode: always the configured index.
    """
    idx = (get_config().replay_index
           if get_config().replay_index_mode == "fixed" else group)
    print(f"-> replay card {idx} (mode={get_config().replay_index_mode})")
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
    device_mgr.wait_for_charge(
        config.power.charge_target, config.power.charge_wait_max
    )

    # 2. Warm-up burn (screen on at full brightness inside warm_and_ready):
    #    burn the device into the hot band, then coast down through the
    #    ready thresholds; the ready gate below passes right after.
    #    TimeoutError (burn budget exhausted) is retryable: the burn
    #    resumes from the already-warm state. Charging continues meanwhile
    #    (topping off what the burn draws).
    if config.power.warmup:
        warm_and_ready()

    # 3. Ready gate: screen off, all zones below thresholds, twice.
    #    Charging still on.
    t0 = monotonic()
    while not device_mgr.ready_for_start_run():
        print(
            f"\rDevice not ready, elapsed: {monotonic() - t0:.2f}s",
            end="", flush=True
        )
        sleep(5)
    print("\n")

    # 4. The only battery-only window: cut the charge path right before
    #    the run. Raises on failure -> retryable.
    if config.power.enable:
        cut_for_measurement()

    try:
        start_temps = device_mgr.read_temps()
        run_start_time = datetime.now()

        # Pre-run check and init
        device_mgr.start_run_init(config.sgame.package_name)

        # Game specific run process
        trace_tmp_path = sgame_run(params, replay_index)

        # Perfetto stopped. Save run metadata
        trace_path = config.output_dir + "/" + trace_tmp_path.name
        shutil.copy(trace_tmp_path, trace_path)
        ckpt_manager.save_run(
            params=params,
            trace_path= trace_path,
            run_start_time=run_start_time,
            start_temps=start_temps,
            replay_index=replay_index,
        )
    finally:
        if config.power.enable:
            # Restore even when the run failed; a failure to restore raises
            # so the retry loop's cut_for_measurement sees a known state.
            restore_charging()
        # Post-run cleanup
        device_mgr.finish_run_cleanup()

if __name__ == "__main__":
    main()

