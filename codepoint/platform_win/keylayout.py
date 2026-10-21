"""虚拟键（VK）→ 字符翻译 + 按键名/热键解析。

用途有两个：
1. 钩子里把 VK 翻成"这个键本来会打出什么可见字符"，从而知道要替换成什么。
2. 界面里把热键字符串 "Ctrl+Alt+A" 解析成 (modifiers, vk)。

翻译优先走 ``ToUnicodeEx``（支持任意键盘布局），失败时回落到内置的
US 布局静态表——这保证了即使用户用的是中文/英文以外的布局也不至于完全不工作。
"""

from __future__ import annotations

import ctypes
from typing import Dict, Optional, Tuple

from ..core.events import Modifiers
from .winapi import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    UINT,
    user32,
)

# ------------------------------------------------------------------ VK 常量

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21         # PageUp
VK_NEXT = 0x22          # PageDown
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_OEM_1 = 0xBA         # ; :
VK_OEM_PLUS = 0xBB      # = +
VK_OEM_COMMA = 0xBC     # , <
VK_OEM_MINUS = 0xBD     # - _
VK_OEM_PERIOD = 0xBE    # . >
VK_OEM_2 = 0xBF         # / ?
VK_OEM_3 = 0xC0         # ` ~
VK_OEM_4 = 0xDB         # [ {
VK_OEM_5 = 0xDC         # \ |
VK_OEM_6 = 0xDD         # ] }
VK_OEM_7 = 0xDE         # ' "
VK_OEM_8 = 0xDF
VK_OEM_102 = 0xE2       # 欧洲布局 < >

MODIFIER_VKS = {
    VK_SHIFT, VK_LSHIFT, VK_RSHIFT,
    VK_CONTROL, VK_LCONTROL, VK_RCONTROL,
    VK_MENU, VK_LMENU, VK_RMENU,
    VK_LWIN, VK_RWIN,
    VK_CAPITAL,
}

OEM_PUNCTUATION_VKS = {
    VK_OEM_1, VK_OEM_PLUS, VK_OEM_COMMA, VK_OEM_MINUS, VK_OEM_PERIOD,
    VK_OEM_2, VK_OEM_3, VK_OEM_4, VK_OEM_5, VK_OEM_6, VK_OEM_7,
    VK_OEM_8, VK_OEM_102,
}

NAVIGATION_VKS = {
    VK_PRIOR, VK_NEXT, VK_END, VK_HOME, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_DELETE, VK_ESCAPE,
}

# ------------------------------------------------------------------ 名称表

_VK_NAMES: Dict[int, str] = {
    VK_BACK: "Backspace", VK_TAB: "Tab", VK_RETURN: "Enter",
    VK_ESCAPE: "Esc", VK_SPACE: "Space", VK_PRIOR: "PageUp", VK_NEXT: "PageDown",
    VK_END: "End", VK_HOME: "Home", VK_LEFT: "Left", VK_UP: "Up",
    VK_RIGHT: "Right", VK_DOWN: "Down", VK_DELETE: "Delete",
    VK_OEM_1: ";", VK_OEM_PLUS: "=", VK_OEM_COMMA: ",", VK_OEM_MINUS: "-",
    VK_OEM_PERIOD: ".", VK_OEM_2: "/", VK_OEM_3: "`", VK_OEM_4: "[",
    VK_OEM_5: "\\", VK_OEM_6: "]", VK_OEM_7: "'",
}
for _c in range(ord("A"), ord("Z") + 1):
    _VK_NAMES[_c] = chr(_c)
for _c in range(ord("0"), ord("9") + 1):
    _VK_NAMES[_c] = chr(_c)
for _i in range(1, 25):
    _VK_NAMES[0x6F + _i] = f"F{_i}"

_NAME_TO_VK: Dict[str, int] = {v.upper(): k for k, v in _VK_NAMES.items()}
_NAME_TO_VK.update({
    "ESCAPE": VK_ESCAPE, "RETURN": VK_RETURN, "SPACEBAR": VK_SPACE,
    "PGUP": VK_PRIOR, "PGDN": VK_NEXT, "DEL": VK_DELETE, "INS": 0x2D,
})


def vk_name(vk: int) -> str:
    return _VK_NAMES.get(vk, f"VK_{vk:02X}")


def vk_from_name(name: str) -> Optional[int]:
    return _NAME_TO_VK.get((name or "").strip().upper())


def is_modifier_vk(vk: int) -> bool:
    return vk in MODIFIER_VKS


# ------------------------------------------------------------------ 静态回退表

_US_PLAIN: Dict[int, str] = {
    VK_OEM_1: ";", VK_OEM_PLUS: "=", VK_OEM_COMMA: ",", VK_OEM_MINUS: "-",
    VK_OEM_PERIOD: ".", VK_OEM_2: "/", VK_OEM_3: "`", VK_OEM_4: "[",
    VK_OEM_5: "\\", VK_OEM_6: "]", VK_OEM_7: "'", VK_SPACE: " ",
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
    0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
}
_US_SHIFTED: Dict[int, str] = {
    VK_OEM_1: ":", VK_OEM_PLUS: "+", VK_OEM_COMMA: "<", VK_OEM_MINUS: "_",
    VK_OEM_PERIOD: ">", VK_OEM_2: "?", VK_OEM_3: "~", VK_OEM_4: "{",
    VK_OEM_5: "|", VK_OEM_6: "}", VK_OEM_7: '"', VK_SPACE: " ",
    0x30: ")", 0x31: "!", 0x32: "@", 0x33: "#", 0x34: "$",
    0x35: "%", 0x36: "^", 0x37: "&", 0x38: "*", 0x39: "(",
}
for _c in range(ord("A"), ord("Z") + 1):
    _US_PLAIN[_c] = chr(_c).lower()
    _US_SHIFTED[_c] = chr(_c).upper()


def _fallback_char(vk: int, shift: bool, caps: bool = False) -> Optional[str]:
    """静态回退表。

    ⚠ 关键细节：**CapsLock 只影响字母，不影响标点**。
    如果无脑把 CapsLock 当成"Shift 反向"，那么开着大写锁定时
    `;` 会被算成 `:`，于是在白名单程序里按 `;` 打出来的是 `:`（真实踩过的坑）。
    """
    is_letter = 0x41 <= vk <= 0x5A
    upper = (shift != caps) if is_letter else shift
    table = _US_SHIFTED if upper else _US_PLAIN
    return table.get(vk)


# ------------------------------------------------------------------ 动态翻译

_key_state = (ctypes.c_char * 256)()
_scratch = (ctypes.c_wchar * 8)()


def current_modifiers(key_state: Optional[bytes] = None) -> Modifiers:
    """从键盘状态数组（或现场读取）解析修饰键。

    注意这里**不把 CapsLock 折算进 shift**：CapsLock 只影响字母大小写，
    对 `;` `,` `.` 这些标点键没有任何影响。折算进来会让 `;` 被误判成 `:`。
    需要"字母大小写"语义的地方请用 :func:`caps_lock_on`。
    """
    if key_state is None:
        if not user32.GetKeyboardState(_key_state):
            return Modifiers()
        key_state = bytes(_key_state)
    down = 0x80
    shift = bool(key_state[VK_SHIFT] & down) or bool(key_state[VK_LSHIFT] & down) \
        or bool(key_state[VK_RSHIFT] & down)
    ctrl = bool(key_state[VK_CONTROL] & down) or bool(key_state[VK_LCONTROL] & down) \
        or bool(key_state[VK_RCONTROL] & down)
    alt = bool(key_state[VK_MENU] & down) or bool(key_state[VK_LMENU] & down) \
        or bool(key_state[VK_RMENU] & down)
    win = bool(key_state[VK_LWIN] & down) or bool(key_state[VK_RWIN] & down)
    return Modifiers(shift=shift, ctrl=ctrl, alt=alt, win=win)


def async_modifiers() -> Modifiers:
    """用 ``GetAsyncKeyState`` 读**物理**修饰键状态。

    为什么不用 :func:`current_modifiers`（GetKeyboardState）：那个反映的是
    **本线程消息队列**的状态，线程没在泵消息时读出来是脏数据（实测静息状态
    下 Ctrl/Alt 恒为 True）。录制热键的对话框必须信物理状态 ——
    尤其是 Alt：Windows 上 Tk 把 Alt 报在 ``event.state`` 的 0x20000 位
    而不是 X11 口径的 0x0008，只查 state 位永远识别不到 Alt（真实 bug）。
    """
    down = 0x8000

    def held(*vks: int) -> bool:
        return any(bool(user32.GetAsyncKeyState(vk) & down) for vk in vks)

    return Modifiers(
        shift=held(VK_SHIFT, VK_LSHIFT, VK_RSHIFT),
        ctrl=held(VK_CONTROL, VK_LCONTROL, VK_RCONTROL),
        alt=held(VK_MENU, VK_LMENU, VK_RMENU),
        win=held(VK_LWIN, VK_RWIN),
    )


def caps_lock_on(key_state: Optional[bytes] = None) -> bool:
    """CapsLock 是否处于打开状态（锁定位）。"""
    if key_state is None:
        if not user32.GetKeyboardState(_key_state):
            return False
        key_state = bytes(_key_state)
    return bool(key_state[VK_CAPITAL] & 0x01)


def vk_to_char(vk: int, scan_code: int = 0, hkl: int = 0,
               key_state: Optional[bytes] = None) -> Optional[str]:
    """把 (VK, 扫描码) 翻成该键"本来会输入的可见字符"。

    - 只用修饰键 Shift/CapsLock 参与，Ctrl/Alt 组合一律返回 None（那是快捷键）
    - 依赖 ToUnicodeEx，失败时回落到 US 布局静态表
    - 失败回退时**不是**直接取静态表，而是先看真实修饰键状态再查表
      （ToUnicodeEx 在输入法组字等状态下会返回 0，此时静态表是唯一的依据）
    """
    if vk in MODIFIER_VKS:
        return None

    mods = current_modifiers(key_state)
    if mods.ctrl or mods.alt or mods.win:
        return None

    state = key_state
    if state is None:
        if not user32.GetKeyboardState(_key_state):
            return _fallback_char(vk, mods.shift)
        state = bytes(_key_state)

    buf = (ctypes.c_char * 256).from_buffer_copy(state)
    if not scan_code:
        scan_code = user32.MapVirtualKeyW(UINT(vk), 0)      # MAPVK_VK_TO_VSC

    # 第 6 个参数 0x04 = 不修改键盘状态。**必须加**：
    # 不加时 ToUnicodeEx 会"消费"掉死键（如中文输入法/欧洲键盘的死键状态），
    # 连续调用会互相干扰，表现为偶发地把字符翻错。
    n = user32.ToUnicodeEx(
        UINT(vk), UINT(scan_code), buf, _scratch, 8, 0x04, hkl or 0
    )
    if n == 1:
        ch = _scratch[0]
        if ch and ch.isprintable():
            return ch
    elif n > 1:
        return str(_scratch[:n])

    return _fallback_char(vk, mods.shift, caps_lock_on(state))


# ------------------------------------------------------------------ 热键解析

_ALIASES = {
    "CTRL": "CTRL", "CONTROL": "CTRL",
    "ALT": "ALT", "MENU": "ALT",
    "SHIFT": "SHIFT",
    "WIN": "WIN", "WINDOWS": "WIN", "META": "WIN", "SUPER": "WIN",
}


def parse_hotkey(text: str) -> Optional[Tuple[Modifiers, int]]:
    """把 "Ctrl+Alt+A" 解析成 (Modifiers, vk)。无法解析返回 None。

    要求至少一个修饰键 + 一个普通键（否则会和正常打字冲突）。
    """
    if not text or not text.strip():
        return None
    parts = [p.strip() for p in text.replace("＋", "+").split("+") if p.strip()]
    if len(parts) < 2:
        return None

    shift = ctrl = alt = win = False
    vk: Optional[int] = None
    for p in parts:
        key = _ALIASES.get(p.upper())
        if key == "CTRL":
            ctrl = True
        elif key == "ALT":
            alt = True
        elif key == "SHIFT":
            shift = True
        elif key == "WIN":
            win = True
        else:
            if vk is not None:
                return None            # 出现了两个普通键
            vk = vk_from_name(p)
            if vk is None:
                return None
    if vk is None or not (ctrl or alt or win or shift):
        return None
    return Modifiers(shift=shift, ctrl=ctrl, alt=alt, win=win), vk


def format_hotkey(mods: Modifiers, vk: int) -> str:
    name = vk_name(vk)
    prefix = mods.describe()
    return f"{prefix}+{name}" if prefix and prefix != "无" else name


def modifiers_to_win32(mods: Modifiers) -> int:
    bits = 0
    if mods.alt:
        bits |= MOD_ALT
    if mods.ctrl:
        bits |= MOD_CONTROL
    if mods.shift:
        bits |= MOD_SHIFT
    if mods.win:
        bits |= MOD_WIN
    return bits


def with_norepeat(flags: int) -> int:
    return flags | MOD_NOREPEAT
