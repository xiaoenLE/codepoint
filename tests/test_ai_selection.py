"""选中文本抓取（Ctrl+Alt+G 的第一步）与 AI 连接测试。

全部用假剪贴板 / 假时钟，不真按 Ctrl+C，也不联网 —— 断网、没装模型都能跑。
"""

from __future__ import annotations

import unittest

from codepoint.core.models import Settings
from codepoint.services import ai_provider as ai
from codepoint.services.selection import SelectionGrabber


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

    def select_lines_up(self, count: int) -> None:
        self.order.append(f"up{count}")

    def select_lines_down(self, count: int) -> None:
        self.order.append(f"down{count}")

    def cancel_selection(self, to_start: bool = False) -> None:
        # to_start=True 是"向下选"之后用的：左端才是原来的光标位置
        self.order.append("left" if to_start else "right")


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


# ---------------------------------------------------------------- 上下文抓取


class TestGrabLines(unittest.TestCase):
    """抓"光标前 N 行"—— 没 UIA 的编辑器（CODESYS）唯一能取上下文的办法。"""

    def test_full_sequence(self):
        clipboard = FakeClipboard("旧剪贴板")
        copier = FakeCopier(clipboard, payload="第1行\n第2行\n第3行\n")
        result = _grabber(clipboard, copier).grab_lines(3)

        self.assertTrue(result.ok)
        self.assertIn("第2行", result.text)
        # 顺序固定：先等修饰键松开，再选中、复制，最后取消选区
        self.assertEqual(copier.order, ["wait", "up3", "copy", "right"])
        # 用户原来的剪贴板要还回去
        self.assertEqual(clipboard.text, "旧剪贴板")

    def test_nothing_copied_still_cancels_selection(self):
        """没抓到也必须按 Right —— 否则用户屏幕上留着一片蓝汪汪的选区。"""
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload=None)
        result = _grabber(clipboard, copier).grab_lines(5)

        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertIn("right", copier.order)

    def test_copy_failure_still_cancels_selection(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="x", fail=True)
        result = _grabber(clipboard, copier).grab_lines(4)

        self.assertFalse(result.ok)
        self.assertIn("right", copier.order, "按 Ctrl+C 失败也要把选区收掉")

    def test_modifiers_stuck_does_nothing(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, ready=False)
        result = _grabber(clipboard, copier).grab_lines(3)

        self.assertTrue(result.modifiers_stuck)
        self.assertEqual(copier.order, ["wait"], "没松手就什么都不该按")

    def test_zero_lines_is_noop(self):
        clipboard = FakeClipboard("旧")
        copier = FakeCopier(clipboard, payload="x")
        result = _grabber(clipboard, copier).grab_lines(0)

        self.assertFalse(result.ok)
        self.assertEqual(copier.order, [], "0 行就别碰键盘了")


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
