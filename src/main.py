from time import sleep

from prepare_grid import get_grid
from checkpoint import CheckpointMgr
from device_state import DeviceMgr
from sgame import sgame_run

def main():
    grid = get_grid()
    ckpt_manager = CheckpointMgr("test_gs")
    device_mgr = DeviceMgr()

    print("Total grid size:", len(grid))
    print("Existing runs:", ckpt_manager.existing_runs_num())
    
    for i, params in enumerate(grid):
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
        device_mgr.turn_on_screen()
        
        sgame_run()

        # Perfetto stopped. Save run metadata
        ckpt_manager.save_run()

        # Post-run cleanup
        device_mgr.turn_off_screen()
