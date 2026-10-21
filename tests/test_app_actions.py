"""组装层的线程安全契约测试。

**背景**：托盘菜单和全局热键跑在"消息线程"上，它们最后会调到
``App.quit()`` / ``show_window()`` / ``hide_to_tray()``。
tkinter 不允许从别的线程操作控件 —— 而它的表现非常阴险：
调用会"正常返回"（几毫秒），窗口却毫无反应，实测"托盘右键退出"
能一直卡着不退出，用户的感觉就是"退个程序卡一下"。

所以这三条必须只往 ``UiBridge`` 投事件，由界面线程执行。
下面用替身窗口把这条规矩锁住 —— 谁要是哪天又写成直接调用，测试立刻红。
"""

from __future__ import annotations

import tempfile
import unittest

from codepoint.app import App
from codepoint.ui.bridge import UiBridge


class _RecordingWindow:
    """记录所有被直接调用的窗口动作（理想情况是一条都没有）。"""

    def __init__(self) -> None:
        self.calls = []

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def destroy(self) -> None:
        self.calls.append("destroy")


class TestWindowActionsGoThroughBridge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = App(config_dir=self._tmp.name, headless=False)
        self.window = _RecordingWindow()
        self.app._window = self.window      # 注入替身

    def _kinds(self):
        return [event.kind for event in self.app.bridge.poll()]

    def test_quit_only_publishes(self):
        self.app.quit()
        self.assertEqual(self.window.calls, [],
                         "quit() 直接操作了窗口 —— 从托盘线程调用会卡住")
        self.assertEqual(self._kinds(), [UiBridge.QUIT])

    def test_hide_to_tray_only_publishes(self):
        self.app.hide_to_tray()
        self.assertEqual(self.window.calls, [])
        self.assertEqual(self._kinds(), [UiBridge.HIDE_WINDOW])

    def test_show_window_only_publishes(self):
        self.app.show_window()
        self.assertEqual(self.window.calls, [])
        self.assertEqual(self._kinds(), [UiBridge.SHOW_WINDOW])

    def test_actions_are_safe_from_other_threads(self):
        """从别的线程调用也不能碰窗口（投递队列本身是线程安全的）。"""
        import threading

        errors = []

        def worker():
            try:
                self.app.show_window()
                self.app.hide_to_tray()
                self.app.quit()
            except Exception as exc:      # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)
        self.assertFalse(errors, f"跨线程调用抛异常：{errors}")
        self.assertEqual(self.window.calls, [])
        self.assertEqual(self._kinds(),
                         [UiBridge.SHOW_WINDOW, UiBridge.HIDE_WINDOW, UiBridge.QUIT])

    def test_headless_quit_shuts_down_without_window(self):
        """没有界面（--headless）时 quit() 直接收尾，不再投事件。"""
        self.app._window = None
        called = []
        self.app.shutdown = lambda: called.append("shutdown")     # type: ignore[assignment]
        self.app.quit()
        self.assertEqual(called, ["shutdown"])
        self.assertEqual(self._kinds(), [])


class TestQuitSavesOnlyWhenDirty(unittest.TestCase):
    """退出时的回写策略：没改过就别写。

    无条件回写会把用户在外部对 settings.json 的修改覆盖掉
    （典型场景：程序开着，用户手动改了配置或用了导入功能）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = App(config_dir=self._tmp.name, headless=True)

    def test_clean_config_is_not_rewritten(self):
        self.assertFalse(self.app._is_dirty(), "刚加载完就判定为脏 —— 基线没记对")
        writes = []
        self.app.store.save = lambda s: writes.append(1) or True     # type: ignore[assignment]
        self.app.shutdown()
        self.assertEqual(writes, [], "设置没变却回写了配置")

    def test_changed_config_is_rewritten(self):
        self.app.settings.punctuation_rules.append(
            __import__("codepoint.core.models", fromlist=["PunctuationRule"])
            .PunctuationRule("。", ".", True))
        self.assertTrue(self.app._is_dirty())
        writes = []
        self.app.store.save = lambda s: writes.append(1) or True     # type: ignore[assignment]
        self.app.shutdown()
        self.assertEqual(writes, [1], "设置改了却没回写")

    def test_save_refreshes_baseline(self):
        self.app.settings.global_enabled = False
        self.assertTrue(self.app._is_dirty())
        self.app.save()
        self.assertFalse(self.app._is_dirty(), "save() 之后基线没更新，会重复回写")


if __name__ == "__main__":
    unittest.main()
