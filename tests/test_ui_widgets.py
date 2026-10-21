"""界面层静态检查 + 控件行为测试。

**为什么要有这个文件**：界面上大量用 ``theme.XXX`` 取颜色，而 Python 只有在
真的执行到那一行时才会发现"常量名写错了"。于是出现一种极难查的故障：

    鼠标碰到开关 → _render(hover=True) → AttributeError
                 → 而 delete("all") 已经先执行了 → 开关变成一片空白
                 → 异常又发生在 command 回调之前 → 点开关永远不生效

表面上像"点了没反应/开关自己消失了"，实际是拼错了一个常量名。
静态扫一遍所有 ``theme.`` 引用，2 毫秒就能把这类问题拦住。
"""

from __future__ import annotations

import pathlib
import re
import unittest

from codepoint.ui import theme

UI_DIR = pathlib.Path(__file__).resolve().parents[1] / "codepoint" / "ui"
PATTERN = re.compile(r"\btheme\.([A-Z][A-Z0-9_]*)")


def _defined() -> set:
    return {name for name in dir(theme) if name.isupper()}


def _referenced():
    """所有 ``theme.XXX`` 引用点。跳过 theme.py 自己（那里的 XXX 只是文档示例）。"""
    for path in sorted(UI_DIR.rglob("*.py")):
        if path.name == "theme.py":
            continue
        src = path.read_text(encoding="utf-8")
        for name in sorted(set(PATTERN.findall(src))):
            yield path, name


class TestThemeConstants(unittest.TestCase):
    def test_every_referenced_constant_exists(self):
        defined = _defined()
        missing = {}
        for path, name in _referenced():
            if name not in defined:
                missing.setdefault(f"{path.name}", []).append(name)
        self.assertEqual(
            missing, {},
            f"这些 theme 常量被引用但没定义（界面会静默失效）：{missing}",
        )

    def test_color_values_are_valid_hex(self):
        """凡是"看起来是颜色"的常量（值是 # 开头的字符串）都必须是合法 #RRGGBB。

        尺寸/字体常量（F_H1、ROW_HEIGHT…）不参与这条检查。
        """
        bad = []
        for name in _defined():
            value = getattr(theme, name)
            if isinstance(value, str) and value.startswith("#"):
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    bad.append((name, value))
        self.assertEqual(bad, [], f"这些颜色值不合法：{bad}")

    def test_switch_colors_present(self):
        """开关用到的一整套颜色必须齐 —— 少一个就整块变空白。"""
        for name in ("SUCCESS", "SUCCESS_HOVER", "SWITCH_OFF",
                     "SWITCH_OFF_HOVER", "SWITCH_DISABLED"):
            self.assertTrue(hasattr(theme, name), f"theme 少了 {name}")


class TestToggleSwitchBehaviour(unittest.TestCase):
    """真建一个 Tk 窗口点它。没有桌面会话时跳过。"""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk

            cls.root = tk.Tk()
            # ⚠ 不能 withdraw()：窗口没映射时 event_generate 的事件会被直接丢掉，
            #   测出来是"回调没触发"，看着像代码有 bug。挪到屏幕外即可。
            cls.root.geometry("200x120+4000+4000")
            cls.root.update()
        except Exception as exc:      # noqa: BLE001
            raise unittest.SkipTest(f"没有可用的 Tk 会话：{exc}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:      # noqa: BLE001
            pass

    def _switch(self, value: bool = True):
        from codepoint.ui import widgets

        events = []
        sw = widgets.ToggleSwitch(self.root, value=value,
                                  command=events.append)
        sw.pack()
        self.root.update()
        return sw, events

    def test_click_fires_callback_with_flipped_value(self):
        sw, events = self._switch(value=True)
        sw.event_generate("<Button-1>", x=22, y=12)
        self.root.update()
        self.assertEqual(events, [False])
        sw.event_generate("<Button-1>", x=22, y=12)
        self.root.update()
        self.assertEqual(events, [False, True])
        sw.destroy()

    def test_click_keeps_widget_drawn(self):
        """点完/悬停之后画布上必须有东西 —— 空了就是渲染抛异常了。"""
        sw, _ = self._switch(value=True)
        for event in ("<Enter>", "<Button-1>", "<Leave>", "<Button-1>"):
            sw.event_generate(event, x=22, y=12)
            self.root.update()
            self.assertGreater(len(sw.find_all()), 0,
                               f"处理 {event} 之后开关变成空白了（渲染异常）")
        sw.destroy()

    def test_set_value_does_not_fire_callback_by_default(self):
        from codepoint.ui import widgets

        events = []
        sw = widgets.ToggleSwitch(self.root, value=True, command=events.append)
        sw.pack()
        self.root.update()
        sw.set_value(False)
        self.root.update()
        self.assertEqual(events, [])          # 外部同步不应触发回调
        self.assertFalse(sw.value)
        self.assertGreater(len(sw.find_all()), 0)
        sw.destroy()

    def test_disabled_switch_ignores_click(self):
        from codepoint.ui import widgets

        events = []
        sw = widgets.ToggleSwitch(self.root, value=True, command=events.append)
        sw.pack()
        self.root.update()
        sw.set_enabled(False)
        sw.event_generate("<Button-1>", x=22, y=12)
        self.root.update()
        self.assertEqual(events, [])
        self.assertGreater(len(sw.find_all()), 0)
        sw.destroy()


class TestBareButtonSizing(unittest.TestCase):
    """``Bare.TButton`` 必须按文字自然宽度，不能被 ttk 默认宽度撑大。

    踩过的坑：「插入变量」那排小按钮本来想一行摆 6 个，结果每个固定占 102px
    （连「年」这种单字也是），一行只放得下 4 个，超出的直接被裁成半个字。
    根因是 ttk 的 TButton 自带默认宽度 —— 必须在样式里写 ``width=0``。
    """

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk

            cls.root = tk.Tk()
            cls.root.geometry("200x120+4000+4000")
            from codepoint.ui import styles

            styles.install_styles(cls.root)
            cls.root.update()
        except Exception as exc:      # noqa: BLE001
            raise unittest.SkipTest(f"没有可用的 Tk 会话：{exc}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:      # noqa: BLE001
            pass

    def test_bare_button_width_follows_text(self):
        from tkinter import ttk

        short = ttk.Button(self.root, text="年", style="Bare.TButton")
        long_ = ttk.Button(self.root, text="计算机名", style="Bare.TButton")
        short.pack()
        long_.pack()
        self.root.update_idletasks()

        self.assertLess(short.winfo_reqwidth(), 60,
                        "Bare.TButton 被 ttk 默认宽度撑大了，小按钮会排不下")
        self.assertLess(long_.winfo_reqwidth(), 90)
        self.assertGreater(long_.winfo_reqwidth(), short.winfo_reqwidth(),
                           "按钮宽度没跟着文字走，说明宽度被写死了")
        short.destroy()
        long_.destroy()


if __name__ == "__main__":
    unittest.main()
