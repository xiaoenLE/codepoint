"""剪贴板变更监听（AddClipboardFormatListener + WM_CLIPBOARDUPDATE）。

挂在 MessageWindow 上，变更时回调。带序号去重，避免"自己写回剪贴板"
触发递归。
"""

from __future__ import annotations

from typing import Callable

from . import clipboard
from .message_loop import MessageWindow
from .winapi import HWND, WM_CLIPBOARDUPDATE, user32

Listener = Callable[[], None]


class ClipboardListener:
    """注册/注销剪贴板变更监听。"""

    def __init__(self, message_window: MessageWindow, on_change: Listener) -> None:
        self._mw = message_window
        self._on_change = on_change
        self._installed = False
        self._last_seq = 0
        self._suppressed_seq = 0
        message_window.add_handler(WM_CLIPBOARDUPDATE, self._handle)

    # ------------------------------------------------------------ 生命周期

    def install(self) -> bool:
        if self._installed:
            return True
        try:
            ok = bool(self._mw.call(self._add))
        except Exception:      # noqa: BLE001
            ok = False
        self._installed = ok
        if ok:
            # 记住当前序号，避免刚启动时把已有内容当成"刚复制"
            self._last_seq = clipboard.sequence_number()
        return ok

    def uninstall(self) -> None:
        if not self._installed:
            return
        self._installed = False
        try:
            self._mw.call(self._remove)
        except Exception:      # noqa: BLE001
            pass

    def _add(self) -> bool:
        hwnd = self._mw.hwnd
        return bool(hwnd and user32.AddClipboardFormatListener(HWND(hwnd)))

    def _remove(self) -> bool:
        hwnd = self._mw.hwnd
        return bool(hwnd and user32.RemoveClipboardFormatListener(HWND(hwnd)))

    # ------------------------------------------------------------ 通知抑制

    def suppress(self, sequence: int) -> None:
        """标记由本程序写入的剪贴板更新。"""
        self._suppressed_seq = sequence

    def is_suppressed(self, sequence: int) -> bool:
        return sequence == self._suppressed_seq

    # ------------------------------------------------------------ 回调

    def _handle(self, _msg: int, _wparam: int, _lparam: int) -> None:
        seq = clipboard.sequence_number()
        if seq == self._last_seq:
            return
        self._last_seq = seq
        if self.is_suppressed(seq):
            return
        try:
            self._on_change()
        except Exception:      # noqa: BLE001
            pass
