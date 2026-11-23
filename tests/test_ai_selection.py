"""选中文本抓取（Ctrl+Alt+G 的第一步）与 AI 连接测试。

全部用假剪贴板 / 假时钟，不真按 Ctrl+C，也不联网 —— 断网、没装模型都能跑。
"""

from __future__ import annotations

import unittest
from unittest import mock

from codepoint.core.models import Settings
from codepoint.services import ai_provider as ai
from codepoint.services.selection import GrabResult, SelectionGrabber


# ---------------------------------------------------------------- 替身


class FakeClock:
    """假时间：sleep 就把时间往前推，于是超时逻辑不用真等。"""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


class FakeClipboard:
    def __init__(self, text=None, seq: int = 1) -> None:
        self.text = text
        self.seq = seq
        self.writes: list = []

    def get_text(self):
        return self.text

    def get_selected_text(self):
        return self.get_text()

    def set_text(self, text: str) -> bool:
        self.text = text
        self.seq += 1
        self.writes.append(text)
        return True

    def sequence_number(self) -> int:
        return self.seq


class FakeCopier:
    """模拟"用户按了 Ctrl+C"：把 payload 塞进剪贴板（序号随之变化）。"""

    def __init__(self, clipboard: FakeClipboard, payload=None,
                 fail: bool = False, ready: bool = True) -> None:
        self.clipboard = clipboard
        self.payload = payload
        self.fail = fail
        self.ready = ready
        self.calls = 0
        self.waits = 0
        self.order: list = []
        self.remaining_after_restore = None

    def wait_ready(self) -> bool:
        self.waits += 1
        self.order.append("wait")
        return self.ready

    def copy_selection(self) -> None:
        self.calls += 1
        self.order.append("copy")
        if self.fail:
            raise RuntimeError("SendInput 失败")
        if self.payload is not None:
            self.clipboard.set_text(self.payload)

    def select_context(self, count: int, *, below: bool) -> bool:
        self.order.append(("select", count, below))
        return True

    def move_context_endpoint(self, count: int, *, left: bool) -> bool:
        self.order.append(("move", count, left))
        self.payload = self.remaining_after_restore
        return True

    def collapse_selection(self, *, to_start: bool) -> bool:
        self.order.append(("collapse", to_start))
        return True

    def caret_position(self):
        return None


def _grabber(clipboard, copier, *, wait=0.05):
    clock = FakeClock()
    return SelectionGrabber(
        clipboard, copier, wait=wait, poll=0.01,
        sleep=clock.sleep, monotonic=clock.monotonic)


# ---------------------------------------------------------------- 抓取


class TestSelectionGrabber(unittest.TestCase):
    def test_grabs_selection_and_restores_clipboard(self):
        clipboard = FakeClipboard("原来复制的东西")
        copier = FakeCopier(clipboard, payload="选中这段中文")
        result = _grabber(clipboard, copier).grab()

        self.assertEqual(result.text, "选中这段中文")
        self.assertTrue(result.ok)
        self.assertTrue(result.restored)
        # 用户原来的剪贴板被还回去了
        self.assertEqual(clipboard.text, "原来复制的东西")
        self.assertEqual(clipboard.writes, ["选中这段中文", "原来复制的东西"])

    def test_nothing_selected_times_out(self):
        # 没选中时 Ctrl+C 不会碰剪贴板 → 序号不变 → 走到超时
        clipboard = FakeClipboard("旧内容")
        copier = FakeCopier(clipboard, payload=None)
        result = _grabber(clipboard, copier).grab()

        self.assertEqual(result.text, "")
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        # 超时也不该动剪贴板
        self.assertEqual(clipboard.text, "旧内容")

    def test_copy_failure_is_swallowed(self):
        clipboard = FakeClipboard("旧内容")
        copier = FakeCopier(clipboard, payload="x", fail=True)
        result = _grabber(clipboard, copier).grab()

        self.assertEqual(result.text, "")
        self.assertFalse(result.ok)
        self.assertFalse(result.restored)

    def test_no_backup_means_no_restore(self):
        """原来剪贴板是图片（没有文本）→ 没法用文本还原，就别乱写空串。"""
        clipboard = FakeClipboard(None)
        copier = FakeCopier(clipboard, payload="选中内容")
        result = _grabber(clipboard, copier).grab()

        self.assertEqual(result.text, "选中内容")
        self.assertFalse(result.restored)
        self.assertEqual(clipboard.writes, ["选中内容"])   # 只写了一次（即复制那次）

    def test_whitespace_only_snippet_counts_as_empty(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="   \n  ")
        result = _grabber(clipboard, copier).grab()
        self.assertFalse(result.ok)          # 全是空白就等于没选中

    def test_editor_whole_line_copy_is_not_an_explicit_selection(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="current_line;\r\n")
        clipboard.get_selected_text = mock.Mock(return_value="")
        result = _grabber(clipboard, copier).grab()
        self.assertFalse(result.ok)
        self.assertFalse(result.timed_out)
        self.assertEqual(clipboard.text, "旧")

    def test_copies_only_once(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="内容")
        _grabber(clipboard, copier).grab()
        self.assertEqual(copier.calls, 1, "多按一次 Ctrl+C 会闪两次")

    def test_waits_for_modifiers_before_pressing(self):
        """热键触发时 Ctrl/Alt 还按着 —— 必须等松开再发 Ctrl+C。

        不等的话发出去是 Ctrl+Alt+C，记事本/CODESYS 都不认，剪贴板不会变，
        表现就是"明明选中了却说没拿到文本"（用户实测就是这个）。
        """
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="选中内容")
        _grabber(clipboard, copier).grab()

        self.assertEqual(copier.order, ["wait", "copy"], "顺序必须是先等松手再按键")

    def test_modifiers_stuck_skips_the_copy_entirely(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="选中内容", ready=False)
        result = _grabber(clipboard, copier).grab()

        self.assertTrue(result.modifiers_stuck)
        self.assertFalse(result.ok)
        self.assertEqual(copier.calls, 0, "没松手就绝不能发 Ctrl+C")
        self.assertEqual(clipboard.text, "旧")

    def test_stuck_is_not_confused_with_nothing_selected(self):
        """两种失败要给不同的提示：一个是"松手"，一个是"先选中"。"""
        clipboard = FakeClipboard("旧")
        stuck = _grabber(clipboard, FakeCopier(clipboard, ready=False)).grab()
        empty = _grabber(clipboard, FakeCopier(clipboard, payload=None)).grab()

        self.assertTrue(stuck.modifiers_stuck)
        self.assertFalse(empty.modifiers_stuck)
        self.assertTrue(empty.timed_out)


    def test_focus_guard_skips_copy_after_waiting(self):
        clipboard = FakeClipboard("旧内容")
        copier = FakeCopier(clipboard, payload="不能复制的内容")
        result = _grabber(clipboard, copier).grab(can_copy=lambda: False)
        self.assertFalse(result.ok)
        self.assertEqual(copier.calls, 0)
        self.assertEqual(clipboard.writes, [])

    def test_empty_selection_at_document_edge_does_not_collapse(self):
        for below in (False, True):
            with self.subTest(below=below):
                clipboard = FakeClipboard("旧")
                copier = FakeCopier(clipboard)
                result = _grabber(clipboard, copier).grab_context(3, below=below, can_copy=lambda: True)
                self.assertTrue(result.timed_out)
                self.assertFalse(result.caret_uncertain)
                self.assertEqual(copier.order, ["wait", ("select", 3, below), "copy"])

    def test_context_copy_restores_from_the_correct_side(self):
        for below in (False, True):
            with self.subTest(below=below):
                clipboard = FakeClipboard("旧")
                copier = FakeCopier(clipboard, payload="code;\n")
                result = _grabber(clipboard, copier).grab_context(3, below=below, can_copy=lambda: True)
                self.assertEqual(result.text, "code;\n")
                self.assertFalse(result.caret_uncertain)
                self.assertEqual(copier.order, ["wait", ("select", 3, below), "copy",
                                                ("move", 6, below), "copy"])
                self.assertEqual(clipboard.text, "旧")

    def test_remaining_selection_marks_context_unusable(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="context")
        copier.remaining_after_restore = "remaining"
        result = _grabber(clipboard, copier).grab_context(3, below=False, can_copy=lambda: True)
        self.assertTrue(result.caret_uncertain)

    def test_legacy_st_capture_collapses_toward_original_caret(self):
        for below in (False, True):
            with self.subTest(below=below):
                clipboard = FakeClipboard("旧")
                copier = FakeCopier(clipboard, payload="context")
                result = _grabber(clipboard, copier).grab_context(
                    3, below=below, can_copy=lambda: True, collapse_to_anchor=True)
                self.assertEqual(result.text, "context")
                self.assertFalse(result.caret_uncertain)
                self.assertEqual(copier.order, ["wait", ("select", 3, below), "copy", ("collapse", below)])
                self.assertEqual(clipboard.text, "旧")

    def test_whole_line_copy_after_restoration_does_not_cancel_context(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="context")
        copier.remaining_after_restore = "current_line;\r\n"
        clipboard.get_selected_text = mock.Mock(side_effect=["context", ""])
        result = _grabber(clipboard, copier).grab_context(3, below=False, can_copy=lambda: True)
        self.assertEqual(result.text, "context")
        self.assertFalse(result.caret_uncertain)
        self.assertEqual(clipboard.text, "旧")

    def test_win32_selected_text_ignores_the_editor_line_marker(self):
        from codepoint.platform_win import clipboard

        with mock.patch.object(clipboard, "_open_clipboard", return_value=True), \
                mock.patch.object(clipboard, "_WHOLE_LINE", 0xC000), \
                mock.patch.object(clipboard.user32, "IsClipboardFormatAvailable", return_value=True), \
                mock.patch.object(clipboard.user32, "CloseClipboard"), \
                mock.patch.object(clipboard, "_read_text", return_value="full line;\r\n"):
            self.assertEqual(clipboard.get_text(), "full line;\r\n")
            self.assertEqual(clipboard.get_text(selection_only=True), "")

    def test_shift_arrows_submit_key_down_and_key_up_separately(self):
        from codepoint.platform_win import input_simulator as sim

        with mock.patch.object(sim, "modifiers_pressed", return_value=False), \
                mock.patch.object(sim, "_send", return_value=1) as sent:
            self.assertTrue(sim.select_context(2, below=False))
        frames = [[(item.ki.wVk, item.ki.dwFlags) for item in call.args[0]]
                  for call in sent.call_args_list]
        self.assertEqual(frames, [[(sim.VK_SHIFT, 0)], [(sim.VK_UP, 0)],
                                  [(sim.VK_UP, sim.KEYEVENTF_KEYUP)], [(sim.VK_UP, 0)],
                                  [(sim.VK_UP, sim.KEYEVENTF_KEYUP)], [(sim.VK_SHIFT, sim.KEYEVENTF_KEYUP)]])

    def test_windows_line_break_restores_with_one_shift_left(self):
        from codepoint.platform_win import input_simulator

        copier = FakeCopier(FakeClipboard("旧"), payload="\r\n")
        result = _grabber(copier.clipboard, copier).grab_context(3, below=True, can_copy=lambda: True)
        self.assertFalse(result.caret_uncertain)
        self.assertIn(("move", 1, True), copier.order)
        with mock.patch.object(input_simulator, "_shift_directions", return_value=True) as shifted:
            self.assertTrue(input_simulator.move_context_endpoint(1, left=True))
        shifted.assert_called_once_with(input_simulator.VK_LEFT, 1)

    def test_multi_line_prefix_restores_one_step_per_character_and_newline(self):
        copier = FakeCopier(FakeClipboard("旧"), payload="甲;\r\n乙")
        result = _grabber(copier.clipboard, copier).grab_context(3, below=False, can_copy=lambda: True)
        self.assertFalse(result.caret_uncertain)
        self.assertIn(("move", 4, False), copier.order)

    def test_extra_terminal_newline_is_corrected_by_reversing_a_step(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="\r\n\r\n")
        moves = []

        def move(count, *, left):
            moves.append((count, left))
            copier.payload = "数" if len(moves) == 1 else None
            return True

        copier.move_context_endpoint = move
        result = _grabber(clipboard, copier).grab_context(5, below=True, can_copy=lambda: True)
        self.assertFalse(result.caret_uncertain)
        self.assertEqual(moves, [(2, True), (1, False)])


class TestAiContextDoesNotMoveCaret(unittest.TestCase):
    def _run_worker(self, context, *, copied=GrabResult(), prefix=GrabResult(), suffix=GrabResult(),
                    process_name="", type_during_request=False):
        import threading
        from codepoint.app import App
        from codepoint.core.events import WindowInfo
        from codepoint.platform_win.focus_state import FocusState

        app = App.__new__(App)
        app.settings = Settings()
        app._ai_cancel = threading.Event()
        app._ai_busy = threading.Lock()
        app._ai_busy.acquire()
        app._grabber = mock.Mock()
        app._grabber.grab.return_value = copied
        app._grabber.grab_context.side_effect = [prefix, suffix]
        app._scope_allows = mock.Mock(return_value=True)
        app._injection_block_reason = mock.Mock(return_value="")
        app._notify_safe = mock.Mock()
        app._notifier = None
        app._stream_updater = mock.Mock(return_value=None)
        app._pipeline = mock.Mock()
        app._pipeline.input_revision = 0
        backend = mock.Mock()
        backend.complete.return_value = "rewritten;"
        backend.complete_fim.return_value = "inserted;"
        if type_during_request:
            def finish_after_typing(*args, **kwargs):
                app._pipeline.input_revision += 1
                return "old context;"
            backend.complete_fim.side_effect = finish_after_typing
        with mock.patch("codepoint.app.editor_context.read", return_value=context), \
                mock.patch("codepoint.app.foreground.query", return_value=WindowInfo(
                    hwnd=101, pid=100, process_name=process_name)), \
                mock.patch("codepoint.app.focus_state.query", return_value=FocusState(hwnd=101, focus_hwnd=102)), \
                mock.patch("codepoint.app.create_completer", return_value=backend):
            app._ai_selection_worker(101)
        return app, backend

    def test_read_only_range_goes_to_model_without_copying(self):
        from codepoint.platform_win.editor_context import EditorContext

        app, backend = self._run_worker(EditorContext(prefix="x := ", suffix=";"))
        app._grabber.grab.assert_not_called()
        self.assertEqual(backend.complete_fim.call_args.args[:2], ("x := ", ";"))
        app._pipeline.offer.assert_called_once()

    def test_typing_in_same_editor_during_generation_discards_result(self):
        from codepoint.platform_win.editor_context import EditorContext

        app, backend = self._run_worker(EditorContext(prefix="x := ", suffix=";"),
                                        type_during_request=True)
        backend.complete_fim.assert_called_once()
        app._pipeline.offer.assert_not_called()

    def test_st_editors_use_legacy_capture_when_text_ranges_are_unavailable(self):
        for process_name in ("invtmatic studio.exe", "codesys.exe"):
            with self.subTest(process_name=process_name):
                app, backend = self._run_worker(None, process_name=process_name,
                                                prefix=GrabResult(text="x := "), suffix=GrabResult(text=";"))
                calls = app._grabber.grab_context.call_args_list
                self.assertEqual([(c.kwargs["below"], c.kwargs["collapse_to_anchor"]) for c in calls],
                                 [(False, True), (True, True)])
                self.assertEqual(backend.complete_fim.call_args.args[:2], ("x := ", ";"))
                app._pipeline.offer.assert_called_once()

    def test_unsupported_editor_uses_keyboard_context(self):
        app, backend = self._run_worker(None, prefix=GrabResult(text="x := "),
                                        suffix=GrabResult(text=";"))
        calls = app._grabber.grab_context.call_args_list
        self.assertEqual([(c.args[0], c.kwargs["below"]) for c in calls], [(6, False), (3, True)])
        self.assertEqual(backend.complete_fim.call_args.args[:2], ("x := ", ";"))
        app._pipeline.offer.assert_called_once()

    def test_uncertain_caret_blocks_model_and_suffix_capture(self):
        app, backend = self._run_worker(None, prefix=GrabResult(text="x := ", caret_uncertain=True))
        app._grabber.grab_context.assert_called_once()
        self.assertEqual(backend.method_calls, [])
        app._pipeline.offer.assert_not_called()

    def test_existing_selection_still_works_in_unsupported_editor(self):
        app, backend = self._run_worker(None, copied=GrabResult(text="chosen;"))
        backend.complete.assert_called_once_with("chosen;", on_delta=None)
        app._pipeline.offer.assert_called_once()

    def test_existing_whitespace_selection_is_not_used_as_a_context_anchor(self):
        app, backend = self._run_worker(None, copied=GrabResult(text="\r\n"))
        app._grabber.grab_context.assert_not_called()
        self.assertEqual(backend.method_calls, [])

    def test_st_blank_line_copy_continues_with_surrounding_context(self):
        for process_name in ("invtmatic studio.exe", "codesys.exe"):
            with self.subTest(process_name=process_name):
                app, backend = self._run_worker(
                    None, process_name=process_name, copied=GrabResult(text="\t \r\n"),
                    prefix=GrabResult(text="CheckANDConfig := TRUE;\r\n\r\n"),
                    suffix=GrabResult(text="\r\n// 下一段"))
                backend.complete.assert_not_called()
                self.assertEqual(backend.complete_fim.call_args.args[:2],
                                 ("CheckANDConfig := TRUE;\r\n\r\n", "\r\n// 下一段"))
                app._notify_safe.assert_not_called()
                app._pipeline.offer.assert_called_once()


class TestCompletionRouting(unittest.TestCase):
    def setUp(self):
        from codepoint.app import App
        self.app = App.__new__(App)
        self.app.settings = Settings(ai_fim_strategy="server")
        self.backend = mock.Mock()

    def test_blank_suffix_goes_to_chat_even_if_server_fim_is_selected(self):
        self.backend.complete_lines.return_value = "42;"
        text = self.app._complete_context(self.backend, "x := ", "\r\n", None, use_fim=True)
        self.assertEqual(text, "42;")
        self.backend.complete_lines.assert_called_once_with("x := ", on_delta=None)
        self.backend.complete_fim.assert_not_called()

    def test_fim_only_endpoint_still_completes_with_a_blank_suffix(self):
        from urllib.error import HTTPError
        self.backend.complete_lines.side_effect = HTTPError("http://x", 404, "Not Found", {}, None)
        self.backend.complete_fim.return_value = "42;"
        self.assertEqual(self.app._complete_context(self.backend, "x := ", "\r\n", None, use_fim=True),
                         "42;")
        self.backend.complete_fim.assert_called_once()

    def test_unsupported_fim_falls_back_and_keeps_suffix(self):
        from codepoint.services.ai_provider import FimUnsupported
        self.backend.complete_fim.side_effect = FimUnsupported("HTTP 404")
        self.backend.complete_lines.return_value = "42"
        self.assertEqual(self.app._complete_context(self.backend, "x := ", ";", None, use_fim=True), "42")
        self.backend.complete_lines.assert_called_once_with("x := ", on_delta=None, suffix=";")

    def test_model_truncation_with_suffix_does_not_restart_generation(self):
        from codepoint.services.ai_provider import AiOutputIncomplete
        self.backend.complete_fim.side_effect = AiOutputIncomplete("token limit")
        with self.assertRaises(AiOutputIncomplete):
            self.app._complete_context(self.backend, "x := ", ";", None, use_fim=True)
        self.backend.complete_lines.assert_not_called()


# ---------------------------------------------------------------- 连接测试


class _OkCompleter:
    def complete(self, prompt, context=""):
        return "OK"

    def describe(self):
        return "fake @ test"


class _BlankCompleter:
    def complete(self, prompt, context=""):
        return "   "

    def describe(self):
        return "blank"


class _BoomCompleter:
    def complete(self, prompt, context=""):
        raise ValueError("模型名写错了")

    def describe(self):
        return "boom"


class _OfflineCompleter:
    def complete(self, prompt, context=""):
        import urllib.error

        raise urllib.error.URLError("Connection refused")

    def describe(self):
        return "offline"


class TestProbe(unittest.TestCase):
    def test_success(self):
        result = ai.probe(_OkCompleter())
        self.assertTrue(result.ok)
        self.assertIn("返回 2 字", result.detail)

    def test_mock_is_reported_as_not_testable(self):
        # 演练模式永远"成功"，不说明任何事 —— 必须如实说"没有可测的连接"
        result = ai.probe(ai.MockCompleter(delay=0))
        self.assertFalse(result.ok)
        self.assertIn("演练模式", result.detail)

    def test_blank_reply_mentions_model_name(self):
        # 服务通了但返回空，最常见原因是模型名写错
        result = ai.probe(_BlankCompleter())
        self.assertFalse(result.ok)
        self.assertIn("模型名", result.detail)

    def test_value_error_surfaces_message(self):
        result = ai.probe(_BoomCompleter())
        self.assertFalse(result.ok)
        self.assertIn("模型名写错了", result.detail)

    def test_urlerror_suggests_checking_service(self):
        result = ai.probe(_OfflineCompleter())
        self.assertFalse(result.ok)
        self.assertIn("连不上", result.detail)

    def test_http_error_reports_status(self):
        import io
        import urllib.error

        class _HttpBoom:
            def complete(self, prompt, context=""):
                raise urllib.error.HTTPError(
                    "http://x", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

            def describe(self):
                return "http"

        result = ai.probe(_HttpBoom())
        self.assertFalse(result.ok)
        self.assertIn("401", result.detail)

    def test_probe_uses_its_own_system_prompt(self):
        """测连接不能套用"只输出 ST 代码"那套 system —— 那会把 OK 当代码处理。"""
        completer = ai.OpenAiCompleter(base_url="http://127.0.0.1:8080/v1", model="m")
        probe_target = completer.with_system(ai._PROBE_SYSTEM)
        self.assertEqual(probe_target._system, ai._PROBE_SYSTEM)
        self.assertEqual(probe_target._max_tokens, 24)
        self.assertEqual(completer._system, ai.DEFAULT_SYSTEM)   # 原件没被改


# ---------------------------------------------------------------- 配置


class TestHotkeySetting(unittest.TestCase):
    def test_default_hotkey(self):
        s = Settings()
        self.assertEqual(s.ai_hotkey, "Ctrl+Alt+G")

    def test_roundtrip(self):
        s = Settings()
        s.ai_hotkey = "Ctrl+Shift+F12"
        self.assertEqual(Settings.from_dict(s.to_dict()).ai_hotkey, "Ctrl+Shift+F12")

    def test_empty_hotkey_means_disabled(self):
        s = Settings()
        s.ai_hotkey = "   "
        s.normalize()
        self.assertEqual(s.ai_hotkey, "")


if __name__ == "__main__":
    unittest.main()
