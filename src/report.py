import os
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from perfetto.trace_processor import TraceProcessor
from perfetto.trace_processor.api import TraceProcessorConfig

from config import get_config
from checkpoint import CheckpointMgr
from models.fpsgo_params import FpsgoParams

SQL_TS = "SELECT ts FROM actual_frame_timeline_slice ORDER BY ts"
SQL_MARKERS = "SELECT ts FROM android_logs WHERE tag='grid' ORDER BY ts"
SQL_POWER = (
    "SELECT c.ts, t.name, c.value FROM counter c "
    "JOIN counter_track t ON c.track_id = t.id "
    "WHERE t.name IN ('batt.power_mw', 'batt.current_ua') "
    "AND c.ts >= {lo} AND c.ts <= {hi} ORDER BY c.ts"
)
JANK_TIERS = {"small": 12.0, "jank": 16.67, "severe": 125.0}
# Cut 5s off each marker-window end: RUN_START lands while the game is
# still settling, RUN_END races the perfetto stop (verified口径, verify_ab)
TRIM_NS = 5_000_000_000


def _power_stats(tp) -> dict:
    """Whole-device power inside the RUN_START..RUN_END marker window.

    Meaningful only in charge-cut runs; old traces (no voltage counter,
    no markers) yield {} and the report shows '-'.
    """
    marks = tp.query(SQL_MARKERS).as_pandas_dataframe()
    if marks.empty:
        return {}
    lo = int(marks["ts"].iloc[0]) + TRIM_NS
    hi = int(marks["ts"].iloc[-1]) - TRIM_NS
    if hi <= lo:
        return {}
    df = tp.query(SQL_POWER.format(lo=lo, hi=hi)).as_pandas_dataframe()
    if df.empty:
        return {}
    pw = df[df["name"] == "batt.power_mw"]["value"].to_numpy(dtype=float)
    if not pw.size:
        return {}
    out = {
        "power_avg_mw": round(float(pw.mean()), 1),
        "power_median_mw": round(float(np.median(pw)), 1),
        "n_power": int(pw.size),
    }
    cur = df[df["name"] == "batt.current_ua"]["value"].to_numpy(dtype=float)
    if cur.size:
        # Ratio of discharging samples; <1.0 exposes charge-current
        # pollution (cut lost mid-run / charger reconnected)
        out["discharge_ratio"] = round(float((cur < 0).mean()), 4)
    return out


def analyze_trace(path: str) -> dict:
    tp_bin = Path("vendor/perfetto") / Path(
        "tp_shell_bin.exe" if os.name == "nt" else "tp_shell_bin"
    )
    if not tp_bin.is_file():
        raise FileNotFoundError(
            f"Missing tp_shell binary: {tp_bin}. Try reinstalling the program"
        )

    tp = TraceProcessor(
        file_path=path,
        config=TraceProcessorConfig(bin_path=str(tp_bin))
    )

    try:
        ts = tp.query(SQL_TS) \
               .as_pandas_dataframe()["ts"] \
               .to_numpy(dtype=np.int64)
        if ts.size < 2:
            return {}
        gaps_ms = np.diff(ts) / 1e6
        fps_s = np.bincount((ts - ts[0]) // 1_000_000_000)[1:-1]
        return {
            "n_frames": int(ts.size),
            "dur_s": float((ts[-1] - ts[0]) / 1e9),
            "fps_avg": float(fps_s.mean() if fps_s.size else 0.0),
            "fps_std": float(fps_s.std(ddof=1) if fps_s.size > 1 else 0.0),
            **{
                f"{k}": int((gaps_ms > t).sum()) for k, t in JANK_TIERS.items()
            },
            **_power_stats(tp),
        }
    finally:
        tp.close()


def collect(output_dir: str) -> pd.DataFrame:
    rows = []
    for m in CheckpointMgr(output_dir).existing_runs:
        if not Path(m.trace_path).is_file():
            print(f"skip missing trace: {m.trace_path}")
            continue
        r = analyze_trace(m.trace_path)
        if r:
            r.update(time=m.time.strftime("%H:%M:%S"), params=m.grid_params)
            rows.append(r)
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


# ── 表格 ────────────────────────────────────────────────────────────────

def _abbr(key: str) -> str:
    fi = FpsgoParams.model_fields.get(key)
    if not fi:
        return key
    extra = fi.json_schema_extra
    if not isinstance(extra, dict):
        return key
    io = extra.get("fpsgo_io")
    abbr = io.get("abbr") if isinstance(io, dict) else None
    return abbr if isinstance(abbr, str) else key


def _mtable(df: pd.DataFrame) -> str:
    has_power = "power_avg_mw" in df.columns
    d = pd.DataFrame({
        "#": range(1, len(df) + 1),
        "time": df["time"],
        "n_frames": df["n_frames"].astype(int),
        "dur_s": df["dur_s"].round(1),
        "fps_avg": df["fps_avg"].round(2),
        "fps_std": df["fps_std"].round(2),
        "jank_s": df["small"].astype(int),
        "jank": df["jank"].astype(int),
        "jank_l": df["severe"].astype(int),
    })
    if has_power:
        # Traces without voltage counter/markers carry NaN -> '-'
        d["P_avg_mW"] = df["power_avg_mw"].round(0)
        d["disch%"] = (df["discharge_ratio"] * 100).round(1)
        d = d.fillna({"P_avg_mW": "-", "disch%": "-"})
    return d.to_string(index=False)


def _ptable(df: pd.DataFrame) -> str:
    p = pd.json_normalize(df["params"].tolist())
    p = p.rename(columns=lambda k: _abbr(k))
    p.insert(0, "#", range(1, len(p) + 1))
    return p.to_string(index=False)

def _gmean(gb, col: str, nd: int = 1) -> list[float]:
    return [round(float(v), nd) for v in gb[col].mean().to_numpy()]

def top5(df: pd.DataFrame, n: int = 5) -> str:
    top = df.sort_values("fps_avg", ascending=False).head(n)
    return _mtable(top) + "\n\nparams\n" + _ptable(top)


# ── 报告 ────────────────────────────────────────────────────────────────

def full_report(df: pd.DataFrame, output_dir: str) -> str:
    g_fps, g_jank = df["fps_avg"].mean(), df["jank"].mean()
    L = [
        f"# grid-search report  generated {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"output_dir {output_dir}   runs {len(df)}",
        "jank_s/jank/jank_l = gap>12ms/16.67ms/125ms 帧数 (独立计数可重叠); ",
        "fps_avg/std = 每秒帧数 (1s bucket 去首尾)",
        "",
        "## runs  (metrics)",
        _mtable(df),
        "",
        "## runs  (params)",
        _ptable(df),
    ]

    gb = df.groupby(df["params"].map(lambda d: json.dumps(d, sort_keys=True)))
    L += ["", "## combos"]
    if len(gb) == len(df):
        L.append(f"每个 param set 只跑 1 次 ({len(gb)} sets), 同 runs")
    else:
        first = gb.first().reset_index()
        agg = pd.DataFrame({
            "#": range(1, len(gb) + 1),
            "n": [len(sub) for _, sub in gb],
            "dur_s": _gmean(gb, "dur_s"),
            "fps_avg": _gmean(gb, "fps_avg", 2),
            "fps_std": _gmean(gb, "fps_std", 2),
            "jank_s": _gmean(gb, "small"),
            "jank": _gmean(gb, "jank"),
            "jank_l": _gmean(gb, "severe"),
        })
        L += [
            "metrics", agg.to_string(index=False), "", "params", _ptable(first)
        ]

    L += [
        "",
        f"## axes   global fps={g_fps:.2f} jank={g_jank:.1f} (每 run 均值)"
    ]
    for k in list(df.iloc[0]["params"]):
        g = df.groupby(df["params"].map(lambda d: d[k]))
        if g.ngroups < 2:
            continue
        rows = []
        for v, sub in g:
            rows.append([
                v, len(sub),
                f"{float(sub['fps_avg'].to_numpy(dtype=float).mean()):.2f}",
                f"{float(sub['fps_avg'].to_numpy(dtype=float).mean()) - g_fps:+.2f}",
                round(float(sub["small"].to_numpy(dtype=float).mean())),
                round(float(sub["jank"].to_numpy(dtype=float).mean())),
                f"{float(sub['jank'].to_numpy(dtype=float).mean()) - g_jank:+.1f}",
                round(float(sub["severe"].to_numpy(dtype=float).mean()))
            ])

        L.append(f"  {_abbr(k)}")
        L.append(
            pd.DataFrame(rows, columns=[
                "value", "n", "fps_avg", "d_fps", "jank_s", "jank", "d_jank", 
                "jank_l"
            ]).to_string(index=False)
        )
    return "\n".join(L) + "\n"


def report(output_dir: str, top_n: int = 5) -> Path:
    df = collect(output_dir)
    if df.empty:
        raise RuntimeError(f"no analyzable trace in {output_dir}")
    print(f"\n=== top{top_n} by fps_avg ===")
    print(top5(df, top_n))
    path = Path(output_dir) / f"report_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(full_report(df, output_dir))
    print(f"\nreport: {path}")
    return path

