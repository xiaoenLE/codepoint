"""码点助手（CodePoint）入口。

    python main.py                # 正常启动
    python main.py --minimized    # 启动后直接收进托盘（开机自启用）
    python main.py --headless     # 无界面（只跑后台，用于排障）
    python main.py --selftest     # 自检：不装钩子，只验证各层能否装配
    python main.py --config DIR   # 指定配置目录（绿色版/测试用）
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="CodePoint",
        description="中文标点自动转英文 + 代码片段快捷补全",
    )
    parser.add_argument("--minimized", action="store_true",
                        help="启动后直接最小化到系统托盘")
    parser.add_argument("--headless", action="store_true",
                        help="不创建界面（后台运行）")
    parser.add_argument("--selftest", action="store_true",
                        help="装配自检，不安装键盘钩子")
    parser.add_argument("--config", default=None,
                        help="自定义配置目录")
    return parser.parse_args(argv)


def run_selftest() -> int:
    """不碰钩子/托盘的环境自检，用来快速判断"能不能跑起来"。"""
    print("== 码点助手 自检 ==")
    ok = True

    from codepoint import __version__
    print(f"版本            : {__version__}")
    print(f"Python          : {sys.version.split()[0]}  ({sys.executable})")

    try:
        import tkinter
        print(f"tkinter         : OK (Tk {tkinter.TkVersion})")
    except Exception as exc:      # noqa: BLE001
        ok = False
        print(f"tkinter         : 缺失 —— {exc}\n"
              f"                  请换一个带 tkinter 的 Python 解释器")

    from codepoint.core.config import resolve_config_dir
    from codepoint.core.defaults import default_punctuation_rules, default_quick_actions
    rules = default_punctuation_rules()
    actions = default_quick_actions()
    print(f"默认标点规则    : {len(rules)} 条")
    print(f"默认快捷输入    : {len(actions)} 条")
    print(f"配置目录        : {resolve_config_dir()}")

    from codepoint.platform_win import privilege
    print(f"管理员权限      : {'是' if privilege.is_admin() else '否（可能无法对提权程序生效）'}")

    # AI 补全：只报告"当前配置会走哪个后端"，**不发请求** ——
    # 自检要能在断网、模型没起的时候照常跑完。
    try:
        from codepoint.core.config import build_default_settings
        from codepoint.services.ai_provider import settings_hint

        print(f"AI 补全         : {settings_hint(build_default_settings())}")
    except Exception as exc:      # noqa: BLE001
        ok = False
        print(f"AI 补全         : 装配失败 —— {exc}")

    from codepoint.platform_win.message_loop import MessageWindow
    mw = MessageWindow("CodePointSelfTest")
    started = mw.start()
    print(f"消息线程        : {'OK' if started else '失败 —— ' + str(mw.start_error)}")
    ok = ok and started
    if started:
        from codepoint.platform_win.foreground import query
        info = query()
        print(f"前台窗口探测    : {info.process_name or '(未知)'} · "
              f"{info.window_title[:28] or '(无标题)'}")
        mw.stop()

    from codepoint.services.pipeline import KeyboardPipeline
    from codepoint.ui.bridge import NullBridge
    from codepoint.services.ports import NullImeProbe

    class _Noop:
        def send_text(self, text): pass
        def send_backspaces(self, count): pass
        def send_ime_guard(self): pass

    class _Win:
        def front(self):
            from codepoint.core.events import WindowInfo
            return WindowInfo(hwnd=1, pid=2, process_name="x.exe")
        def self_pid(self): return 3

    try:
        p = KeyboardPipeline(window_probe=_Win(), ime_probe=NullImeProbe(),
                             injector=_Noop(), notifier=NullBridge())
        print("主板装配        : OK")
    except Exception as exc:      # noqa: BLE001
        ok = False
        print(f"主板装配        : 失败 —— {exc}")

    print("== 自检结果:", "通过 ==" if ok else "有问题，见上 ==")
    return 0 if ok else 1


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.selftest:
        return run_selftest()

    from codepoint.app import App

    app = App(config_dir=args.config,
              headless=args.headless,
              start_hidden=args.minimized)
    try:
        if not app.start():
            return 2
        return app.run()
    except Exception:      # noqa: BLE001
        traceback.print_exc()
        try:
            app.shutdown()
        except Exception:      # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
