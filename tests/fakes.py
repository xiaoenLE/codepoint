"""测试替身（Fake / Stub）。

它们的存在正是"低耦合"的回报：services 只认 ports 里的 Protocol，
所以这里用几十行就能把整个外部世界替换掉。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from codepoint.core.events import ImeSnapshot, KeyPress, Modifiers, WindowInfo


class FakeInjector:
    """记录所有注入动作，而不是真的按键。"""

    def __init__(self) -> None:
        self.texts: List[str] = []
        self.backspaces: List[int] = []
        self.guards: int = 0
        self.calls: List[Tuple[str, object]] = []
        self.last_always_paste: bool = False

    def send_text(self, text: str) -> None:
        self.texts.append(text)
        self.calls.append(("text", text))

    def send_block(self, text: str, *, always_paste: bool = False, **_kw) -> None:
        self.texts.append(text)
        self.calls.append(("block", text))
        self.last_always_paste = always_paste

    def send_backspaces(self, count: int) -> None:
        self.backspaces.append(count)
        self.calls.append(("back", count))

    def send_ime_guard(self) -> None:
        self.guards += 1
        self.calls.append(("guard", None))

    @property
    def total_backspaces(self) -> int:
        return sum(self.backspaces)

    def output(self) -> str:
        return "".join(self.texts)


class FakeWindowProbe:
    """可任意设定"当前前台窗口"。"""

    def __init__(self, window: Optional[WindowInfo] = None, pid: int = 4242) -> None:
        self.window = window or WindowInfo(
            hwnd=1001, pid=100, process_name="tiaportal.exe",
            process_path=r"C:\Siemens\Portal\bin\tiaportal.exe",
            window_title="TIA Portal - 项目1", thread_id=100,
        )
        self._pid = pid

    def front(self) -> WindowInfo:
        return self.window

    def self_pid(self) -> int:
        return self._pid


class FakeImeProbe:
    def __init__(self, snapshot: Optional[ImeSnapshot] = None) -> None:
        self.snapshot = snapshot or ImeSnapshot(
            available=True, open=True, native=True, fullshape=False, composing=False
        )

    def state(self, hwnd: int) -> ImeSnapshot:
        return self.snapshot


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: List[Tuple[str, Optional[object]]] = []

    def show(self, action: object) -> None:
        self.events.append(("show", action))

    def hide(self) -> None:
        self.events.append(("hide", None))

    @property
    def shown(self) -> int:
        return sum(1 for k, _ in self.events if k == "show")

    @property
    def hidden(self) -> int:
        return sum(1 for k, _ in self.events if k == "hide")


# ------------------------------------------------------------------ 构造按键


def key(vk: int, char: Optional[str] = None, *,
        shift: bool = False, ctrl: bool = False, alt: bool = False,
        win: bool = False, is_down: bool = True) -> KeyPress:
    return KeyPress(
        vk=vk,
        scan_code=0,
        is_down=is_down,
        char=char,
        modifiers=Modifiers(shift=shift, ctrl=ctrl, alt=alt, win=win),
    )


# 常用 VK（与 core.events 中的常量保持一致）
VK_A = 0x41
VK_F = 0x46
VK_I = 0x49
VK_COMMA = 0xBC
VK_PERIOD = 0xBE
VK_SLASH = 0xBF
VK_OEM_1 = 0xBA        # ; :
VK_BACKSLASH = 0xDC
VK_SPACE = 0x20
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_ESCAPE = 0x1B
