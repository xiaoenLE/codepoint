"""AI 补全的模型接入层。

职责边界
    这里只做三件事：**拼提示词**、**发请求**、**把返回的乱七八糟洗干净**。
    什么时候触发、在哪注入、钩子线程怎么不被拖死 —— 全在 services/pipeline.py。

为什么只有一个 HTTP 实现
    CodeGeeX（智谱）、DeepSeek、通义、Ollama、llama.cpp 的 /v1/chat/completions
    都是同一套 OpenAI 兼容协议，差别只在 base_url / 模型名 / 有没有 key。
    所以 `openai` 这一个 provider 就能把它们全盖住，界面上只填三格。
    `mock` 是演练实现，用来看"敲 → Tab → 生成中 → 代码落进去"这条链路。

零依赖
    全程 urllib.request，不引 requests —— 这个项目的卖点之一就是拿起来就能跑。

⚠ 安全提醒
    api_key 明文存在 settings.json 里（本机自用够用）。如果这台机器多人共用，
    宁可关掉 AI 补全，也别把带计费的 key 放在里面。
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

log = logging.getLogger("codepoint.ai")

PROVIDER_MOCK = "mock"
PROVIDER_OPENAI = "openai"

#: 系统提示词（生成一整块）。**不绑定任何语言** —— 用户可能要 ST、C、Python，
#: 写死了就会把 Python 需求也答成 ST。语言一律跟随用户给的上下文/需求。
DEFAULT_SYSTEM = (
    "你是编程助手，能写 IEC 61131-3 ST、C、Python 等各种语言的代码。\n"
    "规则：\n"
    "1. 用什么语言：跟随用户需求和参考代码里的语言；用户没明说也看不出时才问。\n"
    "2. 只输出代码本身。不要 Markdown 代码围栏，不要任何解释、不要说明文字。\n"
    "3. 缩进宽度、命名风格、关键字大小写都与参考代码保持一致。\n"
    "4. 拿不准就给最朴素、能编译/能运行的写法，不要发明不存在的库函数。"
)


#: 「接着往下写」用的提示词。和"给个需求生成一整块"不是一回事：
#: 这里要的是**预测接下来的几行**，所以强调"别重复上文、按同样的规律推"。
#: 同样不绑定语言：上文是 C 就续 C，是 Python 就续 Python。
#:
#: ⚠ 这段**刻意保持原样、写得很短**。试过把它扩成"ST 专用规则 + 禁止 C/Pascal +
#:   二维下标三条硬规定"的详版：二维下标那题是更听话了，但定时器/状态机/结构体
#:   三题从 6/12 掉到 3/12 —— 1.5B 这种小模型会被长规则带跑，开始照抄上文。
#:   所以规律性问题改由 `index_pattern_hint` 在代码里算好、只对认得出的场景补一句。
COMPLETION_SYSTEM = (
    "你是代码补全引擎。\n"
    "用户给你光标之前的代码，你要接着往下写出最可能出现的几行。\n"
    "规则：\n"
    "1. 只输出要新增的纯代码行。不要注释，不要解释，不要 Markdown 围栏，\n"
    "   不要任何给人看的说明文字（例如\"这是一个示例\"\"请按实际修改\"）。这些都不要。\n"
    "2. 不要重复上文已有的内容。\n"
    "3. 语言必须与上文完全一致（上文是 ST 就写 ST，是 C 就写 C，是 Python 就写 Python）。\n"
    "4. 风格必须与上文完全一致：缩进宽度、空格、关键字大小写、变量命名。\n"
    "5. 如果上文是规律性的连续结构（例如按索引递增的连续赋值、重复的样板行），\n"
    "   就按同样的规律往下推：索引递增，名字和值都照抄上文，每行结构保持一致。\n"
    "6. 拿不准就输出空字符串，不要编造不存在的函数或变量。"
)


#: few-shot 示例：专门对付"规律性连续结构"的补全。小模型（1.3B 级）靠**模仿示例**
#: 比靠"读懂指令"有效得多 —— 实测 deepseek-coder:1.3b 只给指令会重复上文，
#: 给 2 组"上下文 → 按规律往下推"的样例就学会递增了。大模型也受益于更稳的格式。
#: 示例一律纯代码、值照抄、无注释 —— 先用样例把小模型爱加解说、爱乱改值的毛病示范掉。
_FEW_SHOT: Tuple[Tuple[str, str], ...] = (
    (
        "a[0] := 1;\n"
        "a[1] := 1;\n"
        "a[2] := 1;\n"
        "\n请只输出光标处应插入的代码，最多 3 行，不要重复上文。",
        "a[3] := 1;\n"
        "a[4] := 1;\n"
        "a[5] := 1;",
    ),
    (
        "yOutput[0] := GVL._Do[Output.绿灯];\n"
        "yOutput[1] := GVL._Do[Output.绿灯];\n"
        "yOutput[2] := GVL._Do[Output.绿灯];\n"
        "\n请只输出光标处应插入的代码，最多 3 行，不要重复上文。",
        "yOutput[3] := GVL._Do[Output.绿灯];\n"
        "yOutput[4] := GVL._Do[Output.绿灯];\n"
        "yOutput[5] := GVL._Do[Output.绿灯];",
    ),
)

#: 认「二维下标成组出现」的形式：``IO_Map.yOutput[1, 0] := ...``
_INDEX_RE = re.compile(r"^\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*:=")


def _index_group(context: str):
    """认出"同一数组、第一维固定、第二维从 lo 排到 7"的一组行。

    返回 ``(name, axis, lo, hi, rows)``，认不出返回 ``None``。``rows`` 是
    ``(整行, 第一维, 第二维, 等号右边的部分)``。宁可不认，也不乱猜。
    """
    rows = []
    for line in (context or "").splitlines():
        m = _INDEX_RE.match(line)
        if m:
            rows.append((line, m.group(1), int(m.group(2)), int(m.group(3)),
                         line.split(":=", 1)[-1]))
    if len(rows) < 3:
        return None
    if rows[-1][0].strip() != _last_line(context).strip():
        return None

    name = rows[-1][1]
    rows = [r for r in rows if r[1] == name]
    if len(rows) < 3:
        return None

    first_dims = {r[2] for r in rows}
    second_dims = [r[3] for r in rows]
    if len(first_dims) != 1:
        return None
    axis, lo, hi = first_dims.pop(), second_dims[0], second_dims[-1]
    if second_dims != list(range(lo, lo + len(second_dims))) or hi < 7:
        return None
    return name, axis, lo, hi, rows


def _rhs_number(rows) -> Optional[int]:
    """右侧名字里的数字（``y2报警输出`` → 2）；各行一致才算得出来。"""
    numbers = set()
    for *_, rhs in rows:
        m = re.search(r"([A-Za-z_]\w*?)(\d+)", rhs)
        if m:
            numbers.add(int(m.group(2)))
    return numbers.pop() if len(numbers) == 1 else None


#: 认「一维下标连续出现」的形式：``yOutput[0] := y1;``
_SINGLE_RE = re.compile(r"^\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\[\s*(\d+)\s*\]\s*:=")


def _last_line(context: str) -> str:
    """最后一行非空内容（光标就停在这后面）。"""
    for line in reversed((context or "").splitlines()):
        if line.strip():
            return line
    return ""


def _index_group_1d(context: str):
    """认出"同一个一维数组、下标连续递增"的一组行（至少 2 行）。

    返回 ``(name, rows)``，``rows`` 是 ``(整行, 名字, 下标, 等号右边)``。
    两行起就算 —— 因为"补全"最常见的场景就是刚敲了两行，等它把后面几组补齐。

    **必须紧贴光标**：最后一行不在这组里就返回 None。否则光标已经移到别处
    （例如后面又写了几行编号），还去补 ``yOutput[4]``，就补错地方了。
    """
    rows = []
    for line in (context or "").splitlines():
        m = _SINGLE_RE.match(line)
        if m:
            rows.append((line, m.group(1), int(m.group(2)), line.split(":=", 1)[-1]))
    if len(rows) < 2:
        return None
    if rows[-1][0].strip() != _last_line(context).strip():
        return None

    name = rows[-1][1]
    rows = [r for r in rows if r[1] == name]
    if len(rows) < 2:
        return None

    idxs = [r[2] for r in rows]
    if idxs != list(range(idxs[0], idxs[0] + len(idxs))):
        return None
    return name, rows


#: 裸编号行：``1`` / ``2`` / ``12``
_INT_RE = re.compile(r"^\s*(\d+)\s*$")


def _number_sequence(context: str):
    """认出光标前"一行一个数字、连续递增"的编号（1/2/3/4 → 接 5）。

    和下标赋值是两回事：那组 ``yOutput[i]`` 已经在上面写完了，光标移到编号上，
    该接的是编号，不是回头补数组。
    """
    lines = [ln for ln in (context or "").splitlines() if ln.strip()]
    numbers = []
    for line in reversed(lines):
        m = _INT_RE.match(line)
        if not m:
            break
        numbers.append(int(m.group(1)))
    numbers.reverse()
    if len(numbers) < 2:
        return None
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    return numbers


def _rhs_numbers(rows):
    """每一行右侧名字里的数字；有一行没有数字就返回 None。"""
    numbers = []
    for *_, rhs in rows:
        m = re.search(r"([A-Za-z_]\w*?)(\d+)", rhs)
        if not m:
            return None
        numbers.append(int(m.group(2)))
    return numbers


def _hint_1d(context: str) -> str:
    group = _index_group_1d(context)
    if group is None:
        return ""
    name, rows = group
    idxs = [r[2] for r in rows]
    lo, hi = idxs[0], idxs[-1]
    numbers = _rhs_numbers(rows)
    if numbers and numbers == list(range(numbers[0], numbers[0] + len(numbers))):
        offset = numbers[0] - lo
        m = re.search(r"([A-Za-z_]\w*?)(\d+)", rows[-1][3])
        prefix = m.group(1) if m else ""
        return (f"注意：上面这 {len(rows)} 行是 {name} 的连续赋值，方括号里的索引从 {lo} 递增到 {hi}，"
                f"右侧名字里的数字同时从 {numbers[0]} 递增到 {numbers[-1]}（比索引大 {offset}）。"
                f"下一行应是 [{hi + 1}] 配 {prefix}{numbers[-1] + 1}，继续按同样的规律递增。")
    return (f"注意：上面这 {len(rows)} 行是 {name} 的连续赋值，方括号里的索引从 {lo} 递增到 {hi}。"
            f"下一行应是 [{hi + 1}]，继续按同样的规律递增。")


def _seed_1d(context: str) -> str:
    group = _index_group_1d(context)
    if group is None:
        return ""
    _, rows = group
    line, _, index, rhs = rows[-1]
    head, _, _ = line.partition(":=")
    head = re.sub(rf"\[\s*{index}\s*\]", f"[{index + 1}]", head, count=1)
    numbers = _rhs_numbers(rows)
    if numbers:
        rhs = re.sub(rf"(?<!\d){numbers[-1]}(?!\d)", str(numbers[-1] + 1), rhs, count=1)
    return f"{head}:= {rhs.strip()}"


def _hint_numbers(context: str) -> str:
    numbers = _number_sequence(context)
    if not numbers:
        return ""
    return (f"注意：上面最后 {len(numbers)} 行是连续编号，从 {numbers[0]} 递增到 {numbers[-1]}。"
            f"下一行是 {numbers[-1] + 1}，继续按 +1 递增，不要重复前面的编号。")


def _seed_numbers(context: str) -> str:
    numbers = _number_sequence(context)
    return str(numbers[-1] + 1) if numbers else ""


def index_pattern_hint(context: str) -> str:
    """看出下标规律，用一句算好的话告诉模型下一行该写什么。

    为什么要在代码里算，而不是全靠提示词
        实测 qwen2.5-coder:1.5b：只给规则时它把 ``[1, 7]`` 续成 ``[1, 8]``、
        ``[1, 15]`` —— 它看不出第二维是"一组 8 路"。而示例法又会让它把示例里的
        变量名抄进无关代码。所以改成：**先在这里算清楚，再当作事实喂进去**。

    两种形态
        二维：第一维固定、第二维排满一组（到 7）→ 换到下一维（``[1, 7]`` → ``[2, 0]``）
        一维：下标连续递增（``[0] → [1]``）→ 接着递增（``[2]``）

    右侧名字里的数字怎么办
        轴号/下标和名字里的数字往往差着一截（``[1, x]`` 配 ``y2报警输出``，
        ``[0]`` 配 ``y1``），所以按"各行一致的差值"去推，而不是想当然地 +1。
    """
    group = _index_group(context)
    if group is None:
        return _hint_1d(context) or _hint_numbers(context)
    name, axis, lo, hi, rows = group
    num = _rhs_number(rows)
    rename = ""
    if num is not None:
        rename = (f"；右侧名字里的数字 {num} 要改成 {num + 1}"
                  f"（轴号 {axis} 对应的是 {num}，下一轴就是 {num + 1}）")
    return (f"注意：上面这 {len(rows)} 行是 {name} 的同一组映射：第一维固定为 {axis}，"
            f"第二维从 {lo} 到 {hi} 正好排满一组（{hi - lo + 1} 路）。"
            f"下一行必须换到 [{axis + 1}, {lo}]，第二维重新从 {lo} 开始递增，"
            f"不要写 [{axis}, {hi + 1}]{rename}。")


def index_pattern_seed(context: str) -> str:
    """直接算出下一组的第一行，当作"种子"给模型打样。

    实测（qwen2.5-coder:0.5b / 1.5b）：只给上面那段说明，0.5b 三次里轴号对两次、
    名字一次都没改对；把这一行算好写进提示词，两个模型都能稳定输出整组 8 行，
    0.5b 从 0/3 变成 3/3，且快一倍（3.4s 对 8s）。

    前提是"各轴信号顺序相同"（``y2报警/匀速/张力…`` → ``y3报警/匀速/张力…``）。
    这个假设对内绕机这类按轴复制的映射成立；如果哪天某轴少一路，预览卡里能一眼看出来。
    """
    group = _index_group(context)
    if group is None:
        return _seed_1d(context) or _seed_numbers(context)
    _, axis, lo, _, rows = group
    num = _rhs_number(rows)

    first_line = next((r[0] for r in rows if r[3] == lo), rows[0][0])
    head, _, rhs = first_line.partition(":=")
    head = re.sub(rf"\[\s*{axis}\s*,\s*{lo}\s*\]", f"[{axis + 1}, {lo}]", head, count=1)
    if num is not None:
        rhs = re.sub(rf"(?<!\d){num}(?!\d)", str(num + 1), rhs, count=1)
    return f"{head}:= {rhs.strip()}"


#: 下标赋值行里被写成 ``=`` 的情况：``yOutput[2] = y3;`` → ``yOutput[2] := y3;``
_ASSIGN_EQ_RE = re.compile(
    r"^(\s*[A-Za-z_]\w*(?:\.\w+)*\s*\[[^\]]+\])\s*=(?!=)\s*(.*)$")


def _fix_single_eq(text: str) -> str:
    """把下标赋值行里的单个 ``=`` 修成 ``:=``。

    实测 qwen2.5-coder:0.5b 会写 ``yOutput[2] = y3;``（漏了冒号）。只处理
    "左边是 ``名字[...]``" 的行，``IF a = b`` 这类比较不受影响。
    """
    out = []
    for line in (text or "").splitlines():
        m = _ASSIGN_EQ_RE.match(line)
        out.append(f"{m.group(1)} := {m.group(2)}" if m else line)
    return "\n".join(out)


def _index_heads(text: str) -> set:
    """取出每一行的下标头部（``yOutput[2]`` / ``IO_Map.yOutput[1, 0]``），用来判重。"""
    heads = set()
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z_]\w*(?:\.\w+)*\s*\[[^\]]+\])", line)
        if m:
            heads.add(re.sub(r"\s+", "", m.group(1)))
    return heads


#: 下标赋值行（``yOutput[2] := y3;`` / ``IO_Map.yOutput[1, 0] := TRUE;``）
_SEED_LINE_RE = re.compile(r"^\s*[A-Za-z_]\w*(?:\.\w+)*\s*\[[^\]]+\]\s*:=")


def _trim_lines(text: str, limit: int) -> str:
    """硬性截断到 limit 行。

    提示词里写了"最多 N 行"，但 0.5b 这种小模型会失控 —— 实测 4 行上文时它一口气
    写了 24 行（``[4]`` 一路到 ``[27]``）。行数上限不能只靠请求，得在出结果时兜住。
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[:max(1, int(limit))])


def ensure_seed_line(context: str, text: str, max_lines: int = 6) -> str:
    """模型有时会从种子行的**下一行**开始写，把算好的那一行补回开头。

    实测 qwen2.5-coder:1.5b 在一维场景（``[0] → [1]``）会输出 ``[3][4][5]``，
    把 ``[2]`` 漏掉；而种子行是我们按上下文算出来的、确定正确的那一行，
    补在开头比丢一行强。

    同时顺手做三件小修补（都只在认出规律时生效）：
      - 判重看的是**下标头部**而不是整行，否则模型把 ``:=`` 写成 ``=`` 时
        会被当成"没写"，补出一行重复的 ``[2]``；
      - 下标赋值行里漏掉的冒号补回去（``[2] = y3`` → ``[2] := y3``）；
      - 认出规律时，输出里**不是该规律的行直接丢掉**（模型偶尔会吐 ``1``、
        ``yOutput[4]`` 这类串场内容）。

    纯编号序列（``1/2/3/4`` → ``5/6/7/8``）例外：这一段是算得出来的，直接生成，
    不问模型 —— 0.5b 在这个场景会完全跑偏（输出 ``yOutput[4] := y5;``）。
    """
    numbers = _number_sequence(context)
    if numbers:
        total = max(1, int(max_lines))
        start = numbers[-1] + 1
        return "\n".join(str(start + i) for i in range(total))

    seed = index_pattern_seed(context)
    if not seed:
        return text

    if _SEED_LINE_RE.match(seed):          # 下标赋值型种子
        text = _fix_single_eq(text)
        seed = _fix_single_eq(seed)
        kept = [ln for ln in (text or "").splitlines() if _SEED_LINE_RE.match(ln)]
        if kept:
            text = "\n".join(kept)
        if _index_heads(seed) & _index_heads(text):
            return text
    return seed + ("\n" + text if (text or "").strip() else "")


def build_completion_messages(context: str, max_lines: int = 6) -> List[Dict[str, str]]:
    """把"光标前几行"拼成一次补全请求（带 few-shot —— 小模型靠模仿才学会规律）。

    为什么塞几组样例：纯指令对 1.3B 级模型基本无效（实测会重复上文、加解说、乱改值），
    给 2 组"按规律往下推"的样例才学会递增。示例保持纯代码、值照抄，示范正确格式。
    """
    body = (context or "").strip("\n")
    hint = index_pattern_hint(body)
    seed = index_pattern_seed(body)

    tail = ""
    if hint:
        tail += f"\n\n{hint}"
    if seed:
        total = max(1, int(max_lines))
        # 措辞很关键：写成"第一行必须是…请原样输出"，1.5b 只肯输出这一行就停；
        # 改成"形如"+"从这里开始写满 N 行"，0.5b / 1.5b 都能稳定输出整组。
        tail += (f"\n\n下一组的第一行形如：\n{seed}\n"
                 f"请从这一行开始，按同样的规律写满 {total} 行，不要重复上文。")

    user = (
        "【光标之前的代码】\n"
        f"{body}{tail}\n\n"
        f"请只输出光标处应插入的代码，最多 {max(1, int(max_lines))} 行，不要重复上文。"
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": COMPLETION_SYSTEM}]
    for example_user, example_assistant in _FEW_SHOT:
        messages.append({"role": "user", "content": example_user})
        messages.append({"role": "assistant", "content": example_assistant})
    messages.append({"role": "user", "content": user})
    return messages


@dataclass(frozen=True)
class Preset:
    """界面上"一键填好"的服务预设。"""

    provider: str
    label: str
    base_url: str
    model: str
    note: str


#: 顺序即界面下拉的顺序。第一条是默认（不需要任何外部条件）。
PRESETS: Tuple[Preset, ...] = (
    Preset(PROVIDER_MOCK, "本地演练", "", "",
           "返回写死的示例代码，用来验证链路。装上就能用。"),
    Preset(PROVIDER_OPENAI, "MiMo（小米）", "https://api.xiaomimimo.com/v1",
           "mimo-v2.6-flash", "需要 API Key。⚠ 推理模型，首字可能要 20 秒左右，"
           "超时请调到 60 秒以上。"),
    Preset(PROVIDER_OPENAI, "llama.cpp（本机）", "http://127.0.0.1:8080/v1",
           "qwen2.5-coder-1.5b", "llama-server --port 8080 起好即可，无需 key。"),
    Preset(PROVIDER_OPENAI, "Ollama（本机）", "http://127.0.0.1:11434/v1",
           "qwen2.5-coder:1.5b", "ollama pull qwen2.5-coder:1.5b 之后直接用。"),
    Preset(PROVIDER_OPENAI, "CodeGeeX（智谱）", "https://open.bigmodel.cn/api/paas/v4",
           "codegeex-4", "需要 API Key。"),
    Preset(PROVIDER_OPENAI, "DeepSeek", "https://api.deepseek.com/v1",
           "deepseek-chat", "需要 API Key。"),
    Preset(PROVIDER_OPENAI, "Ollama 远程（测试机）", "http://159.75.50.193:6500/v1",
           "qwen2.5-coder:1.5b-base",
           "已经部署好的一台，端口 6500（不是默认 11434）。无需 key。"
           "base 模型，支持 FIM。"),
    Preset(PROVIDER_OPENAI, "Ollama deepseek-base", "http://127.0.0.1:11434/v1",
           "deepseek-coder:1.3b-base",
           "ollama pull deepseek-coder:1.3b-base 之后用。"
           "FIM 用 <fim_begin> 那套 token，程序会自动认。"),
)


# ---------------------------------------------------------------- FIM（Fill-in-the-Middle）
#
# FIM 和「接着往下写」不是一回事：
#   · 补全（chat）：只给光标之前的代码，模型猜接下来写什么 —— 它**看不见后面**；
#   · FIM：把光标之后的内容也给它（suffix），模型只负责补中间缺的那段。
# 后者在小模型上强得多：实测同一个 qwen2.5-coder:1.5b-base，
# 只给 prefix 会自己编出一整段跑飞；给了 suffix 就老老实实补上该补的一行。
#
# 两条实现路线，实测都能用：
#   · server —— /v1/completions 里带 ``suffix`` 字段，Ollama 自己在 token 层套模板。快一倍。
#   · token  —— 客户端手拼 ``<|fim_prefix|>…<|fim_suffix|>…<|fim_middle|>``，兼容性好，
#               适合那些没有 suffix 参数、但模型本身会 FIM 的服务（llama.cpp 等）。

FIM_AUTO = "auto"
FIM_SERVER = "server"
FIM_TOKEN = "token"

#: 发给模型的上下文**字符**上限。只限行数是不够的 —— 一行长得离谱的赋值语句
#: （PLC 里那种一长串 IO 映射）能把 6 行撑到几千字符，prefill 直接慢下来，
#: 小模型还容易在长上下文里丢掉"要补哪一行"。
#:
#: 截断方向不是随便定的：**prefix 留尾部、suffix 留头部**，也就是都朝光标收敛。
#: 离光标越远的代码越没用 —— 用户正在写的那一行，前面两行比两百行前重要得多。
DEFAULT_PREFIX_CHARS = 1200
DEFAULT_SUFFIX_CHARS = 600

#: 自动补全能生成多少行的**安全阀**（不是目标值）。
#:
#: 这个数和"预览窗显示几行"（``ai_card_max_lines``）是两回事：
#: 显示行数只决定卡片里折叠到第几行（超出显示「… 还有 N 行」），
#: 不该反过来掐模型的输出 —— 掐了就是"明明还能补两行，却只给半截"。
#:
#: 这里留 24 行纯粹是防跑飞：模型偶尔会一口气续几百行，插进 PLC 工程里是事故。
#: 真正决定生成量的是 ``ai_max_tokens``，想多要就调那个。
MAX_AUTO_LINES = 24

#: 各族 base 模型的 FIM 特殊 token：(前缀, 后缀, 中间)。
#:
#: 为什么必须按族选对：用错了模型**不会报错**，只会把 token 当普通文字读进去，
#: 然后给你一段空行或者跑飞的代码。实测（qwen2.5-coder:1.5b-base）：
#:   · 用 <PRE>/<SUF>/<MID>                → 吐 64 个换行；
#:   · 用 <fim_begin>/<fim_hole>/<fim_end> → 直接返回空串；
#:   · 用 <|fim_prefix|>/<|fim_suffix|>/<|fim_middle|> → 正确补出该补的那行。
FIM_TOKEN_FAMILIES: Dict[str, Tuple[str, str, str]] = {
    "qwen":      ("<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>"),
    "codegemma": ("<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>"),
    "deepseek":  ("<fim_begin>", "<fim_hole>", "<fim_end>"),
    "codellama": ("<PRE>", "<SUF>", "<MID>"),
    "starcoder": ("<fim_prefix>", "<fim_suffix>", "<fim_middle>"),
}

#: 界面上那条下拉的选项：(值, 显示文字)。
#: 标签要短 —— 下拉框是固定宽度（22 字符），太长会被截成"自动（优先…"。
FIM_FAMILY_CHOICES: Tuple[Tuple[str, str], ...] = (
    (FIM_AUTO, "自动（按模型名）"),
    ("qwen", "qwen / codegemma"),
    ("deepseek", "deepseek-coder"),
    ("codellama", "codellama"),
    ("starcoder", "starcoder"),
)

#: 界面上策略下拉的选项。
FIM_STRATEGY_CHOICES: Tuple[Tuple[str, str], ...] = (
    (FIM_AUTO, "自动（先服务端后手拼）"),
    (FIM_SERVER, "服务端 suffix（快）"),
    (FIM_TOKEN, "手拼 token（兼容好）"),
)


def fim_family_of(model: str) -> str:
    """从模型名猜 FIM 族。猜不到就按 qwen 系处理（目前最常见）。"""
    name = (model or "").lower()
    for family in FIM_TOKEN_FAMILIES:
        if family in name:
            return family
    if "star" in name:
        return "starcoder"
    if "gemma" in name:
        return "codegemma"
    return "qwen"


def fim_tokens_for(model: str, family: str = FIM_AUTO) -> Tuple[str, str, str]:
    """取 (前缀, 后缀, 中间) 三个特殊 token。

    ``family`` 明确指定了就用它，否则按模型名猜 —— 界面上留「自动」是常态，
    手选只是给"模型名看不出来"的场合兜底。
    """
    key = (family or FIM_AUTO).strip().lower()
    if key not in FIM_TOKEN_FAMILIES:
        key = fim_family_of(model)
    return FIM_TOKEN_FAMILIES[key]


class FimUnsupported(Exception):
    """服务端不认 suffix（或压根没有 /completions）—— 调用方可以退到手拼 token。"""


def clamp_tail(text: str, limit: int) -> str:
    """只留尾部（离光标最近的那段）。用于 prefix。"""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-int(limit):]


def clamp_head(text: str, limit: int) -> str:
    """只留头部（离光标最近的那段）。用于 suffix。"""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:int(limit)]


# ---------------------------------------------------------------- 接口


@runtime_checkable
class AiCompleter(Protocol):
    """把一个提示词变成一段代码。同步阻塞 —— 调用方负责放到后台线程。

    两个方法都支持 ``on_delta`` 流式回调（``(累积正文,)``）：
    传了就边生成边推给界面，不传就等整块。返回值始终是**完整**正文。
    """

    def complete(self, prompt: str, context: str = "",
                 on_delta: Optional[DeltaCallback] = None) -> str:
        ...

    def complete_lines(self, context: str, max_lines: int = 6,
                       on_delta: Optional[DeltaCallback] = None) -> str:
        """「接着往下写」：给光标前几行，返回接下来的几行。

        和 ``complete()`` 分开是因为提示词完全不同 —— 那个是"给我写一段"，
        这个是"猜我接下来会写什么"，后者要强调别重复上文、按同样规律推。
        """
        ...

    def describe(self) -> str:
        """给自检/界面显示的一行说明。"""
        ...


class NullCompleter:
    """不生成任何东西（AI 关闭时用，避免到处判 None）。"""

    def complete(self, prompt: str, context: str = "",
                 on_delta: Optional[DeltaCallback] = None) -> str:  # noqa: D102
        return ""

    def complete_lines(self, context: str, max_lines: int = 6,
                       on_delta: Optional[DeltaCallback] = None) -> str:  # noqa: D102
        return ""

    def complete_fim(self, prefix: str, suffix: str = "", max_lines: int = 6,
                     on_delta: Optional[DeltaCallback] = None, **_: object) -> str:  # noqa: D102
        return ""

    def describe(self) -> str:                                      # noqa: D102
        return "已关闭"


# ---------------------------------------------------------------- 演练实现

#: mock 的关键词 → 片段。让演示看起来像回事，而不是每次吐同一坨。
_MOCK_SNIPPETS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("timer", "定时", "延时", "ton", "tonoff"), (
        "VAR\n"
        "    fbDelay : TON;\n"
        "    xStart : BOOL;\n"
        "END_VAR\n"
        "\n"
        "fbDelay(IN := xStart, PT := T#5S);\n"
        "IF fbDelay.Q THEN\n"
        "    ;\n"
        "END_IF"
    )),
    (("interlock", "互锁", "正反转", "电机"), (
        "VAR\n"
        "    xFwd  : BOOL;\n"
        "    xRev  : BOOL;\n"
        "    xStop : BOOL;\n"
        "END_VAR\n"
        "\n"
        "IF NOT xStop THEN\n"
        "    xFwd := xFwd AND NOT xRev;   // 正转与反转互锁\n"
        "    xRev := xRev AND NOT xFwd;\n"
        "ELSE\n"
        "    xFwd := FALSE;\n"
        "    xRev := FALSE;\n"
        "END_IF"
    )),
    (("pid", "闭环", "调节"), (
        "VAR\n"
        "    fbPid : PID;\n"
        "    rSet  : REAL := 100.0;\n"
        "    rAct  : REAL;\n"
        "    rOut  : REAL;\n"
        "END_VAR\n"
        "\n"
        "fbPid(ACTUAL := rAct, SET_POINT := rSet, KP := 1.0, TN := T#2S);\n"
        "rOut := fbPid.Y;"
    )),
)

_MOCK_FALLBACK = (
    "VAR\n"
    "    nState : INT;\n"
    "    xDone  : BOOL;\n"
    "END_VAR\n"
    "\n"
    "CASE nState OF\n"
    "    0:  // 等待启动\n"
    "        ;\n"
    "    1:  // 执行\n"
    "        xDone := TRUE;\n"
    "END_CASE"
)


def mock_reply(prompt: str) -> str:
    """按关键词挑一个片段；都不沾边就给通用骨架。"""
    key = (prompt or "").lower()
    for words, snippet in _MOCK_SNIPPETS:
        if any(w in key for w in words):
            return snippet
    return _MOCK_FALLBACK


class MockCompleter:
    """演练实现：睡一小会儿再返回写死的片段。

    刻意保留 0.35s 延迟 —— 太快反而看不出"异步"到底有没有生效，
    实机上要验证「生成期间按别的键会取消」也需要这点时间窗。
    """

    def __init__(self, delay: float = 0.35) -> None:
        self._delay = max(0.0, delay)

    def _reply(self, text: str,
               on_delta: Optional[DeltaCallback]) -> str:
        """演练模式也要走流式：按行分批推，不然界面那条流式链路验不到。"""
        if on_delta is None:
            return text
        acc = ""
        lines = text.split("\n")
        for index, line in enumerate(lines):
            acc += ("\n" if index else "") + line
            on_delta(acc)
            if self._delay:
                time.sleep(self._delay / max(1, len(lines)))
        return text

    def complete(self, prompt: str, context: str = "",
                 on_delta: Optional[DeltaCallback] = None) -> str:  # noqa: D102
        if self._delay and on_delta is None:
            time.sleep(self._delay)
        return self._reply(mock_reply(prompt), on_delta)

    def complete_lines(self, context: str, max_lines: int = 6,
                       on_delta: Optional[DeltaCallback] = None) -> str:  # noqa: D102
        if self._delay and on_delta is None:
            time.sleep(self._delay)
        return self._reply(mock_reply(context), on_delta)

    def complete_fim(self, prefix: str, suffix: str = "", max_lines: int = 6,
                     on_delta: Optional[DeltaCallback] = None, **_: object) -> str:  # noqa: D102
        """演练 FIM：把前后两段一起当关键词挑片段，至少让链路能跑通。"""
        if self._delay and on_delta is None:
            time.sleep(self._delay)
        return self._reply(mock_reply(prefix + "\n" + suffix), on_delta)

    def describe(self) -> str:                                      # noqa: D102
        return "本地演练"


# ---------------------------------------------------------------- OpenAI 兼容

_VERSION_TAIL = re.compile(r"/v\d+$")


def endpoint_of(base_url: str) -> str:
    """把用户填的地址补成完整的 chat/completions 端点。

    容忍这些写法（用户不会记得该填到哪一层）：
        https://api.deepseek.com            → …/v1/chat/completions
        https://api.deepseek.com/v1         → …/v1/chat/completions
        https://open.bigmodel.cn/api/paas/v4→ …/v4/chat/completions
        http://127.0.0.1:8080/v1/chat/completions → 原样使用
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("没有填 AI 服务地址")
    if url.endswith("/chat/completions"):
        return url
    if _VERSION_TAIL.search(url):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


def completions_endpoint_of(base_url: str) -> str:
    """把地址补成 ``/completions`` 端点 —— FIM 走这条，不走 chat。

    和 ``endpoint_of`` 的区别就在最后那个词。用户如果整条 chat 地址粘进来
    （很多文档只给这个），这里会把它改回 completions，而不是拼出
    ``/chat/completions/completions`` 这种鬼东西。
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("没有填 AI 服务地址")
    if url.endswith("/chat/completions"):
        return url[: -len("/chat/completions")] + "/completions"
    if url.endswith("/completions"):
        return url
    if _VERSION_TAIL.search(url):
        return url + "/completions"
    return url + "/v1/completions"


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=max(1.0, timeout)) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"返回的不是 JSON（前 80 字：{raw[:80]}）") from exc
    if not isinstance(data, dict):
        raise ValueError("返回结构不认识")
    return data


#: 流式回调：``on_delta(到目前为止累积的正文)``。
#: **累积**而不是增量片段 —— 界面直接整块重画，不用自己拼。
DeltaCallback = Callable[[str], None]


class _StreamDone(Exception):
    """流式已经拿到够用的内容，主动收工（**不是错误**）。

    为什么需要：FIM 没有"写几行就停"的提示词约束，模型会一直往下写，
    直到自己觉得写完或撞上 ``max_tokens``。而卡片只显示前 N 行 ——
    于是用户看到正文不动了、状态却还挂着"生成中"，白白干等十几秒
    （用户报障："都没有继续生成了，为什么还等那么久"）。
    多出来的那些行反正会被 clean_fim 截掉，不如立刻断开。
    """


def _post_stream(url: str, payload: dict, api_key: str, timeout: float,
                 on_delta) -> str:
    """发一次 SSE 流式请求，边收边把累积的正文交给 ``on_delta``。

    为什么值得单独一条路：模型 3~6 秒才出全，整块等完再显示就是
    "盯着一块'生成中'发呆"；边收边画，第一行出来就能开始看 ——
    体感快一倍以上，而且早看出跑偏就能按 Esc 省掉等待。

    服务端不认 ``stream``（整块回普通 JSON）时自动退化成一次性回调，
    调用方无感。返回**未清洗**的正文（清洗交给调用方做一次就够）。

    推理模型的 ``reasoning_content`` 直接扔掉 —— 用户要的是代码，不是思考过程。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    acc: List[str] = []
    with urllib.request.urlopen(req, timeout=max(1.0, timeout)) as resp:
        first_line = None
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if first_line is None:
                if not line:
                    continue
                first_line = line
                if not (line.startswith("data:") or line.startswith("event:")
                        or line.startswith(":")):
                    # —— 不是 SSE：服务端把 stream 当耳旁风，整块 JSON ——
                    rest = line
                    try:
                        data = json.loads(rest)
                    except json.JSONDecodeError:
                        rest = line + "\n" + resp.read().decode("utf-8", "replace")
                        data = json.loads(rest)
                    text = _first_content(data)
                    if on_delta is not None and text:
                        on_delta(text)
                    return text

            if not line or line.startswith(":"):
                continue                      # 心跳 / 空行
            if not line.startswith("data:"):
                continue                      # event: xxx 之类的元行
            piece = line[5:].strip()
            if piece == "[DONE]":
                break
            try:
                chunk = json.loads(piece)
            except json.JSONDecodeError:
                continue
            if not isinstance(chunk, dict):
                continue
            err = chunk.get("error")
            if err:
                raise ValueError(f"服务返回错误：{err}")
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            first = choices[0] if isinstance(choices[0], dict) else {}
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            content = delta.get("content")
            if not isinstance(content, str) or not content:
                # /v1/completions（FIM 用的那条）增量放在 choices[0].text，
                # 不在 delta.content 里 —— 少认这一个字段，流式就永远是空的。
                plain = first.get("text")
                content = plain if isinstance(plain, str) else ""
            if content:
                acc.append(content)
                if on_delta is not None:
                    try:
                        on_delta("".join(acc))
                    except _StreamDone:
                        # 内容够了，主动收工。连接随后被 with 关掉，
                        # 服务端那边的生成也会停 —— 但已经拿到的照用。
                        break
    return "".join(acc)


class OpenAiCompleter:
    """任何 OpenAI 兼容端点（CodeGeeX / DeepSeek / Ollama / llama.cpp …）。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 8.0,
        temperature: float = 0.2,
        max_tokens: int = 400,
        system: str = DEFAULT_SYSTEM,
        disable_thinking: bool = True,
        prefix_chars: int = DEFAULT_PREFIX_CHARS,
        suffix_chars: int = DEFAULT_SUFFIX_CHARS,
    ) -> None:
        self._url = endpoint_of(base_url)
        self._model = (model or "").strip()
        self._key = (api_key or "").strip()
        self._timeout = max(1.0, float(timeout))
        self._temperature = float(temperature)
        self._max_tokens = int(max_tokens)
        self._system = system
        self._disable_thinking = bool(disable_thinking)
        self._prefix_chars = max(0, int(prefix_chars))
        self._suffix_chars = max(0, int(suffix_chars))

    @property
    def url(self) -> str:
        return self._url

    def _thinking_flag(self) -> dict:
        """关思考的请求字段。

        实测 MiMo 补全任务：不关思考首字 9.7s，关掉 1.7s —— 补全只需要
        "接着写几行"，思考几百字纯属浪费。不同服务字段名不同，这里发
        OpenAI 的 ``thinking`` 字段；服务端不认识就自动去掉重试（见 _chat）。
        """
        return {"thinking": {"type": "disabled"}} if self._disable_thinking else {}

    def _chat(self, messages: List[Dict[str, str]], max_tokens: int,
              on_delta: Optional[DeltaCallback] = None,
              temperature: Optional[float] = None) -> str:
        if not self._model:
            raise ValueError("没有填模型名")
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": (self._temperature if temperature is None
                            else float(temperature)),
            "max_tokens": int(max_tokens),
            "stream": on_delta is not None,
        }
        payload.update(self._thinking_flag())
        try:
            return self._post(payload, max_tokens, on_delta)
        except ValueError as exc:
            # 服务端不认 thinking 字段时去掉重试一次 —— 老服务不该因此挂掉
            if not self._disable_thinking or "thinking" not in str(exc).lower():
                raise
            log.info("服务端不认 thinking 字段，去掉重试")
            payload.pop("thinking", None)
            self._disable_thinking = False      # 后续请求不再带
            return self._post(payload, max_tokens, on_delta)

    def _post(self, payload: dict, max_tokens: int,
              on_delta: Optional[DeltaCallback]) -> str:
        if on_delta is None:
            data = _post_json(self._url, payload, self._key, self._timeout)
            return clean_code(_first_content(data))
        raw = _post_stream(self._url, payload, self._key, self._timeout, on_delta)
        return clean_code(raw)

    def complete(self, prompt: str, context: str = "",
                 on_delta: Optional[DeltaCallback] = None) -> str:  # noqa: D102
        return self._chat(
            build_messages(prompt, context, system=self._system),
            self._max_tokens,
            on_delta,
        )

    def complete_lines(self, context: str, max_lines: int = 6,
                       on_delta: Optional[DeltaCallback] = None) -> str:  # noqa: D102
        # 纯编号序列（1/2/3/4 → 5/6/7/8）是算得出来的，不必问模型：
        # 实测 0.5b 在这里会完全跑偏，输出 yOutput[4] := y5;，还慢好几秒。
        numbers = _number_sequence(context)
        if numbers:
            total = max(1, int(max_lines))
            start = numbers[-1] + 1
            text = "\n".join(str(start + i) for i in range(total))
            if on_delta is not None:
                on_delta(text)
            return text

        # 温度压到 0.1：补全要的是"老老实实按上文规律往下推"（确定性模仿），
        # 不是"发挥创意"。小模型（1~2B 级）对温度尤其敏感 —— 高一点就爱乱改值、
        # 加解说、重复上文；压低了才肯照着 few-shot 的样例走。
        #
        # token 预算直接给足 self._max_tokens：想让它多写就调 ai_max_tokens，
        # 而不是靠"少说几行"把它忽悠住。行数只用来告诉模型"大概要几行"。
        text = self._chat(
            build_completion_messages(context, max_lines),
            self._max_tokens,
            on_delta,
            temperature=0.1,
        )
        # 模型偶尔从种子的下一行开始写，把算好的那一行补回来（不会重复）
        return _trim_lines(ensure_seed_line(context, text, max_lines), max_lines)

    # ------------------------------------------------------------ FIM

    def _fim_request(self, prompt: str, suffix: str, max_tokens: int,
                     on_delta: Optional[DeltaCallback]) -> str:
        """打一次 ``/completions``。``suffix`` 非空就是"服务端 FIM"。

        端点每次现推（``completions_endpoint_of``）：``self._url`` 存的是 chat
        端点，而用户填地址的写法千奇百怪，不能靠字符串截一截就完事。
        """
        url = completions_endpoint_of(self._url)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "max_tokens": int(max_tokens),
            # FIM 要的是"照上文补"，不是发挥。比 chat 那条再压一档。
            "temperature": min(self._temperature, 0.2),
            "stream": on_delta is not None,
        }
        if suffix:
            payload["suffix"] = suffix
        try:
            if on_delta is None:
                data = _post_json(url, payload, self._key, self._timeout)
                return _first_content(data)
            return _post_stream(url, payload, self._key, self._timeout, on_delta)
        except urllib.error.HTTPError as exc:
            # 400/404/405/422：不是"没这个端点"就是"不认 suffix 这个字段"
            if exc.code in (400, 404, 405, 422, 501):
                raise FimUnsupported(f"HTTP {exc.code}") from exc
            raise

    def complete_fim(self, prefix: str, suffix: str = "", max_lines: int = 6,
                     on_delta: Optional[DeltaCallback] = None, *,
                     strategy: str = FIM_AUTO,
                     tokens: str = FIM_AUTO) -> str:
        """Fill-in-the-Middle：给光标前后两段，只补中间缺的。

        和 ``complete_lines`` 的本质差别：**模型能看见光标后面**。所以它不会把
        suffix 里已经写好的东西又编一遍。实测差别很大 —— 同一个 1.5B 模型，
        只给 prefix 会自己另起一段跑飞，给了 suffix 就精确补上该补的那行。

        ``strategy``：
          · ``server`` 只走服务端 suffix，不支持就报错（想确认能力时用）；
          · ``token``  只走手拼 token；
          · ``auto``   先 server，失败自动退 token（默认，也是推荐）。
        """
        if not self._model:
            raise ValueError("没有填模型名")
        mode = (strategy or FIM_AUTO).strip().lower()
        # token 预算直接给满 —— 生成多少由 ai_max_tokens 决定，不再用行数反推。
        # 行数只在 clean_fim 里当安全阀用（防一口气续几百行那种）。
        budget = self._max_tokens

        # 行数限住了，字符数没限 —— 一行长得离谱照样能把上下文撑爆。
        # 两头都朝光标收敛：前面留最近的，后面留最近的。
        raw_prefix, raw_suffix = prefix or "", suffix or ""
        prefix = clamp_tail(raw_prefix, self._prefix_chars)
        suffix = clamp_head(raw_suffix, self._suffix_chars)
        if len(prefix) < len(raw_prefix) or len(suffix) < len(raw_suffix):
            log.info("FIM 上下文按字符上限裁剪：prefix %d→%d，suffix %d→%d",
                     len(raw_prefix), len(prefix), len(raw_suffix), len(suffix))

        # 流式也要按"最终会插入的样子"推给界面：否则预览窗会先显示一大段
        # （包括和 suffix 重复的尾巴），确认后才突然变短，看起来像出错。
        live = _fim_live_delta(on_delta, suffix, max_lines)

        if mode in (FIM_AUTO, FIM_SERVER):
            try:
                raw = self._fim_request(prefix, suffix, budget, live)
                # auto 模式下"200 但空正文"也当作不支持 —— 服务端吃了 suffix
                # 却不干活，跟不支持没区别，不如换条路再试一次。
                if mode == FIM_AUTO and not (raw or "").strip():
                    raise FimUnsupported("返回空")
                return clean_fim(raw, suffix, max_lines)
            except FimUnsupported as exc:
                if mode == FIM_SERVER:
                    raise ValueError(
                        f"服务端不支持 suffix 参数（{exc}）。"
                        "把「FIM 策略」改成「手拼 token」再试。") from exc
                log.info("服务端 suffix 不可用（%s），改用手拼 FIM token", exc)

        head, hole, tail = fim_tokens_for(self._model, tokens)
        prompt = f"{head}{prefix}{hole}{suffix}{tail}"
        try:
            raw = self._fim_request(prompt, "", budget, live)
        except FimUnsupported as exc:
            raise ValueError(
                f"这个服务没有可用的 /completions 接口（{exc}），FIM 走不通 —— "
                "把「FIM 补全」关掉，改用 chat 接口的普通补全。") from exc
        return clean_fim(raw, suffix, max_lines)

    def describe(self) -> str:                                      # noqa: D102
        return f"{self._model} @ {self._url}"

    def probe_meta(self, prompt: str) -> Tuple[str, str]:
        """测连接专用：返回 ``(正文, 思考内容)``。

        为什么需要第二个值：推理模型（MiMo / deepseek-flash / r1）经常
        **content 为空但 reasoning_content 有一大段** —— 服务明明是通的，
        只看 content 会误报"内容是空的，检查模型名"（真实踩过）。
        """
        if not self._model:
            raise ValueError("没有填模型名")
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            # 推理模型会把 token 花在思考上，给太少就永远轮不到正文
            "max_tokens": 300,
            "stream": False,
        }
        payload.update(self._thinking_flag())
        data = _post_json(self._url, payload, self._key, self._timeout)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            err = data.get("error")
            if err:
                raise ValueError(f"服务返回错误：{err}")
            return "", ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            content = ""
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = ""
        return content, reasoning

    def with_system(self, system: str, *, max_tokens: int = 24) -> "OpenAiCompleter":
        """换一套 system prompt 的副本（测连接用）。

        不改自己也就不怕和正在跑的生成抢状态；``max_tokens`` 压得很小，
        测连通性回两个字母就够，没必要让模型写 400 个 token 的废话。
        """
        return OpenAiCompleter(
            base_url=self._url,
            model=self._model,
            api_key=self._key,
            timeout=self._timeout,
            temperature=0.0,
            max_tokens=max_tokens,
            system=system,
            disable_thinking=self._disable_thinking,
            prefix_chars=self._prefix_chars,
            suffix_chars=self._suffix_chars,
        )


def _first_content(data: dict) -> str:
    """从 chat/completions 的回应里取正文。取不到就给空串（上层按失败处理）。"""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        # 有的服务出错也返回 200，把错误塞在 error 里
        err = data.get("error")
        if err:
            raise ValueError(f"服务返回错误：{err}")
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    # 少数实现把 content 做成 [{"text": ...}] 数组
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    # /v1/completions 的返回没有 message，正文直接是 choices[0].text
    plain = first.get("text")
    if isinstance(plain, str):
        return plain
    return ""


# ---------------------------------------------------------------- 提示词


def build_messages(prompt: str, context: str = "", *, system: str = DEFAULT_SYSTEM,
                   template: str = "") -> List[Dict[str, str]]:
    """拼出要发出去的 messages。

    ``template`` 是界面上那条规则的 content（里面可能有 ``${prompt}``）；
    留空则直接用 prompt 本身。
    """
    text = (template or "").replace("${prompt}", prompt or "").strip()
    if not text:
        text = (prompt or "").strip()
    if context.strip():
        text += (
            "\n\n当前文件已有的代码（只作参考，不要重复输出）：\n"
            "-----\n" + context.strip()[:6000] + "\n-----"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


# ---------------------------------------------------------------- 清洗

_FENCE = re.compile(r"```[A-Za-z0-9_+\-#]*[ \t]*\r?\n(.*?)(?:```|\Z)", re.S)

#: 一行"看着像代码"的开头。用来砍掉模型在代码前面絮叨的那几句中文。
#: **不绑定语言** —— ST / C / Python 都得认，否则 Python 生成会被从
#: `def` 前面砍掉、C 会把 `#include` 当解说砍掉。
_CODE_START = re.compile(
    r"^\s*("
    # —— ST / IEC 61131-3 ——
    r"VAR\w*|PROGRAM|FUNCTION_BLOCK|FUNCTION|METHOD|INTERFACE|TYPE|"
    r"IF|FOR|WHILE|REPEAT|CASE|END_\w+|"
    # —— Python ——
    r"def|class|import|from|if|elif|else|for|while|try|except|finally|"
    r"with|return|raise|pass|break|continue|global|lambda|print|async|await|"
    # —— C / C++ ——
    r"#\s*(include|define|ifdef|ifndef|endif|pragma)|"
    r"void|int|char|float|double|long|short|unsigned|signed|struct|enum|"
    r"union|static|const|extern|typedef|return|if|else|for|while|do|switch|"
    r"case|break|continue|sizeof|goto|printf|scanf|malloc|free|"
    # —— 通用 ——
    r"[A-Za-z_]\w*\s*(:=|:|=)\s*|"   # x := 1; / x : INT; / x = 1
    r"[A-Za-z_]\w*\s*\(|"            # fbTimer(...); / foo(...)
    r"@|\}|\{|\)|\]|\[|"
    r"//|/\*|\*|#"
    r")",
)

_MAX_LINES = 200


def clean_code(text: str) -> str:
    """把模型返回的东西洗成"能直接插进编辑器"的代码。

    为什么不信任原文：
      · 模型爱包 ```st 围栏，插进去就是一堆反引号；
      · 爱先来一句"下面是一个定时器：" —— 那是给人看的，不是给人编译的；
      · 爱整体缩进一级 —— 编辑器自己还会再缩一次，叠加起来越来越深；
      · 偶尔跑飞几百行 —— 插进 PLC 工程里当场就是事故。
    """
    if not text:
        return ""

    work = text.replace("\r\n", "\n").replace("\r", "\n")

    fenced = _FENCE.search(work)
    if fenced:
        work = fenced.group(1)

    lines = work.split("\n")

    # 砍掉第一行像代码之前的那些话。找不到像代码的行就原样保留，
    # 免得把用户真的要插入的注释头/空骨架也砍没了。
    for index, line in enumerate(lines):
        if _CODE_START.match(line):
            lines = lines[index:]
            break

    lines = [line.rstrip() for line in lines]

    # 去掉公共缩进（只按非空行算，否则空行会把缩进算成 0）
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    if indents:
        cut = min(indents)
        if cut > 0:
            lines = [line[cut:] if line.strip() else "" for line in lines]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) > _MAX_LINES:
        log.warning("模型返回 %d 行，已截断到 %d 行", len(lines), _MAX_LINES)
        lines = lines[:_MAX_LINES]

    return "\n".join(lines)


# ---------------------------------------------------------------- FIM 清洗


def _trim_overlap(gen: str, suffix: str, window: int = 240) -> str:
    """剪掉生成结果末尾与 suffix 开头重复的那一截。

    1.5B 级的模型经常把后面几行又写一遍，直接插进去就是
    ``return x; return x;``。按"最长重叠"剪，最少要重叠 3 个字符才算 ——
    否则一个分号、一个换行也会被当成重叠剪掉。
    """
    if not gen or not suffix:
        return gen
    limit = min(len(gen), len(suffix), max(0, int(window)))
    for size in range(limit, 2, -1):
        if gen.endswith(suffix[:size]):
            return gen[:-size]
    return gen


def clean_fim(text: str, suffix: str = "", max_lines: int = 6) -> str:
    """洗 FIM 返回的片段。

    ⚠ **不能和 clean_code 混用。** 那个是给"生成一整块"用的：会剥公共缩进、
    砍掉第一行前面"看起来不像代码"的行。FIM 返回的是**要接在光标处的一小段**，
    里面的缩进和空行都是有意义的（要插的可能就是一行带缩进的语句），
    洗过头反而插坏。

    这里只做三件事：拆围栏、剪掉和 suffix 的重叠、去掉尾部空行 + 限行。
    首行缩进一律保留。
    """
    if not text:
        return ""
    work = text.replace("\r\n", "\n").replace("\r", "\n")

    fenced = _FENCE.search(work)
    if fenced:
        work = fenced.group(1)

    work = _trim_overlap(work, suffix)

    lines = work.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    limit = max(1, int(max_lines))
    if len(lines) > limit:
        # debug 级：流式回调每来一段都会走一遍这里，打 info 会变成
        # 20 条/秒的写盘（实测日志刷屏，还拖慢流式）。
        log.debug("FIM 返回 %d 行，按上限截到 %d 行", len(lines), limit)
        lines = lines[:limit]
    return "\n".join(lines)


def _fim_live_delta(on_delta: Optional[DeltaCallback], suffix: str,
                    max_lines: int) -> Optional[DeltaCallback]:
    """把流式回调包一层：每次推给界面的都是"按最终形态洗过"的内容。

    不包会怎样：预览窗先显示模型多写的那几行（和 suffix 重复的部分），
    用户按 Tab 确认后插进去的却是剪过的 —— 看到和拿到的不一致，像 bug。
    """
    if on_delta is None:
        return None

    def push(acc: str) -> None:
        on_delta(clean_fim(acc, suffix, max_lines))
        # 已经写超上限了 → 后面的行横竖都会被截掉，再等下去用户只能看着
        # "生成中"发呆（正文一动不动，因为早就截满 N 行了）。
        if acc.count("\n") > max_lines:
            raise _StreamDone()

    return push


# ---------------------------------------------------------------- 工厂


def create_completer(settings) -> AiCompleter:
    """按当前配置造一个 completer。配置不完整时**退回 mock** 并记日志 ——
    宁可生成一个假片段让人看见"链路是通的"，也别让 Tab 按下去毫无反应。
    """
    provider = (getattr(settings, "ai_provider", PROVIDER_MOCK) or PROVIDER_MOCK).lower()
    if provider == PROVIDER_OPENAI:
        try:
            return OpenAiCompleter(
                base_url=settings.ai_base_url,
                model=settings.ai_model,
                api_key=settings.ai_api_key,
                timeout=settings.ai_timeout,
                temperature=settings.ai_temperature,
                max_tokens=settings.ai_max_tokens,
                disable_thinking=getattr(settings, "ai_disable_thinking", True),
                prefix_chars=getattr(settings, "ai_prefix_max_chars",
                                      DEFAULT_PREFIX_CHARS),
                suffix_chars=getattr(settings, "ai_suffix_max_chars",
                                      DEFAULT_SUFFIX_CHARS),
            )
        except Exception as exc:      # noqa: BLE001
            log.warning("AI 配置不完整（%s），退回本地演练模式", exc)
            return MockCompleter()
    return MockCompleter(delay=0.0)


def settings_hint(settings) -> str:
    """一行说明，给自检和界面用。"""
    if not getattr(settings, "ai_enabled", False):
        return "AI 补全：已关闭"
    completer = create_completer(settings)
    return f"AI 补全：{'演练模式' if isinstance(completer, MockCompleter) else completer.describe()}"


# ---------------------------------------------------------------- 连接测试


@dataclass(frozen=True)
class ProbeResult:
    """界面上那个「测试连接」按钮的结果。"""

    ok: bool
    detail: str
    seconds: float = 0.0


#: 专门用来测连通性的提示词：要求极短，省 token 也省时间。
PROBE_PROMPT = "只回复两个字母：OK"

#: 测连接**不复用**配置里的 system prompt —— 那套要求"只输出代码"，
#: 会把 "OK" 也当成代码来处理，看不出服务到底通没通。
_PROBE_SYSTEM = "你是连通性测试对象。严格按照用户要求，只回复指定的几个字符。"


def probe(completer: AiCompleter, prompt: str = PROBE_PROMPT) -> ProbeResult:
    """真发一次极小的请求，判断后端到底通不通。

    同步阻塞，调用方负责放到后台线程（界面点按钮时必须这么做，
    否则整个窗口会卡住几秒 —— 用户会以为程序死了）。

    ⚠ 推理模型（MiMo / deepseek-flash）可能 content 为空但 reasoning 有一大段，
    那也是**通了** —— 用 probe_meta 区分，别误报"检查模型名"。
    """
    if isinstance(completer, (MockCompleter, NullCompleter)):
        return ProbeResult(False, "当前是本地演练模式，没有可测的连接")

    started = time.monotonic()
    if isinstance(completer, OpenAiCompleter):
        try:
            content, reasoned = completer.probe_meta(prompt)
        except ValueError as exc:
            return ProbeResult(False, str(exc), time.monotonic() - started)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:120]
            except Exception:      # noqa: BLE001
                pass
            return ProbeResult(False, f"HTTP {exc.code} {body}".strip(),
                               time.monotonic() - started)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return ProbeResult(False,
                               f"连不上（{reason}）。服务起没起？地址端口对不对？",
                               time.monotonic() - started)
        except TimeoutError:
            return ProbeResult(False,
                               "超时。推理模型首字可能要 20 秒以上 —— "
                               "把 settings.json 的 ai_timeout 调到 60 以上再试。",
                               time.monotonic() - started)
        except Exception as exc:             # noqa: BLE001
            return ProbeResult(False, f"{exc.__class__.__name__}: {exc}",
                               time.monotonic() - started)
        seconds = time.monotonic() - started
        if (content or "").strip():
            return ProbeResult(True, f"{seconds:.1f}s，返回 {len(content)} 字", seconds)
        if reasoned:
            return ProbeResult(
                True,
                f"{seconds:.1f}s，服务正常（推理模型：思考了 {len(reasoned)} 字，"
                "正文为空属正常，等它想完就好）",
                seconds)
        return ProbeResult(False, "服务有回应，但内容是空的 —— 先检查模型名", seconds)

    # 其他自定义实现走通用路径
    try:
        text = completer.complete(prompt)
    except ValueError as exc:            # 配置问题（地址/模型名/服务端报错）
        return ProbeResult(False, str(exc), time.monotonic() - started)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:120]
        except Exception:      # noqa: BLE001
            pass
        return ProbeResult(False, f"HTTP {exc.code} {body}".strip(),
                           time.monotonic() - started)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return ProbeResult(False, f"连不上（{reason}）。服务起没起？地址端口对不对？",
                           time.monotonic() - started)
    except TimeoutError:
        return ProbeResult(False, "超时。模型可能还在加载，或网络被挡。",
                           time.monotonic() - started)
    except Exception as exc:             # noqa: BLE001
        return ProbeResult(False, f"{exc.__class__.__name__}: {exc}",
                           time.monotonic() - started)

    seconds = time.monotonic() - started
    if not (text or "").strip():
        # 通了但没内容，最常见的原因是模型名写错（服务照样回 200）
        return ProbeResult(False, "服务有回应，但内容是空的 —— 先检查模型名", seconds)
    return ProbeResult(True, f"{seconds:.1f}s，返回 {len(text)} 字", seconds)


__all__ = [
    "AiCompleter", "NullCompleter", "MockCompleter", "OpenAiCompleter",
    "Preset", "PRESETS", "PROVIDER_MOCK", "PROVIDER_OPENAI",
    "DEFAULT_SYSTEM", "build_messages", "clean_code", "endpoint_of",
    "COMPLETION_SYSTEM", "build_completion_messages", "index_pattern_hint",
    "index_pattern_seed", "ensure_seed_line",
    "create_completer", "settings_hint", "mock_reply",
    "ProbeResult", "probe", "PROBE_PROMPT",
    # FIM
    "FIM_AUTO", "FIM_SERVER", "FIM_TOKEN", "FIM_TOKEN_FAMILIES",
    "FIM_FAMILY_CHOICES", "FIM_STRATEGY_CHOICES", "FimUnsupported",
    "fim_family_of", "fim_tokens_for", "completions_endpoint_of", "clean_fim",
]
