from pydantic import BaseModel
from datetime import datetime

class GridRunMeta(BaseModel):
    time: datetime
    trace_path: str
    grid_params: dict
    # Thermal zone temps (milli-C) right before the run, keys = "zone0/44/53"
    start_temps: dict[str, int] | None = None
    # Which replay card this run played; None = legacy (always card 0)
    replay_index: int | None = None
    # Early-stopped run: the trace was incomplete and got deleted, so there
    # is nothing to analyse - keep the meta for the run list only.
    early_stopped: bool = False
    # fps samples from the early-stop window, oldest first
    early_stop_fps: list[int] | None = None

