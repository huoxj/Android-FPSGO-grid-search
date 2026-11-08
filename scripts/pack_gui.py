"""用 flet pack 打 GUI exe，顺带绕开 flet-cli 1.1.2 的一个 Windows bug。

flet_cli/__pyinstaller/win_utils.py 恢复 PE overlay 时写的是
`open(exe_path, "ab", encoding="utf-8")`，二进制模式带 encoding 在 Python 3 里直接
ValueError。上游 1.1.2 是最新版，没修，所以打包前把站点包里这一行改掉（幂等），
再在同一个进程里调 flet 的 CLI —— 避免 uv 下一次 sync 把补丁冲掉。

用法与 flet pack 完全一致：
    uv run --group gui python scripts/pack_gui.py src/gui_main.py -n fpsgo-optim-gui ...
"""

import sys
from pathlib import Path

BUG = 'open(exe_path, "ab", encoding="utf-8")'
FIX = 'open(exe_path, "ab")'


def _win_utils_path() -> Path:
    p = Path(sys.prefix) / "Lib" / "site-packages" / "flet_cli" / "__pyinstaller" / "win_utils.py"
    if p.exists():            # Windows 布局
        return p
    import importlib.util

    spec = importlib.util.find_spec("flet_cli")   # namespace package，__file__ 是 None
    root = Path(next(iter(spec.submodule_search_locations)))
    return root / "__pyinstaller" / "win_utils.py"


def patch() -> None:
    p = _win_utils_path()
    src = p.read_text(encoding="utf-8")
    if FIX in src:
        return
    if BUG not in src:
        raise SystemExit(f"flet-cli changed: no buggy line in {p}; drop scripts/pack_gui.py")
    p.write_text(src.replace(BUG, FIX), encoding="utf-8")
    print(f"patched {p}")


def main() -> None:
    patch()
    from flet_cli.cli import main as flet_main

    sys.argv = ["flet", "pack", *sys.argv[1:]]
    flet_main()


if __name__ == "__main__":
    main()
