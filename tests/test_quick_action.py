"""快捷输入引擎测试。"""

from __future__ import annotations

import unittest

from codepoint.core.models import DETECT_CHAR, DETECT_WORD, QuickAction
from codepoint.services import quick_action as qa
from codepoint.services.quick_action import QuickActionEngine

from .fakes import VK_A, VK_COMMA, VK_ESCAPE, VK_RETURN, VK_SPACE, VK_TAB, key


def engine(actions, **kw) -> QuickActionEngine:
    return QuickActionEngine(actions, **kw)


def type_text(eng: QuickActionEngine, text: str, now: float = 0.0):
    """把一串字符逐键投喂进去，返回最后一个结果。"""
    result = qa._RESULT_NONE
    for ch in text:
        vk = ord(ch.upper()) if ch.isalpha() else 0
        result = eng.feed(key(vk, ch), ch, now)
    return result


class TestWordMode(unittest.TestCase):
    def setUp(self):
        self.act = QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True)
        self.eng = engine([self.act])

    def test_trigger_enters_pending_not_complete(self):
        res = type_text(self.eng, "if")
        self.assertEqual(res.kind, qa.SHOW)
        self.assertEqual(res.action, self.act)
        self.assertIs(self.eng.pending, self.act)

    def test_tab_accepts_and_returns_backspaces(self):
        type_text(self.eng, "if")
        res = self.eng.accept(1.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.backspaces, 2)       # 吃掉 "if"
        self.assertIn("END_IF", res.text)
        self.assertIsNone(self.eng.pending)

    def test_word_boundary_prevents_false_positive(self):
        """"sif" 不该命中 "if"。"""
        res = type_text(self.eng, "s")
        self.assertEqual(res.kind, qa.NONE)
        res = type_text(self.eng, "if")
        self.assertEqual(res.kind, qa.NONE, "词中间不应命中")

    def test_boundary_after_separator_allows_match(self):
        type_text(self.eng, "x")
        self.eng.feed(key(VK_SPACE, " "), " ", 0.0)      # 分隔
        res = type_text(self.eng, "if")
        self.assertEqual(res.kind, qa.SHOW, "分隔符之后应可命中")

    def test_non_matching_char_hides_pending(self):
        type_text(self.eng, "if")
        res = type_text(self.eng, "x")
        self.assertEqual(res.kind, qa.HIDE)
        self.assertIsNone(self.eng.pending)

    def test_enter_resets(self):
        type_text(self.eng, "if")
        res = self.eng.feed(key(VK_RETURN, "\r"), "\r", 0.0)
        self.assertEqual(res.kind, qa.HIDE)
        self.assertEqual(self.eng.buffer, "")

    def test_escape_resets(self):
        type_text(self.eng, "if")
        res = self.eng.feed(key(VK_ESCAPE, None), None, 0.0)
        self.assertEqual(res.kind, qa.HIDE)

    def test_pending_expires(self):
        type_text(self.eng, "if")
        self.assertFalse(self.eng.expire(1.0))            # 还没到超时
        self.assertTrue(self.eng.expire(100.0))           # 超时
        self.assertIsNone(self.eng.pending)

    def test_window_change_resets(self):
        type_text(self.eng, "if")
        res = self.eng.on_window(777)
        self.assertEqual(res.kind, qa.HIDE)
        self.assertIsNone(self.eng.pending)

    def test_accept_without_pending_is_noop(self):
        self.assertEqual(self.eng.accept(0.0).kind, qa.NONE)

    def test_disabled_engine_does_nothing(self):
        eng = engine([self.act], enabled=False)
        self.assertEqual(type_text(eng, "if").kind, qa.NONE)


class TestCaseInsensitive(unittest.TestCase):
    """词触发串不区分大小写 —— ST 关键字本身就不区分，敲 `if`/`IF`/`If` 都该命中。"""

    def setUp(self):
        self.act = QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True)
        self.eng = engine([self.act])

    def test_lowercase_trigger_hit_by_uppercase_input(self):
        res = type_text(self.eng, "IF")
        self.assertEqual(res.kind, qa.SHOW)
        self.assertIs(res.action, self.act)

    def test_mixed_case_input(self):
        self.assertEqual(type_text(self.eng, "If").kind, qa.SHOW)

    def test_uppercase_trigger_hit_by_lowercase_input(self):
        """规则里写的是大写的年代留下的老配置，敲小写也要能用。"""
        for typed in ("for", "FOR", "For"):
            eng = engine([QuickAction("FOR", DETECT_WORD, "FOR ... END_FOR;", True)])
            self.assertEqual(type_text(eng, typed).kind, qa.SHOW, f"敲 {typed!r} 没命中")

    def test_boundary_still_enforced(self):
        """大小写放宽了，但词边界不能松：'sif' 依然不能命中 'if'。"""
        for typed in ("sif", "sIF", "IF"):
            eng = engine([QuickAction("if", DETECT_WORD, "IF  THEN\nEND_IF;", True)])
            if typed == "IF":
                self.assertEqual(type_text(eng, typed).kind, qa.SHOW)
            else:
                self.assertEqual(type_text(eng, typed).kind, qa.NONE, f"{typed!r} 不该命中")

    def test_punctuation_trigger_stays_literal(self):
        """含标点的触发串不折叠大小写。"""
        eng = engine([QuickAction("///", DETECT_WORD, "//====", True)])
        self.assertEqual(type_text(eng, "///").kind, qa.SHOW)

    def test_case_duplicates_are_deduped(self):
        """`for` 和 `FOR` 同时存在时只留先出现的那条，避免多出一行永远不命中的规则。"""
        first = QuickAction("for", DETECT_WORD, "A", True)
        second = QuickAction("FOR", DETECT_WORD, "B", True)
        eng = engine([first, second])
        self.assertEqual(eng.action_count, 1)
        res = type_text(eng, "for")
        self.assertIs(res.action, first)


class TestCharMode(unittest.TestCase):
    def setUp(self):
        self.act = QuickAction(":", DETECT_CHAR, ":=", True)
        self.eng = engine([self.act])

    def test_char_trigger_completes_immediately(self):
        res = self.eng.feed(key(0xBA, ":"), ":", 0.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.backspaces, 1)
        self.assertEqual(res.text, ":=")

    def test_other_char_does_nothing(self):
        self.assertEqual(self.eng.feed(key(VK_COMMA, ","), ",", 0.0).kind, qa.NONE)


class TestPunctuationTrigger(unittest.TestCase):
    """形如 "///" 的标点触发串。"""

    def setUp(self):
        self.act = QuickAction("///", DETECT_WORD, "//=====", True)
        self.eng = engine([self.act])

    def test_three_slashes_match(self):
        res = type_text(self.eng, "//")
        self.assertEqual(res.kind, qa.NONE)
        res = type_text(self.eng, "/")
        self.assertEqual(res.kind, qa.SHOW)

    def test_two_slashes_do_not_match(self):
        res = type_text(self.eng, "//")
        self.assertEqual(res.kind, qa.NONE)


class TestPriority(unittest.TestCase):
    def test_longest_trigger_wins(self):
        short = QuickAction("if", DETECT_WORD, "SHORT", True)
        long_ = QuickAction("ifelse", DETECT_WORD, "LONG", True)
        eng = engine([short, long_])
        res = type_text(eng, "ifelse")
        self.assertEqual(res.kind, qa.SHOW)
        self.assertEqual(res.action, long_)

    def test_disabled_action_skipped(self):
        eng = engine([QuickAction("if", DETECT_WORD, "X", False)])
        self.assertEqual(type_text(eng, "if").kind, qa.NONE)

    def test_empty_trigger_skipped(self):
        eng = engine([QuickAction("", DETECT_WORD, "X", True)])
        self.assertEqual(eng.action_count, 0)

    def test_is_accept_key(self):
        eng = engine([])
        self.assertTrue(eng.is_accept_key(key(VK_TAB, "\t")))
        self.assertFalse(eng.is_accept_key(key(VK_A, "a")))


class TestDefaults(unittest.TestCase):
    def test_default_actions_present(self):
        from codepoint.core.defaults import default_quick_actions
        acts = default_quick_actions()
        triggers = {a.trigger for a in acts}
        for t in (":", "if", "ifelse", "for", "while", "case", "///"):
            self.assertIn(t, triggers)

    def test_every_default_word_trigger_works_lowercase(self):
        """默认规则必须"敲小写就能用" —— 这是最容易翻车的一处
        （默认串写成大写 `FOR` 时，用户敲 `for` 完全没有反应，
         而 `if`/`while`/`case` 都是小写所以看起来一切正常）。"""
        from codepoint.core.defaults import default_quick_actions
        eng = engine(default_quick_actions())
        for word in ("if", "ifelse", "for", "while", "case", "fb", "fc"):
            eng.reset()
            res = type_text(eng, word)
            self.assertEqual(res.kind, qa.SHOW, f"敲 {word!r} 没有弹出补全提示")
            self.assertEqual(res.action.trigger, word)

    def test_default_colon_is_not_hijacked(self):
        """默认配置下敲冒号必须"原样通过"。

        单字符触发会**永久劫持**那个字符 —— `:` 要靠 Shift+; 才打得出来，
        默认启用 `:` → `:=` 的后果是冒号在受管程序里永远打不出来
        （CASE 标签 `0:`、变量声明 `x : INT;` 全废，用户实测报障）。
        所以这条规则默认停用：engine 根本不会装载它。
        """
        from codepoint.core.defaults import default_quick_actions
        eng = engine(default_quick_actions())
        res = eng.feed(key(0xBA, ":"), ":", 0.0)
        self.assertEqual(res.kind, qa.NONE)
        self.assertEqual(eng.action_count, 8)      # 9 条默认规则，停用 1 条（`:`）

    def test_colon_rule_completes_when_enabled(self):
        """用户在界面里手动启用后，`:` → `:=` 仍然可用。"""
        from codepoint.core.defaults import default_quick_actions
        from codepoint.core.models import QuickAction as QA

        actions = []
        for action in default_quick_actions():
            if action.trigger == ":":
                action.enabled = True
            actions.append(action)
        eng = engine(actions)
        res = eng.feed(key(0xBA, ":"), ":", 0.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.text, ":=")

    def test_default_if_completes_with_tab(self):
        from codepoint.core.defaults import default_quick_actions
        eng = engine(default_quick_actions())
        type_text(eng, "if")
        res = eng.accept(0.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.backspaces, 2)
        self.assertTrue(res.text.startswith("IF"))

    def test_default_for_completes_with_tab(self):
        from codepoint.core.defaults import default_quick_actions
        eng = engine(default_quick_actions())
        type_text(eng, "for")
        res = eng.accept(0.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.backspaces, 3)       # 吃掉 "for"
        self.assertIn("END_FOR", res.text)


if __name__ == "__main__":
    unittest.main()
