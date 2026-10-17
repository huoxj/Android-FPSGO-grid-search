"""``uv run python -m src.webui --output gs/0919 [--port 8787]``."""

import argparse

from .server import serve


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m src.webui",
        description="Standalone web UI for grid-search output (Pareto front).",
    )
    ap.add_argument(
        "--output", required=True,
        help="grid-search output dir holding the *.json run metas",
    )
    ap.add_argument(
        "--port", type=int, default=8787, help="listen port (default 8787)",
    )
    ap.add_argument(
        "--host", default="127.0.0.1", help="bind address (default 127.0.0.1)",
    )
    a = ap.parse_args(argv)
    serve(a.output, a.port, a.host)


if __name__ == "__main__":
    main()
