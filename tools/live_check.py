"""真实按键注入自检（需要桌面会话，不放进单元测试套件）。

单元测试里的 Injector 是假的——它能证明"决策对"，但证明不了
"SendInput 真的把字符送到了目标窗口"。这个脚本用真实的 tkinter
文本框当靶子，把注入链路走一遍。

⚠ 关键：tkinter 只有在自己跑消息循环时才会处理注入进来的按键。
   所以这里所有等待都用 ``settle()``（反复 update + 小睡），
   而不是 ``time.sleep`` —— 否则读到的文本是滞后的，结论全是错的。

用法：
    python tools/live_check.py

⚠ 运行前先退出正在运行的码点助手实例（托盘右键退出）——
   两个键盘钩子会互相干扰，测出来的结果不可信（脚本会自己检测并拒绝运行）。
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codepoint.platform_win import input_simulator as sim          # noqa: E402

PASS = "  [OK]  "
FAIL = "  [!!]  "

_results: list = []


def check(label: str, got, want) -> None:
    ok = got == want
    print((PASS if ok else FAIL) + f"{label}\n              期望 {want!r}\n              实际 {got!r}")
    _results.append(ok)


def make_window():
    root = tk.Tk()
    root.title("码点助手 live check")
    root.geometry("520x220+240+240")
    text = tk.Text(root, font=("Consolas", 12))
    text.pack(fill="both", expand=True)
    return root, text


def settle(root, seconds: float = 0.6, refocus=None) -> None:
    """反复泵消息，直到注入的事件全部被处理完。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if refocus is not None:
            refocus()
        root.update()
        time.sleep(0.01)


def main() -> int:
    # ⚠ 同类工具的键盘钩子会互相干扰：两个低级钩子同时看着同一批注入按键，
    #   结果是字符被吞/重放，验证结论全是错的。曾经因此 16 项掉到 11 项，
    #   查了半天代码，最后发现是用户正开着这个程序。
    from codepoint.platform_win.message_loop import find_peer_window

    peer = find_peer_window()
    if peer:
        print(f"检测到另一个码点助手实例正在运行（hwnd=0x{peer:X}）。")
        print("它的键盘钩子和本验证会互相干扰，结果不可信。")
        print("请先从托盘退出那个实例，再运行本脚本。")
        return 2                                   # 2 = 跳过（环境不允许）

    root, text = make_window()

    def refocus() -> None:
        root.focus_force()
        text.focus_set()

    settle(root, 0.6, refocus)

    def reset(value: str = "") -> None:
        text.delete("1.0", "end")
        if value:
            text.insert("1.0", value)
        settle(root, 0.3, refocus)

    def content() -> str:
        return text.get("1.0", "end-1c")

    # 1) 纯 ASCII 注入
    reset()
    sim.send_unicode_text("hello,world")
    settle(root, refocus=refocus)
    check("ASCII 注入", content(), "hello,world")

    # 2) 中文 + 全角标点注入（证明 KEYEVENTF_UNICODE 绕过输入法）
    reset()
    sim.send_unicode_text("变量，测试。")
    settle(root, refocus=refocus)
    check("中文/全角注入", content(), "变量，测试。")

    # 3) 多行内容（换行必须走 VK_RETURN 才被文本框接受）
    reset()
    sim.send_unicode_text("IF x THEN\n    y := 1;\nEND_IF;")
    settle(root, refocus=refocus)
    check("多行注入", content(), "IF x THEN\n    y := 1;\nEND_IF;")

    # 4) 短串退格（验证"每个键单独一次 SendInput，不会被合并"）
    reset("abcdef")
    sim.send_backspaces(3)
    settle(root, refocus=refocus)
    check("退格 3 次", content(), "abc")

    # 5) 长串退格
    reset("0123456789")
    sim.send_backspaces(8)
    settle(root, refocus=refocus)
    check("退格 8 次", content(), "01")

    # 6) replace_typed：吃掉 N 个字符再写入内容（快捷补全的核心动作）
    reset("if")
    sim.replace_typed(2, "IF  THEN\nEND_IF;")
    settle(root, refocus=refocus)
    check("replace_typed 吃掉 2 字符", content(), "IF  THEN\nEND_IF;")

    # 7) replace_typed 带前缀（模拟前面已有正确文字，只替换尾部）
    reset("x := if")
    sim.replace_typed(2, "IF y THEN\nEND_IF;")
    settle(root, refocus=refocus)
    check("replace_typed 保留前缀", content(), "x := IF y THEN\nEND_IF;")

    # ================================================================
    # 8~9) 完整业务链路：KeyboardPipeline（真实注入，假钩子/假窗口探针）
    #      这是"单元测试 + 真实窗口"之间的最后一块拼图。
    # ================================================================
    run_pipeline_checks(root, text, reset, content, refocus)

    root.destroy()
    total, ok = len(_results), sum(1 for r in _results if r)
    print(f"\n== {ok}/{total} 项通过 ==")
    return 0 if ok == total else 1


def run_pipeline_checks(root, text, reset, content, refocus) -> None:
    import os

    from codepoint.core.config import build_default_settings
    from codepoint.core.events import ImeSnapshot, WindowInfo
    from codepoint.core.models import AppItem
    from codepoint.platform_win.winapi import GA_ROOT, HWND, user32
    from codepoint.services.pipeline import KeyboardPipeline

    from tests.fakes import key, VK_OEM_1, VK_I, VK_F, VK_TAB   # type: ignore

    hwnd = int(user32.GetAncestor(HWND(text.winfo_id()), GA_ROOT) or 0)

    class _Win:
        def front(self):
            return WindowInfo(hwnd=hwnd, pid=os.getpid(),
                              process_name="live-check.exe",
                              window_title="码点助手 live check")
        def self_pid(self):
            return -1        # 允许"自己打自己"，方便用同一个窗口当靶子

    class _Ime:
        def state(self, _hwnd):
            # 假装输入法处于中文模式、且没有在组字
            return ImeSnapshot(available=True, open=True, native=True, composing=False)

    class _Inject:
        def send_text(self, t):
            sim.send_unicode_text(t)
        def send_backspaces(self, n):
            sim.send_backspaces(n)
        def send_ime_guard(self):
            sim.send_ime_guard()

    class _Notifier:
        def __init__(self):
            self.shown = 0
            self.hidden = 0
        def show(self, action):
            self.shown += 1
        def hide(self):
            self.hidden += 1

    settings = build_default_settings()
    settings.app_items = [AppItem("live-check.exe")]
    notifier = _Notifier()
    pipeline = KeyboardPipeline(
        window_probe=_Win(), ime_probe=_Ime(),
        injector=_Inject(), notifier=notifier,
    )
    pipeline.apply_settings(settings)

    # 8) 标点替换：键入 ',' 应被吞掉并重新注入半角 ','（结果一致，但计数应 +1）
    reset("a")
    swallowed = pipeline.handle(key(0xBC, ","))     # VK_OEM_COMMA
    settle(root, refocus=refocus)
    check("标点拦截（吞键）", swallowed, True)
    check("标点替换后的文本", content(), "a,")
    check("标点替换计数", pipeline.stats.punctuation_replaced, 1)

    # 9) 单字符快捷输入：':' → ':='（默认规则里 ':' 也在标点表中，应直接写入）
    reset()
    pipeline.handle(key(VK_OEM_1, ":"))
    settle(root, refocus=refocus)
    check("单字符补全 ':' → ':='", content(), ":=")

    # 10) 整词快捷输入：键入 if → 呼吸灯 → Tab 补全成完整骨架
    reset()
    pipeline.handle(key(VK_I, "i"))
    pipeline.handle(key(VK_F, "f"))
    check("整词命中后请求显示呼吸灯", notifier.shown, 1)
    pipeline.handle(key(VK_TAB, "\t"))
    settle(root, refocus=refocus)
    got = content()
    check("Tab 补全后以 IF 开头", got.startswith("IF"), True)
    check("Tab 补全后包含 END_IF;", got.endswith("END_IF;"), True)
    check("补全后呼吸灯已隐藏", notifier.hidden >= 1, True)

    # 11) 非白名单程序不受影响
    reset()
    settings.app_items = []
    pipeline.apply_settings(settings)
    pipeline.handle(key(0xBC, ","))
    settle(root, refocus=refocus)
    check("非白名单时不做注入", content(), "")


if __name__ == "__main__":
    raise SystemExit(main())
