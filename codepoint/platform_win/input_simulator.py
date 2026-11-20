"""按键注入。

全部走 Win32 ``SendInput``（键盘注入 + Unicode 注入）。

关键点：**注入的 Unicode 字符会绕过输入法**（KEYEVENTF_UNICODE 直接产生 WM_CHAR），
这正是"把 IME 打出的全角标点换回半角"的手段。
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import List, Sequence

from . import winapi as _w
from . import clipboard as _clipboard
from .keylayout import (
    VK_BACK, VK_CONTROL, VK_DOWN, VK_LEFT, VK_RETURN, VK_RIGHT, VK_SHIFT, VK_TAB, VK_UP,
)
from .winapi import (
    DWORD,
    INPUT,
    INPUT_KEYBOARD,
    KEYBDINPUT,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    UINT,
    ULONG_PTR,
    WORD,
)


def _make_key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(
        wVk=WORD(vk),
        wScan=WORD(scan),
        dwFlags=DWORD(flags),
        time=DWORD(0),
        dwExtraInfo=ULONG_PTR(0),
    )
    return item


def _send(items: Sequence[INPUT]) -> int:
    """批量下发一个 INPUT 数组，返回系统实际接受的条数。"""
    if not items:
        return 0
    arr = (INPUT * len(items))(*items)
    return int(_w.user32.SendInput(UINT(len(items)), arr, ctypes.sizeof(INPUT)))


def _release_modifier(vk: int) -> None:
    """把一个修饰键**确保**抬起来：失败重试，再不行就补一对 down+up。

    为什么值得这么较真：``SendInput`` 被 UIPI 之类的原因拒掉时**返回 0 但不抛异常**，
    原来的写法（发一次 key-up 就完事）会把修饰键留在"按下"状态。

    Shift 卡住的后果特别严重 —— 键盘钩子只对 Ctrl/Alt/Win 按物理状态自愈，
    Shift 必须信事件流（见 ``keyboard_hook._heal_command_modifiers`` 的说明），
    所以它不会被自动清掉。于是之后**每一次**按热键，``wait_modifiers_released``
    都会判成"按键没松开"直接放弃，整个功能看着就像坏了。
    （用户报障原话："打断之后一直说按钮没有松开"。）

    补 down+up 的理由：只发 key-up 时，如果系统认为它已经是抬起的，这一下
    会被丢掉，残留还在；先按一下再松开才真能把状态带回来。

    ⚠ **Alt / Win 例外，永不补按**（见 ``_KEYUP_ONLY_VKS``）：这两个键按一下
    就是一次真实操作 —— Alt 打开菜单栏、Win 弹开始菜单。为了清一个残留而
    打开人家的菜单，代价太大。
    """
    if vk in _KEYUP_ONLY_VKS:
        for _ in range(3):
            if _send([_make_key_input(vk, 0, KEYEVENTF_KEYUP)]) > 0:
                return
            time.sleep(0.005)
        return
    for _ in range(3):
        if _send([_make_key_input(vk, 0, KEYEVENTF_KEYUP)]) > 0:
            return
        time.sleep(0.005)
    _send([_make_key_input(vk, 0, 0)])
    _send([_make_key_input(vk, 0, KEYEVENTF_KEYUP)])


# 实测结论（见 tools/live_check.py）：
#   把「同一个键的多次按下」塞进**同一个** SendInput 批次，Windows 会把它们
#   当作键盘自动重复合并掉——很多程序（含 Tk）最终只认到 1 次。
#   改成「一次按键 = 一次 SendInput」后，即使间隔 0 ms 也全部生效。
#   所以下面 send_key_press 刻意不合并；而 send_unicode_text 里每个字符的
#   wScan 不同（同一个字符重复也验证过没问题），可以安全合批。
_MAX_BATCH_RECORDS = 64


def _utf16_units(text: str) -> List[int]:
    """把字符串拆成 UTF-16 码元序列（代理对拆成两个）。"""
    raw = text.encode("utf-16-le")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def send_unicode_char(ch: str) -> None:
    """用 KEYEVENTF_UNICODE 打出一个字符（绕过 IME）。"""
    for unit in _utf16_units(ch):
        _send([_make_key_input(0, unit, KEYEVENTF_UNICODE),
               _make_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])


def send_unicode_text(text: str) -> None:
    """打出一段文本。

    换行/制表符走真实虚拟键（很多编辑器只认 VK_RETURN / VK_TAB，
    对 U+000A 的 WM_CHAR 不响应），其余字符走 Unicode 注入。
    """
    batch: List[INPUT] = []
    for ch in text:
        if ch == "\n":
            batch.append(_make_key_input(VK_RETURN, 0, 0))
            batch.append(_make_key_input(VK_RETURN, 0, KEYEVENTF_KEYUP))
            continue
        if ch == "\r":
            continue
        if ch == "\t":
            batch.append(_make_key_input(VK_TAB, 0, 0))
            batch.append(_make_key_input(VK_TAB, 0, KEYEVENTF_KEYUP))
            continue
        for unit in _utf16_units(ch):
            batch.append(_make_key_input(0, unit, KEYEVENTF_UNICODE))
            batch.append(_make_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        if len(batch) >= _MAX_BATCH_RECORDS:
            _send(batch)
            batch = []
    _send(batch)


def send_vk(vk: int, down: bool = True) -> None:
    """按下/抬起一个虚拟键。"""
    flags = 0 if down else KEYEVENTF_KEYUP
    _send([_make_key_input(vk, 0, flags)])


VK_V = 0x56
_paste_lock = threading.Lock()


def paste_text(text: str, *, suppress=None, restore: bool = True,
               settle: float = 0.12, from_hook: bool = False) -> bool:
    """把 ``text`` 整段"粘贴"进去：塞剪贴板 → 一次 Ctrl+V → 还原原剪贴板。

    为什么要这条路径（用户提的，2026-10-21）：

        "有的软件他写入会索引其他的，会导致写入不正常。"

    逐字符注入（``send_unicode_text``）在目标程序看来就是"有人一个字一个字敲"，
    于是自动补全、自动缩进、代码索引这些功能会**在中间插一脚**：补一个括号、
    把刚写的行重新缩进、弹出提示框吃掉后续字符…… 整段粘贴只有一个动作，
    这些中间态根本不会发生。

    ``suppress``：写入后标记序号，让监听器跳过我们塞进去的
    内容当成"用户复制"再净化一遍（否则代码里的中文标点会被改写，粘贴出来的
    和预览的不一样）。

    ``settle``：粘贴后等多久再还原剪贴板。目标程序读剪贴板是异步的，
    还原太早可能让它读到旧内容（大段文本尤其明显）。
    ``from_hook``：键盘钩子只尝试立即粘贴，剪贴板由后台定时恢复。

    返回 False 表示粘贴这条路走不通（剪贴板被占、修饰键还按着），
    调用方应当退回 ``send_unicode_text``。
    """
    if not text:
        return True
    if not _paste_lock.acquire(blocking=False):
        return False
    deferred = False
    try:
        if from_hook:
            if modifiers_pressed():
                return False
        elif not wait_modifiers_released():
            return False

        swapped = _clipboard.swap_text(text, retries=1 if from_hook else 6,
                                       on_written=suppress)
        if swapped is None:
            return False
        old, sequence = swapped
        send_needed = _clipboard.sequence_number() == sequence
        try:
            if send_needed:
                send_ctrl_key(VK_V)
        finally:
            if send_needed and restore and old is not None and old != text:
                delay = max(0.0, settle)
                if from_hook:
                    timer = threading.Timer(delay, _restore_clipboard,
                                            args=(old, text, sequence, suppress))
                    timer.daemon = False   # 正常退出时也要把用户的文本还回去
                    try:
                        timer.start()
                    except RuntimeError:
                        pass   # 后台线程启动失败时，仍在当前线程完成恢复
                    else:
                        deferred = True
                if not deferred:
                    time.sleep(delay)
                    _clipboard.set_text(old, expected_sequence=sequence,
                                        expected_text=text, on_written=suppress)
        return send_needed
    finally:
        if not deferred:
            _paste_lock.release()


def _restore_clipboard(old: str, temporary: str, sequence: int, suppress) -> None:
    try:
        _clipboard.set_text(old, expected_sequence=sequence,
                            expected_text=temporary, on_written=suppress)
    finally:
        _paste_lock.release()


# ------------------------------------------------------------------ 输入法守卫键
#
# 为什么需要它：中文输入法（微软拼音默认如此）用「Shift 按下与抬起之间有没有
# 别的键」来区分"单击 Shift 切中英文"和"Shift + 别的键"。我们把 Shift+符号
# 那个键**吞掉**之后，输入法根本看不到它 —— 于是把这次 Shift 当成单击，
# 每敲一个 `:` `<` `>` `?` `"` 就把中英文切走一次（用户实测报障，
# 复现与验证脚本见 tools/ime_shift_probe.py）。
#
# 修法：吞键的同时补一个「无名键」，让输入法看到"Shift 之后还有别的键"，
# 它就放弃切换了。实测：0xE8 / 0xFC / 0xFD / 0xFE / 0xFF 都有效。
#
# 选 0xE8 的理由：Windows 里 0xE8–0xEF 是**未分配**的虚拟键码 ——
# 没有字符映射、没有默认消息处理，目标程序收到 WM_KEYDOWN 只会忽略
# （寄存器/记事本/Tk 实测均无副作用、无可见字符）；
# 而 0xFE(VK_OEM_CLEAR)、0xFC/0xFD 在个别老程序里有语义，风险更大。
VK_IME_GUARD = 0xE8


def _send_unnamed_key(vk: int) -> None:
    """把一个"没名字的键"按下再抬起（无字符、无默认处理）。"""
    _send([_make_key_input(vk, 0, 0),
           _make_key_input(vk, 0, KEYEVENTF_KEYUP)])


def send_ime_guard() -> None:
    """告诉输入法："这次 Shift 是组合键，不是单击"，从而**不要**切中英文。

    ⚠ 必须在 Shift **物理抬起之前**发出去。钩子在 Shift+符号 的 key-down
      里就会调用它，所以时序天然满足。
    """
    _send_unnamed_key(VK_IME_GUARD)


def send_probe_key() -> None:
    """探活用的无害按键 —— 和守卫键**同一个 VK**（0xE8，Windows 未分配，
    目标程序收到只会忽略）。见 ``keyboard_hook.KeyboardHook.probe``。
    """
    _send_unnamed_key(VK_IME_GUARD)


def send_key_press(vk: int, count: int = 1) -> None:
    """连按 count 次某个键。

    ⚠ 每一次都单独调用 SendInput —— 合并成一批会让系统当成自动重复而丢键。
    """
    for _ in range(max(0, count)):
        _send([_make_key_input(vk, 0, 0),
               _make_key_input(vk, 0, KEYEVENTF_KEYUP)])


def send_backspaces(count: int) -> None:
    """发送 count 次退格。"""
    send_key_press(VK_BACK, count)


def replace_typed(backspace_count: int, text: str) -> None:
    """"吃掉刚敲进去的 N 个字符，再写入 text"——快捷输入补全的核心动作。"""
    if backspace_count > 0:
        send_backspaces(backspace_count)
    if text:
        send_unicode_text(text)


# ------------------------------------------------------------------ 选区内取出
#
# 拿"当前选中的文字"没有干净办法：UIA 在 CODESYS / Invtmatic 上读不到正文
# （实测整个窗口只暴露 6 个元素），所以只能**替用户按一次 Ctrl+C**，
# 再从剪贴板读。业务逻辑（备份/还原/等序号变化）在 services/selection.py，
# 这里只负责把那四个按键动作发出去。

VK_C = 0x43

#: 修饰键：**通用码 + 左右具体码都查**。
#:
#: 只查通用码（0x11/0x12/0x10）不够。实测报障："打断 AI 之后每次触发都提示
#: 按键没松开" —— 钩子那边是按左右码（0xA2 左 Ctrl、0xA4 左 Alt）对账的，
#: 它说"物理上没按"、并把这些键清掉了；而我们这边查通用码却一直报"按着"。
#: 两套状态在我们自己 SendInput 注入过 Ctrl（Ctrl+C 抓选区）之后可能不同步。
#: 所以两边都看，任一为按下就算按着 —— 宁可多等一会儿，也不能误判成"松开了"
#: 就发 Ctrl+C（那样会变成 Ctrl+Alt+C，什么都拿不到）。
_MODIFIER_VKS = (
    0x11, 0xA2, 0xA3,   # Ctrl：通用 / 左 / 右
    0x12, 0xA4, 0xA5,   # Alt ：通用 / 左 / 右
    0x10, 0xA0, 0xA1,   # Shift：通用 / 左 / 右
    0x5B, 0x5C,         # Win ：左 / 右
)


def modifiers_pressed() -> bool:
    """物理上还有没有修饰键按着。

    ``GetAsyncKeyState`` 看的是**真实**键状态（不是我们的钩子事件流），
    所以它能回答一个钩子答不了的问题：热键刚触发的那一瞬间，
    用户手上的 Ctrl / Alt 到底松了没有。
    """
    try:
        return any(_w.user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS)
    except Exception:      # noqa: BLE001
        return False


#: 可以"补一对 down+up"来清残留的键：**只有 Ctrl / Shift**。
#:
#: ⚠ 绝不能包含 Alt 和 Win —— 它们"按下"本身就是有意义的动作，副作用远大于
#: "残留"本身：
#:
#: · **Alt**：单独按一下 = 激活窗口菜单栏（标题下面那排）。之后注入的
#:   Shift+Up / Down 会去**移动菜单里的选中项**，用户随后敲的回车就成了
#:   "点击那一项" —— 实测报障："我在其他软件中，怎么会选择到上面的属性，
#:   然后进行了点击，导致软件被关闭"。所以 Alt 一个键都不许按（用户要求：
#:   "不要动 alt 按键"）。
#: · **Win**：单独按一下 = 弹开始菜单（v1.6.2 报障）。
_RELEASABLE_VKS = (
    0x11,   # VK_CONTROL
    0x10,   # VK_SHIFT
)

#: **只抬不按**的键：Alt / Win。
#:
#: 真卡在"按下"状态时，一次 key-up 就能清掉；没卡着时系统直接忽略这下的
#: 效果（不会激活菜单、不会弹开始菜单）。宁可残留清不掉，也不能凭空按一下。
_KEYUP_ONLY_VKS = (
    0x12, 0xA4, 0xA5,   # Alt：通用 / 左 / 右
    0x5B, 0x5C,         # Win：左 / 右
)


def release_modifiers() -> None:
    """清掉"残留在按下"的修饰键状态。

    Ctrl / Shift：补一对 **down+up**，把人造的"按下"再抬起来。
    只发 key-up 时，如果系统状态认为它已经是抬起的，这一下会被丢掉；
    先按一下再松开才真能把它带回"抬起"。这两个键单独按一下没有任何副作用。

    Alt / Win：**只发 key-up，一个 down 都不许发**。它们按一下就会做事情 ——
    Alt 打开窗口菜单栏、Win 弹开始菜单，那比"状态残留"严重得多。

    ⚠ 只用来清"物理上没按、状态却说按着"的残留。用户真按着的时候清不掉
    （物理按键还在，``GetAsyncKeyState`` 照样报按下），所以不会把键抢走。
    """
    for vk in _RELEASABLE_VKS:
        _send([_make_key_input(vk, 0, 0)])
        _send([_make_key_input(vk, 0, KEYEVENTF_KEYUP)])
    for vk in _KEYUP_ONLY_VKS:
        _send([_make_key_input(vk, 0, KEYEVENTF_KEYUP)])


def wait_modifiers_released(timeout: float = 1.2, poll: float = 0.008,
                            heal: bool = True) -> bool:
    """等用户松开修饰键。返回 False 表示等超时了他还按着。

    为什么必须有这一步：全局热键（``WM_HOTKEY``）是在**按下**那一刻触发的，
    此时 Ctrl 和 Alt 还实实在在按着。这时候直接 ``SendInput`` 发 Ctrl+C，
    系统看到的是 **Ctrl+Alt+C** —— 记事本、CODESYS 都没有这个命令，
    剪贴板纹丝不动，最后表现成"明明选中了却说没拿到文本"。

    松手之后还要再等 20ms：目标程序处理 key-up 也需要一点时间，
    抢在它前面发，它那边可能还认为 Alt 是按下的。

    超时后的 ``heal``（默认开）：先补一遍 down+up 清残留，再给一小段时间。
    不加这一步时，只要有一次状态残留，之后**每次**触发都会立刻失败 ——
    用户看到的就是"打断一次之后，再也用不了，一直提示按键没松开"。
    真按着的情况清不掉（物理键还在），所以不会误放行。
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not modifiers_pressed():
            time.sleep(0.02)
            return True
        time.sleep(max(0.002, poll))

    if heal:
        release_modifiers()
        for _ in range(25):                 # 再给约 0.2 秒
            if not modifiers_pressed():
                time.sleep(0.02)
                return True
            time.sleep(max(0.002, poll))
    return not modifiers_pressed()


def send_ctrl_key(vk: int) -> None:
    """按一次 Ctrl + vk。

    ⚠ 四步**必须分四次 SendInput**：合批时 Windows 会把同类按键当自动重复
    合并掉（见本文件开头那条实测结论），Ctrl 会变成"没按下"。

    ⚠ 抬起 Ctrl 放在 finally 里：中途任何一步出错都得抬 —— 否则 Ctrl 卡在
    "按下"状态，之后每次触发都会因为 ``wait_modifiers_released`` 判定
    "还没松开"而直接放弃（用户报障："打断之后一直说按键没松开"）。
    """
    _send([_make_key_input(VK_CONTROL, 0, 0)])
    try:
        _send([_make_key_input(vk, 0, 0)])
        _send([_make_key_input(vk, 0, KEYEVENTF_KEYUP)])
    finally:
        _release_modifier(VK_CONTROL)


def copy_selection() -> None:
    """给当前焦点程序发一个 Ctrl+C。"""
    send_ctrl_key(VK_C)


def select_context(count: int, *, below: bool) -> bool:
    """用 Shift 和方向键扩展选区，返回按键是否全部送出。"""
    return _shift_directions(VK_DOWN if below else VK_UP, count)


def move_context_endpoint(count: int, *, left: bool) -> bool:
    """保持 Shift，移动临时选区的活动端。"""
    return _shift_directions(VK_LEFT if left else VK_RIGHT, count)


def collapse_selection(*, to_start: bool) -> bool:
    return not modifiers_pressed() and _send_direction(VK_LEFT if to_start else VK_RIGHT)


def _shift_directions(key: int, count: int) -> bool:
    if count <= 0 or modifiers_pressed():
        return False
    if _send([_make_key_input(VK_SHIFT)]) != 1:
        return False
    try:
        for _ in range(count):
            if not _send_direction(key):
                return False
        return True
    finally:
        _release_modifier(VK_SHIFT)


def _send_direction(key: int) -> bool:
    # 与原有上下文抓取时序一致，每次按下和抬起分别提交。
    if _send([_make_key_input(key)]) != 1:
        return False
    up = _make_key_input(key, flags=KEYEVENTF_KEYUP)
    return _send([up]) == 1 or _send([up]) == 1
