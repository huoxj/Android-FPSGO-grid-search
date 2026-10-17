from time import monotonic, sleep
import shutil
from datetime import datetime

from config import get_config
from fpsgo_params_grid import get_grid_samples as get_grid
from checkpoint import CheckpointMgr
from device_state import DeviceMgr
from report import report
from sgame import sgame_run
from power import cut_for_measurement, restore_charging
from warmup import warm_and_ready

_NON_RETRYABLE = (FileNotFoundError, KeyError)

def main():

    config = get_config()

    grid = get_grid()
    ckpt_manager = CheckpointMgr(config.output_dir)
    device_mgr = DeviceMgr()

    print("Total grid size:", len(grid))
    print("Existing runs:", ckpt_manager.existing_runs_num())
    
    completed_runs = 0
    skipped_runs = 0
    for i, params in enumerate(grid):
        print(f"[{i}/{len(grid)}]: {params}")
        # Check if already searched
        if ckpt_manager.check_searched(params):
            print("Already searched. Skipping...")
            skipped_runs += 1
            continue

        # Run with backoff retry
        attempt = 0
        backoff = config.backoff_initial_secs
        while True:
            attempt += 1
            try:
                single_run(device_mgr, ckpt_manager, params)
                completed_runs += 1
                break
            except _NON_RETRYABLE:
                raise
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
          f"{completed_runs}/{skipped_runs}/{len(grid)}"
    )

    print("Generating report... ")
    report(config.output_dir)

    input("\n\nSearch finished, press Enter to exit...")

def single_run(
    device_mgr: DeviceMgr,
    ckpt_manager: CheckpointMgr,
    params: dict,
):
    config = get_config()

    if config.power.warmup:
        # Burn the device into the hot band, then coast down through the
        # ready thresholds; the ready gate below passes right after.
        # TimeoutError (burn budget exhausted) is retryable: the burn
        # resumes from the already-warm state.
        warm_and_ready()

    # Waiting for device to ready (hot state: cooled below per-zone thresholds)
    t0 = monotonic()
    while not device_mgr.ready_for_start_run():
        print(
            f"\rDevice not ready, elapsed: {monotonic() - t0:.2f}s",
            end="", flush=True
        )
        sleep(5)
    print("\n")

    if config.power.enable:
        # Blocks until the charge path is cut and verified (PE5 may stall
        # this for minutes after a full charge). Raises on failure, which
        # the retry loop in main() treats as a retryable run failure.
        power.cut_for_measurement()

    try:
        start_temps = device_mgr.read_temps()
        run_start_time = datetime.now()

        # Pre-run check and init
        device_mgr.start_run_init(config.sgame.package_name)

        # Game specific run process
        trace_tmp_path = sgame_run(params)

        # Perfetto stopped. Save run metadata
        trace_path = config.output_dir + "/" + trace_tmp_path.name
        shutil.copy(trace_tmp_path, trace_path)
        ckpt_manager.save_run(
            params=params,
            trace_path= trace_path,
            run_start_time=run_start_time,
            start_temps=start_temps,
        )
    finally:
        if config.power.enable:
            # Restore even when the run failed; a failure to restore raises
            # so the retry loop's cut_for_measurement sees a known state.
            power.restore_charging()
        # Post-run cleanup
        device_mgr.finish_run_cleanup()

if __name__ == "__main__":
    main()

