from pydantic import BaseModel
from datetime import datetime

class GridRunMeta(BaseModel):
    time: datetime
    trace_path: str
    grid_params: dict

