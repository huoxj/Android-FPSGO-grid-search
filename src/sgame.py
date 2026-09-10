from datetime import datetime
from time import sleep
from pathlib import Path

from config import get_config
from utils.perfetto import start_perfetto_tracing
from utils.fpsgo import read_fpsgo_fps
from utils.adb import adb

def sgame_run() -> tuple[datetime, datetime, Path]:
    conf = get_config()
    run_dur = conf.run_duration

    # Cleanly enter replay
    _reenter_replay()

    # Start perfetto recording immediately
    proc, _, trace_tmp_path = start_perfetto_tracing()

    # Timeline 30s, record the start_time
    _wait_timeline_30s()
    start_time = datetime.now()
    
    # Early stopping check:
    # reads fps from fpsgo_status per 5s, if the average fps is 5 fps lower than
    # the optimal (need to be check), then early stop
    fps_sum, check_count = 0, 0
    while (datetime.now() - start_time).total_seconds() < conf.early_stop_dur:
        t0 = datetime.now()
        fps = read_fpsgo_fps(conf.sgame.package_name_short)
        fps_sum += fps
        check_count += 1

        elapsed = (datetime.now() - t0).total_seconds()
        sleep(conf.early_stop_check_interval - elapsed)
    fps_avg = fps_sum / check_count
    if fps_avg < conf.early_stop_fps_threshold:
        print(f"Fps avg {fps_avg} is lower than threshold, early stopping")
        proc.terminate()
    else:
        # Dont stop! Wait til the end
        sleep(run_dur - (datetime.now() - start_time).total_seconds())
        if proc.poll() is not None:
            print(
                "Warning: Perfetto tracing stopped earlier than game process"
            )

    # Stop perfetto recording
    end_time = datetime.now()
    return_code = proc.wait()

    _exit_game()

    return (start_time, end_time, trace_tmp_path)

def _reenter_replay():
    conf = get_config()

    # 1. Restart game and make sure game is on
    adb(f"am force-stop {conf.sgame.package_name}")
    adb(f"am start com.tencent.tmgp.sgame/com.tencent.tmgp.sgame.SGameActivity")

    # 2. Enter the replay menu. Closes all ads or notifications
    # through the opening process

    # 2.1. Waiting & then press 'Enter game' button
    """Need image module to detect button:
    while not shown:
        detect button image
        sleep(1)
    """
    """Detected, press it
    press(location)
    """

    # 2.2. Close all notifications, ads, etc
    """
    t0 = datetime.now()
    while true :
        detect any 'closable'
        press close

        sleep(0.5)

        check if really entered main lobby; breaks
        timeout check t0+30s
    """

    # 3. Enter the replay. Wait until loaded
    """Press series of icons
    press(replay_icon_location)
    press(local_replay_tab)
    press(first_replay_item)
    """

    """Wait and polling
    t0 = datetime.now()
    while True:
        detect if entered replay
        sleep(1)
        timeout check t0+60s
    """

def _wait_timeline_30s():
    pass

def _exit_game():
    # Exit the game completely
    ...
