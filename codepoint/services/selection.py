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
#: 抓"前 N 行"要更宽裕一点：Shift+Up 是按行发的，行多了本身要几十毫秒，
#: 编辑器还要先处理选区再复制。
DEFAULT_LINE_WAIT = 0.7
DEFAULT_POLL = 0.01


@dataclass(frozen=True)
class GrabResult:
    """一次抓取的结论。"""

    text: str = ""
    restored: bool = False       # 剪贴板还原成功了吗
    timed_out: bool = False      # 是不是等到超时都没见剪贴板变化
    #: 用户一直按着 Ctrl/Alt 没松手 —— 此时**根本没发** Ctrl+C。
    #: 单独一个标记，是因为给用户的提示完全不同（"松手" vs "先选中"）。
    modifiers_stuck: bool = False

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
        line_wait: float = DEFAULT_LINE_WAIT,
        poll: float = DEFAULT_POLL,
        sleep: Optional[Callable[[float], None]] = None,
        monotonic: Optional[Callable[[], float]] = None,
    ) -> None:
        self._clipboard = clipboard
        self._copier = copier
        self._wait = max(0.0, wait)
        self._line_wait = max(self._wait, line_wait)
        self._poll = max(0.001, poll)
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def grab(self) -> GrabResult:
        backup = self._read_backup()
        before = self._sequence()

        # ① 必须等修饰键松开再发 Ctrl+C。
        #    热键在"按下"那一刻触发，这时 Ctrl/Alt 还按着 —— 直接发就变成
        #    Ctrl+Alt+C，目标程序根本不认，剪贴板不会变，最后表现成
        #    "明明选中了却说没拿到文本"（实测踩过）。
        if not self._copier.wait_ready():
            log.info("修饰键一直没松开，本次不发送 Ctrl+C")
            return GrabResult(modifiers_stuck=True)

        try:
            self._copier.copy_selection()
        except Exception:      # noqa: BLE001 —— 按不出 Ctrl+C 就当作"没选中"
            log.warning("发送 Ctrl+C 失败，拿不到选中内容", exc_info=True)
            return GrabResult()

        text, timed_out = self._wait_for_change(before)
        restored = self._restore(backup)
        return GrabResult(text=text, restored=restored, timed_out=timed_out)

    def grab_lines(self, count: int) -> GrabResult:
        """抓"光标上方 count 行"当上下文。

        做法：Shift+Up × count 选中 → Ctrl+C → Right 取消选区（光标归位）。
        这是没有 UIA 的编辑器（CODESYS / Invtmatic）唯一能取出上下文的办法。

        ⚠ 无论中途发生什么，最后那次 Right **一定要发** —— 否则用户屏幕上
          会留着一片蓝汪汪的选区，还不知道是谁弄的。
        """
        count = int(count)
        if count <= 0:
            return GrabResult()

        backup = self._read_backup()
        before = self._sequence()

        if not self._copier.wait_ready():
            return GrabResult(modifiers_stuck=True)

        try:
            self._copier.select_lines_up(count)
            self._copier.copy_selection()
        except Exception:      # noqa: BLE001
            log.warning("抓上下文时按键失败", exc_info=True)
            self._safe_cancel_selection()
            return GrabResult()

        text, timed_out = self._wait_for_change(before, wait=self._line_wait)
        self._safe_cancel_selection()
        restored = self._restore(backup)
        return GrabResult(text=text, restored=restored, timed_out=timed_out)

    def grab_lines_down(self, count: int) -> GrabResult:
        """抓"光标下方 count 行"—— FIM 的 suffix。

        做法：Shift+Down × count 选中 → Ctrl+C → **Left** 取消选区（光标归位）。

        为什么取消要用 Left：向下选出来的选区，左端才是原来的光标位置；
        用 Right（向上选时是对的）会把光标挪到下方那行的行尾去，
        用户接着打字就插错地方了。

        ⚠ 失败也要保证取消选区那次按键发出去，否则屏幕上留一片蓝。
        """
        count = int(count)
        if count <= 0:
            return GrabResult()

        backup = self._read_backup()
        before = self._sequence()

        if not self._copier.wait_ready():
            return GrabResult(modifiers_stuck=True)

        try:
            self._copier.select_lines_down(count)
            self._copier.copy_selection()
        except Exception:      # noqa: BLE001
            log.warning("抓下文时按键失败", exc_info=True)
            self._safe_cancel_selection(to_start=True)
            return GrabResult()

        # 向下选的行数少（默认 3 行），用普通等待就够；这里多等没用，
        # 光标在文件末尾时根本没东西可选，白等只会让用户觉得卡。
        text, timed_out = self._wait_for_change(before, wait=self._wait)
        self._safe_cancel_selection(to_start=True)
        restored = self._restore(backup)
        return GrabResult(text=text, restored=restored, timed_out=timed_out)

    def _safe_cancel_selection(self, to_start: bool = False) -> None:
        try:
            self._copier.cancel_selection(to_start=to_start)
        except Exception:      # noqa: BLE001
            log.warning("取消选区失败（用户屏幕上可能留着一片选区）", exc_info=True)

    # ------------------------------------------------------------ 内部

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

    def _wait_for_change(self, before: int, *,
                         wait: Optional[float] = None) -> tuple[str, bool]:
        """轮询到剪贴板序号变了为止。

        序号变了才去读内容：没选中东西时 Ctrl+C 不会碰剪贴板，
        序号不变，于是走到超时 —— 正好把"用户其实没选"这种情况区分出来。
        """
        deadline = self._monotonic() + (self._wait if wait is None else max(0.0, wait))
        while self._monotonic() < deadline:
            if self._sequence() != before:
                try:
                    value = self._clipboard.get_text()
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
