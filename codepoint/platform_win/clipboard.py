"""Win32 剪贴板文本读写。"""

from __future__ import annotations

import ctypes
import time
from typing import Callable, Optional, Tuple

from .winapi import CF_UNICODETEXT, GMEM_MOVEABLE, kernel32, user32


_register_format = user32.RegisterClipboardFormatW
_register_format.argtypes = [ctypes.c_wchar_p]
_register_format.restype = ctypes.c_uint
# Invtmatic 所用的编辑器在没有选区时可复制整行，并用这个格式标记。
_WHOLE_LINE = _register_format("MSDEVLineSelect")


def _open_clipboard(retries: int = 6, delay: float = 0.02) -> bool:
    attempts = max(1, retries)
    for attempt in range(attempts):
        if user32.OpenClipboard(None):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def _read_text() -> Optional[str]:
    """读取已打开的剪贴板。"""
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


def get_text(*, selection_only: bool = False) -> Optional[str]:
    """返回剪贴板文本。选区读取遇到带标记的整行复制时返回空字符串。"""
    if not _open_clipboard():
        return None
    try:
        if selection_only and _WHOLE_LINE and user32.IsClipboardFormatAvailable(_WHOLE_LINE):
            return ""
        return _read_text()
    except Exception:      # noqa: BLE001
        return None
    finally:
        user32.CloseClipboard()


def _write_text(text: str) -> bool:
    """写入已打开的剪贴板。"""
    raw = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
    if not handle:
        return False
    try:
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        try:
            ctypes.memmove(ptr, raw, len(raw))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.EmptyClipboard() or not user32.SetClipboardData(CF_UNICODETEXT, handle):
            return False
        handle = None    # 所有权已移交系统
        return True
    finally:
        if handle:
            kernel32.GlobalFree(handle)


def _notify_written(on_written: Optional[Callable[[int], None]]) -> int:
    # CloseClipboard 会补充其他文本格式并改变序号，写入序号必须在关闭后读取。
    sequence = sequence_number()
    if on_written is not None:
        try:
            on_written(sequence)
        except Exception:      # noqa: BLE001 —— 写入已经成功
            pass
    return sequence


def set_text(text: str, retries: int = 6, delay: float = 0.02, *,
             expected_sequence: Optional[int] = None,
             expected_text: Optional[str] = None,
             on_written: Optional[Callable[[int], None]] = None) -> bool:
    """写剪贴板。指定条件时，仅在序号和文本仍符合预期时写入。"""
    if text is None or not _open_clipboard(retries, delay):
        return False
    try:
        if expected_sequence is not None and sequence_number() != expected_sequence:
            return False
        if expected_text is not None and _read_text() != expected_text:
            return False
        if not _write_text(text):
            return False
    except Exception:      # noqa: BLE001
        return False
    finally:
        user32.CloseClipboard()
    if on_written is not None:
        _notify_written(on_written)
    return True


def swap_text(text: str, *, retries: int = 6,
              on_written: Optional[Callable[[int], None]] = None) -> Optional[Tuple[Optional[str], int]]:
    """同一次打开内备份并替换文本，返回旧文本和写入后的序号。"""
    if not _open_clipboard(retries):
        return None
    try:
        old = _read_text()
        if not _write_text(text):
            return None
    except Exception:      # noqa: BLE001
        return None
    finally:
        user32.CloseClipboard()
    return old, _notify_written(on_written)


def sequence_number() -> int:
    """剪贴板变更序号——比内容比较便宜得多。"""
    try:
        return int(user32.GetClipboardSequenceNumber())
    except Exception:      # noqa: BLE001
        return 0
