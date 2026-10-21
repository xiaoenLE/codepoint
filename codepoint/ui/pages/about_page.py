"""「关于」页：居中的大 logo + 名称 + 几句话说明 + 环境信息。

把"这软件干什么""怎么用它""有哪几个快捷键"集中放在一页里，
省得用户去翻文档。
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import List, Tuple

from ... import APP_DISPLAY_NAME, __version__
from .. import theme
from ...iconmaker import logo_tile_png_base64
from .base import Page

SHORTCUTS: List[Tuple[str, str]] = [
    ("Tab", "整词快捷输入命中后，按下即补全"),
    ("Ctrl + Alt + A", "全局暂停 / 恢复（可在常规设置里改）"),
    ("Ctrl + Q", "退出程序"),
]


class AboutPage(Page):
    """关于。"""

    def __init__(self, master, facade, **kw) -> None:
        super().__init__(master, **kw)
        self.facade = facade
        self._logo: tk.PhotoImage | None = None
        self._build()

    def _build(self) -> None:
        head = tk.Frame(self.content, bg=theme.BG)
        head.pack(pady=(34, 6))
        holder = tk.Canvas(head, width=82, height=82, bg=theme.BG,
                           highlightthickness=0, bd=0)
        holder.pack()
        self._logo = tk.PhotoImage(data=logo_tile_png_base64(82))
        holder.create_image(41, 41, image=self._logo)

        tk.Label(self.content, text=APP_DISPLAY_NAME, bg=theme.BG, fg=theme.TEXT,
                 font=(theme.family(), 20, "bold")).pack(pady=(12, 2))
        tk.Label(self.content, text="中文标点自动转英文 · 代码片段快捷补全",
                 bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.F_BODY).pack()
        tk.Label(self.content, text=f"V{__version__}", bg=theme.BG,
                 fg=theme.TEXT_FAINT, font=theme.F_SMALL).pack(pady=(6, 0))
        tk.Label(self.content, text="由 xiaoen 制作", bg=theme.BG,
                 fg=theme.TEXT_MUTED, font=theme.F_SMALL).pack(pady=(2, 0))

        self._card("它是干什么的", [
            "在你指定的程序里，把你打出来的中文标点自动换成英文标点；",
            "再用「检测字符串 → 补全内容」把常用代码骨架一键补出来。",
            "本程序由 xiaoen 制作。",
        ])
        self._card("怎么开始用", [
            "① 打开你要写代码的软件（会显示在「程序列表」页的状态栏里）；",
            "② 回主页点「加入列表」，或者点蓝色 + 从正在运行的程序里挑；",
            "③ 回到那个软件正常打字，标点就自动变半角了；",
            "④ 想暂停就按 Ctrl+Alt+A，或点右上角那个开关。",
        ])
        self._card("快捷键", [f"{key}   ——   {desc}" for key, desc in SHORTCUTS])
        self._card("运行自检（功能不对时看这几行）", self._diag_lines())
        self._card(f"本版更新（V{__version__}）", [
            "修复：偶发「能识别到程序、就是不转换」，要重启才好 —— 现在会自己救回来。",
            "① Ctrl/Alt/Win 的状态会被 UAC 提示框 / 锁屏界面弄「卡住」（那上面我们的",
            "    钩子收不到按键），卡住之后所有按键都不再转换 —— 现在每次按键都跟物理",
            "    状态对一次账，卡住立刻复位；",
            "② 键盘钩子被系统悄悄摘掉时（Windows 对超时的钩子不做任何通知），会自动",
            "    探活并重新挂上，不用再重启程序。",
            "另外这里多了一张「运行自检」卡片，专门用来看「没转换的原因」。",
        ])

        env = (f"Python {sys.version.split()[0]}   ·   "
               f"{sys.platform}   ·   {sys.executable}")
        tk.Label(self.content, text=env, bg=theme.BG, fg=theme.TEXT_FAINT,
                 font=theme.F_SMALL, wraplength=theme.CONTENT_MAX_WIDTH - 80,
                 justify="center").pack(pady=(18, 26))

    # ------------------------------------------------------------ 卡片

    def _diag_lines(self) -> List[str]:
        """把组装根给的运行统计摊成几行。

        为什么要露在这里：这个程序失效的时候**表面上什么都正常**（前台也识别到了、
        开关也开着），唯一能说明问题的就是"最近为什么没转换"这几个计数。
        """
        try:
            text = self.facade.stats_text()
        except Exception:      # noqa: BLE001
            return ["（取不到运行统计）"]
        lines = [line for line in (text or "").splitlines() if line.strip()]
        return lines or ["（没有可显示的内容）"]

    def _card(self, title: str, lines: List[str]) -> None:
        wrap = tk.Frame(self.content, bg=theme.BORDER)
        wrap.pack(fill="x", padx=self.pad + 20, pady=(14, 0))
        inner = tk.Frame(wrap, bg=theme.SUNKEN)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text=title, bg=theme.SUNKEN, fg=theme.TEXT,
                 font=(theme.family(), 10, "bold")).pack(anchor="w", padx=14,
                                                         pady=(10, 4))
        for line in lines:
            tk.Label(inner, text=line, bg=theme.SUNKEN, fg=theme.TEXT_BODY,
                     font=theme.F_SMALL, justify="left",
                     wraplength=theme.CONTENT_MAX_WIDTH - 80,
                     anchor="w").pack(anchor="w", padx=14)
        tk.Frame(inner, bg=theme.SUNKEN, height=10).pack()


__all__ = ["AboutPage"]
