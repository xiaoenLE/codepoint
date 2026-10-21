"""剪贴板过滤器 与 配置存储 的测试。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from codepoint.core.config import ConfigPaths, build_default_settings, merge_with_defaults
from codepoint.core.defaults import default_punctuation_rules, text_conversion_map
from codepoint.core.models import AppItem, QuickAction
from codepoint.core.storage import SettingsStore
from codepoint.services.clipboard_filter import ClipboardPunctuationFilter


class TestClipboardFilter(unittest.TestCase):
    def setUp(self):
        table = text_conversion_map(default_punctuation_rules())
        self.f = ClipboardPunctuationFilter(enabled=True)
        self.f.configure(table, enabled=True)

    def test_converts_fullwidth_punctuation(self):
        # 用全角句点 `．`（U+FF0E，默认启用），不是表意句号 `。`
        out, n = self.f.transform("IF a，THEN b．")
        self.assertEqual(out, "IF a,THEN b.")
        self.assertEqual(n, 2)

    def test_keeps_plain_text_untouched(self):
        out, n = self.f.transform("already half width, ok.")
        self.assertEqual(out, "already half width, ok.")
        self.assertEqual(n, 0)

    def test_disabled_returns_original(self):
        self.f.set_enabled(False)
        out, n = self.f.transform("a，b")
        self.assertEqual(out, "a，b")
        self.assertEqual(n, 0)

    def test_should_rewrite_shortcut(self):
        self.assertTrue(self.f.should_rewrite("a，b"))
        self.assertFalse(self.f.should_rewrite("a,b"))
        self.assertFalse(self.f.should_rewrite(""))
        self.assertFalse(self.f.should_rewrite(None))

    def test_none_input(self):
        out, n = self.f.transform(None)
        self.assertEqual(out, "")
        self.assertEqual(n, 0)

    def test_ideographic_punctuation_left_alone_by_default(self):
        """「表意标点」默认不转，且现在也**没有**能悄悄转掉它的兜底表。

        `。` `、` 不是"全角版本"（全角逗号是 U+FF0C），以前它们靠
        text_conversion_map 的 setdefault 硬塞进来 —— 规则页里没有对应条目，
        用户怎么关都关不掉。现在默认表里没有，就是不转；想要就在规则页加一条。
        """
        out, n = self.f.transform("你好。世界、测试")
        self.assertEqual(out, "你好。世界、测试")
        self.assertEqual(n, 0)


class TestMergeDefaults(unittest.TestCase):
    def test_empty_dict_gives_defaults(self):
        s = merge_with_defaults({})
        self.assertTrue(s.global_enabled)
        self.assertGreater(len(s.punctuation_rules), 10)
        self.assertGreater(len(s.quick_actions), 3)
        # 程序列表默认只有「记事本」一条（刻意的：预置一堆用不上的程序名
        # 既让人困惑又容易被当成"必须保持"，见 defaults.default_app_items）
        self.assertGreaterEqual(len(s.app_items), 1)
        self.assertEqual(s.app_items[0].process_name, "notepad.exe")

    def test_partial_dict_keeps_defaults_for_missing(self):
        s = merge_with_defaults({"global_enabled": False})
        self.assertFalse(s.global_enabled)
        self.assertTrue(s.quick_action_enabled)          # 未来字段回落到默认
        self.assertGreater(len(s.punctuation_rules), 10)

    def test_explicit_empty_list_is_respected(self):
        s = merge_with_defaults({"quick_actions": []})
        self.assertEqual(s.quick_actions, [])

    def test_garbage_types_do_not_crash(self):
        s = merge_with_defaults({"punctuation_rules": "oops", "app_items": 42})
        self.assertGreater(len(s.punctuation_rules), 10)

    def test_roundtrip(self):
        s = build_default_settings()
        s.app_items.append(AppItem("my.exe", "我的程序"))
        s.quick_actions.append(QuickAction("xx", "word", "YY", True))
        restored = merge_with_defaults(json.loads(json.dumps(s.to_dict())))
        self.assertEqual(len(restored.app_items), len(s.app_items))
        self.assertEqual(restored.app_items[-1].process_name, "my.exe")
        self.assertEqual(restored.quick_actions[-1].trigger, "xx")


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codepoint-test-"))
        self.paths = ConfigPaths(self.tmp)
        self.store = SettingsStore(self.paths)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_when_missing_returns_defaults(self):
        s, err = self.store.load()
        self.assertIsNone(err)
        self.assertTrue(s.global_enabled)

    def test_save_then_load(self):
        s = build_default_settings()
        s.toggle_hotkey = "Ctrl+Shift+F9"
        s.global_enabled = False
        self.assertTrue(self.store.save(s))

        back, err = self.store.load()
        self.assertIsNone(err)
        self.assertEqual(back.toggle_hotkey, "Ctrl+Shift+F9")
        self.assertFalse(back.global_enabled)

    def test_save_is_atomic_and_makes_backup(self):
        s = build_default_settings()
        self.store.save(s)
        s.toggle_hotkey = "Ctrl+Alt+Z"
        self.store.save(s)
        self.assertTrue((self.tmp / "settings.json.bak").exists())
        # 临时文件不应残留
        leftovers = [p.name for p in self.tmp.iterdir() if p.name.startswith(".settings-")]
        self.assertEqual(leftovers, [])

    def test_corrupt_file_falls_back_and_backs_up(self):
        self.store.path.write_text("{ this is not json", encoding="utf-8")
        s, err = self.store.load()
        self.assertIsNotNone(err)
        self.assertTrue(s.global_enabled)
        self.assertTrue((self.tmp / "settings.json.corrupt").exists())

    def test_reset_to_defaults(self):
        s = build_default_settings()
        s.app_items = []
        self.store.save(s)
        fresh = self.store.reset_to_defaults()
        self.assertGreater(len(fresh.app_items), 0)


if __name__ == "__main__":
    unittest.main()
