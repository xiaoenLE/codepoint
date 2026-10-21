"""运行时诊断：在**真实消息线程**里驱动真实钩子回调。

    python tools/diag_runtime.py

**为什么需要它**：单元测试和 live_check 都用"假钩子"直接调 pipeline，
能证明决策对，但证明不了"真钩子回调跑起来不会卡"。而"功能全失效 +
退出卡顿"这类故障的根因恰好只在真实回调里出现（回调跑在消息线程上，
一旦卡住，钩子、托盘、热键、剪贴板全跟着完蛋，退出时 join 也会超时）。

做法：手工构造 KBDLLHOOKSTRUCT（flags=0，即"不是注入的"，等价于真人按键），
再用 ``MessageWindow.call`` 把它投到消息线程上执行 —— 和真实回调**同一环境**。

⚠ 运行前先退出正在运行的码点助手实例。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk                                                   # noqa: E402

from codepoint.core.config import build_default_settings               # noqa: E402
from codepoint.core.events import ImeSnapshot, WindowInfo              # noqa: E402
from codepoint.core.models import AppItem                              # noqa: E402
from codepoint.platform_win import input_simulator as sim              # noqa: E402
from codepoint.platform_win.keyboard_hook import KeyboardHook          # noqa: E402
from codepoint.platform_win.message_loop import (                      # noqa: E402
    MessageWindow, find_peer_window,
)
from codepoint.platform_win.winapi import (                            # noqa: E402
    GA_ROOT, HWND, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP, user32,
)
from codepoint.services.pipeline import KeyboardPipeline               # noqa: E402

VK_LSHIFT = 0xA0
VK_OEM_1 = 0xBA        # ; :
VK_OEM_COMMA = 0xBC    # , <
VK_OEM_PERIOD = 0xBE   # . >
VK_F, VK_O, VK_R = 0x46, 0x4F, 0x52
VK_TAB = 0x09

CALL_TIMEOUT = 3.0


def scan_of(vk: int) -> int:
    return int(user32.MapVirtualKeyW(vk, 0) or 0)


class CallTimeout(Exception):
    pass


def main() -> int:
    peer = find_peer_window()
    if peer:
        print(f"检测到另一个实例（hwnd=0x{peer:X}），请先从托盘退出再运行。")
        return 2

    # ---------------------------------------------------------------- 靶窗口
    root = tk.Tk()
    root.title("codepoint diag target")
    root.geometry("560x240+200+200")
    root.attributes("-topmost", True)
    text = tk.Text(root, font=("Consolas", 12))
    text.pack(fill="both", expand=True)

    def settle(seconds: float = 0.45) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            text.focus_set()
            time.sleep(0.01)

    def refocus() -> None:
        root.lift()
        root.focus_force()
        text.focus_set()

    settle(0.6)
    refocus()
    settle(0.3)

    hwnd_target = int(user32.GetAncestor(HWND(text.winfo_id()), GA_ROOT) or 0)

    class _Win:
        def front(self):
            return WindowInfo(hwnd=hwnd_target, pid=os.getpid(),
                              process_name="diag-target.exe",
                              window_title="codepoint diag target")
        def self_pid(self):
            return -1

    class _Ime:
        def state(self, _hwnd):
            return ImeSnapshot(available=True, open=True, native=True,
                               composing=False)

    class _Inject:
        def send_text(self, t):
            sim.send_unicode_text(t)
        def send_backspaces(self, n):
            sim.send_backspaces(n)
        def send_ime_guard(self):
            sim.send_ime_guard()

    class _Notifier:
        def show(self, action):
            print(f"       [呼吸灯] 显示 {action}")
        def hide(self):
            pass

    settings = build_default_settings()
    settings.app_items = [AppItem("diag-target.exe")]
    pipeline = KeyboardPipeline(window_probe=_Win(), ime_probe=_Ime(),
                                injector=_Inject(), notifier=_Notifier())
    pipeline.apply_settings(settings)

    # ---------------------------------------------------------------- 钩子
    mw = MessageWindow()
    if not mw.start():
        print("消息线程启动失败:", mw.start_error)
        return 1
    hook = KeyboardHook(mw, pipeline.handle)
    if not hook.install():
        print("钩子安装失败:", hook.last_error)
        return 1
    print("钩子已安装\n")

    def fire(vk: int, down: bool, shift_down: bool = False) -> float:
        """在消息线程里投递一个"真人按键"事件，返回耗时（毫秒）。"""
        info = KBDLLHOOKSTRUCT()
        info.vkCode = vk
        info.scanCode = scan_of(vk)
        info.flags = 0                    # 0 = 物理按键，不是注入的
        info.time = 0
        info.dwExtraInfo = 0
        ptr = ctypes.cast(ctypes.byref(info), ctypes.c_void_p).value
        msg = WM_KEYDOWN if down else WM_KEYUP

        def job():
            t0 = time.perf_counter()
            hook._proc(0, msg, ptr)
            return (time.perf_counter() - t0) * 1000

        return mw.call(job, timeout=CALL_TIMEOUT)

    def tap(vk: int, shift: bool = False, label: str = "") -> None:
        name = label or f"VK_{vk:02X}"
        try:
            if shift:
                fire(VK_LSHIFT, True)
            dt = fire(vk, True)
            fire(vk, False)
            if shift:
                fire(VK_LSHIFT, False)
            print(f"  {name:14} 回调耗时 {dt:7.2f} ms")
        except Exception as exc:                      # noqa: BLE001
            print(f"  {name:14} ✗ 卡住/异常：{type(exc).__name__}: {exc}")
            raise CallTimeout(name)

    def content() -> str:
        return text.get("1.0", "end-1c")

    def reset(value: str = "") -> None:
        text.delete("1.0", "end")
        if value:
            text.insert("1.0", value)
        settle(0.25)
        refocus()

    failures = 0

    try:
        print("== 1) 单次物理按键（不触发替换的普通键） ==")
        reset()
        tap(VK_F, label="f")
        settle()
        print(f"     靶窗口内容 {content()!r}\n")

        print("== 2) 标点键 ','（应被吞掉 → 注入半角 ','） ==")
        reset()
        tap(VK_OEM_COMMA, label=",")
        settle()
        got = content()
        ok = got == ","
        failures += 0 if ok else 1
        print(f"     靶窗口内容 {got!r}   {'✓' if ok else '✗ 期望 ,'}\n")

        print("== 3) Shift+,（应解析成 '<' 并放行） ==")
        reset()
        # 3a 纯流式：只投递"Shift 按下"事件，物理键盘上并没有真的按 Shift。
        #     这正好检验"物理状态只能补漏、不能覆盖流式"这条规则 ——
        #     钩子回调滞后于物理按键时就是这种情形。
        tap(VK_OEM_COMMA, shift=True, label="Shift+, (流式)")
        settle()
        got = content()
        ok = got == "<"
        failures += 0 if ok else 1
        print(f"     靶窗口内容 {got!r}   {'✓' if ok else '✗ 期望 <'}")
        # 3b 真物理 Shift：真的把 Shift 按下去（SendInput），物理状态也生效
        reset()
        sim.send_vk(VK_LSHIFT, True)
        try:
            tap(VK_OEM_COMMA, label="Shift+, (物理)")
        finally:
            sim.send_vk(VK_LSHIFT, False)
        settle()
        got = content()
        ok = got == "<"
        failures += 0 if ok else 1
        print(f"     靶窗口内容 {got!r}   {'✓' if ok else '✗ 期望 <'}\n")

        print("== 4) Shift+;（应解析成 ':' 并放行） ==")
        reset()
        tap(VK_OEM_1, shift=True, label="Shift+; (流式)")
        settle()
        got = content()
        ok = got == ":"
        failures += 0 if ok else 1
        print(f"     靶窗口内容 {got!r}   {'✓' if ok else '✗ 期望 :'}")

        print("== 5) 整词快捷输入 for + Tab ==")
        reset()
        tap(VK_F, label="f")
        tap(VK_O, label="o")
        tap(VK_R, label="r")
        tap(VK_TAB, label="Tab")
        settle(0.8)
        got = content()
        ok = got.startswith("FOR") and "END_FOR;" in got
        failures += 0 if ok else 1
        print(f"     靶窗口内容 {got[:40]!r}   {'✓' if ok else '✗ 期望 FOR 骨架'}\n")

        print("== 6) 连续 20 次按键（看会不会越跑越慢 / 卡死） ==")
        reset()
        t0 = time.perf_counter()
        worst = 0.0
        for _ in range(20):
            dt = fire(VK_OEM_PERIOD, True)
            fire(VK_OEM_PERIOD, False)
            worst = max(worst, dt)
        total = (time.perf_counter() - t0) * 1000
        print(f"     20 次共 {total:.0f} ms，单次最慢 {worst:.2f} ms")
        if worst > 200:
            print("     ✗ 单次回调超过 200ms —— 系统会摘掉钩子（LowLevelHooksTimeout）")
            failures += 1
        print()

        print("== 7) 退出耗时（真实 shutdown 顺序） ==")
        t0 = time.perf_counter()
        hook.uninstall()
        t_hook = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        mw.stop()
        t_mw = (time.perf_counter() - t0) * 1000
        print(f"     hook.uninstall {t_hook:.0f} ms    message_window.stop {t_mw:.0f} ms")
        if t_mw > 800:
            print("     ✗ 消息线程停止太慢")
            failures += 1
    except CallTimeout as exc:
        print(f"\n!! 在 {exc} 处卡住 —— 消息线程已不可恢复，后续检查跳过")
        failures += 1
    finally:
        try:
            hook.uninstall()
        except Exception:                             # noqa: BLE001
            pass
        try:
            mw.stop()
        except Exception:                             # noqa: BLE001
            pass
        try:
            root.destroy()
        except Exception:                             # noqa: BLE001
            pass

    print(f"\n== 诊断结束：{'有问题' if failures else '全部通过'} ==")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
