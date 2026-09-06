import os

from models.grid import GridRunMeta

class CheckpointMgr:

    def __init__(self, path: str):
        self.existing_runs: list[GridRunMeta] = []
        self.path = path
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

    def save_run(
        self,
        params: dict,
        trace_path: str,
        run_start_time: datetime,
        game_start_time: datetime,
        game_end_time: datetime,
    ) -> GridRunMeta:
        meta = GridRunMeta(
            grid_params=params,
            trace_path=trace_path,
            time=run_start_time,
            start_time=game_start_time,
            end_time=game_end_time
        )
        meta_path = os.path.join(
            self.path, f"{run_start_time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(meta_path, "w") as f:
            f.write(meta.model_dump_json(indent=4))
        return meta

    def check_searched(self, grid_params: dict) -> bool:
        # Check if the given grid_params have already been searched
        # in existing_runs by comparing grid_params dict
        for run in self.existing_runs:
            if run.grid_params == grid_params:
                return True
        return False
    
    def existing_runs_num(self) -> int:
        return len(self.existing_runs)
