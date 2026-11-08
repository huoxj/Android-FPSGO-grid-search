# fpsgo-gui entry: 桌面窗口跑 GUI；设 GUI_WEB=1 则开浏览器版。
# 和数据层解耦：这里只负责把 src 放进 sys.path 再转发给 gui.app.run。
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gui.app import run  # noqa: E402


if __name__ == "__main__":
    run()
