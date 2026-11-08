import logging
import os
import pathlib
from datetime import datetime

from models.grid import GridRunMeta

log = logging.getLogger(__name__)

class CheckpointMgr:

    def __init__(self, path: str):
        self.existing_runs: list[GridRunMeta] = []
        self.path = path
        # Make sure path exists
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)
        # Walk through the dir, deserialize and collect all grid run's json meta
        files = os.listdir(path)
        for file in files:
            # 分析阶段会在同目录写 <trace>.metrics.json，不是 run 元数据
            if not file.endswith(".json") or file.endswith(".metrics.json"):
                continue
            try:
                meta = GridRunMeta.model_validate_json(
                    pathlib.Path(path, file).read_text()
                )
            except Exception as e:
                log.warning("failed to load %s: %s", file, e)
                continue
            self.existing_runs.append(meta)

    def save_run(
        self,
        params: dict,
        trace_path: str,
        run_start_time: datetime,
        start_temps: dict[str, int] | None = None,
        replay_index: int | None = None,
        early_stopped: bool = False,
        early_stop_fps: list[int] | None = None,
        cut_rewrites: int = 0,
        cut_lost: bool = False,
    ) -> GridRunMeta:
        meta = GridRunMeta(
            grid_params=params,
            trace_path=trace_path,
            time=run_start_time,
            start_temps=start_temps,
            replay_index=replay_index,
            early_stopped=early_stopped,
            early_stop_fps=early_stop_fps,
            cut_rewrites=cut_rewrites,
            cut_lost=cut_lost,
        )
        meta_path = os.path.join(
            self.path, f"{run_start_time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(meta_path, "w") as f:
            f.write(meta.model_dump_json(indent=4))
        # Keep the live list in step with disk: the retry loop asks
        # searched_count() whether this combo already landed, and a stale list
        # would rerun a finished run.
        self.existing_runs.append(meta)
        return meta

    def searched_count(self, grid_params: dict) -> int:
        # Count existing_runs whose grid_params equal the given dict
        count = 0
        for run in self.existing_runs:
            if run.grid_params == grid_params:
                count += 1
        return count
    
    def existing_runs_num(self) -> int:
        return len(self.existing_runs)
