"""ttk 样式表 —— 全工程唯一配置 ttk 的地方。

视觉基调：纯白底、无竖线的表格、浅灰填充的输入框、蓝色实心主按钮、
红色实心删除按钮。ttk 默认主题（vista）自带渐变和立体边框，必须先
``theme_use("clam")`` 拍平，再逐项覆盖，才能得到"扁平干净"的观感。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


def install_styles(root: tk.Misc) -> ttk.Style:
    """安装全部自定义样式。必须在创建控件之前调用。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")      # clam 允许完全自定义，vista 有很多画死在系统主题里的部分
    except tk.TclError:
        pass

    fam = theme.family()

    # ---------------------------------------------------------- 基础容器
    style.configure(".", background=theme.BG, foreground=theme.TEXT,
                    font=(fam, 10), borderwidth=0, focuscolor=theme.BG)
    style.configure("TFrame", background=theme.BG)
    style.configure("Sunken.TFrame", background=theme.SUNKEN)
    style.configure("Divider.TFrame", background=theme.BORDER)

    style.configure("TLabel", background=theme.BG, foreground=theme.TEXT,
                    font=(fam, 10))
    style.configure("Muted.TLabel", foreground=theme.TEXT_MUTED, font=(fam, 9))
    style.configure("Body.TLabel", foreground=theme.TEXT_BODY, font=(fam, 10))
    style.configure("H1.TLabel", font=theme.F_H1, foreground=theme.TEXT)
    style.configure("Sunken.TLabel", background=theme.SUNKEN, foreground=theme.TEXT)

    # ---------------------------------------------------------- 按钮
    style.configure("Primary.TButton", background=theme.PRIMARY,
                    foreground=theme.TEXT_ON_PRIMARY, font=(fam, 10),
                    borderwidth=0, focusthickness=0, padding=(12, 6))
    style.map("Primary.TButton",
              background=[("pressed", theme.PRIMARY_ACTIVE),
                          ("active", theme.PRIMARY_HOVER),
                          ("disabled", "#B9CFEA")],
              foreground=[("disabled", "#F0F5FB")])

    style.configure("Warning.TButton", background=theme.WARNING,
                    foreground=theme.TEXT_ON_PRIMARY, font=(fam, 10),
                    borderwidth=0, padding=(14, 6))
    style.map("Warning.TButton",
              background=[("pressed", theme.WARNING_HOVER),
                          ("active", theme.WARNING_HOVER)])

    style.configure("Ghost.TButton", background=theme.SUNKEN, foreground=theme.TEXT_BODY,
                    font=(fam, 10), borderwidth=0, padding=(10, 6))
    style.map("Ghost.TButton",
              background=[("active", theme.SUNKEN_HOVER), ("pressed", theme.BORDER)])

    style.configure("Tool.TButton", background=theme.PRIMARY, foreground=theme.TEXT_ON_PRIMARY,
                    font=(fam, 10), borderwidth=0, padding=(10, 5))
    style.map("Tool.TButton",
              background=[("active", theme.PRIMARY_HOVER), ("pressed", theme.PRIMARY_ACTIVE)])

    # ⚠ 必须显式写 width=0：ttk 的 TButton 自带一个默认宽度 —— 实测不管文字多长
    #   （连「年」这种单字）都固定占 102px，小按钮会被撑成一排大方块、一行放不下几个。
    #   width=0 才是"按文字自然宽度"。这个坑在「插入变量」那一排按钮上踩到过。
    style.configure("Bare.TButton", background=theme.BG, foreground=theme.TEXT_BODY,
                    font=(fam, 10), borderwidth=0, padding=(6, 4), width=0)
    style.map("Bare.TButton", background=[("active", theme.HOVER)])

    # ---------------------------------------------------------- 输入
    style.configure("TEntry", fieldbackground=theme.SUNKEN, background=theme.SUNKEN,
                    foreground=theme.TEXT, borderwidth=0, insertcolor=theme.TEXT,
                    padding=(8, 6))
    style.map("TEntry", fieldbackground=[("focus", "#FFFFFF")],
              bordercolor=[("focus", theme.PRIMARY)])

    style.configure("TCombobox", fieldbackground=theme.SUNKEN, background=theme.SUNKEN,
                    foreground=theme.TEXT, borderwidth=0, arrowsize=13,
                    padding=(8, 5))
    style.map("TCombobox",
              fieldbackground=[("readonly", theme.SUNKEN)],
              selectbackground=[("readonly", theme.SUNKEN)],
              selectforeground=[("readonly", theme.TEXT)])
    root.option_add("*TCombobox*Listbox.background", theme.BG)
    root.option_add("*TCombobox*Listbox.foreground", theme.TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", theme.PRIMARY_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", theme.TEXT)
    root.option_add("*TCombobox*Listbox.font", (fam, 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure("TCheckbutton", background=theme.BG, foreground=theme.TEXT_BODY,
                    font=(fam, 10), focuscolor=theme.BG)
    style.map("TCheckbutton", background=[("active", theme.BG)])

    # ---------------------------------------------------------- 表格
    # 表格：无竖线、表头灰字、行高 40、分隔线极浅
    style.configure("Data.Treeview", background=theme.BG, fieldbackground=theme.BG,
                    foreground=theme.TEXT_BODY, rowheight=theme.ROW_HEIGHT,
                    borderwidth=0, font=(fam, 10))
    style.configure("Data.Treeview.Heading", background=theme.BG,
                    foreground=theme.TEXT_MUTED, font=(fam, 10),
                    borderwidth=0, relief="flat", padding=(8, 8))
    style.map("Data.Treeview.Heading",
              background=[("active", theme.BG)], relief=[("active", "flat")])
    style.map("Data.Treeview",
              background=[("selected", theme.PRIMARY_SOFT)],
              foreground=[("selected", theme.TEXT)])
    style.layout("Data.Treeview", [
        ("Treeview.treearea", {"sticky": "nswe"}),
    ])
    style.configure("Data.Treeview.Item", padding=(0, 0))

    # 滚动条：默认的又宽又丑，收细
    style.configure("Thin.Vertical.TScrollbar", background=theme.BORDER_STRONG,
                    troughcolor=theme.BG, borderwidth=0, arrowsize=10, width=10)
    style.map("Thin.Vertical.TScrollbar",
              background=[("active", theme.TEXT_FAINT)])

    # ---------------------------------------------------------- 分隔线
    style.configure("TSeparator", background=theme.BORDER)
    return style


def apply_treeview_row_stripes(tree: ttk.Treeview) -> None:
    """登记行标签（隔行/禁用行用不同字色）。"""
    tree.tag_configure("off", foreground=theme.TEXT_FAINT)


__all__ = ["install_styles", "apply_treeview_row_stripes"]
