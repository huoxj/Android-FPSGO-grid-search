import os
import sys
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from config import get_config

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

    return proc, trace_tmp_path

