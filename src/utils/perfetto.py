import os
import re
import tempfile
from pathlib import Path

CONFIG_PATH = Path(os.getcwd()) / "perfetto_config.txtpb"

def _make_temp_config(duration_s: float) -> str:
    """Read config, substitute duration_ms, write to a temp file."""
    text = CONFIG_PATH.read_text()
    text = re.sub(
        r"duration_ms:\s*\d+",
        f"duration_ms: {int(duration_s * 1000)}",
        text,
    )
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txtpb", delete=False
    )
    tmp.write(text)
    tmp.close()
    return tmp.name

def 
