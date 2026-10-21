"""对话框预览 & 自动截图 —— 改对话框布局后自己看一眼，别凭空猜。

    python tools/dialog_preview.py

它会把「编辑快捷输入」对话框（带默认的 ``///`` 片段）真实建出来、截图到
``build/preview_dialog/``，并把控件尺寸写进 ``measure.txt``。

为什么单独做一个：``tools/ui_preview.py`` 只截主窗口，对话框装不下。
而对话框恰恰是最容易出布局问题的地方 —— **这个工具刚抓到一个真问题**：
「插入变量」那排按钮每个固定占 102px（ttk 的 TButton 自带默认宽度，
连单字「年」也是），一行只放得下 4 个，超出的被裁成半个字。
只靠读代码永远看不出来，``measure.txt`` 里的 reqW 一行就露馅了。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codepoint.core.defaults import default_quick_actions              # noqa: E402
from codepoint.platform_win.winapi import (                            # noqa: E402
    GA_ROOT, HWND, byref, user32,
)
from codepoint.ui import styles                                        # noqa: E402
from codepoint.ui.dialogs import QuickActionDialog                     # noqa: E402
from tools.grab_window import grab                                     # noqa: E402


def toplevel_hwnd(window) -> int:
    """拿到真正的顶层窗口句柄（winfo_id 给的是子窗口）。"""
    raw = int(window.winfo_id())
    root = int(user32.GetAncestor(HWND(raw), GA_ROOT) or 0)
    return root or raw


def _dump_sizes(widget, depth=0, lines=None):
    """把整棵控件树的请求宽度写下来 —— 布局出问题时看这一份就够。"""
    lines = lines if lines is not None else []
    for child in widget.winfo_children():
        text = ""
        try:
            text = child.cget("text")
        except Exception:      # noqa: BLE001 —— 不是所有控件都有 text
            pass
        lines.append("  " * depth
                     + f"{child.winfo_class()} reqW={child.winfo_reqwidth()}"
                       f" x={child.winfo_x()} text={text!r}")
        _dump_sizes(child, depth + 1, lines)
    return lines


def main() -> int:
    import tkinter as tk

    out_dir = ROOT / "build" / "preview_dialog"
    out_dir.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    root.geometry("200x100+40+40")
    styles.install_styles(root)
    root.update()

    sample = next(a for a in default_quick_actions() if a.trigger == "///")
    dlg = QuickActionDialog(root, action=sample)
    dlg.deiconify()
    dlg.update_idletasks()
    height = max(dlg._height, dlg.winfo_reqheight())
    dlg.geometry(f"{dlg._width}x{height}+300+120")
    dlg.lift()
    for _ in range(30):
        dlg.update()
        time.sleep(0.015)

    ok = grab(toplevel_hwnd(dlg), out_dir / "quick_dialog.png")

    report = [f"对话框 {dlg.winfo_width()}x{height}（内容需求高 {dlg.winfo_reqheight()}）",
              f"body 宽 = {dlg.body.winfo_width()}"]
    report += _dump_sizes(dlg)
    report.append(f"截图 {'OK' if ok else '失败'} -> {out_dir / 'quick_dialog.png'}")
    (out_dir / "measure.txt").write_text("\n".join(report), encoding="utf-8")

    dlg.destroy()
    root.destroy()
    import os
    os._exit(0)          # Tk 退出时本机偶发挂住，直接结束进程


if __name__ == "__main__":
    raise SystemExit(main())
