from datetime import datetime

from config import get_config
from errors import Kind
import utils.device_utils as du
from utils.adb import adb_shell
from utils.wait import wait_until

# mC. Warmup heats every zone ABOVE its ready threshold; the gate then has to
# pass as the device coasts down, so it accepts threshold + 0.5C - with the old
# strict `<` the two conditions were mutually exclusive and deadlocked.
READY_TEMP_TOLERANCE = 500

class DeviceMgr:
    
    def __init__(self):
        self.config = get_config()

        # temperature check
        self.last_temp_check = False
        self.last_temp_check_time = datetime.now()

    def ready_for_start_run(self) -> bool:
        # Check if the device is ready for a new run
        return self._battery_okay() and \
               self._temperature_okay()

    def start_run_init(self, package_name: str):
        # 1. Turn on screen
        du.toggle_screen(True)
        # 2. Screen brightness
        du.screen_birghtness(1.0)
        # 3. Clear logcat
        adb_shell("logcat -c")

    def finish_run_cleanup(self):
        du.toggle_screen(False)

    def read_temps(self) -> dict[str, int]:
        # Same zones as _temperature_okay; for run metadata
        return {f"zone{z}": du.read_temp(z) for z in (0, 44, 53)}

    def wait_for_charge(self, target: int, max_secs: int) -> None:
        """Block until the battery gauge reaches target (charger plugged).

        SCREEN OFF is part of the wait, not a side effect: the screen state is
        otherwise whatever the previous run left behind, and a lit screen
        throttles charging (a 5V/0.5A port cannot even cover it).

        Charging is NOT cut here - the cut happens only right before the
        run (power.cut_for_measurement). A timeout is a PRECONDITION failure:
        the environment cannot charge, so retrying the run cannot help.
        """
        du.toggle_screen(False)

        cap = 0

        def charged() -> bool:
            nonlocal cap
            cap = du.read_capacity()
            return cap >= target

        def report(elapsed: float) -> None:
            cur = du.read_current_ma()
            note = " (battery not charging)" if cur <= 0 else ""
            print(f"charging {cap}% -> {target}% "
                  f"current {cur}mA{note} "
                  f"elapsed {elapsed:.0f}s")

        wait_until(f"battery at {target}%", charged, max_secs,
                   interval=30, on_miss=report, kind=Kind.PRECONDITION)
        print(f"Battery at {cap}% (target {target})")

    def _battery_okay(self) -> bool:
        """
        We are planning to grid search with charger on, because we currently
        cannot control charging state. Until we can, this battery check will
        always be okay
        """
        return True

    def _temperature_okay(self) -> bool:
        """
        Check if the device temperature is okay for a new run
        """
        # 0: soc_max
        # 44: tsx_ntc
        # 53: battery
        thresholds = {
            0: self.config.ready_temp_soc_max,
            44: self.config.ready_temp_tsx,
            53: self.config.ready_temp_battery,
        }

        self.last_temp_check_time = datetime.now()

        cur_okay = all(
            du.read_temp(zone) < thre + READY_TEMP_TOLERANCE
            for zone, thre in thresholds.items()
        )
        twice_okay = cur_okay and self.last_temp_check
        self.last_temp_check = cur_okay

        return twice_okay

