"""GUI 与真实数据/进程之间的唯一一层。

- 配置：读写仓库根目录的 config.toml（tomlkit，保留注释）
- 搜索：以子进程方式跑 CLI（源码 `python src/main.py`，打包后 `fpsgo-optim.exe`）
- 进度：读 output_dir/gui_status.json（CLI 在 GUI_STATUS=1 时写）
- 日志：tail output_dir/grid_search.log
- 结果：复用 src/webui/server.py 的指标计算（带磁盘缓存）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import tomlkit

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
# checkpoint 的元数据文件名：20251108_123456.json
RUN_META_RE = re.compile(r"^\d{8}_\d{6}\.json$")


# ── 配置 ────────────────────────────────────────────────────────────────
def config_file() -> Path:
    return Path.cwd() / "config.toml"


def load_config() -> dict:
    p = config_file()
    if not p.exists():
        example = Path.cwd() / "config.example.toml"
        if not example.exists():
            raise FileNotFoundError(f"找不到 {p} 或 config.example.toml")
        p.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    with open(p, "rb") as f:
        return tomllib.load(f)


def grid_rows(cfg: dict) -> list[dict]:
    """[grid] -> GUI 的行模型：[{keys: ["a","b"], vals: [(1,2), ...]}]"""
    rows = []
    for key, values in (cfg.get("grid") or {}).items():
        keys = [k.strip() for k in key.split(",")]
        vals: list[tuple] = []
        for v in values:
            if isinstance(v, (list, tuple)):
                if len(v) != len(keys):
                    raise ValueError(f"grid {key!r} 的值 {v!r} 长度和参数个数不一致")
                vals.append(tuple(v))
            else:
                vals.append(tuple([v] * len(keys)))
        rows.append({"keys": keys, "vals": vals})
    return rows


def _grid_toml(rows: list[dict]):
    tbl = tomlkit.table()
    for row in rows:
        keys = list(row["keys"])
        if len(keys) == 1:
            tbl[keys[0]] = [v[0] for v in row["vals"]]
        else:
            tbl[",".join(keys)] = [list(v) for v in row["vals"]]
    return tbl


def save_config(*, output_dir: str, run_duration: int, repeats: int,
                charge_target: int, warmup: bool, temps: tuple[int, int, int],
                attempts: int, backoff: int, early_dur: int, early_fps: int,
                profile: str, package: str, port: int, log_level: str,
                rows: list[dict]) -> None:
    p = config_file()
    doc = tomlkit.parse(p.read_text(encoding="utf-8"))
    doc["output_dir"] = output_dir
    doc["run_duration"] = int(run_duration)
    doc["repeats"] = int(repeats)
    doc["run_max_attempts"] = int(attempts)
    doc["backoff_initial_secs"] = int(backoff)
    doc["early_stop_dur"] = int(early_dur)
    doc["early_stop_fps_threshold"] = int(early_fps)
    doc["ready_temp_soc_max"] = int(temps[0] * 1000)
    doc["ready_temp_tsx"] = int(temps[1] * 1000)
    doc["ready_temp_battery"] = int(temps[2] * 1000)
    doc["webui_port"] = int(port)
    doc["log_level"] = log_level
    doc["grid"] = _grid_toml(rows)

    for name in ("power", "perfetto", "sgame"):
        if name not in doc:
            doc[name] = tomlkit.table()
    doc["power"]["charge_target"] = int(charge_target)
    doc["power"]["warmup"] = bool(warmup)
    doc["perfetto"]["profile"] = profile
    doc["sgame"]["package_name"] = package

    p.write_text(tomlkit.dumps(doc), encoding="utf-8")


def total_from_rows(rows: list[dict], repeats: int) -> int:
    n = 1
    for r in rows:
        n *= max(len(r["vals"]), 1)
    return n * max(int(repeats), 1)


def total_runs(cfg: dict) -> int:
    return total_from_rows(grid_rows(cfg), int(cfg.get("repeats", 1)))


def resolve_output_dir(cfg: dict) -> str:
    p = Path(cfg.get("output_dir", "gs/test")).expanduser()
    return str(p if p.is_absolute() else Path.cwd() / p)


def run_counts(output_dir: str) -> int:
    """输出目录里已经落盘的 run 数（checkpoint 的 json 元数据）。"""
    d = Path(output_dir)
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.is_file() and RUN_META_RE.match(f.name))


def resume_info(output_dir: str, total: int) -> dict:
    done = run_counts(output_dir)
    return {"done": done, "total": total, "incomplete": 0 < done < total}


# ── 搜索进程 ────────────────────────────────────────────────────────────
def cli_command() -> list[str]:
    if getattr(sys, "frozen", False):
        # 单文件打包：CLI 是同一个 exe 的 --cli 模式
        return [sys.executable, "--cli"]
    return [sys.executable, str(SRC / "main.py")]


def start_search(output_dir: str) -> subprocess.Popen:
    out = Path(output_dir) / ".gui"
    out.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GUI_STATUS"] = "1"
    stdout = open(out / "stdout.log", "a", encoding="utf-8")
    return subprocess.Popen(
        cli_command(), cwd=str(Path.cwd()), env=env,
        stdout=stdout, stderr=subprocess.STDOUT,
    )


def stop_search(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# ── 状态 / 日志 ─────────────────────────────────────────────────────────
def status_file(output_dir: str) -> Path:
    # .gui/ 子目录：checkpoint 只扫输出目录顶层，状态文件不会被当成 run 元数据
    return Path(output_dir) / ".gui" / "status.json"


def read_status(output_dir: str) -> dict | None:
    import json
    p = status_file(output_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_log(output_dir: str, offset: int) -> tuple[list[str], int]:  # noqa: D401
    p = Path(output_dir) / "grid_search.log"
    try:
        size = p.stat().st_size
    except OSError:
        return [], offset
    if size < offset:          # 被截断/换了目录
        offset = 0
    with open(p, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        data = f.read()
        return data.splitlines(), f.tell()


# ── 结果 ────────────────────────────────────────────────────────────────
def load_results(output_dir: str) -> tuple[list[dict], list[dict], list[dict]]:
    """(每次 run, 组合均值, 包络线组合)。首次分析 trace 可能很慢，调用方放线程里。"""
    from webui.server import load_pareto, load_runs

    runs, _abbr, _skipped = load_runs(output_dir)
    pareto = load_pareto(output_dir)
    return runs, pareto.get("combos", []), pareto.get("front", [])
