from time import monotonic, sleep
import shutil
from datetime import datetime

from config import get_config
from prepare_grid import get_grid
from checkpoint import CheckpointMgr
from device_state import DeviceMgr
from sgame import sgame_run

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

def single_run(
    device_mgr: DeviceMgr,
    ckpt_manager: CheckpointMgr,
    params: dict,
):
    config = get_config()

    # Waiting for device to ready
    t0 = monotonic()
    while not device_mgr.ready_for_start_run():
        print(
            f"\rDevice not ready, elapsed: {monotonic() - t0:.2f}s",
            end="", flush=True
        )
        sleep(5)
    print("\n")

    run_start_time = datetime.now()
    
    try:
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
        )
    finally:
        # Post-run cleanup
        device_mgr.finish_run_cleanup()

if __name__ == "__main__":
    main()

