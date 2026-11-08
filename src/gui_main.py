# fpsgo-optim-gui 入口：双击开 GUI；带 --cli 时跑命令行主程序。
# 打包成单文件后 CLI 不再是独立 exe，而是同一个 exe 的另一个模式（GUI 起搜索子进程时用）。
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gui.app import run  # noqa: E402


def main():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from main import main as cli_main
        try:
            cli_main()
        except KeyboardInterrupt:     # GUI 停止信号：收尾已跑完，不用打 traceback
            raise SystemExit(130)
    else:
        run()


if __name__ == "__main__":
    main()
