"""前台窗口与进程识别。

这是"白名单"判定的数据来源，也是钩子回调里的**热路径**，
因此带一层按 hwnd 缓存（TTL 可配），把 OpenProcess 的开销压到几乎为零。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from typing import Dict, Optional, Tuple

from ..core.events import WindowInfo
from .winapi import (
    DWORD,
    HWND,
    PROCESS_QUERY_LIMITED_INFORMATION,
    byref,
    kernel32,
    user32,
)

_CACHE_TTL = 2.0          # 秒：同一个 hwnd 的进程信息缓存多久
_CACHE_MAX = 64
_PATH_BUF = 32768


_EMPTY = WindowInfo()


class _Cache:
    def __init__(self, ttl: float = _CACHE_TTL) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: Dict[int, Tuple[float, Tuple[int, str, str]]] = {}

    def get(self, hwnd: int) -> Optional[Tuple[int, str, str]]:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(hwnd)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        return None

    def put(self, hwnd: int, value: Tuple[int, str, str]) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._data) > _CACHE_MAX:
                self._data.clear()
            self._data[hwnd] = (now, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _Cache()


# ------------------------------------------------------------------ 基础取值


def foreground_hwnd() -> int:
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:      # noqa: BLE001
        return 0


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        length = user32.GetWindowTextLengthW(HWND(hwnd))
        if length <= 0:
            return ""
        buf = (ctypes.c_wchar * (length + 2))()
        user32.GetWindowTextW(HWND(hwnd), buf, length + 1)
        return buf.value or ""
    except Exception:      # noqa: BLE001
        return ""


def window_thread_id(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        return int(user32.GetWindowThreadProcessId(HWND(hwnd), None) or 0)
    except Exception:      # noqa: BLE001
        return 0


def _process_identity(hwnd: int) -> Tuple[int, str, str]:
    """返回 (pid, 进程名小写, 完整路径)。失败时尽量只返回能拿到的部分。"""
    try:
        pid = DWORD(0)
        user32.GetWindowThreadProcessId(HWND(hwnd), byref(pid))
        if pid.value == 0:
            return 0, "", ""

        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not hproc:
            return pid.value, "", ""      # 目标完整性级别更高时拿不到路径
        try:
            size = DWORD(_PATH_BUF)
            buf = (ctypes.c_wchar * _PATH_BUF)()
            path = buf.value if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, byref(size)) else ""
        finally:
            kernel32.CloseHandle(hproc)

        name = os.path.basename(path).lower() if path else ""
        return pid.value, name, path
    except Exception:      # noqa: BLE001
        return 0, "", ""


# ------------------------------------------------------------------ 对外接口


def query(hwnd: Optional[int] = None, use_cache: bool = True) -> WindowInfo:
    """取前台（或指定 hwnd）的信息。异常情况一律返回 valid=False 的对象。"""
    if hwnd is None:
        hwnd = foreground_hwnd()
    hwnd = int(hwnd or 0)
    if not hwnd:
        return _EMPTY

    pid, name, path = 0, "", ""
    if use_cache:
        hit = _cache.get(hwnd)
        if hit is not None:
            pid, name, path = hit
        else:
            pid, name, path = _process_identity(hwnd)
            _cache.put(hwnd, (pid, name, path))
    else:
        pid, name, path = _process_identity(hwnd)

    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        process_name=name,
        process_path=path,
        window_title=window_title(hwnd),
        thread_id=window_thread_id(hwnd),
    )


def invalidate_cache() -> None:
    _cache.clear()
