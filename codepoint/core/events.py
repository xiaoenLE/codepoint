"""跨层数据契约（DTO）。

放在 core 是**架构上的刻意选择**：platform_win 负责把 Win32 概念翻译成这里的
中立类型，services/ui 只认识这些类型。于是：
  - services 层可以在没有 Windows 的机器上单元测试
  - 换平台（比如以后做 macOS 版）只要重写 platform 层
本模块仍然零依赖。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

_MODIFIER_KEYS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})

# ------------------------------------------------------------------ 修饰键


@dataclass(frozen=True)
class Modifiers:
    shift: bool = False
    ctrl: bool = False
    alt: bool = False
    win: bool = False

    @property
    def any(self) -> bool:
        return self.shift or self.ctrl or self.alt or self.win

    @property
    def has_command(self) -> bool:
        """按下了 Ctrl/Alt/Win —— 这是快捷键，绝不能拿去做标点替换。"""
        return self.ctrl or self.alt or self.win

    def describe(self) -> str:
        parts = []
        if self.ctrl:
            parts.append("Ctrl")
        if self.alt:
            parts.append("Alt")
        if self.shift:
            parts.append("Shift")
        if self.win:
            parts.append("Win")
        return "+".join(parts) or "无"


# ------------------------------------------------------------------ 按键


@dataclass(frozen=True)
class KeyPress:
    """一次按键 —— 已由平台层把 VK/扫描码翻译成"会打出什么字符"。"""

    vk: int = 0
    scan_code: int = 0
    is_down: bool = True
    char: Optional[str] = None          # 该键对应的可见字符，无则 None
    modifiers: Modifiers = field(default_factory=Modifiers)
    timestamp: float = field(default_factory=time.monotonic)

    # —— 语义分类：给快捷输入的缓冲区用 ——

    @property
    def is_modifier(self) -> bool:
        return self.vk in _MODIFIER_KEYS

    @property
    def is_word_char(self) -> bool:
        """构成"一个词"的字符：字母、数字、下划线。"""
        c = self.char
        return bool(c) and (c.isalnum() or c == "_")

    @property
    def is_separator(self) -> bool:
        """会切断"一个词"的按键：空白、标点、回车、Tab。"""
        c = self.char
        if c is None:
            return True          # 无可见字符的键（方向键、F 键…）一律视为分隔
        return not (c.isalnum() or c == "_")


# ------------------------------------------------------------------ 窗口


@dataclass(frozen=True)
class WindowInfo:
    """前台窗口快照。"""

    hwnd: int = 0
    pid: int = 0
    process_name: str = ""      # 小写文件名
    process_path: str = ""
    window_title: str = ""
    thread_id: int = 0

    @property
    def valid(self) -> bool:
        return self.hwnd != 0


# ------------------------------------------------------------------ 输入法


@dataclass(frozen=True)
class ImeSnapshot:
    """输入法状态快照。available=False 表示"没问到"，调用方需自行决定默认行为。"""

    available: bool = False
    open: bool = False          # 输入法是否处于打开状态
    native: bool = False        # 中文/日文等"本地语言"输入模式
    fullshape: bool = False     # 全角
    symbol: bool = False        # 中文标点（IMM 的 IME_CMODE_SYMBOL）
    katakana: bool = False
    composing: bool = False

    @property
    def cjk_mode(self) -> bool:
        """处于可能产出全角标点的状态。"""
        return self.open and (self.symbol or self.fullshape or self.native or self.katakana)

    @property
    def definitely_halfwidth(self) -> bool:
        """**确定**这个键打出来的就是半角字符，不需要我们插手。

        只有"问到状态、且输入法根本没打开"才敢这么说。
        中文标点模式下 `;` 会变成 `；`，所以那时不能跳过。
        """
        return self.available and not self.open


UNAVAILABLE_IME = ImeSnapshot()
EMPTY_WINDOW = WindowInfo()
NO_MODIFIERS = Modifiers()


# ------------------------------------------------------------------ 常用虚拟键码
#
# 这些是 Windows 虚拟键码，放在 core 是刻意的：它们是 platform 层与 services 层
# 之间的**数据契约**（platform 层负责填，services 层只做比较），
# 而不是某个 API 调用。这样 services 不必 import 任何 Win32 模块。

VK_BACK = 0x08      # Backspace
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_DELETE = 0x2E
