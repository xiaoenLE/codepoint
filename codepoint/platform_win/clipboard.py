"""剪贴板读写（文本）。带重试，因为剪贴板可能被别的进程短暂占用。"""

from __future__ import annotations

import ctypes
import time
from typing import Optional

from .winapi import CF_UNICODETEXT, GMEM_MOVEABLE, kernel32, user32


def _open_clipboard(retries: int = 6, delay: float = 0.02) -> bool:
    for _ in range(max(1, retries)):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_text() -> Optional[str]:
    """读剪贴板文本。没有文本格式或打不开返回 None。"""
    if not _open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:      # noqa: BLE001
        return None
    finally:
        user32.CloseClipboard()


def set_text(text: str, retries: int = 6, delay: float = 0.02) -> bool:
    """写剪贴板文本。成功返回 True。"""
    if text is None:
        return False
    for _ in range(max(1, retries)):
        if user32.OpenClipboard(None):
            break
        time.sleep(delay)
    else:
        return False

    try:
        if not user32.EmptyClipboard():
            return False
        size = (len(text) + 1) * 2
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(ptr, text.encode("utf-16-le") + b"\x00\x00", size)
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        # 成功时所有权已移交系统，**不能**再 GlobalFree
        return True
    except Exception:      # noqa: BLE001
        return False
    finally:
        user32.CloseClipboard()


def sequence_number() -> int:
    """剪贴板变更序号——比内容比较便宜得多。"""
    try:
        return int(user32.GetClipboardSequenceNumber())
    except Exception:      # noqa: BLE001
        return 0
