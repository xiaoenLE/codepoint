"""「常规设置」页。

布局：分节展示设置名称与右侧控件。
所有改动**即时生效并落盘**，不需要"保存"按钮。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Tuple

from ...core.models import (
    INSERT_LABELS, INSERT_MODES, INSERT_PASTE,
    MATCH_ALL, MATCH_LABELS, MATCH_WHITELIST,
)
from ...services.ai_provider import PRESETS, PROVIDER_MOCK, PROVIDER_OPENAI
from .. import theme
from .base import Page


_CUSTOM_AI_SERVICE = "自定义"


class GeneralPage(Page):
    """常规设置。"""

    def __init__(self, master, facade, **kw) -> None:
        super().__init__(master, **kw)
        self.facade = facade
        self._build()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        settings = self.facade.get_settings()

        self._section("程序生效范围")
        self._choice_row(
            "程序列表模式",
            options=[MATCH_LABELS[MATCH_WHITELIST], MATCH_LABELS[MATCH_ALL]],
            current=MATCH_LABELS.get(settings.match_mode, MATCH_LABELS[MATCH_WHITELIST]),
            on_change=self._on_mode)

        self._section("标点替换")
        self._switch_row("剪贴板中的中文标点转为英文标点",
                         settings.enable_clipboard_replace, self._on_clipboard)
        self._switch_row("仅在中文标点状态下替换",
                         settings.require_ime, self._on_require_ime)
        self._switch_row("输入法选词时保留标点键",
                         settings.smart_guard_compose, self._on_guard)

        self._section("快捷输入")
        self._switch_row("启用快捷输入",
                         settings.quick_action_enabled, self._on_quick_enabled)
        self._switch_row("显示快捷输入的 Tab 提示",
                         settings.indicator_enabled, self._on_indicator)

        self._section("启动")
        self._switch_row("开机自动启动",
                         settings.enable_auto_startup, self._on_autostart)
        self._switch_row("启动时收进托盘",
                         settings.start_hidden, self._on_start_hidden)
        self._switch_row("关闭窗口时收进托盘",
                         settings.minimize_to_tray, self._on_minimize_tray)

        self._section("全局热键")
        self._hotkey_row("暂停 / 恢复热键", settings.toggle_hotkey, self._record_hotkey)

        self._ai_section(settings)

        self._section("配置备份")
        self._backup_row()

        self._section("配置文件")
        self._config_row()

    def refresh(self) -> None:
        """整页重画。

        设置可能被外部整体替换（导入配置），开关/下拉框里的旧值就全是错的，
        逐个同步控件状态容易漏 —— 直接重建最省心。
        """
        self.rebuild()
        self._build()
        super().refresh()

    # ------------------------------------------------------------ 部件

    def _section(self, title: str) -> None:
        holder = tk.Frame(self.content, bg=theme.BG)
        holder.pack(fill="x", padx=self.pad, pady=(14, 6))
        tk.Label(holder, text=title, bg=theme.BG, fg=theme.TEXT,
                 font=theme.F_H1).pack(anchor="w")
        line = tk.Frame(holder, bg=theme.BORDER, height=1)
        line.pack(fill="x", pady=(8, 0))

    def _color_row(self, title: str, current: str,
                   on_change: Callable[[str], None]) -> None:
        """色块 + 「选色」按钮。tkinter 自带取色器就够用，不必自己画调色盘。"""
        from tkinter import colorchooser

        right = self._row(title)
        state = {"color": current}

        swatch = tk.Label(right, text="      ", bg=current, bd=1, relief="flat",
                          font=theme.F_SMALL)
        swatch.pack(side="left", ipady=4)
        code = tk.Label(right, text=current, bg=theme.BG, fg=theme.TEXT_MUTED,
                        font=theme.F_SMALL)
        code.pack(side="left", padx=(6, 0))

        def pick() -> None:
            _rgb, chosen = colorchooser.askcolor(
                color=state["color"], parent=self.winfo_toplevel(),
                title=f"选择{title}")
            if not chosen:
                return
            state["color"] = chosen
            try:
                swatch.config(bg=chosen)
                code.config(text=chosen)
            except tk.TclError:
                return
            on_change(chosen)

        ttk.Button(right, text="选色", style="Ghost.TButton",
                   command=pick).pack(side="left", padx=(8, 0))

    def _alpha_row(self, value: float) -> None:
        right = self._row("不透明度")
        var = tk.DoubleVar(value=float(value))
        pct = tk.Label(right, text=f"{float(value):.0%}", bg=theme.BG, width=5,
                       fg=theme.TEXT_MUTED, font=theme.F_SMALL)
        scale = ttk.Scale(right, from_=0.2, to=1.0, variable=var, length=150,
                          command=lambda v: pct.config(text=f"{float(v):.0%}"))
        scale.pack(side="left")
        pct.pack(side="left", padx=(8, 0))

        def commit(_event=None) -> None:
            self.facade.get_settings().ai_card_alpha = round(float(var.get()), 2)
            self._commit(quiet=True)

        # 只认松手那一下：拖动过程中每帧都写盘既吵又没必要
        scale.bind("<ButtonRelease-1>", commit)

    def _card_size_row(self, settings) -> None:
        right = self._row("字号 · 最大行数 · 宽度")
        specs = (
            ("字", "ai_card_font_size", settings.ai_card_font_size, 8, 24, 3),
            ("行", "ai_card_max_lines", settings.ai_card_max_lines, 3, 60, 3),
            ("宽", "ai_card_width", settings.ai_card_width, 260, 1000, 4),
        )
        for label, field, value, lo, hi, width in specs:
            tk.Label(right, text=label, bg=theme.BG, fg=theme.TEXT_MUTED,
                     font=theme.F_SMALL).pack(side="left", padx=(8, 2))
            var = tk.StringVar(value=str(value))
            spin = ttk.Spinbox(right, from_=lo, to=hi, width=width, textvariable=var,
                               command=lambda f=field, v=var: self._on_card_number(f, v))
            spin.pack(side="left")
            spin.bind("<FocusOut>",
                      lambda e, f=field, v=var: self._on_card_number(f, v))

    def _on_card_number(self, field: str, var) -> None:
        try:
            value = int(str(var.get()).strip())
        except (TypeError, ValueError):
            return
        settings = self.facade.get_settings()
        setattr(settings, field, value)
        settings.normalize()                 # 夹回合法范围，再把夹过的值写回界面
        var.set(str(getattr(settings, field)))
        self._commit(quiet=True)

    def _number_row(self, title: str, field: str, value,
                    lo: float, hi: float, step: float = 1,
                    *, is_float: bool = False) -> None:
        """一个数字输入行（Spinbox）。失焦或点上下箭头才提交。"""
        right = self._row(title)
        var = tk.StringVar(value=f"{float(value):g}")
        spin = ttk.Spinbox(right, from_=lo, to=hi, increment=step, width=6,
                           textvariable=var)
        spin.pack()
        handler = lambda: self._on_number(field, var, is_float)   # noqa: E731
        spin.configure(command=handler)
        spin.bind("<FocusOut>", lambda e: handler())

    def _on_number(self, field: str, var, is_float: bool = False) -> None:
        try:
            value = float(var.get()) if is_float else int(float(var.get()))
        except (TypeError, ValueError):
            return
        settings = self.facade.get_settings()
        setattr(settings, field, value)
        settings.normalize()                 # 夹回合法范围
        current = getattr(settings, field)
        var.set(f"{float(current):g}")
        self._commit(quiet=True)

    def _record_ai_hotkey(self, var: tk.StringVar) -> None:
        from ..dialogs import HotkeyDialog

        settings = self.facade.get_settings()
        self.facade.suspend_hotkeys()
        try:
            result = HotkeyDialog(self.winfo_toplevel(),
                                  current=settings.ai_hotkey or "").show()
        finally:
            self.facade.resume_hotkeys()
        if not result:
            return
        var.set(result)
        settings.ai_hotkey = result
        # _commit → apply_live → _register_ai_hotkey：热键当场换成新的
        self._commit()

    def _row(self, title: str) -> tk.Frame:
        """一行：左边标题，返回右侧操作区。

        ⚠ **右侧必须先 pack**：pack 是按顺序分配空间的，
        如果先 pack 左侧的 ``expand=True`` 容器，它会先把宽度吃掉，
        右侧的开关/下拉框就被挤出可视区（截图上表现为"开关只剩半个"）。
        """
        row = tk.Frame(self.content, bg=theme.BG)
        row.pack(fill="x", padx=self.pad, pady=(8, 0))
        right = tk.Frame(row, bg=theme.BG)
        right.pack(side="right", padx=(12, 0))
        tk.Label(row, text=title, bg=theme.BG, fg=theme.TEXT_BODY,
                 font=theme.F_BODY, anchor="w").pack(side="left", fill="x", expand=True)
        return right

    def _switch_row(self, title: str, value: bool,
                    on_change: Callable[[bool], None]) -> None:
        from .. import widgets

        right = self._row(title)
        sw = widgets.ToggleSwitch(right, value=value, command=on_change)
        sw.pack()

    def _choice_row(self, title: str, *, options: List[str],
                    current: str, on_change: Callable[[str], None]) -> tk.StringVar:
        right = self._row(title)
        var = tk.StringVar(value=current)

        def _changed(_event=None):
            on_change(var.get())

        combo = ttk.Combobox(right, values=options, textvariable=var,
                             state="readonly", style="TCombobox", width=22)
        combo.pack()
        combo.bind("<<ComboboxSelected>>", _changed)
        return var

    def _hotkey_row(self, title: str, current: str,
                    on_record: Callable[[tk.StringVar], None]) -> tk.Frame:
        right = self._row(title)
        var = tk.StringVar(value=current or "（未设置）")
        entry = tk.Entry(right, textvariable=var, width=16, state="readonly",
                         bg=theme.SUNKEN, fg=theme.TEXT, relief="flat",
                         font=theme.F_BODY, justify="center",
                         highlightthickness=0)
        entry.pack(side="left", ipady=5)
        ttk.Button(right, text="录制", style="Ghost.TButton",
                   command=lambda: on_record(var)).pack(side="left", padx=(6, 0))
        return right

    def _config_row(self) -> None:
        from ...core.config import resolve_config_dir

        right = self._row("配置目录")
        path = str(resolve_config_dir())
        tk.Label(right, text=path, bg=theme.BG, fg=theme.TEXT_MUTED,
                 font=theme.F_SMALL).pack(side="left")
        ttk.Button(right, text="打开目录", style="Ghost.TButton",
                   command=lambda: self._open_dir(path)).pack(side="left", padx=(8, 0))

    def _backup_row(self) -> None:
        right = self._row("导入 / 导出配置")
        ttk.Button(right, text="导入…", style="Ghost.TButton",
                   command=self._import_config).pack(side="left", padx=(0, 6))
        ttk.Button(right, text="导出…", style="Tool.TButton",
                   command=self._export_config).pack(side="left")

    # ------------------------------------------------------------ AI 补全

    def _ai_section(self, settings) -> None:
        from ...services.ai_provider import FIM_FAMILY_CHOICES, FIM_STRATEGY_CHOICES

        self._section("AI 补全")

        self._switch_row("启用 AI 补全",
                         settings.ai_enabled, self._on_ai_enabled)

        self._ai_preset_var = self._choice_row(
            "AI 服务",
            options=[p.label for p in PRESETS] + [_CUSTOM_AI_SERVICE],
            current=self._preset_label(settings),
            on_change=self._on_ai_preset)

        self._ai_fields = {
            "ai_base_url": self._entry_row(
                "API 地址",
                settings.ai_base_url, lambda text: self._on_ai_field("ai_base_url", text)),
            "ai_model": self._entry_row(
                "模型名", settings.ai_model, lambda text: self._on_ai_field("ai_model", text)),
            "ai_api_key": self._entry_row(
                "API Key",
                settings.ai_api_key, lambda text: self._on_ai_field("ai_api_key", text), secret=True),
        }
        self._update_ai_fields(settings.ai_provider)

        summaries = (
            ("生成参数", f"超时 {settings.ai_timeout:.0f} 秒 · 温度 {settings.ai_temperature:.2f}"
                        f" · 最多 {settings.ai_max_tokens} token"),
            ("上下文字数上限", f"光标前 {settings.ai_prefix_max_chars} · 光标后 {settings.ai_suffix_max_chars}"),
        )
        for title, value in summaries:
            right = self._row(title)
            tk.Label(right, text=value, bg=theme.BG, fg=theme.TEXT_BODY,
                     font=theme.F_BODY).pack()

        self._switch_row(
            "关闭模型思考",
            settings.ai_disable_thinking, self._on_disable_thinking)

        right = self._hotkey_row("AI 补全热键", settings.ai_hotkey, self._record_ai_hotkey)
        ttk.Button(right, text="测试连接", style="Tool.TButton",
                   command=self._on_test_ai).pack(side="left", padx=(6, 0))

        self._section("AI 补全预览窗")
        self._switch_row("插入前显示预览",
                         settings.ai_card_enabled, self._on_card_enabled)
        self._color_row("背景色", settings.ai_card_bg, self._on_card_bg)
        self._color_row("文字颜色", settings.ai_card_fg, self._on_card_fg)
        self._alpha_row(settings.ai_card_alpha)
        self._card_size_row(settings)
        self._number_row(
            "预览关闭时间（秒，0 表示常驻）",
            "ai_card_timeout", settings.ai_card_timeout, 0, 300, 1, is_float=True)
        self._number_row(
            "读取光标前的行数",
            "ai_context_lines", settings.ai_context_lines, 0, 30, 1)

        self._switch_row(
            "AI 补全遵守程序生效范围",
            settings.ai_scope_limited, self._on_scope_limited)

        self._choice_row(
            "AI 补全和快捷输入的写入方式",
            options=[INSERT_LABELS[m] for m in INSERT_MODES],
            current=INSERT_LABELS.get(
                settings.ai_insert_mode, INSERT_LABELS[INSERT_PASTE]),
            on_change=self._on_insert_mode)

        self._section("代码中间补全（FIM）")
        self._switch_row(
            "结合光标前后的代码补全",
            settings.ai_fim_enabled, self._on_fim_enabled)
        self._choice_row(
            "FIM 请求方式",
            options=[label for _value, label in FIM_STRATEGY_CHOICES],
            current=self._fim_choice_label(
                FIM_STRATEGY_CHOICES, settings.ai_fim_strategy),
            on_change=self._on_fim_strategy)
        self._choice_row(
            "模型的 FIM token 格式",
            options=[label for _value, label in FIM_FAMILY_CHOICES],
            current=self._fim_choice_label(
                FIM_FAMILY_CHOICES, settings.ai_fim_tokens),
            on_change=self._on_fim_tokens)
        self._number_row(
            "读取光标后的行数",
            "ai_suffix_lines", settings.ai_suffix_lines, 0, 10, 1)

    def _fim_choice_label(self, choices, value: str) -> str:
        """把存下来的值翻成界面上的文字。认不出来就显示第一项（自动）。"""
        for item_value, label in choices:
            if item_value == (value or "auto"):
                return label
        return choices[0][1]

    def _preset_label(self, settings) -> str:
        # provider=mock 时先认它：默认 settings 里 base_url 预填的是 llama.cpp 的
        # 地址，若先按地址匹配，界面上会显示成 "llama.cpp" 而实际在跑演练模式。
        if settings.ai_provider == PROVIDER_MOCK:
            return PRESETS[0].label
        base = (settings.ai_base_url or "").rstrip("/")
        model = (settings.ai_model or "").strip()
        for preset in PRESETS:
            if preset.base_url and preset.base_url.rstrip("/") == base and preset.model == model:
                return preset.label
        return _CUSTOM_AI_SERVICE

    def _entry_row(self, title: str, value: str,
                   on_commit: Callable[[str], None],
                   *, secret: bool = False) -> Tuple[tk.StringVar, tk.Entry]:
        """一行输入框。**失焦或回车才提交** —— 每敲一个字符就 save 一次太吵。"""
        right = self._row(title)
        var = tk.StringVar(value=value or "")
        entry = tk.Entry(right, textvariable=var, width=30,
                         bg=theme.SUNKEN, readonlybackground=theme.SUNKEN,
                         fg=theme.TEXT, relief="flat",
                         font=theme.F_SMALL, highlightthickness=0,
                         show="*" if secret else "")
        entry.pack(ipady=5)
        def _flush(_event=None) -> None:
            if entry.cget("state") == "normal":
                on_commit(var.get().strip())

        entry.bind("<FocusOut>", _flush)
        entry.bind("<Return>", _flush)
        return var, entry

    # ------------------------------------------------------------ 回调

    def _commit(self, *, live: bool = True, save: bool = True,
                quiet: bool = False) -> None:
        if live:
            self.facade.apply_live()
        if save:
            self.facade.save()
        if not quiet:
            self.facade.notify("已保存", "设置已生效。")

    def _on_mode(self, label: str) -> None:
        mode = MATCH_ALL if MATCH_LABELS.get(MATCH_ALL) == label else MATCH_WHITELIST
        self.facade.get_settings().match_mode = mode
        self._commit(save=True)

    def _on_clipboard(self, value: bool) -> None:
        self.facade.get_settings().enable_clipboard_replace = value
        self._commit()

    def _on_require_ime(self, value: bool) -> None:
        self.facade.get_settings().require_ime = value
        self._commit()

    def _on_guard(self, value: bool) -> None:
        self.facade.get_settings().smart_guard_compose = value
        self._commit()

    def _on_quick_enabled(self, value: bool) -> None:
        self.facade.get_settings().quick_action_enabled = value
        self._commit()

    def _on_indicator(self, value: bool) -> None:
        self.facade.get_settings().indicator_enabled = value
        self._commit()

    # —— AI 补全：输入框走 quiet，避免每失焦一次就弹一条"已保存" ——

    def _on_ai_enabled(self, value: bool) -> None:
        self.facade.get_settings().ai_enabled = value
        self._commit(quiet=True)

    def _on_ai_preset(self, label: str) -> None:
        settings = self.facade.get_settings()
        if label == _CUSTOM_AI_SERVICE:
            settings.ai_provider = PROVIDER_OPENAI
        else:
            preset = next((p for p in PRESETS if p.label == label), None)
            if preset is None:
                return
            settings.ai_provider = preset.provider
            if preset.base_url:
                settings.ai_base_url = preset.base_url
                self._ai_fields["ai_base_url"][0].set(preset.base_url)
            if preset.model:
                settings.ai_model = preset.model
                self._ai_fields["ai_model"][0].set(preset.model)
        self._update_ai_fields(settings.ai_provider)
        self._commit(quiet=True)

    def _update_ai_fields(self, provider: str) -> None:
        editable = provider == PROVIDER_OPENAI
        for _, entry in self._ai_fields.values():
            entry.configure(state="normal" if editable else "readonly", takefocus=editable)

    def _on_ai_field(self, field: str, text: str) -> None:
        settings = self.facade.get_settings()
        if settings.ai_provider != PROVIDER_OPENAI or getattr(settings, field) == text:
            return
        setattr(settings, field, text)
        self._ai_preset_var.set(self._preset_label(settings))
        self._commit(quiet=True)

    def _on_disable_thinking(self, value: bool) -> None:
        self.facade.get_settings().ai_disable_thinking = value
        self._commit()

    # —— FIM ——
    #
    # 下拉里显示的是中文标签，存进 settings.json 的是 "auto"/"server"/"token"
    # 这种值 —— 存标签的话，以后改文案就把老配置读坏了。

    def _on_fim_enabled(self, value: bool) -> None:
        self.facade.get_settings().ai_fim_enabled = value
        self._commit(quiet=True)

    def _on_fim_strategy(self, label: str) -> None:
        from ...services.ai_provider import FIM_STRATEGY_CHOICES

        settings = self.facade.get_settings()
        settings.ai_fim_strategy = next(
            (v for v, text in FIM_STRATEGY_CHOICES if text == label), "auto")
        self._commit(quiet=True)

    def _on_fim_tokens(self, label: str) -> None:
        from ...services.ai_provider import FIM_FAMILY_CHOICES

        settings = self.facade.get_settings()
        settings.ai_fim_tokens = next(
            (v for v, text in FIM_FAMILY_CHOICES if text == label), "auto")
        self._commit(quiet=True)

    def _on_insert_mode(self, label: str) -> None:
        settings = self.facade.get_settings()
        settings.ai_insert_mode = next(
            (v for v, text in INSERT_LABELS.items() if text == label), INSERT_PASTE)
        self._commit(quiet=True)

    def _on_scope_limited(self, value: bool) -> None:
        self.facade.get_settings().ai_scope_limited = value
        self._commit()

    def _on_test_ai(self) -> None:
        # 结果走托盘通知（可能在十几秒后才到），按钮不做"转圈"状态 ——
        # 一次点击最多发一个请求，重复点也不会有副作用。
        self.facade.test_ai()

    def _on_card_enabled(self, value: bool) -> None:
        self.facade.get_settings().ai_card_enabled = value
        self._commit(quiet=True)

    def _on_card_bg(self, text: str) -> None:
        self.facade.get_settings().ai_card_bg = text
        self._commit(quiet=True)

    def _on_card_fg(self, text: str) -> None:
        self.facade.get_settings().ai_card_fg = text
        self._commit(quiet=True)

    def _on_autostart(self, value: bool) -> None:
        ok = self.facade.set_autostart(value)
        settings = self.facade.get_settings()
        settings.enable_auto_startup = bool(value and ok)
        self.facade.save()
        if not ok:
            self.facade.notify("开机自启失败", "写注册表被拒绝（可能被安全软件拦了）。")

    def _on_start_hidden(self, value: bool) -> None:
        self.facade.get_settings().start_hidden = value
        self._commit(live=False)

    def _on_minimize_tray(self, value: bool) -> None:
        self.facade.get_settings().minimize_to_tray = value
        self._commit(live=False)

    def _record_hotkey(self, var: tk.StringVar) -> None:
        from ..dialogs import HotkeyDialog

        # 录制期间必须把全局热键注销掉：不然按到已注册的组合键会被
        # RegisterHotKey 吃掉 key-down，对话框只收到 key-up。
        self.facade.suspend_hotkeys()
        try:
            result = HotkeyDialog(self.winfo_toplevel(),
                                  current=var.get()).show()
        finally:
            self.facade.resume_hotkeys()
        if not result:
            return
        var.set(result)
        self.facade.get_settings().toggle_hotkey = result
        self._commit()

    def _open_dir(self, path: str) -> None:
        import os
        import subprocess

        try:
            os.startfile(path)      # noqa: S606 —— Windows 专用，打开资源管理器
        except Exception:      # noqa: BLE001
            try:
                subprocess.Popen(["explorer", path])
            except Exception:      # noqa: BLE001
                self.facade.notify("打不开", f"配置目录：{path}")

    # ------------------------------------------------------------ 配置导入导出

    def _export_config(self) -> None:
        from tkinter import filedialog

        from ...core.portable import DEFAULT_FILENAME, FILE_TYPES

        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="导出配置",
            initialfile=DEFAULT_FILENAME,
            defaultextension=".json",
            filetypes=list(FILE_TYPES),
        )
        if not path:
            return                       # 用户取消了
        ok, message = self.facade.export_config(path)
        self.facade.notify("导出配置" if ok else "导出失败", message)

    def _import_config(self) -> None:
        from tkinter import filedialog

        from ...core.portable import FILE_TYPES
        from ..dialogs import ask_confirm, show_message

        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="导入配置",
            filetypes=list(FILE_TYPES),
        )
        if not path:
            return

        # ① 先解析，把"里面有什么"摆出来 —— 导错文件（比如选了个别的 json）
        #    能在这一步就挡住，而不是等导完发现规则全没了
        ok, summary = self.facade.inspect_config(path)
        if not ok:
            show_message(self.winfo_toplevel(), title="这个文件不能用",
                         message=summary, ok_text="知道了", wide=True)
            return

        # ② 明确告知"会覆盖"，用危险色按钮
        if not ask_confirm(
            self.winfo_toplevel(),
            title="导入配置",
            message=f"{summary}\n\n"
                    f"现有配置会被**整个替换**（当前 "
                    f"{len(self.facade.get_settings().punctuation_rules)} 条标点规则、"
                    f"{len(self.facade.get_settings().quick_actions)} 条快捷输入、"
                    f"{len(self.facade.get_settings().app_items)} 个程序）。\n"
                    f"替换前会自动备份到配置目录。",
            confirm_text="导入并替换",
            danger=True,
        ):
            return

        # ③ 导入 → 立即生效 + 落盘 → 整页重画（开关/表格全刷成新值）
        ok, message = self.facade.import_config(path)
        if not ok:
            show_message(self.winfo_toplevel(), title="导入失败",
                         message=message, ok_text="知道了", wide=True)
            return
        self._refresh_everything()
        show_message(self.winfo_toplevel(), title="导入完成", message=message)

    def _refresh_everything(self) -> None:
        """让**所有**页面重新读配置（导入是全局替换，不能只刷自己这一页）。"""
        window = self.winfo_toplevel()
        refresh_all = getattr(window, "refresh_all", None)
        if callable(refresh_all):
            refresh_all()
        else:
            self.refresh()


__all__ = ["GeneralPage"]
