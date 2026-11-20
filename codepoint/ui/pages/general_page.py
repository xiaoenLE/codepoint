"""「常规设置」页。

布局：分节的"标题 + 说明 + 右侧开关"，最后是热键与配置目录。
所有改动**即时生效并落盘**，不需要"保存"按钮。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Tuple

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
        self._rows: List[Callable[[], None]] = []
        self._build()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        from ... import __version__

        settings = self.facade.get_settings()

        self._section("生效范围", "决定这个工具在哪些程序里起作用。")
        self._choice_row(
            "",
            "仅列表内的程序：只有主页列表里的程序才转换标点（默认，最安全）。\n"
            "所有程序：任何程序都生效，主页列表变成排除名单。",
            options=[MATCH_LABELS[MATCH_WHITELIST], MATCH_LABELS[MATCH_ALL]],
            current=MATCH_LABELS.get(settings.match_mode, MATCH_LABELS[MATCH_WHITELIST]),
            on_change=self._on_mode)

        self._section("标点替换")
        self._switch_row("剪贴板净化", "复制/粘贴时把中文标点换成英文标点。",
                         settings.enable_clipboard_replace, self._on_clipboard)
        self._switch_row("仅在中文标点状态下替换",
                         "更保守：只有输入法确实处于中文标点状态才动手。\n"
                         "拿不到输入法状态时会放行（宁可不动，也不替你改错）。",
                         settings.require_ime, self._on_require_ime)
        self._switch_row("词组保护",
                         "候选词列表打开时不抢标点键（很多输入法用 , . - 翻页选词）。",
                         settings.smart_guard_compose, self._on_guard)

        self._section("快捷输入")
        self._switch_row("启用快捷输入", "检测字符串命中后补全代码片段。",
                         settings.quick_action_enabled, self._on_quick_enabled)
        self._switch_row("Tab 补全提示（呼吸灯）",
                         "整词命中时在光标旁浮出提示条。只管**显不显示**这块提示 ——\n"
                         "关掉它快捷输入照样能用（打完触发词按 Tab 照样补全），\n"
                         "也**不会**影响 AI 补全预览窗（那是下面「显示预览窗」的事）。",
                         settings.indicator_enabled, self._on_indicator)

        self._section("启动")
        self._switch_row("开机自动启动", "写入注册表 Run 键（当前用户）。",
                         settings.enable_auto_startup, self._on_autostart)
        self._switch_row("启动后最小化到托盘", "开机自启时不弹窗口。",
                         settings.start_hidden, self._on_start_hidden)
        self._switch_row("关闭窗口时收进托盘",
                         "关掉窗口不退出，从托盘右键退出。",
                         settings.minimize_to_tray, self._on_minimize_tray)

        self._section("全局热键")
        self._hotkey_row(settings.toggle_hotkey)

        self._ai_section(settings)

        self._section("配置备份",
                      "换电脑、重装、或者把调好的这套规则给同事用：导出成一个文件带走。")
        self._backup_row()

        self._section("关于配置")
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

    def _section(self, title: str, desc: str = "") -> None:
        holder = tk.Frame(self.content, bg=theme.BG)
        holder.pack(fill="x", padx=self.pad, pady=(14, 6))
        tk.Label(holder, text=title, bg=theme.BG, fg=theme.TEXT,
                 font=theme.F_H1).pack(anchor="w")
        if desc:
            tk.Label(holder, text=desc, bg=theme.BG, fg=theme.TEXT_MUTED,
                     font=theme.F_SMALL, justify="left").pack(anchor="w", pady=(3, 0))
        line = tk.Frame(holder, bg=theme.BORDER, height=1)
        line.pack(fill="x", pady=(8, 0))

    def _color_row(self, title: str, desc: str, current: str,
                   on_change: Callable[[str], None]) -> None:
        """色块 + 「选色」按钮。tkinter 自带取色器就够用，不必自己画调色盘。"""
        from tkinter import colorchooser

        _row, right = self._row(title, desc)
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
        _row, right = self._row("不透明度", "0.2 太透看不清，1.0 完全不透明。")
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
        _row, right = self._row(
            "字号 · 行数 · 宽度",
            "超过宽度截成「…」，超过行数折叠成「还有 N 行」。\n"
            "这里的「行」只管怎么显示，不限制模型能生成几行 —— "
            "想让它少写多写，去上面调生成 token。")
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

    def _number_row(self, title: str, desc: str, field: str, value,
                    lo: float, hi: float, step: float = 1,
                    *, is_float: bool = False) -> None:
        """一个数字输入行（Spinbox）。失焦或点上下箭头才提交。"""
        _row, right = self._row(title, desc)
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

    def _record_ai_hotkey(self) -> None:
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
        settings.ai_hotkey = result
        # _commit → apply_live → _register_ai_hotkey：热键当场换成新的
        self._commit()

    def _row(self, title: str, desc: str = "") -> Tuple[tk.Frame, tk.Frame]:
        """一行：左边文字，右边操作区。返回 (行, 右侧容器)。

        ⚠ **右侧必须先 pack**：pack 是按顺序分配空间的，
        如果先 pack 左侧的 ``expand=True`` 容器，它会先把宽度吃掉，
        右侧的开关/下拉框就被挤出可视区（截图上表现为"开关只剩半个"）。
        """
        row = tk.Frame(self.content, bg=theme.BG)
        row.pack(fill="x", padx=self.pad, pady=(8, 0))
        right = tk.Frame(row, bg=theme.BG)
        right.pack(side="right", padx=(12, 0))
        left = tk.Frame(row, bg=theme.BG)
        left.pack(side="left", fill="x", expand=True)
        if title:
            tk.Label(left, text=title, bg=theme.BG, fg=theme.TEXT_BODY,
                     font=theme.F_BODY, justify="left").pack(anchor="w")
        if desc:
            tk.Label(left, text=desc, bg=theme.BG, fg=theme.TEXT_MUTED,
                     font=theme.F_SMALL, justify="left",
                     wraplength=430).pack(anchor="w", pady=(2, 0))
        return row, right

    def _switch_row(self, title: str, desc: str, value: bool,
                    on_change: Callable[[bool], None]) -> None:
        from .. import widgets

        _row, right = self._row(title, desc)
        sw = widgets.ToggleSwitch(right, value=value, command=on_change)
        sw.pack()

    def _choice_row(self, title: str, desc: str, *, options: List[str],
                    current: str, on_change: Callable[[str], None]) -> tk.StringVar:
        _row, right = self._row(title, desc)
        var = tk.StringVar(value=current)

        def _changed(_event=None):
            on_change(var.get())

        combo = ttk.Combobox(right, values=options, textvariable=var,
                             state="readonly", style="TCombobox", width=22)
        combo.pack()
        combo.bind("<<ComboboxSelected>>", _changed)
        return var

    def _hotkey_row(self, current: str) -> None:
        row, right = self._row("启用/暂停热键",
                               "默认 Ctrl+Alt+A：按一下暂停，再按一下恢复。")
        var = tk.StringVar(value=current or "（未设置）")
        entry = tk.Entry(right, textvariable=var, width=16, state="readonly",
                         bg=theme.SUNKEN, fg=theme.TEXT, relief="flat",
                         font=theme.F_BODY, justify="center",
                         highlightthickness=0)
        entry.pack(side="left", ipady=5)
        ttk.Button(right, text="录制", style="Ghost.TButton",
                   command=lambda: self._record_hotkey(var)).pack(side="left", padx=(6, 0))

    def _config_row(self) -> None:
        from ...core.config import resolve_config_dir

        row, right = self._row("配置文件位置",
                               "改坏了可以直接删，下次启动会用默认值重建。")
        path = str(resolve_config_dir())
        tk.Label(right, text=path, bg=theme.BG, fg=theme.TEXT_MUTED,
                 font=theme.F_SMALL).pack(side="left")
        ttk.Button(right, text="打开目录", style="Ghost.TButton",
                   command=lambda: self._open_dir(path)).pack(side="left", padx=(8, 0))

    def _backup_row(self) -> None:
        # ⚠ 界面上不要用 Markdown 的 ** 强调语法 —— tkinter 不解析，会把星号原样画出来
        _row, right = self._row(
            "导入 / 导出配置",
            "导出：把标点规则、快捷输入、程序列表和所有开关存成一个 .json 文件。\n"
            "导入：整体替换现有配置（导入前会先显示文件里有什么）。")
        ttk.Button(right, text="导入…", style="Ghost.TButton",
                   command=self._import_config).pack(side="left", padx=(0, 6))
        ttk.Button(right, text="导出…", style="Tool.TButton",
                   command=self._export_config).pack(side="left")

    # ------------------------------------------------------------ AI 补全

    def _ai_section(self, settings) -> None:
        from ...services.ai_provider import FIM_FAMILY_CHOICES, FIM_STRATEGY_CHOICES

        self._section(
            "AI 补全",
            "选中代码可改写选区，选中文本需求可生成代码。无选区时读取光标附近的代码。\n"
            "「本地演练」返回固定示例。使用实际模型时请选择相应服务。")

        self._switch_row("启用 AI 补全",
                         "开启后可用下方热键启动生成。",
                         settings.ai_enabled, self._on_ai_enabled)

        self._ai_preset_var = self._choice_row(
            "服务预设",
            "本地演练只显示固定示例。请选择实际服务，或选「自定义」填写地址和模型。",
            options=[p.label for p in PRESETS] + [_CUSTOM_AI_SERVICE],
            current=self._preset_label(settings),
            on_change=self._on_ai_preset)

        self._ai_fields = {
            "ai_base_url": self._entry_row(
                "服务地址",
                "OpenAI 兼容端点。填到 /v1 就够 —— 程序会自己补 /chat/completions。",
                settings.ai_base_url, lambda text: self._on_ai_field("ai_base_url", text)),
            "ai_model": self._entry_row(
                "模型名", "", settings.ai_model, lambda text: self._on_ai_field("ai_model", text)),
            "ai_api_key": self._entry_row(
                "API Key",
                "本地模型留空即可。明文存在 settings.json 里（本机自用没问题，\n"
                "多人共用这台机器时请三思）。",
                settings.ai_api_key, lambda text: self._on_ai_field("ai_api_key", text), secret=True),
        }
        self._update_ai_fields(settings.ai_provider)

        # 超时/温度/最大长度不放到界面上：调它们的人会直接改 settings.json，
        # 不调的人看到三个数字只会犹豫。这里把当前值显示出来，方便核对。
        self._row(
            "高级参数",
            f"超时 {settings.ai_timeout:.0f}s · 温度 {settings.ai_temperature:.2f} · "
            f"最多生成 {settings.ai_max_tokens} tokens（这个才是决定写多少的旋钮）。\n"
            f"上下文宽度：上文 {settings.ai_prefix_max_chars} 字 / "
            f"下文 {settings.ai_suffix_max_chars} 字（超了自动裁，越靠近光标的越优先）。\n"
            "要改就编辑 settings.json：ai_timeout / ai_temperature / ai_max_tokens /\n"
            "ai_prefix_max_chars / ai_suffix_max_chars。")
        self._switch_row(
            "关闭模型思考",
            "推理模型（MiMo 等）会先想几百字再写代码，实测慢 5 倍以上。\n"
            "补全只要\"接着写几行\"，不需要想 —— 默认关掉思考，快很多。",
            settings.ai_disable_thinking, self._on_disable_thinking)

        _row, right = self._row(
            "选中一段生成",
            f"在任意程序里选中文字，按 {settings.ai_hotkey or '（未设置）'}：选中的内容当提示词"
            "（中文也行），生成后弹出预览，按 Tab 插入、Esc 放弃。\n"
            "做法是替你按一次 Ctrl+C 再还原剪贴板，所以原来复制的图片无法还原。",
        )
        ttk.Button(right, text="录制热键", style="Ghost.TButton",
                   command=lambda: self._record_ai_hotkey()).pack(side="left", padx=(0, 6))
        ttk.Button(right, text="测试连接", style="Tool.TButton",
                   command=self._on_test_ai).pack(side="left")

        self._section(
            "补全预览窗",
            "生成好之后先弹出这块窗给你看，按 Tab 才真的插进去；"
            "按 Esc 或随便敲别的键就放弃。")
        self._switch_row("显示预览窗",
                         "关掉之后生成完直接插入，不给你确认的机会（老行为）。",
                         settings.ai_card_enabled, self._on_card_enabled)
        self._color_row("背景色", "这块窗会盖在编辑器上，深色在浅色背景上最清楚。",
                        settings.ai_card_bg, self._on_card_bg)
        self._color_row("文字色", "", settings.ai_card_fg, self._on_card_fg)
        self._alpha_row(settings.ai_card_alpha)
        self._card_size_row(settings)
        self._number_row(
            "自动关闭（秒）",
            "多少秒后自己消失。设 0 = 不自动关闭，一直挂着等你按 Tab 插入\n"
            "或按 Esc 放弃。想提前关随时按 Esc，不受秒数限制。",
            "ai_card_timeout", settings.ai_card_timeout, 0, 300, 1, is_float=True)
        self._number_row(
            "上下文行数",
            "取光标前的代码（默认 6 行，最多 30）。"
            "调 0 时仅使用选区。",
            "ai_context_lines", settings.ai_context_lines, 0, 30, 1)

        self._switch_row(
            "AI 也遵守生效范围",
            "开：只有「生效范围」列表里的程序才能按 AI 热键（推荐）。\n"
            "关：任何程序里都能调用 AI —— 但「窗口切走了 / 焦点在菜单上」"
            "这两道保护仍然有效。",
            settings.ai_scope_limited, self._on_scope_limited)

        self._choice_row(
            "写入方式",
            "AI 结果、快捷输入的补全内容怎么塞进编辑器（标点替换不受这里影响，始终逐字符）：\n"
            "· 整段粘贴 —— 塞剪贴板按一次 Ctrl+V，再把原剪贴板还原。动作只有一个，\n"
            "  自动补全 / 自动缩进 / 代码索引来不及插手（逐字符写入时它们会边敲边联想，\n"
            "  写进去的代码是歪的）。快捷输入里 `:=` 这种两三个字的仍逐字符，\n"
            "  为它绕一圈剪贴板太慢。\n"
            "· 逐字符写入 —— 跟真人一个个敲一样，不碰剪贴板，兼容老软件。",
            options=[INSERT_LABELS[m] for m in INSERT_MODES],
            current=INSERT_LABELS.get(
                settings.ai_insert_mode, INSERT_LABELS[INSERT_PASTE]),
            on_change=self._on_insert_mode)

        self._section(
            "FIM 补全（顺便看光标后面）",
            "把光标前后的代码一起交给模型，补中间缺的片段。")
        self._switch_row(
            "启用 FIM 补全",
            "只支持聊天接口的服务也能使用已抓取的下文。",
            settings.ai_fim_enabled, self._on_fim_enabled)
        self._choice_row(
            "请求方式",
            "服务端 suffix：把后半段作为 suffix 字段发过去，让服务端自己在 token 层套模板，快一倍。\n"
            "手拼 token：客户端自己拼 special token，兼容性好，但慢一点。\n"
            "不确定就留「自动」：先试服务端，不支持再自动换手拼。",
            options=[label for _value, label in FIM_STRATEGY_CHOICES],
            current=self._fim_choice_label(
                FIM_STRATEGY_CHOICES, settings.ai_fim_strategy),
            on_change=self._on_fim_strategy)
        self._choice_row(
            "token 格式",
            "不同模型族的 FIM 特殊 token 不一样。选错了不会报错，只会补出空行 ——\n"
            "qwen / codegemma 是一套，deepseek-coder 是另一套。留「自动」按模型名判断。",
            options=[label for _value, label in FIM_FAMILY_CHOICES],
            current=self._fim_choice_label(
                FIM_FAMILY_CHOICES, settings.ai_fim_tokens),
            on_change=self._on_fim_tokens)
        self._number_row(
            "抓下文行数",
            "光标后面抓几行当后缀（默认 3，最多 10）。调 0 = 只抓上文。\n"
            "光标停在文件最后一行时抓不到，会自动按空的处理，不会报错。",
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

    def _entry_row(self, title: str, desc: str, value: str,
                   on_commit: Callable[[str], None],
                   *, secret: bool = False) -> Tuple[tk.StringVar, tk.Entry]:
        """一行输入框。**失焦或回车才提交** —— 每敲一个字符就 save 一次太吵。"""
        _row, right = self._row(title, desc)
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
