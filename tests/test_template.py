"""补全内容里的动态变量（``${...}``）测试。

这套东西的**安全底线**是「不认识的名字原样保留」—— ``${}`` 在 JS/PHP 的
模板字符串里也合法，而本工具常在 VS Code 里生效。只展开认得名字，
才不会把用户的代码搞坏。下面好几条测试就是钉这条底线。
"""

from __future__ import annotations

import datetime
import os
import unittest

from codepoint.services import template as tpl


def ctx(**kw) -> tpl.Context:
    """一个确定不变的上下文 —— 测试里绝不用真时钟。"""
    base = dict(
        now=datetime.datetime(2026, 10, 8, 9, 5, 3),
        username="xiaoen",
        hostname="LAPTOP-TEST",
    )
    base.update(kw)
    return tpl.Context(**base)


class TestBasicExpansion(unittest.TestCase):
    def setUp(self):
        self.c = ctx()

    def test_datetime_and_parts(self):
        cases = {
            "${datetime}": "2026-10-08 09:05:03",
            "${date}": "2026-10-08",
            "${time}": "09:05:03",
            "${year}": "2026",
            "${month}": "10",
            "${day}": "08",
            "${hour}": "09",
            "${minute}": "05",
            "${second}": "03",
            "${author}": "xiaoen",
            "${pc}": "LAPTOP-TEST",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(tpl.expand(text, context=self.c), want)

    def test_two_digit_padding(self):
        """月/日/时/分/秒 必须补零 —— 注释头里 2026-10-8 看着很业余。"""
        c = ctx(now=datetime.datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(tpl.expand("${date} ${time}", context=c),
                         "2026-01-02 03:04:05")

    def test_text_around_variable(self):
        text = "//创建时间: ${datetime}\n//作者: ${author}"
        self.assertEqual(
            tpl.expand(text, context=self.c),
            "//创建时间: 2026-10-08 09:05:03\n//作者: xiaoen")

    def test_multiple_same_variable(self):
        self.assertEqual(tpl.expand("${year}-${year}", context=self.c),
                         "2026-2026")

    def test_empty_text(self):
        self.assertEqual(tpl.expand("", context=self.c), "")

    def test_text_without_dollar_is_untouched(self):
        text = "IF 1 THEN\n    ;\nEND_IF"
        self.assertEqual(tpl.expand(text, context=self.c), text)


class TestChineseAliases(unittest.TestCase):
    """中文名与英文名等价 —— 用户写注释头时更顺手。"""

    def test_chinese_aliases_match_english(self):
        c = ctx()
        pairs = [
            ("${日期}", "${date}"),
            ("${时间}", "${time}"),
            ("${日期时间}", "${datetime}"),
            ("${年}", "${year}"),
            ("${月}", "${month}"),
            ("${日}", "${day}"),
            ("${时}", "${hour}"),
            ("${分}", "${minute}"),
            ("${秒}", "${second}"),
            ("${作者}", "${author}"),
            ("${计算机名}", "${pc}"),
        ]
        for cn, en in pairs:
            with self.subTest(cn=cn):
                self.assertEqual(tpl.expand(cn, context=c),
                                 tpl.expand(en, context=c))

    def test_ascii_alias_is_case_insensitive(self):
        c = ctx()
        for spelling in ("${Date}", "${DATE}", "${DaTe}"):
            with self.subTest(spelling=spelling):
                self.assertEqual(tpl.expand(spelling, context=c), "2026-10-08")

    def test_whitespace_inside_braces_is_tolerated(self):
        self.assertEqual(tpl.expand("${ date }", context=ctx()), "2026-10-08")


class TestSafetyNet(unittest.TestCase):
    """不认识的名字必须原样保留 —— 这是不破坏用户代码的关键。"""

    def test_unknown_name_kept_verbatim(self):
        c = ctx()
        for text in ("${foo}", "${i}", "${row.id}", "${UNKNOWN_VAR}",
                     "${}", "${  }"):
            with self.subTest(text=text):
                self.assertEqual(tpl.expand(text, context=c), text)

    def test_js_template_literal_survives(self):
        """真实场景：在 VS Code 里写 JS，别把模板字符串吃了。"""
        js = "const s = `行${row.id} 共${count}条`;"
        self.assertEqual(tpl.expand(js, context=ctx()), js)

    def test_php_variable_survives(self):
        php = '$name = "x"; echo $name; echo "${unset_var}";'
        self.assertEqual(tpl.expand(php, context=ctx()), php)

    def test_unclosed_brace_kept_verbatim(self):
        self.assertEqual(tpl.expand("${date", context=ctx()), "${date")

    def test_lone_dollar_kept(self):
        self.assertEqual(tpl.expand("100$ 和 $x", context=ctx()), "100$ 和 $x")


class TestEscape(unittest.TestCase):
    def test_double_dollar_yields_literal(self):
        self.assertEqual(tpl.expand("$${date}", context=ctx()), "${date}")

    def test_plain_double_dollar(self):
        self.assertEqual(tpl.expand("a$$b", context=ctx()), "a$b")

    def test_escape_and_expand_mixed(self):
        text = "$${date} 真的 ${date}"
        self.assertEqual(tpl.expand(text, context=ctx()),
                         "${date} 真的 2026-10-08")

    def test_escape_alone_does_not_need_known_name(self):
        self.assertEqual(tpl.expand("$${whatever}", context=ctx()), "${whatever}")


class TestContextDefaults(unittest.TestCase):
    def test_default_context_reads_system(self):
        """真取一次系统值：不应抛异常，用户名/主机名应是非空串。"""
        c = tpl.default_context()
        self.assertIsInstance(c.now, datetime.datetime)
        self.assertTrue(c.username, "读不到系统用户名")
        # 主机名个别环境可能为空，但至少类型要对
        self.assertIsInstance(c.hostname, str)

    def test_default_context_accepts_injected_now(self):
        stamp = datetime.datetime(2001, 2, 3, 4, 5, 6)
        self.assertEqual(tpl.default_context(stamp).now, stamp)

    def test_expand_without_context_uses_now(self):
        """不给 context 时用真时间：结果应当就是"今天"的年份。"""
        expected = datetime.datetime.now().strftime("%Y")
        self.assertEqual(tpl.expand("${year}"), expected)


class TestVariableRegistry(unittest.TestCase):
    def test_all_variables_resolvable_by_canonical_name(self):
        for var in tpl.VARIABLES:
            with self.subTest(name=var.name):
                self.assertIs(tpl.resolve(var.name), var)

    def test_aliases_unique_across_variables(self):
        """一个别名不能被两个变量占用，否则谁赢看运气。"""
        seen = {}
        for var in tpl.VARIABLES:
            for alias in var.aliases:
                key = alias.casefold()
                self.assertNotIn(key, seen,
                                 f"别名 {alias} 同时属于 {seen.get(key)} 和 {var.name}")
                seen[key] = var.name

    def test_every_variable_has_label_desc_and_sample(self):
        for var in tpl.VARIABLES:
            with self.subTest(name=var.name):
                self.assertTrue(var.label)
                self.assertTrue(var.desc)
                self.assertTrue(var.sample)
                self.assertEqual(var.placeholder(), "${%s}" % var.name)

    def test_button_labels_are_short_enough_to_fit(self):
        """按钮短名太长会把这一行撑爆（实测「系统登录用户名」把最后一个按钮挤出界）。"""
        for var in tpl.VARIABLES:
            with self.subTest(name=var.name):
                self.assertLessEqual(len(var.label), 4,
                                     f"{var.name} 的按钮名「{var.label}」太长了")

    def test_samples_match_actual_rendering(self):
        """界面提示里显示的示例值必须和真实渲染一致，否则是骗人的。

        示例值绑定 ``SAMPLE_CONTEXT`` —— 谁改了格式却没改示例，这条就红。
        """
        c = tpl.SAMPLE_CONTEXT
        for var in tpl.VARIABLES:
            with self.subTest(name=var.name):
                self.assertEqual(var.build(c), var.sample,
                                 f"{var.name} 的示例值过时了，应改成 {var.build(c)!r}")

    def test_resolve_unknown_returns_none(self):
        self.assertIsNone(tpl.resolve("nope"))
        self.assertIsNone(tpl.resolve(""))
        self.assertIsNone(tpl.resolve(None))


class TestEngineIntegration(unittest.TestCase):
    """引擎在补全那一刻展开 —— 顺便验证"注释头里的时间是插入时间"。"""

    def _engine(self, content):
        from codepoint.core.models import DETECT_WORD, QuickAction
        from codepoint.services.quick_action import QuickActionEngine

        action = QuickAction("///", DETECT_WORD, content, True)
        # 冻结时间，保证断言确定
        fixed = datetime.datetime(2026, 10, 8, 9, 5, 3)
        return QuickActionEngine(
            [action],
            render=lambda text: tpl.expand(text, context=ctx(now=fixed)),
        ), action

    def test_accept_expands_variables(self):
        from codepoint.services import quick_action as qa

        eng, _ = self._engine("//作者: ${author}\n//创建时间: ${datetime}")
        for ch in "///":
            eng.feed(_key(ch), ch, 0.0)
        res = eng.accept(1.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.text,
                         "//作者: xiaoen\n//创建时间: 2026-10-08 09:05:03")

    def test_char_mode_expands_variables(self):
        from codepoint.core.models import DETECT_CHAR, QuickAction
        from codepoint.services import quick_action as qa
        from codepoint.services.quick_action import QuickActionEngine

        fixed = datetime.datetime(2026, 10, 8, 9, 5, 3)
        act = QuickAction("`", DETECT_CHAR, "`${date}`", True)
        eng = QuickActionEngine(
            [act],
            render=lambda text: tpl.expand(text, context=ctx(now=fixed)))
        res = eng.feed(_key("`"), "`", 0.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.text, "`2026-10-08`")

    def test_render_error_does_not_break_completion(self):
        """渲染炸了也要能把原始内容补出来，不能让 Tab 没反应。"""
        from codepoint.core.models import DETECT_WORD, QuickAction
        from codepoint.services import quick_action as qa
        from codepoint.services.quick_action import QuickActionEngine

        act = QuickAction("if", DETECT_WORD, "IF  THEN", True)

        def boom(_text):
            raise RuntimeError("渲染炸了")

        eng = QuickActionEngine([act], render=boom)
        for ch in "if":
            eng.feed(_key(ch), ch, 0.0)
        res = eng.accept(1.0)
        self.assertEqual(res.kind, qa.COMPLETE)
        self.assertEqual(res.text, "IF  THEN")

    def test_default_engine_expands_real_time(self):
        """不注入 render 时，默认就用真时间展开。"""
        from codepoint.core.models import DETECT_WORD, QuickAction
        from codepoint.services.quick_action import QuickActionEngine

        act = QuickAction("yy", DETECT_WORD, "${year}", True)
        eng = QuickActionEngine([act])
        for ch in "yy":
            eng.feed(_key(ch), ch, 0.0)
        res = eng.accept(1.0)
        self.assertEqual(res.text, datetime.datetime.now().strftime("%Y"))


def _key(ch: str):
    from tests.fakes import key as _fake_key

    vk = ord(ch.upper()) if ch.isalpha() else 0
    return _fake_key(vk, ch)


if __name__ == "__main__":
    unittest.main()
