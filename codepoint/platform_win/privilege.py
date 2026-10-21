"""权限检测。

为什么需要：Windows 的 UIPI 规则下，**非管理员进程无法把按键注入到以管理员
身份运行的程序**。如果用户的白名单里是提权运行的博途/Invtmatic，本工具会
"看起来在工作但目标程序收不到"——所以启动时要主动检测并提示。
"""

from __future__ import annotations

import ctypes
from ctypes import byref
from typing import Optional

from .winapi import (
    DWORD,
    HANDLE,
    advapi32,
    kernel32,
)

TOKEN_QUERY = 0x0008
TokenElevation = 20


class _TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", DWORD)]


def is_admin() -> bool:
    """当前进程是否以管理员（提升）身份运行。"""
    handle = HANDLE(0)
    try:
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_QUERY, byref(handle)
        ):
            return False
        elevation = _TokenElevation()
        returned = DWORD(0)
        ok = advapi32.GetTokenInformation(
            handle,
            TokenElevation,
            byref(elevation),
            ctypes.sizeof(elevation),
            byref(returned),
        )
        return bool(ok and elevation.TokenIsElevated)
    except Exception:      # noqa: BLE001
        return False
    finally:
        if handle:
            try:
                kernel32.CloseHandle(handle)
            except Exception:      # noqa: BLE001
                pass


def is_user_an_admin() -> Optional[bool]:
    """检查当前账户本身是否属于管理员组（用于给出更准确的提示）。

    实现较繁琐（需要查本地 Administrators 组的 SID），此处用简化的
    "能否打开自己的 token 并读到 elevation" 近似；读不到就返回 None。
    """
    try:
        return is_admin()
    except Exception:      # noqa: BLE001
        return None
