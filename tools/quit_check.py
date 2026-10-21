"""端到端检查："从托盘退出"能不能干脆退出。

    python tools/quit_check.py

**为什么需要单独测这个**：托盘菜单和全局热键的回调跑在"消息线程"上，
如果它们直接去操作 tkinter 的窗口对象，调用会"看起来正常返回"，
但窗口毫无反应 —— 用户感觉是"退出时卡一下"，严重时干脆退不掉。
单元测试只能验证"没直接碰窗口"，真实的卡顿要跑起来才看得到。

正常结果：``实际退出耗时`` 在 100 毫秒上下。超过 1 秒就说明又有人在别的
线程里碰界面了（或者 shutdown 里有阻塞的等待）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import codepoint.app as app_module                                    # noqa: E402
from codepoint.app import CMD_QUIT, App                               # noqa: E402


class _NoGuard:
    """绕过单实例护栏：本机很可能正开着一个实例。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        pass


def main() -> int:
    app_module.SingleInstance = _NoGuard        # type: ignore[assignment]

    cfg = os.path.join(tempfile.gettempdir(), "codepoint-quit-check")
    os.makedirs(cfg, exist_ok=True)
    app = App(config_dir=cfg, headless=False)
    if not app.start():
        print("启动失败")
        return 1

    started = time.perf_counter()

    def tray_quit() -> None:
        time.sleep(1.2)                          # 等界面起来再动手
        print("  [托盘线程] 触发退出…", flush=True)
        app._on_tray_command(CMD_QUIT)
        print("  [托盘线程] quit() 返回 +%.0f ms"
              % ((time.perf_counter() - started) * 1000), flush=True)

    threading.Thread(target=tray_quit, daemon=True, name="fake-tray").start()
    app.run()                                    # 真主循环，直到窗口销毁

    total = (time.perf_counter() - started) * 1000
    actual = total - 1200
    print(f"  进程退出总耗时 {total:.0f} ms，实测退出耗时约 {actual:.0f} ms")
    if actual > 1000:
        print("  ✗ 太慢了 —— 大概率又有人在非界面线程里直接操作 tkinter")
        return 1
    print("  ✓ 退出干脆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
