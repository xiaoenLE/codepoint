"""快捷输入引擎（代码片段 Tab 补全）。

纯状态机，零平台依赖，可完整单元测试。

匹配语义（对应界面上的"整词检测 / 单字符"）：
  - 单字符模式：最后敲入的那个字符 == 检测字符串 → **立即**补全
  - 整词模式  ：缓冲区末尾 == 检测字符串，且
        · 若检测串全是"词字符"（字母/数字/下划线），要求它前面是词边界
          （否则 "sif" 会错误命中 "if"）
        · 若检测串含标点（如 "///"），直接按末尾匹配即可
    命中后进入 pending 状态 → 界面弹 Tab 呼吸灯 → 用户按 Tab 才真正补全

补全动作 = 先发 N 次退格吃掉已输入的检测串，再写入补全内容。

补全内容里的 ``${变量}``（日期/时间/用户名…）在**补全这一刻**才展开，
详见 ``services/template.py`` —— 所以片段里的时间每次插入都是当下那一刻。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from ..core.events import VK_ESCAPE, VK_RETURN, VK_TAB, KeyPress
from ..core.models import DETECT_CHAR, QuickAction
from . import template as _template

# FeedResult.kind 的取值
NONE = "none"
SHOW = "show"          # 进入 pending，请显示预览卡
HIDE = "hide"          # 取消 pending，请隐藏预览卡
COMPLETE = "complete"  # 立刻补全

_BUFFER_LIMIT = 64
_PENDING_TIMEOUT = 6.0


@dataclass(frozen=True)
class FeedResult:
    kind: str = NONE
    action: Optional[QuickAction] = None
    backspaces: int = 0
    text: str = ""


_RESULT_NONE = FeedResult()


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _is_word_trigger(trigger: str) -> bool:
    return bool(trigger) and all(_is_word_char(c) for c in trigger)


class QuickActionEngine:
    """按键流 → 快捷输入状态机。"""

    def __init__(
        self,
        actions: Optional[Sequence[QuickAction]] = None,
        *,
        enabled: bool = True,
        pending_timeout: float = _PENDING_TIMEOUT,
        buffer_limit: int = _BUFFER_LIMIT,
        render: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._actions: List[QuickAction] = []
        self._enabled = enabled
        self._timeout = pending_timeout
        self._limit = buffer_limit
        # 补全内容的"渲染器"：把 content 里的 ${变量} 换成当时的值。
        # 可注入是为了测试能冻结时间/用户名（见 tests/test_template.py）。
        self._render = render or _template.expand
        self._buffer = ""
        self._hwnd = 0
        self._pending: Optional[QuickAction] = None
        self._pending_at = 0.0
        #: 每来一个有效按键 +1。AI 生成是异步的，靠它判断"我还在等结果的时候，
        #: 用户是不是已经接着敲了别的字" —— 变了就说明光标位置不可信，结果必须丢弃。
        self._seq = 0
        self.configure(actions or (), enabled=enabled)

    # ------------------------------------------------------------ 渲染

    def _render_content(self, action: QuickAction) -> str:
        """产出真正要注入的文本。

        ⚠ 展开必须发生在**这一刻**（用户按 Tab / 命中单字符的瞬间），
        而不是保存片段的时候 —— 否则注释头里的时间永远是"写片段的那天"。
        """
        try:
            return self._render(action.content)
        except Exception:      # noqa: BLE001
            # 渲染失败绝不能连累补全本身：宁可原样输出，也别让 Tab 没反应。
            return action.content

    # ------------------------------------------------------------ 配置

    def configure(self, actions: Sequence[QuickAction], *, enabled: Optional[bool] = None) -> None:
        """装载规则。

        两件事：
          1. **按触发串去重**（词触发串折叠大小写后比较）——否则同时存在
             `for` 和 `FOR` 时其中一条永远没机会命中，界面上却多一行"看着有用"的规则；
          2. 按触发串长度**降序**排列，保证"最长匹配优先"（`ifelse` 不会被 `if` 抢走）。
        """
        seen = set()
        picked: List[QuickAction] = []
        for action in actions:
            if not (action.enabled and action.trigger):
                continue
            trigger = action.trigger
            key = (action.detect_mode,
                   trigger.lower() if _is_word_trigger(trigger) else trigger)
            if key in seen:
                continue
            seen.add(key)
            picked.append(action)
        picked.sort(key=lambda a: (-len(a.trigger), a.trigger.lower()))
        self._actions = picked
        if enabled is not None:
            self._enabled = enabled
        self._pending = None

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._pending = None

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def pending(self) -> Optional[QuickAction]:
        return self._pending

    @property
    def buffer(self) -> str:
        return self._buffer

    @property
    def action_count(self) -> int:
        return len(self._actions)

    @property
    def seq(self) -> int:
        """按键序号。异步生成回来时对一下：变了就说明用户接着敲了别的字。"""
        return self._seq

    # ------------------------------------------------------------ 状态

    def reset(self, hwnd: Optional[int] = None) -> bool:
        """清空缓冲与 pending。返回之前是否处于 pending（需要隐藏呼吸灯）。"""
        had_pending = self._pending is not None
        self._buffer = ""
        self._pending = None
        self._pending_at = 0.0
        if hwnd is not None:
            self._hwnd = hwnd
        return had_pending

    def on_window(self, hwnd: int) -> Optional[FeedResult]:
        """前台窗口变了就重置；若之前在 pending，返回 HIDE。"""
        if hwnd == self._hwnd:
            return None
        had = self.reset(hwnd)
        return FeedResult(kind=HIDE) if had else None

    def expire(self, now: float) -> bool:
        """pending 超时自动作废。返回是否需要隐藏呼吸灯。"""
        if self._pending is not None and now - self._pending_at > self._timeout:
            self._pending = None
            return True
        return False

    def cancel(self) -> bool:
        if self._pending is not None:
            self._pending = None
            return True
        return False

    # ------------------------------------------------------------ 匹配

    def _find_match(self, char: str) -> Optional[QuickAction]:
        """在当前缓冲末尾找匹配的规则；最长触发串优先（_actions 已排序）。"""
        tail = self._buffer
        folded = tail.lower()
        for action in self._actions:
            trigger = action.trigger
            if action.detect_mode == DETECT_CHAR:
                if trigger == char:
                    return action
                continue

            if _is_word_trigger(trigger):
                # 词触发串**不区分大小写**：ST 里的关键字本身就不区分大小写，
                # 用户敲 `for` / `For` / `FOR` 应该命中同一条规则。
                # （曾经因为默认串写成大写 `FOR`、匹配又是精确比较，
                #   结果只有 `if`/`while`/`case` 能用、`for` 死活没反应。）
                key = trigger.lower()
                if not folded.endswith(key):
                    continue
                # 词边界检查：匹配处的前一个字符不能还是"词字符"
                # （否则 "sif" 会错误命中 "if"）
                idx = len(tail) - len(trigger)
                if idx > 0 and _is_word_char(tail[idx - 1]):
                    continue
                return action

            # 含标点的触发串（`///`、`:）按字面匹配，不折叠大小写
            if tail.endswith(trigger):
                return action
        return None

    # ------------------------------------------------------------ 输入

    def feed(self, key: KeyPress, char: Optional[str], now: float) -> FeedResult:
        """投喂一个按键。``char`` 是**最终到达目标程序的那个字符**
        （已经过标点替换），由调用方保证。"""
        if not key.is_down:
            return _RESULT_NONE

        # 任何一次真实按下都算"用户又动了键盘"，异步生成的结果随之作废
        self._seq += 1

        # Enter / Esc：硬重置
        if key.vk in (VK_RETURN, VK_ESCAPE):
            return FeedResult(kind=HIDE) if self.reset() else _RESULT_NONE

        if not self._enabled:
            return _RESULT_NONE

        # 无可见字符的键（方向键、F 键、纯修饰键）：切断缓冲区
        if char is None:
            had = self._pending is not None
            self._buffer = ""
            self._pending = None
            return FeedResult(kind=HIDE) if had else _RESULT_NONE

        self._buffer = (self._buffer + char)[-self._limit:]

        match = self._find_match(char)
        if match is None:
            if self._pending is not None:
                self._pending = None
                return FeedResult(kind=HIDE)
            return _RESULT_NONE

        if match.detect_mode == DETECT_CHAR:
            self._buffer = ""
            self._pending = None
            return FeedResult(
                kind=COMPLETE,
                action=match,
                backspaces=match.backspace_count(),
                text=self._render_content(match),
            )

        # 整词：进入 pending，等 Tab
        self._pending = match
        self._pending_at = now
        return FeedResult(kind=SHOW, action=match)

    def accept(self, now: float) -> FeedResult:
        """用户按了 Tab：确认补全。"""
        action = self._pending
        if action is None:
            return _RESULT_NONE
        self._pending = None
        self._buffer = ""
        return FeedResult(
            kind=COMPLETE,
            action=action,
            backspaces=action.backspace_count(),
            text=self._render_content(action),
        )

    def is_accept_key(self, key: KeyPress) -> bool:
        """这个按键是不是"确认补全"（当前只有 Tab）。"""
        return key.is_down and key.vk == VK_TAB
