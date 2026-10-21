"""对话框里回车键的行为。

**为什么要这个文件**（真实事故，修了两次才对）：

    在「快捷输入」里编辑/新增规则，光标在「补全内容」多行框里打代码，
    敲一次回车想换行 —— 对话框直接确认并关掉了，多行内容根本写不进去。

根因：``ModalBase`` 把 ``<Return>`` 绑在了 **Toplevel** 上。
Tk 会把 Toplevel 塞进**每个子控件的 bindtags 链**尾部：

    Text 的 bindtags = ('...!text', 'Text', '.!quickactiondialog', 'all')
                                              ^^^^^^^^^^^^^^^^^^^^^ 就是它

于是任何子控件里的回车都会冒泡到 Toplevel 触发确认，包括本来就该换行的多行 ``Text``。

⚠ **这条测试为什么不敢再用 ``event_generate``**：v1.0.2 第一版就是这么测的，
未映射/未获焦的控件上 ``event_generate`` 根本不派发事件（实测所有回调计数都是 0），
于是"测过了"是假象，真机上 bug 照旧。真正可靠的不变量是**绑定表**本身 ——
所以这里直接查 bindtags 与 ``bind("<Return>")``，不依赖事件派发。
"""

from __future__ import annotations

import unittest


class _DialogHarness(unittest.TestCase):
    """在一块公共 root 上起对话框。没有 Tk 会话时整类跳过。"""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk

            cls.root = tk.Tk()
            cls.root.geometry("320x200+4000+4000")
            cls.root.update()
        except Exception as exc:      # noqa: BLE001
            raise unittest.SkipTest(f"没有可用的 Tk 会话：{exc}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:      # noqa: BLE001
            pass

    def _simulate_show_binding(self, dlg) -> None:
        """复刻 ``ModalBase.show()`` 里那段绑定决策（``show()`` 本身会阻塞）。"""
        dlg.update_idletasks()
        if not dlg._multiline_used and not dlg._toplevel_enter_bound:
            dlg._bind_enter_to_confirm(dlg)
            dlg._toplevel_enter_bound = True

    def _has_own_return_binding(self, widget) -> bool:
        return bool(widget.bind("<Return>"))


class TestQuickActionDialogEnter(_DialogHarness):
    """带多行补全框的对话框：对话框层面绝不能绑回车。"""

    def _open(self):
        from codepoint.ui.dialogs import QuickActionDialog

        dlg = QuickActionDialog(self.root)
        dlg.update()
        self._simulate_show_binding(dlg)
        return dlg

    def test_toplevel_has_no_return_binding(self):
        """核心不变量：Toplevel 没绑 <Return>，多行框回车才会冒泡不上去。"""
        dlg = self._open()
        try:
            self.assertTrue(dlg._multiline_used, "这个对话框应当被标记为有多个多行框")
            self.assertFalse(
                self._has_own_return_binding(dlg),
                "Toplevel 绑了 <Return>！多行框里的回车会冒泡上来把对话框关掉")
        finally:
            dlg.destroy()

    def test_text_widget_has_no_return_binding(self):
        """多行框自己不绑回车，回车就该由 Tk 的 Text 类绑定插入换行。"""
        dlg = self._open()
        try:
            self.assertFalse(self._has_own_return_binding(dlg.text),
                             "多行框绑了 <Return>，回车就不能换行了")
        finally:
            dlg.destroy()

    def test_text_bindtags_really_include_toplevel(self):
        """把"为什么不能绑 Toplevel"这条因果钉在测试里。

        哪天 Tk 行为变了（bindtags 不再含 Toplevel），这条会红 —— 提醒我们
        重新评估那个绑定策略，而不是继续照抄一段已经不成立的注释。
        """
        dlg = self._open()
        try:
            self.assertIn(str(dlg), dlg.text.bindtags(),
                          "多行框的 bindtags 里居然没有对话框，说明前提变了")
        finally:
            dlg.destroy()

    def test_single_line_entry_still_confirms(self):
        """单行框回车 = 确认：这条手感不能被修没了。"""
        dlg = self._open()
        try:
            import tkinter as tk

            entry = next(c for c in dlg.body.winfo_children()
                         if isinstance(c, tk.Entry))
            self.assertTrue(self._has_own_return_binding(entry),
                            "单行输入框应当绑回车确认")
        finally:
            dlg.destroy()


class TestSingleFieldDialogEnter(_DialogHarness):
    """只有单行输入的对话框：回车确认应当保留。"""

    def test_punctuation_dialog_binds_toplevel_enter(self):
        from codepoint.ui.dialogs import PunctuationDialog

        dlg = PunctuationDialog(self.root)
        dlg.update()
        self._simulate_show_binding(dlg)
        try:
            self.assertFalse(dlg._multiline_used)
            self.assertTrue(self._has_own_return_binding(dlg),
                            "没有多行框的对话框应当保留回车确认")
        finally:
            dlg.destroy()


class TestNoToplevelReturnInInit(unittest.TestCase):
    """静态防线：``ModalBase.__init__`` 里不许再出现 Toplevel 级 <Return> 绑定。

    只看可执行语句（``ast``），不误伤解释这段坑的注释。
    """

    def test_init_does_not_bind_return_on_self(self):
        import ast
        import inspect
        import textwrap

        from codepoint.ui import dialogs

        src = textwrap.dedent(inspect.getsource(dialogs.ModalBase.__init__))
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "bind"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "self"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value == "<Return>":
                offenders.append(ast.get_source_segment(src, node))

        self.assertEqual(
            offenders, [],
            "ModalBase.__init__ 里绑了 Toplevel 级 <Return>：Tk 会让它冒泡到所有"
            "子控件，多行框里回车就会关掉对话框。应只在单行 entry 上绑。")


if __name__ == "__main__":
    unittest.main()
