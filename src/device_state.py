
class DeviceStateMgr:
    
    def __init__(self):
        pass

    def ready_for_start_run(self) -> bool:
        # Check if the device is ready for a new run
        return \
            self._battery_okay() and \
            self._temperature_okay()

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
        # Multiple temp node need to be confirmed.
        return True

