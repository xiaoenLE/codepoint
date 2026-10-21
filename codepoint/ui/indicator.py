"""补全提示的通知器 —— services 层要"显示/隐藏预览窗"时走这里。

为什么不直接调窗口：这个类的方法会被**钩子线程**调用（比如整词命中那一瞬间），
而 tkinter 只有主线程能碰。所以这里只往 ``UiBridge`` 里投事件，
真正的绘制由界面线程的轮询去做（见 ui/main_window.py）。

历史上这里还负责"呼吸灯"那个单行小条（IndicatorWindow）。v1.3.0 起换成
多行预览窗 ``ui.card.CardWindow`` —— 用户要的是"能看到要补什么再决定"，
一条 30px 高的单行提示根本装不下一段 ST。
"""

from __future__ import annotations

from typing import Optional

from .bridge import UiBridge
from .card import Card, CardLook, CardWindow

__all__ = ["IndicatorNotifier", "CardWindow", "Card", "CardLook"]


class IndicatorNotifier:
    """``services.ports.QuickActionNotifier`` 的实现。"""

    def __init__(self, bridge: UiBridge, *, enabled: bool = True) -> None:
        self._bridge = bridge
        self._enabled = enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self.hide()

    # ------------------------------------------------------------ 端口

    def show(self, action: object) -> None:
        """模板补全（整词命中）：把片段内容做成预览卡。

        和 AI 走同一块窗 —— 用户的心智模型应该是"一回事"：
        提示出现 → 看一眼 → Tab 接受。以前模板补全只弹一条单行小条，
        内容一多就看不全，只能盲按 Tab。

        ⚠ 这里才看「Tab 补全提示（呼吸灯）」开关：它管的是**快捷输入的提示**。
        AI 的预览窗归「显示预览窗」（``ai_card_enabled``）管。以前两者混在一起，
        关掉呼吸灯连 AI 预览一起没了，结果还被挂起等 Tab —— 用户看到的就是
        "关了就什么都不显示"（报障原话）。
        """
        if not self._enabled:
            return
        self.show_card(Card.ready(
            label=getattr(action, "trigger", "") or "补全",
            body=getattr(action, "content", "") or "",
            hint="Tab 补全 · Esc 取消",
        ))

    def hide(self) -> None:
        self._bridge.publish(UiBridge.CARD_HIDE)

    # ------------------------------------------------------------ 扩展

    def show_card(self, card: Card) -> None:
        """AI 预览窗 / 生成中的卡。**不看呼吸灯开关**（见 ``show`` 的说明）。"""
        self._bridge.publish(UiBridge.CARD_SHOW, card=card)

    def stream_card(self, body: str, *, label: str = "") -> None:
        """流式生成中刷正文（由后台线程高频调用，走 bridge 队列）。"""
        self._bridge.publish(UiBridge.CARD_STREAM, body=body, label=label)

    @property
    def enabled(self) -> bool:
        return self._enabled
