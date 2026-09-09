
from utils.adb import adb
from models.fpsgo_params import FpsgoParams

_FPSGO_STATUS = "/sys/kernel/fpsgo/fstb/fpsgo_status"

def apply_params(params: dict, package_name: str, check=True):
    # Set default params and apply incoming params
    # Re-check if params properly applied

    # 1. Set params
    set_parm = FpsgoParams(**params)
    set_parm.apply(package_name)

    # 2. Checking
    if check:
        get_parm = FpsgoParams.read(package_name)
        # Compare expected (defaults+override) vs readback
        exp = FpsgoParams(**params).model_dump()
        got = get_parm.model_dump()
        # print only mismatches
        bad = {f: (exp[f], got[f]) for f in exp if exp[f] != got[f]}
        for f, (w, r) in bad.items():
            print(f"apply mismatch {f}: set={w} read={r}")
        if bad:
            raise RuntimeError(
                f"apply check failed ({len(bad)} fields): {', '.join(bad)}"
            )

def read_fpsgo_fps(package_name: str) -> int:
    raw = adb(f"cat {_FPSGO_STATUS}")
    for line in raw.splitlines():
        if package_name not in line:
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[4].isdigit():
            # TODO: check which part is the FPS
            return int(parts[4])
    raise ValueError(
        f"FPS for package '{package_name}' not found in fpsgo_status"
    )
