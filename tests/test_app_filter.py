"""生效范围判定（services.app_filter）的测试。

覆盖两种模式：
  whitelist —— 只有列表里的程序生效
  all       —— 所有程序生效，列表变成排除名单
"""

from __future__ import annotations

import unittest

from codepoint.core.events import WindowInfo
from codepoint.core.models import MATCH_ALL, MATCH_WHITELIST, AppItem
from codepoint.services.app_filter import (
    REASON_IN_SCOPE,
    REASON_NO_WINDOW,
    REASON_OUT_OF_SCOPE,
    REASON_SELF,
    AppFilter,
)


def win(process: str, title: str = "", pid: int = 100, hwnd: int = 5) -> WindowInfo:
    return WindowInfo(hwnd=hwnd, pid=pid, process_name=process, window_title=title)


class TestMatching(unittest.TestCase):
    def test_process_name_exact_case_insensitive(self):
        f = AppFilter([AppItem("TiaPortal.exe")])
        self.assertTrue(f.matches(win("tiaportal.exe")))
        self.assertFalse(f.matches(win("tiaportal.other.exe")))

    def test_window_title_substring(self):
        f = AppFilter([AppItem("", "博途")])
        self.assertTrue(f.matches(win("whatever.exe", "博途 - 项目1")))
        self.assertFalse(f.matches(win("whatever.exe", "别的窗口")))

    def test_both_fields_must_match(self):
        f = AppFilter([AppItem("tiaportal.exe", "项目1")])
        self.assertTrue(f.matches(win("tiaportal.exe", "项目1 - 主程序")))
        self.assertFalse(f.matches(win("tiaportal.exe", "项目2")))

    def test_disabled_item_never_matches(self):
        f = AppFilter([AppItem("tiaportal.exe", enabled=False)])
        self.assertFalse(f.matches(win("tiaportal.exe")))

    def test_empty_item_is_not_a_wildcard(self):
        """空条目绝不能通吃所有程序。"""
        f = AppFilter([AppItem("", "")])
        self.assertFalse(f.matches(win("anything.exe", "任何标题")))

    def test_never_matches_self(self):
        f = AppFilter([AppItem("python.exe")], self_pid=999)
        ok, reason = f.decide(win("python.exe", pid=999))
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_SELF)

    def test_invalid_window(self):
        f = AppFilter([AppItem("tiaportal.exe")])
        ok, reason = f.decide(WindowInfo())
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_NO_WINDOW)


class TestWhitelistMode(unittest.TestCase):
    def setUp(self):
        self.f = AppFilter([AppItem("codesys.exe")], mode=MATCH_WHITELIST)

    def test_listed_program_is_active(self):
        ok, reason = self.f.decide(win("codesys.exe"))
        self.assertTrue(ok)
        self.assertEqual(reason, REASON_IN_SCOPE)

    def test_unlisted_program_is_ignored(self):
        ok, reason = self.f.decide(win("notepad.exe"))
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_OUT_OF_SCOPE)

    def test_empty_list_means_nothing_happens(self):
        f = AppFilter([], mode=MATCH_WHITELIST)
        self.assertFalse(f.matches(win("codesys.exe")))


class TestAllMode(unittest.TestCase):
    """全部程序模式：列表是排除名单。"""

    def setUp(self):
        self.f = AppFilter([AppItem("wechat.exe")], mode=MATCH_ALL)

    def test_unlisted_program_is_active(self):
        ok, reason = self.f.decide(win("notepad.exe"))
        self.assertTrue(ok)
        self.assertEqual(reason, REASON_IN_SCOPE)

    def test_listed_program_is_excluded(self):
        ok, reason = self.f.decide(win("wechat.exe"))
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_OUT_OF_SCOPE)

    def test_empty_list_means_everything(self):
        f = AppFilter([], mode=MATCH_ALL)
        self.assertTrue(f.matches(win("随便什么.exe")))

    def test_self_still_excluded(self):
        f = AppFilter([], mode=MATCH_ALL, self_pid=999)
        self.assertFalse(f.matches(win("python.exe", pid=999)))


class TestModeSwitching(unittest.TestCase):
    def test_update_can_change_mode(self):
        f = AppFilter([AppItem("codesys.exe")], mode=MATCH_WHITELIST)
        self.assertFalse(f.matches(win("notepad.exe")))
        f.update([AppItem("codesys.exe")], MATCH_ALL)
        self.assertTrue(f.matches(win("notepad.exe")))

    def test_unknown_mode_falls_back_to_whitelist(self):
        f = AppFilter([AppItem("codesys.exe")], mode="乱写的模式")
        self.assertEqual(f.mode, MATCH_WHITELIST)

    def test_describe_reason_is_human_readable(self):
        f = AppFilter([AppItem("codesys.exe")], mode=MATCH_WHITELIST)
        self.assertIn("codesys.exe", f.describe_reason(win("codesys.exe")))
        self.assertIn("未加入", f.describe_reason(win("notepad.exe")))

    def test_describe_reason_for_excluded(self):
        f = AppFilter([AppItem("wechat.exe")], mode=MATCH_ALL)
        self.assertIn("排除", f.describe_reason(win("wechat.exe")))


if __name__ == "__main__":
    unittest.main()
