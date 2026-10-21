"""补全内容里的「动态变量」—— 把 ``${...}`` 换成**按下 Tab 那一刻**的真实值。

为什么要有这个
    代码片段的注释头通常要写日期、时间、作者。以前这些只能手打，或者
    当死文字写进片段里（那时间就永远是"保存片段的那天"）。现在片段里写
    ``${datetime}``，**补全的瞬间**才去取系统时间 —— 每次插入都是当下那一刻。

写法
    ``${名字}``    展开成当前值，例如 ``${datetime}`` → ``2026-10-08 11:39:36``
    ``${名字}``    中英文名等价：``${日期}`` 与 ``${date}`` 是同一样东西
    ``$$``         转义成一个字面 ``$``：``$${date}`` 会原样输出 ``${date}``
    ``${不认识}``  **不认识的名字原样保留**，绝不乱改用户代码

最后那条是这套东西的安全底线：``${}`` 在 JS / PHP 的模板字符串里也是合法语法，
本工具又常在 VS Code 里生效。只展开我们认得名字，才不会把用户的代码搞坏。

支持哪些名字见 ``VARIABLES``；界面上的「插入变量」按钮就是照着它生成的，
加一个新变量只要往那张表里加一行。
"""

from __future__ import annotations

import datetime as _dt
import getpass
import platform
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

#: 转义哨兵。用 NUL 包起来 —— 正常文本里不可能出现（输入框也打不出 NUL），
#: 于是可以放心地和"用户真的写了 $ "区分开。
_ESCAPE = "\x00$\x00"


# ---------------------------------------------------------------- 上下文


@dataclass(frozen=True)
class Context:
    """展开时用到的一组「当下值」。

    单独抽出来是为了**可测试**：测试里塞一个假时间/假用户名，
    结果就完全确定，不用去 mock 系统时钟。
    """

    now: _dt.datetime
    username: str = ""
    hostname: str = ""


def _read_username() -> str:
    """当前系统登录用户名。取不到就返回空串（变量展开成空，不报错）。"""
    try:
        name = getpass.getuser()
        if name:
            return name
    except Exception:      # noqa: BLE001 —— 环境缺 USERNAME 等，不影响主流程
        pass
    import os

    for key in ("USERNAME", "USER", "LOGNAME"):
        value = os.environ.get(key)
        if value:
            return value
    return ""


def _read_hostname() -> str:
    """计算机名。取不到就返回空串。"""
    try:
        return platform.node() or ""
    except Exception:      # noqa: BLE001
        return ""


def default_context(now: Optional[_dt.datetime] = None) -> Context:
    """取一组真实的当下值。``now`` 留给测试注入。"""
    return Context(
        now=now or _dt.datetime.now(),
        username=_read_username(),
        hostname=_read_hostname(),
    )


# ---------------------------------------------------------------- 变量表

#: 「示例值」用的固定上下文。
#:
#: 变量表里每个变量都写了一行 ``sample``（界面提示要显示"长什么样"）。
#: 示例值**必须**等于这个上下文下的真实渲染结果 —— 否则改了格式忘了改示例，
#: 界面上就会挂着一个骗人的例子。``tests/test_template.py`` 拿这条做断言。
SAMPLE_CONTEXT = Context(
    now=_dt.datetime(2026, 10, 8, 11, 39, 36),
    username="xiaoen",
    hostname="LAPTOP-XXXX",
)


@dataclass(frozen=True)
class Variable:
    """一个可用的变量。"""

    name: str                      #: 规范名（英文）—— ``${name}`` 用这个
    aliases: Tuple[str, ...]       #: 所有可用写法（含中文别名）
    label: str                     #: 按钮上的**短名**（界面一行要塞得下）
    desc: str                      #: 说明（提示文字里用）
    sample: str                    #: 示例值（必须 == build(SAMPLE_CONTEXT)）
    build: Callable[[Context], str]

    def placeholder(self) -> str:
        """插进文本里的样子，如 ``${datetime}``。"""
        return "${" + self.name + "}"


VARIABLES: Tuple[Variable, ...] = (
    # —— 日期时间 ——
    Variable("datetime", ("datetime", "日期时间", "now", "现在"),
             "日期时间", "日期 + 时间", "2026-10-08 11:39:36",
             lambda c: c.now.strftime("%Y-%m-%d %H:%M:%S")),
    Variable("date", ("date", "日期", "today", "今天"),
             "日期", "日期", "2026-10-08",
             lambda c: c.now.strftime("%Y-%m-%d")),
    Variable("time", ("time", "时间"),
             "时间", "时间", "11:39:36",
             lambda c: c.now.strftime("%H:%M:%S")),
    Variable("year", ("year", "年", "yyyy"),
             "年", "年（4 位）", "2026",
             lambda c: c.now.strftime("%Y")),
    Variable("month", ("month", "月"),
             "月", "月（2 位）", "10",
             lambda c: c.now.strftime("%m")),
    Variable("day", ("day", "日", "天"),
             "日", "日（2 位）", "08",
             lambda c: c.now.strftime("%d")),
    Variable("hour", ("hour", "时", "小时"),
             "时", "时（24 小时制）", "11",
             lambda c: c.now.strftime("%H")),
    Variable("minute", ("minute", "分", "分钟"),
             "分", "分", "39",
             lambda c: c.now.strftime("%M")),
    Variable("second", ("second", "秒"),
             "秒", "秒", "36",
             lambda c: c.now.strftime("%S")),
    # —— 系统信息 ——
    Variable("author", ("author", "作者", "user", "用户", "username", "用户名"),
             "用户名", "系统登录用户名", "xiaoen",
             lambda c: c.username),
    Variable("pc", ("pc", "电脑", "computer", "计算机", "计算机名",
                    "host", "hostname", "主机", "主机名"),
             "计算机名", "计算机名", "LAPTOP-XXXX",
             lambda c: c.hostname),
)

#: 别名（折叠大小写）→ 变量。ASCII 别名不区分大小写，中文别名天然无大小写。
_BY_ALIAS: Dict[str, Variable] = {}
for _var in VARIABLES:
    for _alias in _var.aliases:
        _BY_ALIAS[_alias.casefold()] = _var
del _var, _alias


def resolve(name: str) -> Optional[Variable]:
    """按名字找变量。找不到返回 None（调用方据此决定"原样保留"）。"""
    return _BY_ALIAS.get((name or "").strip().casefold())


# ---------------------------------------------------------------- 展开


def expand(text: str, *, context: Optional[Context] = None) -> str:
    """把 ``text`` 里的 ``${变量}`` 换成当前值。

    规则（顺序即语义）：
      1. 先把 ``$$`` 换成哨兵 —— 这样"转义出来的 $ "就不会被当成变量开头；
      2. 从左到右扫 ``${...}``，认得的名字才展开，不认得的原样留着；
      3. 最后把哨兵还原成字面 ``$``。

    文本里没有 ``$`` 时直接返回，连上下文都不取（补全热路径上的零开销分支）。
    """
    if not text or "$" not in text:
        return text or ""

    ctx = context or default_context()
    work = text.replace("$$", _ESCAPE)

    out: List[str] = []
    index = 0
    while True:
        start = work.find("${", index)
        if start < 0:                       # 没有更多变量了
            out.append(work[index:])
            break
        out.append(work[index:start])
        close = work.find("}", start + 2)
        if close < 0:                       # 少了右括号 —— 原样输出，不猜
            out.append(work[start:])
            break
        var = resolve(work[start + 2:close])
        out.append(var.build(ctx) if var is not None else work[start:close + 1])
        index = close + 1

    return "".join(out).replace(_ESCAPE, "$")


__all__ = ["Context", "Variable", "VARIABLES", "SAMPLE_CONTEXT",
           "resolve", "expand", "default_context"]
