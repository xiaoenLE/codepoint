"""键盘事件总管 —— 把钩子回调接到各业务服务上。

这是 services 层的"应用服务"，也是唯一被 platform 层直接调用的业务对象。
它只做编排，不含判断细节（细节都在 punctuation / quick_action / app_filter 里）。

处理顺序（顺序本身就是安全策略）：

    ① 总开关关闭 → 放行
    ② 看前台窗口 / 生效范围 → 不在范围内就只维护状态、放行
    ③ Tab 且有待确认的快捷输入 → 吞掉 Tab，执行补全
    ④ 标点替换决策 → 需要吞键就在这里吞，并注入半角字符
    ⑤ 快捷输入状态机（投喂的是"最终到达目标程序的那个字符"）
    ⑥ 统一执行注入
    ⑦ 只要吞了键、且当时按着 Shift，就补一个"输入法守卫键"
       （见 ``_guard_ime``：不补的话输入法会把 Shift 当单击，切中英文）
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.events import (
    UNAVAILABLE_IME,
    KeyPress,
    VK_ESCAPE,
    VK_TAB,
)
from ..core.card import Card
from ..core.defaults import text_conversion_map
from ..core.models import DETECT_CHAR, Settings
from . import quick_action as qa
from .ports import ImeProbe, QuickActionNotifier, TextInjector, WindowProbe
from .punctuation import PunctuationService
from .quick_action import QuickActionEngine
from .app_filter import AppFilter

log = logging.getLogger("codepoint.pipeline")


@dataclass
class PipelineStats:
    """给界面显示的运行统计。"""

    keys_seen: int = 0
    keys_swallowed: int = 0
    punctuation_replaced: int = 0
    quick_completed: int = 0
    ai_started: int = 0
    ai_completed: int = 0
    ai_dropped: int = 0
    last_reason: str = ""
    # 诊断用：本该能转换、却放行了的原因计数。
    # 这是"为什么不转换"唯一的现场证据 —— 用户报障往往是"重启又好了"，
    # 事后翻日志什么也看不到，只能靠这个计数。
    block_counts: Dict[str, int] = field(default_factory=dict)

    #: 计数时忽略的原因码 —— ``not_in_rules`` 覆盖全部字母/数字/方向键，
    #: 量级碾压其它原因，留着只会把真正可疑的那条淹掉。
    _BORING = ("not_in_rules", "pass")

    def note_block(self, reason: str) -> None:
        if not reason or reason in self._BORING:
            return
        self.block_counts[reason] = self.block_counts.get(reason, 0) + 1

    def top_blocks(self, limit: int = 3) -> List[Tuple[str, int]]:
        ranked = sorted(self.block_counts.items(), key=lambda kv: -kv[1])
        return ranked[:limit]


@dataclass
class _PendingCompletion:
    """已经生成好、**等用户按 Tab 确认**的一段文本。

    为什么要挂起而不是直接插入：用户按一下热键，几百毫秒后屏幕上的代码
    自己变了 —— 他不知道出来的是什么，想反悔也来不及。先预览、再确认，
    和 ``///`` 的补全同一个心智模型。

    ``hwnd`` 记下当时的前台窗口：确认时如果用户已经切走，就不能再插了。
    """

    text: str
    backspaces: int          # 确认时要先吃掉的字符数（`//ai xxx` 那串）
    hwnd: int
    label: str = "AI 生成"


class KeyboardPipeline:
    """钩子回调的业务入口。"""

    def __init__(
        self,
        *,
        window_probe: WindowProbe,
        ime_probe: ImeProbe,
        injector: TextInjector,
        notifier: QuickActionNotifier,
        app_filter: Optional[AppFilter] = None,
        punctuation: Optional[PunctuationService] = None,
        quick_actions: Optional[QuickActionEngine] = None,
        clock=None,
        cancel_hook=None,
    ) -> None:
        self._windows = window_probe
        self._ime = ime_probe
        self._injector = injector
        self._notifier = notifier
        self._app_filter = app_filter or AppFilter()
        self._punctuation = punctuation or PunctuationService()
        self._quick = quick_actions or QuickActionEngine()
        self._now = clock or time.monotonic
        #: 按下 Esc 时通知上层"正在生成的话就掐掉"。None = 没人关心。
        #: 不在这里做中断，是因为 HTTP 请求在 app 的线程里 —— pipeline
        #: 只负责"看见了 Esc"这一件事。
        self._cancel_hook = cancel_hook
        self._enabled = True
        self._stats = PipelineStats()
        # —— AI 补全：只保留"挂起等确认"这一半 ——
        # 生成在 app 的热键线程里做（那一头要在按键前后操作剪贴板），
        # "弹窗还是直接插"也由 app 根据 ai_card_enabled 决定。
        self._pending_text: Optional[_PendingCompletion] = None

    # ------------------------------------------------------------ 配置

    def apply_settings(self, settings: Settings) -> None:
        """把配置推给各子服务（配置变化后调用）。"""
        self._app_filter.update(settings.app_items, settings.match_mode)
        self._punctuation.configure(
            settings.punctuation_rules,
            enabled=True,                              # 单项开关由总开关控制
            require_ime=settings.require_ime,
            guard_compose=settings.smart_guard_compose,
            text_table=text_conversion_map(settings.punctuation_rules),
        )
        self._quick.configure(
            settings.quick_actions,
            enabled=settings.quick_action_enabled,
        )
        self._enabled = settings.global_enabled
        if not self._enabled:
            self._close_indicator()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._close_indicator()

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stats(self) -> PipelineStats:
        return self._stats

    @property
    def punctuation(self) -> PunctuationService:
        return self._punctuation

    @property
    def quick_actions(self) -> QuickActionEngine:
        return self._quick

    @property
    def app_filter(self) -> AppFilter:
        return self._app_filter

    # ------------------------------------------------------------ 主流程

    def handle(self, key: KeyPress) -> bool:
        """返回 True 表示吞掉这个按键。异常一律喂给上层时保持放行，绝不卡死输入。"""
        try:
            return self._handle(key)
        except Exception:      # noqa: BLE001
            log.exception("处理按键时异常，已放行")
            return False

    def _handle(self, key: KeyPress) -> bool:
        self._stats.keys_seen += 1

        if not self._enabled:
            self._stats.last_reason = "已暂停"
            return False

        window = self._windows.front()

        # 窗口切换 → 重置快捷输入状态
        switched = self._quick.on_window(window.hwnd)
        if switched is not None and switched.kind == qa.HIDE:
            self._notifier.hide()

        targeted = self._app_filter.matches(window)

        # ②.4 生成过程中按 Esc = **掐断请求**，不是等它写完再丢掉。
        #     FIM 一次可能补十几行、跑十几秒，早看到跑偏就该能停手。
        #     这里只是"通知"，真正的中断由 app 在流式回调里抛异常完成。
        if key.is_down and key.vk == VK_ESCAPE and self._cancel_hook is not None:
            self._cancel_hook()

        # ②.5 有挂起的补全结果时：Tab = 接受，其它任何键 = 作废
        #     （必须先于后面所有逻辑：此时 Tab 不能被当成"触发 AI"，
        #       也不该落到编辑器里插一个制表符。）
        #
        #     ⚠ Tab 只在**受管程序**里才算"接受"。用户切到别的软件后按的 Tab
        #     跟我们没关系，不能吞 —— 以前不看 ``targeted``，在别的程序里按
        #     Tab 会被吃掉、看起来像"键盘失灵"。结果本身照旧作废（窗口/光标
        #     都变了，插进去就是错位）。
        if self._pending_text is not None and key.is_down:
            if targeted and key.vk == VK_TAB:
                self.accept_pending()
                self._stats.keys_swallowed += 1
                return True
            # Esc、方向键、普通字符 —— 光标位置要变了，这份结果就不能再插，
            # 否则代码会落在错误的地方。宁可丢掉，也不能插错。
            self.cancel_pending()

        if not targeted:
            return False

        # ③ Tab（必须在标点之前，否则 Tab 会被当成普通键）
        #    两条路：有待确认的片段 → 老逻辑（同步补全）；
        #            没有 pending 但缓冲区是 `//ai 参数` → 派发给后台模型。
        if key.vk == VK_TAB:
            if self._quick.pending is not None:
                result = self._quick.accept(self._now())
                self._notifier.hide()
                if result.kind == qa.COMPLETE:
                    self._guard_ime(key)
                    self._injector.send_backspaces(result.backspaces)
                    # 快捷输入的补全内容也走 send_block（短的单行仍逐字符）
                    self._injector.send_block(result.text)
                    self._stats.quick_completed += 1
                    self._stats.last_reason = f"快捷输入：{result.action.trigger if result.action else ''}"
                self._stats.keys_swallowed += 1
                return True

        # ④ 标点替换 —— **只决策，先不注入**
        #    先注入再判断快捷输入会让"单字符补全"多绕一圈（注入字符 → 退格 → 再注入），
        #    所以把注入推迟到第 ⑥ 步统一执行。
        #
        #    ⚠ 输入法状态查询要跨进程调 IMM，是整个回调里最贵的动作。
        #    只有"这个键真有可能命中标点规则"时才去查——字母数字按键直接跳过。
        maybe_target = (
            key.char is not None
            and not key.modifiers.has_command
            and self._punctuation.is_target(key.char)
        )
        ime = self._ime.state(window.hwnd) if maybe_target else UNAVAILABLE_IME
        outcome = self._punctuation.decide(key, window, ime, targeted=True)
        swallow = outcome.swallow
        emit = outcome.emit if outcome.replaced else None
        effective_char = emit if emit is not None else key.char

        if outcome.replaced:
            self._stats.last_reason = f"标点替换：{emit}"
        else:
            self._stats.last_reason = outcome.reason
            # 留一份"为什么不转换"的计数：用户报障时功能往往已经恢复
            # （重启就好），这是唯一还能事后复盘的现场。
            self._stats.note_block(outcome.reason)

        # ⑤ 快捷输入
        result = self._quick.feed(key, effective_char, self._now())

        backspaces = 0
        completion_text = None

        if result.kind == qa.COMPLETE and result.action is not None:
            completion_text = result.text
            backspaces = result.backspaces
            if result.action.detect_mode == DETECT_CHAR:
                # 单字符补全：触发字符**还没落到目标程序里**（要么本来该由我们注入，
                # 要么就是下面这次强制吞键）。所以：
                #   ① 不用再注入那个字符了
                #   ② 退格数减 1
                #   ③ **必须吞掉这个键** —— 不吞的话目标程序随后才收到该字符，
                #      而我们的退格已经先执行了，会删掉前一个字符再补内容，位置全错。
                #      （这是"按 : 结果 : 和 := 混着乱跳"的根因）
                emit = None
                backspaces = max(0, backspaces - 1)
                swallow = True

        # ⑥ 统一执行注入（顺序即语义：输入法守卫键 → 标点字符 → 退格 → 补全内容）
        #
        #    守卫键排在替换字符前面：让输入法先看到"还有别的键"，
        #    后面那个字符以什么形式到达就都无所谓了。
        if swallow:
            self._guard_ime(key)

        if emit is not None:
            self._injector.send_text(emit)
            self._stats.punctuation_replaced += 1

        if completion_text is not None:
            if backspaces > 0:
                self._injector.send_backspaces(backspaces)
            # 快捷输入：多行模板走粘贴，`:=` 这种短的仍逐字符（见 send_block）
            self._injector.send_block(completion_text)
            self._stats.quick_completed += 1

        # ⑦ 呼吸灯
        if result.kind == qa.SHOW and result.action is not None:
            self._notifier.show(result.action)
        elif result.kind in (qa.HIDE, qa.COMPLETE):
            self._notifier.hide()

        if swallow:
            self._stats.keys_swallowed += 1
        return swallow

    # ------------------------------------------------------------ 待确认的补全
    #
    # 生成在 app 的热键线程里做（那一头要在按键前后操作剪贴板，塞进钩子线程
    # 会把钩子拖死）。管道这里只负责"接收一段现成的文本 → 挂着 → 等 Tab"。
    #
    # 为什么注入不在钩子线程之外的线程做：等 Tab 是用户动作，Tab 的 key-down
    # 就在钩子回调里，同步插进去最自然，也不会有并发问题。

    def offer(self, text: str, *, backspaces: int = 0,
              label: str = "AI 生成", hwnd: Optional[int] = None,
              timeout: float = 0.0) -> bool:
        """把生成好的文本**挂起**，等用户按 Tab 确认。

        这是整个交互的核心：不再"生成完就插"，而是"生成完先给你看"。
        ``timeout`` > 0 时，预览卡到点会自己关（界面层的倒计时），
        用户也可以随时按 Esc 提前关掉。
        返回 False 表示内容为空、没挂上。
        """
        if not (text or "").strip():
            return False
        target = self._windows.front().hwnd if hwnd is None else hwnd
        self._pending_text = _PendingCompletion(
            text=text,
            backspaces=max(0, int(backspaces)),
            hwnd=int(target or 0),
            label=label,
        )
        self._stats.ai_started += 1
        self._notifier.show_card(Card.ready(label, text, timeout=timeout))
        self._stats.last_reason = f"待确认：{label}"
        return True

    @property
    def has_pending_text(self) -> bool:
        return self._pending_text is not None

    def accept_pending(self) -> bool:
        """用户按 Tab：真正插入。"""
        pending = self._pending_text
        if pending is None:
            return False
        self._pending_text = None
        self._notifier.hide()

        if self._windows.front().hwnd != pending.hwnd:
            self._stats.ai_dropped += 1
            log.info("确认时前台窗口已变，放弃插入")
            return False

        if pending.backspaces > 0:
            self._injector.send_backspaces(pending.backspaces)
        # 整段插入（AI 结果 → 必须粘贴，哪怕只有一行）：逐字符写入会被编辑器的
        # 自动补全/自动缩进插一脚，写出来的代码是歪的。
        self._injector.send_block(pending.text, always_paste=True)
        self._stats.ai_completed += 1
        self._stats.quick_completed += 1
        self._stats.last_reason = "已接受补全"
        return True

    def cancel_pending(self) -> bool:
        """用户按了别的键 / Esc：这份结果作废。"""
        if self._pending_text is None:
            return False
        self._pending_text = None
        self._notifier.hide()
        self._stats.ai_dropped += 1
        self._stats.last_reason = "补全已取消"
        return True

    def _auto_hide(self, delay: float) -> None:
        """过一会儿自动收起提示（失败提示不该一直挂在那）。"""
        try:
            timer = threading.Timer(delay, self._notifier.hide)
            timer.daemon = True
            timer.start()
        except Exception:      # noqa: BLE001
            pass

    def close(self) -> None:
        """退出前收摊：作废挂起的结果，别让一块窗孤零零留在屏幕上。"""
        self._pending_text = None
        try:
            self._notifier.hide()
        except Exception:      # noqa: BLE001
            pass

    # ------------------------------------------------------------ 辅助

    def _guard_ime(self, key: KeyPress) -> None:
        """吞掉一个「按着 Shift 的键」之后，补一个无名键给输入法看。

        中文输入法（微软拼音默认）用「Shift 按下与抬起之间有没有别的键」来区分
        "单击 Shift 切中英文" 和 "Shift 组合键"。我们吞掉 Shift+符号 那个键之后，
        输入法看到的就只剩一个孤零零的 Shift —— 于是每敲一个 `:` `<` `>` `?` `"`
        中英文就被切走一次（用户实测报障原话："每次中文输入其他需要 shift 加其他
        按键时，他会触发切换语言"）。补一个无名键即可让输入法放弃切换
        （实测见 `tools/ime_shift_probe.py`）。

        Ctrl/Alt/Win 组合我们**从不吞**（标点决策第一步就放行），所以只看 Shift。

        这里刻意吞掉异常：守卫失败最多是"输入法被切走一次"，
        绝不能因此让 `handle()` 抛出、把本该吞掉的键放行出去。
        """
        if not (key.modifiers.shift and not key.modifiers.has_command):
            return
        try:
            self._injector.send_ime_guard()
        except Exception:      # noqa: BLE001
            log.exception("补输入法守卫键失败（输入法可能被切到英文）")

    def _close_indicator(self) -> None:
        # 挂起的结果也要一起作废：总开关关掉之后按 Tab 不该再插入任何东西
        self._pending_text = None
        self._quick.reset()
        try:
            self._notifier.hide()
        except Exception:      # noqa: BLE001
            pass

    def tick(self) -> None:
        """周期性调用：处理呼吸灯超时。"""
        if self._quick.expire(self._now()):
            self._notifier.hide()
