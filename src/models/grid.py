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
    # fpsgo current fps sampled in the early-stop check window (one per
    # check_interval, oldest first); recorded on every run, so it can be
    # compared against the trace's presented fps.
    early_stop_fps: list[int] | None = None
    # How often the charge cut had to be re-applied mid-run (the ODM re-opens
    # the power path on its own) and whether it finally refused to cut - a lost
    # cut means the battery was not carrying the load, so the power numbers of
    # this run are polluted while its fps numbers are still valid.
    cut_rewrites: int = 0
    cut_lost: bool = False

