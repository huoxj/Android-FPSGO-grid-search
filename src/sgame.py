from time import sleep, monotonic
from pathlib import Path
from typing import NoReturn
import subprocess
import cv2
import numpy as np
import queue
import threading

from config import get_config
from utils.perfetto import start_perfetto_tracing
from utils.fpsgo import read_fpsgo_fps, apply_params
from utils.adb import adb_follow, adb_shell
from utils import ui

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

    # The game logs its own stage transitions, so the steps that are only
    # blind taps can still prove themselves. One stream for the whole entry.
    with _LogWatch("sgame_unity:I") as logs:
        # 2. Wait for the start button. Ads / update announcements cover it, so
        # every poll also tries to close one.
        _sync_point(
            "start_button",
            lambda: _find("start") is not None,
            timeout=60, interval=2,
            on_wait=lambda: _find_and_tap("close_x", raise_on_missing=False),
        )
        _tap("start")

        # 3. Lobby: the log line proves the lobby finished opening, then two
        # clean frames prove no popup is covering it. Polls close popups; the
        # rest banner is a lockout, not a retry.
        def _rest_check():
            # Health-system rest banner ("呵护双眼 请您休息一下"): tapping 确定
            # QUITS THE GAME. Raise so the caller runs the 15-min cooldown and
            # restarts the whole run; _exit_game happens there already.
            if _find("resting") is not None:
                raise RestLockout()

        def _clear_popups():
            _rest_check()
            # Try to close any popups, ads, etc
            for name in ["back_arrow", "close_x"]:
                if _find_and_tap(name, raise_on_missing=False):
                    return

        logs.wait("lobby_open", "ImpOpenLobbyStep6", timeout=60,
                  on_wait=_clear_popups)
        _sync_point(
            "lobby_clean",
            lambda: _is_in_lobby(ui.shot()),
            timeout=30, interval=1, stable=2,
            on_wait=_clear_popups,
        )

        # 4. Enter the replay. The picker / tab / card taps stay blind (the
        # game logs nothing between the lobby and the battle), but the confirm
        # is proven ~1.3s later by the work-mode line instead of 60s later by
        # the 0s needle, and the rest banner breaks the wait instead of
        # slipping past a post-hoc check.
        _tap("replay")
        sleep(2)
        _tap("local_tab")
        sleep(2)
        # The index comes from the CALLER (resolved per run), never from
        # config: this line once read conf.replay_index instead, silently
        # pinning every group to card 0 while the meta recorded the intended
        # index.
        _tap_replay_card(replay_index)
        sleep(2)
        _tap("confirm")
        logs.wait("replay_accepted", "watchWorkMode = Replay", timeout=20,
                  on_wait=_rest_check)

def _sync_timeline_0s():
    TARGET_TAG = "sgame_unity:I"
    TARGET_NEEDLE = ("Enter DoBattleStartFightStart")

    TIMEOUT_SECS = 60
    TIMEOUT_MSG = f"Sync replay timeline 0s timeout after {TIMEOUT_SECS}s"

    def _timeout() -> NoReturn:
        # Distinguish the rest-banner stall from a generic timeout: banner
        # present -> RestLockout (cooldown + rerun), else dump the stuck frame
        # and raise a sync timeout (generic retry ladder).
        if _find("resting") is not None:
            raise RestLockout()
        _dump_failure("battle_0s", TIMEOUT_SECS)
        raise SyncTimeout(TIMEOUT_MSG)

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
                _timeout()

            try:
                line = q.get(timeout=remaining)
            except queue.Empty:
                _timeout()

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


class SyncTimeout(TimeoutError):
    """A named entry step never reached its completion state; rerun the run."""


def _sync_point(name, ok, timeout, interval=1.0, stable=1, on_wait=None):
    """Wait until `ok()` held `stable` times in a row.

    Every step of the entry flow proves its own completion through one of
    these, so a stuck run fails at the step that actually stalled instead of
    surfacing as a timeline-sync timeout at the end.
    """
    deadline = monotonic() + timeout
    hits = 0
    while True:
        if monotonic() > deadline:
            _dump_failure(name, timeout)
            raise SyncTimeout(f"{name}: not reached within {timeout}s")
        if ok():
            hits += 1
            if hits >= stable:
                return
            continue        # confirm on a fresh frame right away
        hits = 0
        if on_wait is not None:
            on_wait()
        sleep(interval)


def _dump_failure(name: str, timeout: float):
    """Save the stuck frame and list which known templates are on it.

    The frame is what localises the failure: an unmodelled screen still shows
    whether we are in the lobby (replay visible), under a popup (close_x), or
    somewhere with no known chrome at all (picker / battle load).
    """
    img = ui.shot()
    out_dir = Path(get_config().output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # One file per step, overwritten: read it right after the failure.
    path = out_dir / f"sync_fail_{name}.png"
    cv2.imwrite(str(path), img)
    seen = [
        n for n, spec in _patterns.items()
        if spec[0].size and ui.find(img, spec) is not None
    ]
    print(f"[sync] step '{name}' not reached in {timeout}s; "
          f"visible templates={seen} frame={path}")


class _LogWatch:
    """One background `logcat -s <tag>` stream, many named needle waits.

    The game logs its own Unity-side stage transitions, so entry steps that are
    only blind taps can still prove themselves. `-T 1` starts at the tail of
    the ring buffer (it does not imply -d, so it keeps following): without it a
    previous run's needle would still be buffered and pass a fresh wait.
    """

    POLL = 0.3

    def __init__(self, tag: str):
        self._lines: list[str] = []
        self._cursor = 0
        self._p = subprocess.Popen(
            ["adb", "logcat", "-v", "monotonic", "-T", "1", "-s", tag],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace", bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        assert self._p.stdout is not None
        for line in self._p.stdout:
            self._lines.append(line)

    def wait(self, name: str, needle: str, timeout: float, on_wait=None):
        """Block until a *new* line contains `needle`; on timeout dump + raise."""
        deadline = monotonic() + timeout
        while True:
            while self._cursor < len(self._lines):
                line = self._lines[self._cursor]
                self._cursor += 1
                if needle in line:
                    return
            if self._p.poll() is not None:
                # Stream died (bad args, adb drop): never disguise it as a
                # slow game.
                raise RuntimeError(f"{name}: logcat stream ended")
            if monotonic() > deadline:
                _dump_failure(name, timeout)
                raise SyncTimeout(f"{name}: '{needle}' not seen in {timeout}s")
            if on_wait is not None:
                on_wait()
            sleep(self.POLL)

    def close(self):
        if self._p.poll() is None:
            self._p.terminate()
            try:
                self._p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._p.kill()
                self._p.wait()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


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

