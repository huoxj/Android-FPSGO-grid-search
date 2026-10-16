from datetime import datetime

from config import get_config
import utils.device_utils as du
from utils.adb import adb_shell

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
            du.read_temp(zone) < thre for zone, thre in thresholds.items()
        )
        twice_okay = cur_okay and self.last_temp_check
        self.last_temp_check = cur_okay

        return twice_okay

