from utils.adb import adb_shell

_THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone{}/temp"

def toggle_screen(on: bool = True):
    event = "KEYCODE_WAKEUP" if on else "KEYCODE_SLEEP"
    adb_shell(f"input keyevent {event}")

    # Additional unlock when on
    if on:
        adb_shell("wm dismiss-keyguard")

def screen_birghtness(percnet: float):
    """
    percent: 0.0 - 1.0
    """
    # Turn off auto brightness
    adb_shell("settings put system screen_brightness_mode 0")
    adb_shell(f"settings put system screen_brightness {int(percnet * 255)}")

def read_temp(thermal_zone: int) -> int:
    # Return temperature in millidegree Celsius
    return int(
        adb_shell(f"cat {_THERMAL_ZONE_PATH.format(thermal_zone)}")
    )

def get_tgid(package_name: str) -> str:
    tgid = adb_shell(f"pidof {package_name}").strip()
    if not tgid or not tgid.isdigit():
        raise ValueError(f"Package '{package_name}' not running")
    return tgid
