"""输入法守卫键探针 —— 用**真输入法**验证「吞掉 Shift+符号 会切中英文」。

    python tools/ime_shift_probe.py

**为什么需要它**：标点替换必须**吞掉** Shift+符号 那个键（`;` → `:`），
可中文输入法（微软拼音默认）恰恰用「Shift 按下与抬起之间有没有别的键」
来区分"单击 Shift 切中英文"和"Shift 组合键"。键被我们吞了，输入法看到的
就只剩一个孤零零的 Shift —— 于是每敲一个 `:` `<` `>` `?` `"` 中英文就被切走。

这是单元测试**永远测不到**的东西：它发生在输入法内部，只有真输入法 + 真钩子
跑一遍才看得见。用户实测报障原话：

    "当 shift 用来切换中英文时，每次中文输入其他需要 shift 加其他按键时，
     他会触发切换语言。"

本工具跑两段：

  ① **机制验证**（纯合成按键）：证明根因、以及"补一个无名键"确实能骗过输入法；
  ② **代码级 A/B**（真钩子 + 真主板 + 真输入法）：走一遍真实的吞键链路，
     对比「有守卫键 / 没有守卫键」两种情况下输入法有没有被切走。

⚠ 运行前先退出正在运行的码点助手实例（两个钩子会互相干扰）。
⚠ 需要系统里有开启"Shift 键切换中英文"的中文输入法；没有的话第 ①② 段的
   "该切换"那一项会显示"没切换"，属正常（说明这个 bug 根本不会在你机器上发生）。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import tkinter as tk
from ctypes import byref, wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codepoint.core.config import build_default_settings               # noqa: E402
from codepoint.core.events import WindowInfo                           # noqa: E402
from codepoint.core.models import AppItem                              # noqa: E402
from codepoint.platform_win import input_simulator as sim              # noqa: E402
from codepoint.platform_win.keyboard_hook import KeyboardHook          # noqa: E402
from codepoint.platform_win.message_loop import (                      # noqa: E402
    MessageWindow, find_peer_window,
)
from codepoint.platform_win.winapi import (                            # noqa: E402
    GA_ROOT, HWND, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP, imm32, user32,
)
from codepoint.services.pipeline import KeyboardPipeline               # noqa: E402

VK_LSHIFT = 0xA0
VK_OEM_1 = 0xBA          # ; : —— 就是"Shift+; 打冒号"那对
DWORD = wintypes.DWORD

PASS = "  [OK]  "
FAIL = "  [!!]  "

_results: list = []


def check(label: str, got, want) -> None:
    ok = got == want
    print((PASS if ok else FAIL) + f"{label}\n              期望 {want}  实际 {got}")
    _results.append(ok)


# ------------------------------------------------------------------ 输入法状态

def ime_state(hwnd: int):
    """(输入法打开, 中文模式, 中文标点, 全角)；问不到返回 None。"""
    if not hwnd:
        return None
    himc = imm32.ImmGetContext(HWND(hwnd))
    if not himc:
        return None
    try:
        conv = DWORD(0)
        sent = DWORD(0)
        ok = imm32.ImmGetConversionStatus(himc, byref(conv), byref(sent))
        v = conv.value if ok else 0
        return (bool(imm32.ImmGetOpenStatus(himc)),
                bool(v & 0x0001),        # IME_CMODE_NATIVE —— 中/英
                bool(v & 0x0040),        # IME_CMODE_SYMBOL —— 中文标点
                bool(v & 0x0008))        # IME_CMODE_FULLSHAPE
    finally:
        imm32.ImmReleaseContext(HWND(hwnd), himc)


def restore_ime(hwnd: int, snap) -> None:
    """把输入法状态复原（探针会把中英切来切去，别把用户的输入法留在英文）。"""
    if snap is None or not hwnd:
        return
    himc = imm32.ImmGetContext(HWND(hwnd))
    if not himc:
        return
    try:
        is_open, native, symbol, fullshape = snap
        v = (0x0001 if native else 0) | (0x0040 if symbol else 0) | (0x0008 if fullshape else 0)
        imm32.ImmSetConversionStatus(himc, DWORD(v), DWORD(0))
        imm32.ImmSetOpenStatus(himc, wintypes.BOOL(1 if is_open else 0))
    finally:
        imm32.ImmReleaseContext(HWND(hwnd), himc)


def main() -> int:
    peer = find_peer_window()
    if peer:
        print(f"检测到另一个码点助手实例正在运行（hwnd=0x{peer:X}），")
        print("它的键盘钩子和本探针会互相干扰。请先从托盘退出那个实例。")
        return 2

    # ---------------------------------------------------------------- 靶窗口
    # 必须用**自己进程**里的窗口：跨进程窗口 ImmGetContext 会返回 NULL，
    # 就拿不到输入法状态了（记事本等 WinUI3 程序尤其如此）。
    root = tk.Tk()
    root.title("码点助手 输入法探针")
    root.geometry("420x140+160+160")
    root.attributes("-topmost", True)
    entry = tk.Entry(root, font=("Consolas", 12))
    entry.pack(fill="both", expand=True, padx=8, pady=8)

    def settle(seconds: float = 0.30) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            entry.focus_set()
            time.sleep(0.005)

    def refocus() -> None:
        root.lift()
        root.focus_force()
        entry.focus_set()

    settle(0.5)
    refocus()
    settle(0.3)

    hwnd = int(user32.GetAncestor(HWND(entry.winfo_id()), GA_ROOT) or 0)
    baseline = ime_state(hwnd)
    print("=" * 64)
    print(f"靶窗口 hwnd=0x{hwnd:X}   前台={'是' if int(user32.GetForegroundWindow()) == hwnd else '否'}")
    print(f"输入法状态 (打开, 中文模式, 中文标点, 全角) = {baseline}")
    if baseline is None:
        print("拿不到输入法状态（ImmGetContext 失败），无法验证。")
        root.destroy()
        return 1
    print("=" * 64)

    def content() -> str:
        return entry.get()

    def reset() -> None:
        entry.delete(0, "end")
        settle(0.15)
        refocus()
        settle(0.10)

    # ================================================================ ① 机制验证
    print("\n== ① 机制验证（纯合成按键：只为看清输入法的判断规则）==")

    def shift_round(middle, name: str) -> bool:
        """Shift 按下 → middle() → Shift 抬起，返回"中英文是否被切走"。"""
        reset()
        before = ime_state(hwnd)
        sim.send_vk(VK_LSHIFT, True)
        settle(0.08)
        if middle is not None:
            middle()
            settle(0.08)
        sim.send_vk(VK_LSHIFT, False)
        settle(0.35)
        after = ime_state(hwnd)
        switched = before != after
        print(f"     {name:<28} {'★ 切换了' if switched else '没切换'}")
        if switched:
            restore_ime(hwnd, baseline)
            settle(0.15)
        return switched

    def no_key():
        return None

    def real_semicolon():
        sim.send_vk(VK_OEM_1, True)
        sim.send_vk(VK_OEM_1, False)

    def inject_char_only():
        sim.send_unicode_char(":")                    # 现状：只补替换字符

    def inject_guard_then_char():
        sim.send_ime_guard()                          # 修法：先补守卫键
        sim.send_unicode_char(":")

    has_toggle = shift_round(no_key, "Shift 单击")
    if not has_toggle:
        print("\n     ⚠ 这台机器的中文输入法没有开「Shift 键切换中英文」——")
        print("       也就是说这个 bug 根本不会在你机器上发生，后续检查跳过。")
        restore_ime(hwnd, baseline)
        root.destroy()
        return 2

    check("Shift 单击 → 切中英文（输入法的正常姿势，说明功能开着）",
          has_toggle, True)
    check("Shift + 真实 ; → 不切（输入法看到了「别的键」）",
          shift_round(real_semicolon, "Shift + ;（真实键）"), False)
    check("吞掉 ; 后只注入 : → 切中英文（＝修之前的程序行为）",
          shift_round(inject_char_only, "只注入 :（复现 bug）"), True)
    check(f"吞掉 ; 后先注入守卫键 0x{sim.VK_IME_GUARD:02X} → 不切（修法）",
          shift_round(inject_guard_then_char,
                      f"守卫键 0x{sim.VK_IME_GUARD:02X} + :"), False)

    # ================================================================ ② 代码级 A/B
    print("\n== ② 代码级 A/B（真钩子 + 真主板 + 真输入法，走真实的 `;` 吞键链路）==")
    failures = run_code_path(root, entry, hwnd, baseline, settle, refocus)

    restore_ime(hwnd, baseline)
    settle(0.15)
    print(f"\n输入法已复原：{ime_state(hwnd)}（初始 {baseline}）")
    root.destroy()

    total, ok = len(_results), sum(1 for r in _results if r)
    bad = failures + (total - ok)
    print(f"\n== {ok}/{total} 项通过" + (f"，另有 {failures} 项未通过 ==" if failures else " =="))
    return 1 if bad else 0


def run_code_path(root, entry, hwnd, baseline, settle, refocus) -> int:
    """把真实主板挂到真实钩子上，手工喂 4 个"真人按键"，对比有/无守卫键。

    为什么手工构造 KBDLLHOOKSTRUCT：钩子会忽略**注入的**按键（flags 带
    LLKHF_INJECTED），用 SendInput 是驱动不了它的。flags=0 等价于真人按键，
    和 tools/diag_runtime.py 是同一套手法。
    """
    failures = 0

    class _Win:
        def front(self):
            return WindowInfo(hwnd=hwnd, pid=os.getpid(),
                              process_name="ime-probe.exe",
                              window_title="码点助手 输入法探针")
        def self_pid(self):
            return -1          # 允许"自己打自己"，这样才能拿本窗口当靶子

    class _Inject:
        def __init__(self) -> None:
            self.guard_enabled = True
            self.guards = 0
        def send_text(self, t):
            sim.send_unicode_text(t)
        def send_backspaces(self, n):
            sim.send_backspaces(n)
        def send_ime_guard(self):
            self.guards += 1
            if self.guard_enabled:
                sim.send_ime_guard()

    from codepoint.platform_win import ime as ime_mod

    class _Ime:
        def state(self, hwnd_):
            return ime_mod.query(hwnd_)        # 真输入法状态，不是假的

    class _Notifier:
        def show(self, action):
            pass
        def hide(self):
            pass

    injector = _Inject()
    settings = build_default_settings()
    settings.app_items = [AppItem("ime-probe.exe")]
    pipeline = KeyboardPipeline(window_probe=_Win(), ime_probe=_Ime(),
                                injector=injector, notifier=_Notifier())
    pipeline.apply_settings(settings)

    mw = MessageWindow()
    if not mw.start():
        print("     消息线程启动失败:", mw.start_error)
        return 1
    hook = KeyboardHook(mw, pipeline.handle)
    if not hook.install():
        print("     钩子安装失败:", hook.last_error)
        mw.stop()
        return 1

    def scan_of(vk: int) -> int:
        return int(user32.MapVirtualKeyW(vk, 0) or 0)

    def fire(vk: int, down: bool) -> None:
        info = KBDLLHOOKSTRUCT()
        info.vkCode = vk
        info.scanCode = scan_of(vk)
        info.flags = 0                 # 0 = 物理按键，不是注入的
        info.time = 0
        info.dwExtraInfo = 0
        ptr = ctypes.cast(ctypes.byref(info), ctypes.c_void_p).value
        msg = WM_KEYDOWN if down else WM_KEYUP
        mw.call(lambda: hook._proc(0, msg, ptr), timeout=3.0)

    def type_shift_semicolon() -> None:
        """真人按法：物理按下 Shift → 敲 `;` → 松 Shift。

        Shift 必须走 **SendInput 真按下去**（这样输入法才看得到），
        而 `;` 走钩子喂键（等价真人按键，钩子会吞掉它并注入替换字符）。
        如果 Shift 也走喂键，输入法压根看不到 Shift，就复现不出来了。
        """
        sim.send_vk(VK_LSHIFT, True)
        settle(0.10)
        fire(VK_OEM_1, True)          # 钩子：吞掉 + 注入守卫键/替换字符
        fire(VK_OEM_1, False)
        settle(0.10)
        sim.send_vk(VK_LSHIFT, False)   # 物理抬起 → 输入法在这里判断"是不是单击"
        settle(0.30)

    def scenario(name: str, guard: bool) -> bool:
        """返回"中英文是否被切走"。"""
        injector.guard_enabled = guard
        entry.delete(0, "end")
        settle(0.15)
        refocus()
        settle(0.10)
        before = ime_state(hwnd)
        try:
            type_shift_semicolon()
        except Exception as exc:                       # noqa: BLE001
            print(f"     {name}: 喂键失败 {type(exc).__name__}: {exc}")
        settle(0.45)
        after = ime_state(hwnd)
        switched = before != after
        print(f"     {name:<26} 文本={entry.get()!r:6} 守卫键×{injector.guards} "
              f"{'★ 中英文被切走' if switched else '中英文没动'}")
        if switched:
            restore_ime(hwnd, baseline)
            settle(0.15)
        return switched

    before_fix = scenario("不补守卫键（修之前）", guard=False)
    after_fix = scenario("补守卫键（修之后）", guard=True)
    if before_fix:
        print(PASS + "复现成功：没有守卫键时，打一个冒号就把中英文切走了")
    else:
        print(FAIL + "没能复现（这台机器上输入法没被切走）—— 结论仅供参考")
        failures += 1
    if after_fix:
        print(FAIL + "仍然被切走：守卫键没起作用")
        failures += 1
    elif entry.get() != ":":
        print(FAIL + f"没被切走，但打出来的不对：期望 ':'，实际 {entry.get()!r}")
        failures += 1
    else:
        print(PASS + "修之后：打出 ':'，且中英文没被切走")

    hook.uninstall()
    mw.stop()
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
