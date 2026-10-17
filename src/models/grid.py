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

