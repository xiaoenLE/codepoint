"""命令行自检：这个服务到底支不支持 FIM，两种策略哪个能用。

用法：
    python tools/fim_selftest.py
    python tools/fim_selftest.py --base http://127.0.0.1:11434/v1 --model qwen2.5-coder:1.5b-base
    python tools/fim_selftest.py --stream

它做的事很笨但很直接：拿一段"中间缺了一行"的代码，分别用
「服务端 suffix」和「手拼 token」各补一次，把补出来的东西原样打出来。
好不好用一眼就能看出来 —— 补对了是中间那行，补飞了会自己另起一段。

为什么值得单独一个工具：FIM 的坑几乎都是"静默失败"。token 用错族、
服务端不认 suffix，都不会报错，只会返回空串或者跑偏的代码。
在界面上看到"补全没反应"，跑一遍这个就知道是模型的问题还是配置的问题。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codepoint.services.ai_provider import (  # noqa: E402
    FIM_SERVER, FIM_TOKEN, OpenAiCompleter, clean_fim, fim_tokens_for,
)

#: 中间正好缺一行「第 4 个」，是 FIM 最典型的考题：
#: 光看上文，模型得自己数到 4；能看见下文（第 5 个），它就知道该补 4。
PREFIX = (
    "10_Map_yOutput[1, 4] := y2张量输出;\n"
    "10_Map_yOutput[2, 4] := y2张量输出;\n"
    "10_Map_yOutput[3, 4] := y2张量输出;\n"
)
SUFFIX = (
    "10_Map_yOutput[5, 4] := y2张量输出;\n"
    "10_eStop := X40.1;\n"
)


def _run(completer, strategy: str, *, stream: bool):
    deltas = []
    on_delta = (lambda text: deltas.append(text)) if stream else None
    started = time.monotonic()
    try:
        text = completer.complete_fim(
            PREFIX, SUFFIX, 6, on_delta=on_delta, strategy=strategy)
        return text, time.monotonic() - started, "", deltas
    except Exception as exc:              # noqa: BLE001
        return "", time.monotonic() - started, f"{exc.__class__.__name__}: {exc}", deltas


def main() -> int:
    parser = argparse.ArgumentParser(description="FIM 能力自检")
    parser.add_argument("--base", default="http://159.75.50.193:6500/v1",
                        help="OpenAI 兼容地址，填到 /v1 即可")
    parser.add_argument("--model", default="qwen2.5-coder:1.5b-base")
    parser.add_argument("--key", default="", help="本地模型留空")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--stream", action="store_true", help="同时验证流式")
    args = parser.parse_args()

    family = fim_tokens_for(args.model)
    print("=" * 66)
    print(" 地址   %s" % args.base)
    print(" 模型   %s" % args.model)
    print(" token   %s" % " … ".join(family))
    print("=" * 66)
    print("\n上文：")
    print(PREFIX.rstrip("\n"))
    print("\n下文（模型能看见这段）：")
    print(SUFFIX.rstrip("\n"))
    print("\n" + "-" * 66)

    completer = OpenAiCompleter(
        base_url=args.base, model=args.model, api_key=args.key,
        timeout=args.timeout)

    results = {}
    for strategy, label in ((FIM_SERVER, "服务端 suffix"), (FIM_TOKEN, "手拼 token")):
        text, seconds, error, deltas = _run(completer, strategy, stream=args.stream)
        results[strategy] = bool(text.strip())
        print("\n[%s] %.2fs" % (label, seconds))
        if error:
            print("   ✗ %s" % error)
        elif text.strip():
            print("   ✓ 补出：%s" % repr(text))
        else:
            print("   ✗ 返回空 —— 这个模型/服务不支持这套")
        if args.stream:
            if deltas:
                print("   流式：%d 次回调，最后一次 = %r" % (len(deltas), deltas[-1]))
            else:
                print("   流式：一次回调都没有 —— 服务端可能没走 SSE，"
                      "或者增量在别的字段里")

    print("\n" + "=" * 66)
    if results[FIM_SERVER]:
        print(" 结论：优先用「服务端 suffix」，最快也最准。")
    elif results[FIM_TOKEN]:
        print(" 结论：服务端不吃 suffix，但手拼 token 能用 —— "
              "界面里把策略设成「手拼 token」。")
    else:
        print(" 结论：两种都不行。先确认模型是不是 -base 版本"
              "（带 instruct 的通常不做 FIM），再确认地址端口。")
    print("=" * 66)
    return 0 if any(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
