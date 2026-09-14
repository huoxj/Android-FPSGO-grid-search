import time
from datetime import datetime

def wait_for(
    cond,
    while_waiting=lambda: None,
    timeout = 60,
    interval = 1.0,
    raise_on_timeout: bool = True
) -> bool:
    t0=time.time()
    while time.time() - t0 < timeout:
        if cond(): return True
        while_waiting()
        time.sleep(interval)
    if raise_on_timeout:
        raise TimeoutError(f"wait_for timeout after {timeout}s")
    return False

