"""「选择程序」对话框 —— 用户不用再手输进程名。

打开时列出**所有有可见窗口的程序**（图标 + 名字 + 完整路径），带搜索框，
点一下选中、点「确认」加入列表。取不到图标的程序显示字母头像，
所以列表里永远不会出现"空白一行"。

两个工程细节：
1. 列表行复用主页的 ``ProgramCard``（同一套外观，"选程序"和"看列表"长得一样）；
2. 图标解 exe 要 ~20ms/个，20 个程序就 400ms。所以**先瞬间把文字列表画出来**，
   图标交给后台线程逐个解析、通过 ``after`` 回到界面线程补画 ——
   对话框立刻可见，图标陆续到位，而不是卡半秒才弹出来。
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import List, Optional, Sequence

from ..core.models import AppItem
from . import icons, theme, widgets
from .dialogs import ModalBase


class ProgramPicker(ModalBase):
    """从"正在运行的程序"里挑一个。"""

    def __init__(self, master, *, apps: Sequence, self_pid: int = 0,
                 existing: Optional[Sequence[str]] = None) -> None:
        super().__init__(master, title="选择程序", width=600, height=560)
        self._apps = [a for a in apps if int(getattr(a, "pid", 0)) != int(self_pid)]
        self._existing = {n.lower() for n in (existing or ())}
        self._icons = icons.IconCache(28)
        self._cards: List[widgets.ProgramCard] = []
        self._selected: Optional[int] = None
        self._shown: List = []
        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._reload())

        head = tk.Frame(self.body, bg=theme.BG)
        head.pack(fill="x")
        tk.Label(head, text="选择一个正在运行的程序", bg=theme.BG, fg=theme.TEXT,
                 font=theme.F_BODY).pack(side="left")
        self._hint = tk.Label(head, text="", bg=theme.BG, fg=theme.TEXT_MUTED,
                              font=theme.F_SMALL)
        self._hint.pack(side="right")

        # 搜索框
        box = tk.Frame(self.body, bg=theme.SUNKEN, highlightthickness=1,
                       highlightbackground=theme.SUNKEN,
                       highlightcolor=theme.PRIMARY)
        box.pack(fill="x", pady=(8, 10))
        glass = tk.Canvas(box, width=22, height=28, bg=theme.SUNKEN,
                          highlightthickness=0, bd=0)
        glass.pack(side="left", padx=(6, 0))
        icons.draw(glass, "search", 13, theme.TEXT_MUTED, 4, 7)
        tk.Entry(box, textvariable=self._search, bg=theme.SUNKEN, fg=theme.TEXT,
                 relief="flat", font=theme.F_BODY, insertbackground=theme.TEXT,
                 highlightthickness=0).pack(side="left", fill="x", expand=True,
                                            ipady=6, padx=(4, 8))

        # 可滚动的程序列表
        outer = tk.Frame(self.body, bg=theme.BG)
        outer.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(outer, bg=theme.BG, highlightthickness=0, bd=0)
        self._canvas.pack(side="left", fill="both", expand=True)
        from tkinter import ttk

        sb = ttk.Scrollbar(outer, orient="vertical",
                           style="Thin.Vertical.TScrollbar",
                           command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._holder = tk.Frame(self._canvas, bg=theme.BG)
        self._win = self._canvas.create_window((0, 0), window=self._holder,
                                               anchor="nw")
        self._holder.bind("<Configure>", lambda _e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(
            self._win, width=e.width))
        self.empty_label = tk.Label(self._holder, text="没有找到匹配的程序",
                                    bg=theme.BG, fg=theme.TEXT_FAINT,
                                    font=theme.F_EMPTY)

        self._reload()
        self._start_icon_worker()

    # ------------------------------------------------------------ 列表

    def _reload(self) -> None:
        for card in self._cards:
            card.destroy()
        self._cards = []
        self._selected = None
        text = self._search.get()
        self._shown = [a for a in self._apps if a.matches(text)]
        self._hint.configure(text=f"共 {len(self._shown)} 个程序")
        if not self._shown:
            self.empty_label.pack(pady=30)
            return
        self.empty_label.pack_forget()
        for index, app in enumerate(self._shown):
            name = app.display_name or app.process_name or f"PID {app.pid}"
            if app.process_name.lower() in self._existing:
                name += "（已在列表）"
            card = widgets.ProgramCard(
                self._holder, name=name, path=app.path or app.process_name,
                icon=self._icons.get(app.path), show_switch=False,
                on_click=lambda i=index: self._select(i))
            card.pack(fill="x", pady=(0, 6))
            self._cards.append(card)

    def _select(self, index: int) -> None:
        for i, card in enumerate(self._cards):
            card.set_selected(i == index)
        self._selected = index

    def _on_double(self, index: int) -> None:
        self._select(index)
        self._on_confirm()

    # ------------------------------------------------------------ 图标

    def _start_icon_worker(self) -> None:
        apps = list(self._shown)
        if not apps:
            return

        def work() -> None:
            for app in apps:
                path = getattr(app, "path", "")
                if not path:
                    continue
                try:
                    from .. import iconmaker
                    from ..platform_win import processes

                    px = processes.exe_icon_rgba(path, 28)
                    b64 = iconmaker.png_base64(px) if px else ""
                except Exception:      # noqa: BLE001
                    b64 = ""
                if not b64:
                    continue
                try:
                    # PhotoImage 必须在界面线程创建
                    self.after(0, lambda p=path, d=b64: self._apply_icon(p, d))
                except Exception:      # noqa: BLE001
                    return

        threading.Thread(target=work, name="picker-icons", daemon=True).start()

    def _apply_icon(self, path: str, b64: str) -> None:
        try:
            img = tk.PhotoImage(data=b64)
        except Exception:      # noqa: BLE001
            return
        self._icons.put(path, img)            # 顺手进缓存，主页直接复用
        for index, app in enumerate(self._shown):
            if getattr(app, "path", "") == path and index < len(self._cards):
                self._cards[index].set_icon(img)
                break

    # ------------------------------------------------------------ 结果

    def collect(self):
        if self._selected is None:
            self.warn("请先点一下要添加的程序")
            return None
        if self._selected >= len(self._shown):
            return None
        app = self._shown[self._selected]
        return AppItem(
            process_name=app.process_name,
            display_name=app.display_name or app.process_name,
            path=app.path,
            enabled=True,
        )


__all__ = ["ProgramPicker"]
