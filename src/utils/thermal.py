
import logging
from time import sleep

from errors import PreconditionError
from utils.adb import adb_shell

log = logging.getLogger(__name__)

_INTF = "/vendor/bin/thermal_intf"
_CURRENT_TP = "/data/vendor/thermal/.current_tp"


def apply_policy(name: str):
    """Switch the vendor thermal policy and prove it took.

    Must run before the fpsgo params: the switch rewrites the cluster
    frequency ceilings, so params applied first would be clobbered.
    Empty name = leave the device policy alone (non-MTK devices).
    """
    if not name:
        return
    adb_shell(f"{_INTF} apply {name}")
    # The apply is synchronous, but .current_tp can lag a fraction of a second.
    current = ""
    for _ in range(5):
        current = adb_shell(f"cat {_CURRENT_TP}")
        if name in current:
            log.info("thermal policy: %s", name)
            return
        sleep(0.2)
    raise PreconditionError(
        f"thermal policy did not switch to {name}: "
        f".current_tp={current.strip()!r}"
    )
