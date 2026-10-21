"""剪贴板标点净化。

做成**纯文本变换**，
IO 由调用方负责，因此完全可测。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple


class ClipboardPunctuationFilter:
    """把文本里的中文全角标点换成英文半角。"""

    def __init__(self, enabled: bool = True) -> None:
        self._table: Dict[str, str] = {}
        self._enabled = enabled

    # ------------------------------------------------------------ 配置

    def configure(self, full_to_half: Dict[str, str], *, enabled: Optional[bool] = None) -> None:
        self._table = dict(full_to_half or {})
        if enabled is not None:
            self._enabled = enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def rule_count(self) -> int:
        return len(self._table)

    # ------------------------------------------------------------ 变换

    def transform(self, text: Optional[str]) -> Tuple[str, int]:
        """返回 (新文本, 替换次数)。未启用或无变化时原样返回。"""
        if not text or not self._enabled or not self._table:
            return (text or ""), 0
        out = []
        count = 0
        table = self._table
        for ch in text:
            half = table.get(ch)
            if half is not None:
                out.append(half)
                count += 1
            else:
                out.append(ch)
        return "".join(out), count

    def should_rewrite(self, text: Optional[str]) -> bool:
        """先做一次"有没有必要改写"的快速判断，避免无谓地动剪贴板。"""
        if not text or not self._enabled or not self._table:
            return False
        return any(ch in self._table for ch in text)
