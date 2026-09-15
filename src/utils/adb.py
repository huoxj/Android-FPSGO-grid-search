from contextlib import contextmanager
import subprocess

def adb(cmd: str) -> str:
    try:
        result = subprocess.run(
            ["adb", *cmd.split()],
            capture_output=True, text=True, timeout=10
        ) 
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error executing adb command: {e}"
    
    return result.stdout.strip()

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

