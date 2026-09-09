from time import sleep
import shutil
from datetime import datetime

from config import get_config
from prepare_grid import get_grid
from checkpoint import CheckpointMgr
from device_state import DeviceMgr
from sgame import sgame_run

def main():

    config = get_config()

    grid = get_grid()
    ckpt_manager = CheckpointMgr(config.output_dir)
    device_mgr = DeviceMgr()

    print("Total grid size:", len(grid))
    print("Existing runs:", ckpt_manager.existing_runs_num())
    
    for i, params in enumerate(grid):
        run_start_time = datetime.now()
        print(f"[{i}/{len(grid)}]: {params}")
        # Check if already searched
        if ckpt_manager.check_searched(params):
            print("Already searched. Skipping...")
            continue

        # Waiting for device to ready
        while not device_mgr.ready_for_start_run():
            print("Device not ready. Waiting...")
            sleep(5)
        
        # Pre-run check and init
        # TODO: if set params here, limit_freq params may be override when sgame
        # enters replay match
        device_mgr.start_run_init(params, config.sgame.package_name)

        # Game specific run process
        game_start_time, game_end_time, trace_tmp_path = sgame_run()

        # Perfetto stopped. Save run metadata
        trace_path = config.output_dir + "/" + trace_tmp_path.name
        shutil.copy(trace_tmp_path, trace_path)
        ckpt_manager.save_run(
            params=params,
            trace_path= trace_path,
            run_start_time=run_start_time,
            game_start_time=game_start_time,
            game_end_time=game_end_time
        )

        # Post-run cleanup
        device_mgr.finish_run_cleanup()

