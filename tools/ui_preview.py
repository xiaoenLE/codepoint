"""界面预览 & 自动截图 —— 改完界面自己看一眼，别凭空猜。

    python tools/ui_preview.py [输出目录]

它会：用 NullFacade（含 4 个假程序，能看出卡片布局）建出真的主窗口，
逐页/逐 Tab 截图，再截一张窄窗折叠态和一张呼吸灯，
最后把窗口销毁。全程约 5 秒。

为什么必须做这件事：**界面好看与否只能看**。而且 tkinter 的坑（属性名撞车、
圆角画不出来、文字被容器裁掉）在代码里完全看不出来。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codepoint.platform_win.winapi import (                        # noqa: E402
    GA_ROOT, HANDLE, HWND, RECT, byref, user32,
)
from codepoint.ui.bridge import UiBridge                           # noqa: E402
from codepoint.ui.main_window import (                             # noqa: E402
    PAGE_ABOUT, PAGE_GENERAL, PAGE_HOME, PAGE_QUICK, PAGE_RULES, MainWindow,
)
from codepoint.ui.ports import NullFacade                          # noqa: E402
from tools.grab_window import grab                                # noqa: E402

SHOTS: List[Tuple[str, str]] = [
    ("01_home", PAGE_HOME),
    ("02_rules", PAGE_RULES),
    ("03_quick", PAGE_QUICK),
    ("04_general", PAGE_GENERAL),
    ("05_about", PAGE_ABOUT),
]


def toplevel_hwnd(window) -> int:
    """拿到真正的顶层窗口句柄（winfo_id 给的是子窗口）。"""
    raw = int(window.winfo_id())
    root = int(user32.GetAncestor(HWND(raw), GA_ROOT) or 0)
    return root or raw


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        return _run(out_dir)
    except Exception:      # noqa: BLE001
        import traceback
        traceback.print_exc()
        return 1


def _run(out_dir) -> int:
    facade = NullFacade()
    bridge = UiBridge()
    w = MainWindow(facade, bridge, positioner=lambda: (260, 160))
    # 独立标题：便于"精确"识别预览窗口（教训：绝不再用宽松子串匹配去杀进程，
    # 曾经因为 'codepoint' 匹配到了 explorer 的文件夹窗口，把任务栏干掉了）
    w.root.title("码点助手 界面预览")
    w.root.update()
    w._pump()                     # 手动开事件泵（不进 mainloop）
    w.show()

    hwnd = toplevel_hwnd(w.root)
    print(f"顶层窗口 hwnd = {hwnd}")
    rect = RECT()
    user32.GetWindowRect(HWND(hwnd), byref(rect))
    print(f"窗口位置 = ({rect.left},{rect.top})-({rect.right},{rect.bottom})")

    ok = 0
    for name, page in SHOTS:
        w.show_page(page)
        for _ in range(24):       # 让 Tk 把重绘做完
            w.root.update()
            time.sleep(0.012)
        w.show()
        for _ in range(10):
            w.root.update()
            time.sleep(0.012)
        ok += 1 if grab(hwnd, out_dir / f"{name}.png") else 0

    # 「常规设置」页的下半部分（配置备份 / 关于配置）—— 一屏放不下，
    # 只截上半张的话这些改动等于没检查
    w.show_page(PAGE_GENERAL)
    for _ in range(16):
        w.root.update()
        time.sleep(0.012)
    page = w._pages.get(PAGE_GENERAL)
    if page is not None:
        page._canvas.yview_moveto(1.0)         # 滚到底
        for _ in range(20):
            w.root.update()
            time.sleep(0.012)
        ok += 1 if grab(hwnd, out_dir / "04b_general_bottom.png") else 0
        page._canvas.yview_moveto(0.0)

    # 「关于」页的下半部分（快捷键 / 本版更新）—— 同样一屏放不下
    w.show_page(PAGE_ABOUT)
    for _ in range(16):
        w.root.update()
        time.sleep(0.012)
    page = w._pages.get(PAGE_ABOUT)
    if page is not None:
        page._canvas.yview_moveto(1.0)         # 滚到底
        for _ in range(20):
            w.root.update()
            time.sleep(0.012)
        ok += 1 if grab(hwnd, out_dir / "05b_about_bottom.png") else 0
        page._canvas.yview_moveto(0.0)

    # 窄窗折叠态（先顶到最前，避免抓到别的窗口）
    w.root.geometry("860x600")
    w.show()
    for _ in range(24):
        w.root.update()
        time.sleep(0.012)
    ok += 1 if grab(hwnd, out_dir / "06_narrow.png") else 0

    # 补全预览窗（独立顶层窗口，单独抓屏幕）
    w.root.geometry("1000x660+260+160")
    for _ in range(10):
        w.root.update()
    from codepoint.core.card import Card

    bridge.publish(UiBridge.CARD_SHOW, card=Card.ready(
        "AI 补全",
        "VAR\n    fbDelay : TON;\nEND_VAR\n\nfbDelay(IN := xStart, PT := T#5S);",
        timeout=15))
    for _ in range(30):
        w.root.update()
        time.sleep(0.012)
    if w._card is not None and w._card.visible:
        ind_hwnd = toplevel_hwnd(w._card._win)
        ok += 1 if grab(ind_hwnd, out_dir / "07_card.png") else 0
    else:
        print("预览窗没显示出来（检查 _pump 与 indicator_enabled）")

    w.destroy()
    print(f"完成：{ok} 张截图 → {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
