"""FIM（Fill-in-the-Middle）补全的测试。

和其它 AI 测试一样**不联网**：HTTP 那一层用假 urlopen 顶替。

重点盯三件事，都是真机上踩过的：
  1. 模型族的 FIM 特殊 token 认错 —— 不报错，只补出空行，最难查；
  2. 服务端没有 /completions 时不降级 —— 表现成"按了热键什么都没发生"；
  3. 返回里带上了 suffix 的重复 —— 插进去就是两遍同样的代码。
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from codepoint.services import ai_provider as ai


def _bytes(data: bytes):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return _Resp(data)


def _resp(payload: dict):
    return _bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _text(value: str):
    """非流式 /completions 的返回形状。"""
    return {"choices": [{"text": value}], "usage": {}}


# ---------------------------------------------------------------- token 族


class TestTokenFamilies(unittest.TestCase):
    def test_recognises_known_families(self):
        self.assertEqual(ai.fim_family_of("qwen2.5-coder:1.5b-base"), "qwen")
        self.assertEqual(ai.fim_family_of("deepseek-coder:1.3b-base"), "deepseek")
        self.assertEqual(ai.fim_family_of("codellama:7b-code"), "codellama")
        self.assertEqual(ai.fim_family_of("codegemma:2b"), "codegemma")
        self.assertEqual(ai.fim_family_of("starcoder2:3b"), "starcoder")

    def test_unknown_model_falls_back_to_qwen(self):
        self.assertEqual(ai.fim_family_of("some-random-model"), "qwen")

    def test_qwen_uses_pipe_fence(self):
        self.assertEqual(
            ai.fim_tokens_for("qwen2.5-coder:1.5b-base"),
            ("<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>"))

    def test_deepseek_uses_begin_hole_end(self):
        self.assertEqual(
            ai.fim_tokens_for("deepseek-coder:1.3b-base"),
            ("<fim_begin>", "<fim_hole>", "<fim_end>"))

    def test_manual_override_wins(self):
        self.assertEqual(ai.fim_tokens_for("qwen2.5-coder", "codellama"),
                         ai.FIM_TOKEN_FAMILIES["codellama"])

    def test_garbage_override_falls_back_to_model_name(self):
        self.assertEqual(ai.fim_tokens_for("deepseek-coder", "乱填"),
                         ai.FIM_TOKEN_FAMILIES["deepseek"])


# ---------------------------------------------------------------- 端点


class TestEndpoint(unittest.TestCase):
    def test_appends_completions(self):
        self.assertEqual(
            ai.completions_endpoint_of("http://159.75.50.193:6500/v1"),
            "http://159.75.50.193:6500/v1/completions")

    def test_chat_endpoint_is_rewritten(self):
        # 用户把整条 chat 地址粘进来是常态，不能拼成 …/chat/completions/completions
        self.assertEqual(ai.completions_endpoint_of("http://x/v1/chat/completions"),
                         "http://x/v1/completions")

    def test_already_completions_is_kept(self):
        self.assertEqual(ai.completions_endpoint_of("http://x/v1/completions"),
                         "http://x/v1/completions")

    def test_no_version_appends_v1(self):
        self.assertEqual(ai.completions_endpoint_of("https://api.deepseek.com"),
                         "https://api.deepseek.com/v1/completions")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            ai.completions_endpoint_of("")


# ---------------------------------------------------------------- 清洗


class TestCleanFim(unittest.TestCase):
    def test_keeps_leading_indent(self):
        # 和 clean_code 最关键的差别：绝不剥公共缩进
        self.assertEqual(ai.clean_fim("    x := 1;", "", 6), "    x := 1;")

    def test_strips_code_fence(self):
        self.assertEqual(ai.clean_fim("```st\nx := 1;\n```", "", 6), "x := 1;")

    def test_trims_overlap_with_suffix(self):
        gen = "  a := 1;\n  return x;\n"
        self.assertEqual(ai.clean_fim(gen, "  return x;\nEND_IF", 6), "  a := 1;")

    def test_tiny_overlap_is_kept(self):
        # 只重叠 1 个字符不算，否则会把分号吃掉
        self.assertEqual(ai.clean_fim("x := 1;", ";;", 6), "x := 1;")

    def test_drops_trailing_blank_lines(self):
        self.assertEqual(ai.clean_fim("a := 1;\n\n\n", "", 6), "a := 1;")

    def test_limits_lines(self):
        out = ai.clean_fim("\n".join("l%d" % i for i in range(10)), "", 3)
        self.assertEqual(out.split("\n"), ["l0", "l1", "l2"])

    def test_empty_input(self):
        self.assertEqual(ai.clean_fim("", "x", 6), "")


# ---------------------------------------------------------------- 双策略


class TestCompleteFim(unittest.TestCase):
    def _completer(self, **kw):
        opts = dict(base_url="http://127.0.0.1:11434/v1",
                    model="qwen2.5-coder:1.5b-base")
        opts.update(kw)
        return ai.OpenAiCompleter(**opts)

    def test_server_strategy_sends_suffix(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_resp(_text("  b := 2;\n"))) as opened:
            out = self._completer().complete_fim(
                "a := 1;\n", "  c := 3;\n", 4, strategy="server")
        self.assertEqual(out, "  b := 2;")
        req = opened.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/v1/completions"))
        sent = json.loads(req.data.decode("utf-8"))
        self.assertEqual(sent["prompt"], "a := 1;\n")
        self.assertEqual(sent["suffix"], "  c := 3;\n")
        self.assertFalse(sent["stream"])

    def test_token_strategy_builds_prompt(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_resp(_text("b := 2;"))) as opened:
            out = self._completer().complete_fim("A\n", "C\n", 4, strategy="token")
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["prompt"],
                         "<|fim_prefix|>A\n<|fim_suffix|>C\n<|fim_middle|>")
        self.assertNotIn("suffix", sent)
        self.assertEqual(out, "b := 2;")

    def test_deepseek_token_strategy(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_resp(_text("x := 1;"))) as opened:
            self._completer(model="deepseek-coder:1.3b-base").complete_fim(
                "A\n", "C\n", 4, strategy="token")
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["prompt"], "<fim_begin>A\n<fim_hole>C\n<fim_end>")

    def test_auto_falls_back_to_token_on_404(self):
        err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen",
                        side_effect=[err, _resp(_text("ok;"))]) as opened:
            out = self._completer().complete_fim("A\n", "C\n", 4, strategy="auto")
        self.assertEqual(out, "ok;")
        self.assertEqual(opened.call_count, 2)
        second = json.loads(opened.call_args_list[1][0][0].data.decode("utf-8"))
        self.assertIn("<|fim_prefix|>", second["prompt"])

    def test_auto_falls_back_when_reply_is_empty(self):
        # 服务端吃了 suffix 却什么也没补 —— 和不支持没区别，退 token 再试
        with mock.patch("urllib.request.urlopen",
                        side_effect=[_resp(_text("")), _resp(_text("ok;"))]) as opened:
            out = self._completer().complete_fim("A", "C", 4, strategy="auto")
        self.assertEqual(out, "ok;")
        self.assertEqual(opened.call_count, 2)

    def test_server_only_strategy_raises_on_404(self):
        err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ValueError):
                self._completer().complete_fim("A", "C", 4, strategy="server")

    def test_streaming_reads_text_delta_and_cleans(self):
        # /completions 的 SSE 增量在 choices[0].text，不在 delta.content 里
        frames = []
        for piece in ("x := 1;\n", "  return y;\n"):
            frames.append("data: " + json.dumps({"choices": [{"text": piece}]}))
        frames.append("data: [DONE]")
        body = ("\n".join(frames) + "\n").encode("utf-8")

        seen = []
        with mock.patch("urllib.request.urlopen", return_value=_bytes(body)):
            out = self._completer().complete_fim(
                "A\n", "  return y;\nEND_IF", 4, on_delta=seen.append)
        self.assertEqual(out, "x := 1;")
        # 推给界面的也必须是剪过的，不能先给带尾巴的
        self.assertEqual(seen[-1], "x := 1;")

    def test_stops_streaming_once_past_the_line_limit(self):
        """写超行数上限后要主动收工，别让用户盯着"生成中"干等。

        FIM 没有"写几行就停"的约束，模型会一直写到 max_tokens；而卡片只显示
        前 N 行 —— 多出来的横竖会被截掉，继续等就是白等（用户报障：
        "都没有继续生成了，为什么还等那么久"）。
        """
        frames = ["data: " + json.dumps({"choices": [{"text": "l%d;\n" % i}]})
                  for i in range(30)]
        frames.append("data: [DONE]")
        body = ("\n".join(frames) + "\n").encode("utf-8")

        seen = []
        with mock.patch("urllib.request.urlopen", return_value=_bytes(body)):
            out = self._completer().complete_fim("a;\n", "", 5, on_delta=seen.append)
        self.assertLessEqual(len(out.split("\n")), 5)
        self.assertLessEqual(len(seen[-1].split("\n")), 5)

    def test_missing_model_raises(self):
        with self.assertRaises(ValueError):
            self._completer(model="").complete_fim("A", "C", 4)


# ---------------------------------------------------------------- 上下文裁剪


class TestContextClamp(unittest.TestCase):
    """行数限住了不等于上下文就小 —— 一行超长的赋值照样能撑到几千字符。"""

    def test_tail_keeps_end(self):
        # prefix 留尾部：离光标最近的才是有用的
        self.assertEqual(ai.clamp_tail("0123456789", 4), "6789")

    def test_head_keeps_start(self):
        self.assertEqual(ai.clamp_head("0123456789", 4), "0123")

    def test_short_text_untouched(self):
        self.assertEqual(ai.clamp_tail("abc", 10), "abc")
        self.assertEqual(ai.clamp_head("abc", 10), "abc")

    def test_zero_means_no_limit(self):
        self.assertEqual(ai.clamp_tail("abcdef", 0), "abcdef")

    def test_complete_fim_clamps_both_sides(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_resp(_text("x"))) as opened:
            ai.OpenAiCompleter(
                base_url="http://x/v1", model="qwen2.5-coder",
                prefix_chars=100, suffix_chars=50,
            ).complete_fim("P" * 5000, "S" * 5000, 4, strategy="server")
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(len(sent["prompt"]), 100)
        self.assertEqual(len(sent["suffix"]), 50)


class TestGenerationNotTiedToDisplay(unittest.TestCase):
    """生成量归 ai_max_tokens 管，预览窗显示几行只管显示。

    这两件事以前被混在一起（拿 ai_card_max_lines 当生成上限，还 min(8, ...)），
    表现是"卡片明明能放 14 行，模型却只补 8 行"。下面这些用例就是防回归的。
    """

    def test_safety_valve_is_generous(self):
        # 安全阀只防跑飞（几百行那种），不能小到像在限制正常输出
        self.assertGreaterEqual(ai.MAX_AUTO_LINES, 16)

    def test_clean_fim_keeps_more_lines_than_card_shows(self):
        # 卡片默认只显示 14 行，但模型写了 20 行就得原样留着 —— 折叠是显示层的事
        body = "\n".join("l%d" % i for i in range(20))
        self.assertEqual(len(ai.clean_fim(body, "", ai.MAX_AUTO_LINES).split("\n")), 20)

    def test_budget_comes_from_max_tokens_not_lines(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_resp(_text("x"))) as opened:
            ai.OpenAiCompleter(
                base_url="http://x/v1", model="qwen2.5-coder", max_tokens=777,
            ).complete_fim("a", "b", 4, strategy="server")
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        # 4 行如果还按 old 的 40*token 反推，这里会是 200
        self.assertEqual(sent["max_tokens"], 777)

    def test_chat_path_budget_also_uses_max_tokens(self):
        payload = {"choices": [{"message": {"content": "x := 1;"}}]}
        with mock.patch("urllib.request.urlopen",
                        return_value=_resp(payload)) as opened:
            ai.OpenAiCompleter(
                base_url="http://x/v1", model="m", max_tokens=777,
            ).complete_lines("a := 1;\n", 24)
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        # 行数不再参与预算换算（老写法是 24*40+40 再和 max_tokens 取小）
        self.assertEqual(sent["max_tokens"], 777)
        # 行数照常写进提示词，它只是"希望写几行"，不是硬掐
        self.assertIn("最多 24 行", sent["messages"][-1]["content"])


# ---------------------------------------------------------------- 演练 / 协议


class TestMockAndNull(unittest.TestCase):
    def test_mock_supports_fim(self):
        out = ai.MockCompleter(delay=0).complete_fim("定时器", "END_IF", 6)
        self.assertTrue(out.strip())

    def test_null_returns_blank(self):
        self.assertEqual(ai.NullCompleter().complete_fim("a", "b"), "")

    def test_mock_streams_through_fim(self):
        seen = []
        ai.MockCompleter(delay=0).complete_fim("a", "b", 6, on_delta=seen.append)
        self.assertTrue(seen)


if __name__ == "__main__":
    unittest.main()
