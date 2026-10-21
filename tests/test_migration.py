"""配置迁移测试（schema v1 → v2）。

迁移必须满足三个性质，缺一个都会变成"跟用户打架"：
  1. 只跑一次 —— 靠写进 settings.json 的 schema 版本号；
  2. 只动"还是出厂状态"的数据 —— 用户改过的绝不碰；
  3. 迁移完成后 schema 必须落盘 —— 否则每次启动都会重跑一遍。
"""

from __future__ import annotations

import unittest

from codepoint.core.config import SCHEMA_VERSION, build_default_settings, merge_with_defaults
from codepoint.core.models import DETECT_CHAR, Settings


def _colon_rule(s: Settings):
    for action in s.quick_actions:
        if action.trigger == ":":
            return action
    return None


class TestSchemaMigration(unittest.TestCase):
    def test_fresh_install_has_no_colon_hijack(self):
        s = merge_with_defaults({})
        self.assertFalse(_colon_rule(s).enabled, "出厂配置不该劫持冒号")
        self.assertEqual(s.schema, SCHEMA_VERSION)

    def test_v1_config_gets_migrated(self):
        """v1 配置（无 schema 字段、`:` 规则启用）→ 迁移后停用。"""
        old = build_default_settings()
        old.quick_actions[0].enabled = True
        raw = old.to_dict()
        raw.pop("schema")
        s = merge_with_defaults(raw)
        self.assertFalse(_colon_rule(s).enabled)
        self.assertEqual(s.schema, SCHEMA_VERSION)

    def test_customized_rule_is_untouched(self):
        """用户改过内容的规则说明真的在用 —— 迁移不许动它。"""
        old = build_default_settings()
        old.quick_actions[0].content = ":=="       # 用户自定义过
        old.quick_actions[0].enabled = True
        raw = old.to_dict()
        raw.pop("schema")
        self.assertTrue(_colon_rule(merge_with_defaults(raw)).enabled)

    def test_migrated_config_is_not_migrated_again(self):
        """schema=2 之后不再迁移：用户手动启用必须保留。"""
        s = build_default_settings()
        s.quick_actions[0].enabled = True          # 用户手动启用
        again = merge_with_defaults(s.to_dict())   # to_dict 带 schema=2
        self.assertTrue(_colon_rule(again).enabled)

    def test_bad_schema_value_is_tolerated(self):
        raw = build_default_settings().to_dict()
        raw["schema"] = "not-a-number"
        s = merge_with_defaults(raw)
        self.assertEqual(s.schema, SCHEMA_VERSION)

    def test_detect_char_constant_matches_rule(self):
        """迁移条件里的 DETECT_CHAR 不能写错（写错了迁移永远不生效）。"""
        self.assertEqual(DETECT_CHAR, "char")


if __name__ == "__main__":
    unittest.main()
