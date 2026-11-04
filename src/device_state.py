from datetime import datetime
from time import monotonic, sleep

from config import get_config
import utils.device_utils as du
from utils.adb import adb_shell

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
        """Block until the battery gauge reaches target (charger plugged),
        with the screen off.

        The screen state is otherwise inherited from whatever the previous
        run left behind (its finally only turns it off when it gets that far),
        and a lit screen throttles charging.

        Charging is NOT cut here - the cut happens only right before the
        run (power.cut_for_measurement). Raises TimeoutError via the
        retry loop otherwise.
        """
        du.toggle_screen(False)
        t0 = monotonic()
        while True:
            cap = du.read_capacity()
            if cap >= target:
                print(f"Battery at {cap}% (target {target})")
                return
            if monotonic() - t0 > max_secs:
                raise TimeoutError(
                    f"Battery {cap}% did not reach {target} "
                    f"within {max_secs}s"
                )
            cur = du.read_current_ma()
            # current <= 0 with the charger plugged in means nothing is going
            # into the battery: weak supply (PC USB port), a cut that did not
            # lift, or a thermal stop. Say so on the spot instead of silently
            # burning charge_wait_max.
            note = " (battery not charging)" if cur <= 0 else ""
            print(f"charging {cap}% -> {target}% "
                  f"current {cur}mA{note} "
                  f"elapsed {monotonic() - t0:.0f}s")
            sleep(30)

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

