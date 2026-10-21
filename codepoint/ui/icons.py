"""界面用小图标：全部用 Canvas 矢量绘制，不依赖任何图片资源。

为什么不用 emoji / 字符图标：不同 Windows 上的字体回退不一样，✏ 和 🗑 经常
被渲染成方块或者彩色 emoji，风格完全失控。这里统一画线框图标，颜色跟着主题走。

同时提供 **exe 图标缓存**：把 ``platform_win.processes`` 抽出来的像素转成
tkinter 的 PhotoImage 并缓存（同一个 exe 只解析一次，列表刷新不会卡）。
"""

from __future__ import annotations

import hashlib
import tkinter as tk
from typing import Dict, List, Optional

from .. import iconmaker
from . import theme

# 图标种类 → 绘制函数。每个函数接收 (canvas, size, color)，返回要绘制的元素 id 列表。

def _draw_home(c: tk.Canvas, s: float, color: str) -> List[int]:
    """房子：屋顶 + 墙体。"""
    ids = []
    ids.append(c.create_polygon(
        s * 0.12, s * 0.46, s * 0.50, s * 0.14, s * 0.88, s * 0.46,
        outline=color, fill="", width=max(1.4, s * 0.075), joinstyle="miter"))
    ids.append(c.create_line(
        s * 0.22, s * 0.42, s * 0.22, s * 0.86, s * 0.78, s * 0.86,
        s * 0.78, s * 0.42,
        fill=color, width=max(1.4, s * 0.075), joinstyle="miter", capstyle="round"))
    return ids


def _draw_info(c: tk.Canvas, s: float, color: str) -> List[int]:
    """信息：圆圈 + i。"""
    w = max(1.4, s * 0.075)
    ids = [c.create_oval(s * 0.14, s * 0.14, s * 0.86, s * 0.86,
                         outline=color, fill="", width=w)]
    ids.append(c.create_line(s * 0.50, s * 0.44, s * 0.50, s * 0.70,
                             fill=color, width=w, capstyle="round"))
    ids.append(c.create_oval(s * 0.455, s * 0.28, s * 0.545, s * 0.37,
                             outline=color, fill=color, width=0))
    return ids


def _draw_gear(c: tk.Canvas, s: float, color: str) -> List[int]:
    """齿轮：一个环 + 六根齿。"""
    w = max(1.3, s * 0.070)
    ids = [c.create_oval(s * 0.30, s * 0.30, s * 0.70, s * 0.70,
                         outline=color, fill="", width=w)]
    import math
    for i in range(6):
        a = math.radians(i * 60 + 30)
        x1, y1 = 0.5 + 0.20 * math.cos(a), 0.5 + 0.20 * math.sin(a)
        x2, y2 = 0.5 + 0.38 * math.cos(a), 0.5 + 0.38 * math.sin(a)
        ids.append(c.create_line(s * x1, s * y1, s * x2, s * y2,
                                 fill=color, width=w, capstyle="round"))
    return ids


def _draw_plus(c: tk.Canvas, s: float, color: str) -> List[int]:
    w = max(1.6, s * 0.14)
    return [
        c.create_line(s * 0.5, s * 0.22, s * 0.5, s * 0.78,
                      fill=color, width=w, capstyle="round"),
        c.create_line(s * 0.22, s * 0.5, s * 0.78, s * 0.5,
                      fill=color, width=w, capstyle="round"),
    ]


def _draw_close(c: tk.Canvas, s: float, color: str) -> List[int]:
    w = max(1.6, s * 0.16)
    return [
        c.create_line(s * 0.26, s * 0.26, s * 0.74, s * 0.74,
                      fill=color, width=w, capstyle="round"),
        c.create_line(s * 0.74, s * 0.26, s * 0.26, s * 0.74,
                      fill=color, width=w, capstyle="round"),
    ]


def _draw_pencil(c: tk.Canvas, s: float, color: str) -> List[int]:
    w = max(1.3, s * 0.085)
    ids = [c.create_polygon(
        s * 0.20, s * 0.80, s * 0.28, s * 0.58, s * 0.64, s * 0.22,
        s * 0.80, s * 0.38, s * 0.44, s * 0.74,
        outline=color, fill="", width=w, joinstyle="round")]
    ids.append(c.create_line(s * 0.56, s * 0.30, s * 0.70, s * 0.44,
                             fill=color, width=w))
    return ids


def _draw_trash(c: tk.Canvas, s: float, color: str) -> List[int]:
    w = max(1.3, s * 0.085)
    ids = [
        c.create_line(s * 0.16, s * 0.28, s * 0.84, s * 0.28,
                      fill=color, width=w, capstyle="round"),
        c.create_line(s * 0.40, s * 0.28, s * 0.40, s * 0.18,
                      s * 0.60, s * 0.18, s * 0.60, s * 0.28,
                      fill=color, width=w, joinstyle="round"),
        c.create_line(s * 0.26, s * 0.34, s * 0.31, s * 0.84,
                      s * 0.69, s * 0.84, s * 0.74, s * 0.34,
                      fill=color, width=w, joinstyle="round", capstyle="round"),
        c.create_line(s * 0.42, s * 0.44, s * 0.42, s * 0.74,
                      fill=color, width=max(1.0, w * 0.75), capstyle="round"),
        c.create_line(s * 0.58, s * 0.44, s * 0.58, s * 0.74,
                      fill=color, width=max(1.0, w * 0.75), capstyle="round"),
    ]
    return ids


def _draw_search(c: tk.Canvas, s: float, color: str) -> List[int]:
    w = max(1.3, s * 0.085)
    return [
        c.create_oval(s * 0.18, s * 0.18, s * 0.70, s * 0.70,
                      outline=color, fill="", width=w),
        c.create_line(s * 0.64, s * 0.64, s * 0.84, s * 0.84,
                      fill=color, width=w, capstyle="round"),
    ]


def _draw_check(c: tk.Canvas, s: float, color: str) -> List[int]:
    w = max(1.6, s * 0.14)
    return [c.create_line(s * 0.20, s * 0.52, s * 0.42, s * 0.74,
                          s * 0.80, s * 0.26,
                          fill=color, width=w, capstyle="round", joinstyle="round")]


def _draw_target(c: tk.Canvas, s: float, color: str) -> List[int]:
    """准星：用于"捕捉当前程序"。"""
    w = max(1.3, s * 0.08)
    ids = [c.create_oval(s * 0.18, s * 0.18, s * 0.82, s * 0.82,
                         outline=color, fill="", width=w),
           c.create_oval(s * 0.42, s * 0.42, s * 0.58, s * 0.58,
                         outline=color, fill=color, width=0)]
    for x1, y1, x2, y2 in ((0.5, 0.06, 0.5, 0.22), (0.5, 0.78, 0.5, 0.94),
                           (0.06, 0.5, 0.22, 0.5), (0.78, 0.5, 0.94, 0.5)):
        ids.append(c.create_line(s * x1, s * y1, s * x2, s * y2,
                                 fill=color, width=w, capstyle="round"))
    return ids


_DRAWERS = {
    "home": _draw_home,
    "info": _draw_info,
    "gear": _draw_gear,
    "plus": _draw_plus,
    "close": _draw_close,
    "pencil": _draw_pencil,
    "trash": _draw_trash,
    "search": _draw_search,
    "check": _draw_check,
    "target": _draw_target,
}


def draw(canvas: tk.Canvas, kind: str, size: float, color: str,
         x: float = 0.0, y: float = 0.0) -> List[int]:
    """在 canvas 的 (x, y) 处画一个 size×size 的图标，返回元素 id。"""
    fn = _DRAWERS.get(kind)
    if fn is None:
        return []
    items = fn(canvas, size, color)
    if x or y:
        for i in items:
            canvas.move(i, x, y)
    return items


# ------------------------------------------------------------------ exe 图标缓存


class IconCache:
    """exe 图标 → PhotoImage 缓存。

    PhotoImage 必须由持有 Tk 的线程创建，所以这个类只能在界面线程用。
    缓存键是 exe 路径；同一个程序的图标只解析一次（一次约 20ms）。
    """

    def __init__(self, size: int = 32) -> None:
        self._size = size
        self._cache: Dict[str, Optional[tk.PhotoImage]] = {}
        self._letters: Dict[str, tk.PhotoImage] = {}

    def get(self, path: str) -> Optional[tk.PhotoImage]:
        """取某个 exe 的图标；取不到返回 None（调用方可以退回字母头像）。"""
        if not path:
            return None
        if path in self._cache:
            return self._cache[path]
        img: Optional[tk.PhotoImage] = None
        try:
            from ..platform_win import processes

            px = processes.exe_icon_rgba(path, self._size)
            if px:
                b64 = iconmaker.png_base64(px)
                if b64:
                    img = tk.PhotoImage(data=b64)
        except Exception:      # noqa: BLE001
            img = None
        self._cache[path] = img
        return img

    def put(self, path: str, image: tk.PhotoImage) -> None:
        """外部解析好的图标塞进缓存（比如选择程序对话框在后台线程解好的那个）。"""
        if path:
            self._cache[path] = image

    def clear(self) -> None:
        self._cache.clear()


def _inside_round(x: int, y: int, size: int, radius: int) -> bool:
    if radius <= 0:
        return True
    cx = min(max(x, radius), size - radius - 1)
    cy = min(max(y, radius), size - radius - 1)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius + radius


AVATAR_COLORS = (
    (0x4C, 0x8D, 0xF6), (0x7C, 0x6B, 0xE8), (0x2F, 0xB3, 0x44),
    (0xF5, 0x89, 0x1F), (0xE8, 0x5D, 0x86), (0x12, 0xA9, 0xA0),
)


def avatar_color(name: str) -> str:
    """按名字哈希挑一个头像底色（同一个程序每次都是同一个色）。

    返回 ``"#RRGGBB"`` 字符串 —— tkinter 的 ``fill=`` 只吃颜色名/十六进制串，
    直接丢 RGB 元组会报 ``unknown color name "124 107 232"``（踩过）。
    """
    h = int(hashlib.md5(name.encode("utf-8", "ignore")).hexdigest()[:8], 16)
    r, g, b = AVATAR_COLORS[h % len(AVATAR_COLORS)]
    return f"#{r:02X}{g:02X}{b:02X}"


__all__ = ["draw", "IconCache", "avatar_color"]
