"""修饰键「等松开」的逻辑测试（不联网、不真按键）。

背景（用户报障）："打断 AI 之后，再触发就一直说按键没松开"。
根因是 ``wait_modifiers_released`` 超时就直接放弃、不重试 —— 只要有一次
状态残留（我们自己注入过 Ctrl+C，或漏收一次 key-up），之后每次触发都会
立刻失败，看起来像"功能坏了"。

这里锁住三件事：
  1. 物理上真按着 → 仍然返回 False（不能误放行，否则 Ctrl+C 变成 Ctrl+Alt+C）；
  2. 只是状态残留 → 清一下之后能通过；
  3. 清残留的动作确实发过。
"""

from __future__ import annotations

import unittest
from unittest import mock

from codepoint.platform_win import input_simulator as ins


class TestWaitModifiersReleased(unittest.TestCase):
    def test_returns_true_when_already_released(self):
        with mock.patch.object(ins, "modifiers_pressed", return_value=False):
            self.assertTrue(ins.wait_modifiers_released(0.2))

    def test_heal_clears_residue_and_succeeds(self):
        # 一开始"按着"（残留），清一遍之后就"松开"了 —— 这正是报障的场景
        state = {"pressed": True}

        with mock.patch.object(ins, "modifiers_pressed",
                               side_effect=lambda: state["pressed"]):
            with mock.patch.object(ins, "release_modifiers",
                                   side_effect=lambda: state.__setitem__("pressed", False)) as heal:
                ok = ins.wait_modifiers_released(0.05, poll=0.001)
        self.assertTrue(ok)
        self.assertEqual(heal.call_count, 1)

    def test_real_press_still_fails(self):
        # 物理上一直按着：自愈清不掉（真实按键还在），必须如实返回 False
        with mock.patch.object(ins, "modifiers_pressed", return_value=True):
            with mock.patch.object(ins, "release_modifiers") as heal:
                ok = ins.wait_modifiers_released(0.05, poll=0.001)
        self.assertFalse(ok)
        self.assertEqual(heal.call_count, 1)

    def test_heal_can_be_disabled(self):
        with mock.patch.object(ins, "modifiers_pressed", return_value=True):
            with mock.patch.object(ins, "release_modifiers") as heal:
                ok = ins.wait_modifiers_released(0.05, poll=0.001, heal=False)
        self.assertFalse(ok)
        heal.assert_not_called()


class TestModifierVks(unittest.TestCase):
    def test_covers_generic_and_side_specific_codes(self):
        """通用码和左右具体码都要查 —— 两套状态在注入之后可能不同步。"""
        for vk in (0x11, 0xA2, 0xA3,    # Ctrl
                   0x12, 0xA4, 0xA5,    # Alt
                   0x10, 0xA0, 0xA1,    # Shift
                   0x5B, 0x5C):         # Win
            self.assertIn(vk, ins._MODIFIER_VKS)


class TestReleaseModifiers(unittest.TestCase):
    """清残留时**绝不能"按一下 Win"** —— 按下 Win 会弹开始菜单。

    用户报障："ctrl+g 补全时怎么还触发 win 按钮"。就是这里：原来直接遍历
    ``_MODIFIER_VKS`` 给每个键补 down+up，里面含 0x5B/0x5C。
    """

    def _capture(self):
        sent = []

        def fake_send(items):
            sent.extend(items)
            return 1

        with mock.patch.object(ins, "_send", side_effect=fake_send):
            ins.release_modifiers()
        return [(getattr(e.ki, "wVk", 0),
                 bool(getattr(e.ki, "dwFlags", 0) & ins.KEYEVENTF_KEYUP))
                for e in sent]

    def test_never_presses_win(self):
        win_down = [v for vk, up in self._capture() if vk in (0x5B, 0x5C) and not up]
        self.assertEqual(win_down, [], "Win 被按下了，会弹开始菜单")

    def test_win_is_still_released(self):
        # 只抬不按：真卡着的时候还得能清掉
        wins = [(vk, up) for vk, up in self._capture() if vk in (0x5B, 0x5C)]
        self.assertTrue(wins)
        self.assertTrue(all(up for _vk, up in wins))

    def test_ctrl_shift_get_down_then_up(self):
        got = self._capture()
        for vk in (0x11, 0x10):
            self.assertIn((vk, False), got, "缺 %s 的按下" % hex(vk))
            self.assertIn((vk, True), got, "缺 %s 的抬起" % hex(vk))

    def test_alt_is_never_pressed(self):
        """**一个 Alt 的按下都不许有** —— 单独按一下 Alt 就是"打开菜单栏"。

        用户报障（v1.6.3）："我在其他软件中，怎么会选择到上面的属性，然后
        进行了点击，导致软件被关闭。" 就是这里补的那对 Alt down+up：菜单栏
        被激活后，注入的 Shift+Up/Down 在菜单里挪选中项，用户敲的回车就成了
        "点击那一项"。
        """
        alt_down = [(vk, up) for vk, up in self._capture()
                    if vk in (0x12, 0xA4, 0xA5) and not up]
        self.assertEqual(alt_down, [], "Alt 被按下了，会打开窗口菜单栏")

    def test_alt_is_still_released(self):
        # 只抬不按：真卡着的时候还得能清掉
        alts = [(vk, up) for vk, up in self._capture() if vk in (0x12, 0xA4, 0xA5)]
        self.assertTrue(alts, "连 key-up 都不发，Alt 残留就清不掉了")
        self.assertTrue(all(up for _vk, up in alts))


class TestReleaseOneModifierNeverPressesAlt(unittest.TestCase):
    """``_release_modifier`` 被别处直接调用时，也不许"补按 Alt"。"""

    def test_alt_keyup_retries_but_never_presses(self):
        sent = []

        def fake_send(items):
            sent.extend(items)
            return 0            # 永远"没送进去"，逼出补按那条分支

        with mock.patch.object(ins, "_send", side_effect=fake_send):
            with mock.patch.object(ins.time, "sleep"):
                ins._release_modifier(0x12)
        flags = [(getattr(e.ki, "dwFlags", 0) & ins.KEYEVENTF_KEYUP) for e in sent]
        self.assertTrue(all(flags), "Alt 出现了按下事件：会打开菜单栏")
        self.assertGreaterEqual(len(sent), 1, "失败至少要重试一次 key-up")


class TestSendCtrlKeyReleasesOnError(unittest.TestCase):
    def test_ctrl_is_released_even_if_key_injection_raises(self):
        sent = []

        def fake_send(events):
            sent.extend(events)
            if len(sent) >= 2:          # 第二次（按 C）之后炸掉
                raise RuntimeError("SendInput 失败")

        with mock.patch.object(ins, "_send", side_effect=fake_send):
            with self.assertRaises(RuntimeError):
                ins.send_ctrl_key(ins.VK_C)
        # 最后一次注入必须是 Ctrl 的 key-up（KEYEVENTF_KEYUP）
        self.assertTrue(sent)
        last = sent[-1]
        self.assertEqual(getattr(last.ki, "wVk", None), 0x11)
        self.assertTrue(getattr(last.ki, "dwFlags", 0) & ins.KEYEVENTF_KEYUP)


if __name__ == "__main__":
    unittest.main()
