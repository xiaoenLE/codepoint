"""输入法守卫键（Shift 组合吞键 → 不能把输入法切到英文）的回归测试。

根因回顾（用户实测报障）：
    "当 shift 用来切换中英文时，每次中文输入其他需要 shift 加其他按键时，
     他会触发切换语言。"

中文输入法（微软拼音默认）用「Shift 按下与抬起之间有没有别的键」来区分
"单击 Shift 切中英文" 和 "Shift + 别的键"。标点替换把 Shift+符号 那个键**吞掉**了，
输入法看不到它 → 把这次 Shift 当成单击 → 中英文被切走。
（真实的复现/验证是 `tools/ime_shift_probe.py`，本文件覆盖的是"我们有没有补守卫键"。）

修法：凡是**吞掉了一个按着 Shift 的键**，就补一个无名键（0xE8）。
"""

from __future__ import annotations

import unittest

from codepoint.core.config import build_default_settings
from codepoint.core.events import ImeSnapshot, WindowInfo
from codepoint.core.models import AppItem, DETECT_CHAR, DETECT_WORD, PunctuationRule, QuickAction
from codepoint.platform_win import input_simulator
from codepoint.services.pipeline import KeyboardPipeline

from .fakes import (
    FakeClock,
    FakeImeProbe,
    FakeInjector,
    FakeWindowProbe,
    RecordingNotifier,
    VK_OEM_1,
    VK_TAB,
    key,
)

IME_CJK = ImeSnapshot(available=True, open=True, native=True)


def make_pipeline(*, whitelisted: bool = True, injector=None, quick_actions=None):
    window = WindowInfo(
        hwnd=101, pid=200,
        process_name="codesys.exe" if whitelisted else "notepad.exe",
        window_title="CODESYS",
        thread_id=200,
    )
    settings = build_default_settings()
    settings.app_items = [AppItem("codesys.exe")]
    settings.punctuation_rules = [
        PunctuationRule("，", ",", True),
        PunctuationRule("：", ":", True),      # ':' 走标点替换路径
    ]
    # 默认不带快捷输入 —— 这里的断言只关心"标点替换"这条链路
    settings.quick_actions = list(quick_actions or [])
    inj = injector or FakeInjector()
    notifier = RecordingNotifier()
    clock = FakeClock()
    pipeline = KeyboardPipeline(
        window_probe=FakeWindowProbe(window),
        ime_probe=FakeImeProbe(IME_CJK),
        injector=inj,
        notifier=notifier,
        clock=clock.now,
    )
    pipeline.apply_settings(settings)
    return pipeline, inj, notifier


class TestImeGuard(unittest.TestCase):
    def test_shifted_punctuation_sends_guard_before_char(self):
        """Shift+; （打冒号）被吞掉 → 必须先补守卫键，再注入半角字符。

        顺序有意义：守卫键要在 Shift 物理抬起之前落到输入法那边，
        而同一批注入里先发的那条一定先到。
        """
        p, inj, _ = make_pipeline()
        swallowed = p.handle(key(VK_OEM_1, ":", shift=True))
        self.assertTrue(swallowed)
        self.assertEqual(inj.calls, [("guard", None), ("text", ":")],
                         "吞掉 Shift 组合键后必须先补守卫键（否则输入法切中英文）")
        self.assertEqual(inj.guards, 1)

    def test_plain_punctuation_sends_no_guard(self):
        """没按 Shift 的标点被吞掉时**不该**多发按键 —— 少一次注入少一次出错机会。"""
        p, inj, _ = make_pipeline()
        p.handle(key(0xBC, ","))
        self.assertEqual(inj.guards, 0)
        self.assertEqual(inj.calls, [("text", ",")])

    def test_shifted_letter_sends_no_guard(self):
        """Shift+A（大写字母）不吞键 → 不需要守卫键。"""
        p, inj, _ = make_pipeline()
        swallowed = p.handle(key(0x41, "A", shift=True))
        self.assertFalse(swallowed)
        self.assertEqual(inj.calls, [])

    def test_ctrl_shift_combo_is_never_swallowed(self):
        """带 Ctrl/Alt/Win 的组合是快捷键，标点替换绝不吞、也就不必补守卫键。"""
        p, inj, _ = make_pipeline()
        swallowed = p.handle(key(VK_OEM_1, ":", shift=True, ctrl=True))
        self.assertFalse(swallowed)
        self.assertEqual(inj.calls, [])

    def test_out_of_scope_shifted_key_sends_no_guard(self):
        """不在生效范围 → 什么都不做（连守卫键也不发）。"""
        p, inj, _ = make_pipeline(whitelisted=False)
        swallowed = p.handle(key(VK_OEM_1, ":", shift=True))
        self.assertFalse(swallowed)
        self.assertEqual(inj.calls, [])

    def test_shift_tab_completion_sends_guard(self):
        """Shift+Tab 被当成"确认补全"吞掉时，同样要补守卫键。"""
        p, inj, _ = make_pipeline(
            quick_actions=[QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True)]
        )
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        self.assertIsNotNone(p.quick_actions.pending)

        swallowed = p.handle(key(VK_TAB, "\t", shift=True))
        self.assertTrue(swallowed)
        self.assertEqual(inj.calls[0], ("guard", None))
        self.assertIn(("back", 2), inj.calls)

    def test_plain_tab_completion_sends_no_guard(self):
        p, inj, _ = make_pipeline(
            quick_actions=[QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True)]
        )
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        p.handle(key(VK_TAB, "\t"))
        self.assertEqual(inj.guards, 0)

    def test_char_mode_quick_action_with_shift_sends_guard(self):
        """单字符补全（':' → ':='）也会吞 Shift+; → 同样要补守卫键。"""
        p, inj, _ = make_pipeline(
            quick_actions=[QuickAction(":", DETECT_CHAR, ":=", True)]
        )
        settings = build_default_settings()
        settings.punctuation_rules = [PunctuationRule("，", ",", True)]   # 不含 ':'
        settings.quick_actions = [QuickAction(":", DETECT_CHAR, ":=", True)]
        settings.app_items = [AppItem("codesys.exe")]
        p.apply_settings(settings)

        swallowed = p.handle(key(VK_OEM_1, ":", shift=True))
        self.assertTrue(swallowed)
        self.assertEqual(inj.calls, [("guard", None), ("block", ":=")])

    def test_guard_failure_does_not_unswallow(self):
        """守卫键发不出去时，绝不能因此把本该吞掉的键放行。

        （`handle()` 的兜底是"异常就放行"，所以守卫单独 try —— 否则一次
           SendInput 失败会让全角 `：` 直接漏进编辑器。）
        """

        class _Boom(FakeInjector):
            def send_ime_guard(self):
                raise RuntimeError("SendInput 失败")

        p, inj, _ = make_pipeline(injector=_Boom())
        swallowed = p.handle(key(VK_OEM_1, ":", shift=True))
        self.assertTrue(swallowed, "守卫键失败不该改变「吞键」这个决定")
        self.assertEqual(inj.texts, [":"], "替换字符仍要注入")


class TestGuardKeyItself(unittest.TestCase):
    """守卫键本身必须是一个"无名键" —— 打不出字符、不触发任何默认行为。"""

    def test_guard_vk_is_unassigned(self):
        self.assertEqual(input_simulator.VK_IME_GUARD, 0xE8,
                         "0xE8 是 Windows 未分配的虚拟键码；别改成有语义的键")

    def test_send_ime_guard_presses_and_releases_one_key(self):
        sent = []
        origin = input_simulator._send
        input_simulator._send = lambda items: sent.append(list(items))
        try:
            input_simulator.send_ime_guard()
        finally:
            input_simulator._send = origin

        self.assertEqual(len(sent), 1, "一次按下+抬起应该合并成一个 SendInput 批次")
        records = sent[0]
        self.assertEqual(len(records), 2, "应该是一按一抬")
        self.assertEqual(records[0].ki.wVk, input_simulator.VK_IME_GUARD)
        self.assertEqual(records[0].ki.dwFlags, 0)
        self.assertEqual(records[1].ki.wVk, input_simulator.VK_IME_GUARD)
        self.assertEqual(records[1].ki.dwFlags, input_simulator.KEYEVENTF_KEYUP)


if __name__ == "__main__":
    unittest.main()
