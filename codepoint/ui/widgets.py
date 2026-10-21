"""复用控件。

tkinter 原生控件做不出圆角/悬停/描边这类观感，所以关键处直接用
``tk.Canvas`` 自己画（一个控件 = 一个画布，元素全在画布里，像素级控制）。

本模块只放**与业务无关**的通用件：
    RoundedRect  圆角矩形绘制工具
    ToggleSwitch 绿色开关（"已开启/已暂停"那个）
    ProgramCard  主页的程序卡片（图标 + 名称 + 完整路径）
    EmptyState   空状态文案（"空空如也 / 快点击"+"按钮 / …"）
    IconAction   小图标按钮（✏ / 🗑 / ✕），带 hover 底色
    SectionTitle 小标题
    table_hit    表格里"点到了第几个图标"的命中判定
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional, Sequence

from . import icons, theme


# ------------------------------------------------------------------ 圆角矩形


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                 radius: float, **kw) -> int:
    """在 canvas 上画一个圆角矩形，返回 item id。

    tkinter 没有原生圆角矩形，用"多段点 + 平滑多边形"来近似。

    ⚠ **每个角必须多采样几个点**（这里 5 个）。只给 8 个点（每角 2 个）时，
    平滑样条会把弧线拉直甚至凹陷 —— 半径小的时候看不出来，但画"完整胶囊"
    （半径 = 高度一半，比如开关的底槽）时会变成一个**月牙**，非常难看。
    """
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    if r <= 0.5:
        return canvas.create_rectangle(x1, y1, x2, y2, **kw)

    import math

    steps = 5
    points: List[float] = []
    # 顺序：左上 → 右上 → 右下 → 左下，每个角按起点角均分成 steps 个点
    corners = (
        (x1 + r, y1 + r, 180), (x2 - r, y1 + r, 270),
        (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90),
    )
    for cx, cy, start in corners:
        for k in range(steps):
            a = math.radians(start + 90.0 * k / (steps - 1))
            points += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return canvas.create_polygon(points, smooth=True, **kw)


# ------------------------------------------------------------------ 卡片


# ------------------------------------------------------------------ 开关


class ToggleSwitch(tk.Canvas):
    """iOS 风格开关：开=绿色，关=灰色，点击回调。

    ⚠ 私有属性**绝不能叫** ``self._w`` / ``self._h`` —— tkinter 用 ``_w`` 存
    控件的 Tcl 路径名，覆盖掉之后所有画布操作都会报
    ``invalid command name "44"``（踩过，排查了半天）。
    """

    W, H = 44, 24

    def __init__(self, master, *, value: bool = False,
                 command: Optional[Callable[[bool], None]] = None,
                 width: int = W, height: int = H, bg: str = theme.BG) -> None:
        super().__init__(master, width=width, height=height, bd=0,
                         highlightthickness=0, bg=bg, cursor="hand2")
        self._width_px, self._height_px = width, height
        self._value = bool(value)
        self._command = command
        self._enabled = True
        self._track = self._knob = None
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self._render(hover=True))
        self.bind("<Leave>", lambda _e: self._render(hover=False))
        self._render()

    # -------------------------------------------------- 状态

    @property
    def value(self) -> bool:
        return self._value

    def set_value(self, value: bool, *, fire: bool = False) -> None:
        value = bool(value)
        if value == self._value:
            return
        self._value = value
        self._render()
        if fire and self._command:
            self._command(self._value)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._render()

    # -------------------------------------------------- 交互

    def _on_click(self, _event) -> None:
        if not self._enabled:
            return
        self._value = not self._value
        self._render(hover=True)
        if self._command:
            self._command(self._value)

    # -------------------------------------------------- 绘制

    def _render(self, hover: bool = False) -> None:
        w, h = self._width_px, self._height_px
        pad = 2
        r = (h - pad * 2) / 2

        # ⚠ **先把颜色全算出来，再清画布**。
        #   反过来的话，只要颜色常量写错一个名字（曾经把 SUCCESS_HOVER 写漏了），
        #   `delete("all")` 已经执行、异常却在画之前抛出 —— 控件的表现是
        #   "鼠标一碰就整块变成空白"，而且异常发生在回调之前，
        #   点击开关永远不会把设置传给应用（用户看到的就是"点了没反应"）。
        if not self._enabled:
            track = theme.SWITCH_DISABLED
        elif self._value:
            track = theme.SUCCESS_HOVER if hover else theme.SUCCESS
        else:
            track = theme.SWITCH_OFF_HOVER if hover else theme.SWITCH_OFF

        self.delete("all")
        rounded_rect(self, pad, pad, w - pad, h - pad, r,
                     fill=track, outline=track)
        kr = r - 2
        cx = (w - pad - r) if self._value else (pad + r)
        self.create_oval(cx - kr, h / 2 - kr, cx + kr, h / 2 + kr,
                         fill="#FFFFFF", outline="#FFFFFF")


# ------------------------------------------------------------------ 程序卡片


class ProgramCard(tk.Canvas):
    """主页的程序卡片：`[图标] 进程名` + 第二行完整路径。

    整张卡片是一个画布，图标/文字/边框全画在上面 —— 这样圆角、悬停高亮、
    图标对齐都能精确控制（用 Frame 拼是拼不出这种观感的）。
    """

    HEIGHT = 56
    ICON = 32

    def __init__(self, master, *, name: str, path: str, icon: Optional[tk.PhotoImage],
                 enabled: bool = True, on_click: Optional[Callable[[], None]] = None,
                 on_toggle: Optional[Callable[[bool], None]] = None,
                 show_switch: bool = True, selected: bool = False,
                 width: int = 640) -> None:
        super().__init__(master, height=self.HEIGHT, bd=0, highlightthickness=0,
                         bg=theme.BG, cursor="hand2")
        self._name = name
        self._path = path
        self._icon = icon
        self._enabled = enabled
        self._on_click = on_click
        self._on_toggle = on_toggle
        self._show_switch = show_switch
        self._selected = selected
        self._hover = False
        self._card_w = width
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._click)
        self._render()

    def _on_configure(self, event) -> None:
        if abs(event.width - self._card_w) > 2:
            self._card_w = event.width
            self._render()

    def _set_hover(self, value: bool) -> None:
        if value != self._hover:
            self._hover = value
            self._render()

    def set_icon(self, icon: Optional[tk.PhotoImage]) -> None:
        """图标是异步解析出来的，拿到后补画一次。"""
        self._icon = icon
        self._render()

    def set_selected(self, value: bool) -> None:
        self._selected = bool(value)
        self._render()

    def _click(self, event) -> None:
        # 右侧开关自己响应；点别处就是"选中这个程序"
        if self._show_switch and event.x >= self._card_w - 60 and self._on_toggle:
            self._enabled = not self._enabled
            self._render()
            self._on_toggle(self._enabled)
            return
        if self._on_click:
            self._on_click()

    def set_enabled(self, value: bool) -> None:
        self._enabled = value
        self._render()

    # -------------------------------------------------- 绘制

    def _render(self) -> None:
        self.delete("all")
        w = max(self._card_w, 120)
        h = self.HEIGHT
        border = theme.PRIMARY if (self._hover or self._selected) else theme.BORDER
        bg = theme.PRIMARY_SOFT if (self._hover or self._selected) else theme.BG
        rounded_rect(self, 1, 1, w - 1, h - 1, theme.CARD_RADIUS,
                     fill=bg, outline=border, width=1)

        ix, iy = 12, (h - self.ICON) // 2
        if self._icon is not None:
            self.create_image(ix, iy, image=self._icon, anchor="nw")
        else:
            # 没有图标 → 画一个字母头像（颜色由名字哈希决定，稳定不跳）
            color = icons.avatar_color(self._name or self._path)
            rounded_rect(self, ix, iy, ix + self.ICON, iy + self.ICON, 7,
                         fill=color, outline=color)
            self.create_text(ix + self.ICON / 2, iy + self.ICON / 2 + 1,
                             text=(self._name or "?")[:1].upper(),
                             fill="#FFFFFF", font=(theme.family(), 13, "bold"))

        tx = ix + self.ICON + 12
        # 右侧给开关腾地方
        right = 68 if self._show_switch else 16
        avail = w - tx - right
        self.create_text(tx, h / 2 - 9, text=_ellipsize(self._name, avail, 11, bold=True),
                         anchor="w", fill=theme.TEXT if self._enabled else theme.TEXT_FAINT,
                         font=(theme.family(), 11, "bold"))
        self.create_text(tx, h / 2 + 10, text=_ellipsize(self._path, avail, 9),
                         anchor="w", fill=theme.TEXT_MUTED,
                         font=(theme.family(), 9))

        if not self._show_switch:
            return
        # 右侧：开关
        sw_w, sw_h = 36, 20
        sx = w - sw_w - 16
        sy = (h - sw_h) / 2
        track = theme.SUCCESS if self._enabled else "#D9DDE3"
        rounded_rect(self, sx, sy, sx + sw_w, sy + sw_h, sw_h / 2,
                     fill=track, outline=track)
        kr = sw_h / 2 - 2
        cx = (sx + sw_w - sw_h / 2) if self._enabled else (sx + sw_h / 2)
        self.create_oval(cx - kr, h / 2 - kr, cx + kr, h / 2 + kr,
                         fill="#FFFFFF", outline="#FFFFFF")


def _ellipsize(text: str, max_px: float, size: int, bold: bool = False) -> str:
    """按像素宽度截断加省略号（tkinter 没有自动省略，长路径会把卡片撑爆）。"""
    if not text:
        return ""
    try:
        from tkinter import font as tkfont

        f = tkfont.Font(family=theme.family(), size=size,
                        weight="bold" if bold else "normal")
        if f.measure(text) <= max_px:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if f.measure(text[:mid] + ell) <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell
    except Exception:      # noqa: BLE001
        return text[:int(max_px / 7)]


# ------------------------------------------------------------------ 空状态


class EmptyState(tk.Frame):
    """空状态：几行居中的灰色文案。"""

    def __init__(self, master, *, lines: Sequence[str], bg: str = theme.BG) -> None:
        super().__init__(master, bg=bg)
        for i, line in enumerate(lines):
            tk.Label(self, text=line, bg=bg, fg=theme.TEXT_FAINT,
                     font=(theme.family(), 11 if i == 0 else 10)).pack(pady=(0, 6))


# ------------------------------------------------------------------ 小图标按钮


class IconAction(tk.Canvas):
    """方形小图标按钮（✏ / 🗑 / ✕），带 hover 底色。"""

    def __init__(self, master, *, kind: str, color: str, hover_bg: str,
                 size: int = 26, icon_size: float = 14,
                 command: Optional[Callable[[], None]] = None,
                 bg: str = theme.BG) -> None:
        super().__init__(master, width=size, height=size, bd=0,
                         highlightthickness=0, bg=bg, cursor="hand2")
        self._kind, self._color, self._hover_bg = kind, color, hover_bg
        self._size, self._icon_size = size, icon_size
        self._command = command
        self._hover = False
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._command() if self._command else None)
        self._render()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._render()

    def _render(self) -> None:
        self.delete("all")
        s = self._size
        if self._hover:
            rounded_rect(self, 1, 1, s - 1, s - 1, 6,
                         fill=self._hover_bg, outline=self._hover_bg)
        off = (s - self._icon_size) / 2
        icons.draw(self, self._kind, self._icon_size, self._color, off, off)


# ------------------------------------------------------------------ 小标题


class SectionTitle(tk.Frame):
    """`英文标点符号列表` + 右侧按钮组，横向一行。"""

    def __init__(self, master, *, text: str, bg: str = theme.BG) -> None:
        super().__init__(master, bg=bg)
        self.label = tk.Label(self, text=text, bg=bg, fg=theme.TEXT,
                              font=theme.F_H1)
        self.label.pack(side="left")
        self.actions = tk.Frame(self, bg=bg)
        self.actions.pack(side="left", padx=(12, 0))


# ------------------------------------------------------------------ 表格命中


def table_hit(tree, event_x: int, event_y: int, slots: int) -> int:
    """判断点击落在表格某一行的第几个"图标槽"上。

    ttk.Treeview 不支持在单元格里放控件，所以操作列用几个字符拼起来，
    再用本函数把点击的 x 坐标换算成"第几个图标"。返回 -1 表示没命中。
    """
    if slots <= 0:
        return -1
    item = tree.identify_row(event_y)
    if not item:
        return -1
    col = tree.identify_column(event_x)
    try:
        col_index = int(col.replace("#", "")) - 1
    except ValueError:
        return -1
    columns = list(tree["columns"])
    if col_index < 0 or col_index >= len(columns):
        return -1
    bbox = tree.bbox(item, columns[col_index])
    if not bbox:
        return -1
    x, _y, w, _h = bbox
    offset = event_x - x
    if offset < 0 or offset > w:
        return -1
    return min(slots - 1, int(offset / max(1, w) * slots))


__all__ = [
    "rounded_rect", "ToggleSwitch", "ProgramCard", "EmptyState",
    "IconAction", "SectionTitle", "table_hit",
]
