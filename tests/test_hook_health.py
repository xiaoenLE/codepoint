"""钩子「静默失效」的自愈测试。

**背景（用户报障）**：

    能识别到当前程序、开关也开着、托盘也正常，就是一个字都不转换；
    退出重开又好了 —— 隔三差五来一次。

这类"看起来一切正常、就是没效果"的故障有两个来源，而且**都必须能自愈**：

① **修饰键状态卡在按下**。我们靠事件流记账，可 UAC 提示框 / Ctrl+Alt+Del /
   锁屏那套**安全桌面**上钩子根本不被调用 —— 用户在那儿松开的 Ctrl/Alt/Win
   我们永远等不到 key-up。状态位一卡住，``vk_to_char`` 会对**每一个**按键
   返回 None（Ctrl 组合不产生字符），于是全部按键原样放行。
② **钩子被系统摘掉**。Windows 对超时的低级钩子静默移除、不做任何通知
   （见 ``LowLevelKeyboardProc`` 文档），而且没有任何 API 能查询它还在不在。

所以这里钉死：卡住要能立刻复位、掉了要能探出来并就地重挂。
"""

from __future__ import annotations

import ctypes
import tempfile
import unittest
from contextlib import contextmanager

from codepoint.app import App
from codepoint.core.models import AppItem, PunctuationRule
from codepoint.core.config import build_default_settings
from codepoint.platform_win import keyboard_hook as kh
from codepoint.platform_win.keylayout import vk_to_char
from codepoint.platform_win.winapi import (
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
)
from codepoint.services.hook_health import HookHealthMonitor
from codepoint.services.pipeline import KeyboardPipeline

from .fakes import (
    FakeClock,
    FakeImeProbe,
    FakeInjector,
    FakeWindowProbe,
    RecordingNotifier,
    VK_OEM_1,
)

VK_LCONTROL = 0xA2
VK_LMENU = 0xA4
VK_LWIN = 0x5B
VK_LSHIFT = 0xA0


class _FakeUser32:
    """替身：只实现状态对账用到的 GetAsyncKeyState 和回调出口。"""

    def __init__(self, down=()) -> None:
        self.down = set(down)

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.down else 0

    def CallNextHookEx(self, *_args):
        return 0


@contextmanager
def patch_user32(down=()):
    original = kh.user32
    kh.user32 = _FakeUser32(down)
    try:
        yield kh.user32
    finally:
        kh.user32 = original


_KEEP: list = []          # 保住结构体内存，别让指针指向已回收的对象


def _lp(vk: int, flags: int = 0) -> int:
    info = KBDLLHOOKSTRUCT()
    info.vkCode = vk
    info.scanCode = 0
    info.flags = flags
    info.time = 0
    info.dwExtraInfo = 0
    _KEEP.append(info)
    return ctypes.cast(ctypes.byref(info), ctypes.c_void_p).value


def make_hook(decide=None, **kw) -> kh.KeyboardHook:
    """造一个不安装的钩子对象（只为用它的状态跟踪 / 探活逻辑）。"""
    return kh.KeyboardHook(None, decide or (lambda _event: False), **kw)


def keydown(hook, vk: int, scan: int = 0):
    """按回调里的**顺序**走一遍按键解析，返回 (字符, 修饰键快照)。

    顺序就是产品代码里的顺序（见 keyboard_hook._hook_proc 的 ② / ②' 步），
    所以这里改错了测试立刻红。
    """
    hook._sync_modifiers_from_async()
    healed = hook._heal_command_modifiers()
    char = vk_to_char(vk, scan, key_state=hook._state_bytes())
    return char, hook._snapshot_modifiers(), healed


class TestStuckCommandModifierIsHealed(unittest.TestCase):
    """Ctrl/Alt/Win 卡在按下 → 逐键对账立刻复位（这是"一个字都不转换"的元凶）。"""

    def test_stale_ctrl_bit_makes_every_key_unconvertible(self):
        """先把故障本身钉住：状态位卡在按下时，任何按键都解析不出字符。

        这一条就是用户报障的机理 —— ``vk_to_char`` 看到 Ctrl 位就说"这是快捷键、
        没有字符"，于是全部按键原样放行；界面、前台探测、托盘却全都正常。
        """
        hook = make_hook()
        hook._update_state(VK_LCONTROL, True)      # 只看到按下，永远等不到抬起
        with patch_user32({VK_LCONTROL}):           # 对账认为"物理上还按着"→ 不清
            char, mods, healed = keydown(hook, VK_OEM_1)
        self.assertEqual(healed, [])
        self.assertTrue(mods.has_command)
        self.assertIsNone(char, "卡住的 Ctrl 没让字符解析失败？那这条链路就不是元凶")

    def test_stuck_ctrl_is_healed_and_char_resolves(self):
        """同一个卡住状态：物理上其实没按 → 立刻复位，标点又能转换了。"""
        hook = make_hook()
        hook._update_state(VK_LCONTROL, True)
        with patch_user32(set()):                  # 物理上没按任何修饰键
            char, mods, healed = keydown(hook, VK_OEM_1)
        self.assertIn(VK_LCONTROL, healed, "卡住的 Ctrl 没被清掉")
        self.assertFalse(mods.ctrl)
        self.assertFalse(mods.has_command)
        self.assertEqual(char, ";", "清掉卡键后还是解析不出字符")

    def test_generic_control_bit_is_also_cleared(self):
        """通用位（VK_CONTROL）由左右键推出来，也必须跟着清掉。"""
        hook = make_hook()
        hook._update_state(VK_LCONTROL, True)
        self.assertTrue(hook._snapshot_modifiers().ctrl)
        with patch_user32(set()):
            hook._heal_command_modifiers()
        self.assertFalse(hook._snapshot_modifiers().ctrl, "通用位没跟着清")

    def test_held_ctrl_is_kept(self):
        """真按着 Ctrl（Ctrl+C 这种）绝不能被清 —— 那是快捷键，我们不插手。"""
        hook = make_hook()
        hook._update_state(VK_LCONTROL, True)
        with patch_user32({VK_LCONTROL}):
            char, mods, healed = keydown(hook, VK_OEM_1)
        self.assertEqual(healed, [])
        self.assertTrue(mods.ctrl)
        self.assertIsNone(char, "按住 Ctrl 时不该解析出字符")

    def test_alt_and_win_are_healed_too(self):
        for concrete in (VK_LMENU, VK_LWIN):
            hook = make_hook()
            hook._update_state(concrete, True)
            with patch_user32(set()):
                _, mods, healed = keydown(hook, VK_OEM_1)
            self.assertIn(concrete, healed)
            self.assertFalse(mods.has_command)

    def test_nothing_stuck_means_no_write(self):
        hook = make_hook()
        with patch_user32(set()):
            self.assertEqual(hook._heal_command_modifiers(), [])

    def test_shift_is_left_alone_by_per_key_heal(self):
        """Shift 不归逐键对账管 —— 它决定 ``:`` 还是 ``;``，必须信事件流。

        （回调滞后于物理按键时，物理状态反而会骗人，见 6.14。）
        """
        hook = make_hook()
        hook._update_state(VK_LSHIFT, True)
        with patch_user32(set()):
            self.assertEqual(hook._heal_command_modifiers(), [])
        self.assertTrue(hook._snapshot_modifiers().shift)

    def test_real_callback_heals_before_deciding(self):
        """**走真实回调**：卡住的 Ctrl 必须在 decide 之前就被清掉。

        这是本文件里最要紧的一条 —— 上面几条都是直接调内部方法，万一哪天
        有人把 ``_heal_command_modifiers()`` 从回调里删掉/挪到解析之后，
        只有这条会红。
        """
        seen: list = []
        hook = make_hook(lambda event: seen.append(event) or False)
        hook._update_state(VK_LCONTROL, True)      # 卡住（安全桌面上松开的）

        with patch_user32(set()):                  # 物理上没按任何修饰键
            hook._proc(0, WM_KEYDOWN, _lp(VK_OEM_1))

        self.assertEqual(len(seen), 1, "回调没把按键交给业务层")
        event = seen[0]
        self.assertFalse(event.modifiers.has_command, "decide 拿到的还是卡住的 Ctrl")
        self.assertEqual(event.char, ";", "decide 拿到的 char 是 None —— 又不会转换了")


class TestStuckShiftTimerHeal(unittest.TestCase):
    """Shift 的兜底：卡得够久 + 物理上确实没按，才敢清。"""

    def _hook_with_stuck_shift(self, age: float) -> kh.KeyboardHook:
        clock = FakeClock(start=1000.0)
        hook = make_hook(clock=clock.now)
        hook._update_state(VK_LSHIFT, True)
        hook._mod_down_since[VK_LSHIFT] = clock.now() - age
        return hook

    def test_fresh_shift_is_not_healed(self):
        hook = self._hook_with_stuck_shift(age=0.5)
        with patch_user32(set()):
            self.assertEqual(hook.heal_stuck_modifiers(max_age=6.0), [])
        self.assertTrue(hook._snapshot_modifiers().shift)

    def test_long_stuck_shift_is_healed(self):
        """在 UAC / 锁屏上松开的 Shift：状态位卡了很久，物理上早就没按 → 清掉。"""
        hook = self._hook_with_stuck_shift(age=30.0)
        with patch_user32(set()):
            healed = hook.heal_stuck_modifiers(max_age=6.0)
        self.assertIn(VK_LSHIFT, healed)
        self.assertFalse(hook._snapshot_modifiers().shift)

    def test_long_held_shift_is_kept(self):
        """真有按住 Shift 超过阈值也不会被清 —— 物理状态说的是"按着"。"""
        hook = self._hook_with_stuck_shift(age=30.0)
        with patch_user32({VK_LSHIFT}):
            self.assertEqual(hook.heal_stuck_modifiers(max_age=6.0), [])
        self.assertTrue(hook._snapshot_modifiers().shift)


class TestProbe(unittest.TestCase):
    """探活：唯一能判断"钩子是否还在被系统调用"的办法。"""

    def test_probe_needs_installed_hook(self):
        hook = make_hook()
        self.assertFalse(hook.installed)
        self.assertFalse(hook.probe(timeout=0.01))

    def test_callback_claiming_the_key_means_alive(self):
        hook = make_hook()
        hook._hook = 1234                       # 假装已经挂着
        hook._probe_sender = lambda: hook._probe_seen.set()   # 回调认领了它
        self.assertTrue(hook.probe(timeout=0.05))

    def test_silence_means_dead(self):
        hook = make_hook(probe_sender=lambda: None)
        hook._hook = 1234
        self.assertFalse(hook.probe(timeout=0.05),
                         "没人认领探活键，却判成了「活着」")

    def test_send_failure_is_not_treated_as_dead(self):
        """探活键发不出去（例如正在安全桌面上）不能算"钩子死了"，否则会疯狂重挂。"""

        def boom():
            raise RuntimeError("SendInput 失败")

        hook = make_hook(probe_sender=boom)
        hook._hook = 1234
        self.assertTrue(hook.probe(timeout=0.05))

    def test_injected_guard_key_sets_probe_flag(self):
        """走真实回调路径：注入的 0xE8 必须被认领。"""
        from codepoint.platform_win.input_simulator import VK_IME_GUARD

        hook = make_hook()
        hook._probe_seen.clear()
        hook._proc(0, WM_KEYDOWN, _lp(VK_IME_GUARD, LLKHF_INJECTED))
        self.assertTrue(hook._probe_seen.is_set())

    def test_physical_guard_key_is_not_a_probe_claim(self):
        """真人按到 0xE8（不可能，但真发生）不算探活成功 —— 探活键一定是我们注入的。"""
        from codepoint.platform_win.input_simulator import VK_IME_GUARD

        hook = make_hook()
        hook._probe_seen.clear()
        hook._proc(0, WM_KEYDOWN, _lp(VK_IME_GUARD, 0))
        self.assertFalse(hook._probe_seen.is_set())

    def test_callback_updates_liveness_stamp(self):
        hook = make_hook()
        before = hook.events_seen
        hook._proc(0, WM_KEYDOWN, _lp(0x41))
        hook._proc(0, WM_KEYUP, _lp(0x41))
        self.assertEqual(hook.events_seen, before + 2)
        self.assertLess(hook.event_age(), 1.0)


class FakeHook:
    """看护器用的钩子替身。"""

    def __init__(self, *, installed: bool = True, age: float = 0.0,
                 probe_ok: bool = True, reinstall_ok: bool = True) -> None:
        self.installed = installed
        self.age = age
        self.probe_ok = probe_ok
        self.reinstall_ok = reinstall_ok
        self.probe_calls = 0
        self.reinstall_calls = 0
        self.last_error = ""

    def event_age(self) -> float:
        return self.age

    def probe(self, timeout=None) -> bool:
        self.probe_calls += 1
        return self.probe_ok

    def reinstall(self) -> bool:
        self.reinstall_calls += 1
        if self.reinstall_ok:
            self.installed = True
        return self.reinstall_ok


class TestHookHealthMonitor(unittest.TestCase):
    """看护策略：平时一声不吭，只在真的有嫌疑时才动手。"""

    def make(self, hook, **kw):
        self.clock = FakeClock(start=500.0)
        self.healed: list = []
        monitor = HookHealthMonitor(
            hook, clock=self.clock.now, on_heal=self.healed.append, **kw)
        return monitor

    def test_active_hook_is_left_alone(self):
        hook = FakeHook(age=0.1)
        monitor = self.make(hook)
        self.assertEqual(monitor.tick().reason, "active")
        self.assertEqual(hook.probe_calls, 0)

    def test_missing_hook_is_reinstalled(self):
        hook = FakeHook(installed=False)
        monitor = self.make(hook)
        report = monitor.tick()
        self.assertTrue(report.reinstalled)
        self.assertEqual(hook.reinstall_calls, 1)
        self.assertEqual(monitor.repairs, 1)
        self.assertEqual(self.healed, [True])
        self.assertIn("已自动重挂 1 次", monitor.status_text)

    def test_probe_needs_two_rounds_of_suspicion(self):
        """鼠标划一下就"静默"很常见，必须连着两轮可疑才去打扰焦点程序。"""
        hook = FakeHook(age=10.0)
        monitor = self.make(hook)
        self.assertEqual(monitor.tick().reason, "suspect")
        self.assertEqual(hook.probe_calls, 0)
        self.assertIn(monitor.tick().reason, ("alive",))
        self.assertEqual(hook.probe_calls, 1)

    def test_probe_storm_is_cooled_down(self):
        hook = FakeHook(age=10.0)
        monitor = self.make(hook)
        monitor.tick()
        monitor.tick()                      # 真的探了
        self.assertEqual(hook.probe_calls, 1)
        monitor.tick()
        monitor.tick()
        self.assertEqual(hook.probe_calls, 1, "冷却期内不该再探")
        self.clock.advance(11.0)
        monitor.tick()
        monitor.tick()
        self.assertEqual(hook.probe_calls, 2)

    def test_idle_user_is_not_probed(self):
        """用户键盘鼠标都没动 → 钩子安静是应该的，不是掉线。"""
        hook = FakeHook(age=60.0)
        monitor = self.make(hook, user_input_age=lambda: 30.0)
        self.assertEqual(monitor.tick().reason, "idle_user")
        self.assertEqual(hook.probe_calls, 0)

    def test_out_of_scope_window_is_not_probed(self):
        """前台程序不归我们管 —— 没必要往它里面注入探活键。"""
        hook = FakeHook(age=60.0)
        monitor = self.make(hook, in_scope=lambda: False)
        self.assertEqual(monitor.tick().reason, "out_of_scope")
        self.assertEqual(hook.probe_calls, 0)

    def test_probe_failure_reinstalls_and_reverifies(self):
        hook = FakeHook(age=10.0, probe_ok=False)
        monitor = self.make(hook)
        monitor.tick()
        report = monitor.tick()
        self.assertTrue(report.reinstalled)
        self.assertEqual(hook.reinstall_calls, 1)
        self.assertFalse(report.healed, "复探没过就该如实报告没验证通过")

    def test_gives_up_after_repeated_failures(self):
        hook = FakeHook(installed=False, reinstall_ok=False)
        monitor = self.make(hook, max_failures=2)
        monitor.tick()
        report = monitor.tick()
        self.assertTrue(report.noteworthy)
        self.assertTrue(monitor.gave_up)
        self.assertIn("自动重挂连续失败", monitor.status_text)
        self.assertEqual(self.healed, [False])
        # 放弃之后不再打扰
        before = hook.reinstall_calls
        monitor.tick()
        self.assertEqual(hook.reinstall_calls, before)


class TestBlockReasonDiagnostics(unittest.TestCase):
    """"为什么不转换"的计数 —— 事后复盘唯一的现场证据。"""

    def make_pipeline(self):
        settings = build_default_settings()
        settings.app_items = [AppItem("codesys.exe")]
        settings.punctuation_rules = [PunctuationRule("；", ";", True)]
        pipeline = KeyboardPipeline(
            window_probe=FakeWindowProbe(),
            ime_probe=FakeImeProbe(),
            injector=FakeInjector(),
            notifier=RecordingNotifier(),
        )
        pipeline.apply_settings(settings)
        return pipeline

    def test_boring_reason_is_ignored(self):
        pipeline = self.make_pipeline()
        pipeline.stats.note_block("not_in_rules")
        pipeline.stats.note_block("pass")
        self.assertEqual(pipeline.stats.top_blocks(), [])

    def test_top_blocks_is_ranked(self):
        pipeline = self.make_pipeline()
        for _ in range(3):
            pipeline.stats.note_block("no_char")
        for _ in range(9):
            pipeline.stats.note_block("shortcut")
        self.assertEqual(pipeline.stats.top_blocks(), [("shortcut", 9), ("no_char", 3)])
        self.assertEqual(pipeline.stats.top_blocks(1), [("shortcut", 9)])


class TestRuntimeHint(unittest.TestCase):
    """主页那行小字：只在有事的时候才说话（用户看到的提示就靠它）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = App(config_dir=self._tmp.name, headless=True)
        settings = build_default_settings()
        settings.app_items = [AppItem("codesys.exe")]
        self.app._pipeline = KeyboardPipeline(
            window_probe=FakeWindowProbe(),
            ime_probe=FakeImeProbe(),
            injector=FakeInjector(),
            notifier=RecordingNotifier(),
        )
        self.app._pipeline.apply_settings(settings)

    def test_silent_when_nothing_is_wrong(self):
        self.assertEqual(self.app.runtime_hint(), "")
        self.assertNotIn("⚠", self.app.stats_text())

    def test_reports_reinstalled_hook(self):
        class _Health:
            repairs = 2
            gave_up = False
            status_text = "键盘钩子正常 · 已自动重挂 2 次"

        self.app._hook_health = _Health()
        self.assertIn("已自动重挂 2 次", self.app.runtime_hint())

    def test_reports_massive_shortcut_blocks(self):
        for _ in range(60):
            self.app._pipeline.stats.note_block("shortcut")
        hint = self.app.runtime_hint()
        self.assertIn("快捷键组合", hint)
        self.assertIn("60", hint)

    def test_ignores_occasional_blocks(self):
        for _ in range(3):
            self.app._pipeline.stats.note_block("shortcut")
        self.assertEqual(self.app.runtime_hint(), "")

    def test_stats_text_lists_hook_and_reasons(self):
        class _Health:
            repairs = 1
            gave_up = False
            status_text = "键盘钩子正常 · 已自动重挂 1 次"

        self.app._hook_health = _Health()
        for _ in range(5):
            self.app._pipeline.stats.note_block("no_char")
        text = self.app.stats_text()
        self.assertIn("键盘钩子正常 · 已自动重挂 1 次", text)
        self.assertIn("拿不到字符×5", text)


class TestTickerWiring(unittest.TestCase):
    """定时线程里那两件事必须真的被调到（不然前面修的一切都不会发生）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = App(config_dir=self._tmp.name, headless=True)

    def test_hook_health_is_ticked(self):
        calls: list = []

        class _Monitor:
            def tick(self):
                calls.append(1)

        self.app._hook_health = _Monitor()
        self.app._tick_hook_health()
        self.assertEqual(len(calls), 1)

    def test_modifier_heal_runs_and_tells_the_user(self):
        class _Hook:
            def heal_stuck_modifiers(self):
                return [VK_LSHIFT]

        self.app._hook = _Hook()
        self.app._tick_modifier_heal()
        kinds = [event.kind for event in self.app.bridge.poll()]
        self.assertIn("toast", kinds, "清掉卡键后没给用户任何反馈")

    def test_modifier_heal_is_quiet_when_nothing_stuck(self):
        class _Hook:
            def heal_stuck_modifiers(self):
                return []

        self.app._hook = _Hook()
        self.app._tick_modifier_heal()
        self.assertEqual(self.app.bridge.poll(), [])


if __name__ == "__main__":
    unittest.main()
