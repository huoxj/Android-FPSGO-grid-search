from contextlib import contextmanager
import subprocess


class AdbOffline(Exception):
    """adb returned nothing - device disconnected / adbd dead."""


def adb(cmd: str) -> str:
    try:
        result = subprocess.run(
            ["adb", *cmd.split()],
            capture_output=True, text=True, timeout=10
        )
    except subprocess.TimeoutExpired as e:
        # adbd wedged: treat like a disconnect (caller retries later)
        raise AdbOffline(f"adb timeout: {cmd}") from e
    except FileNotFoundError as e:
        raise AdbOffline("adb binary not found in PATH") from e

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
