"""主板（KeyboardPipeline）集成测试 —— 用假平台把整条链路跑通。"""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from codepoint.core.config import build_default_settings
from codepoint.core.events import ImeSnapshot, WindowInfo
from codepoint.core.models import AppItem, DETECT_CHAR, DETECT_WORD, PunctuationRule, QuickAction
from codepoint.services.pipeline import KeyboardPipeline

from .fakes import (
    FakeClock,
    FakeImeProbe,
    FakeInjector,
    FakeWindowProbe,
    RecordingNotifier,
    VK_COMMA,
    VK_OEM_1,
    VK_TAB,
    key,
)

IME_CJK = ImeSnapshot(available=True, open=True, native=True)


def make_pipeline(*, whitelisted: bool = True, ime: ImeSnapshot = IME_CJK,
                  use_default_quick_actions: bool = False):
    window = WindowInfo(
        hwnd=101, pid=200,
        process_name="codesys.exe" if whitelisted else "notepad.exe",
        window_title="CODESYS",
        thread_id=200,
    )
    settings = build_default_settings()
    settings.app_items = [AppItem("codesys.exe")]
    if use_default_quick_actions:
        # 用出厂默认的快捷输入（`:` → `:=` 是停用的）
        settings.quick_actions = build_default_settings().quick_actions
    else:
        settings.punctuation_rules = [
            PunctuationRule("，", ",", True),
            PunctuationRule("：", ":", True),   # 让 ':' 也走标点替换路径
        ]
        settings.quick_actions = [
            QuickAction(":", DETECT_CHAR, ":=", True),
            QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True),
        ]
    injector = FakeInjector()
    notifier = RecordingNotifier()
    clock = FakeClock()
    pipeline = KeyboardPipeline(
        window_probe=FakeWindowProbe(window),
        ime_probe=FakeImeProbe(ime),
        injector=injector,
        notifier=notifier,
        clock=clock.now,
    )
    pipeline.apply_settings(settings)
    return pipeline, injector, notifier, clock


class TestPipelineBasics(unittest.TestCase):
    def test_disabled_pipeline_passes_everything(self):
        p, inj, _, _ = make_pipeline()
        p.set_enabled(False)
        self.assertFalse(p.handle(key(VK_COMMA, ",")))
        self.assertEqual(inj.calls, [])

    def test_out_of_scope_app_is_untouched(self):
        p, inj, _, _ = make_pipeline(whitelisted=False)
        self.assertFalse(p.handle(key(VK_COMMA, ",")))
        self.assertEqual(inj.calls, [])
        self.assertEqual(p.stats.punctuation_replaced, 0)

    def test_targeted_comma_is_swallowed_and_reinjected(self):
        p, inj, _, _ = make_pipeline()
        self.assertTrue(p.handle(key(VK_COMMA, ",")))
        self.assertEqual(inj.output(), ",")
        self.assertEqual(p.stats.punctuation_replaced, 1)

    def test_letter_passes_through(self):
        p, inj, _, _ = make_pipeline()
        self.assertFalse(p.handle(key(0x41, "a")))
        self.assertEqual(inj.calls, [])

    def test_ctrl_combo_never_touched(self):
        p, inj, _, _ = make_pipeline()
        self.assertFalse(p.handle(key(VK_COMMA, ",", ctrl=True)))
        self.assertEqual(inj.calls, [])

    def test_toggle_switches_state(self):
        p, _, _, _ = make_pipeline()
        self.assertFalse(p.toggle())
        self.assertFalse(p.enabled)
        self.assertTrue(p.toggle())


class TestPipelineQuickAction(unittest.TestCase):
    def test_char_mode_colon_completes_via_punctuation_path(self):
        """':' 同时在标点规则里 —— 应吞键，且省掉"先注入 : 再退格"的多余往返。"""
        p, inj, _, _ = make_pipeline()
        swallowed = p.handle(key(VK_OEM_1, ":"))
        self.assertTrue(swallowed)
        self.assertEqual(inj.calls, [("block", ":=")], "应直接写入 :=（走 send_block），不做多余的注入+退格")
        self.assertEqual(p.stats.quick_completed, 1)

    def test_default_settings_do_not_hijack_colon(self):
        """出厂配置下按 Shift+;（冒号）必须**原样通过**。

        默认的 `:` → `:=` 规则是停用的：单字符触发会永久劫持那个字符，
        否则冒号在受管程序里永远变成 `:=`（CASE 标签 `0:`、`x : INT;` 全废）。
        """
        p, inj, _, _ = make_pipeline(use_default_quick_actions=True)
        swallowed = p.handle(key(VK_OEM_1, ":"))
        # 冒号会走"标点替换"路径：吞键 → 以半角字符重新注入（绕过输入法）。
        # 关键断言是**注入的内容必须是单个 ":"** —— 曾经快捷输入把它劫持成 ":="，
        # 导致冒号在受管程序里根本打不出来。
        self.assertTrue(swallowed)
        self.assertEqual(inj.calls, [("text", ":")],
                         "冒号必须原样注入，不能被快捷输入改成 :=")
        self.assertEqual(p.stats.quick_completed, 0, "快捷输入不该接管冒号")

    def test_char_mode_must_swallow_even_when_punctuation_not_configured(self):
        """':' 不在标点规则里时也**必须吞键**。

        反例（曾经的实现）：放行原按键再立刻发退格 + 补全内容。
        由于放行的按键还在消息队列里，退格会先执行 → 删掉的是**前一个**字符，
        接着目标程序才插入 ':'，位置全乱。用户看到的就是"按 : 结果 : 和 := 乱跳"。
        """
        p, inj, _, _ = make_pipeline()
        settings = build_default_settings()
        settings.punctuation_rules = [PunctuationRule("，", ",", True)]   # 不含 ':'
        settings.quick_actions = [QuickAction(":", DETECT_CHAR, ":=", True)]
        settings.app_items = [AppItem("codesys.exe")]
        p.apply_settings(settings)
        swallowed = p.handle(key(VK_OEM_1, ":"))
        self.assertTrue(swallowed, "单字符补全必须吞掉触发键，否则退格会删错位置")
        self.assertEqual(inj.calls, [("block", ":=")])
        self.assertEqual(p.stats.keys_swallowed, 1)

    def test_word_mode_does_not_force_swallow(self):
        """整词模式不吞触发字符（字符已经在目标程序里了），退格数保持 = 触发串长度。"""
        p, inj, _, _ = make_pipeline()
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        p.handle(key(VK_TAB, "\t"))
        self.assertEqual(inj.calls[0], ("back", 2))

    def test_word_mode_then_tab(self):
        p, inj, notifier, _ = make_pipeline()
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        self.assertEqual(notifier.shown, 1, "整词命中应请求显示呼吸灯")
        self.assertIsNotNone(p.quick_actions.pending)

        swallowed = p.handle(key(VK_TAB, "\t"))
        self.assertTrue(swallowed, "Tab 应被吞掉")
        self.assertIn(("back", 2), inj.calls)
        self.assertTrue(any(t.startswith("IF") for t in inj.texts))
        self.assertEqual(notifier.hidden, 1)

    def test_tab_without_pending_passes_through(self):
        p, inj, _, _ = make_pipeline()
        self.assertFalse(p.handle(key(VK_TAB, "\t")))
        self.assertEqual(inj.calls, [])

    def test_out_of_scope_does_not_trigger_quick_action(self):
        p, inj, notifier, _ = make_pipeline(whitelisted=False)
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        self.assertEqual(notifier.shown, 0)
        self.assertIsNone(p.quick_actions.pending)

    def test_pending_expires_via_tick(self):
        p, _, notifier, clock = make_pipeline()
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        clock.advance(120)
        p.tick()
        self.assertIsNone(p.quick_actions.pending)
        self.assertGreaterEqual(notifier.hidden, 1)


class TestPipelineExceptions(unittest.TestCase):
    def test_handler_exception_still_passes_key(self):
        """业务层任何异常都不能把用户的键盘卡住。"""
        p, _, _, _ = make_pipeline()

        class Boom:
            def front(self):
                raise RuntimeError("boom")

        p._windows = Boom()          # 故意打破
        logging.disable(logging.CRITICAL)      # 这是预期内的异常，别刷屏
        try:
            self.assertFalse(p.handle(key(VK_COMMA, ",")))
        finally:
            logging.disable(logging.NOTSET)


class TestPendingTabOnlyInScope(unittest.TestCase):
    """挂起结果的 Tab 只在**受管程序**里才算"接受"。

    用户报障（v1.6.3）："我在其他软件中，怎么会选择到上面的属性，然后进行了
    点击，导致软件被关闭。" —— 同一个毛病：没校验范围就动手。这里锁住
    "在别的程序里按键不许吞、更不许插入"。
    """

    def test_tab_in_other_app_is_not_swallowed(self):
        p, inj, _, _ = make_pipeline(whitelisted=False)
        p._notifier = mock.MagicMock()      # offer() 要弹预览卡，测试里不需要真弹
        self.assertTrue(p.offer("x := 1;", backspaces=0, label="AI", hwnd=101,
                                timeout=10))
        self.assertFalse(p.handle(key(VK_TAB, "\t")), "别家程序的 Tab 必须放行")
        self.assertEqual(inj.calls, [], "不许往别家程序里写东西")
        self.assertIsNone(p._pending_text, "窗口不合适就立刻作废，别留着")

    def test_tab_in_targeted_app_still_accepts(self):
        p, inj, _, _ = make_pipeline()
        p._notifier = mock.MagicMock()
        self.assertTrue(p.offer("x := 1;", backspaces=0, label="AI", hwnd=101,
                                timeout=10))
        self.assertTrue(p.handle(key(VK_TAB, "\t")))
        self.assertEqual(inj.output(), "x := 1;")


class TestEscapeCancelsGeneration(unittest.TestCase):
    """生成中按 Esc 要能掐断请求。

    FIM 补一大段可能跑十几秒，早看出跑偏就该能停手 —— 不能"按了 Esc 还得
    干等它写满"（用户原话："他还在输出的时候，我能不能直接按 esc 打断他"）。
    pipeline 只负责"看见 Esc"，真正断流由 app 在流式回调里抛异常完成。
    """

    def _pipeline(self, hook):
        from codepoint.core.events import VK_ESCAPE

        window = WindowInfo(hwnd=1, pid=2, process_name="codesys.exe",
                            window_title="x", thread_id=2)
        p = KeyboardPipeline(
            window_probe=FakeWindowProbe(window),
            ime_probe=FakeImeProbe(IME_CJK),
            injector=FakeInjector(),
            notifier=RecordingNotifier(),
            cancel_hook=hook,
        )
        p.apply_settings(build_default_settings())
        return p, VK_ESCAPE

    def test_escape_calls_cancel_hook(self):
        calls = []
        p, vk = self._pipeline(lambda: calls.append(1))
        p.handle(key(vk))
        self.assertEqual(calls, [1])

    def test_escape_keyup_does_not_trigger(self):
        calls = []
        p, vk = self._pipeline(lambda: calls.append(1))
        p.handle(key(vk, is_down=False))
        self.assertEqual(calls, [])

    def test_no_hook_is_harmless(self):
        p, vk = self._pipeline(None)
        self.assertFalse(p.handle(key(vk)))


if __name__ == "__main__":
    unittest.main()
