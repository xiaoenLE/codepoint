"""依赖倒置用的抽象接口（Protocol）。

上层（app.py 组装根）负责把 platform_win / ui 的具体实现塞进来。
只声明 services **真正需要**的能力，不贪多——接口越小，耦合越松。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from ..core.events import ImeSnapshot, WindowInfo
from ..core.models import Settings


@runtime_checkable
class TextInjector(Protocol):
    """向焦点窗口写入按键。"""

    def send_text(self, text: str) -> None:
        """注入一段文本（绕过输入法）。逐字符，等于真人一个个敲。"""

    def send_block(self, text: str, *, always_paste: bool = False) -> None:
        """**整段**插入一块内容（AI 结果 / 快捷输入的补全内容）。

        默认实现可以和 ``send_text`` 一样，但具体实现会按设置在"剪贴板粘贴"
        和"逐字符写入"之间选 —— 逐字符在"边敲边联想"的编辑器里会被自动补全、
        自动缩进插一脚，写出来的代码是歪的（用户报障："有的软件他写入会索引
        其他的，会导致写入不正常"）。

        ``always_paste=True``：哪怕是几个字也走粘贴（AI 结果用这个 —— 一行
        补全照样会被联想打断）。默认 False 时，短的单行内容仍走逐字符
        （快捷输入 `:` → `:=` 这种，为两个字绕一圈剪贴板太重）。

        ⚠ 标点替换**不走这里**，它继续用 ``send_text`` 逐字符 —— 那本来就是
        "把刚敲的键换个写法"，走剪贴板既慢又会频繁动剪贴板。
        """

    def send_backspaces(self, count: int) -> None:
        """发送若干次退格。"""

    def send_ime_guard(self) -> None:
        """补一个"无名键"，告诉输入法**刚才那下 Shift 是组合键、不是单击**。

        标点替换会吞掉 Shift+符号 那个键；中文输入法看不到它，就会把 Shift
        当成"单击切中英文"（微软拼音默认如此），于是每打一个 `:` `<` `?`
        中英文就被切走一次。实现与实测见
        ``platform_win/input_simulator.send_ime_guard``。
        """


@runtime_checkable
class WindowProbe(Protocol):
    """取当前前台窗口。"""

    def front(self) -> WindowInfo:
        ...

    def self_pid(self) -> int:
        """本进程 pid —— 用来避免"自己盯自己"造成死循环。"""
        ...


@runtime_checkable
class ImeProbe(Protocol):
    """取输入法状态。"""

    def state(self, hwnd: int) -> ImeSnapshot:
        ...


@runtime_checkable
class QuickActionNotifier(Protocol):
    """把"该显示/隐藏预览窗"这件事推给界面层。"""

    def show(self, action: object) -> None:
        """模板补全命中：显示这个片段的内容预览。"""

    def hide(self) -> None:
        ...

    def show_card(self, card: object) -> None:
        """显示一张预览卡（``ui.card.Card``）。

        类型写成 ``object`` 是因为 services 不该 import ui —— 这里只靠
        duck typing 传过去，界面层自己认。AI 的"生成中 / 预览 / 失败"三种
        状态都走这一个入口。
        """


@runtime_checkable
class ClipboardPort(Protocol):
    """读写剪贴板文本 + 取变更序号。

    只声明 selection 真正要用的三件事。序号比内容比较便宜得多，
    "复制到底成功了没有"就靠它判断。
    """

    def get_text(self) -> Optional[str]:
        ...

    def get_selected_text(self) -> Optional[str]:
        """读复制结果。编辑器代复制的整行按空选区处理。"""
        ...

    def set_text(self, text: str) -> bool:
        ...

    def sequence_number(self) -> int:
        ...


@runtime_checkable
class SelectionCopier(Protocol):
    """替用户按一次 Ctrl+C。拿选中文本唯一通用的手段。"""

    def wait_ready(self) -> bool:
        """等修饰键松开（热键触发时 Ctrl/Alt 还按着）。

        返回 False = 用户一直没松手，此时**不许发** Ctrl+C ——
        发出去会变成 Ctrl+Alt+C，既拿不到内容，又把键盘状态搅乱。
        """
        ...

    def copy_selection(self) -> None:
        ...

    def select_context(self, count: int, *, below: bool) -> bool:
        """以光标为锚点向上或向下选取文字。"""
        ...

    def move_context_endpoint(self, count: int, *, left: bool) -> bool:
        """保持临时选区的锚点，移动活动端。"""
        ...

    def collapse_selection(self, *, to_start: bool) -> bool:
        """用方向键折叠到选区的指定一端。"""
        ...

    def caret_position(self) -> Optional[tuple[int, int]]:
        ...


class NullClipboard:
    """什么都不记的剪贴板（无界面 / 测试用）。"""

    def __init__(self) -> None:
        self.text: Optional[str] = None
        self.seq = 0

    def get_text(self) -> Optional[str]:        # noqa: D102
        return self.text

    def get_selected_text(self) -> Optional[str]:
        return self.get_text()

    def set_text(self, text: str) -> bool:      # noqa: D102
        self.text = text
        self.seq += 1
        return True

    def sequence_number(self) -> int:           # noqa: D102
        return self.seq


class NullCopier:
    """不按任何键（测试用 —— 需要"什么都没选中"的场景就靠它）。"""

    def wait_ready(self) -> bool:               # noqa: D102
        return True

    def copy_selection(self) -> None:           # noqa: D102
        return None

    def select_context(self, count: int, *, below: bool) -> bool:
        return False

    def move_context_endpoint(self, count: int, *, left: bool) -> bool:
        return False

    def caret_position(self) -> Optional[tuple[int, int]]:
        return None


@runtime_checkable
class Clock(Protocol):
    """时间源——测试时可注入假时钟，让超时逻辑可验证。"""

    def now(self) -> float:
        ...


@runtime_checkable
class HotkeyBinder(Protocol):
    """全局热键绑定。"""

    def bind(self, spec: str) -> bool:
        ...

    def unbind(self) -> None:
        ...


class NullNotifier:
    """什么都不做的通知器——无界面模式/测试用。"""

    def show(self, action: object) -> None:      # noqa: D102
        return None

    def hide(self) -> None:                      # noqa: D102
        return None


class NullImeProbe:
    """永远回答"不知道"的输入法探针。"""

    def state(self, hwnd: int) -> ImeSnapshot:   # noqa: D102
        return ImeSnapshot()


def settings_snapshot(settings: Settings) -> Settings:
    """留一个显式的取值入口，方便以后换成"带锁的配置提供者"。"""
    return settings
