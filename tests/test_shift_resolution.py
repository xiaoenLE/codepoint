"""Shift 组合符号的解析测试（`<` `>` `:` `?` `"` …）。

**背景**：钩子线程里用 ``GetKeyboardState`` 解析字符是不可靠的 —— 它返回
*调用线程* 的键盘状态，在 LL 钩子回调里经常拿不到刚按下的 Shift。
后果是 Shift+, 被解析成 `,`、Shift+; 解析成 `;`，再被标点替换吞掉重新注入，
于是 **所有需要 Shift 的符号都只能打出一个**（用户实测报障）。

修复：钩子自己维护键盘状态（事件流跟踪为主 + GetAsyncKeyState 只做补漏），
字符解析用这份状态。这里的测试**精确模拟钩子看到的事件流**，
不用真键盘就能把这条链路钉死。
"""

from __future__ import annotations

import unittest

from codepoint.platform_win import keyboard_hook as kh
from codepoint.platform_win.keylayout import vk_to_char

# 常用键
VK_COMMA = 0xBC          # , <
VK_PERIOD = 0xBE         # . >
VK_OEM_1 = 0xBA          # ; :
VK_OEM_7 = 0xDE          # ' "
VK_A = 0x41
VK_CAPITAL = 0x14
VK_LSHIFT = 0xA0


def make_hook() -> kh.KeyboardHook:
    """造一个不安装的钩子对象（只为用它的状态跟踪）。"""
    return kh.KeyboardHook(None, lambda event: False)


def resolve(hook, vk: int, scan: int = 0) -> str:
    """按钩子当前维护的状态解析字符（与回调里的调用完全一致）。"""
    return vk_to_char(vk, scan, key_state=hook._state_bytes())


class TestShiftedPunctuation(unittest.TestCase):
    """每一对"同键双字符"都必须两个都能解析出来。"""

    PAIRS = [
        (VK_COMMA, ",", "<"),
        (VK_PERIOD, ".", ">"),
        (VK_OEM_1, ";", ":"),
        (VK_OEM_7, "'", '"'),
    ]

    def test_unshifted_char_resolves_without_shift(self):
        for vk, plain, _shifted in self.PAIRS:
            hook = make_hook()
            self.assertEqual(resolve(hook, vk), plain, f"{plain!r} 解析失败")

    def test_shifted_char_resolves_with_shift(self):
        """这是本次 bug 的核心断言：按住 Shift 时必须解析出上档字符。"""
        for vk, plain, shifted in self.PAIRS:
            hook = make_hook()
            hook._update_state(VK_LSHIFT, True)          # 钩子看到 Shift 按下
            self.assertEqual(resolve(hook, vk), shifted,
                             f"Shift+{plain!r} 没能解析成 {shifted!r}")

    def test_shift_release_restores_plain_char(self):
        hook = make_hook()
        hook._update_state(VK_LSHIFT, True)
        hook._update_state(VK_LSHIFT, False)
        self.assertEqual(resolve(hook, VK_COMMA), ",")

    def test_both_shift_keys_are_tracked(self):
        """左 Shift 和右 Shift 都要认（有人真的用右边的）。"""
        for vk in (VK_LSHIFT, 0xA1):                     # 0xA1 = VK_RSHIFT
            hook = make_hook()
            hook._update_state(vk, True)
            self.assertEqual(resolve(hook, VK_COMMA), "<")

    def test_modifier_snapshot_agrees_with_resolution(self):
        """Modifiers.shift（业务层判断用）和字符解析必须来自同一份状态。"""
        hook = make_hook()
        hook._update_state(VK_LSHIFT, True)
        self.assertTrue(hook._snapshot_modifiers().shift)
        self.assertEqual(resolve(hook, VK_OEM_1), ":")


class TestCapsLockIsolation(unittest.TestCase):
    """CapsLock 只影响字母大小写，**绝不能**把标点键折算成 Shift
    （曾经因此 `;` 被当成 `:`，修过一次的 bug，这里钉死）。"""

    def test_capslock_uppercases_letters(self):
        hook = make_hook()
        hook._update_state(VK_CAPITAL, True)             # 钩子看到 CapsLock 按下
        self.assertEqual(resolve(hook, VK_A).lower(), "a")
        self.assertIn(resolve(hook, VK_A), ("a", "A"))   # 具体大小写取决于初始切换位

    def test_capslock_does_not_affect_punctuation(self):
        hook = make_hook()
        hook._update_state(VK_CAPITAL, True)
        self.assertEqual(resolve(hook, VK_OEM_1), ";")   # 不是 ':'
        self.assertEqual(resolve(hook, VK_COMMA), ",")   # 不是 '<'

    def test_capslock_does_not_set_shift_modifier(self):
        hook = make_hook()
        hook._update_state(VK_CAPITAL, True)
        self.assertFalse(hook._snapshot_modifiers().shift,
                         "CapsLock 折进了 Shift —— 标点键会被误判")


class TestAsyncCrossCheck(unittest.TestCase):
    """GetAsyncKeyState 交叉校验：流式跟踪漏了事件时能自我修复。"""

    class _FakeUser32:
        """替身：只实现钩子同步用到的 GetAsyncKeyState。"""

        def __init__(self, down: set) -> None:
            self.down = set(down)

        def GetAsyncKeyState(self, vk):
            return 0x8000 if vk in self.down else 0

    def test_missed_shift_down_is_healed(self):
        """流式跟踪没看到 Shift 按下（事件被系统跳过）→ 异步校验补上。"""
        import codepoint.platform_win.keyboard_hook as kh_mod

        hook = make_hook()
        self.assertEqual(resolve(hook, VK_COMMA), ",")

        fake = self._FakeUser32({VK_LSHIFT})
        original = kh_mod.user32
        kh_mod.user32 = fake
        try:
            hook._sync_modifiers_from_async()
        finally:
            kh_mod.user32 = original

        self.assertEqual(resolve(hook, VK_COMMA), "<", "漏掉的 Shift-down 没被补上")
        self.assertTrue(hook._snapshot_modifiers().shift)

    def test_tracked_shift_survives_physical_release(self):
        """回调滞后场景：流式说"按着"、物理已松开 → **保留**按下。

        这条刻意和"补漏"那条相反，而且不能反过来写。
        钩子回调滞后于物理按键是真实存在的（回调排队、系统忙、钩子刚装上）：
        用户 Shift+, 之后手已经松开了，回调才跑起来。此时若拿物理状态去
        **覆盖**流式跟踪，Shift 就被抹掉 → `Shift+,` 解析成 `,` → 命中标点
        规则被吞掉再注入 `,` → 用户永远打不出 `<`。
        代价权衡：修饰键的 key-up 我们从不通吃，所以"流式卡在按下"极少发生；
        而"抹掉 Shift"直接毁掉全部上档符号。所以只补不减。
        """
        import codepoint.platform_win.keyboard_hook as kh_mod

        hook = make_hook()
        hook._update_state(VK_LSHIFT, True)

        fake = self._FakeUser32(set())                   # 物理上没按任何键
        original = kh_mod.user32
        kh_mod.user32 = fake
        try:
            hook._sync_modifiers_from_async()
        finally:
            kh_mod.user32 = original

        self.assertEqual(resolve(hook, VK_COMMA), "<",
                         "回调滞后时 Shift 不该被物理状态抹掉")
        self.assertTrue(hook._snapshot_modifiers().shift)

    def test_stuck_shift_is_cleared_by_keyup(self):
        """流式跟踪的"卡住"由 key-up 事件自己清掉（修饰键从不被吞）。"""
        hook = make_hook()
        hook._update_state(VK_LSHIFT, True)
        self.assertTrue(hook._snapshot_modifiers().shift)
        hook._update_state(VK_LSHIFT, False)
        self.assertFalse(hook._snapshot_modifiers().shift)
        self.assertEqual(resolve(hook, VK_COMMA), ",")


if __name__ == "__main__":
    unittest.main()
