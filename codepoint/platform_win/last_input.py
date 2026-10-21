"""用户空闲时长（键盘**和鼠标**都算）。

为什么需要它：低级键盘钩子只能看到键盘事件。我们想回答的问题是
"用户这会儿明明在动，钩子却一声不吭 —— 是不是已经被系统摘掉了？"
只看键盘是回答不了的（用户可能只在动鼠标），所以要用
``GetLastInputInfo``：它记录的是**任意输入**（键盘/鼠标）的最后时刻。

拿不到时返回 0.0 —— 语义是"刚刚有输入"，也就是**保守地不触发探活**。
"""

from __future__ import annotations

import ctypes

from .winapi import DWORD, LASTINPUTINFO, byref, kernel32, user32


def idle_seconds() -> float:
    """距最近一次用户输入过了多少秒（取不到时返回 0.0）。"""
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(byref(info)):
            return 0.0
        # 两个都是 32 位毫秒计数，会回绕；用掩码相减天然处理回绕
        delta = (DWORD(kernel32.GetTickCount()).value - info.dwTime) & 0xFFFFFFFF
        return delta / 1000.0
    except Exception:      # noqa: BLE001
        return 0.0


__all__ = ["idle_seconds"]
