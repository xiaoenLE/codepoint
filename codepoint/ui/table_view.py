"""自绘表格 —— 替掉 ttk.Treeview。

**为什么要自己画**：表格里有两种 Treeview 给不了的东西：
  · 蓝色的勾选框（Treeview 只能输出 ☑ 文本，颜色跟不了单元格）
  · 红色实心圆角块里的白色 ✕ / 铅笔 / 垃圾桶图标
另外 Treeview 的行高、分隔线、hover 都只能整体设，做不到"极浅的 1px 分隔线
+ 整行 hover 高亮"那种观感。

设计：**一行一个 Canvas**（和 ProgramCard 同一套思路）。
行自己处理 hover 与点击，把"点到了哪一列 / 第几个图标"回调上去。
列定义只管宽度和对齐，画法由 ``renderers`` 决定 —— 加一种新单元格类型
只要往 renderers 里加一个函数。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from . import icons, theme, widgets


@dataclass
class Column:
    """一列。``width`` 是像素宽；``stretch`` 时吃掉剩余空间。"""

    key: str
    title: str
    width: int = 120
    align: str = "w"
    stretch: bool = False


@dataclass
class Row:
    """一行数据。``cells`` 按列顺序给值；``enabled`` 只影响文字颜色。"""

    rid: str
    cells: List[str] = field(default_factory=list)
    enabled: bool = True


# ------------------------------------------------------------------ 单元格画法
#
# 每种画法签名统一：fn(canvas, x, y, w, h, value, row) -> None
#   · checkbox  蓝色勾选框（对应"启"列）
#   · action    操作列：红底 ✕ / 铅笔 / 垃圾桶，按 value 里的字符数决定画几个
#   · text      普通文字

_CHECK_SIZE = 15
_ACTION_SIZE = 24


def draw_checkbox(c: tk.Canvas, x: float, y: float, w: float, h: float,
                  value: str, row: Row) -> None:
    cy = y + h / 2
    box = _CHECK_SIZE
    x1, y1 = x, cy - box / 2
    if value == "☑":
        color = theme.PRIMARY
        widgets.rounded_rect(c, x1, y1, x1 + box, y1 + box, 3.5,
                             fill=color, outline=color)
        icons.draw(c, "check", box * 0.78, "#FFFFFF", x1 + box * 0.11, y1 + box * 0.11)
    else:
        widgets.rounded_rect(c, x1, y1, x1 + box, y1 + box, 3.5,
                             fill=theme.BG, outline=theme.BORDER_STRONG, width=1.4)


def draw_actions(c: tk.Canvas, x: float, y: float, w: float, h: float,
                 value: str, row: Row, hover_slot: int = -1,
                 align: str = "w") -> None:
    """操作列。``value`` 里每个"字符"代表一个图标。

    约定：``✕`` 删除（红块） / ``✎`` 编辑（蓝块） / 其它 删除（灰块）。
    用字符而不是索引，是为了让页面代码一眼看出有什么动作。
    """
    cy = y + h / 2
    marks = value.replace(" ", "")
    if align == "e" and marks:
        width = len(marks) * (_ACTION_SIZE + 6) - 6
        x = x + w - width
    cx = x
    for index, mark in enumerate(value.replace(" ", "")):
        box = _ACTION_SIZE
        bx, by = cx, cy - box / 2
        hovered = (index == hover_slot)
        if mark == "✕":
            color = theme.DANGER_HOVER if hovered else theme.DANGER
            widgets.rounded_rect(c, bx, by, bx + box, by + box, 5,
                                 fill=color, outline=color)
            icons.draw(c, "close", box * 0.56, "#FFFFFF",
                       bx + box * 0.22, by + box * 0.22)
        elif mark == "✎":
            bg = theme.PRIMARY_SOFT_HOVER if hovered else theme.PRIMARY_SOFT
            widgets.rounded_rect(c, bx, by, bx + box, by + box, 5,
                                 fill=bg, outline=bg)
            icons.draw(c, "pencil", box * 0.60, theme.PRIMARY,
                       bx + box * 0.20, by + box * 0.20)
        else:
            bg = "#FBD9D5" if hovered else theme.DANGER_SOFT
            widgets.rounded_rect(c, bx, by, bx + box, by + box, 5,
                                 fill=bg, outline=bg)
            icons.draw(c, "trash", box * 0.60, theme.DANGER,
                       bx + box * 0.20, by + box * 0.20)
        cx += box + 6


class TableRow(tk.Canvas):
    """一行。整行一个画布，hover 时整行浅灰底。"""

    def __init__(self, master, view: "TableView", row: Row, index: int,
                 height: int) -> None:
        super().__init__(master, height=height, bg=theme.BG, bd=0,
                         highlightthickness=0)
        self._view = view
        self._row = row
        self._index = index
        self._hover = False
        self._hover_slot = -1
        self._widths: List[float] = []
        self._x0: List[float] = []
        self.bind("<Configure>", lambda e: self.redraw(e.width))
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Motion>", self._on_motion)
        self.bind("<Button-1>", self._on_click)

    def redraw(self, width: Optional[float] = None) -> None:
        self.delete("all")
        w = width if width else self.winfo_width()
        if w <= 1:
            return
        h = int(self.cget("height"))
        if self._hover:
            self.create_rectangle(0, 0, w, h, fill=theme.HOVER, outline=theme.HOVER)

        columns = self._view.columns
        self._widths, self._x0 = self._view.layout(w)
        x = 0.0
        for idx, (col, cw) in enumerate(zip(columns, self._widths)):
            value = self._row.cells[idx] if idx < len(self._row.cells) else ""
            renderer = self._view.renderer_for(col.key)
            if renderer:
                extra = {}
                if col.key == "action":
                    extra = {"hover_slot": self._hover_slot, "align": col.align}
                renderer(self, x + 8, 0, cw - 16, h, value, self._row, **extra)
            else:
                color = theme.TEXT_BODY if self._row.enabled else theme.TEXT_FAINT
                self.create_text(
                    x + 8 if col.align == "w" else x + cw - 8, h / 2,
                    text=value, anchor=col.align, fill=color,
                    font=theme.F_BODY)
            x += cw

        # 极浅的下分隔线（最后一行的线由表格容器补）
        self.create_line(0, h - 1, w, h - 1, fill=theme.BORDER)

    # ------------------------------------------------------------ 交互

    def _set_hover(self, value: bool) -> None:
        if value != self._hover:
            self._hover = value
            self._hover_slot = -1
            self.redraw()

    def _column_at(self, x: float) -> int:
        acc = 0.0
        for index, cw in enumerate(self._widths):
            if acc <= x < acc + cw:
                return index
            acc += cw
        return -1

    def _slot_at(self, index: int, x: float) -> int:
        if index < 0 or index >= len(self._widths):
            return -1
        start = sum(self._widths[:index]) + 8
        rel = x - start
        if rel < 0:
            return -1
        step = _ACTION_SIZE + 6
        return min(len(self._row.cells[index].replace(" ", "")) - 1,
                   int(rel // step))

    def _on_motion(self, event) -> None:
        index = self._column_at(event.x)
        key = self._view.columns[index].key if index >= 0 else ""
        slot = self._slot_at(index, event.x) if key == "action" else -1
        if slot != self._hover_slot:
            self._hover_slot = slot
            self.configure(cursor="hand2" if (slot >= 0 or key == "checkbox") else "arrow")
            self.redraw()

    def _on_click(self, event) -> None:
        index = self._column_at(event.x)
        if index < 0:
            return
        key = self._view.columns[index].key
        if key == "action":
            slot = self._slot_at(index, event.x)
            if slot >= 0:
                self._view.on_action(self._row.rid, slot)
            return
        if key == "checkbox":
            self._view.on_toggle(self._row.rid)
            return
        self._view.on_cell(self._row.rid, key)


class TableView(tk.Frame):
    """可滚动的自绘表格。"""

    HEADER_H = 38
    ROW_H = 40
    PAD_X = 14

    def __init__(self, master, *, columns: Sequence[Column],
                 on_action: Callable[[str, int], None],
                 on_toggle: Callable[[str], None],
                 on_cell: Optional[Callable[[str, str], None]] = None,
                 renderers: Optional[Dict[str, Callable]] = None) -> None:
        super().__init__(master, bg=theme.BG)
        self.columns = list(columns)
        self.on_action = on_action
        self.on_toggle = on_toggle
        self.on_cell = on_cell or (lambda _rid, _key: None)
        self._renderers = dict(renderers or {})
        self._rows: List[TableRow] = []
        self._empty: Optional[tk.Label] = None

        self.header = tk.Canvas(self, height=self.HEADER_H, bg=theme.BG,
                                bd=0, highlightthickness=0)
        self.header.pack(fill="x", padx=(self.PAD_X, self.PAD_X))
        self.header.bind("<Configure>", lambda _e: self._draw_header())

        # 可滚动区域
        from tkinter import ttk

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(body, bg=theme.BG, bd=0, highlightthickness=0)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._sb = ttk.Scrollbar(body, orient="vertical",
                                 style="Thin.Vertical.TScrollbar",
                                 command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side="right", fill="y")
        self._holder = tk.Frame(self._canvas, bg=theme.BG)
        self._win = self._canvas.create_window((0, 0), window=self._holder, anchor="nw")
        self._holder.bind("<Configure>", lambda _e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", self._on_canvas)
        self._canvas.bind("<Enter>", lambda _e: self._canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        self._canvas.bind("<Leave>", lambda _e: self._canvas.unbind_all("<MouseWheel>"))

    # ------------------------------------------------------------ 布局

    def layout(self, width: float):
        """算每列实际宽度（stretch 列平分剩余空间）。返回 (宽度列表, 起点列表)。"""
        avail = max(width - self.PAD_X * 2, 200)
        fixed = sum(c.width for c in self.columns if not c.stretch)
        flex = [c for c in self.columns if c.stretch]
        widths = []
        extra = max(avail - fixed, 0) / len(flex) if flex else 0
        for col in self.columns:
            widths.append(col.width + extra if col.stretch else float(col.width))
        starts = []
        acc = 0.0
        for w in widths:
            starts.append(acc)
            acc += w
        return widths, starts

    def _on_canvas(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)
        self._draw_header()

    def _draw_header(self) -> None:
        self.header.delete("all")
        w = self.header.winfo_width()
        if w <= 1:
            return
        widths, starts = self.layout(w + self.PAD_X * 2)
        for col, cw, x in zip(self.columns, widths, starts):
            self.header.create_text(
                x + 8 if col.align == "w" else x + cw - 8,
                self.HEADER_H / 2 + 2, text=col.title,
                anchor=col.align, fill=theme.TEXT_MUTED, font=theme.F_BODY)
        self.header.create_line(0, self.HEADER_H - 1,
                                w + self.PAD_X * 2, self.HEADER_H - 1,
                                fill=theme.BORDER)

    def renderer_for(self, column_key: str):
        if column_key == "checkbox":
            return draw_checkbox
        if column_key == "action":
            return draw_actions
        return self._renderers.get(column_key)

    # ------------------------------------------------------------ 数据

    def set_rows(self, rows: Sequence[Row]) -> None:
        for row in self._rows:
            row.destroy()
        self._rows = []
        if self._empty is not None:
            self._empty.destroy()
            self._empty = None
        if not rows:
            self._empty = tk.Label(self._holder, text="列表是空的，点上面的「增加+」加一条",
                                   bg=theme.BG, fg=theme.TEXT_FAINT, font=theme.F_EMPTY)
            self._empty.pack(pady=40)
            return
        for index, row in enumerate(rows):
            widget = TableRow(self._holder, self, row, index, self.ROW_H)
            widget.pack(fill="x", padx=self.PAD_X)
            self._rows.append(widget)

    # ------------------------------------------------------------ 滚动

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


__all__ = ["Column", "Row", "TableView"]
