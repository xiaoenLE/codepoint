"""标点替换决策。

把「这个按键要不要吞掉、吞掉后该写出什么」这件事变成**纯函数式判断**，
不碰任何 Win32 —— 所以可以完整地写单元测试。

决策链（任一环节不满足就放行，绝不"猜"）：

    总开关 → 生效范围 → 快捷键(Ctrl/Alt/Win) → 有无可见字符
           → 组字保护 → 输入法要求 → 规则表命中

最后一步命中才返回 swallow=True + 要写出的半角字符。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from ..core.events import ImeSnapshot, KeyPress, WindowInfo
from ..core.models import PunctuationRule


@dataclass(frozen=True)
class PunctuationOutcome:
    """一次标点决策的结果。"""

    swallow: bool = False
    emit: Optional[str] = None      # swallow=True 时要注入的字符
    reason: str = "pass"            # pass / replaced / not_in_scope / ...

    @property
    def replaced(self) -> bool:
        return self.swallow and self.emit is not None


_PASS = PunctuationOutcome()


class PunctuationService:
    """中文标点 → 英文标点。"""

    def __init__(self) -> None:
        self._half_to_full: Dict[str, str] = {}
        self._full_to_half: Dict[str, str] = {}
        self._configured_count = 0
        self._enabled = True
        self._require_ime = False
        self._guard_compose = True

    # ------------------------------------------------------------ 配置

    def configure(
        self,
        rules: Sequence[PunctuationRule],
        *,
        enabled: bool = True,
        require_ime: bool = False,
        guard_compose: bool = True,
        text_table: Optional[Dict[str, str]] = None,
    ) -> None:
        """把规则列表编译成 ``{英文字符: 中文全角字符}`` 的查找表。

        为什么要反向存？因为在线路上我们只知道"这个键会打出哪个半角字符"，
        需要立刻回答"它有没有被某个启用的规则覆盖"。
        """
        half_to_full: Dict[str, str] = {}
        full_to_half: Dict[str, str] = {}
        configured = 0
        listed = 0
        for rule in rules:
            half = rule.halfwidth
            full = rule.fullwidth
            if len(half) != 1 or len(full) != 1:
                continue
            # 列表里能显示的（哪怕当前是关着的）都要算进"规则 N 条"——
            # 用户在界面上数了 24 行，状态栏就该写 24，不能写"启用了 16 条"。
            listed += 1
            if not rule.enabled:
                continue
            configured += 1
            # 同一字符若有多条规则，先到的生效（用户可自行调整顺序）
            half_to_full.setdefault(half, full)
            full_to_half.setdefault(full, half)
        self._listed_count = listed
        self._configured_count = configured
        self._half_to_full = half_to_full
        # 文本转换表可以由外部注入（含中文表意标点），否则就用规则反推
        self._full_to_half = dict(text_table) if text_table else full_to_half
        self._enabled = enabled
        self._require_ime = require_ime
        self._guard_compose = guard_compose

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def rule_count(self) -> int:
        """界面上"标点规则"该显示多少条 —— **等于列表里的行数**（含关着的）。

        不能用 ``len(self._half_to_full)``：那是"有哪些按键需要拦"的去重集合
        （一个半角字符可能被多条规则覆盖，比如 ``-`` 同时对应 ``－`` 和 ``—``），
        数字会比列表里看到的行数小，用户一对照就以为规则丢了。
        同理也不能用"启用的条数"：默认表里有几条是关着的占位
        （＄％＆＊ 这些全角形式很少用），状态栏写 16 而列表 24 行，同样对不上。
        """
        return self._listed_count

    @property
    def enabled_count(self) -> int:
        """真正在生效的条数。要区分"列出了多少"和"开了多少"时用这个。"""
        return self._configured_count

    @property
    def key_target_count(self) -> int:
        """真正会被拦截的按键有几种（去重后），供排障/统计用。"""
        return len(self._half_to_full)

    @property
    def text_map_count(self) -> int:
        """文本转换表的条数（剪贴板净化用）。"""
        return len(self._full_to_half)

    def is_target(self, char: Optional[str]) -> bool:
        return bool(char) and char in self._half_to_full

    # ------------------------------------------------------------ 决策

    def decide(
        self,
        key: KeyPress,
        window: WindowInfo,
        ime: ImeSnapshot,
        *,
        targeted: bool,
    ) -> PunctuationOutcome:
        """决定这个按键要不要吞、吞掉后写什么。

        **原则：能在不插手的情况下得到正确结果，就绝不插手。**
        每多吞一个键就多一次注入，多一次失败机会。所以：
          - 输入法压根没开（纯英文状态）→ 打出来本来就是半角 → 直接放行
          - ``require_ime`` 打开时，只有在输入法确实处于中文标点状态才替换
        """
        if not self._enabled:
            return PunctuationOutcome(reason="disabled")
        if not targeted:
            return PunctuationOutcome(reason="not_in_scope")
        if key.modifiers.has_command:
            return PunctuationOutcome(reason="shortcut")      # Ctrl/Alt/Win 组合是快捷键
        char = key.char
        if not char or len(char) != 1:
            return PunctuationOutcome(reason="no_char")
        if char not in self._half_to_full:
            return PunctuationOutcome(reason="not_in_rules")

        # 组字保护：候选列表打开时绝不抢标点键（很多输入法用 , . - = 翻页选词）
        if self._guard_compose and ime.available and ime.composing:
            return PunctuationOutcome(reason="composing")

        if self._require_ime:
            # 严格模式：确认在中文标点/全角状态才替换。
            # 拿不到状态时保守放行（宁可不动，也不要替用户做错事）。
            if not (ime.available and ime.cjk_mode):
                return PunctuationOutcome(reason="ime_not_cjk")
        elif ime.definitely_halfwidth:
            # 输入法没打开 → 这个键本来就会打出半角字符，我们什么都不用做。
            # 少吞一个键 = 少一次注入 = 少一次出错机会。
            return PunctuationOutcome(reason="already_halfwidth")

        return PunctuationOutcome(swallow=True, emit=char, reason="replaced")

    # ------------------------------------------------------------ 直接转换（剪贴板用）

    def convert_text(self, text: str) -> tuple[str, int]:
        """把整段文本里的全角标点换成半角，返回 (新文本, 替换次数)。"""
        if not text:
            return text, 0
        out = []
        count = 0
        table = self._full_to_half
        for ch in text:
            half = table.get(ch)
            if half is not None:
                out.append(half)
                count += 1
            else:
                out.append(ch)
        return "".join(out), count

    def fullwidth_map(self) -> Dict[str, str]:
        """返回 ``{全角: 半角}``（含中文表意标点），供剪贴板过滤器复用。"""
        return dict(self._full_to_half)
