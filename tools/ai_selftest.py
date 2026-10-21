"""AI 补全的端到端演练：不装钩子、不动真实键盘，把整条链路跑一遍。

它回答的是"我配的这个模型到底通不通"，而不是"程序能不能启动"：

    调模型生成 → 清洗 → 挂起成预览卡 → 按 Tab 接受 → 记录要注入的文本

用法
    python tools/ai_selftest.py                        # 用配置里的后端，默认 mock
    python tools/ai_selftest.py --prompt "timer"
    python tools/ai_selftest.py --prompt "电机正反转互锁" --live   # 真发请求
    python tools/ai_selftest.py --mock                 # 强制演练模式（不看配置）

⚠ 默认读**真实配置**（%APPDATA%\\CodePoint\\settings.json），所以你在界面上
  改完地址/模型，直接跑这个就能验证，不用真的切到 CODESYS 里敲一遍。

⚠ 不装钩子、不发任何真实按键：注入动作被一个假的 injector 记录下来打印，
  所以它不会往任何程序里写字，跑的时候可以放心。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codepoint.core.config import ConfigPaths, resolve_config_dir          # noqa: E402
from codepoint.core.events import KeyPress, WindowInfo                     # noqa: E402
from codepoint.core.models import MATCH_ALL                                # noqa: E402
from codepoint.core.storage import SettingsStore                           # noqa: E402
from codepoint.platform_win.keylayout import VK_TAB                        # noqa: E402
from codepoint.services.ai_provider import (                               # noqa: E402
    MockCompleter, clean_code, create_completer,
)
from codepoint.services.app_filter import AppFilter                        # noqa: E402
from codepoint.services.pipeline import KeyboardPipeline                   # noqa: E402
from codepoint.services.quick_action import QuickActionEngine              # noqa: E402


# ---------------------------------------------------------------- 假零件


class _Windows:
    def __init__(self) -> None:
        self.window = WindowInfo(hwnd=1, pid=2, process_name="demo.exe",
                                 window_title="demo - 演练窗口", thread_id=3)

    def front(self) -> WindowInfo:
        return self.window

    def self_pid(self) -> int:
        return 4


class _Injector:
    """把"要注入什么"记下来，绝不真的按键。"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.backs: list[int] = []

    def send_text(self, text: str) -> None:
        self.texts.append(text)

    def send_backspaces(self, count: int) -> None:
        self.backs.append(count)

    def send_ime_guard(self) -> None:
        pass


class _Notifier:
    """记录界面层收到的通知（v1.3 起补全是"预览卡"）。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.cards: list = []

    def show(self, action) -> None:
        trigger = getattr(action, "trigger", "?")
        self.events.append(f"模板预览[{trigger}]")

    def show_card(self, card) -> None:
        self.cards.append(card)
        self.events.append(f"卡片[{card.state}] {card.label}")

    def hide(self) -> None:
        self.events.append("隐藏")

    @property
    def last_card(self):
        return self.cards[-1] if self.cards else None


# ---------------------------------------------------------------- 主流程


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ai_selftest",
                                     description="AI 补全端到端演练（不装钩子）")
    parser.add_argument("--prompt", default="5 秒延时启动定时器",
                        help="要生成什么（相当于选中/输入的那句话）")
    parser.add_argument("--live", action="store_true",
                        help="用配置里的真实后端发请求（默认只演练）")
    parser.add_argument("--mock", action="store_true",
                        help="强制用演练后端，无视配置")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="等结果的最长秒数")
    parser.add_argument("--config", default=None, help="指定配置目录")
    args = parser.parse_args(argv)

    config_dir = resolve_config_dir(args.config)
    settings, error = SettingsStore(ConfigPaths(Path(config_dir))).load()
    if error:
        print(f"[警告] {error}")

    if args.mock or not args.live:
        completer = MockCompleter(delay=0.35)
        label = "演练模式"
    else:
        completer = create_completer(settings)
        label = completer.describe()

    print("== AI 补全演练 ==")
    print(f"配置目录        : {config_dir}")
    print(f"后端            : {label}")
    print(f"提示词          : {args.prompt}")
    print("-" * 60)

    injector = _Injector()
    notifier = _Notifier()
    pipeline = KeyboardPipeline(
        window_probe=_Windows(),
        ime_probe=type("ImeStub", (), {"state": lambda self, hwnd: None})(),
        injector=injector,
        notifier=notifier,
        app_filter=AppFilter([], mode=MATCH_ALL),
        quick_actions=QuickActionEngine([]),
    )

    # ① 生成（真实调用，这里就是唯一会联网/耗时的地方）
    started = time.monotonic()
    try:
        raw = completer.complete(args.prompt)
    except Exception as exc:      # noqa: BLE001
        print(f"生成失败        : {exc.__class__.__name__}: {exc}")
        print("排查建议        : 先跑设置页的「测试连接」，或用 --mock 排除模型因素")
        pipeline.close()
        return 1
    cost_ms = (time.monotonic() - started) * 1000

    text = clean_code(raw)
    if not (text or "").strip():
        print(f"生成结果        : 空（耗时 {cost_ms:.0f} ms）")
        print("排查建议        : 服务通了但模型没吐内容，先检查模型名")
        pipeline.close()
        return 1
    print(f"生成            : 耗时 {cost_ms:.0f} ms，清洗后 {len(text)} 字")

    # ② 挂起成预览卡（这一步之后就要等用户按 Tab 了）
    if not pipeline.offer(text, label="AI 生成"):
        print("挂起失败        : 文本为空")
        pipeline.close()
        return 1

    card = notifier.last_card
    print("-" * 60)
    print(f"预览卡          : {card.state}（{len(card.body)} 字，等 Tab 确认）")
    print("会插入的内容：")
    print("-" * 60)
    print(card.body)
    print("-" * 60)

    # ③ 模拟用户按 Tab
    accepted = pipeline.handle(KeyPress(vk=VK_TAB, char="\t", is_down=True))
    print(f"Tab 接受        : {'已吞掉' if accepted else '没接住'}")

    if injector.texts:
        print(f"实际注入        : {len(injector.texts[0])} 字 · 退格 {injector.backs or [0]}")
        print("结论            : 链路通 ✅（生成 → 预览 → Tab → 插入）")
        code = 0
    else:
        print("实际注入        : （空）")
        print("结论            : 预览出来了，但接受后没插入 ❌")
        code = 1

    if notifier.events:
        print("界面事件        : " + " → ".join(notifier.events[:8]))
    print(f"统计            : 挂起 {pipeline.stats.ai_started} · "
          f"完成 {pipeline.stats.ai_completed} · 丢弃 {pipeline.stats.ai_dropped}")
    pipeline.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
