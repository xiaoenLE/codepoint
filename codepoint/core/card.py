"""预览卡的数据结构 —— 纯数据，不含任何 tkinter。

放 core 而不是 ui，是因为 **services 也要构造它**（管道要知道"生成中/就绪/失败"
该给界面看什么），而 services 不允许 import ui。界面层反过来 import 这里。

真正负责画的是 ``ui/card.py`` 里的 ``CardWindow``。
"""

from __future__ import annotations

from dataclasses import dataclass

STATE_BUSY = "busy"
STATE_READY = "ready"
STATE_FAILED = "failed"


@dataclass(frozen=True)
class Card:
    """一块要显示的内容。

    ``state`` 决定颜色和提示语：``busy`` 脉动等待、``ready`` 等 Tab、
    ``failed`` 报错（这种状态**绝不会**插入任何东西）。
    """

    state: str = STATE_READY
    label: str = ""           # 左上角那行字："AI 生成" / "if"
    body: str = ""            # 正文：代码预览 / 提示词 / 错误原因
    hint: str = ""            # 右下角操作提示："Tab 接受 · Esc 取消"
    #: 多少秒后自动关（0 = 一直等）。倒计时会显示在标题栏右侧，
    #: 用户随时可以按 Esc 提前关掉 —— 自动关只是兜底，不是限制。
    timeout: float = 0.0

    @classmethod
    def busy(cls, label: str, body: str = "") -> "Card":
        return cls(state=STATE_BUSY, label=label, body=body, hint="稍等…")

    @classmethod
    def ready(cls, label: str, body: str,
              hint: str = "Tab 接受 · Esc 取消",
              *, timeout: float = 0.0) -> "Card":
        return cls(state=STATE_READY, label=label, body=body, hint=hint,
                   timeout=timeout)

    @classmethod
    def failed(cls, label: str, body: str) -> "Card":
        return cls(state=STATE_FAILED, label=label, body=body, hint="Esc 关闭")

    @property
    def actionable(self) -> bool:
        """是不是"按 Tab 就会插入"的状态。"""
        return self.state == STATE_READY


__all__ = ["Card", "STATE_BUSY", "STATE_READY", "STATE_FAILED"]
