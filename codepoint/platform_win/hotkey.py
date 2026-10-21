"""全局热键（RegisterHotKey / WM_HOTKEY）。"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from . import keylayout
from .message_loop import MessageWindow
from .winapi import HWND, WM_HOTKEY, user32

HotkeyCallback = Callable[[int], None]


class HotkeyManager:
    """在消息线程上注册全局热键。

    热键 ID 由调用方给出（建议从 1 开始），回调收到同一个 ID。
    """

    def __init__(self, message_window: MessageWindow, on_hotkey: HotkeyCallback) -> None:
        self._mw = message_window
        self._on_hotkey = on_hotkey
        self._registered: Dict[int, Tuple[int, int]] = {}
        message_window.add_handler(WM_HOTKEY, self._handle)

    # ------------------------------------------------------------ 注册

    def register(self, hotkey_id: int, mods: keylayout.Modifiers, vk: int) -> bool:
        """注册热键。重复注册同一 ID 会先注销旧的。"""
        self.unregister(hotkey_id)
        flags = keylayout.with_norepeat(keylayout.modifiers_to_win32(mods))

        def do() -> bool:
            hwnd = self._mw.hwnd
            if not hwnd:
                return False
            return bool(user32.RegisterHotKey(HWND(hwnd), hotkey_id, flags, vk))

        try:
            ok = bool(self._mw.call(do))
        except Exception:      # noqa: BLE001
            ok = False
        if ok:
            self._registered[hotkey_id] = (flags, vk)
        return ok

    def register_text(self, hotkey_id: int, text: str) -> bool:
        """按 "Ctrl+Alt+A" 这种字符串注册。空串或非法串返回 False。"""
        parsed = keylayout.parse_hotkey(text)
        if not parsed:
            return False
        mods, vk = parsed
        return self.register(hotkey_id, mods, vk)

    def unregister(self, hotkey_id: int) -> None:
        if hotkey_id not in self._registered:
            return

        def do() -> None:
            hwnd = self._mw.hwnd
            if hwnd:
                user32.UnregisterHotKey(HWND(hwnd), hotkey_id)

        try:
            self._mw.call(do)
        except Exception:      # noqa: BLE001
            pass
        self._registered.pop(hotkey_id, None)

    def unregister_all(self) -> None:
        for hotkey_id in list(self._registered):
            self.unregister(hotkey_id)

    @property
    def registered_ids(self) -> List[int]:
        return sorted(self._registered)

    # ------------------------------------------------------------ 回调

    def _handle(self, _msg: int, wparam: int, _lparam: int) -> None:
        hotkey_id = int(wparam)
        if hotkey_id not in self._registered:
            return
        try:
            self._on_hotkey(hotkey_id)
        except Exception:      # noqa: BLE001
            pass
