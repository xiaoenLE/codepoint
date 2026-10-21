"""后台线程 ↔ 界面线程的唯一通道。

tkinter 不是线程安全的：只有主线程能碰控件。所以规则是
**后台线程只能 publish，界面线程只能 poll**，双方不直接调用对方。
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class UiEvent:
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


class UiBridge:
    """线程安全事件队列。"""

    # 事件类型（用常量而非枚举，方便跨模块引用）
    INDICATOR_SHOW = "indicator.show"
    INDICATOR_HIDE = "indicator.hide"
    #: 补全预览窗。payload: card（ui.card.Card 实例）
    CARD_SHOW = "card.show"
    CARD_HIDE = "card.hide"
    #: 流式生成过程中刷正文 —— 只更新内容，不动位置不重启动画
    CARD_STREAM = "card.stream"
    STATUS = "status"
    SETTINGS_CHANGED = "settings.changed"
    SHOW_WINDOW = "window.show"       # 请把窗口显示出来（托盘/第二实例）
    HIDE_WINDOW = "window.hide"       # 请把窗口收进托盘
    TOAST = "toast"
    QUIT = "quit"

    def __init__(self) -> None:
        self._queue: "queue.Queue[UiEvent]" = queue.Queue()

    def publish(self, kind: str, **payload: Any) -> None:
        """后台线程调用。绝不阻塞、绝不抛异常。"""
        try:
            self._queue.put_nowait(UiEvent(kind, payload))
        except Exception:      # noqa: BLE001
            pass

    def publish_event(self, event: UiEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except Exception:      # noqa: BLE001
            pass

    def poll(self, limit: int = 64) -> List[UiEvent]:
        """界面线程调用（建议挂在 after(30, ...) 上）。"""
        out: List[UiEvent] = []
        for _ in range(max(1, limit)):
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


class NullBridge(UiBridge):
    """什么都不做——无界面模式下用它，避免到处判 None。"""

    def publish(self, kind: str, **payload: Any) -> None:      # noqa: D102
        return None
