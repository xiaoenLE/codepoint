"""「程序列表」页。

  · 顶部一个蓝色大「+」按钮：点开就能从"正在运行的程序"里挑，不用手输进程名
  · 下面是程序卡片：图标 + 程序名（粗体）+ 完整路径（灰字）+ 右侧启用开关
  · 列表为空时显示三行提示文案

**关键的一条状态栏**：实时显示"你现在切过去的那个程序生效了没有"。
这类工具最难受的体验就是"加了程序但不知道有没有生效，只能打字试试看" ——
状态栏把结论直接写在脸上，后面还跟一个「加入列表」按钮一键加进来。
"""

from __future__ import annotations

import tkinter as tk
from typing import Dict, List, Optional

from ...core.models import AppItem
from .. import icons, theme, widgets
from .base import Page

POLL_MS = 1000          # 前台程序轮询间隔（底层带缓存，很便宜）


class HomePage(Page):
    """主页：程序列表 + 生效状态。"""

    def __init__(self, master, facade, **kw) -> None:
        super().__init__(master, **kw)
        self.facade = facade
        self._icons = icons.IconCache(32)
        self._cards: Dict[str, widgets.ProgramCard] = {}
        self._status_job: Optional[str] = None
        self._width = 640
        self._hint_text = ""

        # ---- 大「+」按钮 ----
        self.add_holder = tk.Frame(self.content, bg=theme.BG)
        self.add_holder.pack(pady=(24, 6))
        _BigPlusButton(self.add_holder, command=self.add_program).pack()

        # ---- 状态栏 ----
        self.status = _ScopeBar(self.content, on_add=self.add_current_program)
        self.status.pack(fill="x", padx=self.pad, pady=(8, 14))

        # ---- 运行提示 ----
        # 平时是空的（而且**不占位置** —— 没内容就 pack_forget），只在
        # 标点替换静默失效这类事上才冒出来说一句"为什么不转换"。
        self.hint = tk.Label(self.content, text="", bg=theme.BG, fg=theme.WARNING,
                             font=theme.F_SMALL, justify="left", anchor="w",
                             wraplength=theme.CONTENT_MAX_WIDTH - 40)

        # ---- 卡片列表 ----
        self.list_holder = tk.Frame(self.content, bg=theme.BG)
        self.list_holder.pack(fill="both", expand=True, padx=self.pad, pady=(0, 20))
        self.empty = widgets.EmptyState(
            self.list_holder,
            lines=("空空如也", '快点击"+"按钮', "添加需要的程序进来吧！"))
        self.list_holder.bind("<Configure>", self._on_resize)

    # ------------------------------------------------------------ 生命周期

    def refresh(self) -> None:
        super().refresh()
        self._rebuild()
        if self._status_job is None:
            self._tick()

    def _tick(self) -> None:
        """每秒刷新一次"当前程序"状态栏。"""
        try:
            window = self.facade.foreground_app()
            self.status.update_status(
                self.facade.describe_scope(window),
                self.facade.is_in_scope(window),
                window.process_name if window else "",
            )
            self._set_hint(self.facade.runtime_hint())
        except Exception:      # noqa: BLE001
            pass
        try:
            self._status_job = self.after(POLL_MS, self._tick)
        except Exception:      # noqa: BLE001
            self._status_job = None

    def _set_hint(self, text: str) -> None:
        """有话说就插在状态栏下面，没话说就彻底收起来（不占高度）。"""
        text = text or ""
        if text == getattr(self, "_hint_text", None):
            return
        self._hint_text = text
        if text:
            self.hint.configure(text=text)
            if not self.hint.winfo_manager():
                self.hint.pack(fill="x", padx=self.pad, pady=(0, 10),
                               before=self.list_holder)
        elif self.hint.winfo_manager():
            self.hint.pack_forget()

    def stop(self) -> None:
        """离开主页/关闭窗口时停掉轮询。"""
        if self._status_job is not None:
            try:
                self.after_cancel(self._status_job)
            except Exception:      # noqa: BLE001
                pass
            self._status_job = None

    def _on_resize(self, event) -> None:
        if abs(event.width - self._width) > 4:
            self._width = event.width

    # ------------------------------------------------------------ 列表

    def _rebuild(self) -> None:
        items = list(self.facade.get_settings().app_items)
        for card in self._cards.values():
            card.destroy()
        self._cards = {}

        if not items:
            self.empty.pack(pady=40)
            return
        self.empty.pack_forget()

        for item in items:
            key = self._key(item)
            card = widgets.ProgramCard(
                self.list_holder,
                name=item.title(),
                path=item.path or item.process_name,
                icon=self._icons.get(item.path),
                enabled=item.enabled,
                on_click=lambda k=key: self._remove(k),
                on_toggle=lambda v, k=key: self._toggle(k, v),
                width=self._width)
            card.pack(fill="x", pady=(0, 8))
            self._cards[key] = card

    @staticmethod
    def _key(item: AppItem) -> str:
        return f"{(item.process_name or '').lower()}|{(item.window_title or '').lower()}"

    def _find(self, key: str) -> Optional[AppItem]:
        for item in self.facade.get_settings().app_items:
            if self._key(item) == key:
                return item
        return None

    def _toggle(self, key: str, enabled: bool) -> None:
        item = self._find(key)
        if item is None:
            return
        item.enabled = enabled
        self._commit()

    def _remove(self, key: str) -> None:
        """点卡片主体 = 问一下要不要移除。"""
        from ..dialogs import ask_confirm

        item = self._find(key)
        if item is None:
            return
        if ask_confirm(self.winfo_toplevel(), title="移除程序",
                       message=f'把「{item.title()}」从列表里移除？\n'
                               f"之后它就不会被自动转换标点了。",
                       confirm_text="移除", danger=True):
            settings = self.facade.get_settings()
            settings.app_items = [i for i in settings.app_items if self._key(i) != key]
            self._commit()

    # ------------------------------------------------------------ 添加

    def add_program(self) -> None:
        """弹「选择程序」——列出所有正在运行的程序，挑一个即可。"""
        from ..program_picker import ProgramPicker

        settings = self.facade.get_settings()
        try:
            apps = self.facade.list_running_apps()
        except Exception:      # noqa: BLE001
            apps = []
        existing = [i.process_name for i in settings.app_items]
        picked = ProgramPicker(
            self.winfo_toplevel(), apps=apps,
            self_pid=self.facade.self_pid(), existing=existing).show()
        if picked is not None:
            self._append(picked)

    def add_current_program(self) -> None:
        """把当前前台程序一键加进列表。"""
        window = self.facade.foreground_app()
        name = getattr(window, "process_name", "") if window is not None else ""
        if not name:
            self.facade.notify("没有抓到程序", "先切到目标软件窗口，再回来点「加入列表」。")
            return
        self._append(AppItem(process_name=name, display_name=_pretty(name),
                             path=getattr(window, "process_path", ""), enabled=True))

    def _append(self, item: AppItem) -> None:
        settings = self.facade.get_settings()
        key = self._key(item)
        for old in settings.app_items:
            if self._key(old) == key:
                old.path = old.path or item.path
                old.display_name = old.display_name or item.display_name
                old.enabled = True
                self._commit()
                return
        settings.app_items.append(item)
        self._commit()

    def _commit(self) -> None:
        self.facade.apply_live()
        self.facade.save()
        self._rebuild()
        self.status.flash()


# ------------------------------------------------------------------ 性能监控面板


def _pretty(process_name: str) -> str:
    """codesys.exe → Codesys（列表里显示得好看一点）。"""
    name = process_name[:-4] if process_name.lower().endswith(".exe") else process_name
    return (name[:1].upper() + name[1:]) if name else process_name


# ------------------------------------------------------------------ 大「+」按钮


class _BigPlusButton(tk.Canvas):
    """顶部那个蓝色大加号按钮。"""

    W, H = 135, 36

    def __init__(self, master, *, command) -> None:
        super().__init__(master, width=self.W, height=self.H, bg=theme.BG,
                         bd=0, highlightthickness=0, cursor="hand2")
        self._command = command
        self._state = "normal"
        self.bind("<Enter>", lambda _e: self._set_state("hover"))
        self.bind("<Leave>", lambda _e: self._set_state("normal"))
        self.bind("<Button-1>", lambda _e: self._set_state("active"))
        self.bind("<ButtonRelease-1>", lambda _e: self._release())
        self._render()

    def _set_state(self, state: str) -> None:
        self._state = state
        self._render()

    def _release(self) -> None:
        self._set_state("hover")
        self._command()

    def _render(self) -> None:
        self.delete("all")
        color = {
            "normal": theme.PRIMARY,
            "hover": theme.PRIMARY_HOVER,
            "active": theme.PRIMARY_ACTIVE,
        }[self._state]
        widgets.rounded_rect(self, 1, 1, self.W - 1, self.H - 1, 6,
                             fill=color, outline=color)
        icons.draw(self, "plus", 20, "#FFFFFF",
                   (self.W - 20) / 2, (self.H - 20) / 2)


# ------------------------------------------------------------------ 状态栏


class _ScopeBar(tk.Canvas):
    """一条显示"当前程序生不生效"的状态栏。"""

    H = 46

    def __init__(self, master, *, on_add) -> None:
        super().__init__(master, height=self.H, bg=theme.BG, bd=0,
                         highlightthickness=0)
        self._on_add = on_add
        self._text = "正在识别当前程序…"
        self._ok = False
        self._process = ""
        self._hover_btn = False
        self._bar_w = 640
        self._flash = 0
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._render()

    # -------------------------------------------------- 外部接口

    def update_status(self, text: str, ok: bool, process: str) -> None:
        if (text, ok, process) == (self._text, self._ok, self._process):
            return
        self._text, self._ok, self._process = text, ok, process
        self._render()

    def flash(self) -> None:
        """列表刚变动时闪一下，给出"已保存"的反馈。"""
        self._flash = 6
        self._render()
        self._decay()

    def _decay(self) -> None:
        if self._flash > 0:
            self._flash -= 1
            self._render()
            self.after(80, self._decay)

    # -------------------------------------------------- 交互

    def _on_configure(self, event) -> None:
        self._bar_w = event.width
        self._render()

    def _btn_rect(self):
        w = 92
        return self._bar_w - w - 12, (self.H - 26) / 2, self._bar_w - 12, (self.H + 26) / 2

    def _motion(self, event) -> None:
        if not self._process or self._ok:
            return
        x1, y1, x2, y2 = self._btn_rect()
        inside = x1 <= event.x <= x2 and y1 <= event.y <= y2
        if inside != self._hover_btn:
            self._hover_btn = inside
            self.configure(cursor="hand2" if inside else "arrow")
            self._render()

    def _set_hover(self, value: bool) -> None:
        if value != self._hover_btn:
            self._hover_btn = value
            self._render()

    def _click(self, event) -> None:
        if self._ok or not self._process:
            return
        x1, y1, x2, y2 = self._btn_rect()
        if x1 <= event.x <= x2 and y1 <= event.y <= y2:
            self._on_add()

    # -------------------------------------------------- 绘制

    def _render(self) -> None:
        self.delete("all")
        w = max(self._bar_w, 260)
        h = self.H
        if self._flash:
            bg, border = theme.SUCCESS_SOFT, theme.SUCCESS
        elif self._ok:
            bg, border = theme.SUCCESS_SOFT, theme.SUCCESS_SOFT
        else:
            bg, border = theme.SUNKEN, theme.SUNKEN
        widgets.rounded_rect(self, 1, 1, w - 1, h - 1, theme.CARD_RADIUS,
                             fill=bg, outline=border, width=1)

        dot = theme.SUCCESS if self._ok else theme.TEXT_FAINT
        cy = h / 2
        self.create_oval(14, cy - 4, 22, cy + 4, fill=dot, outline=dot)
        self.create_text(32, cy, text=self._text, anchor="w",
                         fill=theme.TEXT_BODY if self._ok else theme.TEXT_MUTED,
                         font=theme.F_SMALL)

        if not self._ok and self._process:
            x1, y1, x2, y2 = self._btn_rect()
            color = theme.PRIMARY_HOVER if self._hover_btn else theme.PRIMARY
            widgets.rounded_rect(self, x1, y1, x2, y2, 5,
                                 fill=color, outline=color)
            self.create_text((x1 + x2) / 2, cy, text="加入列表",
                             fill="#FFFFFF", font=theme.F_BTN)


__all__ = ["HomePage"]
