"""真机自检：钩子掉了 / 修饰键卡住了，能不能自己救回来。

单元测试（``tests/test_hook_health.py``）证的是"逻辑对不对"，用假 user32、
假钩子跑。这个脚本走**真钩子 + 真消息线程 + 真 SendInput**，证明三件事：

    ① 探活：注入一个无害键，真回调认不认领（认领 = 钩子还在被系统调用）；
    ② 卡键复位：人为把 Ctrl 位设成"卡在按下"，再投一个真 ``;`` 按键 ——
       业务层拿到的必须是 ``;`` 而不是 None（用户报障就是这里出的问题）；
    ③ 卡键复位之后，真主板确实把该按键当标点处理了（吞掉 + 补半角）。

⚠ 全程**不会往任何程序里打字**：②③ 走的是"直接调回调"（伪造 KBDLLHOOKSTRUCT，
   事件不会进系统队列），③ 用的注入器是记录器。只有 ① 会真的注入一个 0xE8 ——
   那是 Windows 未分配的虚拟键码，无字符、无默认处理（见 README 6.17）。

用法：
    python tools/hook_health_check.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codepoint.core.config import build_default_settings            # noqa: E402
from codepoint.core.events import ImeSnapshot, WindowInfo           # noqa: E402
from codepoint.core.models import AppItem, PunctuationRule          # noqa: E402
from codepoint.platform_win import keyboard_hook as kh              # noqa: E402
from codepoint.platform_win.message_loop import MessageWindow       # noqa: E402
from codepoint.platform_win.keyboard_hook import KeyboardHook       # noqa: E402
from codepoint.platform_win.winapi import (                         # noqa: E402
    KBDLLHOOKSTRUCT,
    WM_KEYDOWN,
    WM_KEYUP,
)
from codepoint.services.pipeline import KeyboardPipeline            # noqa: E402

VK_OEM_1 = 0xBA          # ; :
VK_LCONTROL = 0xA2

PASS = "  [OK]  "
FAIL = "  [!!]  "
_results: list = []
_KEEP: list = []


def check(label: str, got, want) -> None:
    ok = got == want
    print((PASS if ok else FAIL) + f"{label}\n              期望 {want!r}\n              实际 {got!r}")
    _results.append(ok)


def _lp(vk: int, flags: int = 0) -> int:
    """伪造一个 KBDLLHOOKSTRUCT 指针（等价于系统传给钩子的 lParam）。"""
    info = KBDLLHOOKSTRUCT()
    info.vkCode = vk
    info.scanCode = 0
    info.flags = flags
    info.time = 0
    info.dwExtraInfo = 0
    _KEEP.append(info)          # ⚠ 不保住引用，回调 deref 到的是垃圾
    return ctypes.cast(ctypes.byref(info), ctypes.c_void_p).value


class _RecordingInjector:
    """记录注入动作 —— 特意不真打字（本脚本不该往你正在用的窗口里写字）。"""

    def __init__(self) -> None:
        self.texts: list = []
        self.guards = 0

    def send_text(self, text: str) -> None:
        self.texts.append(text)

    def send_backspaces(self, count: int) -> None:
        pass

    def send_ime_guard(self) -> None:
        self.guards += 1


class _Window:
    def front(self) -> WindowInfo:
        return WindowInfo(hwnd=1, pid=2, process_name="hook-health-check.exe")

    def self_pid(self) -> int:
        return -1


class _Ime:
    def state(self, _hwnd: int) -> ImeSnapshot:
        # 输入法开着且是中文标点态 → 主板应该下手替换
        return ImeSnapshot(available=True, open=True, native=True)


class _Notifier:
    def show(self, action) -> None:
        pass

    def hide(self) -> None:
        pass


class _FakeUser32:
    """状态对账用的替身：指定"物理上按着哪些键"。"""

    def __init__(self, down=()) -> None:
        self.down = set(down)
        self.orig = kh.user32

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.down else 0

    def CallNextHookEx(self, *_a):
        return 0

    def __enter__(self):
        kh.user32 = self
        return self

    def __exit__(self, *_exc):
        kh.user32 = self.orig
        return False


def main() -> int:
    print("== 码点助手 · 钩子自愈真机自检 ==\n")

    settings = build_default_settings()
    settings.app_items = [AppItem("hook-health-check.exe")]
    settings.punctuation_rules = [PunctuationRule("；", ";", True)]
    injector = _RecordingInjector()
    seen: list = []

    pipeline = KeyboardPipeline(
        window_probe=_Window(), ime_probe=_Ime(),
        injector=injector, notifier=_Notifier(),
    )
    pipeline.apply_settings(settings)

    def decide(event):
        seen.append(event)
        return pipeline.handle(event)

    mw = MessageWindow()
    if not mw.start():
        print("消息线程启动失败:", mw.start_error)
        return 1
    hook = KeyboardHook(mw, decide)
    if not hook.install():
        print("钩子安装失败:", hook.last_error)
        return 1
    print(f"钩子已安装（事件计数起点 {hook.events_seen}）\n")

    def fire(vk: int, down: bool = True) -> None:
        mw.call(lambda: hook._proc(0, WM_KEYDOWN if down else WM_KEYUP, _lp(vk)),
                timeout=2.0)

    try:
        # ① 探活：真注入 + 真回调
        ok = hook.probe()
        check("① 探活（注入无害键，回调认领）", ok, True)

        # ② 卡住的 Ctrl 必须在解析字符之前被清掉
        mw.call(lambda: hook._update_state(VK_LCONTROL, True), timeout=2.0)
        check("   前置：Ctrl 位确实卡住了",
              True, hook._snapshot_modifiers().ctrl)
        with _FakeUser32(set()):           # 物理上没按任何修饰键
            seen.clear()
            fire(VK_OEM_1)
        event = seen[-1] if seen else None
        check("② 业务层拿到的字符（卡键复位后）",
              getattr(event, "char", None), ";")
        check("   业务层拿到的修饰键是干净的",
              bool(getattr(event, "modifiers", None) and
                   not event.modifiers.has_command), True)

        # ③ 真主板随后确实把它当标点处理了
        check("③ 主板吞掉该键（swallow）", pipeline.stats.keys_swallowed >= 1, True)
        check("   主板补出半角字符", injector.texts[-1:] == [";"], True)

        # ④ 控制组：真按着 Ctrl 时绝不能下手（那是快捷键）
        hook.reset_state()
        mw.call(lambda: hook._update_state(VK_LCONTROL, True), timeout=2.0)
        with _FakeUser32({VK_LCONTROL}):    # 物理上确实按着
            seen.clear()
            fire(VK_OEM_1)
        event = seen[-1] if seen else None
        check("④ 真按着 Ctrl 时不下手（char 为 None）",
              getattr(event, "char", None), None)
        check("   真按着 Ctrl 时主板也放行",
              getattr(event, "modifiers", None) is not None
              and event.modifiers.has_command, True)

        # ⑤ 亲手把钩子摘掉（模拟 Windows 静默移除：句柄还在，回调不再来）
        #    —— 探活必须能发现，否则整个看护机制就是摆设。
        handle = hook._hook
        kh.user32.UnhookWindowsHookEx(handle)
        check("   前置：钩子句柄看着还在（正是「静默摘掉」的样子）",
              hook.installed, True)
        check("⑤ 钩子被摘掉后，探活必须失败", hook.probe(), False)

        # ⑥ 重挂之后必须恢复
        check("⑥ 重挂成功", hook.reinstall(), True)
        check("   重挂后探活恢复", hook.probe(), True)
        check("   重挂计数已记录", hook.reinstalls >= 1, True)
    finally:
        hook.uninstall()
        mw.stop()

    print()
    if all(_results):
        print(f"== 全部通过（{len(_results)} 项）==")
        return 0
    print(f"== 有 {_results.count(False)} 项没过 ==")
    return 1


if __name__ == "__main__":
    time.sleep(0)          # 留个断点位置，方便调试
    raise SystemExit(main())
