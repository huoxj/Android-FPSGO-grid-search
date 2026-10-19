"""JSON API + static file server for the grid-search Pareto explorer.

Stdlib only (http.server).  Trace metrics come from ``src/report.py``; each
trace is analysed at most once and the result is cached on disk next to the
trace as ``<trace>.metrics.json`` so repeat visits are instant.
"""

import argparse
import json
import math
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent  # repo/src — makes `report` / `models` importable
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd  # noqa: E402

import report  # noqa: E402  (src/report.py)
from models.grid import GridRunMeta  # noqa: E402

STATIC_DIR = _HERE / "static"
METRICS_SUFFIX = ".metrics.json"
# Bump when the metrics payload shape changes: stale caches are recomputed.
CACHE_VERSION = 1

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}

# TraceProcessor is a heavy external process and not reliably thread-safe:
# serialize all metric computation.  The disk cache makes this cheap after
# the first scan.
_compute_lock = threading.Lock()
# trace path -> metrics dict (already resolved and analysed this process)
_metrics_memo: dict[str, dict] = {}


# ── metrics cache ───────────────────────────────────────────────────────

def _resolve_trace(trace_path: str, root: Path) -> Path | None:
    """Meta files store repo-root-relative paths; tolerate both spellings."""
    p = Path(trace_path)
    for cand in (p, root / p):
        if cand.is_file():
            return cand
    return None


def _read_cache(trace: Path) -> dict | None:
    cache = Path(str(trace) + METRICS_SUFFIX)
    if not cache.is_file():
        return None
    try:
        blob = json.loads(cache.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(blob, dict) or blob.get("v") != CACHE_VERSION:
        return None
    metrics = blob.get("metrics")
    return metrics if isinstance(metrics, dict) else None


def _write_cache(trace: Path, metrics: dict) -> None:
    cache = Path(str(trace) + METRICS_SUFFIX)
    try:
        cache.write_text(json.dumps({"v": CACHE_VERSION, "metrics": metrics}))
    except OSError as e:  # read-only trace dir: still serve, just re-analyse
        print(f"  (cannot write metrics cache: {e})", flush=True)


def metrics_for(trace: Path) -> dict:
    """Analyse a trace once, memoized in-process and cached on disk."""
    key = str(trace)
    hit = _metrics_memo.get(key)
    if hit is not None:
        return hit
    with _compute_lock:
        hit = _metrics_memo.get(key)
        if hit is not None:
            return hit
        metrics = _read_cache(trace)
        if metrics is None:
            print(f"analyzing {trace} ...", flush=True)
            metrics = report.analyze_trace(str(trace))
            _write_cache(trace, metrics)
            print(f"  cached -> {trace}{METRICS_SUFFIX}", flush=True)
        else:
            print(f"metrics cache hit: {trace}", flush=True)
        _metrics_memo[key] = metrics
        return metrics


# ── data assembly ───────────────────────────────────────────────────────

def _load_metas(root: Path) -> list[GridRunMeta]:
    metas: list[GridRunMeta] = []
    for f in sorted(root.glob("*.json")):
        if f.name.endswith(METRICS_SUFFIX):
            continue  # our own cache files live next to traces
        try:
            metas.append(GridRunMeta.model_validate_json(f.read_text()))
        except Exception as e:
            print(f"skip meta {f}: {e}", flush=True)
    metas.sort(key=lambda m: m.time)
    return metas


def load_runs(output_dir: str) -> tuple[list[dict], dict, int]:
    """(runs, abbr map, skipped count) for every analysable meta in the dir."""
    root = Path(output_dir)
    metas = _load_metas(root)
    abbr = {
        k: report._abbr(k) for m in metas for k in (m.grid_params or {})
    }
    runs, skipped = [], 0
    for m in metas:
        trace = _resolve_trace(m.trace_path, root)
        if trace is None:
            print(f"skip missing trace: {m.trace_path}", flush=True)
            skipped += 1
            continue
        met = metrics_for(trace)
        if not met:
            skipped += 1
            continue
        runs.append({
            "time": m.time.strftime("%H:%M:%S"),
            "time_iso": m.time.isoformat(),
            "trace": str(trace),
            "params": m.grid_params,
            "replay_index": m.replay_index,  # None = pre-grouping era (= card 0)
            "n_frames": met.get("n_frames"),
            "dur_s": met.get("dur_s"),
            "fps_avg": met.get("fps_avg"),
            "fps_std": met.get("fps_std"),
            # report.py's small/jank/severe -> jank_s/jank/jank_l
            "jank_s": met.get("small"),
            "jank": met.get("jank"),
            "jank_l": met.get("severe"),
            "power_avg_mw": met.get("power_avg_mw"),
            "power_median_mw": met.get("power_median_mw"),
            "discharge_ratio": met.get("discharge_ratio"),
        })
    return runs, abbr, skipped


def _num(v, nd: int = 3):
    """JSON-safe number: NaN/None -> None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _combo_key(params: dict) -> str:
    return json.dumps(params or {}, sort_keys=True)


def load_pareto(output_dir: str) -> dict:
    """Combo-level Pareto front (fps max / power min), via report.py."""
    runs, _, _ = load_runs(output_dir)
    if not runs:
        return {"front": [], "combos": []}

    rows = []
    has_power = False
    for r in runs:
        row = {
            "params": r["params"],
            "fps_avg": r["fps_avg"],
            "fps_std": r["fps_std"],
            "jank": r["jank"],
            "severe": r["jank_l"],
            "small": r["jank_s"],
        }
        for k in ("power_avg_mw", "power_median_mw", "discharge_ratio"):
            if r.get(k) is not None:
                row[k] = r[k]
                has_power = has_power or k == "power_avg_mw"
        rows.append(row)
    df = pd.DataFrame(rows)

    stats = report._combo_stats(df)
    # _combo_stats aggregates jank/severe but not small; same groupby key and
    # default sort => row order matches, so it can be attached directly.
    gb = df.groupby(df["params"].map(_combo_key))
    stats["jank_s"] = gb["small"].mean().to_numpy()
    front = report.pareto_front(stats)

    front_keys = {_combo_key(p) for p in front["params"].tolist()}
    combos = [_combo_record(rec, front_keys) for rec in stats.to_dict("records")]
    fwd = [
        _combo_record(rec, front_keys)
        for rec in front.to_dict("records")
    ]
    return {"front": fwd, "combos": combos, "has_power": has_power}


def _combo_record(rec: dict, front_keys: set[str]) -> dict:
    return {
        "params": rec["params"],
        "n": int(rec["n"]),
        "fps_avg": _num(rec.get("fps_avg")),
        "fps_std": _num(rec.get("fps_std")),
        "jank_s": _num(rec.get("jank_s"), 2),
        "jank": _num(rec.get("jank"), 2),
        "jank_l": _num(rec.get("jank_l"), 2),
        "power_avg_mw": _num(rec.get("power_avg_mw"), 1),
        "power_median_mw": _num(rec.get("power_median_mw"), 1),
        "discharge_ratio": _num(rec.get("discharge_ratio"), 4),
        "disch_ok": bool(rec.get("disch_ok")),
        "on_front": _combo_key(rec["params"]) in front_keys,
    }


def load_datasets(output_dir: str) -> dict:
    """Read-only dataset list for the chosen output root.

    Simplification: the CLI takes exactly one output dir.  We still report
    the sub-directories that actually hold traces (so the UI can show what
    was scanned), falling back to the root itself when there are none.
    """
    root = Path(output_dir).resolve()
    dirs = sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and next(p.glob("*.pftrace"), None) is not None
    ) if root.is_dir() else []
    if not dirs:
        dirs = [root.name]
    return {"root": str(root), "dirs": dirs}


# ── HTTP ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "grid-search-webui"
    protocol_version = "HTTP/1.1"
    output_dir = "."

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/datasets":
                self._json(load_datasets(self.output_dir))
            elif path == "/api/runs":
                runs, abbr, skipped = load_runs(self.output_dir)
                self._json({"runs": runs, "abbr": abbr, "skipped": skipped})
            elif path == "/api/pareto":
                self._json(load_pareto(self.output_dir))
            elif path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._static(path)
        except BrokenPipeError:
            pass
        except Exception as e:  # never kill the server on one bad request
            traceback.print_exc()
            self._json({"error": str(e)}, status=500)

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        # whitelist by basename: no traversal, no hidden files
        if name != Path(name).name or name.startswith("."):
            self._json({"error": "not found"}, status=404)
            return
        f = STATIC_DIR / name
        if not f.is_file():
            self._json({"error": "not found"}, status=404)
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            CONTENT_TYPES.get(f.suffix, "application/octet-stream"),
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def serve(output_dir: str, port: int = 8787, host: str = "127.0.0.1") -> None:
    root = Path(output_dir)
    if not root.is_dir():
        raise SystemExit(f"output dir not found: {root.resolve()}")
    Handler.output_dir = str(root.resolve())
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(
        f"grid-search webui: http://{host}:{port}/   output={Handler.output_dir}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        httpd.server_close()
