"""预览窗 / 呼吸灯 两个开关各管各的 —— 回归测试。

背景（用户报障，2026-10-21）：
    "tab补全提示（呼吸灯等）关了就没有显示了，这个也是bug"

根因：「Tab 补全提示（呼吸灯）」这个开关（``indicator_enabled``）**同时**挡掉了
AI 的预览窗（`IndicatorNotifier.show_card` / `stream_card` / `MainWindow._show_card`
三处都看它）。于是关掉呼吸灯之后：

  1. AI 预览窗不再显示（被挡）；
  2. 但 app 还以为"预览开着"，把结果**挂起等按 Tab**；
  3. 屏幕上什么都没有，用户随便敲一个键 → 这份结果被作废。

表现就是"关了就什么都不显示"，生成的代码也丢了。

现在：呼吸灯开关只管快捷输入的提示（`show()`），AI 预览窗归「显示预览窗」
（``ai_card_enabled``）管 —— app 里关掉它就直接插入，根本不挂起。
"""

from __future__ import annotations

import unittest
from unittest import mock

from codepoint.core.config import build_default_settings
from codepoint.core.events import ImeSnapshot, WindowInfo
from codepoint.ui.bridge import UiBridge
from codepoint.ui.card import Card
from codepoint.ui.indicator import IndicatorNotifier


class TestNotifierGating(unittest.TestCase):
    def _kinds(self, notifier, card):
        bridge = UiBridge()
        notifier._bridge = bridge
        notifier.show_card(card)
        return [e.kind for e in bridge.poll()]

    def test_show_card_ignores_indicator_switch(self):
        """呼吸灯关着，AI 预览卡照样要发出去 —— 否则结果挂起却看不见。"""
        n = IndicatorNotifier(UiBridge(), enabled=False)
        kinds = self._kinds(n, Card.ready("AI 补全", "x := 1;"))
        self.assertIn(UiBridge.CARD_SHOW, kinds)

    def test_stream_card_ignores_indicator_switch(self):
        n = IndicatorNotifier(UiBridge(), enabled=False)
        bridge = UiBridge()
        n._bridge = bridge
        n.stream_card("x := 1;", label="AI 补全")
        self.assertIn(UiBridge.CARD_STREAM, [e.kind for e in bridge.poll()])

    def test_quick_action_hint_still_respects_the_switch(self):
        """快捷输入的提示**是**这个开关管的：关了就不许弹。"""
        n = IndicatorNotifier(UiBridge(), enabled=False)
        bridge = UiBridge()
        n._bridge = bridge
        n.show(mock.MagicMock(trigger="if", content="IF  THEN\nEND_IF;"))
        self.assertEqual([e.kind for e in bridge.poll()], [],
                         "呼吸灯关着就不该弹快捷输入提示")

    def test_quick_action_hint_shows_when_switch_on(self):
        n = IndicatorNotifier(UiBridge(), enabled=True)
        bridge = UiBridge()
        n._bridge = bridge
        n.show(mock.MagicMock(trigger="if", content="IF  THEN\nEND_IF;"))
        self.assertIn(UiBridge.CARD_SHOW, [e.kind for e in bridge.poll()])


class TestQuickInputSurvivesIndicatorOff(unittest.TestCase):
    """呼吸灯关着，快捷输入**功能**必须照常 —— 只是提示条不显示。

    用户问（2026-10-21）："关了这个后快捷输入也关了呢" —— 答案是不会。
    这里把"关了提示也照样能 Tab 补全"钉住，免得以后谁再把它做成强耦合。
    """

    def _pipeline(self, indicator_on: bool):
        from codepoint.core.models import AppItem, DETECT_WORD, QuickAction
        from codepoint.services.pipeline import KeyboardPipeline
        from .fakes import FakeInjector, FakeImeProbe, FakeWindowProbe

        window = WindowInfo(hwnd=101, pid=200, process_name="codesys.exe",
                            window_title="x", thread_id=200)
        settings = build_default_settings()
        settings.app_items = [AppItem("codesys.exe")]
        settings.quick_actions = [
            QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True)]

        bridge = UiBridge()
        notifier = IndicatorNotifier(bridge, enabled=indicator_on)
        injector = FakeInjector()
        pipeline = KeyboardPipeline(
            window_probe=FakeWindowProbe(window),
            ime_probe=FakeImeProbe(ImeSnapshot(True, True, True)),
            injector=injector,
            notifier=notifier,
        )
        pipeline.apply_settings(settings)
        return pipeline, injector, bridge

    def test_tab_still_completes_when_indicator_off(self):
        from .fakes import key, VK_TAB

        p, inj, bridge = self._pipeline(indicator_on=False)
        p.handle(key(0x49, "i"))
        p.handle(key(0x46, "f"))
        self.assertIsNotNone(p.quick_actions.pending, "命中要记下来，跟显不显示无关")
        self.assertEqual([e.kind for e in bridge.poll()], [], "关着就不该弹提示")

        self.assertTrue(p.handle(key(VK_TAB, "\t")), "Tab 应被吞掉并补全")
        self.assertTrue(any(t.startswith("IF") for t in inj.texts),
                        "呼吸灯关着也要能补全 —— 快捷输入没被关掉")


class TestMainWindowGating(unittest.TestCase):
    def test_card_shows_even_when_indicator_off(self):
        """界面侧也不能再看呼吸灯开关 —— 那是第二道挡错位置的闸。"""
        from codepoint.ui.main_window import MainWindow

        settings = build_default_settings()
        settings.indicator_enabled = False          # 呼吸灯关着
        settings.ai_card_enabled = True            # 预览窗开着

        fake = mock.MagicMock()
        fake._facade.get_settings.return_value = settings
        fake._card = None
        fake.root = mock.MagicMock()

        with mock.patch("codepoint.ui.main_window.CardWindow") as card_cls:
            MainWindow._show_card(fake, Card.ready("AI 补全", "x := 1;"))
        self.assertTrue(card_cls.called,
                        "呼吸灯关着也要建预览窗，否则结果挂起却看不见")


if __name__ == "__main__":
    unittest.main()
