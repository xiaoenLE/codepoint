"""「写入方式」测试：整段粘贴 vs 逐字符。

背景（用户提的，2026-10-21）：
    "有的软件他写入会索引其他的，会导致写入不正常。"

逐字符写入在目标程序看来就是"有人一个字一个字敲"，自动补全/自动缩进会
在中间插一脚。整段粘贴只有一个动作，这些中间态根本不会发生。
"""

from __future__ import annotations

import unittest
from unittest import mock

from codepoint.core.models import (
    INSERT_LABELS, INSERT_MODES, INSERT_PASTE, INSERT_TYPE, Settings,
)
from codepoint.platform_win import input_simulator as ins


class TestPasteText(unittest.TestCase):
    def _run(self, *, old="老内容", text="新内容", set_ok=True, mods_released=True):
        calls = []

        def fake_set(value):
            calls.append(("set", value))
            return set_ok

        with mock.patch.object(ins, "wait_modifiers_released",
                               return_value=mods_released), \
                mock.patch.object(ins._clipboard, "get_text", return_value=old), \
                mock.patch.object(ins._clipboard, "set_text", side_effect=fake_set), \
                mock.patch.object(ins, "send_ctrl_key",
                                  side_effect=lambda vk: calls.append(("key", vk))), \
                mock.patch.object(ins.time, "sleep"):
            ok = ins.paste_text(text, suppress=lambda: calls.append(("suppress", None)))
        return ok, calls

    def test_pastes_via_ctrl_v(self):
        ok, calls = self._run()
        self.assertTrue(ok)
        self.assertIn(("key", ins.VK_V), calls)

    def test_restores_previous_clipboard(self):
        ok, calls = self._run(old="原来是这个")
        self.assertTrue(ok)
        # 最后一次 set 应该是把老内容放回去
        sets = [v for kind, v in calls if kind == "set"]
        self.assertEqual(sets[0], "新内容")
        self.assertEqual(sets[-1], "原来是这个")

    def test_no_restore_when_clipboard_was_empty(self):
        ok, calls = self._run(old=None)
        self.assertTrue(ok)
        sets = [v for kind, v in calls if kind == "set"]
        self.assertEqual(sets, ["新内容"])

    def test_suppress_runs_before_touching_clipboard(self):
        """剪贴板净化器必须先被喊停，否则代码里的中文标点会被改掉。"""
        _ok, calls = self._run()
        kinds = [kind for kind, _ in calls]
        self.assertLess(kinds.index("suppress"), kinds.index("set"),
                        "要在改剪贴板之前 suppress")

    def test_fails_when_clipboard_is_occupied(self):
        ok, calls = self._run(set_ok=False)
        self.assertFalse(ok)
        self.assertNotIn(("key", ins.VK_V), calls, "剪贴板没写进去就不该按 Ctrl+V")

    def test_fails_when_modifiers_still_held(self):
        """Ctrl/Alt 还按着时按 Ctrl+V 会变成 Ctrl+Alt+V，谁都认不出来。"""
        ok, calls = self._run(mods_released=False)
        self.assertFalse(ok)
        self.assertNotIn(("key", ins.VK_V), calls)


class TestInjectorAdapter(unittest.TestCase):
    def _adapter(self, mode, paste_ok=True):
        from codepoint.app import _InjectorAdapter

        calls = []
        adapter = _InjectorAdapter(
            mode_getter=lambda: mode,
            suppress=lambda: calls.append(("suppress", None)))
        patchers = [
            mock.patch.object(ins, "paste_text",
                              side_effect=lambda text, **kw: calls.append(("paste", text)) or paste_ok),
            mock.patch.object(ins, "send_unicode_text",
                              side_effect=lambda text: calls.append(("type", text))),
            mock.patch.object(ins, "wait_modifiers_released", return_value=True),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return adapter, calls

    LONG = "IF x THEN\n    y := 1;\nEND_IF;"      # 多行 —— 该粘贴
    SHORT = "x := 1;"                              # 7 字单行 —— 该逐字符

    def test_paste_mode_pastes_multi_line_blocks(self):
        adapter, calls = self._adapter(INSERT_PASTE)
        adapter.send_block(self.LONG)
        self.assertEqual(calls, [("paste", self.LONG)])

    def test_short_single_line_stays_keystrokes(self):
        """快捷输入 `:` → `:=` 这种短的仍逐字符：为两个字绕剪贴板太重。"""
        adapter, calls = self._adapter(INSERT_PASTE)
        adapter.send_block(self.SHORT)
        self.assertEqual(calls, [("type", self.SHORT)])

    def test_always_paste_forces_paste_even_for_short_text(self):
        """AI 结果必须粘贴：哪怕只补一行，逐字符照样会被联想打断。"""
        adapter, calls = self._adapter(INSERT_PASTE)
        adapter.send_block(self.SHORT, always_paste=True)
        self.assertEqual(calls, [("paste", self.SHORT)])

    def test_type_mode_keeps_keystroke_writing(self):
        adapter, calls = self._adapter(INSERT_TYPE)
        adapter.send_block(self.LONG)
        self.assertEqual(calls, [("type", self.LONG)])

    def test_paste_failure_falls_back_to_typing(self):
        adapter, calls = self._adapter(INSERT_PASTE, paste_ok=False)
        adapter.send_block(self.LONG)
        self.assertEqual(calls, [("paste", self.LONG), ("type", self.LONG)],
                         "粘贴走不通必须退回逐字符，不能什么都不写")

    def test_send_text_is_always_keystrokes(self):
        """标点替换永远走逐字符 —— 它本来就是"把刚敲的键换个写法"。"""
        adapter, calls = self._adapter(INSERT_PASTE)
        adapter.send_text(":")
        self.assertEqual(calls, [("type", ":")])


class TestWhoUsesPaste(unittest.TestCase):
    """用户定的边界：粘贴给 AI 和快捷输入，标点符号保持原样。"""

    def _pipeline(self):
        from .fakes import key
        from .test_pipeline import make_pipeline

        p, inj, _notifier, _clock = make_pipeline()
        return p, inj, key

    def test_punctuation_stays_keystrokes(self):
        p, inj, key = self._pipeline()
        self.assertTrue(p.handle(key(0xBC, ",")), "标点键应被吞掉重发")
        self.assertEqual(inj.calls, [("text", ",")],
                         "标点替换必须逐字符，不能走粘贴（send_block）")

    def test_quick_input_goes_through_send_block(self):
        p, inj, key = self._pipeline()
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        p.handle(key(0x09, "\t"))
        kinds = [kind for kind, _ in inj.calls]
        self.assertIn("block", kinds, "快捷输入的补全内容要走 send_block（多行会粘贴）")

    def test_ai_accept_uses_always_paste(self):
        p, inj, key = self._pipeline()
        p._notifier = mock.MagicMock()      # offer() 要弹预览卡，测试里不需要真弹
        p.offer("x := 1;", backspaces=0, label="AI", hwnd=101, timeout=10)
        p.handle(key(0x09, "\t"))
        self.assertIn("block", [kind for kind, _ in inj.calls])
        # AI 结果哪怕一行也要粘贴 —— 这个开关必须是 True
        self.assertTrue(inj.last_always_paste)


class TestAltNeverSentDuringPaste(unittest.TestCase):
    def test_paste_never_touches_alt(self):
        """粘贴这条路也不能动 Alt —— 单按一下 Alt 就是激活窗口菜单栏。"""
        sent = []

        def fake_send(items):
            sent.extend(items)
            return 1

        with mock.patch.object(ins, "_send", side_effect=fake_send), \
                mock.patch.object(ins, "wait_modifiers_released", return_value=True), \
                mock.patch.object(ins._clipboard, "get_text", return_value=None), \
                mock.patch.object(ins._clipboard, "set_text", return_value=True), \
                mock.patch.object(ins.time, "sleep"):
            ins.paste_text("x := 1;")

        pressed = [getattr(e.ki, "wVk", 0) for e in sent
                   if not (getattr(e.ki, "dwFlags", 0) & ins.KEYEVENTF_KEYUP)]
        self.assertEqual(pressed.count(ins.VK_V), 1, "要按 Ctrl+V 才算粘贴")
        bad = [hex(v) for v in pressed if v in (0x12, 0xA4, 0xA5, 0x5B, 0x5C)]
        self.assertEqual(bad, [], "粘贴时按了 %s，会打开菜单/开始菜单" % bad)


class TestInsertModeSetting(unittest.TestCase):
    def test_default_is_paste(self):
        self.assertEqual(Settings().ai_insert_mode, INSERT_PASTE)

    def test_round_trip(self):
        s = Settings()
        s.ai_insert_mode = INSERT_TYPE
        back = Settings.from_dict(s.to_dict()).normalize()
        self.assertEqual(back.ai_insert_mode, INSERT_TYPE)

    def test_unknown_value_falls_back_to_paste(self):
        s = Settings.from_dict({"ai_insert_mode": "乱填"}).normalize()
        self.assertEqual(s.ai_insert_mode, INSERT_PASTE)

    def test_old_config_without_the_key_still_works(self):
        d = Settings().to_dict()
        d.pop("ai_insert_mode", None)
        self.assertEqual(Settings.from_dict(d).normalize().ai_insert_mode,
                         INSERT_PASTE)

    def test_every_mode_has_a_label(self):
        for mode in INSERT_MODES:
            self.assertIn(mode, INSERT_LABELS)


if __name__ == "__main__":
    unittest.main()
