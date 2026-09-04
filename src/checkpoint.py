from models.grid import GridRunMeta

class CheckpointMgr:

    def __init__(self, path: str):
        self.existing_runs: list[GridRunMeta] = []
        # Walk through the dir, deserialize and collect all grid run's json meta
        ...

    def check_searched(self, grid_params: dict) -> bool:
        # Check if the given grid_params have already been searched in existing_runs by comparing grid_params dict
        return False
    
    def existing_runs_num(self) -> int:
        return len(self.existing_runs)
