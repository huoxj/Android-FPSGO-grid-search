import subprocess

def adb(cmd: str, device: str | None = None) -> str:
    prefix = ["adb"]
    if device:
        prefix += ["-s", device]

    try:
        result = subprocess.run(
            prefix + ["shell", cmd],
            capture_output=True, text=True, timeout=10
        ) 
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error executing adb command: {e}"
    
    return result.stdout.strip()

