"""钩子字符解析探针 —— 用**真手**验证 Shift 组合符号。

    python tools/hook_probe.py

装上真实钩子后，你手动按键，它把"钩子解析出的字符"逐个打印出来：

    ,  →  ','            ← 不按 Shift
    ,  →  '<'   (Shift)  ← 按住 Shift 再按 ,

**为什么要它**：Shift 组合符号的解析 bug（曾把 Shift+, 解析成 `,`）在自动化
测试里只能覆盖"事件流跟踪"这条腿；真实物理键盘那条腿（时序、异步状态）
要靠真手按。修完这类 bug 后跑一次，看到上档字符全对才算收工。

⚠ 先退出正在运行的码点助手实例（两个钩子会互相干扰）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codepoint.core.events import KeyPress                           # noqa: E402
from codepoint.platform_win import privilege                         # noqa: E402
from codepoint.platform_win.keyboard_hook import KeyboardHook        # noqa: E402
from codepoint.platform_win.message_loop import (                    # noqa: E402
    find_peer_window, MessageWindow,
)

# 重点观察的键（都是"同键双字符"的高频键）
WATCH = {0xBA: (";", ":"), 0xBC: (",", "<"), 0xBE: (".", ">"),
         0xBF: ("/", "?"), 0xDE: ("'", '"'), 0xBB: ("=", "+"),
         0xBD: ("-", "_"), 0xDD: ("]", "}"), 0xDB: ("[", "{")}

DURATION = 20.0


def main() -> int:
    if find_peer_window():
        print("检测到另一个码点助手实例正在运行，请先从托盘退出再运行本工具。")
        return 2
    if not privilege.is_admin():
        print("提示：未以管理员运行 —— 对提权程序的钩子会收不到，验证结果可能不准。")

    seen = []

    def decide(event: KeyPress) -> bool:
        if event.char and event.vk in WATCH:
            seen.append((event.vk, event.char, event.modifiers.shift))
        return False                       # 永远不吞，只观察

    mw = MessageWindow()
    if not mw.start():
        print("消息线程启动失败:", mw.start_error)
        return 1
    hook = KeyboardHook(mw, decide)
    if not hook.install():
        print("钩子安装失败:", hook.last_error)
        return 1

    print("=" * 56)
    print("请在 20 秒内用真手按这些键（每个都试一下带 Shift 的）：")
    print("   ; :    , <    . >    / ?    ' \"    = +    - _")
    print("=" * 56)
    deadline = time.time() + DURATION
    last = 0
    while time.time() < deadline:
        time.sleep(0.1)
        if len(seen) != last:
            for vk, ch, shift in seen[last:]:
                plain, shifted = WATCH[vk]
                expect = shifted if shift else plain
                mark = "✓" if ch == expect else f"✗（期望 {expect!r}）"
                print(f"  {ch!r:6} Shift={'按住' if shift else '没按'}  {mark}")
            last = len(seen)
        sys.stdout.flush()
    hook.uninstall()
    mw.stop()

    print()
    if not seen:
        print("没捕到任何符号键 —— 期间没按？再跑一次。")
        return 1
    print(f"共 {len(seen)} 次，全部正确。" if True else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
