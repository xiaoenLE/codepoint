"""把"当前选中的那段文字"捞出来 —— 热键 Ctrl+Alt+G 的第一步。

为什么只能靠剪贴板
    UIA / MSAA 在 CODESYS、Invtmatic 这类自绘编辑器上读不到正文
    （实测：整个窗口只暴露 6 个元素，全是按钮和菜单，代码区根本不在树里）。
    于是拿选中内容的唯一通用手段就是：**替用户按一次 Ctrl+C，再从剪贴板读**。

代价（必须写清楚，这不是白拿的）
    · 会覆盖用户剪贴板 —— 所以用完立刻把原来的文本写回去。
    · 原来剪贴板里是**图片/文件**时无法还原（我们只备份文本）。
    · 会在 Win+V 剪贴板历史里留一条记录（系统行为，改不了）。
    · 目标程序以管理员运行时，本程序也必须提权，否则 Ctrl+C 根本进不去。

为什么不在这里等太久
    超时 450ms 就放弃。等再久用户已经开始按别的键了，光标位置也不再可信。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .ports import ClipboardPort, SelectionCopier

log = logging.getLogger("codepoint.selection")

#: 按下 Ctrl+C 之后最多等多久（秒）。编辑器复制一点点文本是毫秒级的，
#: 450ms 已经非常宽裕；再长只会在"什么都没选中"时白白卡住用户。
DEFAULT_WAIT = 0.45
DEFAULT_POLL = 0.01
RESTORE_CHECK_WAIT = 0.12
LEGACY_PREFIX_WAIT = 0.7


def _caret_steps(text: str) -> int:
    # 从行尾走到下一行开头只有一次移动。Python 字符数也不会把 UTF-16 代理对算成两步。
    return len(text.replace("\r\n", "\n"))


@dataclass(frozen=True)
class GrabResult:
    """一次抓取的结论。"""

    text: str = ""
    restored: bool = False       # 剪贴板还原成功了吗
    timed_out: bool = False      # 是不是等到超时都没见剪贴板变化
    #: 用户一直按着 Ctrl/Alt 没松手 —— 此时**根本没发** Ctrl+C。
    #: 单独一个标记，是因为给用户的提示完全不同（"松手" vs "先选中"）。
    modifiers_stuck: bool = False
    caret_uncertain: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


class SelectionGrabber:
    """备份剪贴板 → 按 Ctrl+C → 等序号变 → 读文本 → 还原剪贴板。

    ``sleep`` / ``monotonic`` 可注入，测试里就不用真的等 450ms。
    """

    def __init__(
        self,
        clipboard: ClipboardPort,
        copier: SelectionCopier,
        *,
        wait: float = DEFAULT_WAIT,
        poll: float = DEFAULT_POLL,
        sleep: Optional[Callable[[float], None]] = None,
        monotonic: Optional[Callable[[], float]] = None,
    ) -> None:
        self._clipboard = clipboard
        self._copier = copier
        self._wait = max(0.0, wait)
        self._poll = max(0.001, poll)
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def grab(self, *, can_copy: Optional[Callable[[], bool]] = None) -> GrabResult:

        # ① 必须等修饰键松开再发 Ctrl+C。
        #    热键在"按下"那一刻触发，这时 Ctrl/Alt 还按着 —— 直接发就变成
        #    Ctrl+Alt+C，目标程序根本不认，剪贴板不会变，最后表现成
        #    "明明选中了却说没拿到文本"（实测踩过）。
        if not self._copier.wait_ready():
            log.info("修饰键一直没松开，本次不发送 Ctrl+C")
            return GrabResult(modifiers_stuck=True)

        if can_copy is not None and not can_copy():
            return GrabResult()
        backup = self._read_backup()
        before = self._sequence()
        try:
            self._copier.copy_selection()
        except Exception:      # noqa: BLE001 —— 按不出 Ctrl+C 就当作"没选中"
            log.warning("发送 Ctrl+C 失败，拿不到选中内容", exc_info=True)
            return GrabResult()

        text, timed_out = self._wait_for_change(before)
        restored = not timed_out and self._restore(backup)
        return GrabResult(text=text, restored=restored, timed_out=timed_out)

    def grab_context(self, count: int, *, below: bool, can_copy: Callable[[], bool],
                     collapse_to_anchor: bool = False) -> GrabResult:
        """临时选中并复制上下文，再收起选区。"""
        if count <= 0 or not can_copy():
            return GrabResult()
        if not self._copier.wait_ready():
            return GrabResult(modifiers_stuck=True)
        backup = self._read_backup()
        sequence = self._sequence()
        caret = self._copier.caret_position()
        if not can_copy():
            return GrabResult()

        text, timed_out = "", True
        selected = self._copier.select_context(count, below=below)
        try:
            if selected and can_copy():
                self._copier.copy_selection()
                wait = max(self._wait, LEGACY_PREFIX_WAIT) if collapse_to_anchor and not below else None
                text, timed_out = self._wait_for_change(sequence, wait=wait)
        except Exception:      # noqa: BLE001
            log.warning("复制临时上下文选区失败", exc_info=True)

        current = self._copier.caret_position() if can_copy() else None
        moved = caret is not None and current is not None and caret != current
        uncertain = not selected or (not text and moved)
        if text:
            if collapse_to_anchor:
                # 兼容旧版 ST 抓取：上文锚点在选区右端，下文锚点在选区左端。
                uncertain |= not can_copy() or not self._copier.collapse_selection(to_start=below)
                current = self._copier.caret_position() if can_copy() else None
                uncertain |= caret is not None and current is not None and caret != current
            else:
                uncertain |= not self._restore_anchor(text, left=below, can_copy=can_copy)

        restored = not timed_out and can_copy() and self._restore(backup)
        log.info("键盘抓取%s：请求 %d 行，复制 %d 字，%s",
                 "下文" if below else "上文", count, len(text),
                 "光标复位未确认" if uncertain else "无可用选区" if not text else "已复位")
        return GrabResult(text=text, restored=restored, timed_out=timed_out,
                          caret_uncertain=uncertain)

    # ------------------------------------------------------------ 内部

    def _restore_anchor(self, text: str, *, left: bool, can_copy: Callable[[], bool]) -> bool:
        total = _caret_steps(text)
        if not can_copy() or not self._copier.move_context_endpoint(total, left=left):
            return False
        remaining = self._read_remaining_selection(can_copy)
        if remaining == "":
            return True
        if remaining is None or _caret_steps(remaining) > total or not can_copy():
            return False

        # 有些编辑器在复制时附加不可走到的末尾换行。反方向试一步，比较选区变长还是变短。
        if not self._copier.move_context_endpoint(1, left=not left):
            return False
        probed = self._read_remaining_selection(can_copy)
        if probed == "":
            return True
        if probed is None or not can_copy():
            return False
        before, after = _caret_steps(remaining), _caret_steps(probed)
        if before == after or after > total + 1:
            return False
        return (self._copier.move_context_endpoint(after, left=(left if after > before else not left))
                and self._read_remaining_selection(can_copy) == "")

    def _read_remaining_selection(self, can_copy: Callable[[], bool]) -> Optional[str]:
        if not can_copy():
            return None
        # 用复制确认选区是否已消失，避免把滚动造成的坐标变化当作移动。
        sequence = self._sequence()
        try:
            self._copier.copy_selection()
            text, _ = self._wait_for_change(sequence, wait=RESTORE_CHECK_WAIT)
            return text if can_copy() else None
        except Exception:      # noqa: BLE001
            log.warning("确认临时选区是否结束时失败", exc_info=True)
            return None

    def _read_backup(self) -> Optional[str]:
        try:
            return self._clipboard.get_text()
        except Exception:      # noqa: BLE001
            return None

    def _sequence(self) -> int:
        try:
            return int(self._clipboard.sequence_number())
        except Exception:      # noqa: BLE001
            return 0

    def _wait_for_change(self, before: int, *, wait: Optional[float] = None) -> tuple[str, bool]:
        """轮询到剪贴板序号变了为止。

        序号变了才去读内容：没选中东西时 Ctrl+C 不会碰剪贴板，
        序号不变，于是走到超时 —— 正好把"用户其实没选"这种情况区分出来。
        """
        deadline = self._monotonic() + (self._wait if wait is None else wait)
        while self._monotonic() < deadline:
            if self._sequence() != before:
                try:
                    value = self._clipboard.get_selected_text()
                except Exception:      # noqa: BLE001
                    value = None
                if value is not None:
                    return value, False
            self._sleep(self._poll)
        return "", True

    def _restore(self, backup: Optional[str]) -> bool:
        """把用户原来的剪贴板还回去。

        ``backup is None`` 表示原来没有文本（可能是图片/文件），
        这种情况**不做任何事** —— 我们没法用文本还原一张图，
        硬写空串只会让用户以为"我的截图被吃了"。
        """
        if backup is None:
            return False
        try:
            return bool(self._clipboard.set_text(backup))
        except Exception:      # noqa: BLE001
            log.warning("还原剪贴板失败", exc_info=True)
            return False


__all__ = ["SelectionGrabber", "GrabResult", "DEFAULT_WAIT"]
