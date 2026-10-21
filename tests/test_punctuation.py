"""core.defaults 与 services.punctuation 的测试。"""

from __future__ import annotations

import unittest

from codepoint.core.defaults import (
    default_punctuation_rules,
    text_conversion_map,
    to_fullwidth,
    to_halfwidth,
)
from codepoint.core.events import ImeSnapshot
from codepoint.core.models import PunctuationRule
from codepoint.services.punctuation import PunctuationService

from .fakes import VK_COMMA, VK_OEM_1, VK_PERIOD, key

TARGETED = True
NOT_TARGETED = False
# 输入法三种状态：中文标点开着 / 输入法关闭 / 问不到状态
IME_CJK = ImeSnapshot(available=True, open=True, native=True, symbol=True)
IME_OFF = ImeSnapshot(available=True, open=False)
IME_UNKNOWN = ImeSnapshot()


def build_service(**kw) -> PunctuationService:
    rules = default_punctuation_rules()
    svc = PunctuationService()
    svc.configure(rules, text_table=text_conversion_map(rules), **kw)
    return svc


class TestFullwidthMapping(unittest.TestCase):
    def test_ascii_to_fullwidth(self):
        self.assertEqual(to_fullwidth(","), "，")
        self.assertEqual(to_fullwidth("?"), "？")
        self.assertEqual(to_fullwidth(" "), "\u3000")
        self.assertIsNone(to_fullwidth("中"))

    def test_fullwidth_to_ascii(self):
        self.assertEqual(to_halfwidth("，"), ",")
        self.assertEqual(to_halfwidth("？"), "?")
        self.assertEqual(to_halfwidth("\u3000"), " ")
        self.assertEqual(to_halfwidth("。"), ".")      # 表意句号
        self.assertEqual(to_halfwidth("、"), ",")      # 顿号
        self.assertEqual(to_halfwidth("《"), "<")
        self.assertIsNone(to_halfwidth("中"))

    def test_default_rules_cover_key_punctuation(self):
        # 默认只保证"写代码最常用的那些"能转。其余（＄％＆＊＾～｜｀ ，！？＜＞）
        # 条目留着但默认停用 —— 全角形式罕见，开着只会增加误转的机会。
        rules = default_punctuation_rules()
        halves = {r.halfwidth for r in rules if r.enabled}
        for ch in ',.;:"()[]{}-_=+/':
            self.assertIn(ch, halves, f"缺少 {ch!r} 的规则")
        # 空格不应成为逐键拦截目标（否则每次空格都多一次注入）
        self.assertNotIn(" ", halves)

    def test_default_rules_keep_rare_ones_as_disabled(self):
        """不常用的全角形式保留条目但默认关着 —— 想要时拨一下就行，不用重新添加。"""
        rules = default_punctuation_rules()
        disabled = {r.fullwidth for r in rules if not r.enabled}
        for ch in "＇＼！？＜＞｜｀":
            self.assertIn(ch, disabled)

    def test_text_table_has_no_ideographic_punctuation_by_default(self):
        """默认不转「表意标点」（。、《》），它们不是全角版本。

        以前这些字符靠 ``text_conversion_map`` 的兜底表硬塞进来 ——
        界面上根本没有对应规则，用户把能关的都关了、复制粘贴时中文句号
        还是变成点（报障原话："关闭转换他还是转换"）。现在默认表里没有它们。
        """
        table = text_conversion_map(default_punctuation_rules())
        for ch in "。、【】《》":
            self.assertNotIn(ch, table)
        self.assertNotIn("\u3000", table)     # 刻意不转换表意空格
        # 全角标点（U+FF0C 这种真正的"全角版本"）照常转
        self.assertEqual(table["，"], ",")

    def test_ideographic_punctuation_converts_once_user_adds_rule(self):
        """想要就自己加一条 —— 加了就照常生效。"""
        rules = default_punctuation_rules()
        rules.append(PunctuationRule(fullwidth="。", halfwidth=".", enabled=True))
        table = text_conversion_map(rules)
        self.assertEqual(table["。"], ".")

    def test_disabled_rule_disappears_from_text_table(self):
        """关掉一条规则，整段转换里也真的没有它（不只是逐键路径）。"""
        rules = default_punctuation_rules()
        for rule in rules:
            if rule.fullwidth == "，":
                rule.enabled = False
        table = text_conversion_map(rules)
        self.assertNotIn("，", table)
        self.assertEqual(table["．"], ".")    # 别的规则不受影响


class TestPunctuationService(unittest.TestCase):
    def test_rule_count_matches_configured_rows(self):
        """状态栏"标点规则 N 条"必须等于列表里的行数 —— 数字对不上用户会以为规则丢了。

        （曾经返回的是"去重后的按键种类"（32），而列表有 49 行，
          因为一个半角字符可能被多条规则覆盖，比如 `-` 同时对应 `－` 和 `—`。）
        """
        from codepoint.core.defaults import default_punctuation_rules, text_conversion_map
        from codepoint.services.punctuation import PunctuationService

        rules = default_punctuation_rules()
        svc = PunctuationService()
        svc.configure(rules, text_table=text_conversion_map(rules))
        self.assertEqual(svc.rule_count, len(rules))
        self.assertLess(svc.key_target_count, svc.rule_count)     # 确有重复覆盖

    def test_rule_count_counts_listed_rows_and_enabled_separately(self):
        """rule_count 是"列表里有几行"，enabled_count 才是"开了几条"。

        以前默认表全启用，两个数字一样，看不出来；现在默认有几条是关着的
        占位（＄％＆＊ 那类全角形式），状态栏若只报启用数就会和列表行数对不上。
        """
        from codepoint.services.punctuation import PunctuationService

        rules = [PunctuationRule("，", ",", True), PunctuationRule("。", ".", False)]
        svc = PunctuationService()
        svc.configure(rules)
        self.assertEqual(svc.rule_count, 2)        # 列表两行
        self.assertEqual(svc.enabled_count, 1)     # 真正生效一条

    def test_targeted_comma_is_replaced(self):
        svc = build_service()
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_CJK,
                         targeted=TARGETED)
        self.assertTrue(out.replaced)
        self.assertEqual(out.emit, ",")

    def test_out_of_scope_passes_through(self):
        svc = build_service()
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_CJK,
                         targeted=NOT_TARGETED)
        self.assertFalse(out.swallow)
        self.assertEqual(out.reason, "not_in_scope")

    def test_shortcut_is_never_touched(self):
        svc = build_service()
        for mods in ({"ctrl": True}, {"alt": True}, {"win": True}):
            out = svc.decide(key(VK_COMMA, ",", **mods), window=None, ime=IME_CJK,
                             targeted=TARGETED)
            self.assertFalse(out.swallow, f"{mods} 组合被误拦截")
            self.assertEqual(out.reason, "shortcut")

    def test_shift_char_uses_resolved_char(self):
        """Shift+; 解析出 ':'，也应命中规则。

        （原来用 Shift+, 的 '<' 举例，但全角 ＜ 现在默认停用 —— 见
          DEFAULT_RULE_SPECS：代码里几乎不会打出全角小于号。）
        """
        svc = build_service()
        out = svc.decide(key(VK_OEM_1, ":", shift=True), window=None, ime=IME_CJK,
                         targeted=TARGETED)
        self.assertTrue(out.swallow)
        self.assertEqual(out.emit, ":")

    def test_letter_is_not_a_target(self):
        svc = build_service()
        out = svc.decide(key(0x41, "a"), window=None, ime=IME_CJK,
                         targeted=TARGETED)
        self.assertFalse(out.swallow)
        self.assertEqual(out.reason, "not_in_rules")

    def test_composing_guard_blocks_interception(self):
        """候选列表打开时不能抢标点键，否则会打断选词。"""
        svc = build_service(guard_compose=True)
        composing = ImeSnapshot(available=True, open=True, native=True, composing=True)
        out = svc.decide(key(VK_PERIOD, "."), window=None, ime=composing,
                         targeted=TARGETED)
        self.assertFalse(out.swallow)
        self.assertEqual(out.reason, "composing")

    def test_composing_guard_can_be_disabled(self):
        svc = build_service(guard_compose=False)
        composing = ImeSnapshot(available=True, open=True, native=True, composing=True)
        out = svc.decide(key(VK_PERIOD, "."), window=None, ime=composing,
                         targeted=TARGETED)
        self.assertTrue(out.swallow)

    def test_require_ime_mode(self):
        svc = build_service(require_ime=True)
        # 输入法是英文态 → 不替换
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_OFF,
                         targeted=TARGETED)
        self.assertFalse(out.swallow)
        self.assertEqual(out.reason, "ime_not_cjk")
        # 中文态 → 替换
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_CJK,
                         targeted=TARGETED)
        self.assertTrue(out.swallow)

    def test_require_ime_off_replaces_without_ime(self):
        svc = build_service(require_ime=False)
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_UNKNOWN,
                         targeted=TARGETED)
        self.assertTrue(out.swallow)

    def test_already_halfwidth_when_ime_closed(self):
        """输入法没开 → 打出来本来就是半角 → 不该吞键（少插手就少出错）。"""
        svc = build_service(require_ime=False)
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_OFF,
                         targeted=TARGETED)
        self.assertFalse(out.swallow)
        self.assertEqual(out.reason, "already_halfwidth")

    def test_semicolon_when_ime_closed_is_untouched(self):
        """英文输入状态下按 ; 必须原样放行——不能吞、更不能变成 :。"""
        svc = build_service(require_ime=False)
        out = svc.decide(key(VK_OEM_1, ";"), window=None, ime=IME_OFF,
                         targeted=TARGETED)
        self.assertFalse(out.swallow)
        self.assertIsNone(out.emit)

    def test_colon_needs_shift_and_is_a_rule_target(self):
        """冒号是 Shift+;，规则表里同时有 ; 和 : 两条，各管各的。"""
        svc = build_service(require_ime=False)
        semi = svc.decide(key(VK_OEM_1, ";"), window=None, ime=IME_CJK, targeted=TARGETED)
        colon = svc.decide(key(VK_OEM_1, ":", shift=True),
                           window=None, ime=IME_CJK, targeted=TARGETED)
        self.assertEqual(semi.emit, ";")
        self.assertEqual(colon.emit, ":")
        # 关键：不能把 ';' 判成 ':'，也不能把 ':' 判成 ';'
        self.assertNotEqual(semi.emit, colon.emit)

    def test_disabled_rule_is_ignored(self):
        svc = PunctuationService()
        svc.configure([PunctuationRule("，", ",", enabled=False)])
        out = svc.decide(key(VK_COMMA, ","), window=None, ime=IME_CJK,
                         targeted=TARGETED)
        self.assertFalse(out.swallow)

    def test_convert_text_mixed(self):
        svc = build_service()
        # 中文句号 `。` 不在默认规则里（它属于"表意标点"，要转得自己加），
        # 所以这里原样保留；全角逗号/冒号/分号照常转。
        text, n = svc.convert_text("变量：a，b。\nIF x THEN // 注释；")
        self.assertEqual(text, "变量:a,b。\nIF x THEN // 注释;")
        self.assertEqual(n, 3)

    def test_convert_text_keeps_chinese_chars(self):
        svc = build_service()
        text, n = svc.convert_text("中文内容保持不变")
        self.assertEqual(text, "中文内容保持不变")
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
