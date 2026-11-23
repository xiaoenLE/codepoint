"""生效范围判定：这个前台程序到底要不要管。

两种模式（对应界面上的「生效范围」）：
  - ``MATCH_WHITELIST``：**只有**列表里的程序生效（默认，最安全）
  - ``MATCH_ALL``      ：**所有**程序都生效，列表变成"排除名单"

命中规则（与 AppItem 字段一一对应）：
  - process_name 非空 → 必须完全相等（忽略大小写）
  - window_title 非空 → 标题需包含该子串（忽略大小写）
  - 两者都为空 → 该条目视为无效，永不匹配（防止"一个空条目通吃所有程序"）
  - enabled=False → 跳过

另有两条硬性护栏：
  1. **永远不匹配本进程自己**（否则自己的界面里打字会自激）
  2. ``MATCH_ALL`` 下若窗口无效，也**不生效**（拿不到进程名就别乱改）
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from ..core.events import WindowInfo
from ..core.models import MATCH_ALL, MATCH_WHITELIST, AppItem

# 判定结果（界面要显示原因，所以不能只返回 bool）
REASON_IN_SCOPE = "in_scope"
REASON_OUT_OF_SCOPE = "out_of_scope"
REASON_SELF = "self"
REASON_NO_WINDOW = "no_window"

_REASON_TEXT = {
    REASON_IN_SCOPE: "已生效",
    REASON_OUT_OF_SCOPE: "不在生效范围内",
    REASON_SELF: "是本程序自己的窗口，跳过",
    REASON_NO_WINDOW: "没有前台窗口",
}


class AppFilter:
    """可热更新的生效范围判定器。"""

    def __init__(
        self,
        items: Optional[Sequence[AppItem]] = None,
        *,
        mode: str = MATCH_WHITELIST,
        self_pid: int = -1,
    ) -> None:
        self._items: List[AppItem] = list(items or ())
        self._mode = mode if mode in (MATCH_WHITELIST, MATCH_ALL) else MATCH_WHITELIST
        self._self_pid = self_pid

    # ------------------------------------------------------------ 配置

    def update(self, items: Iterable[AppItem], mode: Optional[str] = None) -> None:
        self._items = list(items)
        if mode is not None:
            self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in (MATCH_WHITELIST, MATCH_ALL) else MATCH_WHITELIST

    def set_self_pid(self, pid: int) -> None:
        self._self_pid = pid

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def items(self) -> List[AppItem]:
        return list(self._items)

    @property
    def needs_window_title(self) -> bool:
        return any(item.enabled and item.window_title for item in self._items)

    # ------------------------------------------------------------ 匹配

    def first_match(self, window: WindowInfo) -> Optional[AppItem]:
        """返回命中的那条列表项（没命中返回 None）。"""
        if not window.valid:
            return None
        if self._self_pid >= 0 and window.pid == self._self_pid:
            return None      # 绝不处理自己，否则会自激
        for item in self._items:
            if item.matches(window.process_name, window.window_title):
                return item
        return None

    def decide(self, window: WindowInfo) -> Tuple[bool, str]:
        """返回 ``(是否生效, 原因码)``。界面直接拿原因码显示。"""
        if not window.valid:
            return False, REASON_NO_WINDOW
        if self._self_pid >= 0 and window.pid == self._self_pid:
            return False, REASON_SELF
        hit = self.first_match(window)
        if self._mode == MATCH_ALL:
            # 全部生效，列表是排除名单 → 命中列表反而不生效
            return (False, REASON_OUT_OF_SCOPE) if hit else (True, REASON_IN_SCOPE)
        return (True, REASON_IN_SCOPE) if hit else (False, REASON_OUT_OF_SCOPE)

    def matches(self, window: WindowInfo) -> bool:
        return self.decide(window)[0]

    def describe_reason(self, window: WindowInfo) -> str:
        """给日志/界面用的一句话说明。"""
        ok, reason = self.decide(window)
        name = window.process_name or "未知进程"
        if reason == REASON_IN_SCOPE:
            hit = self.first_match(window)
            if self._mode == MATCH_ALL:
                return f"生效中（全部程序模式）· {name}"
            return f"生效中（{hit.title() if hit else name}）"
        if reason == REASON_OUT_OF_SCOPE:
            if self._mode == MATCH_ALL:
                return f"当前程序在排除名单里 · {name}"
            return f"当前程序未加入列表 · {name}"
        return _REASON_TEXT.get(reason, reason)

    @staticmethod
    def reason_text(reason: str) -> str:
        return _REASON_TEXT.get(reason, reason)


__all__ = [
    "AppFilter",
    "REASON_IN_SCOPE",
    "REASON_OUT_OF_SCOPE",
    "REASON_SELF",
    "REASON_NO_WINDOW",
]
