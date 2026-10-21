"""「标点符号」页 —— 标点映射列表。

列：启 / 英文字符 / 操作（红色 ✕ 删除）。
点「启」列的勾选框切换启用；点英文字符那一格直接编辑。
"""

from __future__ import annotations

from typing import List

from ...core.defaults import default_punctuation_rules
from ..table_view import Column, Row
from .base import TablePage
from ..dialogs import PunctuationDialog, ask_confirm


class PunctuationPage(TablePage):
    """英文标点符号列表。"""

    # 三列：启 / 英文字符（要输出成什么） / 操作
    # 全角那一侧不单独占一列，点这一行就能看到并修改它
    columns = (
        Column("checkbox", "启", 60),
        Column("char", "英文字符", 200),
        Column("gap", "", 0, stretch=True),      # 弹性空白：把"操作"顶到最右边
        Column("action", "操作", 84, align="e"),
    )

    def _build_actions(self) -> None:
        self.header.button("增加+", self._add)
        self.header.button("恢复默认", self._reset)

    # ------------------------------------------------------------ 数据

    def rows(self) -> List[Row]:
        return [
            Row(rid=str(i),
                cells=["☑" if r.enabled else "☐", r.halfwidth, "", "✕"],
                enabled=r.enabled)
            for i, r in enumerate(self.facade.get_settings().punctuation_rules)
        ]

    # ------------------------------------------------------------ 动作

    def on_toggle(self, rid: str) -> None:
        rules = self.facade.get_settings().punctuation_rules
        index = _index_of(rid, len(rules))
        if index is None:
            return
        rules[index].enabled = not rules[index].enabled
        self._commit()

    def on_action(self, rid: str, slot: int) -> None:
        rules = self.facade.get_settings().punctuation_rules
        index = _index_of(rid, len(rules))
        if index is None or slot != 0:
            return
        rule = rules[index]
        if ask_confirm(self.winfo_toplevel(), title="删除标点",
                       message=f"删除规则「{rule.fullwidth} → {rule.halfwidth}」？",
                       confirm_text="删除", danger=True):
            del rules[index]
            self._commit()

    def on_cell(self, rid: str, column: str) -> None:
        """点"英文字符"那一格 = 编辑这条规则。"""
        rules = self.facade.get_settings().punctuation_rules
        index = _index_of(rid, len(rules))
        if index is None:
            return
        if column != "char":
            return
        edited = PunctuationDialog(self.winfo_toplevel(), rule=rules[index]).show()
        if edited is not None:
            rules[index] = edited
            self._commit()

    def _add(self) -> None:
        rule = PunctuationDialog(self.winfo_toplevel()).show()
        if rule is None:
            return
        rules = self.facade.get_settings().punctuation_rules
        for old in rules:
            if old.fullwidth == rule.fullwidth:
                old.halfwidth = rule.halfwidth
                old.enabled = True
                self._commit()
                return
        rules.append(rule)
        self._commit()

    def _reset(self) -> None:
        if not ask_confirm(self.winfo_toplevel(), title="恢复默认",
                           message="把所有标点规则恢复成默认列表？\n自己加的规则会丢失。",
                           confirm_text="恢复"):
            return
        self.facade.get_settings().punctuation_rules = default_punctuation_rules()
        self._commit()

    def _commit(self) -> None:
        self.facade.apply_live()
        self.facade.save()
        self.refresh()


def _index_of(rid: str, limit: int):
    try:
        index = int(rid)
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < limit else None


__all__ = ["PunctuationPage"]
