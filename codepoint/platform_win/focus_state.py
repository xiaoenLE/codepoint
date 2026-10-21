"""焦点状态探测 —— 注入按键前的最后一道闸。

背景（用户报障 v1.6.3）：
    "我在其他软件中，怎么会选择到上面的属性，然后进行了点击，导致软件被关闭。"
    "反正是触发他获取后就点到菜单那边去了。"

抓上下文要注入 ``Shift+方向键``。如果此刻前台窗口的焦点其实在**菜单栏 / 工具条 /
列表**上（用户刚按过 Alt、或本来就是从菜单里点出来的），方向键就会去那里挪选中项，
紧接着一个回车就成了"点击那一项" —— 在编辑器里按个热键，把别的程序关掉了。

所以动手前问两个问题：

1. **有没有菜单正开着？** ``GUITHREADINFO.hwndMenuOwner`` 非空就是有。
2. **焦点落在什么控件上？** 菜单（``#32768``）、工具条、列表、按钮……都不是文本框。

判定刻意做成"只挡确定的坏情况"：类名在已知非文本名单里 → 拒绝；
认不出来（自绘控件、CODESYS 那种）→ 放行。宁可漏挡，也不能把正常流程卡死。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from .winapi import DWORD, HWND, byref, gui_thread_info, user32

#: 确定了"不可能是文本输入区"的窗口类名（小写比较）。
#:
#: ``#32768`` 是 Windows 的菜单弹出窗；其余是通用控件 —— 焦点落在它们身上时，
#: 注入方向键等于在操作控件，不是在写代码。
_NON_TEXT_CLASSES = frozenset({
    "#32768",                 # 菜单弹出窗（Menu）
    "toolbarwindow32",        # 工具条
    "rebarwindow32",          # 工具条容器
    "msctls_toolbar",         # 旧式工具条
    "systabcontrol32",        # 选项卡
    "sysheader32",            # 表头
    "systreeview32",          # 树
    "syslistview32",          # 列表
    "sysipaddress32",
    "button",                 # 按钮
    "static",                 # 静态文本（含图标/分隔线）
    "msctls_statusbar32",     # 状态栏
    "msctls_trackbar32",      # 滑杆
    "msctls_progress32",      # 进度条
    "scrollbar",              # 滚动条
    "combobox",               # 下拉框本体（可编辑的那个有独立的 Edit 子窗）
})


@dataclass(frozen=True)
class FocusState:
    """前台线程的焦点快照。"""

    hwnd: int = 0             # 前台窗口
    focus_hwnd: int = 0       # 焦点控件
    focus_class: str = ""     # 焦点控件的窗口类名
    menu_open: bool = False   # 有菜单正开着

    @property
    def looks_editable(self) -> bool:
        """焦点是不是可能在文本框里。

        认不出来（``focus_class`` 为空）也算"可能" —— 不认识的控件不要去猜，
        猜错就会把正常补全挡掉。
        """
        cls = (self.focus_class or "").strip().lower()
        if not cls:
            return True
        return cls not in _NON_TEXT_CLASSES


def class_name(hwnd: int) -> str:
    """取窗口类名（拿不到返回空串）。"""
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(HWND(hwnd), buf, 256):
            return buf.value
    except Exception:      # noqa: BLE001
        return ""
    return ""


def query() -> FocusState:
    """取当前前台窗口的焦点状态。任何一步失败都退化成"不知道"，不抛异常。"""
    try:
        hwnd = int(user32.GetForegroundWindow() or 0)
    except Exception:      # noqa: BLE001
        return FocusState()
    if not hwnd:
        return FocusState()

    pid = DWORD(0)
    try:
        tid = int(user32.GetWindowThreadProcessId(HWND(hwnd), byref(pid)) or 0)
    except Exception:      # noqa: BLE001
        return FocusState(hwnd=hwnd)
    if not tid:
        return FocusState(hwnd=hwnd)

    info = gui_thread_info(tid)
    if info is None:
        return FocusState(hwnd=hwnd)

    focus = int(info.hwndFocus or 0)
    return FocusState(
        hwnd=hwnd,
        focus_hwnd=focus,
        focus_class=class_name(focus),
        menu_open=bool(info.hwndMenuOwner),
    )


def report() -> str:
    """给日志用的一行描述。"""
    state = query()
    return ("hwnd=%s focus=%s class=%r menu=%s"
            % (state.hwnd, state.focus_hwnd, state.focus_class,
               "开着" if state.menu_open else "无"))
