"""内置默认数据：标点映射表、快捷输入骨架、常见编程环境的程序名。

标点表覆盖两部分：
  1. Unicode **U+FF01–U+FF5E**（即"全角 ASCII"整段，映射关系为 ``code - 0xFEE0``）；
  2. 若干**排版标点**（弯引号、破折号、省略号…）与**中文表意标点**（。、、《》…），
     它们不在上面那段区间里，必须单独列出来。

这些都只是"默认值"，用户可以在界面上增删改；改动会落到 settings.json。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .models import (
    AppItem,
    DETECT_CHAR,
    DETECT_WORD,
    PunctuationRule,
    QuickAction,
)

# ---------------------------------------------------------------- 全角 / 半角

FULLWIDTH_OFFSET = 0xFEE0
_IDEOGRAPHIC_SPACE = "\u3000"

# U+FF01..U+FF5E 之外，码点助手 表里还包含的排版标点 → 半角
#
# ⚠ 注意区分：中文输入法按 "." 打出的是**表意句号 U+3002「。」**，
#   而不是全角句点 U+FF0E「．」；按 "\\" 打出的是「、」；按 Shift+, 打出「《」。
#   这些"中文专用标点"不在 U+FF01–FF5E 区间里，必须单独列出来（见 CJK_PUNCTUATION）。
EXTRA_PUNCTUATION: Dict[str, str] = {
    "\u2010": "-",   # ‐ hyphen
    "\u2011": "-",   # ‑ non-breaking hyphen
    "\u2012": "-",   # ‒ figure dash
    "\u2013": "-",   # – en dash
    "\u2014": "-",   # — em dash
    "\u2018": "'",   # ‘
    "\u2019": "'",   # ’
    "\u201a": "'",   # ‚
    "\u201c": '"',   # “
    "\u201d": '"',   # ”
    "\u201e": '"',   # „
    "\u2026": ".",   # …
    "\u2032": "'",   # ′
    "\u2035": "`",   # ‵
    "\u2039": "<",   # ‹
    "\u203a": ">",   # ›
    "\u00b7": ".",   # · 中点
}

# 中文输入法专用的表意标点（**仅用于文本转换**，例如剪贴板净化）。
#
# 为什么不放进"标点规则"里？因为规则表的语义是"某个**半角英文字符**是否要强制转换"，
# 而 。、《》 这些是全角字符、不是英文字符，放进界面清单会让用户困惑。
# 但它们确实需要参与"整段文本"的正反向转换，所以单独维护。
#
# 刻意**不含 U+3000 表意空格**：它常被用来做中文段落首行缩进，
# 一旦在剪贴板里被换成半角空格，中文文档排版会被破坏——收益远小于风险。
CJK_PUNCTUATION: Dict[str, str] = {
    "\u3002": ".",   # 。 表意句号
    "\u3001": ",",   # 、 顿号
    "\u3003": '"',   # 〃 同上符号
    "\u3008": "<",   # 〈
    "\u3009": ">",   # 〉
    "\u300a": "<",   # 《
    "\u300b": ">",   # 》
    "\u300c": '"',   # 「
    "\u300d": '"',   # 」
    "\u300e": "'",   # 『
    "\u300f": "'",   # 』
    "\u3010": "[",   # 【
    "\u3011": "]",   # 】
    "\u301c": "~",   # 〜 波浪破折号
    "\u301d": '"',   # 〝
    "\u301e": '"',   # 〞
    "\uffe5": "$",   # ￥ 全角日元/人民币符号（IME 的 Shift+4）
    "\u00a5": "$",   # ¥ 半角日元符号
}

# 只有真正"能用键盘敲出来"的 ASCII 字符才值得做替换；
# 空格不算（全角空格单独处理，见 to_fullwidth）。
TYPEABLE_ASCII_PUNCTUATION = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

# 界面里默认规则的**排列顺序**（不是优先级，只是显示顺序）。
#
# 为什么不用 ASCII 码顺序：写代码最常用的标点应该排在前面（`, . ; ' [ ] / \ : ! ?`）。
# 照 ASCII 排序的话头十条全是 ! " # $ % & ' ( ) *，想找逗号得往下翻，很难用。
PUNCTUATION_ORDER = (
    ",", ".", ";", "'", "[", "]", "/", "\\", ":", "!", "?",
    "(", ")", "{", "}", "<", ">", "-", "_", "=", "+",
    '"', "@", "#", "$", "%", "&", "*", "^", "~", "|", "`",
)


def to_fullwidth(ascii_char: str) -> str | None:
    """半角 → 全角。仅覆盖可键入 ASCII 与空格。"""
    if not ascii_char or len(ascii_char) != 1:
        return None
    o = ord(ascii_char)
    if 0x21 <= o <= 0x7E:
        return chr(o + FULLWIDTH_OFFSET)
    if ascii_char == " ":
        return _IDEOGRAPHIC_SPACE
    return None


def to_halfwidth(char: str) -> str | None:
    """全角 → 半角。

    查三张表：U+FF01–FF5E 整段、排版标点表、中文表意标点表。
    """
    if not char or len(char) != 1:
        return None
    o = ord(char)
    if 0xFF01 <= o <= 0xFF5E:
        return chr(o - FULLWIDTH_OFFSET)
    if char == _IDEOGRAPHIC_SPACE:
        return " "
    half = EXTRA_PUNCTUATION.get(char)
    if half is not None:
        return half
    return CJK_PUNCTUATION.get(char)


#: 默认标点规则：(全角, 半角, 是否启用)。
#:
#: 这份是**实测筛过的一版**，不是"把 U+FF01–FF5E 全列一遍"。和前几版的区别：
#:
#:   · **指向同一个 ASCII 的变体默认不列**：四个破折号（‐ ‑ ‒ – —）都变 ``-``、
#:     四个引号（‘ ’ ” „）都变 ``"``。全列出来的话，规则页里几十行都指向同一个
#:     字符，想找要关的那条反而费劲（用户报障："默认的符号表有很多是重复的"）。
#:   · **写代码时很少用全角形式的默认停用**（＄％＆＊＾～｜｀ ，！？＜＞＼＇）：
#:     留着条目、默认关着，用户想要时拨一下就行，不用从头添加。
#:   · **中文表意标点（。、《》、【】）不进默认表**。以前它们活在剪贴板净化的
#:     兜底表里 —— 界面上没有开关，怎么关都还在转（用户报障："关闭转换他还是转换"）。
#:     现在要转就在规则页自己加一条，不加就完全不碰。
DEFAULT_RULE_SPECS: Tuple[Tuple[str, str, bool], ...] = (
    # 顺序沿用"写代码从常用到不常用"，和规则页显示顺序一致
    ("\uff0c", ",", True),    # ，
    ("\uff0e", ".", True),    # ． 全角句点
    ("\uff1b", ";", True),    # ；
    ("\uff07", "'", False),   # ＇
    ("\uff3b", "[", True),    # ［
    ("\uff3d", "]", True),    # ］
    ("\uff0f", "/", True),    # ／
    ("\uff3c", "\\", False),  # ＼
    ("\uff1a", ":", True),    # ：
    ("\uff01", "!", False),   # ！
    ("\uff1f", "?", False),   # ？
    ("\uff08", "(", True),    # （
    ("\uff09", ")", True),    # ）
    ("\uff5b", "{", True),    # ｛
    ("\uff5d", "}", True),    # ｝
    ("\uff1c", "<", False),   # ＜
    ("\uff1e", ">", False),   # ＞
    ("\uff0d", "-", True),    # －
    ("\uff3f", "_", True),    # ＿
    ("\uff1d", "=", True),    # ＝
    ("\uff0b", "+", True),    # ＋
    ("\uff02", '"', True),    # ＂
    ("\uff5c", "|", False),   # ｜
    ("\uff40", "`", False),   # ｀
)


def default_punctuation_rules() -> List[PunctuationRule]:
    """全新安装时的标点规则（见 ``DEFAULT_RULE_SPECS`` 里的取舍说明）。

    注意这里**不再**按 U+FF01–FF5E 机械生成：那会带出一堆"同一目标多个源"
    的变体，列表看着重复。想加字符就走规则页的「增加+」。
    """
    return [
        PunctuationRule(fullwidth=fw, halfwidth=half, enabled=enabled)
        for fw, half, enabled in DEFAULT_RULE_SPECS
    ]


def fullwidth_to_halfwidth_map(rules: List[PunctuationRule]) -> Dict[str, str]:
    """把规则列表编译成只含"已启用"项的词典，转换时 O(1) 查表。"""
    return {r.fullwidth: r.halfwidth for r in rules if r.enabled and r.fullwidth and r.halfwidth}


def text_conversion_map(rules: List[PunctuationRule]) -> Dict[str, str]:
    """**整段文本转换**用的全角→半角表（只含已启用规则）。

    ⚠ 以前这里会**无条件**把 ``CJK_PUNCTUATION`` 叠回来（``setdefault``），
    于是"规则关掉了、复制粘贴照样转" —— ``。`` ``、`` ``《`` 这批字符压根不在
    规则列表里，怎么关都关不掉（用户报障："关闭转换他还是转换"）。

    现在只认 enabled。想转换中文句号这类字符，在「标点符号」页自己加一条即可；
    默认表里不预置，免得用户以为关不掉。
    """
    return fullwidth_to_halfwidth_map(rules)


# ---------------------------------------------------------------- 快捷输入默认项


def default_quick_actions() -> List[QuickAction]:
    """默认快捷项：一组 ST（IEC 61131-3）代码骨架。

    每一条都能直接敲出来用，按"写 ST 时最常敲的顺序"排列。

    ⚠ 内容是按**作者实际在用的那套**固化下来的（v1.0.1 起）：骨架里不写多余的分号，
    而是把光标要落的位置留成空行 / 一个 `;`，敲 `if` + Tab 之后直接往下写即可。

    v1.0.3 起补全内容支持 ``${变量}``（日期/时间/用户名…，见 services/template.py），
    ``///`` 这条注释头就用上了 ``${author}`` / ``${datetime}``。
    """
    return [
        # ⚠ 这条**默认停用**，原因很实际：单字符触发会**永久劫持**那个字符 ——
        #   触发串是 `:`，而冒号恰恰要靠 Shift+; 才打得出来，于是按冒号永远
        #   变成 `:=`，CASE 标签 `0:`、变量声明 `x : INT;` 全部废掉
        #   （用户实测报障："只能打分号，冒号打不出来"）。
        #   想要 `:=` 快捷输入的话，在「快捷输入」页把这条勾上即可，
        #   代价是接受"受管程序里打不出单个冒号"。
        QuickAction(trigger=":", detect_mode=DETECT_CHAR, content=":=", enabled=False),
        QuickAction(
            trigger="if",
            detect_mode=DETECT_WORD,
            content="IF 1 THEN\n    ;\nEND_IF",
            enabled=True,
        ),
        QuickAction(
            trigger="ifelse",
            detect_mode=DETECT_WORD,
            content="IF 1 THEN\n    ;\nELSE\n    ;\nEND_IF",
            enabled=True,
        ),
        QuickAction(
            trigger="for",
            detect_mode=DETECT_WORD,
            content="FOR i := 0 TO 1  DO\n    ;\nEND_FOR",
            enabled=True,
        ),
        QuickAction(
            trigger="while",
            detect_mode=DETECT_WORD,
            content="WHILE 1 DO\n    \nEND_WHILE",
            enabled=True,
        ),
        QuickAction(
            trigger="case",
            detect_mode=DETECT_WORD,
            content="CASE step OF\n    0: \n\t;\n    1: \n\t;\nEND_CASE",
            enabled=True,
        ),
        QuickAction(
            trigger="///",
            detect_mode=DETECT_WORD,
            # 注释头：作者/创建时间用变量，**补全那一刻**才取系统值 ——
            # 所以每次插入都是当下的时间和当前登录用户，不用手填。
            content=("//==================================================\n"
                     "//内容: \n"
                     "//作者: ${author}\n"
                     "//创建时间: ${datetime}\n"
                     "//=================================================="),
            enabled=True,
        ),
        QuickAction(
            trigger="fb",
            detect_mode=DETECT_WORD,
            content="FUNCTION_BLOCK FB_\nVAR_INPUT\nEND_VAR\nVAR_OUTPUT\nEND_VAR\nVAR\nEND_VAR\n\n\n",
            enabled=True,
        ),
        QuickAction(
            trigger="fc",
            detect_mode=DETECT_WORD,
            content="FUNCTION FC_ : \nVAR_INPUT\nEND_VAR\nVAR\nEND_VAR\n\n\n",
            enabled=True,
        ),
    ]


# ---------------------------------------------------------------- 默认生效程序


def default_app_items() -> List[AppItem]:
    """首次运行时的预置程序：**记事本 + VS Code**。

    为什么留着记事本：Windows 上人人都有它，装好立刻能试 ——
    打开记事本敲个中文逗号，看会不会变成英文逗号，两三秒就能确认生效没有。

    其余程序（CODESYS、博途、GX Works…）点主页的蓝色「+」从"正在运行的程序"里挑即可，
    不必手输进程名。
    """
    return [
        AppItem(process_name="notepad.exe", display_name="记事本", enabled=True),
        AppItem(
            process_name="code.exe",
            display_name="manifest.json",
            path=r"C:\Users\xiaoen\AppData\Local\Programs\Microsoft VS Code\Code.exe",
            enabled=True,
        ),
    ]
