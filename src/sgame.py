from time import sleep, monotonic
from pathlib import Path
import subprocess
import cv2
import numpy as np
import queue
import threading

from config import get_config
from utils.perfetto import start_perfetto_tracing
from utils.fpsgo import read_fpsgo_fps, apply_params
from utils.adb import adb_follow, adb_shell
from utils import misc, ui

def sgame_run(params: dict, replay_index: int) -> Path:
    conf = get_config()
    run_dur = conf.run_duration
    _prepare_resources()

    # Cleanly enter replay (replay_index picks the card in the picker grid)
    _reenter_replay(replay_index)
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
        # Measurement window start marker: lands in the trace's android.log,
        # the report extracts power stats between RUN_START/RUN_END.
        _grid_marker("RUN_START")
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
        # Window end marker must precede perfetto stop, else it misses the trace
        _grid_marker("RUN_END")
        # Stop perfetto tracing
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    _exit_game()

    return trace_tmp_path


def _reenter_replay(replay_index: int):
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
        # Health-system rest banner ("呵护双眼 请您休息一下"): tapping 确定
        # QUITS THE GAME. Raise so the caller runs the 15-min cooldown and
        # restarts the whole run; _exit_game happens there already.
        if _find("resting", ss) is not None:
            raise RestLockout()
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
    # The index comes from the CALLER (resolved per run), never from config:
    # this line once read conf.replay_index instead, silently pinning every
    # group to card 0 while the meta recorded the intended index.
    _tap_replay_card(replay_index)
    sleep(2)
    _tap("confirm")

def _sync_timeline_0s():
    TARGET_TAG = "sgame_unity:I"
    TARGET_NEEDLE = ("Enter DoBattleStartFightStart")

    TIMEOUT_SECS = 60
    TIMEOUT_MSG = f"Sync replay timeline 0s timeout after {TIMEOUT_SECS}s"

    with adb_follow(
        cmd = f"logcat -v monotonic -s {TARGET_TAG}",
        stdout=subprocess.PIPE
    ) as p:
        if p.stdout is None:
            raise RuntimeError("Failed to follow logcat")
        
        # Cannot use select.select on Windows
        q: queue.Queue[str] = queue.Queue()

        def _reader():
            assert p.stdout is not None
            for line in p.stdout:
                q.put(line)

        threading.Thread(target=_reader, daemon=True).start()

        deadline = monotonic() + TIMEOUT_SECS

        # Follow logcat until target needle. Or timeout exit
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError(TIMEOUT_MSG)

            try:
                line = q.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(TIMEOUT_MSG)

            if TARGET_NEEDLE in line:
                return

def _exit_game():
    # Exit the game completely
    conf = get_config()
    adb_shell(f"am force-stop {conf.sgame.package_name}")

def _grid_marker(text: str):
    # adb_shell swallows errors into its return string; a silently missing
    # marker would kill the whole power pipeline downstream, so fail loud.
    out = adb_shell(f"log -t grid {text}")
    if out.startswith("Error"):
        raise RuntimeError(f"grid marker failed: {out}")

# ========== ui utils ==========

_patterns: dict[str, ui.UiSpec] = {
    "start":       (np.empty((0, 0)), (0, 0, 2720, 1224), 0.90),
    "replay":      (np.empty((0, 0)), (1863, 0, 2096, 172), 0.90),
    "back_arrow":  (np.empty((0, 0)), (0, 0, 2720, 1224), 0.85),
    "close_x":     (np.empty((0, 0)), (0, 0, 2720, 1224), 0.85),
    # Health-system rest reminder banner + its 确定 button. Match threshold
    # 0.85 for headroom against popup animation; the "帮助" button looks
    # similar to 确定 (second-best 0.50 vs 1.00 on the reference frame)
    "resting":     (np.empty((0, 0)), (0, 0, 2720, 1224), 0.85),
    "resting_ok":  (np.empty((0, 0)), (0, 0, 2720, 1224), 0.85),
}

_fixed_centers = {
    "start":       (1354, 955),
    "replay":      (1979, 68),
    "local_tab":   (258, 836),
    "confirm":     (1570, 866),
}

def _tap_replay_card(index: int):
    # Replay picker is a 2D grid of cards. Measured on-device (2026-10-17):
    # first card center (560, 487), dx=346, dy=500, 6 per row, 2 visible
    # rows without scrolling -> supports repeat <= 12.
    conf = get_config()
    g = conf.sgame.replay_grid
    row, col = divmod(index, g["cols"])
    x = g["card0"][0] + col * g["dx"]
    y = g["card0"][1] + row * g["dy"]
    if row >= 2:
        raise ValueError(
            f"replay index {index} needs row {row}; only 2 rows visible "
            "without scrolling (repeat <= 12)"
        )
    print(f"tapping replay card {index} at ({x}, {y})")
    ui.tap(x, y)

def _prepare_resources():
    # Load all template images into memory
    conf = get_config()
    res_dir = Path(conf.sgame.resource_dir)
    for name, spec in _patterns.items():
        templ = cv2.imread(res_dir / f"{name}.png")
        if templ is None:
            raise FileNotFoundError(f"Missing template {name}.png")
        _patterns[name] = (templ, *spec[1:])

class RestLockout(Exception):
    """Health-system rest banner detected; tapping 确定 quits the game."""


def _find(name, img=None):
    if img is None:
        img = ui.shot()
    return ui.find(img, _patterns[name])

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

