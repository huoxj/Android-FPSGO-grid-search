"""重新生成 GUI 内嵌的中文字体子集。

字体跟着代码提交（Windows 构建机不一定有 Noto CJK），所以改了界面文案之后要跑一次：

    uv run python scripts/gen_gui_font.py

它会扫描 GUI 源码里出现的所有非 ASCII 字符，从系统 Noto Sans CJK 里裁出子集。
输出文件名带版本号（cjk3.otf），换文件名是为了绕开浏览器/Flutter 的缓存；
换完记得同步 src/gui/app.py 里的 page.fonts。
"""

from pathlib import Path

from fontTools.subset import main as subset_main

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "src/gui/app.py", ROOT / "src/gui_backend.py", ROOT / "src/status.py"]
NOTO = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
OUT = ROOT / "src/gui/assets/fonts/cjk3.otf"
PUNCT = "·×—…℃“”‘’、。，：；！？（）《》【】→←↑↓≥≤"


def main() -> None:
    chars = {chr(c) for c in range(0x20, 0x7F)}
    for p in SOURCES:
        chars |= {c for c in p.read_text(encoding="utf-8") if ord(c) > 127}
    chars |= set(PUNCT)

    charset = ROOT / "build_chars.txt"
    charset.write_text("".join(sorted(chars)), encoding="utf-8")
    subset_main([str(NOTO), "--font-number=2", f"--text-file={charset}",
                 f"--output-file={OUT}", "--layout-features=*"])
    charset.unlink()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(chars)} chars)")


if __name__ == "__main__":
    main()
