"""模态对话框。

版式：浅灰填充的输入框、圆角、右上角 ✕、
右下角「确认」(蓝) +「取消」(橙)。

    ModalBase         模态骨架（居中 / 阻塞 / 回车确认 / Esc 取消）
    PunctuationDialog 增改一条"中文标点 → 英文标点"
    QuickActionDialog 增改一条快捷输入（检测字符串/检测方式/补全内容）
    HotkeyDialog      录制全局热键
    ask_confirm       是/否确认框
    show_message      纯提示框（只有一个按钮）

放在一个模块里的理由：它们共享同一套"模态 + 居中 + 确认/取消"骨架，
拆开反而要复制那份骨架。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from ..core.models import (
    DETECT_LABELS,
    DETECT_WORD,
    PunctuationRule,
    QuickAction,
)
from ..services.template import VARIABLES
from . import icons, theme, widgets


class ModalBase(tk.Toplevel):
    """所有对话框的共同骨架。"""

    def __init__(self, master, *, title: str, width: int = 420, height: int = 300) -> None:
        super().__init__(master)
        self.withdraw()
        self.title(title)
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self._width, self._height = width, height
        self._warn_label: Optional[tk.Label] = None

        self.body = tk.Frame(self, bg=theme.BG)
        self.body.pack(fill="both", expand=True, padx=22, pady=(18, 0))

        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x", padx=22, pady=(12, 16))
        self._confirm = ttk.Button(bar, text="确认", style="Primary.TButton",
                                   command=self._on_confirm)
        self._confirm.pack(side="right")
        # 留个引用：纯提示框（只有一个按钮）需要把它藏起来
        self._cancel = ttk.Button(bar, text="取消", style="Warning.TButton",
                                  command=self._on_cancel)
        self._cancel.pack(side="right", padx=(0, 8))

        # 回车 = 确认。绑在**哪里**是个坑，见下：
        #
        # ⚠ 不能在对话框（Toplevel）上绑 <Return>：Tk 会把它塞进每个子控件的
        #    bindtags 链尾部（实测多行 Text 的 bindtags =
        #    ('...!text', 'Text', '.!quickactiondialog', 'all')），于是任何子控件里的
        #    回车都会冒泡上来触发确认 —— 多行补全内容框里一敲回车就把对话框关了，
        #    多行代码根本打不进去（v1.0.1 的真实事故，v1.0.2 第一次没修干净）。
        #
        # 策略：一旦用了多行输入（text_area），就**只在单行 entry 上**绑回车确认，
        # 对话框层面绝不绑 —— 多行框里的回车留给它自己换行。
        # 纯确认框（ask_confirm / show_message，没有多行框）才在对话框上绑，
        # 那边回车确认是想要的行为，也不会误伤任何输入。
        self._multiline_used = False
        self._toplevel_enter_bound = False
        self.bind("<Escape>", lambda _e: self._on_cancel())

        CloseButton(self, command=self._on_cancel).place(relx=1.0, x=-12, y=10,
                                                        anchor="ne")

    # ------------------------------------------------------------ 布局辅助

    def field_label(self, text: str, *, top: int = 0) -> tk.Label:
        lbl = tk.Label(self.body, text=text, bg=theme.BG, fg=theme.TEXT,
                       font=theme.F_BODY)
        lbl.pack(anchor="w", pady=(top, 6))
        return lbl

    def _bind_enter_to_confirm(self, widget) -> None:
        """只给"单个控件"绑回车确认，并吃掉事件不让它继续冒泡。

        绑定在具体控件（而不是 Toplevel）上，多行 Text 里的回车才不会误触发确认；
        ``return "break"`` 阻断 bindtags 的后续传播，防止同一事件被上层再处理一次。
        """
        widget.bind("<Return>", self._on_enter_confirm)

    def _on_enter_confirm(self, _event) -> str:
        self._on_confirm()
        return "break"

    def entry(self, *, textvariable=None) -> tk.Entry:
        e = tk.Entry(self.body, textvariable=textvariable, bg=theme.SUNKEN,
                     fg=theme.TEXT, relief="flat", font=theme.F_BODY,
                     insertbackground=theme.TEXT, highlightthickness=1,
                     highlightbackground=theme.SUNKEN,
                     highlightcolor=theme.PRIMARY)
        e.pack(fill="x", ipady=7)
        # 单行输入框里回车 = 确认（此时不会想输入换行）
        self._bind_enter_to_confirm(e)
        return e

    def combo(self, values, *, textvariable=None, state: str = "readonly"):
        c = ttk.Combobox(self.body, values=list(values), textvariable=textvariable,
                         state=state, style="TCombobox")
        c.pack(fill="x", ipady=2)
        return c

    def text_area(self, *, height: int = 9) -> tk.Text:
        # 用了多行框 → 禁止对话框层面的回车确认（见 __init__ 的说明）。
        self._multiline_used = True
        if self._toplevel_enter_bound:
            self.unbind("<Return>")
            self._toplevel_enter_bound = False
        wrap = tk.Frame(self.body, bg=theme.SUNKEN, highlightthickness=0)
        wrap.pack(fill="both", expand=True)
        t = tk.Text(wrap, height=height, bg=theme.SUNKEN, fg=theme.TEXT,
                    relief="flat", font=theme.F_BODY, wrap="none",
                    insertbackground=theme.TEXT, highlightthickness=0,
                    padx=8, pady=8)
        t.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical",
                           style="Thin.Vertical.TScrollbar", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        return t

    def hint(self, text: str, *, top: int = 12) -> tk.Label:
        lbl = tk.Label(self.body, text=text, bg=theme.BG, fg=theme.TEXT_MUTED,
                       font=theme.F_SMALL, wraplength=self._width - 60,
                       justify="left")
        lbl.pack(anchor="w", pady=(top, 0))
        return lbl

    # ------------------------------------------------------------ 生命周期

    def show(self):
        """居中显示并阻塞，返回 ``self.result``（取消为 None）。"""
        self.update_idletasks()
        # 纯确认框（没有多行输入）在对话框层面绑回车确认：此时没有可输入换行的控件，
        # 回车确认是想要的手感。带多行框的对话框绝不绑，免得回车冒泡把框关掉。
        if not self._multiline_used and not self._toplevel_enter_bound:
            self._bind_enter_to_confirm(self)
            self._toplevel_enter_bound = True
        w = self._width
        h = max(self._height, self.winfo_reqheight())
        x = self.winfo_screenwidth() // 2 - w // 2
        y = max(0, self.winfo_screenheight() // 2 - h // 2 - 30)
        self.geometry(f"{w}x{h}+{max(0, x)}+{y}")
        self.deiconify()
        self.lift()
        self.grab_set()
        self.focus_force()
        self.wait_window(self)
        return self.result

    def _on_confirm(self) -> None:
        result = self.collect()
        if result is None:
            return               # 校验没过，对话框保持打开
        self.result = result
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def collect(self):
        """子类实现：校验并返回结果；返回 None 表示校验失败。"""
        return True

    def warn(self, message: str) -> None:
        self.bell()
        if self._warn_label is None:
            self._warn_label = tk.Label(self, text="", bg=theme.BG,
                                        fg=theme.DANGER, font=theme.F_SMALL)
            self._warn_label.place(relx=0.0, x=22, rely=1.0, y=-54, anchor="sw")
        self._warn_label.configure(text=message)


class CloseButton(tk.Canvas):
    """对话框右上角的 ✕。"""

    def __init__(self, master, *, command: Callable[[], None], size: int = 26,
                 bg: str = theme.BG) -> None:
        super().__init__(master, width=size, height=size, bg=bg, bd=0,
                         highlightthickness=0, cursor="hand2")
        self._size, self._command = size, command
        self._render(False)
        self.bind("<Button-1>", lambda _e: self._command())
        self.bind("<Enter>", lambda _e: self._render(True))
        self.bind("<Leave>", lambda _e: self._render(False))

    def _render(self, hover: bool) -> None:
        self.delete("all")
        s = self._size
        if hover:
            widgets.rounded_rect(self, 1, 1, s - 1, s - 1, 6,
                                 fill=theme.HOVER, outline=theme.HOVER)
        icons.draw(self, "close", s * 0.50,
                   theme.DANGER if hover else theme.TEXT_MUTED,
                   s * 0.25, s * 0.25)


# ------------------------------------------------------------------ 标点


class PunctuationDialog(ModalBase):
    """增改一条标点映射。"""

    def __init__(self, master, *, rule: Optional[PunctuationRule] = None) -> None:
        is_new = rule is None
        super().__init__(master, title="添加标点" if is_new else "编辑标点",
                         width=420, height=250)
        rule = rule or PunctuationRule()
        self._var_fw = tk.StringVar(value=rule.fullwidth)
        self._var_hw = tk.StringVar(value=rule.halfwidth)

        self.field_label("中文标点（全角）")
        self.entry(textvariable=self._var_fw)
        self.field_label("英文标点（半角）", top=14)
        self.entry(textvariable=self._var_hw)
        self.hint("两个输入框各填 1 个字符，例如中文「，」→ 英文「,」。")

    def collect(self):
        fw = self._var_fw.get()
        hw = self._var_hw.get()
        if len(fw) != 1 or len(hw) != 1:
            self.warn("两个输入框都必须正好填 1 个字符")
            return None
        if fw == hw:
            self.warn("中文标点和英文标点不能相同")
            return None
        return PunctuationRule(fullwidth=fw, halfwidth=hw, enabled=True)


# ------------------------------------------------------------------ 快捷输入


class QuickActionDialog(ModalBase):
    """增改一条快捷输入：检测字符串 / 检测方式 / 补全内容。"""

    def __init__(self, master, *, action: Optional[QuickAction] = None) -> None:
        is_new = action is None
        super().__init__(master, title="添加快捷输入" if is_new else "编辑快捷输入",
                         width=520, height=440)
        action = action or QuickAction(detect_mode=DETECT_WORD)
        self._var_trigger = tk.StringVar(value=action.trigger)
        self._var_mode = tk.StringVar(
            value=DETECT_LABELS.get(action.detect_mode, DETECT_LABELS[DETECT_WORD]))

        self.field_label("检测字符串")
        self.entry(textvariable=self._var_trigger)

        self.field_label("检测方式", top=14)
        self.combo(list(DETECT_LABELS.values()), textvariable=self._var_mode)

        self.field_label("补全内容", top=14)
        self.text = self.text_area(height=9)
        self.text.insert("1.0", action.content)

        self._build_variable_bar()
        self.hint("整词检测：敲完触发词后按 Tab 才补全；单字符：命中即补全。\n"
                  "变量写法 ${名字}（中英文名都行）：补全那一刻才换成当时的值。"
                  "想输出字面的 ${ 就写成 $$。")

    # ------------------------------------------------------------ 变量

    def _build_variable_bar(self) -> None:
        """「插入变量」按钮行 —— 点一下把 ``${名字}`` 插到光标处。

        按钮是照着 ``services.template.VARIABLES`` 生成的：那边加一个变量，
        这里自动多一个按钮，不用两头改。

        用 ``pack(side="left")`` 逐行摆，**不用 grid**：grid 在 `fill="x"` 的
        容器里会把列拉开、超出宽度的那几个还会被裁掉（实测「计算机名」被切成
        「计」）。pack 只按内容占位，放不下就老实换行，所见即所得。
        （``Bare.TButton`` 已在样式里设了 ``width=0``，否则每个按钮固定占 102px。）
        """
        box = tk.Frame(self.body, bg=theme.BG)
        box.pack(fill="x", pady=(8, 0))
        tk.Label(box, text="插入变量（补全时才替换成当时的值）",
                 bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.F_SMALL,
                 anchor="w").pack(fill="x", pady=(0, 4))

        per_row = 6                    # 11 个变量摆两行；标签用短名，一行放得下
        for start in range(0, len(VARIABLES), per_row):
            line = tk.Frame(box, bg=theme.BG)
            line.pack(fill="x", anchor="w")
            for var in VARIABLES[start:start + per_row]:
                ttk.Button(line, text=var.label, style="Bare.TButton",
                           command=lambda v=var: self._insert_variable(v)
                           ).pack(side="left", padx=(0, 2), pady=1)

    def _insert_variable(self, var) -> None:
        """把 ``${名字}`` 插到多行框的光标处（没有光标就追加到末尾）。

        用 ``insert`` 标记插入而不是替换选区：用户可能先选了文字想包起来，
        但更常见的是直接插 —— 选中的话 Tk 的 ``insert`` 会替换选区，
        那正好是"把选中的包成变量"的直觉行为，不必额外处理。
        """
        try:
            self.text.insert("insert", var.placeholder())
        except tk.TclError:
            self.text.insert("end-1c", var.placeholder())
        self.text.focus_set()

    def collect(self):
        trigger = self._var_trigger.get()
        if not trigger:
            self.warn("检测字符串不能为空")
            return None
        label = self._var_mode.get()
        mode = DETECT_WORD
        for key, text in DETECT_LABELS.items():
            if text == label:
                mode = key
                break
        return QuickAction(trigger=trigger, detect_mode=mode,
                           content=self.text.get("1.0", "end-1c"), enabled=True)


# ------------------------------------------------------------------ 热键


class HotkeyDialog(ModalBase):
    """录制一个组合键（至少一个修饰键 + 一个可识别的普通键）。"""

    def __init__(self, master, *, current: str = "") -> None:
        super().__init__(master, title="设置全局热键", width=420, height=230)
        from ..platform_win import keylayout

        self._keylayout = keylayout
        self._pending: Optional[str] = None
        self._var = tk.StringVar(value=current or "（按下组合键）")

        self.field_label("当前热键")
        e = self.entry(textvariable=self._var)
        e.configure(state="readonly")
        # entry() 默认给单行输入框绑了「回车 = 确认」，热键框是只读的、
        # 回车会落到 <KeyPress> 录制逻辑上，这里把那条绑定撤掉免得两套逻辑打架。
        e.unbind("<Return>")
        self.hint("点一下输入框，然后直接按组合键（例如 Ctrl+Alt+A）。")
        self.bind("<KeyPress>", self._on_key)

    def _on_key(self, event) -> None:
        kl = self._keylayout
        vk = int(event.keycode)
        if kl.is_modifier_vk(vk):
            return
        # ⚠ 修饰键**不信 event.state**，读物理键状态（GetAsyncKeyState）。
        #   Windows 上 Tk 把 Alt 报在 state 的 0x20000 位，而这里原来查的是
        #   X11 口径的 0x0008 —— 永远为 0，Alt 配什么键都识别不到（真实 bug）。
        #   Ctrl=0x0004 / Shift=0x0001 碰巧是对的，所以只有 Alt 坏。
        mods_state = kl.async_modifiers()
        mods = []
        if mods_state.ctrl:
            mods.append("Ctrl")
        if mods_state.alt:
            mods.append("Alt")
        if mods_state.shift:
            mods.append("Shift")
        name = kl.vk_name(vk)
        if not mods or name.startswith("VK_"):
            self.warn("热键要带 Ctrl/Alt/Shift，且必须是可识别的按键")
            return
        self._pending = "+".join(mods + [name])
        self._var.set(self._pending)

    def collect(self):
        if not self._pending:
            self.warn("还没有录制热键")
            return None
        return self._pending


# ------------------------------------------------------------------ 确认框


def ask_confirm(master, *, title: str, message: str,
                confirm_text: str = "确认", danger: bool = False) -> bool:
    """是/否确认。返回 True 表示用户点了确认。"""

    class _Ask(ModalBase):
        def __init__(self) -> None:
            super().__init__(master, title=title, width=400, height=160)
            tk.Label(self.body, text=message, bg=theme.BG, fg=theme.TEXT_BODY,
                     font=theme.F_BODY, wraplength=340,
                     justify="left").pack(anchor="w")
            self._confirm.configure(
                text=confirm_text,
                style="Warning.TButton" if danger else "Primary.TButton")

        def collect(self):
            return True

    return bool(_Ask().show())


def show_message(master, *, title: str, message: str,
                 ok_text: str = "知道了", wide: bool = False) -> None:
    """纯提示框 —— 只有一个按钮。

    确认框（``ask_confirm``）在"这是结果不是选择"的场合很别扭：
    用户会以为点了「取消」能把刚才的操作撤销。导入结果、导出失败、
    文件格式不对这类**纯告知**都用这个。
    """
    width = 520 if wide else 420

    class _Info(ModalBase):
        def __init__(self) -> None:
            super().__init__(master, title=title, width=width, height=140)
            tk.Label(self.body, text=message, bg=theme.BG, fg=theme.TEXT_BODY,
                     font=theme.F_BODY, wraplength=width - 70,
                     justify="left").pack(anchor="w")
            self._confirm.configure(text=ok_text, style="Primary.TButton")
            self._cancel.pack_forget()          # 只留一个按钮

        def collect(self):
            return True

    _Info().show()


__all__ = ["ModalBase", "PunctuationDialog", "QuickActionDialog", "HotkeyDialog",
           "ask_confirm", "CloseButton"]
