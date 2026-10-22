# fpsgo-webui entry: serve the result UI over an existing output dir.
# Kept plain (outside the PyArmor pack) - it only forwards to webui.server.
import argparse

from config import get_config
from webui.server import serve


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="fpsgo-webui",
        description="Serve the grid-search result UI over an existing output dir.",
    )
    ap.add_argument(
        "--output", default=get_config().output_dir,
        help="grid-search output dir (default: config.toml output_dir)",
    )
    ap.add_argument("--port", type=int, default=get_config().webui_port)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    serve(a.output, a.port, a.host)


if __name__ == "__main__":
    main()
