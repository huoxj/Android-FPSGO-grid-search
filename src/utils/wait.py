"""The only place a bounded wait is allowed to live.

Six hand-written wait loops used to carry six timeout policies and two of them
had no deadline at all (the ready gate and the warm-up entry loop could spin
forever). One util with an explicit `kind` keeps the policy visible at every
call site and makes "how long can this block" answerable by grep.
"""
from time import monotonic, sleep

from errors import Kind, error_for


def wait_until(desc, ok, timeout, *, interval=1.0, consecutive=1,
               on_miss=None, kind=Kind.TRANSIENT):
    """Poll `ok` until it holds `consecutive` times in a row or timeout runs out.

    `ok` is re-checked immediately after a hit (no sleep) so a `consecutive`
    of 2 really means two fresh samples. `on_miss(elapsed)` runs between polls
    for progress output and popup recovery. Raises the error class matching
    `kind`, so the caller's layer decides what a timeout means.
    """
    t0 = monotonic()
    deadline = t0 + timeout
    hits = 0
    while True:
        if ok():
            hits += 1
            if hits >= consecutive:
                return
            continue
        hits = 0
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise error_for(kind, f"{desc}: not reached within {timeout}s")
        if on_miss is not None:
            on_miss(monotonic() - t0)
        sleep(min(interval, remaining))
