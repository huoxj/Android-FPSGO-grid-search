import os

from models.grid import GridRunMeta

class CheckpointMgr:

    def __init__(self, path: str):
        self.existing_runs: list[GridRunMeta] = []
        # Walk through the dir, deserialize and collect all grid run's json meta
        files = os.listdir(path)
        for file in files:
            if not file.endswith(".json"):
                continue
            try:
                meta = GridRunMeta.model_validate_json(os.path.join(path, file))
            except Exception as e:
                print(f"Failed to load {file}: {e}")
                continue
            self.existing_runs.append(meta)

    def save_run(self):
        # TODO: save run metadata
        # i.e. GridRunMeta instance
        pass

    def check_searched(self, grid_params: dict) -> bool:
        # Check if the given grid_params have already been searched
        # in existing_runs by comparing grid_params dict
        for run in self.existing_runs:
            if run.grid_params == grid_params:
                return True
        return False
    
    def existing_runs_num(self) -> int:
        return len(self.existing_runs)
