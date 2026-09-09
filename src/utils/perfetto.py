import os
import sys
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from config import get_config

def _make_temp_config(config_path: str, duration_s: int) -> str:
    """Read config, substitute duration_ms, write to a temp file."""
    text = Path(config_path).read_text()
    text = re.sub(
        r"duration_ms:\s*\d+",
        f"duration_ms: {duration_s * 1000}",
        text,
    )
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txtpb", delete=False
    )
    tmp.write(text)
    tmp.close()
    return tmp.name

def start_perfetto_tracing():
    config = get_config()

    duration_s = config.run_duration
    ext_dur = duration_s + 10 # Extra 10s for safety
    tmp_config = _make_temp_config(config.perfetto.config_file, ext_dur)
    trace_tmp_path = Path(tempfile.gettempdir()) \
        / f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pftrace"

    cmd = [
        sys.executable,
        config.perfetto.record_script,
        "-c", tmp_config,
        "-o", trace_tmp_path,
        "-n",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        env=os.environ.copy()
    )   

    bench_start = datetime.now()
    return proc, bench_start, trace_tmp_path

