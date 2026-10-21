"""「快捷输入」页 —— 代码片段补全规则。

列：检测字符串 / 检测方法 / 补全内容 / 操作（铅笔编辑 + 垃圾桶删除）。
和其它表格页共用 ``TablePage``，差别只在列定义与两个动作。
"""

from __future__ import annotations

from typing import List, Optional

from ...core.defaults import default_quick_actions
from ...core.models import DETECT_LABELS
from ..table_view import Column, Row
from .base import TablePage
from ..dialogs import QuickActionDialog, ask_confirm

_PREVIEW_LIMIT = 22     # 补全内容列只显示前 N 个字符（换行会让行高爆掉）


class QuickActionPage(TablePage):
    """快捷操作列表。"""

    # 列：检测字符串 / 检测方法 / 补全内容 / 操作
    columns = (
        Column("checkbox", "启", 60),
        Column("trigger", "检测字符串", 150),
        Column("mode", "检测方法", 100),
        Column("content", "补全内容", 0, stretch=True),
        Column("action", "操作", 74, align="e"),
    )

    def _build_actions(self) -> None:
        self.header.button("增加+", self._add)
        self.header.button("恢复默认", self._reset)

    # ------------------------------------------------------------ 数据

    def rows(self) -> List[Row]:
        return [
            Row(rid=str(i),
                cells=["☑" if a.enabled else "☐", a.trigger,
                       DETECT_LABELS.get(a.detect_mode, a.detect_mode),
                       _preview(a.content), "✎🗑"],
                enabled=a.enabled)
            for i, a in enumerate(self.facade.get_settings().quick_actions)
        ]

    # ------------------------------------------------------------ 动作

    def on_toggle(self, rid: str) -> None:
        actions = self.facade.get_settings().quick_actions
        index = _index_of(rid, len(actions))
        if index is None:
            return
        actions[index].enabled = not actions[index].enabled
        self._commit()

    def on_action(self, rid: str, slot: int) -> None:
        actions = self.facade.get_settings().quick_actions
        index = _index_of(rid, len(actions))
        if index is None:
            return
        if slot == 0:                       # 铅笔：编辑
            edited = QuickActionDialog(self.winfo_toplevel(),
                                       action=actions[index]).show()
            if edited is not None:
                actions[index] = edited
                self._commit()
            return
        if slot == 1:                       # 垃圾桶：删除
            action = actions[index]
            if ask_confirm(self.winfo_toplevel(), title="删除快捷输入",
                           message=f"删除规则「{action.trigger} → {action.content[:20]}」？",
                           confirm_text="删除", danger=True):
                del actions[index]
                self._commit()

    def on_cell(self, rid: str, column: str) -> None:
        """点任意一格 = 编辑这条（比去点小铅笔顺手）。"""
        actions = self.facade.get_settings().quick_actions
        index = _index_of(rid, len(actions))
        if index is None:
            return
        self.on_action(rid, 0)

    def _add(self) -> None:
        action = QuickActionDialog(self.winfo_toplevel()).show()
        if action is None:
            return
        actions = self.facade.get_settings().quick_actions
        for old in actions:
            if old.trigger == action.trigger and old.detect_mode == action.detect_mode:
                old.content = action.content
                old.enabled = True
                self._commit()
                return
        actions.append(action)
        self._commit()

    def _reset(self) -> None:
        if not ask_confirm(self.winfo_toplevel(), title="恢复默认",
                           message="把所有快捷输入恢复成默认列表？\n自己加的规则会丢失。",
                           confirm_text="恢复"):
            return
        self.facade.get_settings().quick_actions = default_quick_actions()
        self._commit()

    def _commit(self) -> None:
        self.facade.apply_live()
        self.facade.save()
        self.refresh()


def _index_of(rid: str, limit: int) -> Optional[int]:
    try:
        index = int(rid)
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < limit else None


def _preview(text: str) -> str:
    """把多行补全内容压成一行预览。"""
    flat = " ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    if len(flat) > _PREVIEW_LIMIT:
        return flat[:_PREVIEW_LIMIT] + "…"
    return flat or "（空）"


__all__ = ["QuickActionPage"]
