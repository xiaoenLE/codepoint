"""焦点闸门测试：注入按键前必须确认"焦点在编辑区、菜单没开着"。

背景（用户报障 v1.6.3）：
    "我在其他软件中，怎么会选择到上面的属性，然后进行了点击，导致软件被关闭。"
    "反正是触发他获取后就点到菜单那边去了。"

抓上下文要注入 Shift+方向键。焦点在菜单上时，方向键会去挪菜单选中项，
用户随后一个回车就成了"点击那一项"。这里锁住"发现这种情况就不许动手"。
"""

from __future__ import annotations

import unittest
from unittest import mock

from codepoint.core.config import build_default_settings
from codepoint.core.events import WindowInfo
from codepoint.core.models import AppItem
from codepoint.platform_win import focus_state
from codepoint.services.app_filter import AppFilter


class TestLooksEditable(unittest.TestCase):
    def test_known_non_text_classes_are_rejected(self):
        for cls in ("#32768", "ToolbarWindow32", "ReBarWindow32", "SysListView32",
                    "SysTreeView32", "Button", "msctls_statusbar32", "ComboBox"):
            self.assertFalse(
                focus_state.FocusState(focus_class=cls).looks_editable,
                "%s 被判成可编辑了，注入的方向键会去操作控件" % cls)

    def test_unknown_and_editor_classes_are_allowed(self):
        """认不出来就放行 —— 猜错会把正常补全挡掉（CODESYS 是自绘控件）。"""
        for cls in ("", "Scintilla", "TEdit", "RichEdit20W",
                    "WindowsForms10.Window.8.app.0.38248fc_r8_ad1"):
            self.assertTrue(
                focus_state.FocusState(focus_class=cls).looks_editable,
                "%s 被误挡了" % (cls or "（空）"))

    def test_case_insensitive(self):
        self.assertFalse(focus_state.FocusState(focus_class="TOOLBARWINDOW32").looks_editable)

    def test_query_never_raises(self):
        state = focus_state.query()
        self.assertGreaterEqual(state.hwnd, 0)
        self.assertIsInstance(state.focus_class, str)

    def test_report_is_one_line(self):
        text = focus_state.report()
        self.assertNotIn("\n", text)
        self.assertIn("menu=", text)


class _Stub:
    """不要用 MagicMock 当 self：它会自动生成同名属性，把类上的 patch 遮掉。"""

    def __init__(self, scope):
        self._app_scope = scope
        self.settings = build_default_settings()


class TestInjectionGate(unittest.TestCase):
    """``App._injection_block_reason`` 的判定顺序与文案。"""

    def _stub(self, processes=("invtmatic studio.exe",)):
        return _Stub(AppFilter([AppItem(p) for p in processes], mode="whitelist"))

    def _check(self, *, hwnd=101, fg_hwnd=101, process="invtmatic studio.exe",
               menu_open=False, focus_class="", limited=True):
        from codepoint.app import App

        window = WindowInfo(hwnd=fg_hwnd, pid=900, process_name=process,
                            window_title="x", thread_id=900)
        fake_fg = mock.MagicMock()
        fake_fg.query.return_value = window
        state = focus_state.FocusState(hwnd=fg_hwnd, focus_hwnd=55,
                                       focus_class=focus_class, menu_open=menu_open)
        stub = self._stub()
        stub.settings.ai_scope_limited = limited
        with mock.patch("codepoint.app.foreground", fake_fg), \
                mock.patch("codepoint.app.focus_state") as fs:
            fs.query.return_value = state
            fs.report.return_value = "stub"
            return App._injection_block_reason(stub, hwnd)

    def test_ok_case_returns_empty(self):
        self.assertEqual(self._check(), "")

    def test_window_switched(self):
        self.assertEqual(self._check(fg_hwnd=202), "窗口变了")

    def test_out_of_scope_process(self):
        self.assertEqual(self._check(process="somegame.exe"), "不在生效范围")

    def test_open_menu_blocks(self):
        """菜单开着时一个键都不许发 —— 否则方向键会去挪菜单选中项。"""
        self.assertEqual(self._check(menu_open=True), "菜单正开着")

    def test_focus_on_toolbar_blocks(self):
        reason = self._check(focus_class="ToolbarWindow32")
        self.assertIn("焦点不在编辑区", reason)
        self.assertIn("ToolbarWindow32", reason)

    def test_scope_allows_agrees_with_reason(self):
        from codepoint.app import App

        class Stub(App):
            """只为了能绑定 ``_scope_allows``；不跑 App.__init__（那要建 Tk）。"""

            def __init__(self):
                self._app_scope = AppFilter([AppItem("invtmatic studio.exe")],
                                            mode="whitelist")
                self.settings = build_default_settings()

        stub = Stub()
        with mock.patch.object(App, "_injection_block_reason", return_value=""):
            self.assertTrue(App._scope_allows(stub, 101))
        with mock.patch.object(App, "_injection_block_reason", return_value="菜单正开着"):
            self.assertFalse(App._scope_allows(stub, 101))


class TestScopeLimitedSwitch(unittest.TestCase):
    """「AI 也遵守生效范围」开关（用户要求的：默认拦，但要能单独放开）。

    报障（2026-10-21）："设置生效范围是列表内程序了，但是在没有的程序里面
    还是可以使用快捷键调用 ai 呢，应该也不能，或者加一个按钮来区分 ai"。
    """

    def _reason(self, *, process="somegame.exe", limited=True,
                menu_open=False, focus_class=""):
        from codepoint.app import App

        window = WindowInfo(hwnd=101, pid=900, process_name=process,
                            window_title="x", thread_id=900)
        fake_fg = mock.MagicMock()
        fake_fg.query.return_value = window
        state = focus_state.FocusState(hwnd=101, focus_hwnd=55,
                                       focus_class=focus_class, menu_open=menu_open)
        stub = _Stub(AppFilter([AppItem("invtmatic studio.exe")], mode="whitelist"))
        stub.settings.ai_scope_limited = limited
        with mock.patch("codepoint.app.foreground", fake_fg), \
                mock.patch("codepoint.app.focus_state") as fs:
            fs.query.return_value = state
            fs.report.return_value = "stub"
            return App._injection_block_reason(stub, 101)

    def test_default_blocks_programs_outside_the_list(self):
        self.assertEqual(self._reason(), "不在生效范围")

    def test_switch_off_allows_programs_outside_the_list(self):
        self.assertEqual(self._reason(limited=False), "")

    def test_switch_off_still_blocks_open_menu(self):
        """放开生效范围 ≠ 放开菜单 —— 那两道保护必须始终在。"""
        self.assertEqual(self._reason(limited=False, menu_open=True), "菜单正开着")

    def test_switch_off_still_blocks_toolbar_focus(self):
        reason = self._reason(limited=False, focus_class="ToolbarWindow32")
        self.assertIn("焦点不在编辑区", reason)

    def test_setting_round_trip(self):
        from codepoint.core.models import Settings

        s = Settings()
        self.assertTrue(s.ai_scope_limited, "默认必须管（推荐）")
        s.ai_scope_limited = False
        self.assertFalse(Settings.from_dict(s.to_dict()).normalize().ai_scope_limited)
        d = Settings().to_dict()
        d.pop("ai_scope_limited", None)
        self.assertTrue(Settings.from_dict(d).normalize().ai_scope_limited,
                        "老配置缺这个键时应走默认（管）")


if __name__ == "__main__":
    unittest.main()
