"""Error taxonomy: one axis for policy, one for the retry-loop owner.

`kind` decides what the caller does - retry the run, stop the batch, or pause.
It is attached where the failure is *written*, never derived from the exception
class: a timeout at the charge target means the environment is wrong, the very
same timeout waiting for a game screen just means rerun the run.

    TRANSIENT     the run may pass next time (adb hiccup, missed screen)
    PRECONDITION  the environment is wrong; retrying cannot fix it
                  (battery never charged, device never cooled)
    CONFIG        bad config / missing resources; retrying is pointless
    BUG           anything unclassified - keep the traceback

Loops own exactly one level: a sub-step never retries itself, it raises and the
run-level loop decides.
"""
from enum import Enum


class Kind(Enum):
    TRANSIENT = "transient"
    PRECONDITION = "precondition"
    CONFIG = "config"
    BUG = "bug"


class GridError(Exception):
    kind = Kind.BUG


class TransientError(GridError):
    kind = Kind.TRANSIENT


class PreconditionError(GridError):
    kind = Kind.PRECONDITION


class ConfigError(GridError):
    kind = Kind.CONFIG


class Suspend(Exception):
    """The device is gone (adb lost): pause, wait for it, repair, carry on.

    Not a failure: no attempt is consumed and nothing is discarded.
    """


_BY_KIND = {
    Kind.TRANSIENT: TransientError,
    Kind.PRECONDITION: PreconditionError,
    Kind.CONFIG: ConfigError,
    Kind.BUG: GridError,
}


def error_for(kind: Kind, msg: str) -> GridError:
    return _BY_KIND[kind](msg)
