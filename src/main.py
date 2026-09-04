from prepare_grid import get_grid
from checkpoint import CheckpointMgr

def main():
    grid = get_grid()
    ckpt_manager = CheckpointMgr("test_gs")
    
    print("Total grid size:", len(grid))
    print("Existing runs:", ckpt_manager.existing_runs_num())
    
    for i, params in enumerate(grid):
        print(f"[{i}/{len(grid)}]: {params}")
        # Check if already searched
        if ckpt_manager.check_searched(params):
            print("Already searched. Skipping...")
            continue

        # Waiting for device to ready
        



