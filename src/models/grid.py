from pydantic import BaseModel
from datetime import datetime

class GridRunMeta(BaseModel):
    time: datetime
    trace_path: str
    grid_params: dict
    # Thermal zone temps (milli-C) right before the run, keys = "zone0/44/53"
    start_temps: dict[str, int] | None = None

