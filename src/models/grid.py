from pydantic import BaseModel
from datetime import datetime

class GridRunMeta(BaseModel):
    time: datetime

    trace_path: str
    start_time: datetime
    end_time: datetime

    config_raw: str
    grid_params: dict

