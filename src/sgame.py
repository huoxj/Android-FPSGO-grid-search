from time import sleep, monotonic
from pathlib import Path
import subprocess
import select
import cv2
import numpy as np

from config import get_config
from utils.perfetto import start_perfetto_tracing
from utils.fpsgo import read_fpsgo_fps, apply_params
from utils.adb import adb_follow, adb_shell
from utils import misc, ui

def sgame_run(params: dict) -> Path:
    conf = get_config()
    run_dur = conf.run_duration
    _prepare_resources()

    # Cleanly enter replay
    _reenter_replay()
    # Start perfetto recording immediately
    proc, trace_tmp_path = start_perfetto_tracing(
        name = "sgame",
        duration_override=run_dur + 120
    )

    try:
        # Timeline 0s
        _sync_timeline_0s()
        timeline_30s = monotonic() + 30
        # Apply params here, so cfreq wont be overridden by game
        sleep(2)
        apply_params(params, conf.sgame.package_name)
        # Timeline 30s
        sleep(timeline_30s - monotonic())
        perfetto_expected_deadline = monotonic() + run_dur
        early_stop_deadline = monotonic() + conf.early_stop_dur

        # Early stopping check:
        # reads fps from fpsgo_status per 5s, if the average fps is 5 fps lower
        # than the optimal (need to be check), then early stop
        fps_sum, check_count = 0, 0
        while monotonic() < early_stop_deadline:
            deadline = monotonic() + conf.early_stop_check_interval
            fps_sum += read_fpsgo_fps(conf.sgame.package_name_short)
            check_count += 1
            sleep(max(0, deadline - monotonic()))
        fps_avg = fps_sum / check_count

        if fps_avg < conf.early_stop_fps_threshold:
            print(f"Fps avg {fps_avg} is lower than threshold, early stopping")
        else:
            # Dont stop! Sleep and wait until perfetto stops
            sleep(perfetto_expected_deadline - monotonic() + 5)
            if proc.poll() is not None:
                print("Warn: Perfetto tracing stopped earlier than record dur")
    except Exception:
        trace_tmp_path.unlink(missing_ok=True)
        raise
    finally:
        # Stop perfetto tracing
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    _exit_game()

    return trace_tmp_path


def _reenter_replay():
    conf = get_config()

    # 1. Restart game and make sure game is on
    adb_shell(f"am force-stop {conf.sgame.package_name}")
    adb_shell(
        "am start "
        f"{conf.sgame.package_name}/{conf.sgame.activity_name}"
    )

    # 2. Enter the replay menu. Closes all ads or notifications
    # through the opening process
    
    # 2.1. Waiting & then press 'Enter game' button
    misc.wait_for(
        lambda: _find("start"), timeout=60, interval=2
    )
    _tap("start")

    # 2.2. Close all notifications, ads, etc
    lobby_detect_times = 0
    deadline = monotonic() + 60
    while lobby_detect_times < 2:
        # Timeout check 60s
        if monotonic() > deadline:
            raise TimeoutError("Did not enter lobby after 60s")

        ss = ui.shot()
        if _is_in_lobby(ss):
            lobby_detect_times += 1
            continue
        lobby_detect_times = 0
        # Try to close any popups, ads, etc
        for name in ["back_arrow", "close_x"]:
            if _find_and_tap(name, raise_on_missing=False):
                break
        sleep(1)

    # 3. Enter the replay. Wait until loaded
    _tap("replay")
    sleep(2)
    _tap("local_tab")
    sleep(2)
    _tap("replay_card")
    sleep(2)
    _tap("confirm")

def _sync_timeline_0s():
    TARGET_TAG = "sgame_unity:I"
    TARGET_NEEDLE = (
        "ApolloHelperCNV5 MSDK OnStatusChangeEvent, "
        "currentStatus: 3,statusRet.ThirdCode = 3"
    )

    TIMEOUT_SECS = 60
    TIMEOUT_MSG = f"Sync replay timeline 0s timeout after {TIMEOUT_SECS}s"

    with adb_follow(
        cmd = f"logcat -v monotonic -s {TARGET_TAG}",
        stdout=subprocess.PIPE
    ) as p:
        if p.stdout is None:
            raise RuntimeError("Failed to follow logcat")

        deadline = monotonic() + TIMEOUT_SECS

        # Follow logcat until target needle. Or timeout exit
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError(TIMEOUT_MSG)
            
            rlist, _, _ = select.select([p.stdout], [], [], remaining)
            if not rlist:
                raise TimeoutError(TIMEOUT_MSG)

            line = p.stdout.readline()
            if TARGET_NEEDLE in line:
                return

def _exit_game():
    # Exit the game completely
    conf = get_config()
    adb_shell(f"am force-stop {conf.sgame.package_name}")

# ========== ui utils ==========

_patterns: dict[str, ui.UiSpec] = {
    "start":       (np.empty((0, 0)), (0, 0, 2720, 1224), 0.90),
    "replay":      (np.empty((0, 0)), (1863, 0, 2096, 172), 0.90),
    "back_arrow":  (np.empty((0, 0)), (0, 0, 2720, 1224), 0.85),
    "close_x":     (np.empty((0, 0)), (0, 0, 2720, 1224), 0.85)
}

_fixed_centers = {
    "start":       (1354, 955),
    "replay":      (1979, 68),
    "local_tab":   (258, 836),
    "replay_card": (560, 487),
    "confirm":     (1570, 866),
}

def _prepare_resources():
    # Load all template images into memory
    conf = get_config()
    res_dir = Path(conf.sgame.resource_dir)
    for name, spec in _patterns.items():
        templ = cv2.imread(res_dir / f"{name}.png")
        if templ is None:
            raise FileNotFoundError(f"Missing template {name}.png")
        _patterns[name] = (templ, *spec[1:])

def _find(name):
    return ui.find(ui.shot(), _patterns[name])

def _tap(name):
    ui.tap(*_fixed_centers[name])

def _find_and_tap(name, raise_on_missing=True) -> bool:
    loc = _find(name)
    if loc is not None:
        ui.tap(*loc)
        return True
    if raise_on_missing:
        raise RuntimeError(f"Cannot find {name} on screen")
    return False

def _is_in_lobby(img) -> bool:
    if ui.find(img, _patterns["replay"]) is None:
        return False
    return (
        ui.find(img, _patterns["close_x"]) is None and
        ui.find(img, _patterns["back_arrow"]) is None
    )

