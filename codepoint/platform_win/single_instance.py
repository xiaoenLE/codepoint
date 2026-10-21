"""单实例守卫（命名互斥体）。

同一时刻只允许跑一个实例：第二个实例会提示用户去托盘区找已经运行的那个。
互斥体句柄要在整个进程生命周期内持有，不能释放。
"""

from __future__ import annotations

import ctypes
import os
from typing import Optional

from .winapi import ERROR_ALREADY_EXISTS, kernel32, last_error

_MUTEX_PREFIX = "Global\\" if os.name == "nt" else ""


class SingleInstance:
    """``with SingleInstance() as first:`` —— first 为 False 表示已有实例在跑。"""

    def __init__(self, name: str = "CodePoint") -> None:
        self._name = f"{_MUTEX_PREFIX}{name}"
        self._handle: Optional[int] = None
        self.acquired = False

    def acquire(self) -> bool:
        if self._handle:
            return self.acquired
        # 用 Global\ 前缀可跨会话；命名冲突时 CreateMutexW 返回已有句柄并置 ERROR_ALREADY_EXISTS
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self._name)
        err = last_error()
        if not handle:
            # Global\ 可能因权限失败，退回会话内命名
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, False, self._name.replace("Global\\", "Local\\"))
            err = last_error()
        if not handle:
            self.acquired = True       # 守不住也别拦着用户用
            return True
        self._handle = int(handle)
        self.acquired = err != ERROR_ALREADY_EXISTS
        return self.acquired

    def release(self) -> None:
        if self._handle:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:      # noqa: BLE001
                pass
            self._handle = None
        self.acquired = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
