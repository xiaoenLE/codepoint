"""AI 补全的测试：清洗、端点拼装、提示词、前缀匹配、异步链路。

这些测试全部**不联网**：HTTP 那一层用假 urlopen 顶替，
所以断网、没有模型也能跑。
"""

from __future__ import annotations

import io
import json
import threading
import time
import unittest
from unittest import mock

from codepoint.core.events import KeyPress, Modifiers, WindowInfo
from codepoint.core.models import Settings
from codepoint.platform_win.keylayout import VK_TAB
from codepoint.services import ai_provider as ai
from codepoint.services import quick_action as qa
from codepoint.services.app_filter import AppFilter
from codepoint.services.pipeline import KeyboardPipeline
from codepoint.services.quick_action import QuickActionEngine
from codepoint.core.models import MATCH_ALL


# ---------------------------------------------------------------- 清洗


class TestCleanCode(unittest.TestCase):
    def test_strips_markdown_fence(self):
        raw = "```st\nVAR\n    x : INT;\nEND_VAR\n```"
        self.assertEqual(ai.clean_code(raw), "VAR\n    x : INT;\nEND_VAR")

    def test_drops_preamble_before_code(self):
        raw = "下面是一个 5 秒延时启动的定时器：\n\nVAR\n    fb : TON;\nEND_VAR"
        self.assertEqual(ai.clean_code(raw), "VAR\n    fb : TON;\nEND_VAR")

    def test_removes_common_indent(self):
        # 模型爱整体缩进一级；编辑器还会再缩一次，不剥掉就越来越深
        raw = "    VAR\n        x : INT;\n    END_VAR"
        self.assertEqual(ai.clean_code(raw), "VAR\n    x : INT;\nEND_VAR")

    def test_keeps_relative_indent(self):
        raw = "IF a THEN\n    b := 1;\nEND_IF"
        self.assertEqual(ai.clean_code(raw), raw)

    def test_empty_input(self):
        self.assertEqual(ai.clean_code(""), "")
        self.assertEqual(ai.clean_code("   \n\n "), "")

    def test_crlf_normalized(self):
        self.assertEqual(ai.clean_code("VAR\r\nEND_VAR"), "VAR\nEND_VAR")

    def test_truncates_runaway_output(self):
        raw = "\n".join(f"x{i} := {i};" for i in range(400))
        out = ai.clean_code(raw)
        self.assertLessEqual(len(out.split("\n")), ai._MAX_LINES)

    def test_assignment_start_is_code(self):
        # `fbTimer(IN := TRUE);` 这种不是关键字开头，但也不该被前置行砍掉
        raw = "说明文字\nfbTimer(IN := TRUE, PT := T#5S);"
        self.assertEqual(ai.clean_code(raw), "fbTimer(IN := TRUE, PT := T#5S);")

    def test_python_start_is_code(self):
        raw = "下面是冒泡排序：\n\ndef bubble_sort(arr):\n    return arr"
        self.assertEqual(ai.clean_code(raw), "def bubble_sort(arr):\n    return arr")

    def test_c_start_is_code(self):
        raw = "这是一个 C 函数：\n\n#include <stdio.h>\n\nint main(void) {\n    return 0;\n}"
        self.assertEqual(
            ai.clean_code(raw),
            "#include <stdio.h>\n\nint main(void) {\n    return 0;\n}")

    def test_python_preamble_not_mistaken_for_code(self):
        # 解说里的中文行不能被当成代码起点
        raw = "这段代码会打印九九乘法表。\nprint('hello')"
        self.assertEqual(ai.clean_code(raw), "print('hello')")


# ---------------------------------------------------------------- 端点


class TestEndpoint(unittest.TestCase):
    def test_plain_host_gets_v1(self):
        self.assertEqual(
            ai.endpoint_of("https://api.deepseek.com"),
            "https://api.deepseek.com/v1/chat/completions")

    def test_version_tail(self):
        self.assertEqual(
            ai.endpoint_of("http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1/chat/completions")

    def test_zhipu_v4(self):
        self.assertEqual(
            ai.endpoint_of("https://open.bigmodel.cn/api/paas/v4"),
            "https://open.bigmodel.cn/api/paas/v4/chat/completions")

    def test_full_endpoint_untouched(self):
        url = "http://127.0.0.1:8080/v1/chat/completions"
        self.assertEqual(ai.endpoint_of(url), url)

    def test_trailing_slash(self):
        self.assertEqual(
            ai.endpoint_of("http://127.0.0.1:8080/v1/"),
            "http://127.0.0.1:8080/v1/chat/completions")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            ai.endpoint_of("   ")


# ---------------------------------------------------------------- 提示词


class TestMessages(unittest.TestCase):
    def test_template_substitutes_prompt(self):
        msgs = ai.build_messages("定时器", template="生成 ST：${prompt}")
        self.assertEqual(msgs[1]["content"], "生成 ST：定时器")
        self.assertEqual(msgs[0]["role"], "system")

    def test_empty_template_falls_back_to_prompt(self):
        msgs = ai.build_messages("定时器", template="")
        self.assertEqual(msgs[1]["content"], "定时器")

    def test_context_appended(self):
        msgs = ai.build_messages("加个互锁", context="VAR\n  x : BOOL;\nEND_VAR")
        self.assertIn("x : BOOL", msgs[1]["content"])
        self.assertIn("不要重复输出", msgs[1]["content"])

    def test_context_trimmed(self):
        msgs = ai.build_messages("x", context="A" * 9000)
        self.assertLess(len(msgs[1]["content"]), 7000)

    def test_selected_code_is_treated_as_a_replacement(self):
        msgs = ai.build_messages("    output := input;")
        self.assertIn("返回完整的替换片段", msgs[0]["content"])
        self.assertIn("选中的是需求时", msgs[0]["content"])


# ---------------------------------------------------------------- 补全提示词


class TestCompletionMessages(unittest.TestCase):
    """「接着往下写」和「给我写一段」是两套提示词，不能混。"""

    @staticmethod
    def _last_user(messages):
        # 真实用户提示词在最后一条 user（前面是 few-shot 样例）
        user_msgs = [m for m in messages if m["role"] == "user"]
        return user_msgs[-1]["content"]

    def test_includes_context(self):
        msgs = ai.build_completion_messages("IO_Map.yOutput[4, 0] := 1;\n", 6)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("IO_Map.yOutput[4, 0] := 1;", self._last_user(msgs))

    def test_states_the_line_limit(self):
        msgs = ai.build_completion_messages("x := 1;", 4)
        self.assertIn("最多 4 行", self._last_user(msgs))

    def test_includes_few_shot(self):
        # 数组规律示例只用于已识别的数组映射。
        msgs = ai.build_completion_messages("a[0] := y1;\na[1] := y2;", 6)
        roles = [m["role"] for m in msgs]
        self.assertIn("assistant", roles)
        last_user = max(i for i, r in enumerate(roles) if r == "user")
        first_assistant = roles.index("assistant")
        self.assertLess(first_assistant, last_user)

    def test_generic_code_has_no_st_examples(self):
        msgs = ai.build_completion_messages("def read_file(path):\n    with open(path) as f:", 6)
        self.assertEqual([message["role"] for message in msgs], ["system", "user"])

    def test_system_tells_it_not_to_repeat(self):
        self.assertIn("不要重复", ai.COMPLETION_SYSTEM)
        self.assertIn("索引递增", ai.COMPLETION_SYSTEM)

    def test_card_carries_timeout(self):
        from codepoint.core.card import Card

        card = Card.ready("AI 补全", "x := 1;", timeout=12)
        self.assertEqual(card.timeout, 12)
        self.assertTrue(card.actionable)

        # busy 卡片不该有倒计时（它不是在等你操作）
        self.assertEqual(Card.busy("AI", "…").timeout, 0)


class TestIndexPatternHint(unittest.TestCase):
    """二维下标排满一组时，提示词里要直接写明下一组从哪开始。

    这条是实测出来的：只给规则，qwen2.5-coder:1.5b 会把 [1, 7] 续成 [1, 8]、
    [1, 15]；把规律算好写进提示词，同一份输入它就能写对 [2, 0] 并且把 y2 改成 y3。
    """

    FULL = "\n".join([
        "IO_Map.yOutput[1, 0] := y2报警输出;",
        "IO_Map.yOutput[1, 1] := y2匀速输出;",
        "IO_Map.yOutput[1, 2] := y2张力输出;",
        "IO_Map.yOutput[1, 3] := y2张力输出;",
        "IO_Map.yOutput[1, 4] := y2张力输出;",
        "IO_Map.yOutput[1, 5] := y2张力输出;",
        "IO_Map.yOutput[1, 6] := y2张力输出;",
        "IO_Map.yOutput[1, 7] := y2张力输出;",
    ])

    def test_switches_to_next_axis(self):
        hint = ai.index_pattern_hint(self.FULL)
        self.assertIn("[2, 0]", hint)
        self.assertIn("不要写 [1, 8]", hint)

    def test_follows_the_offset_between_axis_and_name(self):
        # 轴号 1 配的是 y2，所以下一轴的名字是 y3，不是 y2
        hint = ai.index_pattern_hint(self.FULL)
        self.assertIn("2 要改成 3", hint)

    def test_silent_without_pattern(self):
        self.assertEqual(
            ai.index_pattern_hint("IF bStart THEN\n    x := 1;\nEND_IF"), "")

    def test_silent_when_group_is_not_full(self):
        ctx = "\n".join(f"IO_Map.yOutput[1, {i}] := TRUE;" for i in range(4))
        self.assertEqual(ai.index_pattern_hint(ctx), "")

    def test_silent_when_first_dim_keeps_changing(self):
        ctx = "\n".join(f"IO_Map.yOutput[{i}, 0] := TRUE;" for i in range(8))
        # 第二维固定、第一维递增：这是另一条规律，不该套用"换轴"的说法
        self.assertNotIn("[2, 0]", ai.index_pattern_hint(ctx))

    def test_hint_reaches_the_user_message(self):
        msgs = ai.build_completion_messages(self.FULL, 8)
        # few-shot 样例插在真实提示词之前，所以真实的那条永远是最后一条
        self.assertIn("[2, 0]", msgs[-1]["content"])
        self.assertIn("不要重复上文", msgs[-1]["content"])

    def test_seed_is_the_first_line_of_the_next_group(self):
        seed = ai.index_pattern_seed(self.FULL)
        self.assertEqual(seed, "IO_Map.yOutput[2, 0] := y3报警输出;")

    def test_seed_takes_the_opening_of_the_group_not_the_last_line(self):
        # 这一组第 3 路起都是"张力"，种子行必须取第 0 路的"报警"，
        # 否则整组会从"张力"写起，语义就错了
        self.assertIn("报警", ai.index_pattern_seed(self.FULL))
        self.assertNotIn("张力", ai.index_pattern_seed(self.FULL))

    def test_seed_empty_without_pattern(self):
        self.assertEqual(ai.index_pattern_seed("IF bStart THEN\n    x := 1;\nEND_IF"), "")

    def test_seed_lands_in_the_user_message(self):
        msgs = ai.build_completion_messages(self.FULL, 8)
        self.assertIn("IO_Map.yOutput[2, 0] := y3报警输出;", msgs[-1]["content"])
        self.assertIn("写满 8 行", msgs[-1]["content"])


class TestOneDimensionIndex(unittest.TestCase):
    """一维下标连续递增（yOutput[0] := y1 / yOutput[1] := y2）同样要给出多行。

    两行起就认 —— 这是"刚敲两行、等它补齐后面"的典型场景。
    """

    TWO = "yOutput[0] := y1;\nyOutput[1] := y2;"

    def test_hint_explains_offset(self):
        hint = ai.index_pattern_hint(self.TWO)
        self.assertIn("索引从 0 递增到 1", hint)
        self.assertIn("[2]", hint)

    def test_seed_is_next_index_and_next_name(self):
        self.assertEqual(ai.index_pattern_seed(self.TWO), "yOutput[2] := y3;")

    def test_two_lines_are_enough(self):
        # 三维场景要求 3 行起，一维只要有 2 行
        self.assertTrue(ai.index_pattern_seed(self.TWO))

    def test_no_pattern_for_plain_assignments(self):
        self.assertEqual(ai.index_pattern_seed("a := 1;\nb := 2;"), "")

    def test_broken_index_sequence_is_ignored(self):
        self.assertEqual(ai.index_pattern_seed("a[0] := x;\na[5] := y;"), "")

    def test_ensure_seed_prepends_when_model_skipped_it(self):
        out = ai.ensure_seed_line(self.TWO, "yOutput[3] := y4;")
        self.assertTrue(out.startswith("yOutput[2] := y3;"))

    def test_ensure_seed_does_not_duplicate(self):
        out = ai.ensure_seed_line(self.TWO, "yOutput[2] := y3;\nyOutput[3] := y4;")
        self.assertEqual(out.count("yOutput[2]"), 1)

    def test_ensure_seed_repairs_missing_colon(self):
        # 0.5b 会写成 yOutput[2] = y3;，既不能重复补行，也要把 := 修回来
        out = ai.ensure_seed_line(self.TWO, "yOutput[2] = y3;\nyOutput[3] = y4;")
        self.assertEqual(out, "yOutput[2] := y3;\nyOutput[3] := y4;")

    def test_ensure_seed_leaves_other_code_alone(self):
        self.assertEqual(ai.ensure_seed_line("IF a THEN\n  b := 1;\nEND_IF", "x := 2;"),
                         "x := 2;")

    def test_garbage_lines_are_dropped(self):
        # 0.5b 偶尔会吐出裸数字行；认出规律时只保留下标赋值
        out = ai.ensure_seed_line(self.TWO, "yOutput[3] := y4;\n1\n2\n3")
        self.assertEqual(out, "yOutput[2] := y3;\nyOutput[3] := y4;")

    def test_trim_caps_runaway_output(self):
        # 实测 0.5b 在 4 行上文时会写 24 行，必须硬截断
        text = "\n".join(f"yOutput[{i}] := y{i};" for i in range(4, 30))
        out = ai._trim_lines(text, 6)
        self.assertEqual(len(out.splitlines()), 6)
        self.assertTrue(out.startswith("yOutput[4]"))


class TestNumberSequence(unittest.TestCase):
    """光标停在"一行一个数字"的连续编号后面时，该接编号，而不是回头补数组。"""

    MIXED = ("yOutput[0] := y1;\nyOutput[1] := y2;\nyOutput[2] := y3;\n"
             "yOutput[3] := y4;\n1\n2\n3\n4")

    def test_tail_wins_over_earlier_array(self):
        self.assertEqual(ai.index_pattern_seed(self.MIXED), "5")

    def test_hint_mentions_next_number(self):
        self.assertIn("下一行是 5", ai.index_pattern_hint(self.MIXED))

    def test_array_is_not_suggested_when_cursor_moved_on(self):
        # 光标的上一行是 "4"，不该再提示 yOutput[4]
        self.assertNotIn("yOutput[4]", ai.index_pattern_hint(self.MIXED))

    def test_generates_whole_block(self):
        out = ai.ensure_seed_line(self.MIXED, "yOutput[4] := y5;", max_lines=6)
        self.assertEqual(out, "5\n6\n7\n8\n9\n10")

    def test_single_number_line_is_not_enough(self):
        self.assertEqual(ai.index_pattern_seed("x := 1;\n7"), "")


# ---------------------------------------------------------------- 演练实现


class TestMock(unittest.TestCase):
    def test_keyword_picks_snippet(self):
        self.assertIn("TON", ai.mock_reply("来个延时定时器"))
        self.assertIn("互锁", ai.mock_reply("电机正反转互锁"))
        self.assertIn("PID", ai.mock_reply("pid 闭环"))

    def test_fallback_is_valid_st(self):
        out = ai.mock_reply("随便什么")
        self.assertIn("CASE", out)

    def test_completer_returns_text(self):
        self.assertTrue(ai.MockCompleter(delay=0).complete("定时器"))


# ---------------------------------------------------------------- HTTP


def _fake_response(payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return _Resp(body)


class TestOpenAiCompleter(unittest.TestCase):
    def _completer(self, **kw):
        opts = dict(base_url="http://127.0.0.1:8080/v1", model="m")
        opts.update(kw)
        return ai.OpenAiCompleter(**opts)

    def test_parses_content_and_cleans(self):
        payload = {"choices": [{"message": {"content": "```st\nVAR\nEND_VAR\n```"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as urlopen:
            out = self._completer().complete("定时器")
        self.assertEqual(out, "VAR\nEND_VAR")
        sent = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["model"], "m")
        self.assertFalse(sent["stream"])
        self.assertEqual(sent["messages"][0]["role"], "system")

    def test_auth_header_only_when_key_present(self):
        payload = {"choices": [{"message": {"content": "x := 1;"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as urlopen:
            self._completer(api_key="sk-123").complete("x")
        headers = urlopen.call_args[0][0].headers
        self.assertEqual(headers.get("Authorization"), "Bearer sk-123")

    def test_error_payload_raises(self):
        payload = {"error": {"message": "invalid api key"}}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            with self.assertRaises(ValueError):
                self._completer().complete("x")

    def test_empty_choices_returns_blank(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response({"choices": []})):
            self.assertEqual(self._completer().complete("x"), "")

    def test_missing_model_raises(self):
        with self.assertRaises(ValueError):
            self._completer(model="").complete("x")


# ---------------------------------------------------------------- 流式


def _fake_sse(chunks: list, *, done: bool = True):
    """把若干 delta 拼成 SSE 字节流（假 urlopen 用）。"""
    lines = []
    for piece in chunks:
        lines.append("data: " + json.dumps(piece, ensure_ascii=False) + "\n\n")
    if done:
        lines.append("data: [DONE]\n\n")
    body = "".join(lines).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return _Resp(body)


def _delta(content=None, reasoning=None):
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning_content"] = reasoning
    return {"choices": [{"delta": d}]}


class TestStreaming(unittest.TestCase):
    def _completer(self, **kw):
        opts = dict(base_url="http://127.0.0.1:8080/v1", model="m")
        opts.update(kw)
        return ai.OpenAiCompleter(**opts)

    def test_streams_accumulated_text(self):
        """回调收到的必须是**累积**正文，方便界面整块重画。"""
        got = []
        resp = _fake_sse([_delta("VAR"), _delta("\n"), _delta("END_VAR")])
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            out = self._completer().complete("x", on_delta=got.append)
        self.assertEqual(out, "VAR\nEND_VAR")
        self.assertEqual(got, ["VAR", "VAR\n", "VAR\nEND_VAR"])
        sent = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertTrue(sent["stream"], "流式路径必须开 stream")

    def test_non_stream_call_still_works(self):
        """不传 on_delta 就走整块路径 —— 测试连接/自检都靠它。"""
        payload = {"choices": [{"message": {"content": "x := 1;"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as urlopen:
            out = self._completer().complete("x")
        self.assertEqual(out, "x := 1;")
        self.assertFalse(json.loads(urlopen.call_args[0][0].data.decode("utf-8"))["stream"])

    def test_reasoning_deltas_are_discarded(self):
        """推理模型的思考过程直接扔掉 —— 用户要的是代码，不是思考。"""
        got = []
        resp = _fake_sse([
            _delta(reasoning="让我想想"),
            _delta(reasoning="再想想"),
            _delta("x := 1;"),
        ])
        with mock.patch("urllib.request.urlopen", return_value=resp):
            out = self._completer().complete("x", on_delta=got.append)
        self.assertEqual(out, "x := 1;")
        self.assertEqual(got, ["x := 1;"], "思考内容一个字都不能漏给界面")

    def test_plain_json_fallback(self):
        """服务端不认 stream（整块回 JSON）时自动退化，调用方无感。"""
        got = []
        payload = {"choices": [{"message": {"content": "a := 2;"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            out = self._completer().complete("x", on_delta=got.append)
        self.assertEqual(out, "a := 2;")
        self.assertEqual(got, ["a := 2;"])

    def test_stream_error_payload_raises(self):
        resp = _fake_sse([{"error": {"message": "quota"}}])
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(ValueError):
                self._completer().complete("x", on_delta=lambda t: None)

    def test_mock_streams_by_lines(self):
        """演练模式也要走流式 —— 否则界面那条链路永远验不到。"""
        got = []
        out = ai.MockCompleter(delay=0).complete_lines(
            "yOutput[4, 0]", on_delta=got.append)
        self.assertTrue(out.strip())
        self.assertGreater(len(got), 1, "应该分多段推，不是一次性给")
        self.assertEqual(got[-1], out, "最后一段必须等于完整正文")
        self.assertEqual(sorted(got, key=len), got, "累积正文必须单调变长")

    def test_complete_lines_streams_too(self):
        got = []
        resp = _fake_sse([_delta("IO_Map"), _delta(".x := 1;")])
        with mock.patch("urllib.request.urlopen", return_value=resp):
            out = self._completer().complete_lines(
                "ctx", 4, on_delta=got.append)
        self.assertEqual(out, "IO_Map.x := 1;")
        self.assertEqual(len(got), 2)

    def test_plain_number_sequences_keep_their_separator_without_request(self):
        for prefix, expected in (
            ("x := 123;\n4 5 6 7 8", " 9 10 11"),
            ("4 5 6 7 8 ", "9 10 11"),
            ("  6\r\n  7\r\n  8", "\n  9\n  10\n  11"),
        ):
            with self.subTest(prefix=prefix), mock.patch("urllib.request.urlopen") as opened:
                out = self._completer().complete_lines(prefix, 3, suffix="\r\n \t")
                opened.assert_not_called()
                self.assertEqual(out, expected)

    def test_chat_completion_uses_both_sides_and_keeps_only_the_gap(self):
        prefix = "def run():\n    prepare()\n"
        suffix = "\n    return result"
        gap = "    result = work()"
        payload = {"choices": [{"message": {"content": prefix + gap + suffix}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as opened:
            output = self._completer().complete_lines(prefix, suffix=suffix)
        request = json.loads(opened.call_args.args[0].data)
        self.assertIn(f"<prefix>{prefix}</prefix>", request["messages"][-1]["content"])
        self.assertIn(f"<suffix>{suffix}</suffix>", request["messages"][-1]["content"])
        self.assertEqual(output, gap)

    def test_selected_code_output_keeps_its_indent(self):
        expected = "    value = read()\n    return value"
        payload = {"choices": [{"message": {"content": f"```python\n{expected}\n```"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            self.assertEqual(self._completer().complete("    return value"), expected)


class TestDisableThinking(unittest.TestCase):
    """关思考：推理模型补全慢 5 倍的元凶（实测 MiMo 首字 9.7s → 1.7s）。"""

    def _completer(self, **kw):
        opts = dict(base_url="http://127.0.0.1:8080/v1", model="m",
                    disable_thinking=True)
        opts.update(kw)
        return ai.OpenAiCompleter(**opts)

    def test_sends_thinking_disabled(self):
        payload = {"choices": [{"message": {"content": "x := 1;"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as urlopen:
            self._completer().complete("x")
        sent = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["thinking"], {"type": "disabled"})

    def test_no_thinking_field_when_disabled_off(self):
        payload = {"choices": [{"message": {"content": "x := 1;"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as urlopen:
            self._completer(disable_thinking=False).complete("x")
        sent = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertNotIn("thinking", sent)

    def test_retries_without_field_when_server_rejects(self):
        """老服务不认 thinking 字段时自动去掉重试，不能因此挂掉。"""
        calls = []

        def fake_urlopen(req, **_kw):
            calls.append(json.loads(req.data.decode("utf-8")))
            if "thinking" in calls[-1]:
                raise ValueError("unknown field 'thinking'")
            return _fake_response({"choices": [{"message": {"content": "x := 1;"}}]})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = self._completer().complete("x")
        self.assertEqual(out, "x := 1;")
        self.assertEqual(len(calls), 2, "第一次带 thinking 被拒，第二次去掉重试")

    def test_settings_roundtrip(self):
        s = Settings()
        self.assertTrue(s.ai_disable_thinking, "默认开 —— 补全不需要思考")
        s.ai_disable_thinking = False
        s2 = Settings.from_dict(s.to_dict())
        self.assertFalse(s2.ai_disable_thinking)


class TestProbeReasoningModel(unittest.TestCase):
    """推理模型经常 content 为空但 reasoning 一大段 —— 那也是通了。

    真实事故：MiMo/DeepSeek flash 测连接报"内容是空的，检查模型名"，
    其实服务正常，只是思考把 token 吃光了。
    """

    def _completer(self, **kw):
        opts = dict(base_url="http://127.0.0.1:8080/v1", model="m")
        opts.update(kw)
        return ai.OpenAiCompleter(**opts)

    def test_reasoning_only_counts_as_ok(self):
        payload = {"choices": [{"message": {"content": "",
                                            "reasoning_content": "让我想想……"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            result = ai.probe(self._completer())
        self.assertTrue(result.ok, "只有思考内容也该算连通")
        self.assertIn("推理模型", result.detail)

    def test_empty_everything_fails(self):
        payload = {"choices": [{"message": {"content": ""}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            result = ai.probe(self._completer())
        self.assertFalse(result.ok)

    def test_content_wins(self):
        payload = {"choices": [{"message": {"content": "OK"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            result = ai.probe(self._completer())
        self.assertTrue(result.ok)
        self.assertNotIn("推理模型", result.detail)

    def test_probe_uses_large_max_tokens(self):
        """推理模型要先思考，max_tokens 给小了永远轮不到正文。"""
        payload = {"choices": [{"message": {"content": "OK"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as urlopen:
            self._completer().probe_meta("hi")
        sent = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertGreaterEqual(sent["max_tokens"], 200)


class TestFactory(unittest.TestCase):
    def test_mock_by_default(self):
        s = Settings()
        self.assertIsInstance(ai.create_completer(s), ai.MockCompleter)

    def test_openai_when_configured(self):
        s = Settings()
        s.ai_provider = "openai"
        s.ai_base_url = "http://127.0.0.1:8080/v1"
        s.ai_model = "qwen"
        self.assertIsInstance(ai.create_completer(s), ai.OpenAiCompleter)

    def test_missing_address_reports_configuration_error(self):
        s = Settings(ai_provider="openai", ai_base_url="")
        with self.assertRaisesRegex(ai.AiConfigurationError, "请输入 AI 服务地址"):
            ai.create_completer(s)

    def test_malformed_address_reports_configuration_error(self):
        s = Settings(ai_provider="openai", ai_base_url="localhost:11434/v1")
        with self.assertRaisesRegex(ai.AiConfigurationError, "http:// 或 https://"):
            ai.create_completer(s)

    def test_missing_model_reports_configuration_error(self):
        s = Settings(ai_provider="openai", ai_model=" ")
        with self.assertRaisesRegex(ai.AiConfigurationError, "请输入 AI 模型名"):
            ai.create_completer(s)


# ---------------------------------------------------------------- 挂起 / 确认


class _RecordNotifier:
    def __init__(self):
        self.shown: list = []
        self.cards: list = []
        self.hidden = 0

    def show(self, action):
        self.shown.append(action)

    def show_card(self, card):
        self.cards.append(card)

    def hide(self):
        self.hidden += 1

    @property
    def last_card(self):
        return self.cards[-1] if self.cards else None


def _press(char: str) -> KeyPress:
    return KeyPress(vk=ord(char) & 0xFFFF, char=char, is_down=True,
                    modifiers=Modifiers())


def _window(hwnd=1001):
    return WindowInfo(hwnd=hwnd, pid=100, process_name="code.exe",
                      window_title="ss.st - Visual Studio Code", thread_id=100)


class _Windows:
    def __init__(self, window):
        self.window = window

    def front(self):
        return self.window

    def self_pid(self):
        return 999


class _Injector:
    def __init__(self):
        self.texts: list = []
        self.backspaces: list = []

    def send_text(self, text):
        self.texts.append(text)

    def send_block(self, text, **_kw):
        self.texts.append(text)

    def send_backspaces(self, count):
        self.backspaces.append(count)

    def send_ime_guard(self):
        pass


class TestPipelineOffer(unittest.TestCase):
    """管道只负责「挂起 → Tab 接受」；生成在 app 的热键线程里做。

    所以这里不再模拟 `//ai` 击键（那条触发方式已删除），而是直接调 offer()。
    """

    def _pipeline(self, window=None):
        self.windows = _Windows(window or _window())
        self.injector = _Injector()
        self.notifier = _RecordNotifier()
        pipe = KeyboardPipeline(
            window_probe=self.windows,
            ime_probe=mock.Mock(state=lambda hwnd: None),
            injector=self.injector,
            notifier=self.notifier,
            app_filter=AppFilter([], mode=MATCH_ALL),
            quick_actions=QuickActionEngine([]),
        )
        self.addCleanup(pipe.close)
        return pipe

    def _tab(self, pipe):
        return pipe.handle(KeyPress(vk=VK_TAB, char="\t", is_down=True))

    def test_offer_passes_timeout_to_card(self):
        """倒计时秒数要跟着卡片一起送到界面层，窗口才知道什么时候自己关。"""
        pipe = self._pipeline()
        pipe.offer("X := 1;", timeout=9)
        self.assertEqual(self.notifier.last_card.timeout, 9)

    def test_offer_shows_ready_card(self):
        pipe = self._pipeline()
        self.assertTrue(pipe.offer("VAR\nEND_VAR"))
        self.assertEqual(self.notifier.last_card.state, "ready")
        self.assertIn("END_VAR", self.notifier.last_card.body)
        self.assertEqual(self.injector.texts, [], "没按 Tab 之前一个字都不能插")

    def test_empty_text_is_rejected(self):
        pipe = self._pipeline()
        self.assertFalse(pipe.offer("   \n "))
        self.assertFalse(pipe.has_pending_text)

    def test_tab_accepts_and_injects(self):
        pipe = self._pipeline()
        pipe.offer("VAR\n    x : INT;\nEND_VAR")
        self.assertTrue(self._tab(pipe), "Tab 要吞掉，否则编辑器会插一个制表符")
        self.assertEqual(self.injector.texts, ["VAR\n    x : INT;\nEND_VAR"])
        self.assertFalse(pipe.has_pending_text)

    def test_backspaces_are_replayed_on_accept(self):
        pipe = self._pipeline()
        pipe.offer("X := 1;", backspaces=7)
        self._tab(pipe)
        self.assertEqual(self.injector.backspaces, [7])

    def test_other_key_discards_pending(self):
        pipe = self._pipeline()
        pipe.offer("X := 1;")
        pipe.handle(_press("a"))
        self.assertFalse(pipe.has_pending_text)
        self.assertEqual(self.injector.texts, [])
        self.assertFalse(self._tab(pipe), "作废之后 Tab 要还给编辑器")

    def test_escape_discards_pending(self):
        pipe = self._pipeline()
        pipe.offer("X := 1;")
        pipe.handle(KeyPress(vk=0x1B, char=None, is_down=True))
        self.assertFalse(pipe.has_pending_text)
        self.assertEqual(self.injector.texts, [])

    def test_tab_without_pending_passes_through(self):
        pipe = self._pipeline()
        self.assertFalse(self._tab(pipe))

    def test_window_switch_drops_on_accept(self):
        pipe = self._pipeline()
        pipe.offer("X := 1;", hwnd=1001)
        self.windows.window = _window(hwnd=2002)
        self.assertFalse(self._tab(pipe), "窗口切换后，Tab 应交还新窗口")
        self.assertEqual(self.injector.texts, [], "窗口变了就别插")
        self.assertEqual(pipe.stats.ai_dropped, 1)

    def test_disabled_pipeline_passes_tab(self):
        pipe = self._pipeline()
        pipe.set_enabled(False)
        self.assertFalse(self._tab(pipe))

    def test_close_clears_pending(self):
        pipe = self._pipeline()
        pipe.offer("X := 1;")
        pipe.close()
        self.assertFalse(pipe.has_pending_text)



# ---------------------------------------------------------------- 配置迁移


class TestConfigMigration(unittest.TestCase):
    def test_retired_prefix_rule_is_dropped(self):
        """v1.3.1 删掉了前缀触发 —— 旧配置里的 //ai 规则必须被清掉。

        不清的话 `from_dict` 会把未知模式回落成「整词」，于是变成
        "敲 //ai 就原样插入 ${prompt}"，比删掉还糟。
        """
        from codepoint.core.config import merge_with_defaults

        old = {"schema": 4, "quick_actions": [
            {"trigger": "//ai", "detect_mode": "prefix",
             "content": "${prompt}", "ai": True, "enabled": True},
            {"trigger": "if", "detect_mode": "word", "content": "IF 1 THEN"},
        ]}
        settings = merge_with_defaults(old)
        self.assertEqual([a.trigger for a in settings.quick_actions], ["if"])

    def test_prefix_rule_dropped_even_from_bare_file(self):
        """导入的裸 JSON（没有 schema 字段）里也不该残留前缀规则。"""
        from codepoint.core.config import merge_with_defaults

        settings = merge_with_defaults({"quick_actions": [
            {"trigger": "//gpt", "detect_mode": "prefix", "content": "${prompt}"},
        ]})
        self.assertEqual(settings.quick_actions, [])

    def test_normal_rules_are_kept(self):
        from codepoint.core.config import merge_with_defaults

        settings = merge_with_defaults({"schema": 5, "quick_actions": [
            {"trigger": "if", "detect_mode": "word", "content": "IF 1 THEN"},
            {"trigger": ":", "detect_mode": "char", "content": ":=", "enabled": False},
        ]})
        self.assertEqual([a.trigger for a in settings.quick_actions], ["if", ":"])


    def test_settings_roundtrip(self):
        s = Settings()
        s.ai_provider = "openai"
        s.ai_base_url = "http://127.0.0.1:8080/v1"
        s.ai_model = "qwen"
        s.ai_api_key = "sk-x"
        s.ai_max_tokens = 600
        restored = Settings.from_dict(s.to_dict())
        self.assertEqual(restored.ai_provider, "openai")
        self.assertEqual(restored.ai_base_url, "http://127.0.0.1:8080/v1")
        self.assertEqual(restored.ai_model, "qwen")
        self.assertEqual(restored.ai_api_key, "sk-x")
        self.assertEqual(restored.ai_max_tokens, 600)

    def test_normalize_clamps_silly_values(self):
        s = Settings()
        s.ai_timeout = -5
        s.ai_temperature = 99
        s.ai_max_tokens = 0
        s.ai_provider = "乱填的"
        s.normalize()
        self.assertEqual(s.ai_timeout, 1.0)
        self.assertEqual(s.ai_temperature, 2.0)
        self.assertEqual(s.ai_max_tokens, 32)
        self.assertEqual(s.ai_provider, "mock")


if __name__ == "__main__":
    unittest.main()
