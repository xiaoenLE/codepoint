"""系统托盘图标（Shell_NotifyIcon）。

挂在 MessageWindow 上，所以不需要额外线程。右键弹菜单用 TrackPopupMenu
返回命令 ID，再回调给上层——托盘本身不认识任何业务。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import byref
from dataclasses import dataclass
from typing import Callable, List, Optional

from .message_loop import MessageWindow
from .winapi import (
    HICON,
    HWND,
    IDI_APPLICATION,
    IMAGE_ICON,
    LR_DEFAULTSIZE,
    LR_LOADFROMFILE,
    MF_CHECKED,
    MF_GRAYED,
    MF_SEPARATOR,
    MF_STRING,
    NIF_ICON,
    NIF_INFO,
    NIF_MESSAGE,
    NIF_TIP,
    NIM_ADD,
    NIM_DELETE,
    NIM_MODIFY,
    NOTIFYICONDATAW,
    POINT,
    TPM_RETURNCMD,
    TPM_RIGHTBUTTON,
    WM_APP,
    WM_LBUTTONDBLCLK,
    WM_LBUTTONUP,
    WM_NULL,
    WM_RBUTTONUP,
    WM_TASKBAR_CREATED,
    shell32,
    user32,
)

TRAY_MESSAGE = WM_APP + 10
DEFAULT_ICON_ID = 1

# 开机自启时的图标重试策略：登录阶段 explorer 可能晚几十秒才就绪
_RETRY_INTERVAL = 2.0      # 秒
_RETRY_TIMES = 15          # 合计约 30 秒


@dataclass
class TrayMenuItem:
    """一条托盘菜单项。id 为 0 表示纯分隔符。"""

    id: int
    text: str = ""
    checked: bool = False
    enabled: bool = True
    separator_before: bool = False


class TrayIcon:
    """托盘图标 + 右键菜单。"""

    def __init__(
        self,
        message_window: MessageWindow,
        tooltip: str = "CodePoint",
        icon_path: Optional[str] = None,
        icon_id: int = DEFAULT_ICON_ID,
    ) -> None:
        self._mw = message_window
        self._tooltip = tooltip
        self._icon_path = icon_path
        self._icon_id = icon_id
        self._hicon: Optional[int] = None
        self._owns_icon = False
        self._added = False
        self._menu_items: List[TrayMenuItem] = []
        self._on_command: Optional[Callable[[int], None]] = None
        self._on_activate: Optional[Callable[[], None]] = None
        self._on_giveup: Optional[Callable[[], None]] = None
        self._stop_retry = threading.Event()
        self._retrying = False
        message_window.add_handler(TRAY_MESSAGE, self._handle)
        # explorer 崩溃重启 / 登录阶段补发 —— 收到就重建图标
        message_window.add_handler(WM_TASKBAR_CREATED, self._on_taskbar_created)

    # ------------------------------------------------------------ 配置

    def set_menu(self, items: List[TrayMenuItem]) -> None:
        self._menu_items = list(items)
        if self._added:
            self.refresh()

    def set_on_command(self, callback: Callable[[int], None]) -> None:
        self._on_command = callback

    def set_on_activate(self, callback: Callable[[], None]) -> None:
        """左键单击托盘图标。"""
        self._on_activate = callback

    # ------------------------------------------------------------ 生命周期

    def show(self, retry: bool = True) -> bool:
        """把图标加到托盘区。

        ``retry=True``（默认）时，第一次失败会转到后台慢慢重试 ——
        **这是开机自启能不能"看得见"的关键**：登录阶段 explorer 往往还没起来，
        ``Shell_NotifyIconW(NIM_ADD)`` 会直接失败。程序照样在后台跑，但没有
        托盘图标、窗口又是最小化启动（``--minimized``），于是彻底隐形 ——
        用户只会得到一句"开机自启没生效"，其实是图标没加上。
        """
        try:
            ok = bool(self._mw.call(self._add))
        except Exception:      # noqa: BLE001
            ok = False
        if ok or not retry:
            return ok
        self._start_retry()
        return False

    def set_on_giveup(self, callback: Optional[Callable[[], None]]) -> None:
        """重试全部失败后的回调（调用方据此把窗口显示出来，别让程序隐形）。"""
        self._on_giveup = callback

    def hide(self) -> None:
        self._stop_retry.set()
        if not self._added:
            return
        try:
            self._mw.call(self._delete)
        except Exception:      # noqa: BLE001
            pass

    def refresh(self) -> None:
        if not self._added:
            return
        try:
            self._mw.call(self._modify)
        except Exception:      # noqa: BLE001
            pass

    def set_tooltip(self, text: str) -> None:
        self._tooltip = text
        self.refresh()

    def show_balloon(self, title: str, text: str) -> None:
        if not self._added:
            return

        def do() -> None:
            nid = self._base_data()
            nid.uFlags = NIF_INFO | NIF_TIP | NIF_ICON
            nid.szInfoTitle = title[:63]
            nid.szInfo = text[:255]
            nid.hIcon = self._hicon
            shell32.Shell_NotifyIconW(NIM_MODIFY, byref(nid))

        try:
            self._mw.call(do)
        except Exception:      # noqa: BLE001
            pass

    # ------------------------------------------------------------ 内部：图标

    def _load_icon(self) -> int:
        if self._hicon:
            return self._hicon
        handle = None
        if self._icon_path:
            handle = user32.LoadImageW(
                None, self._icon_path, IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            self._owns_icon = bool(handle)
        if not handle:
            handle = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
            self._owns_icon = False
        self._hicon = int(handle) if handle else 0
        return self._hicon

    def _base_data(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = HWND(self._mw.hwnd or 0)
        nid.uID = self._icon_id
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = TRAY_MESSAGE
        nid.hIcon = HICON(self._load_icon())
        nid.szTip = (self._tooltip or "")[:127]
        return nid

    # ------------------------------------------------------------ 内部：装卸

    def _start_retry(self) -> None:
        """后台重试加图标（开机自启/explorer 未就绪的兜底）。

        刻意放在**独立线程**里等：重试期间不能占着消息线程睡，
        否则钩子、热键、剪贴板全会一起停摆。
        """
        if self._retrying:
            return
        self._retrying = True

        def loop() -> None:
            try:
                for _ in range(_RETRY_TIMES):
                    if self._stop_retry.wait(_RETRY_INTERVAL):
                        return
                    try:
                        if self._mw.call(self._add):
                            return
                    except Exception:      # noqa: BLE001
                        pass
                if self._on_giveup is not None:
                    try:
                        self._on_giveup()
                    except Exception:      # noqa: BLE001
                        pass
            finally:
                self._retrying = False

        threading.Thread(target=loop, name="codepoint-tray-retry",
                         daemon=True).start()

    def _on_taskbar_created(self, _msg: int, _wparam: int, _lparam: int) -> None:
        """explorer 重建了任务栏 —— 之前加的图标已经失效，重新加一遍。"""

        def do() -> None:
            self._added = False            # 旧图标随 explorer 一起没了
            if self._add() and self._menu_items:
                self.refresh()

        try:
            self._mw.call(do)
        except Exception:      # noqa: BLE001
            pass

    def _add(self) -> bool:
        if self._added:
            return True
        nid = self._base_data()
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, byref(nid)))
        self._added = ok
        return ok

    def _modify(self) -> bool:
        nid = self._base_data()
        return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, byref(nid)))

    def _delete(self) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = HWND(self._mw.hwnd or 0)
        nid.uID = self._icon_id
        shell32.Shell_NotifyIconW(NIM_DELETE, byref(nid))
        self._added = False
        if self._owns_icon and self._hicon:
            user32.DestroyIcon(HICON(self._hicon))
        self._hicon = None

    # ------------------------------------------------------------ 消息处理

    def _handle(self, _msg: int, _wparam: int, lparam: int) -> None:
        mouse_msg = lparam & 0xFFFF
        if mouse_msg == WM_RBUTTONUP:
            self._popup_menu()
        elif mouse_msg in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            if self._on_activate is not None:
                self._on_activate()

    def _popup_menu(self) -> None:
        hwnd = self._mw.hwnd
        if not hwnd:
            return
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            for item in self._menu_items:
                if item.separator_before:
                    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                if item.id == 0:
                    continue
                flags = MF_STRING
                if item.checked:
                    flags |= MF_CHECKED
                if not item.enabled:
                    flags |= MF_GRAYED
                user32.AppendMenuW(hmenu, flags, item.id, item.text)

            pt = POINT()
            user32.GetCursorPos(byref(pt))
            # 经典托盘坑：不先抢前台，菜单不会自动消失
            user32.SetForegroundWindow(HWND(hwnd))
            cmd = user32.TrackPopupMenu(
                hmenu,
                TPM_RETURNCMD | TPM_RIGHTBUTTON,
                pt.x, pt.y, 0, HWND(hwnd), None,
            )
            user32.PostMessageW(HWND(hwnd), WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(hmenu)

        if cmd and self._on_command is not None:
            try:
                self._on_command(int(cmd))
            except Exception:      # noqa: BLE001
                pass
