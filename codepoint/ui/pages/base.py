"""页面公共骨架。

    Page       可纵向滚动的页面容器（内容比窗口高时自动出细滚动条）
    PageHeader 「英文标点符号列表」+ 右侧蓝色按钮组
    TablePage  两个"规则表"页面的共同实现（标点列表 / 快捷输入）

两个表格页的结构完全一致（标题 + 按钮组 + 四列表格 + 行内操作图标），
差别只在列名、数据来源和动作。所以抽一个基类，子类只填"差异部分"。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Sequence

from .. import theme, widgets
from ..table_view import Column, Row, TableView


class Page(tk.Frame):
    """页面容器：白底 + 可滚动内容区。"""

    def __init__(self, master, *, pad: int = 18, bg: str = theme.BG) -> None:
        super().__init__(master, bg=bg)
        self._bg = bg
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._scroll = ttk.Scrollbar(self, orient="vertical",
                                     style="Thin.Vertical.TScrollbar",
                                     command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._canvas.pack(side="left", fill="both", expand=True)

        self.content = tk.Frame(self._canvas, bg=bg)
        self._window = self._canvas.create_window((0, 0), window=self.content,
                                                 anchor="nw")
        self.content.bind("<Configure>", self._on_content)
        self._canvas.bind("<Configure>", self._on_canvas)
        # 鼠标滚轮（只在指针位于本页时生效，避免抢别的页面）
        self._canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self._canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))
        self.pad = pad

    # ------------------------------------------------------------ 滚动

    def _on_content(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        # 宽度跟视口；高度取"视口高度"和"内容需要的高度"里更大的那个，
        # 这样内容少时表格能撑满整页，内容多时又能正常滚动。
        self._canvas.itemconfigure(
            self._window, width=event.width,
            height=max(event.height, self.content.winfo_reqheight()))
        self._update_scrollbar()

    def _update_scrollbar(self) -> None:
        self.update_idletasks()
        need = self.content.winfo_reqheight() > self._canvas.winfo_height()
        if need and not self._scroll.winfo_ismapped():
            self._scroll.pack(side="right", fill="y")
        elif not need and self._scroll.winfo_ismapped():
            self._scroll.pack_forget()

    def _bind_wheel(self, on: bool) -> None:
        if on:
            self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        if self.content.winfo_reqheight() <= self._canvas.winfo_height():
            return
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def add(self, widget) -> None:
        """把一个控件放进内容区（统一用 padx）。"""
        widget.pack(fill="x", padx=self.pad, pady=(0, 10))

    def rebuild(self) -> None:
        """清空内容区，供"整页重画"用。

        设置类页面是在 ``__init__`` 里一次性把控件建好的（开关、下拉框都带状态），
        值一旦被外部整体换掉（比如导入配置），**只能重建** ——
        逐个去同步控件状态既啰嗦又容易漏，重建是唯一不会出错的做法。
        """
        for child in self.content.winfo_children():
            child.destroy()

    def refresh(self) -> None:
        """页面数据变化时调用（子类覆盖）。"""
        self._update_scrollbar()


class PageHeader(tk.Frame):
    """页面顶部：左边标题，右边按钮组。"""

    def __init__(self, master, *, text: str, bg: str = theme.BG) -> None:
        super().__init__(master, bg=bg)
        tk.Label(self, text=text, bg=bg, fg=theme.TEXT,
                 font=theme.F_H1).pack(side="left")
        self.actions = tk.Frame(self, bg=bg)
        self.actions.pack(side="left", padx=(12, 0))

    def button(self, text: str, command: Callable[[], None],
               kind: str = "Tool") -> ttk.Button:
        btn = ttk.Button(self.actions, text=text, command=command,
                         style=f"{kind}.TButton")
        btn.pack(side="left", padx=(0, 6))
        return btn


class TablePage(Page):
    """标题 + 按钮组 + 自绘表格 的通用页面。

    两个表格页（标点列表 / 快捷输入）结构完全一样，差别只有：
      ``columns`` 列定义、``rows()`` 数据、``on_action()`` 动作。
    """

    #: 列定义（ui.table_view.Column）
    columns: Sequence = ()

    def __init__(self, master, facade, *, title: str, **kw) -> None:
        super().__init__(master, **kw)
        self.facade = facade
        self._title_text = title

        self.header = PageHeader(self.content, text=title, bg=self._bg)
        self.header.pack(fill="x", padx=self.pad, pady=(self.pad, 12))
        self._build_actions()

        wrap = tk.Frame(self.content, bg=self._bg)
        wrap.pack(fill="both", expand=True, padx=self.pad)
        self.table = TableView(
            wrap, columns=self.columns,
            on_action=self.on_action, on_toggle=self.on_toggle,
            on_cell=self.on_cell)
        self.table.pack(fill="both", expand=True)

    # ------------------------------------------------------------ 子类接口

    def _build_actions(self) -> None:
        """子类在这里往 self.header.actions 里加按钮。"""

    def rows(self) -> List[Row]:
        """返回要显示的行（ui.table_view.Row）。"""
        return []

    def on_action(self, rid: str, slot: int) -> None:
        """点到了某行的第 slot 个操作图标。"""

    def on_toggle(self, rid: str) -> None:
        """点到了"启"列的勾选框。"""

    def on_cell(self, rid: str, column: str) -> None:
        """点到了某一行的普通单元格（默认什么都不做）。"""

    # ------------------------------------------------------------ 刷新

    def refresh(self) -> None:
        super().refresh()
        try:
            self.table.set_rows(self.rows())
        except Exception:      # noqa: BLE001
            self.table.set_rows([])


__all__ = ["Page", "PageHeader", "TablePage"]
