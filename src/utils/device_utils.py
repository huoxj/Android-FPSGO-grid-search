from utils.adb import adb

_THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone{}/temp"

def toggle_screen(on: bool = True):
    event = "KEYCODE_WAKEUP" if on else "KEYCODE_SLEEP"
    adb(f"input keyevent {event}")

    # Additional unlock when on
    adb("wm dismiss-keyguard")

def screen_birghtness(percnet: float):
    """
    percent: 0.0 - 1.0
    """
    # Turn off auto brightness
    adb("settings put system screen_brightness_mode 0")
    adb(f"settings put system screen_brightness {int(percnet * 255)}")

def read_temp(thermal_zone: int) -> int:
    # Return temperature in millidegree Celsius
    return int(
        adb(f"cat {_THERMAL_ZONE_PATH.format(thermal_zone)}")
    )

def get_tgid(package_name: str) -> str:
    tgid = adb(f"pidof {package_name}").strip()
    if not tgid or not tgid.isdigit():
        raise ValueError(f"Package '{package_name}' not running")
    return tgid
