"""全局低级键盘钩子（WH_KEYBOARD_LL）。

职责单一：**把每一次物理按键翻译成 core.events.KeyPress 交给回调，并可按回调要求吞掉该键。**
不包含任何业务判断（是否白名单、要不要替换标点，全在 services 层）。

三个必须注意的工程细节：
1. 钩子回调运行在安装它的那个线程上，所以必须由 MessageWindow 的线程安装 → `install()` 内部用 `call()`。
2. 回调**必须足够快**（默认 LowLevelHooksTimeout 约 300 ms），超时会被系统静默摘掉钩子。
   因此回调里只做查表级工作，进程/输入法查询都带缓存。
3. **修饰键状态必须自己跟踪，绝不能依赖 GetKeyboardState** —— 它返回的是
   *调用线程* 的键盘状态，在钩子回调里经常拿不到刚按下的 Shift，后果是
   Shift+, 被解析成 `,`、Shift+; 解析成 `;`……然后被标点替换吞掉再注入
   错误的字符。用户实测报障："`<` 和 `,`、`.` 和 `>`，跟 `;` `:` 一样，
   都只能输出一个字符"。做法：事件流跟踪 + GetAsyncKeyState 交叉校验，
   字符解析一律用这份自己维护的状态。
4. **业务回调（decide）必须在锁外调用**。decide 会触发按键注入（SendInput），
   而 SendInput 会在同一个调用栈里把注入的按键重新喂回本钩子 —— 同一个线程
   二次进入回调。此时若第一层还握着 ``self._lock``，就自己把自己锁死了。
   卡住的是消息线程，而钩子/托盘/热键/剪贴板都寄生在它上面，于是
   "第一次替换之后所有功能一起失效"，退出时 join 也要等到超时。
5. **修饰键状态必须能和物理状态对账**（``_heal_command_modifiers`` /
   ``heal_stuck_modifiers``）。我们靠事件流记账，可有些键盘事件**我们根本
   收不到** —— 最典型的是 UAC / Ctrl+Alt+Del / 锁屏那套**安全桌面**：桌面
   一换，钩子就不被调用，用户在那上面松开的 Ctrl/Alt/Win/Shift 我们就永远
   等不到 key-up。状态位一旦"卡在按下"，``vk_to_char`` 会对**所有**按键返回
   None（Ctrl 组合不产生字符），表现为"程序看着一切正常，就是一个字都不转换，
   重启又好了"（用户报障原话）。详见 ``_heal_command_modifiers`` 的注释。
6. **钩子可能被系统悄悄摘掉**。Windows 对超时的低级钩子不做任何通知，
   直接移除（见第 2 条）。所以要有 ``probe`` / ``reinstall``：探活失败就
   就地重挂，不必让用户重启程序。
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import POINTER, cast
from typing import Callable, Dict, List, Optional

from ..core.events import KeyPress, Modifiers
from . import input_simulator
from .keylayout import (
    vk_to_char,
    VK_CAPITAL,
    VK_CONTROL,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    VK_SHIFT,
)
from .message_loop import MessageWindow
from .winapi import (
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    LLKHF_LOWER_IL_INJECTED,
    LPARAM,
    WH_KEYBOARD_LL,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    HOOKPROC,
    WPARAM,
    kernel32,
    user32,
)

_POINTER_KBD = POINTER(KBDLLHOOKSTRUCT)

# 键盘状态字节里的两个位
_DOWN_BIT = 0x80        # "这个键正按着"
_TOGGLE_BIT = 0x01      # "这个锁定键处于生效状态"（CapsLock）

_MOD_VKS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT,
            VK_CONTROL, VK_LCONTROL, VK_RCONTROL,
            VK_MENU, VK_LMENU, VK_RMENU,
            VK_LWIN, VK_RWIN}

# 通用键位由哪些"物理键位"组成：通用位 = 左右的"或"。
# 同步状态时必须**先读齐再统一写**，否则通用位会被同批的 L/R 键位来回覆盖
#（_MOD_VKS 是集合，顺序不保证 —— 踩过：VK_LSHIFT 刚把 VK_SHIFT 置上，
#  轮到 VK_SHIFT 自己时又被异步状态里的"未按下"清掉）。
_GENERIC_PARTS = {
    VK_SHIFT: (VK_LSHIFT, VK_RSHIFT),
    VK_CONTROL: (VK_LCONTROL, VK_RCONTROL),
    VK_MENU: (VK_LMENU, VK_RMENU),
}

# 能"卡在按下"的物理键位（通用位由 _GENERIC_PARTS 推导，不必单独列）。
# 左/右 Shift 也在内 —— 只在"卡了很久 + 物理确实没按"时才清（见 heal_stuck_modifiers）。
_HEALABLE_VKS = (VK_LSHIFT, VK_RSHIFT,
                 VK_LCONTROL, VK_RCONTROL,
                 VK_LMENU, VK_RMENU,
                 VK_LWIN, VK_RWIN)

# 「命令修饰键」= Ctrl / Alt / Win。它们只被用来回答一个问题：
# "这一下是不是快捷键？"（是就不插手，见 services/punctuation.py）。
# 而这个问题**物理状态就能准确回答**，所以每次按键都拿物理状态对一次账，
# 不需要"卡够多久"的阈值 —— 真按着的键在 GetAsyncKeyState 里永远是在按下状态。
# 对比 Shift：它决定"打出来的是 : 还是 ;"，必须信事件流（见 _sync_modifiers_from_async），
# 所以 Shift 不在这份名单里。
_COMMAND_KEYS = (VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)

# Shift 兜底自愈的阈值：卡住这么久、而且物理上确实没按，才敢清掉。
# 取 6 秒的理由：真人不会按住 Shift 6 秒不松（真按着的时候物理状态是"按下"，
# 根本不会被清）；而"回调滞后 6 秒"这种场景，整个消息线程早就废了。
_STUCK_MODIFIER_SECONDS = 6.0

#: 探活等待时长：发一个无害键，等钩子回调认领它。正常是微秒级，
#: 300ms 已经宽到不可能误判（回调超时阈值本身才 300ms）。
_PROBE_TIMEOUT = 0.3


# 回调签名：返回 True 表示"吞掉这个键，不要传给后面的程序"
DecideFn = Callable[[KeyPress], bool]

log = logging.getLogger("codepoint.hook")


class KeyboardHook:
    """低级键盘钩子的生命周期管理。"""

    def __init__(self, message_window: MessageWindow, decide: DecideFn,
                 *, clock=None, probe_sender: Optional[Callable[[], None]] = None) -> None:
        self._mw = message_window
        self._decide = decide
        self._hook: Optional[int] = None
        self._proc = HOOKPROC(self._hook_proc)     # 必须持引用
        self._ks = bytearray(256)                  # 自己维护的键盘状态（见模块 docstring 第 3 条）
        self._swallowed: set = set()               # 已吞掉 key-down、还在等 key-up 的键
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        # 每个修饰键"从什么时候开始被记成按下"（用于卡键自愈，见 heal_stuck_modifiers）
        self._mod_down_since: Dict[int, float] = {}
        self._now = clock or time.monotonic
        # —— 探活（见 probe / reinstall）——
        self._probe_sender = probe_sender or input_simulator.send_probe_key
        self._probe_seen = threading.Event()
        self._last_event_ts = self._now()
        self._events_seen = 0
        self._reinstalls = 0
        self._seed_state()

    # ------------------------------------------------------------ 属性

    @property
    def installed(self) -> bool:
        return self._hook not in (None, 0)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def events_seen(self) -> int:
        """钩子回调累计被调用次数（含注入的按键）。"""
        return self._events_seen

    @property
    def reinstalls(self) -> int:
        """探活失败后自动重挂的次数。"""
        return self._reinstalls

    def event_age(self) -> float:
        """距上一次钩子回调过了多少秒。钩子被摘掉时它会一直涨。"""
        return self._now() - self._last_event_ts

    # ------------------------------------------------------------ 生命周期

    def install(self) -> bool:
        """安装钩子（在消息线程上执行）。"""
        if self.installed:
            return True
        try:
            ok = bool(self._mw.call(self._install_on_thread))
        except Exception as exc:      # noqa: BLE001
            self._last_error = f"安装钩子失败：{exc}"
            return False
        if ok:
            self._seed_state()        # 安装后再播种一次（此时线程状态最完整）
        return ok

    def _install_on_thread(self) -> bool:
        if self._hook:
            return True
        hmod = kernel32.GetModuleHandleW(None)
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
        if not handle:
            from .winapi import error_text, last_error

            self._last_error = f"SetWindowsHookExW 失败：{error_text(last_error())}"
            return False
        self._hook = int(handle)
        self._last_error = None
        return True

    def uninstall(self) -> None:
        hook = self._hook
        if not hook:
            return
        self._hook = None
        with self._lock:
            self._swallowed.clear()
            self._mod_down_since.clear()
        try:
            user32.UnhookWindowsHookEx(hook)
        except Exception:      # noqa: BLE001
            pass

    def reinstall(self) -> bool:
        """掉线自救：摘掉再挂一次，让用户不必重启整个程序。

        典型场景 —— Windows 对超时的低级钩子**不做任何通知**就直接移除
        （见模块 docstring 第 2、6 条）。这时前台探测、界面、托盘全都正常，
        只有"按键再也不经过我们"，症状就是"能识别到程序，就是不转换"。
        """
        self.uninstall()
        ok = self.install()
        if ok:
            self._reinstalls += 1
            log.warning("键盘钩子已重挂（第 %d 次）", self._reinstalls)
        else:
            log.error("键盘钩子重挂失败：%s", self._last_error)
        return ok

    def probe(self, timeout: float = _PROBE_TIMEOUT) -> bool:
        """探活：发一个无害的注入键，看钩子回调认不认领它。

        返回 True = **钩子活着**（回调确实被调用了）。这也是唯一可靠的探法：
        Windows 没有任何"查询钩子是否仍挂着"的 API，超时被摘掉也不通知。

        ⚠ 会往当前焦点程序注入一个 0xE8（未分配的虚拟键码，无字符、无默认
          处理，见 ``input_simulator.send_probe_key``）。所以调用方必须**低频 +
          有嫌疑时才探**（见 ``services/hook_health.py``），不能逢 tick 就探。
        """
        if not self.installed:
            return False
        self._probe_seen.clear()
        try:
            self._probe_sender()
        except Exception:      # noqa: BLE001
            # 发不出去（例如当前在安全桌面上）→ 这次判断不算数，按"活着"处理，
            # 免得把"发不出去"误诊成"钩子死了"而反复重挂。
            log.exception("探活按键注入失败")
            return True
        return self._probe_seen.wait(timeout)

    def reset_state(self) -> None:
        """丢掉按键/修饰键的跟踪状态（切换总开关、失焦时调用）。"""
        with self._lock:
            self._swallowed.clear()
            self._seed_state()

    # ------------------------------------------------------------ 键盘状态跟踪
    #
    # 为什么不用 GetKeyboardState：它返回**调用线程**的键盘状态，
    # 在 LL 钩子回调里经常滞后/缺失（尤其 Shift）—— 这是"Shift 组合的符号
    # 全都打不出来"这一 bug 的根因。这里改为：
    #   · 事件流跟踪：钩子能看到每一个按键的按下/抬起，直接记账；
    #   · GetAsyncKeyState 交叉校验：解析字符前用物理异步状态兜底，
    #     修复"个别事件被系统跳过"造成的失真；
    #   · CapsLock 切换位只能靠事件流（GetAsyncKeyState 的低位不是切换状态）。

    def _seed_state(self) -> None:
        """用当前系统状态初始化跟踪数组。

        两类信息来源刻意分开：
        - **修饰键**只认 ``GetAsyncKeyState``（物理异步状态）。
          ``GetKeyboardState`` 的修饰字节在某些环境带杂散位
          （实测返回过 ``VK_SHIFT=0x01``），照抄会污染解析；
        - **CapsLock 切换位**只认 ``GetKeyboardState``——
          ``GetAsyncKeyState`` 的低位是"自上次调用以来按过没有"，不是切换状态。
        """
        ks = bytearray(256)
        buf = (ctypes.c_char * 256)()
        try:
            if user32.GetKeyboardState(buf):
                ks[VK_CAPITAL] = bytes(buf)[VK_CAPITAL] & _TOGGLE_BIT
        except Exception:      # noqa: BLE001
            pass
        for vk in _MOD_VKS:
            try:
                if user32.GetAsyncKeyState(vk) & 0x8000:
                    ks[vk] = _DOWN_BIT
            except Exception:      # noqa: BLE001
                pass
        self._sync_generic_bits(ks)
        self._ks = ks
        self._mod_down_since.clear()
        self._track_down_since(ks)

    @staticmethod
    def _sync_generic_bits(ks: bytearray) -> None:
        """通用键位（VK_SHIFT 等）= 左/右物理键位的"或"。"""
        for generic, parts in _GENERIC_PARTS.items():
            down = any(ks[part] & _DOWN_BIT for part in parts)
            if down:
                ks[generic] |= _DOWN_BIT
            else:
                ks[generic] &= 0x7F

    def _track_down_since(self, ks: bytearray) -> None:
        """让"从什么时候开始按下"跟着状态位走。

        ``setdefault`` 是刻意的：只在**本次事件之前没记过**时才盖章。
        通用位（VK_CONTROL 等）由左右键推出来，也顺带盖章 —— 否则
        "通用位卡住"这种漏网的情况没法自愈。
        """
        now = self._now()
        for vk in _MOD_VKS:
            if ks[vk] & _DOWN_BIT:
                self._mod_down_since.setdefault(vk, now)
            else:
                self._mod_down_since.pop(vk, None)

    def _async_down(self, vk: int) -> bool:
        """``GetAsyncKeyState``：这个键**物理上**是不是按着。

        读不到时返回 True（"当作按着"）—— 宁可不清、也不要凭猜去清状态。
        """
        try:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:      # noqa: BLE001
            return True

    def _update_state(self, vk: int, is_down: bool) -> None:
        """把一个按键事件记入状态数组（所有事件都要过这里，包括注入的）。"""
        ks = self._ks
        if vk in _MOD_VKS:
            ks[vk] = _DOWN_BIT if is_down else 0
            self._sync_generic_bits(ks)
            self._track_down_since(ks)
        elif vk == VK_CAPITAL and is_down:
            # 切换键：每按一次翻转一次。只翻"切换位"，绝不折进 Shift
            #（否则 ; 会被当成 : —— 修过一次的 bug）
            ks[VK_CAPITAL] ^= _TOGGLE_BIT

    def _sync_modifiers_from_async(self) -> None:
        """用 GetAsyncKeyState（物理异步状态）**补漏**，不覆盖流式跟踪。

        只补不减 —— 这一点是关键的，反过来会直接毁掉上档符号：
        钩子回调**可能滞后于物理按键**（回调排队、系统忙、钩子刚装上）。
        极端情况：用户按下 Shift 再按 ``,`` 并迅速松开，等回调真正跑起来时
        物理 Shift 已经抬起了。若拿物理状态去覆盖流式跟踪，Shift 就被抹掉，
        ``Shift+,`` 被解析成 ``,`` → 命中标点规则 → 吞掉 + 注入 ``,``，
        于是 ``<`` 永远打不出来（用户原话："`,` `<`、`.` `>`、跟 `;` `:` 一样，
        都只能输出一个字符"）。

        所以规则是：
          · 流式说"按下" → 信流式（事件顺序才是这次按键的真相）；
          · 流式说"没按"、物理说"按着" → 补上（说明漏收了 key-down）；
          · 两边都说没按 → 没按。

        修饰键的 key-up 我们从不吞（decide 对修饰键返回 False），
        所以"流式卡在按下"的风险可以接受；反过来抹掉 Shift 的代价则大得多。
        """
        ks = self._ks
        physical = {}
        for vk in _MOD_VKS:
            try:
                physical[vk] = bool(user32.GetAsyncKeyState(vk) & 0x8000)
            except Exception:      # noqa: BLE001
                return              # 拿不到物理状态就保持流式跟踪的现状
        for vk, down in physical.items():
            if down and not (ks[vk] & _DOWN_BIT):
                ks[vk] = _DOWN_BIT      # 只补
        self._sync_generic_bits(ks)
        self._track_down_since(ks)

    # ------------------------------------------------------------ 卡键自愈
    #
    # 背景（模块 docstring 第 5 条）：修饰键的状态靠事件流记账，而**安全桌面**
    # （UAC 提示框、Ctrl+Alt+Del、Win+L 锁屏）上我们的钩子根本不被调用。
    # 用户在那上面松开的 Ctrl/Alt/Win，我们就永远等不到 key-up。
    #
    # 后果不是"某个符号打错"，而是**整个功能静默失效**：
    #   Ctrl 位卡在按下 → platform_win.keylayout.vk_to_char 对**任何**按键都
    #   返回 None（"Ctrl 组合不产生字符"）→ services/punctuation 拿不到 char
    #   → 所有按键原样放行。界面、前台探测、托盘全都正常，就是一个字都不转换，
    #   重启进程（状态数组重建）又好了 —— 正是用户报障的那个现象。
    #
    # 两条自愈路线，故意的：命令键每次按键都对账（零风险、立刻生效），
    # Shift 只在"卡够久 + 物理没按"时兜底（见常量区的阈值说明）。

    def _heal_command_modifiers(self) -> List[int]:
        """Ctrl/Alt/Win 的"卡在按下"按物理状态就地清掉（**调用方须持锁**）。

        为什么它们可以每键对账、而 Shift 不行：这两个问题的性质完全不同 ——

          · Ctrl/Alt/Win 只用来判断"是不是快捷键"（``Modifiers.has_command``），
            真按着的时候 ``GetAsyncKeyState`` 一定说按着，所以"物理说没按"
            可以无条件采信，不存在"回调滞后导致误判"的问题；
          · Shift 决定"这次打出来的是 ``:`` 还是 ``;``"，必须信事件流
            （滞后时物理状态反而会骗人，见 :meth:`_sync_modifiers_from_async`）。

        返回被清掉的键（供日志/统计用）。
        """
        ks = self._ks
        healed = [vk for vk in _COMMAND_KEYS
                  if (ks[vk] & _DOWN_BIT) and not self._async_down(vk)]
        if not healed:
            return []
        for vk in healed:
            ks[vk] = 0
        self._sync_generic_bits(ks)      # 通用位（VK_CONTROL 等）跟着左右键走
        self._track_down_since(ks)
        return healed

    def heal_stuck_modifiers(self, max_age: float = _STUCK_MODIFIER_SECONDS) -> List[int]:
        """定时兜底：清掉"卡住很久、物理上确实没按"的修饰键（**含 Shift**）。

        与 :meth:`_heal_command_modifiers` 的区别是**有阈值**：Shift 必须已经
        被记成按下超过 ``max_age`` 秒才会被清，避免"回调滞后于物理按键"那一瞬
        把用户真按着的 Shift 抹掉（那会毁掉全部上档符号，见 6.14）。
        真按着 Shift 六秒以上也不会被清 —— 物理状态那时说的是"按着"。

        本方法自己加锁，供定时器（非钩子线程）调用。
        """
        with self._lock:
            now = self._now()
            ks = self._ks
            healed = []
            for vk in _HEALABLE_VKS:
                if not (ks[vk] & _DOWN_BIT):
                    continue
                if now - self._mod_down_since.get(vk, now) < max_age:
                    continue
                if self._async_down(vk):
                    continue
                healed.append(vk)
            if not healed:
                return []
            for vk in healed:
                ks[vk] = 0
            self._sync_generic_bits(ks)
            self._track_down_since(ks)
            return healed

    def _state_bytes(self) -> bytes:
        return bytes(self._ks)

    def _snapshot_modifiers(self) -> Modifiers:
        ks = self._ks
        return Modifiers(
            shift=bool(ks[VK_SHIFT] & _DOWN_BIT)
            or bool(ks[VK_LSHIFT] & _DOWN_BIT)
            or bool(ks[VK_RSHIFT] & _DOWN_BIT),
            ctrl=bool(ks[VK_CONTROL] & _DOWN_BIT)
            or bool(ks[VK_LCONTROL] & _DOWN_BIT)
            or bool(ks[VK_RCONTROL] & _DOWN_BIT),
            alt=bool(ks[VK_MENU] & _DOWN_BIT)
            or bool(ks[VK_LMENU] & _DOWN_BIT)
            or bool(ks[VK_RMENU] & _DOWN_BIT),
            win=bool(ks[VK_LWIN] & _DOWN_BIT) or bool(ks[VK_RWIN] & _DOWN_BIT),
        )

    # ------------------------------------------------------------ 回调

    def _hook_proc(self, n_code, w_param, l_param):
        # ⓪ 钩子"还在被调用"这件事本身就是活着的证据 —— 探活只看这一个时间戳。
        #    连 n_code < 0 的分支都算：系统确实把控制权交到我们手里了。
        self._events_seen += 1
        self._last_event_ts = self._now()

        # n_code < 0 时必须原样传递，不能做任何处理
        if n_code < 0:
            return user32.CallNextHookEx(None, n_code, WPARAM(w_param), LPARAM(l_param))

        try:
            info = cast(l_param, _POINTER_KBD).contents
            vk = int(info.vkCode)
            flags = int(info.flags)
            msg = int(w_param)

            is_injected = bool(flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED))
            is_down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = msg in (WM_KEYUP, WM_SYSKEYUP)

            if is_injected and vk == input_simulator.VK_IME_GUARD:
                # 探活键（KeyboardHook.probe 发出来的）—— 认领它
                self._probe_seen.set()

            # ① 先记账（**所有事件都要过这里，包括注入的**——修饰键按下/抬起是
            #    全局事实，目标程序同样看得到；我们自己的注入不含修饰键，无影响）
            with self._lock:
                self._update_state(vk, is_down)

            if is_injected:
                # 自己注入的事件：一律放行，不参与业务
                return user32.CallNextHookEx(None, n_code, WPARAM(w_param), LPARAM(l_param))

            swallow = False
            with self._lock:
                # 之前吞掉了 key-down，对应的 key-up 也要吞掉，否则目标程序会收到孤儿抬起
                if is_up and vk in self._swallowed:
                    self._swallowed.discard(vk)
                    return 1

            if is_down:
                with self._lock:
                    # ② 解析前用物理状态兜底一次（防个别事件被系统跳过）
                    self._sync_modifiers_from_async()
                    # ②' 再把"卡在按下"的 Ctrl/Alt/Win 按物理状态清掉。
                    #     顺序很重要：必须在解析字符**之前**，否则本按键自己
                    #     就白挨一下（char=None → 不转换）。
                    healed = self._heal_command_modifiers()
                    scan = int(info.scanCode)
                    event = KeyPress(
                        vk=vk,
                        scan_code=scan,
                        is_down=True,
                        # ③ 字符解析用**自己维护的状态**，绝不依赖 GetKeyboardState
                        char=vk_to_char(vk, scan, key_state=self._state_bytes()),
                        modifiers=self._snapshot_modifiers(),
                    )
                if healed:
                    log.info("清掉卡住的修饰键：%s（物理上没按，判定为漏收 key-up）",
                             ", ".join(f"0x{h:02X}" for h in healed))

                # ④ decide **必须在锁外调用**。
                #
                #    这条是用真机死机换来的教训：decide 会触发按键注入
                #    （SendInput），而 SendInput 会在**当前调用栈里**把注入的
                #    按键重新喂给低级钩子 —— 也就是同一个线程再次进入本回调。
                #    那时第一层的锁还没放，第二次 acquire 同一个非可重入 Lock
                #    → 自己把自己锁死。
                #
                #    后果是毁灭性的：卡住的是**消息线程**，而钩子、托盘、热键、
                #    剪贴板全都寄生在它上面 —— 表现为"第一次标点替换之后，
                #    所有功能一起失效"，退出时 join 也要等到超时（退出卡顿）。
                try:
                    swallow = bool(self._decide(event))
                except Exception:      # noqa: BLE001 —— 业务异常绝不拖垮钩子
                    log.exception("按键决策回调异常，已放行（vk=0x%02X）", vk)
                    swallow = False
                if swallow:
                    with self._lock:
                        self._swallowed.add(vk)
            if swallow:
                return 1
        except Exception:      # noqa: BLE001
            # 绝不能默默 pass：这里吞掉的异常会让"功能全失效"变成无头案
            log.exception("钩子回调异常")

        return user32.CallNextHookEx(None, n_code, WPARAM(w_param), LPARAM(l_param))


__all__ = ["KeyboardHook", "DecideFn"]
