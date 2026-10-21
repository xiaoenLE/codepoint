"""显示器工作区查询（Win32）。

**为什么不能直接用 tkinter 的 ``winfo_screenwidth()``**：它只报**主显示器**，
而且不减任务栏。多显示器 + 任务栏的常见组合下，按它算出来的"居中"会：

1. 落在主屏，而不是"鼠标所在的那块屏" —— 用户正在副屏干活，窗口却在主屏弹出；
2. 被任务栏吃掉一截；
3. 更糟的：副屏在主屏左侧时坐标是**负数**，拿主屏尺寸去居中就会算出越界值，
   窗口跑到可视区外，表现为"窗口不见了 / 拖不出来"。

这里走 ``MonitorFromPoint(鼠标位置)`` → ``GetMonitorInfoW`` 取 ``rcWork``
（已排除任务栏）。真正的居中和夹取是纯算术，在 ``core/window_geom.py``，
那边才有单测 —— 两边分工是刻意的：平台层只负责"问出事实"。
"""

from __future__ import annotations

import ctypes
from typing import Optional, Tuple

from .winapi import (
    MONITORINFO,
    MONITOR_DEFAULTTONEAREST,
    POINT,
    UINT,
    user32,
)

Area = Tuple[int, int, int, int]        # (x, y, width, height)


def cursor_point() -> Tuple[int, int]:
    """当前鼠标位置（拿不到就返回原点）。"""
    pt = POINT()
    try:
        if user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:      # noqa: BLE001
        pass
    return 0, 0


def work_area(point: Optional[Tuple[int, int]] = None) -> Area:
    """鼠标（或指定点）所在显示器的工作区 ``(x, y, width, height)``。"""
    px, py = point if point is not None else cursor_point()
    try:
        pt = POINT(int(px), int(py))
        hmon = user32.MonitorFromPoint(pt, UINT(MONITOR_DEFAULTTONEAREST))
        if hmon:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(info)):
                r = info.rcWork
                x, y = int(r.left), int(r.top)
                w, h = int(r.right) - x, int(r.bottom) - y
                if w > 0 and h > 0:
                    return x, y, w, h
    except Exception:      # noqa: BLE001
        pass
    # 退化：整屏（不减任务栏，总比算不出来强）
    try:
        w = int(user32.GetSystemMetrics(0))
        h = int(user32.GetSystemMetrics(1))
        if w > 0 and h > 0:
            return 0, 0, w, h
    except Exception:      # noqa: BLE001
        pass
    return 0, 0, 1280, 720


__all__ = ["cursor_point", "work_area"]
