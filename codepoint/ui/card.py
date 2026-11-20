"""补全预览窗 —— 按热键/命中触发后浮出来的那块卡片。

为什么要有它
    以前 `//ai` 和 `Ctrl+Alt+G` 都是"按下去 → 等几秒 → 代码突然出现在编辑器里"。
    用户完全不知道发生了什么：等多久？出来的是什么？插到哪？想反悔怎么办？
    这块窗把那个黑盒打开：**先给你看要补什么，按 Tab 才真的插进去**。
    和 `///` 的补全一样的心智模型，只是内容多了、能看全了。

三种状态
    busy    正在生成（脉冲呼吸，正文显示提示词）
    ready   好了，等你按 Tab（正文是代码预览；Esc 取消）
    failed  出错了（正文是原因，不会插任何东西）

外观
    背景色 / 文字色 / 不透明度 / 字号 / 最大行数 / 最大宽度都能配（见 Settings.ai_card_*）。
    默认深色：这块窗会盖在任意编辑器上，深色在浅色背景上最清楚。

两个必须做到的点（沿用呼吸灯的经验）
    1. **不抢焦点**：WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW。抢走焦点的话，
       用户按 Tab 就落到这块窗上，编辑器根本收不到。
    2. **不挡住光标**：默认浮在光标右下方，右侧放不下就翻到左边，下方放不下就翻上去。
"""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from typing import Callable, List, Optional, Tuple

from ..core.card import STATE_BUSY, STATE_FAILED, STATE_READY, Card
from . import theme, widgets
Positioner = Callable[[], Tuple[int, int]]

_ACCENT = {
    STATE_BUSY: "#F0B429",
    STATE_READY: "#3FBF7F",
    STATE_FAILED: "#E5534B",
}

_BREATH_MS = 45
_PAD = 13
_HEADER_H = 20
_BODY_GAP = 7


@dataclass(frozen=True)
class CardLook:
    """外观参数。从 Settings 来，也可以在预览工具里直接构造。"""

    bg: str = "#1B1F27"
    fg: str = "#E8ECF4"
    alpha: float = 0.94
    font_size: int = 11
    max_lines: int = 14
    max_width: int = 460

    @classmethod
    def from_settings(cls, settings) -> "CardLook":
        return cls(
            bg=getattr(settings, "ai_card_bg", cls.bg),
            fg=getattr(settings, "ai_card_fg", cls.fg),
            alpha=getattr(settings, "ai_card_alpha", cls.alpha),
            font_size=getattr(settings, "ai_card_font_size", cls.font_size),
            max_lines=getattr(settings, "ai_card_max_lines", cls.max_lines),
            max_width=getattr(settings, "ai_card_width", cls.max_width),
        )


class CardWindow:
    """无边框、置顶、可配外观的补全预览窗。只在界面主线程里用。"""

    def __init__(self, master: tk.Misc, *,
                 positioner: Optional[Positioner] = None,
                 on_expire: Optional[Callable[[], None]] = None) -> None:
        self._master = master
        self._positioner = positioner
        #: 倒计时归零时回调 —— 由界面层转给管道，把挂起的结果作废。
        self._on_expire = on_expire
        self._win: Optional[tk.Toplevel] = None
        self._canvas: Optional[tk.Canvas] = None
        self._look = CardLook()
        self._card: Optional[Card] = None
        self._job: Optional[str] = None
        self._alpha = self._look.alpha
        self._no_activate_applied = False
        self._countdown = 0.0
        self._countdown_job: Optional[str] = None
        #: 生成中起始时刻（显示"已等 Ns"用）—— 推理模型要静默十几秒才出正文
        self._busy_at: Optional[float] = None
        self._hint_job: Optional[str] = None
        self._first_row = 0
        self._last_first_row = 0
        self._visible_rows = 0

    # ------------------------------------------------------------ 外观

    def set_look(self, look: CardLook) -> None:
        """换外观。正在显示的话按新外观重画一次。"""
        self._look = look
        if self._win is not None and self._win.winfo_exists() and self._card:
            self._draw(self._card)
            self._apply_alpha()

    # ------------------------------------------------------------ 显示

    def show_with(self, card: Card) -> None:
        x, y = 0, 0
        if self._positioner is not None:
            try:
                x, y = self._positioner()
            except Exception:      # noqa: BLE001
                x, y = 0, 0
        self.show(card, x, y)

    def show(self, card: Card, x: int, y: int) -> None:
        win = self._ensure()
        self._card = card
        self._first_row = 0
        # ⚠ 倒计时的值必须**在 _draw 之前**设好 —— _draw 会读它来决定
        #   标题栏右侧显示什么。放在后面的话第一帧没有秒数，要等 1 秒
        #   后第一次 tick 才补上（看着像"倒计时没生效"）。
        self._stop_countdown()
        if card.timeout > 0 and card.state == STATE_READY:
            self._countdown = float(card.timeout)
        # "生成中"记下起始时刻，提示语里显示"已等 Ns"（推理模型要静默十几秒）
        self._busy_at = (time.monotonic() if card.state == STATE_BUSY else None)
        self._draw(card)
        win.update_idletasks()

        w = max(int(self._canvas.winfo_reqwidth()), 160)
        h = max(int(self._canvas.winfo_reqheight()), 44)
        px, py = self._place(x, y, w, h)
        win.geometry(f"{w}x{h}+{px}+{py}")

        try:
            win.deiconify()
            win.lift()
        except tk.TclError:
            return
        self._apply_no_activate(win)
        self._apply_alpha()
        self._start_breathing(card.state == STATE_BUSY)
        # 只有"等你操作"的状态才需要倒计时；"生成中"和"失败"不用。
        # 注意这里只负责把计时器挂上，秒数已经在上面设好了。
        if self._countdown > 0:
            self._countdown_job = win.after(1000, self._tick_countdown)
        self._restart_hint_tick()

    def hide(self) -> None:
        self._stop_breathing()
        self._stop_countdown()
        self._stop_hint_tick()
        self._busy_at = None
        if self._win is not None and self._win.winfo_exists():
            try:
                self._win.withdraw()
            except tk.TclError:
                pass

    def update_stream(self, body: str, *, label: str = "") -> None:
        """流式生成过程中就地刷新正文。

        和 ``show()`` 的区别：**不动位置、不重启呼吸动画、不碰倒计时** ——
        每来一段就整套 show 一遍的话，窗口会跟着光标位置抖、呼吸脉动也永远
        起不来（每 80ms 被重置一次）。
        """
        win = self._win
        if win is None or not win.winfo_exists() or self._card is None:
            return
        if self._card.state != STATE_BUSY:
            return                      # 只有"生成中"才允许流式刷
        card = Card(state=STATE_BUSY, label=label or self._card.label,
                    body=body or "…", hint="生成中…")
        self._card = card
        if self._busy_at is None:
            self._busy_at = time.monotonic()
        self._draw(card)
        self._restart_hint_tick()
        win.update_idletasks()
        w = max(int(self._canvas.winfo_reqwidth()), 160)
        h = max(int(self._canvas.winfo_reqheight()), 44)
        x, y = 0, 0
        if self._positioner is not None:
            try:
                x, y = self._positioner()
            except Exception:      # noqa: BLE001
                x, y = 0, 0
        px, py = self._place(x, y, w, h)
        win.geometry(f"{w}x{h}+{px}+{py}")

    def destroy(self) -> None:
        self._stop_breathing()
        self._stop_hint_tick()
        self._busy_at = None
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
            self._canvas = None

    @property
    def visible(self) -> bool:
        return bool(self._win is not None and self._win.winfo_exists()
                    and self._win.state() == "normal")

    @property
    def card(self) -> Optional[Card]:
        return self._card

    # ------------------------------------------------------------ 内部：窗口

    def _ensure(self) -> tk.Toplevel:
        if self._win is not None and self._win.winfo_exists():
            return self._win
        win = tk.Toplevel(self._master)
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        win.configure(bg=self._look.bg)
        canvas = tk.Canvas(win, bg=self._look.bg, highlightthickness=0, bd=0)
        canvas.pack()
        canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        canvas.tag_bind("previous_page", "<Button-1>", lambda _: self._scroll(-self._visible_rows))
        canvas.tag_bind("next_page", "<Button-1>", lambda _: self._scroll(self._visible_rows))
        self._win = win
        self._canvas = canvas
        self._no_activate_applied = False
        return win

    def _place(self, x: int, y: int, w: int, h: int) -> Tuple[int, int]:
        """默认落在光标右下方；右边放不下翻到左边，下边放不下翻到上边。"""
        win = self._win
        assert win is not None
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        px = x + 12
        if px + w > sw - 8:
            px = x - w - 12
        py = y + 20
        if py + h > sh - 56:          # 56 ≈ 任务栏
            py = y - h - 12
        px = min(max(px, 6), max(sw - w - 6, 0))
        py = min(max(py, 6), max(sh - h - 6, 0))
        return px, py

    # ------------------------------------------------------------ 内部：绘制

    def _draw(self, card: Card) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.delete("all")

        look = self._look
        size = max(8, int(look.font_size))
        code_font = tkfont.Font(family="Consolas", size=size)
        ui_font = tkfont.Font(family=theme.family(), size=max(8, size - 1))
        title_font = tkfont.Font(family=theme.family(), size=max(8, size - 1),
                                 weight="bold")

        line_h = max(14, code_font.metrics("linespace"))
        inner_max = max(120, look.max_width - _PAD * 2)
        all_lines = self._body_lines(card.body, code_font, inner_max)
        screen_rows = max(1, (canvas.winfo_screenheight() - 130) // line_h)
        rows = max(1, min(look.max_lines, screen_rows))
        self._last_first_row = max(0, len(all_lines) - rows)
        self._first_row = min(self._first_row, self._last_first_row)
        if card.state == STATE_BUSY:
            self._first_row = self._last_first_row
        lines = all_lines[self._first_row:self._first_row + rows]
        self._visible_rows = len(lines)
        body_w = max((code_font.measure(line) for line in all_lines), default=0)
        body_h = line_h * len(lines) if lines else 0

        label_w = title_font.measure(card.label) if card.label else 0
        hint = self._hint_text()
        hint_w = ui_font.measure(hint) if hint else 0
        header_w = 14 + label_w + (18 + hint_w if hint_w else 0)

        width = max(body_w, header_w) + _PAD * 2
        width = max(180, min(width, look.max_width))
        height = _PAD * 2 + _HEADER_H + (_BODY_GAP + body_h if lines else 0)
        if self._last_first_row:
            height += _HEADER_H + _BODY_GAP

        canvas.configure(width=width, height=height)
        widgets.rounded_rect(canvas, 0, 0, width - 1, height - 1, 10,
                             fill=look.bg, outline=look.bg)

        # 头部：状态点 + 标题 + 右侧提示
        cy = _PAD + _HEADER_H / 2
        dot = 5
        canvas.create_oval(_PAD, cy - dot, _PAD + dot * 2, cy + dot,
                           fill=_ACCENT.get(card.state, _ACCENT[STATE_READY]),
                           outline="")
        x = _PAD + dot * 2 + 7
        if card.label:
            canvas.create_text(x, cy, text=card.label, anchor="w",
                               fill=look.fg, font=title_font)
        if card.hint:
            canvas.create_text(width - _PAD, cy, text=hint, anchor="e",
                               fill=self._muted(look.fg), font=ui_font)

        if not lines:
            return
        # 正文：逐行画。换行只影响显示，插入仍使用原始代码。
        y = _PAD + _HEADER_H + _BODY_GAP
        for line in lines:
            canvas.create_text(_PAD, y, text=line, anchor="nw",
                               fill=look.fg, font=code_font)
            y += line_h
        if self._last_first_row:
            self._draw_scroll_controls(canvas, ui_font, y + _BODY_GAP + _HEADER_H / 2,
                                       width, len(lines), len(all_lines))

    def _body_lines(self, body: str, font: tkfont.Font,
                    limit_px: int) -> List[str]:
        if not body:
            return []
        raw = body.replace("\r\n", "\n").replace("\t", "    ").split("\n")
        out: List[str] = []
        for line in raw:
            while line and font.measure(line) > limit_px:
                end = _fitting_length(line, font, limit_px)
                out.append(line[:end])
                line = line[end:]
            out.append(line)
        return out

    def _draw_scroll_controls(self, canvas, font, y: float, width: int,
                              visible: int, total: int) -> None:
        fg = self._muted(self._look.fg)
        canvas.create_text(_PAD, y, anchor="w", fill=fg, font=font,
                           text=f"{self._first_row + 1}–{self._first_row + visible} / {total}")
        if self._card is None or self._card.state == STATE_BUSY:
            return
        for label, offset, tag in (("上一页", 55, "previous_page"), ("下一页", 0, "next_page")):
            canvas.create_text(width - _PAD - offset, y, text=label, tags=(tag,),
                               anchor="e", fill=self._look.fg, font=font)

    def _on_mouse_wheel(self, event) -> None:
        if event.delta:
            self._scroll(-3 if event.delta > 0 else 3)

    def _scroll(self, rows: int) -> None:
        if self._card is None or self._card.state == STATE_BUSY:
            return
        first = max(0, min(self._first_row + rows, self._last_first_row))
        if first != self._first_row:
            self._first_row = first
            self._draw(self._card)

    @staticmethod
    def _muted(fg: str) -> str:
        """把前景色压暗一点当次要色，省得再配一个字段。"""
        try:
            r = int(fg[1:3], 16)
            g = int(fg[3:5], 16)
            b = int(fg[5:7], 16)
            mix = lambda c: int(c * 0.62)      # noqa: E731
            return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"
        except Exception:      # noqa: BLE001
            return "#9AA3B2"

    # ------------------------------------------------------------ 倒计时
    #
    # 为什么要有自动关闭：这块窗是"贴在光标旁边"的，用户可能直接走开、
    # 或者干脆不管它。一直挂着会挡住下面的代码。倒计时只是兜底 ——
    # 想立刻关就按 Esc，不受秒数限制。

    def _hint_text(self) -> str:
        card = self._card
        if card is None:
            return ""
        if card.state == STATE_BUSY and self._busy_at:
            # 生成中显示已等秒数 —— 推理模型（MiMo 等）要静默十几秒才出正文，
            # 光转"…"看着像卡死，有秒数至少知道还活着。
            elapsed = max(0, int(time.monotonic() - self._busy_at))
            return f"{card.hint} · 已等 {elapsed}s"
        if self._countdown > 0:
            return f"{card.hint} · {max(0, int(round(self._countdown)))}s 后自动关"
        return card.hint

    def _start_countdown(self, seconds: float) -> None:
        """重新开始倒计时（当前只在测试/外部复用，show() 里是内联设置的）。"""
        self._stop_countdown()
        self._countdown = float(seconds)
        if self._win is not None:
            self._countdown_job = self._win.after(1000, self._tick_countdown)

    def _stop_countdown(self) -> None:
        if self._countdown_job is not None and self._win is not None:
            try:
                self._win.after_cancel(self._countdown_job)
            except Exception:      # noqa: BLE001
                pass
        self._countdown_job = None
        self._countdown = 0.0

    def _restart_hint_tick(self) -> None:
        """生成中每秒重画一次头部（"已等 Ns"要走秒）。

        只重画不重排：正文位置不动，避免秒数跳动把代码行带得抖。
        """
        self._stop_hint_tick()
        if self._busy_at is None or self._win is None:
            return
        self._hint_job = self._win.after(1000, self._tick_hint)

    def _stop_hint_tick(self) -> None:
        if self._hint_job is not None and self._win is not None:
            try:
                self._win.after_cancel(self._hint_job)
            except Exception:      # noqa: BLE001
                pass
        self._hint_job = None

    def _tick_hint(self) -> None:
        self._hint_job = None
        if self._win is None or not self._win.winfo_exists() or self._card is None:
            return
        self._draw(self._card)          # 提示语变了就整卡重画（小窗口，代价可忽略）
        self._restart_hint_tick()

    def _tick_countdown(self) -> None:
        if self._card is None or self._win is None or not self._win.winfo_exists():
            self._countdown_job = None
            return
        self._countdown -= 1.0
        if self._countdown <= 0:
            self._countdown = 0.0
            self._countdown_job = None
            self.hide()
            if self._on_expire is not None:
                try:
                    self._on_expire()
                except Exception:      # noqa: BLE001
                    pass
            return
        self._draw(self._card)
        self._countdown_job = self._win.after(1000, self._tick_countdown)

    # ------------------------------------------------------------ 透明度

    def _apply_alpha(self) -> None:
        if self._win is None:
            return
        try:
            self._win.attributes("-alpha", round(max(0.2, min(self._alpha, 1.0)), 3))
        except tk.TclError:
            pass

    def _start_breathing(self, on: bool) -> None:
        self._stop_breathing()
        self._alpha = self._look.alpha
        self._apply_alpha()
        if on:
            self._tick()

    def _stop_breathing(self) -> None:
        if self._job is not None and self._win is not None:
            try:
                self._win.after_cancel(self._job)
            except Exception:      # noqa: BLE001
                pass
        self._job = None
        self._alpha = self._look.alpha

    def _tick(self) -> None:
        """只给"生成中"用：在设定透明度上下来回脉动，表示还活着。"""
        if self._win is None or not self._win.winfo_exists():
            self._job = None
            return
        base = self._look.alpha
        low = max(0.2, base * 0.72)
        self._alpha = low if self._alpha > (base + low) / 2 else base
        self._apply_alpha()
        self._job = self._win.after(_BREATH_MS * 12, self._tick)

    # ------------------------------------------------------------ 不抢焦点

    def _apply_no_activate(self, win: tk.Toplevel) -> None:
        if self._no_activate_applied:
            return
        try:
            from ..platform_win.winapi import (
                GA_ROOT, GWL_EXSTYLE, HWND, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                user32,
            )

            hwnd = int(user32.GetAncestor(HWND(win.winfo_id()), GA_ROOT) or 0)
            if not hwnd:
                hwnd = int(win.winfo_id())
            style = int(user32.GetWindowLongW(HWND(hwnd), GWL_EXSTYLE) or 0)
            user32.SetWindowLongW(HWND(hwnd), GWL_EXSTYLE,
                                  style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:      # noqa: BLE001 —— 拿不到也不致命，只是可能抢焦点
            pass
        self._no_activate_applied = True


def _fitting_length(line: str, font: tkfont.Font, limit_px: int) -> int:
    """找出能放在当前预览宽度里的字符数。"""
    lo, hi = 1, len(line)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(line[:mid]) <= limit_px:
            lo = mid
        else:
            hi = mid - 1
    return lo


__all__ = [
    "Card", "CardLook", "CardWindow",
    "STATE_BUSY", "STATE_READY", "STATE_FAILED",
]
