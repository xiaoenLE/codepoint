"""配置导入 / 导出测试。

重点覆盖"宽容导入"：用户手上会有各种奇怪来源的文件 ——
别的程序导出的 json、记事本存过带 BOM 的、旧版本缺字段的、
甚至半截损坏的。这些**都不能把程序搞崩**，要么救回来，要么给一句人话。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from codepoint.core import portable
from codepoint.core.config import build_default_settings
from codepoint.core.models import AppItem, PunctuationRule, QuickAction


def sample_settings():
    s = build_default_settings()
    s.punctuation_rules = [PunctuationRule("，", ",", True),
                           PunctuationRule("。", ".", False)]
    s.quick_actions = [QuickAction("for", "word", "FOR ... END_FOR;", True)]
    s.app_items = [AppItem("CODESYS.exe", display_name="CODESYS")]
    s.match_mode = "all"
    s.toggle_hotkey = "Ctrl+Shift+P"
    return s


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name: str, data, *, encoding: str = "utf-8") -> Path:
        p = self.dir / name
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        p.write_text(text, encoding=encoding)
        return p


class TestExport(TempDirCase):
    def test_round_trip(self):
        s = sample_settings()
        target = portable.write_export(s, self.dir / "backup.json")
        self.assertTrue(target.exists())
        self.assertEqual(target.suffix, ".json")

        result = portable.load_export(target)
        self.assertEqual([r.halfwidth for r in result.settings.punctuation_rules], [",", "."])
        self.assertEqual([r.enabled for r in result.settings.punctuation_rules], [True, False])
        self.assertEqual(result.settings.quick_actions[0].trigger, "for")
        self.assertEqual(result.settings.app_items[0].display_name, "CODESYS")
        self.assertEqual(result.settings.match_mode, "all")
        self.assertEqual(result.settings.toggle_hotkey, "Ctrl+Shift+P")
        self.assertFalse(result.from_bare_file)

    def test_payload_has_identity_fields(self):
        s = sample_settings()
        payload = portable.build_payload(s, now=datetime(2026, 10, 6, 23, 30))
        self.assertEqual(payload["format"], portable.EXPORT_FORMAT)
        self.assertEqual(payload["version"], portable.EXPORT_VERSION)
        self.assertEqual(payload["exported_at"], "2026-10-06T23:30:00")
        self.assertIn("settings", payload)

    def test_suffix_appended_when_missing(self):
        target = portable.write_export(sample_settings(), self.dir / "noext")
        self.assertEqual(target.name, "noext.json")
        self.assertTrue(target.exists())

    def test_overwrite_is_atomic_and_keeps_no_temp_files(self):
        """重复导出应该干净地覆盖，且不留 .tmp 残渣。"""
        target = self.dir / "x.json"
        portable.write_export(sample_settings(), target)
        first = target.read_text(encoding="utf-8")
        s = sample_settings()
        s.toggle_hotkey = "Ctrl+Alt+Z"
        portable.write_export(s, target)
        second = target.read_text(encoding="utf-8")
        self.assertNotEqual(first, second)
        self.assertIn("Ctrl+Alt+Z", second)
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_export_creates_missing_directories(self):
        target = portable.write_export(sample_settings(), self.dir / "a" / "b" / "cfg.json")
        self.assertTrue(target.exists())

    def test_written_file_is_valid_json_with_utf8_chinese(self):
        target = portable.write_export(sample_settings(), self.dir / "cn.json")
        raw = target.read_text(encoding="utf-8")
        self.assertIn("，", raw)                 # 中文标点没被转义成 \uXXXX
        self.assertIsInstance(json.loads(raw), dict)


class TestImportBareFile(TempDirCase):
    """直接把 %APPDATA% 里的 settings.json 拷过来 —— 最常见的用法。"""

    def test_bare_settings_file_is_accepted(self):
        p = self.write("settings.json", sample_settings().to_dict())
        result = portable.load_export(p)
        self.assertTrue(result.from_bare_file)
        self.assertEqual(len(result.settings.punctuation_rules), 2)
        self.assertIn("标点规则", result.describe())

    def test_missing_fields_fall_back_to_defaults(self):
        p = self.write("old.json", {"global_enabled": False})
        result = portable.load_export(p)
        self.assertFalse(result.settings.global_enabled)
        self.assertGreater(len(result.settings.punctuation_rules), 5)   # 补了默认

    def test_list_fields_still_loaded_when_present(self):
        p = self.write("part.json", {"quick_actions": [{"trigger": "xx", "content": "YY"}]})
        result = portable.load_export(p)
        self.assertEqual([q.trigger for q in result.settings.quick_actions], ["xx"])

    def test_bom_from_notepad_is_tolerated(self):
        p = self.write("bom.json", sample_settings().to_dict(), encoding="utf-8-sig")
        self.assertEqual(len(portable.load_export(p).settings.app_items), 1)

    def test_counts_reported(self):
        result = portable.load_export(self.write("s.json", sample_settings().to_dict()))
        self.assertEqual(result.counts["punctuation_rules"], 2)
        self.assertEqual(result.counts["quick_actions"], 1)
        self.assertEqual(result.counts["app_items"], 1)


class TestImportErrors(TempDirCase):
    def test_missing_file(self):
        with self.assertRaises(portable.ConfigFileError) as ctx:
            portable.load_export(self.dir / "nope.json")
        self.assertIn("找不到文件", str(ctx.exception))

    def test_invalid_json_points_at_line(self):
        p = self.write("broken.json", '{"a": 1,,}')
        with self.assertRaises(portable.ConfigFileError) as ctx:
            portable.load_export(p)
        self.assertIn("JSON", str(ctx.exception))

    def test_other_programs_export_rejected(self):
        p = self.write("other.json", {"format": "some-other-app", "settings": {}})
        with self.assertRaises(portable.ConfigFileError) as ctx:
            portable.load_export(p)
        self.assertIn("别的程序", str(ctx.exception))

    def test_json_without_any_known_field_rejected(self):
        p = self.write("random.json", {"hello": "world"})
        with self.assertRaises(portable.ConfigFileError):
            portable.load_export(p)

    def test_non_object_json_rejected(self):
        with self.assertRaises(portable.ConfigFileError):
            portable.load_export(self.write("list.json", "[1, 2, 3]"))

    def test_wrong_encoding_rejected(self):
        # 注意必须带中文：纯 ASCII 的 JSON 在 GBK 和 UTF-8 下字节完全相同，
        # 那样测的是"能不能读 ASCII"，而不是"能不能识别编码错误"
        p = self.dir / "gbk.json"
        p.write_bytes(json.dumps({"quick_actions": [{"trigger": "a", "content": "中文片段"}]},
                                 ensure_ascii=False).encode("gbk"))
        with self.assertRaises(portable.ConfigFileError) as ctx:
            portable.load_export(p)
        self.assertIn("UTF-8", str(ctx.exception))

    def test_our_format_with_broken_settings_section(self):
        p = self.write("half.json", {"format": portable.EXPORT_FORMAT, "settings": 42})
        with self.assertRaises(portable.ConfigFileError):
            portable.load_export(p)

    def test_error_messages_are_chinese(self):
        """报错是给用户看的，必须是中文人话。"""
        for payload in ({"hello": 1}, {"format": "x/y", "settings": {}}):
            with self.assertRaises(portable.ConfigFileError) as ctx:
                portable.load_export(self.write("t.json", payload))
            self.assertTrue(any("\u4e00" <= c <= "\u9fff" for c in str(ctx.exception)))


class TestResultDescription(unittest.TestCase):
    def test_describe_mentions_all_three_tables(self):
        result = portable.ImportResult(
            settings=build_default_settings(),
            counts={"punctuation_rules": 7, "quick_actions": 3, "app_items": 2},
        )
        text = result.describe()
        for piece in ("7 条标点规则", "3 条快捷输入", "2 条程序"):
            self.assertIn(piece, text)

    def test_source_text_for_exported_file(self):
        result = portable.ImportResult(
            settings=build_default_settings(), counts={},
            exported_at="2026-10-06T23:30:00", app_version="1.0.0")
        self.assertIn("2026-10-06T23:30:00", result.source_text())
        self.assertIn("1.0.0", result.source_text())

    def test_source_text_for_bare_file(self):
        result = portable.ImportResult(settings=build_default_settings(), counts={},
                                       from_bare_file=True)
        self.assertIn("settings.json", result.source_text())


if __name__ == "__main__":
    unittest.main()
