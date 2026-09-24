import re
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from config import get_config
from utils.record_trace import (
    setup_arguments, start_trace, request_stop, _stop_requested
)

class _Recorder:
    def __init__(self, thread, err):
        self._thread = thread
        self._err = err

    def poll(self):
        if self._thread.is_alive():
            return None
        return 1 if self._err else 0

    def terminate(self):
        request_stop()

    def kill(self):
        request_stop()

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("record_trace", timeout or 0.0)
        if self._err:
            raise self._err[0]

def _make_temp_config(config_path: Path, duration_s: int) -> str:
    """Read config, substitute duration_ms, write to a temp file."""
    text = config_path.read_text()
    text = re.sub(
        r"duration_ms:\s*\d+",
        f"duration_ms: {duration_s * 1000}",
        text,
    )
    
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txtpb", delete=False
    ) as tmp:
        tmp.write(text)

    return tmp.name

def start_perfetto_tracing(name: str, duration_override: int | None = None):
    config = get_config()

    config_path = Path(config.perfetto.config_dir) / f"{name}.txtpb"
    duration_s = config.run_duration if duration_override is None \
                    else duration_override
    tmp_config = _make_temp_config(config_path, duration_s)
    trace_tmp_path = Path(tempfile.gettempdir()) \
        / f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pftrace"

    try:
        args = setup_arguments([
            "-c", tmp_config, "-o", str(trace_tmp_path), "-n"
        ])
    except SystemExit as e:
        raise RuntimeError(
            f"record_trace init failed (adb not in PATH?): {e}"
        ) from e

    _stop_requested.clear()
    err: list[BaseException] = []

    def _run():
        try:
            start_trace(args, print_log=False)
        except BaseException as e:
            err.append(e)
        finally:
            Path(tmp_config).unlink(missing_ok=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return _Recorder(t, err), trace_tmp_path

