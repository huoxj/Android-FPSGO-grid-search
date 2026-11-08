"""机器可读的搜索状态，给 GUI 轮询用。

只在 `GUI_STATUS=1` 时启停（GUI 启动子进程时设置），CLI 行为不变：
输出目录下多一个 `gui_status.json`，每次变化原子替换。

字段与 GUI 的三块内容一一对应：
  state / total / done / skipped / current   总进度与 ETA
  stage / stage_started_at / stages          当前 run 的阶段轴
  battery / current_ma / temps / targets     充电、热机、冷却面板
  params / group / replay                    当前 run 在跑什么
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_ENABLED = os.environ.get("GUI_STATUS") == "1"

# 采样间隔：只在这些阶段读设备，录制阶段不打扰测量
_SAMPLE_STAGES = ("charge", "warmup", "ready")
_SAMPLE_INTERVAL = 5

_writer: "StatusWriter | None" = None
_lock = threading.Lock()
_stop_handler = None
_MAIN_THREAD_ID = threading.main_thread().ident


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StatusWriter:
    def __init__(self, output_dir: str, total: int, repeats: int, combos: int,
                 seed: int | None):
        from config import get_config

        conf = get_config()
        # .gui/ 子目录：checkpoint 只扫输出目录顶层，不把状态文件当 run 元数据
        self.path = Path(output_dir) / ".gui" / "status.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state: dict = {
            "state": "searching",
            "started_at": _now(),
            "updated_at": _now(),
            "pid": os.getpid(),
            "output_dir": str(output_dir),
            "total": total,
            "done": 0,
            "skipped": 0,
            "current": 0,
            "combos": combos,
            "repeats": repeats,
            "seed": seed,
            "stage": None,
            "stage_started_at": None,
            "stages": [],
            "run_started_at": None,
            "params": {},
            "group": None,
            "replay": None,
            "battery": None,
            "current_ma": None,
            "temps": {},
            "targets": {
                "zone0": conf.ready_temp_soc_max,
                "zone44": conf.ready_temp_tsx,
                "zone53": conf.ready_temp_battery,
            },
            "run_duration": conf.run_duration,
            "error": None,
        }
        self._stop = threading.Event()
        self._stop_file = self.path.parent / "stop"
        self._sampler = threading.Thread(target=self._sample_loop, daemon=True)
        self._write()
        self._sampler.start()
        threading.Thread(target=self._watch_stop, daemon=True).start()

    # ── 写盘 ────────────────────────────────────────────────────────────
    def _write(self) -> None:
        with _lock:
            self.state["updated_at"] = _now()
            blob = json.dumps(self.state, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(blob, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot write gui status: %s", e)

    def _patch(self, **fields) -> None:
        self.state.update(fields)
        self._write()

    # ── 事件 ────────────────────────────────────────────────────────────
    def run_start(self, index: int, params: dict, group: int, replay: int | None):
        self._patch(current=index + 1, params=dict(params), group=group,
                    replay=replay, run_started_at=_now(), stage=None,
                    stage_started_at=None, stages=[])

    def stage(self, name: str) -> None:
        now = _now()
        stages = self.state["stages"]
        if stages and stages[-1]["name"] == name and stages[-1]["ended_at"] is None:
            return
        if stages and stages[-1]["ended_at"] is None:
            stages[-1]["ended_at"] = now
        stages.append({"name": name, "started_at": now, "ended_at": None})
        self._patch(stage=name, stage_started_at=now)

    def skipped(self) -> None:
        self._patch(skipped=self.state["skipped"] + 1)

    def run_end(self) -> None:
        self._patch(done=self.state["done"] + 1, stage=None, stage_started_at=None)

    def finish(self) -> None:
        self._patch(state="finished", stage=None)
        self._stop.set()

    def error(self, message: str) -> None:
        self._patch(state="stopped", error=str(message))
        self._stop.set()

    def close(self) -> None:
        """进程退出兜底：没走到 finish 就是被中断/出错。"""
        self._stop.set()
        if self.state["state"] == "searching":
            self._patch(state="stopped")

    # ── 停止 ────────────────────────────────────────────────────────────
    def _watch_stop(self) -> None:
        """GUI 点停止 = 写 .gui/stop。这里收尾（熄屏、恢复充电）再退出。

        不能靠 terminate()：Windows 上是硬杀，finally 不会跑，手机就亮着。
        """
        while not self._stop.wait(1.0):
            if not self._stop_file.exists():
                continue
            try:
                self._stop_file.unlink()
            except OSError:
                pass
            log.info("stop requested by GUI: tearing down, then exiting")
            self._patch(state="stopped", stopped_at=_now(), error="已手动停止")
            self._stop.set()
            handler = _stop_handler
            if handler is not None:
                try:
                    handler()
                except Exception as e:
                    log.warning("stop teardown failed: %s", e)
            _interrupt_main()
            time.sleep(3)      # 给主线程走完 finally 的机会
            os._exit(0)

    # ── 采样 ────────────────────────────────────────────────────────────
    def _sample_loop(self) -> None:
        from utils import device_utils as du

        while not self._stop.wait(_SAMPLE_INTERVAL):
            if self.state.get("stage") not in _SAMPLE_STAGES:
                continue
            try:
                temps = {f"zone{z}": du.read_temp(z) for z in (0, 44, 53)}
                self._patch(battery=du.read_capacity(),
                            current_ma=du.read_current_ma(), temps=temps)
            except Exception:
                # 设备忙/断连都正常，下一轮再试；不要污染搜索流程
                continue


# ── 模块级门面：没启用就是 no-op ────────────────────────────────────────
def init(output_dir: str, total: int, repeats: int, combos: int,
         seed: int | None = None) -> None:
    global _writer
    if not _ENABLED:
        return
    try:
        _writer = StatusWriter(output_dir, total, repeats, combos, seed)
        atexit.register(close)
    except Exception as e:
        log.warning("cannot start gui status writer: %s", e)
        _writer = None


def on_stop(handler) -> None:
    """注册停止时的收尾动作（main.py 里给 _teardown）。"""
    global _stop_handler
    _stop_handler = handler


def _interrupt_main() -> None:
    """主线程可能卡在 sleep/backoff 里，用异步异常把它叫醒。"""
    try:
        import ctypes

        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(_MAIN_THREAD_ID), ctypes.py_object(KeyboardInterrupt))
    except Exception:
        pass


def _call(name: str, *args) -> None:
    if _writer is None:
        return
    try:
        getattr(_writer, name)(*args)
    except Exception as e:
        log.warning("gui status %s failed: %s", name, e)


def run_start(index: int, params: dict, group: int, replay: int | None) -> None:
    _call("run_start", index, params, group, replay)


def on_stage(name: str) -> None:
    _call("stage", name)


def skipped() -> None:
    _call("skipped")


def run_end() -> None:
    _call("run_end")


def finish() -> None:
    _call("finish")


def close() -> None:
    _call("close")
