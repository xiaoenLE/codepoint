"""重新生成 exe 图标 build/icon.ico（**打包前跑一次**）。

    python tools/make_icon.py            :: 生成 build/icon.ico（16/24/32/48/64/128/256）
    python tools/make_icon.py --preview  :: 顺便把各尺寸导出成 PNG 到 build/preview，肉眼检查

**为什么要有这个工具**：图标是代码画出来的（`codepoint/iconmaker.render_logo`），
而 `build/icon.ico` 是个**提交进仓库的二进制文件** —— 代码改了它不会自己变。

所以：**改了 `iconmaker` 里的任何形状/配色，都要跑一次这个脚本，然后重新打包。**
`tests/test_icon.py` 会校验 build/icon.ico 与代码一致，不一致就让测试红。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codepoint import iconmaker                                          # noqa: E402

TARGET = ROOT / "build" / "icon.ico"


def main() -> int:
    preview = "--preview" in sys.argv
    t0 = time.perf_counter()
    ok = iconmaker.build_ico(TARGET, iconmaker.ICON_SIZES)
    dt = time.perf_counter() - t0
    if not ok:
        print("生成失败")
        return 1

    sizes = iconmaker.ICON_SIZES
    print(f"已生成 {TARGET}")
    print(f"  尺寸   : {'/'.join(str(s) for s in sizes)}   "
          f"（{iconmaker.ICO_PNG_FROM}+ 用 PNG 帧）")
    print(f"  大小   : {TARGET.stat().st_size:,} 字节")
    print(f"  耗时   : {dt:.1f}s（纯 Python 4 倍超采样，256x256 最慢）")

    if preview:
        out = ROOT / "build" / "preview"
        out.mkdir(parents=True, exist_ok=True)
        for size in sizes:
            rows = iconmaker.render_logo(size)
            (out / f"logo_{size:03d}.png").write_bytes(iconmaker.png_bytes(rows))
        sheet = out / "logo_sheet.png"
        sheet.write_bytes(_sheet(sizes))
        print(f"  预览   : {out}\\logo_*.png   （拼图 {sheet.name} 一眼看各尺寸）")
    return 0


def _sheet(sizes, zoom: int = 4, gap: int = 8, bg=(0xF2, 0xF3, 0xF5)):
    """把各尺寸并排放大成一张对比图 —— 图标好看与否只能看，小尺寸糊不糊尤其要看。"""
    cells = []
    for size in sizes:
        rows = iconmaker.render_logo(size)
        big = [[rows[y // zoom][x // zoom] for x in range(size * zoom)]
               for y in range(size * zoom)]
        cells.append(big)

    width = sum(len(c[0]) for c in cells) + gap * (len(cells) + 1)
    height = max(len(c) for c in cells) + gap * 2
    canvas = [[(bg[0], bg[1], bg[2], 255)] * width for _ in range(height)]
    x0 = gap
    for cell in cells:
        cw, ch = len(cell[0]), len(cell)
        y0 = (height - ch) // 2
        for y in range(ch):
            for x in range(cw):
                r, g, b, a = cell[y][x]
                if a == 0:
                    continue                      # 透明处露出背景，才能看出圆角
                k = a / 255.0
                yr, yg, yb, _ = canvas[y0 + y][x0 + x]
                canvas[y0 + y][x0 + x] = (
                    int(r * k + yr * (1 - k)),
                    int(g * k + yg * (1 - k)),
                    int(b * k + yb * (1 - k)),
                    255,
                )
        x0 += cw + gap
    return iconmaker.png_bytes(canvas)


if __name__ == "__main__":
    raise SystemExit(main())
