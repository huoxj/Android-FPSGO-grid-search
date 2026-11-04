from contextlib import contextmanager
import subprocess
from time import sleep

from errors import Suspend


class AdbOffline(Suspend):
    """adb returned nothing - device disconnected / adbd dead."""


# adbd serializes commands: while a 1.3GB trace is being pulled every other
# adb call queues behind it and blows through any short timeout. A timeout is
# NOT a disconnect - ask `adb devices` and back off before believing it.
_ADB_TIMEOUT = 10
_BACKOFF = (1, 2, 4, 8)


def _device_state() -> str:
    """'online' / 'offline' / 'unknown' (the check itself timed out)."""
    try:
        r = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return "unknown"
    except FileNotFoundError:
        return "offline"
    states = [
        parts[1] for parts in (line.split() for line in r.stdout.splitlines()[1:])
        if len(parts) >= 2
    ]
    return "online" if {"device", "recovery"} & set(states) else "offline"


def adb(cmd: str) -> str:
    for delay in (0, *_BACKOFF):
        if delay:
            sleep(delay)
        try:
            result = subprocess.run(
                ["adb", *cmd.split()],
                capture_output=True, text=True, timeout=_ADB_TIMEOUT
            )
            break
        except FileNotFoundError as e:
            raise AdbOffline("adb binary not found in PATH") from e
        except subprocess.TimeoutExpired as e:
            state = _device_state()
            if state == "offline":
                raise AdbOffline(f"adb timeout, device offline: {cmd}") from e
            if delay == _BACKOFF[-1]:
                raise AdbOffline(
                    f"adb timeout after {len(_BACKOFF) + 1} tries "
                    f"(device {state}): {cmd}"
                ) from e
    else:
        raise AdbOffline(f"adb retries exhausted: {cmd}")

    out = result.stdout.strip()
    if not out and result.returncode != 0:
        # `adb devices` with no device, dead adbd, unplugged cable...
        raise AdbOffline(
            f"adb rc={result.returncode} with empty output: {cmd} "
            f"(stderr: {result.stderr.strip()[:120]})"
        )
    return out

def adb_shell(cmd: str) -> str:
    return adb(f"shell {cmd}")

@contextmanager
def adb_follow(
    cmd: str,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
):
    p = subprocess.Popen(
        ["adb", *cmd.split()],
        stdout=stdout, stderr=stderr,
        text=True, bufsize=1,
        errors="replace"
    )
    try:
        yield p
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if p.stdout:
            p.stdout.close()
        if p.stderr:
            p.stderr.close()
