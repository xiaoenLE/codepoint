"""组装根（Composition Root）。

**整个工程里唯一知道"所有东西怎么拼在一起"的地方。**
其它模块只依赖抽象（Protocol），只有这里做具体绑定。

    core  ←  services  ←  app  →  platform_win
                          ↓
                         ui

启动顺序（顺序有讲究）：
    单实例 → 配置 → 消息线程 → 裁剪配置 → 界面 → 钩子 → 托盘/热键/剪贴板 → 主循环
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import APP_NAME, APP_DISPLAY_NAME, __version__
from .core import portable
from .core.card import Card
from .core.config import ConfigPaths, resolve_config_dir
from .core.defaults import text_conversion_map
from .core.events import ImeSnapshot, WindowInfo
from .core.models import INSERT_PASTE, Settings
from .core.storage import SettingsStore
from .iconmaker import ensure_icon
from .platform_win import autostart as autostart_mod
from .platform_win import clipboard as clipboard_io
from .platform_win import foreground, ime as ime_mod, input_simulator, last_input, privilege
from .platform_win import editor_context, focus_state
from .platform_win import processes
from .platform_win import screen
from .platform_win.clipboard_listener import ClipboardListener
from .platform_win.hotkey import HotkeyManager
from .platform_win.keyboard_hook import KeyboardHook
from .platform_win import message_loop
from .platform_win.message_loop import MessageWindow
from .platform_win.single_instance import SingleInstance
from .platform_win.tray import TrayIcon, TrayMenuItem
from .services.clipboard_filter import ClipboardPunctuationFilter
from .services.ai_provider import (
    MAX_AUTO_LINES, AiConfigurationError, AiOutputIncomplete, MockCompleter,
    ProbeResult, create_completer, probe,
)
from .services.selection import SelectionGrabber
from .services.hook_health import HookHealthMonitor
from .services.pipeline import KeyboardPipeline
from .services.app_filter import AppFilter
from .ui.bridge import UiBridge
from .ui.indicator import IndicatorNotifier
from .ui.main_window import MainWindow

log = logging.getLogger("codepoint")

# 托盘菜单命令 ID
CMD_SHOW = 1001
CMD_TOGGLE = 1002
CMD_SETTINGS = 1003
CMD_QUIT = 1004

HOTKEY_TOGGLE = 1
#: 选中文本 → AI 生成。和总开关分开注册，禁用一个不影响另一个。
HOTKEY_AI = 2

# 生效范围的原因码 → 界面上那句人话（界面只看到这一层，不知道内部枚举）
_SCOPE_HINT: Dict[str, str] = {
    "in_scope": "标点会自动转半角",
    "out_of_scope": "把它加进列表才会生效",
    "self": "本程序自己的窗口",
    "no_window": "没有前台窗口",
}

# "没转换"的原因码 → 人话。排障时这一行就是全部线索：
#   shortcut      ← Ctrl/Alt/Win 位卡住了（曾经让整个功能静默失效）
#   no_char       ← 同上（拿不到字符）
#   composing     ← 输入法候选框开着，我们刻意不抢键
#   ime_not_cjk   ← 勾了"只在中文标点状态下替换"，而当前不是
#   already_halfwidth ← 输入法本来就是半角，不用我们插手
_REASON_CN: Dict[str, str] = {
    "shortcut": "快捷键组合",
    "no_char": "拿不到字符",
    "composing": "输入法正在组字",
    "ime_not_cjk": "输入法不在中文标点态",
    "already_halfwidth": "本来就是半角",
    "disabled": "标点替换已关",
    "no_window": "没有前台窗口",
    "not_in_scope": "不在生效范围",
}


# ==================================================================== 适配器
#
# 这一小段是"把 platform_win 的具体能力伪装成 services 要的接口"。
# 只有组装根该做这件事；如果散到别处，services 就会不知不觉依赖上 Win32。


class _WindowProbeAdapter:
    """services.ports.WindowProbe 的实现。"""

    def __init__(self, self_pid: int) -> None:
        self._self_pid = self_pid

    def front(self) -> WindowInfo:
        return foreground.query()

    def self_pid(self) -> int:
        return self._self_pid


class _ImeProbeAdapter:
    """services.ports.ImeProbe 的实现。"""

    def state(self, hwnd: int) -> ImeSnapshot:
        return ime_mod.query(hwnd)


class _InjectorAdapter:
    """services.ports.TextInjector 的实现。"""

    def __init__(self, mode_getter=None, suppress=None, message_window=None) -> None:
        self._mode = mode_getter or (lambda: INSERT_PASTE)
        self._suppress = suppress
        self._message_window = message_window

    def send_text(self, text: str) -> None:
        input_simulator.send_unicode_text(text)

    def send_block(self, text: str, *, always_paste: bool = False) -> None:
        """整段插入：优先剪贴板粘贴，不行就逐字符。

        两种方式的区别（用户提的，2026-10-21）：
          · **粘贴**：一个动作塞进去。目标程序的自动补全、自动缩进、代码索引
            来不及在中间插一脚 —— 以前逐字符写入时它们会"索引其他的"，导致
            写进去的代码是歪的。
          · **逐字符**：跟真人一个个敲一样，不碰剪贴板。兼容老软件，但容易被
            边敲边联想的功能打断。

        谁走这里、谁不走（用户定的边界）：
          · **AI 结果** → 走，且 ``always_paste=True``：哪怕只补一行也粘贴，
            一行照样会被联想打断。
          · **快捷输入**（`if` → `IF  THEN\nEND_IF;`）→ 走，但短的单行内容
            （≤8 字，比如 `:` → `:=`）仍然逐字符：为两个字绕一圈剪贴板太重。
          · **标点替换** → 不走这里，继续 ``send_text`` 逐字符。
        """
        short = "\n" not in text and len(text) <= 8
        want_paste = (self._mode() == INSERT_PASTE) and (always_paste or not short)
        if want_paste:
            ok = False
            try:
                from_hook = (self._message_window is not None
                             and self._message_window.thread_id == threading.get_ident())
                ok = input_simulator.paste_text(text, suppress=self._suppress, from_hook=from_hook)
            except Exception:      # noqa: BLE001
                log.exception("粘贴插入时异常，退回逐字符写入")
                ok = False
            if ok:
                return
            log.info("粘贴插入没走通（剪贴板被占/修饰键还按着），退回逐字符写入")
        input_simulator.send_unicode_text(text)

    def send_backspaces(self, count: int) -> None:
        input_simulator.send_backspaces(count)

    def send_ime_guard(self) -> None:
        input_simulator.send_ime_guard()


class _ClipboardAdapter:
    """services.ports.ClipboardPort 的实现。"""

    def get_text(self):
        return clipboard_io.get_text()

    def get_selected_text(self) -> Optional[str]:
        return clipboard_io.get_text(selection_only=True)

    def set_text(self, text: str) -> bool:
        return clipboard_io.set_text(text)

    def sequence_number(self) -> int:
        return clipboard_io.sequence_number()


class _AiCancelled(Exception):
    """用户按 Esc 掐断了正在进行的生成。

    单独一个类型，是为了别把它当成"AI 补全失败"弹错误提示 ——
    用户自己取消的，提示"失败"只会让人以为坏了。
    """


class _CopierAdapter:
    """services.ports.SelectionCopier 的实现。"""

    def wait_ready(self) -> bool:
        # 热键在"按下"那一刻触发，这时 Ctrl/Alt 还按着；
        # 不等它松开就发 Ctrl+C，会变成 Ctrl+Alt+C（谁都认不出来）。
        return input_simulator.wait_modifiers_released(heal=False)

    def copy_selection(self) -> None:
        input_simulator.copy_selection()

    def select_context(self, count: int, *, below: bool) -> bool:
        return input_simulator.select_context(count, below=below)

    def move_context_endpoint(self, count: int, *, left: bool) -> bool:
        return input_simulator.move_context_endpoint(count, left=left)

    def collapse_selection(self, *, to_start: bool) -> bool:
        return input_simulator.collapse_selection(to_start=to_start)

    def caret_position(self) -> Optional[tuple[int, int]]:
        point = ime_mod.caret_point(fallback_to_cursor=False)
        return (point.x, point.y) if point.valid else None


def _caret_positioner():
    """给呼吸灯定位用：优先插入符位置，退化到鼠标位置。

    ⚠ 只给呼吸灯用。**绝不能拿它定位主窗口** —— 它返回的是光标坐标，
    主窗口跟着鼠标跑就会出现在屏幕角落甚至可视区外
    （用户报障："不显示在桌面中间，而且到右下角，有时不能拖出来"）。
    主窗口的位置走 ``_screen_work_area`` + ``core.window_geom.centered``。
    """
    try:
        point = ime_mod.caret_point()
        return int(point.x), int(point.y)
    except Exception:      # noqa: BLE001
        return 620, 360


def _screen_work_area() -> Tuple[int, int, int, int]:
    """主窗口该居中的那块区域：鼠标所在显示器的工作区（已减任务栏）。"""
    return screen.work_area()


# ==================================================================== 应用


class App:
    """把各层拼装成可运行的整体，并实现 ui.ports.AppFacade。"""

    def __init__(self, *, config_dir: Optional[str] = None,
                 headless: bool = False, start_hidden: bool = False) -> None:
        self.headless = headless
        self.start_hidden = start_hidden

        self.paths = ConfigPaths(resolve_config_dir(config_dir))
        self.store = SettingsStore(self.paths)
        loaded, self.load_error = self.store.load()
        self.settings: Settings = loaded
        # 开机自启状态以**注册表**为准，不以配置文件为准。
        # 配置会被导出/导入搬来搬去（换机器、重装），而注册表才是
        # "重启后到底会不会起来"的唯一事实来源。不校准的话界面会显示"关"、
        # 结果重启又自己起来了 —— 这种"开关骗人"比功能坏了更难查。
        try:
            actual = autostart_mod.is_enabled()
            if actual != self.settings.enable_auto_startup:
                self.settings.enable_auto_startup = actual
        except Exception:      # noqa: BLE001
            pass
        # 记下"磁盘上/上次保存时的样子"，退出时据此决定要不要回写
        self._baseline: str = self._fingerprint()

        self.icon_path: Optional[str] = None
        self.bridge = UiBridge()
        self._instance = SingleInstance(APP_NAME)
        self._message_window: Optional[MessageWindow] = None
        self._hook: Optional[KeyboardHook] = None
        self._tray: Optional[TrayIcon] = None
        self._hotkeys: Optional[HotkeyManager] = None
        self._clipboard_listener: Optional[ClipboardListener] = None
        self._clipboard_filter = ClipboardPunctuationFilter(self.settings.enable_clipboard_replace)
        self._clipboard_jobs: "queue.Queue[bool]" = queue.Queue(maxsize=1)
        self._clipboard_worker: Optional[threading.Thread] = None
        self._ticker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._window: Optional[MainWindow] = None
        self._pipeline: Optional[KeyboardPipeline] = None
        #: 「生效范围」判定器。AI 补全要往**别人家的窗口**里注入 Ctrl+C / Shift+Up，
        #: 所以动手前必须先问它一句：当前这个程序算不算受管程序。
        self._app_scope: Optional[AppFilter] = None
        #: 注入器（逐字符 / 整段粘贴）。放在 app 这层是因为"粘贴"要动剪贴板，
        #: 而剪贴板监听器在这里 —— services 层不该认识这些。
        self._injector: Optional[_InjectorAdapter] = None
        self._notifier: Optional[IndicatorNotifier] = None
        self._hook_health: Optional[HookHealthMonitor] = None
        # —— 选中文本 + 热键（Ctrl+Alt+G）——
        self._grabber: Optional[SelectionGrabber] = None
        self._ai_busy = threading.Lock()
        #: 生成期间按 Esc → set()。流式回调每次收到增量都查一下，置位就抛
        #: _AiCancelled 把连接断掉（模型那边也会停，不是白烧算力）。
        self._ai_cancel = threading.Event()

    # ================================================================ 生命周期

    def start(self) -> bool:
        if not self._instance.acquire():
            # 已经有实例在跑：先去请它把窗口显示出来，**而不是**只丢一句
            # "请到托盘区查看" —— 默认关闭窗口就是收进托盘，而 Win11 会把
            # 新托盘图标折叠起来，用户根本找不到，等于被关在门外。
            if message_loop.activate_peer_window():
                log.info("已有实例在运行，已请求它显示窗口，本次启动退出")
                return False
            self._fatal("码点助手已经在运行了，请到系统托盘区查看！")
            return False

        self._setup_logging()
        log.info("启动 %s v%s", APP_DISPLAY_NAME, __version__)
        if self.load_error:
            log.warning(self.load_error)

        self.paths.ensure()
        icon = Path(__file__).resolve().parents[1] / "build" / "icon.ico"
        self.icon_path = str(icon) if icon.is_file() else ensure_icon(self.paths.base_dir, tag=__version__)

        self._setup_services()
        if not self._setup_message_thread():
            return False
        self._setup_pipeline()
        self._install_hook()
        self._setup_tray()
        self._setup_hotkeys()
        self._setup_clipboard()
        self._start_tickers()
        self._warn_if_not_admin()
        return True

    def run(self) -> int:
        if self.headless:
            try:
                while not self._stop.is_set():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            return 0

        self._window = MainWindow(
            self, self.bridge,
            positioner=_caret_positioner,
            icon_path=self.icon_path,
            start_hidden=self.start_hidden,
            area_fn=_screen_work_area,
        )
        try:
            self._window.run()
        finally:
            self.shutdown()
        return 0

    def _fingerprint(self) -> str:
        """当前配置的指纹（用来判断"改过没有"）。"""
        try:
            return json.dumps(self.settings.to_dict(), sort_keys=True,
                              ensure_ascii=False, default=str)
        except Exception:      # noqa: BLE001
            return ""          # 算不出来就当作"改过"，宁可多写一次

    def _is_dirty(self) -> bool:
        return self._fingerprint() != self._baseline

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        log.info("退出中…")
        try:
            # 退出阶段把跨线程等待压到 1 秒：消息线程万一卡住（比如钩子回调
            # 死锁），卸载动作不至于把退出拖成"点了没反应"。
            if self._message_window is not None:
                self._message_window.set_default_timeout(1.0)
            if self._hook:
                self._hook.uninstall()
            # AI 线程池不关的话，正在等 HTTP 的那个 worker 会拖住解释器退出
            if self._pipeline is not None:
                self._pipeline.close()
            if self._hotkeys:
                self._hotkeys.unregister_all()
            if self._clipboard_listener:
                self._clipboard_listener.uninstall()
            if self._tray:
                self._tray.hide()
            if self._message_window:
                self._message_window.stop()
        except Exception:      # noqa: BLE001
            pass
        # 只在"真的改过"时回写。
        # 无条件回写会踩这个坑：用户开着程序，同时手动改了 settings.json
        # （或者用导入功能换了配置），一退出就被内存里的旧值覆盖回去 ——
        # 表现为"我改的配置怎么自己变回去了"，非常难查。
        if self._is_dirty():
            self.store.save(self.settings)
        else:
            log.info("设置未改动，跳过回写")
        self._instance.release()

    # ================================================================ 内部搭建

    def _fatal(self, message: str) -> None:
        log.error(message)
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_DISPLAY_NAME, 0x40)
        except Exception:      # noqa: BLE001
            print(message, file=sys.stderr)

    def _setup_logging(self) -> None:
        try:
            self.paths.ensure()
            handler = logging.FileHandler(self.paths.log_dir / "codepoint.log",
                                          encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
            logging.getLogger("codepoint").addHandler(handler)
            logging.getLogger("codepoint").setLevel(logging.INFO)
        except Exception:      # noqa: BLE001
            pass

    def _setup_services(self) -> None:
        self._clipboard_filter.configure(
            text_conversion_map(self.settings.punctuation_rules),
            enabled=self.settings.enable_clipboard_replace,
        )

    def _setup_message_thread(self) -> bool:
        self._message_window = MessageWindow()
        if not self._message_window.start():
            self._fatal(f"无法创建后台消息线程：{self._message_window.start_error}")
            return False
        # 别的实例（用户又双击了一次）会发这个消息过来，请我们把窗口显出来
        self._message_window.add_handler(
            message_loop.WM_APP_ACTIVATE,
            lambda *_a: self.bridge.publish(UiBridge.SHOW_WINDOW),
        )
        return True

    def _setup_pipeline(self) -> None:
        # 预览窗的位置由界面层负责（main_window 把 _caret_positioner 传给 CardWindow），
        # 通知器只负责"投递一块要显示的内容"。
        notifier = IndicatorNotifier(
            self.bridge,
            enabled=self.settings.indicator_enabled,
        )
        self._notifier = notifier
        injector = _InjectorAdapter(
            mode_getter=lambda: str(getattr(self.settings, "ai_insert_mode", "paste")),
            suppress=self._suppress_clipboard_listener,
            message_window=self._message_window,
        )
        self._injector = injector
        # 同一个判定器同时给「逐键替换」和「AI 补全」用 —— 两边必须是同一个范围，
        # 否则会出现"这里生效、那里不生效"的诡异状态。
        scope = AppFilter(self.settings.app_items, mode=self.settings.match_mode,
                          self_pid=os.getpid())
        self._app_scope = scope
        self._pipeline = KeyboardPipeline(
            window_probe=_WindowProbeAdapter(os.getpid()),
            ime_probe=_ImeProbeAdapter(),
            injector=injector,
            notifier=notifier,
            app_filter=scope,
            cancel_hook=self._on_escape,
        )
        self._pipeline.apply_settings(self.settings)
        # 选中文本 → AI：抓取器只管"备份剪贴板 → Ctrl+C → 读 → 还原"，
        # 和 pipeline 无关（走的是全局热键，不是键盘钩子）。
        self._grabber = SelectionGrabber(_ClipboardAdapter(), _CopierAdapter())

    def _install_hook(self) -> None:
        assert self._message_window is not None
        assert self._pipeline is not None
        self._hook = KeyboardHook(self._message_window, self._pipeline.handle)
        if not self._hook.install():
            log.error("键盘钩子安装失败：%s", self._hook.last_error)
            self._fatal(
                "键盘钩子安装失败，程序无法工作。\n"
                f"原因：{self._hook.last_error}\n\n"
                "请尝试以管理员身份运行。"
            )
            return
        # 装好之后挂上看护：系统会静默摘掉超时的钩子，掉了要能自己捡回来，
        # 不能让用户"重启一下又好了"（用户报障：能识别到程序，就是不转换）。
        self._hook_health = HookHealthMonitor(
            self._hook,
            user_input_age=last_input.idle_seconds,
            in_scope=self._foreground_in_scope,
            on_heal=self._on_hook_healed,
        )

    def _foreground_in_scope(self) -> bool:
        """前台程序在生效范围内吗（探活只在"真该干活"的时候做）。"""
        if self._pipeline is None:
            return False
        try:
            info = foreground.query()
            return bool(info.valid and self._pipeline.app_filter.matches(info))
        except Exception:      # noqa: BLE001
            return False

    def _on_hook_healed(self, ok: bool) -> None:
        """看护器救回来（或救不回来）时的对外反馈。"""
        message = ("键盘钩子被系统摘掉了，已自动重挂，标点替换恢复。"
                   if ok else
                   "键盘钩子重挂后仍未生效，请退出后重新启动本程序。")
        try:
            self.bridge.publish(UiBridge.TOAST, title="码点助手", message=message)
        except Exception:      # noqa: BLE001
            pass

    def _setup_tray(self) -> None:
        if self._message_window is None:
            return
        self._tray = TrayIcon(
            self._message_window,
            tooltip=f"{APP_DISPLAY_NAME} v{__version__}",
            icon_path=self.icon_path,
        )
        self._tray.set_on_command(self._on_tray_command)
        self._tray.set_on_activate(self.show_window)
        self._tray.set_on_giveup(self._on_tray_unavailable)
        if not self._tray.show():
            # 常见于开机自启：explorer 还没起来。图标会在后台持续重试，
            # 这里只记一笔，不要弹窗吓人。
            log.warning("托盘图标首次添加失败，正在后台重试（开机自启时属正常现象）")
        self._refresh_tray_menu()

    def _on_tray_unavailable(self) -> None:
        """托盘图标始终加不上 → 把窗口显示出来。

        开机自启是 ``--minimized``：窗口藏起来 + 托盘又没图标 = 程序彻底隐形，
        用户只会认为"自启没生效"。宁可弹个窗口，也不能让它消失。
        """
        log.error("托盘图标多次重试仍失败，改为显示主窗口")
        self.bridge.publish(UiBridge.SHOW_WINDOW)

    def _setup_hotkeys(self) -> None:
        if self._message_window is None:
            return
        self._hotkeys = HotkeyManager(self._message_window, self._on_hotkey)
        if self.settings.toggle_hotkey:
            ok = self._hotkeys.register_text(HOTKEY_TOGGLE, self.settings.toggle_hotkey)
            if not ok:
                log.warning("热键 %s 注册失败（可能已被占用）", self.settings.toggle_hotkey)
        self._register_ai_hotkey()

    def _register_ai_hotkey(self) -> None:
        """注册「选中文本 → AI」热键。不满足条件就注销，不留一个哑热键。"""
        if self._hotkeys is None:
            return
        self._hotkeys.unregister(HOTKEY_AI)
        if not (self.settings.ai_enabled and self.settings.ai_hotkey):
            return
        if not self._hotkeys.register_text(HOTKEY_AI, self.settings.ai_hotkey):
            log.warning("AI 热键 %s 注册失败（可能已被占用）", self.settings.ai_hotkey)
            self._notify_safe(
                "AI 热键没注册上",
                f"{self.settings.ai_hotkey} 被别的程序占了。可在 settings.json 里改 ai_hotkey。",
            )

    def _setup_clipboard(self) -> None:
        if self._message_window is None:
            return
        self._clipboard_listener = ClipboardListener(
            self._message_window, self._on_clipboard_changed
        )
        self._clipboard_listener.install()
        self._clipboard_worker = threading.Thread(
            target=self._clipboard_loop, name="codepoint-clipboard", daemon=True
        )
        self._clipboard_worker.start()

    def _start_tickers(self) -> None:
        def loop() -> None:
            while not self._stop.wait(1.0):
                try:
                    if self._pipeline:
                        self._pipeline.tick()
                except Exception:      # noqa: BLE001
                    pass
                # 下面两件事都自带节流（卡键阈值 6 秒 / 探活冷却 10 秒），
                # 每秒调一次只是便宜地"问一句"，真动手的门槛在里面。
                self._tick_modifier_heal()
                self._tick_hook_health()

        self._ticker = threading.Thread(target=loop, name="codepoint-tick", daemon=True)
        self._ticker.start()

    def _tick_modifier_heal(self) -> None:
        """兜底清掉"卡住很久、物理上没按"的修饰键（含 Shift）。

        安全桌面（UAC / 锁屏）上我们的钩子收不到按键，用户在那儿松开的
        Ctrl/Alt/Win/Shift 会让状态位永远停在"按下"。卡住的命令键会让
        ``vk_to_char`` 对所有按键返回 None —— 整个程序看着正常却一个字都不
        转换（用户的报障）。命令键在钩子回调里已经逐键对账，这里是 Shift 的兜底。
        """
        if self._hook is None:
            return
        try:
            healed = self._hook.heal_stuck_modifiers()
        except Exception:      # noqa: BLE001
            log.exception("卡键自愈失败")
            return
        if healed:
            names = ", ".join(f"0x{h:02X}" for h in healed)
            log.warning("清掉卡住的修饰键：%s（物理上没按，判定为漏收 key-up）", names)
            try:
                self.bridge.publish(
                    UiBridge.TOAST, title="码点助手",
                    message="检测到修饰键状态卡住（可能是在 UAC/锁屏界面按过），已自动复位。",
                )
            except Exception:      # noqa: BLE001
                pass

    def _tick_hook_health(self) -> None:
        if self._hook_health is None:
            return
        self._hook_health.tick()

    def _warn_if_not_admin(self) -> None:
        if privilege.is_admin():
            return
        log.warning("当前非管理员运行，无法向提权程序注入按键")
        self.bridge.publish(
            UiBridge.TOAST, title="权限提示",
            message="当前未以管理员运行，若目标程序是以管理员启动的，"
                    "标点替换将不会生效。",
        )

    # ================================================================ 回调

    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_TOGGLE:
            self.set_global_enabled(not self.is_global_enabled())
            self._refresh_tray_menu()
            self.bridge.publish(UiBridge.SETTINGS_CHANGED)
        elif hotkey_id == HOTKEY_AI:
            # ⚠ 这里是**消息线程**：只派发，绝不做复制/请求/等待。
            self._start_ai_from_selection()

    # ---------------------------------------------------------------- AI（选中文本）

    def _start_ai_from_selection(self) -> None:
        """Ctrl+Alt+G：把选中的文字当提示词，生成后替换选区。

        选区为什么能直接替换：复制（Ctrl+C）**不会取消选区**，所以注入的字符
        到达编辑器时，编辑器会按标准行为"用输入内容替换选中内容"。
        """
        if not self.settings.ai_enabled:
            self._notify_safe("AI 补全已关闭", "到「常规设置 → AI 补全」把它打开。")
            return
        if not self._ai_busy.acquire(blocking=False):
            self._notify_safe("正在生成", "上一次还没回来，稍等一下。")
            return
        try:
            window = foreground.query()
            threading.Thread(
                target=self._ai_selection_worker,
                args=(int(window.hwnd or 0),),
                name="codepoint-ai-select",
                daemon=True,
            ).start()
        except Exception:      # noqa: BLE001
            self._ai_busy.release()
            log.exception("启动 AI 选区任务失败")

    def _hide_card_safe(self) -> None:
        """失败/取消路径上收掉"生成中"的卡。别让一块空转的卡留在屏幕上。"""
        notifier = self._notifier
        if notifier is not None:
            try:
                notifier.hide()
            except Exception:      # noqa: BLE001
                pass

    def _stream_updater(self, label: str):
        """给 AI 生成做一个"边生成边刷预览窗"的回调。

        先弹一块"生成中"的卡把窗口立起来（流式更新只能刷已存在的卡），
        之后每来一段就地刷新正文。

        节流到约 10 次/秒：token 一段段到，每次都整卡重画的话，
        既追不上（画一帧 10ms 量级），又会让窗口跟着抖。
        """
        state = {"last": 0.0, "count": 0}
        notifier = self._notifier
        if notifier is not None and self.settings.ai_card_enabled:
            try:
                notifier.show_card(Card.busy(label, "…"))
            except Exception:      # noqa: BLE001
                pass

        def on_delta(partial: str) -> None:
            # 用户按了 Esc → 立刻抛出去，把 HTTP 连接断掉。
            # 检查放在最前面（连节流都不过）：Esc 之后不该再画一帧出来。
            if self._ai_cancel.is_set():
                raise _AiCancelled()
            now = time.monotonic()
            state["count"] += 1
            if now - state["last"] < 0.1 and state["count"] % 5 != 0:
                return
            state["last"] = now
            if notifier is None:
                return
            try:
                # 标题带上已生成的行数 —— 内容是逐行长出来的，光看"生成中"
                # 分不清是在慢慢吐还是卡住了。
                notifier.stream_card(
                    partial, label="%s · %d 行" % (label, partial.count("\n") + 1))
            except Exception:      # noqa: BLE001 —— 界面刷新失败不能打断生成
                log.debug("流式刷新预览失败", exc_info=True)
        return on_delta

    def _suppress_clipboard_listener(self, sequence: int) -> None:
        """我们自己在改剪贴板 —— 让"标点净化"别再改一遍。

        粘贴插入会先把生成的代码塞进剪贴板。如果这时候净化器看到剪贴板变了，
        它会把代码里的中文标点（`：` `、` 之类在注释里很常见）改写成半角，
        粘出来的内容就和预览窗里看到的不一样了。
        """
        listener = self._clipboard_listener
        if listener is not None:
            try:
                listener.suppress(sequence)
            except Exception:      # noqa: BLE001
                pass

    def _injection_block_reason(self, hwnd: int) -> str:
        """能不能往目标窗口注入按键？空串 = 可以，否则是拒绝原因。

        AI 补全可能往目标窗口发送 Ctrl+C 或写入文本 —— 这跟"处理自己的
        界面"完全是两件事，是在**操作别人的程序**。所以动手前逐条问：

        1. 前台还是触发热键时那个窗口吗？（用户可能已经切走了）
        2. 这个程序在「生效范围」里吗？
        3. 有菜单正开着吗？焦点是不是落在菜单/工具条/列表上？

        第 3 条是补的第 3 次报障（v1.6.3）："我在其他软件中，怎么会选择到上面的
        属性，然后进行了点击，导致软件被关闭" / "触发他获取后就点到菜单那边去了"。
        焦点在菜单上时，注入的方向键会去挪菜单选中项，用户随后一个回车就成了
        "点击那一项"。
        """
        scope = self._app_scope
        window = foreground.query()
        if int(window.hwnd or 0) != int(hwnd or 0):
            return "窗口变了"
        # 「生效范围」这一道由 ai_scope_limited 控制（默认管）：
        # 用户报障"设置了只在列表内程序，但没列表的程序里照样能调 ai"。
        # 关掉它 = AI 不看生效范围，哪儿都能用 —— 但下面两道闸始终有效。
        if (scope is not None and getattr(self.settings, "ai_scope_limited", True)
                and not scope.matches(window)):
            return "不在生效范围"
        state = focus_state.query()
        if state.menu_open:
            return "菜单正开着"
        if not state.looks_editable:
            return "焦点不在编辑区（%s）" % (state.focus_class or "未知控件")
        return ""

    def _scope_allows(self, hwnd: int) -> bool:
        """``_injection_block_reason`` 的布尔版本（抓上下文途中反复用）。"""
        return not self._injection_block_reason(hwnd)

    def _ai_selection_worker(self, hwnd: int) -> None:
        """优先读取编辑器文本范围，必要时用键盘取上下文。"""
        # 每次开工先把上次的取消标记清掉，否则上一次按过 Esc 会让这一次
        # 刚发出请求就被判定为"已取消"。
        self._ai_cancel.clear()
        try:
            grabber = self._grabber
            if grabber is None:
                return
            # ⚠ 第一道闸：窗口换了 / 程序不在生效范围 / 焦点在菜单上 —— 立刻收手。
            #    这一步必须早于 grab()，因为 grab() 一上来就是一个 Ctrl+C。
            blocked = self._injection_block_reason(hwnd)
            if blocked:
                self._hide_card_safe()
                log.info("AI 补全：跳过注入（%s）· %s", blocked, focus_state.report())
                if blocked == "不在生效范围":
                    self._notify_safe(
                        "不在生效范围",
                        "当前程序不在「生效范围」里，没动它。\n"
                        "要么把它加进「常规设置 → 生效范围」，要么到「AI 补全」"
                        "关掉「也遵守生效范围」（那样 AI 在哪儿都能用）。")
                else:
                    self._notify_safe(
                        "没有动手",
                        "%s，按一个键一个都没发。\n"
                        "把光标放回代码里（先关掉菜单）再按一次热键。" % blocked)
                return
            completer = create_completer(self.settings)
            demo = isinstance(completer, MockCompleter)

            lines = self.settings.ai_context_lines
            use_fim = self.settings.ai_fim_enabled and hasattr(completer, "complete_fim")
            target_focus = focus_state.query()
            if target_focus.hwnd != hwnd:
                return
            context = editor_context.read(
                target_focus,
                before=lines, after=self.settings.ai_suffix_lines if lines and use_fim else 0,
                prefix_chars=self.settings.ai_prefix_max_chars,
                suffix_chars=self.settings.ai_suffix_max_chars,
            )

            def target_still_current() -> bool:
                return (not self._ai_cancel.is_set()
                        and focus_state.query() == target_focus and self._scope_allows(hwnd))

            if not target_still_current():
                return
            if context is None:
                process_name = foreground.query(hwnd).process_name
                is_st_editor = process_name in {"codesys.exe", "invtmatic studio.exe"}
                grabbed = grabber.grab(can_copy=target_still_current)
                if grabbed.modifiers_stuck:
                    self._notify_safe("按键还没松开", f"按完 {self.settings.ai_hotkey} 把键都松开，再试一次。")
                    return
                if not target_still_current():
                    return
                if grabbed.ok:
                    context = editor_context.EditorContext(selection=grabbed.text)
                elif grabbed.text and not is_st_editor:
                    self._notify_safe("选区没有代码", "请清除空白选区，或选中相关代码后再试。")
                    return
                elif lines <= 0:
                    self._notify_safe("请先选中代码", "上下文行数设为 0 时只处理选区。")
                    return
                else:
                    # ST 编辑器在空行上也可能复制出换行，按旧版行为继续取上下文。
                    capture_options = {
                        "can_copy": target_still_current,
                        "collapse_to_anchor": is_st_editor,
                    }
                    prefix = grabber.grab_context(lines, below=False, **capture_options)
                    if not target_still_current() or self._context_grab_failed(prefix):
                        return
                    suffix = ""
                    if use_fim and self.settings.ai_suffix_lines:
                        below = grabber.grab_context(self.settings.ai_suffix_lines, below=True,
                                                     **capture_options)
                        if not target_still_current() or self._context_grab_failed(below):
                            return
                        suffix = below.text
                    context = editor_context.EditorContext(prefix=prefix.text, suffix=suffix)

            if context.selection.strip():
                label = "本地演练 · 示例" if demo else "AI 改写"
                log.info("AI 改写：选中 %d 字", len(context.selection))
                on_delta = self._stream_updater(label)
                text = completer.complete(context.selection, on_delta=on_delta)
            else:
                prefix, suffix = context.prefix, context.suffix
                if not prefix.strip() and not suffix.strip():
                    self._notify_safe("没有可用的上下文", "请先选中相关代码，或在已有代码附近触发补全。")
                    return
                label = "本地演练 · 示例" if demo else "AI 补全"
                log.info("AI 补全：上文 %d 字 · 下文 %d 字", len(prefix), len(suffix))
                on_delta = self._stream_updater(label)
                if use_fim and (suffix.strip() or self.settings.ai_fim_strategy != "auto"):
                    text = self._fim_or_fallback(completer, prefix, suffix, on_delta)
                else:
                    text = completer.complete_lines(prefix, on_delta=on_delta)

            if not text.strip():
                self._hide_card_safe()
                self._notify_safe("没有生成可插入的代码", "可以补充相关上下文，或在选区里说明想改什么。")
                return
            if not target_still_current():
                self._hide_card_safe()
                self._notify_safe("已取消", "等待期间前台窗口变了，没有插入。")
                return

            pipeline = self._pipeline
            if pipeline is not None and self.settings.ai_card_enabled:
                # 挂起等确认：倒计时到点自己关，也可以随时按 Esc 关掉。
                if pipeline.offer(text, backspaces=0, label=label, hwnd=hwnd,
                                  timeout=self.settings.ai_card_timeout):
                    log.info("%s：已挂起 %d 字，等 Tab 确认", label, len(text))
                    return
            self._hide_card_safe()
            injector = self._injector
            if injector is not None:
                injector.send_block(text, always_paste=True)
            else:      # pragma: no cover —— 装配正常时不会走到这里
                input_simulator.send_unicode_text(text)
            self._notify_safe("已插入演练示例" if demo else "已插入", f"{len(text)} 字")
        except AiConfigurationError as exc:
            self._hide_card_safe()
            self._notify_safe("AI 配置需要检查", str(exc))
        except AiOutputIncomplete as exc:
            self._hide_card_safe()
            self._notify_safe("补全未完成", str(exc))
        except _AiCancelled:
            # 用户按 Esc 主动掐掉的 —— 不弹"失败"，也别留半截卡片
            self._hide_card_safe()
            log.info("AI 生成被 Esc 取消")
            self._notify_safe("已取消", "生成被掐断了，没有插入任何内容。")
        except Exception as exc:      # noqa: BLE001
            self._hide_card_safe()
            log.exception("AI 生成失败")
            self._notify_safe("AI 补全失败", str(exc)[:160])
        finally:
            try:
                self._ai_busy.release()
            except Exception:      # noqa: BLE001
                pass

    def _context_grab_failed(self, result) -> bool:
        if result.caret_uncertain:
            self._notify_safe("已取消补全", "无法确认光标已回到原位，请检查编辑位置后重试。")
        elif result.modifiers_stuck:
            self._notify_safe("按键还没松开", f"按完 {self.settings.ai_hotkey} 把键都松开，再试一次。")
        return result.caret_uncertain or result.modifiers_stuck

    def _fim_or_fallback(self, completer, prefix: str, suffix: str, on_delta) -> str:
        """先走 FIM，不行就退回老 chat 补全。

        为什么要有兜底：FIM 依赖服务端支持 ``/completions``。碰上不支持的服务
        （只给 chat 端点的云 API 很常见），直接抛异常的表现是"按了热键什么都没
        发生"，用户完全不知道发生了什么。退回 chat 至少还能补出点东西。
        """
        strategy = getattr(self.settings, "ai_fim_strategy", "auto")
        tokens = getattr(self.settings, "ai_fim_tokens", "auto")
        try:
            text = completer.complete_fim(
                prefix, suffix, MAX_AUTO_LINES,
                on_delta=on_delta, strategy=strategy, tokens=tokens)
            if text.strip():
                return text
            log.info("FIM 返回空，退回普通补全")
        except _AiCancelled:
            # 用户按 Esc 掐的 —— 绝不能当成"FIM 失败，退回普通补全"，
            # 否则取消之后会立刻又发一次请求，白等两遍。
            raise
        except Exception as exc:      # noqa: BLE001
            log.warning("FIM 补全失败（%s），退回普通补全", exc)
        return completer.complete_lines(prefix, on_delta=on_delta, suffix=suffix)

    def cancel_pending(self) -> None:
        """预览窗倒计时到点时由界面线程调进来。"""
        if self._pipeline is not None:
            self._pipeline.cancel_pending()

    def _on_escape(self) -> None:
        """键盘钩子看见 Esc 时调这里：正在生成就掐掉。

        只在 ``_ai_busy`` 被占着时才置位 —— 平时按 Esc（关闭对话框、编辑器
        里退出）不该留下一个"下次生成立刻取消"的脏标记。清理动作在
        ``_ai_selection_worker`` 开头做，双保险。
        """
        if self._ai_busy.locked():
            self._ai_cancel.set()
            log.info("Esc：已请求取消当前生成")

    def suspend_hotkeys(self) -> None:
        """录制热键期间临时注销全局热键。

        不注销的话，按到已注册的组合键会被 RegisterHotKey 吃掉 key-down，
        对话框只收到 key-up —— 表现成"按了没反应 / Alt 识别不到"。
        """
        if self._hotkeys is not None:
            self._hotkeys.unregister_all()

    def resume_hotkeys(self) -> None:
        """录制结束后按当前设置重新注册。"""
        if self._hotkeys is not None:
            self._setup_hotkeys()

    def test_ai(self) -> None:
        """界面上那个「测试连接」按钮。**真发一次请求**，不是看配置猜。"""
        try:
            completer = create_completer(self.settings)
        except AiConfigurationError as exc:
            self._notify_safe("AI 配置需要检查", str(exc))
            return
        if isinstance(completer, MockCompleter):
            self._notify_safe("当前为本地演练", "演练模式使用固定示例。选择实际服务后可测试连接。")
            return
        self._notify_safe("正在测试 AI 连接…", "最多等 20 秒。")

        def worker() -> None:
            result: ProbeResult
            try:
                result = probe(completer)
            except Exception as exc:      # noqa: BLE001
                result = ProbeResult(False, f"{exc.__class__.__name__}: {exc}")
            if result.ok:
                self._notify_safe("AI 连接成功", f"{completer.describe()}\n{result.detail}")
            else:
                self._notify_safe("AI 连接失败", result.detail[:200])

        threading.Thread(target=worker, name="codepoint-ai-probe", daemon=True).start()

    def _notify_safe(self, title: str, message: str) -> None:
        """从任何线程弹托盘气泡。失败就只记日志 —— 通知发不出去不该影响主流程。"""
        try:
            self.notify(title, message)
        except Exception:      # noqa: BLE001
            log.debug("通知发送失败：%s / %s", title, message)

    def _on_tray_command(self, command: int) -> None:
        if command == CMD_SHOW:
            self.show_window()
        elif command == CMD_TOGGLE:
            self.set_global_enabled(not self.is_global_enabled())
            self._refresh_tray_menu()
            self.bridge.publish(UiBridge.SETTINGS_CHANGED)
        elif command == CMD_SETTINGS:
            self.show_window()
        elif command == CMD_QUIT:
            self.quit()

    def _refresh_tray_menu(self) -> None:
        if self._tray is None:
            return
        enabled = self.is_global_enabled()
        self._tray.set_menu([
            TrayMenuItem(CMD_SHOW, "显示主窗口"),
            TrayMenuItem(CMD_TOGGLE, "启用功能", checked=enabled),
            TrayMenuItem(CMD_SETTINGS, "设置…", separator_before=True),
            TrayMenuItem(CMD_QUIT, "退出", separator_before=True),
        ])
        state = "已启用" if enabled else "已暂停"
        self._tray.set_tooltip(f"{APP_DISPLAY_NAME} v{__version__}  ·  {state}")

    # ---------------------------------------------------------------- 剪贴板

    def _on_clipboard_changed(self) -> None:
        """在消息线程上被调用 —— 只丢一个信号，重的活交给 worker。"""
        if not self.settings.enable_clipboard_replace:
            return
        try:
            self._clipboard_jobs.put_nowait(True)
        except queue.Full:
            pass

    def _clipboard_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._clipboard_jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_clipboard()
            except Exception:      # noqa: BLE001
                log.exception("处理剪贴板时异常")

    def _process_clipboard(self) -> None:
        if not self.settings.enable_clipboard_replace:
            return
        sequence = clipboard_io.sequence_number()
        listener = self._clipboard_listener
        if listener is not None and listener.is_suppressed(sequence):
            return
        text = clipboard_io.get_text()
        if not text or not self._clipboard_filter.should_rewrite(text):
            return
        converted, count = self._clipboard_filter.transform(text)
        if count and converted != text:
            if clipboard_io.set_text(converted, expected_sequence=sequence,
                                     on_written=self._suppress_clipboard_listener):
                if self._pipeline:
                    self._pipeline.stats.last_reason = f"剪贴板净化 {count} 处"
                log.info("剪贴板已净化 %d 处标点", count)

    # ================================================================ AppFacade

    def get_settings(self) -> Settings:
        return self.settings

    def save(self) -> bool:
        ok = self.store.save(self.settings)
        if ok:
            self._baseline = self._fingerprint()      # 落盘成功才更新基线
        return ok

    def apply_live(self) -> None:
        assert self._pipeline is not None
        self._pipeline.apply_settings(self.settings)

        self._clipboard_filter.configure(
            text_conversion_map(self.settings.punctuation_rules),
            enabled=self.settings.enable_clipboard_replace,
        )
        if self._notifier is not None:
            self._notifier.set_enabled(self.settings.indicator_enabled)
        self.settings.normalize()
        if self._hotkeys is not None:
            self._hotkeys.unregister(HOTKEY_TOGGLE)
            if self.settings.toggle_hotkey:
                self._hotkeys.register_text(HOTKEY_TOGGLE, self.settings.toggle_hotkey)
            # AI 热键可能刚被改过（或者 AI 刚被关掉 → 要注销掉，别留个哑热键占着）
            self._register_ai_hotkey()
        self._refresh_tray_menu()

    def set_global_enabled(self, enabled: bool) -> None:
        self.settings.global_enabled = enabled
        if self._pipeline is not None:
            self._pipeline.set_enabled(enabled)

    def is_global_enabled(self) -> bool:
        # 以 settings 为唯一事实来源（set_global_enabled 会同步推给管道）
        return bool(self.settings.global_enabled)

    def set_autostart(self, enabled: bool) -> bool:
        ok = autostart_mod.sync(enabled)
        if ok:
            self.settings.enable_auto_startup = enabled
        return ok

    # ---------------------------------------------------------------- 配置导入导出

    def inspect_config(self, path: str) -> Tuple[bool, str]:
        """只看不改：读一个配置文件并回报里面有什么（导入前给用户过目）。"""
        try:
            result = portable.load_export(path)
        except portable.ConfigFileError as exc:
            return False, str(exc)
        return True, f"{result.source_text()}\n将导入：{result.describe()}"

    def export_config(self, path: str) -> Tuple[bool, str]:
        """把当前配置导出到 ``path``。返回 ``(是否成功, 给用户看的一句话)``。"""
        try:
            target = portable.write_export(self.settings, path)
        except OSError as exc:
            log.warning("导出配置失败：%s", exc)
            return False, f"写不了这个位置：{exc}"
        log.info("配置已导出到 %s", target)
        return True, f"已导出到 {target}"

    def import_config(self, path: str) -> Tuple[bool, str]:
        """从 ``path`` 导入配置并**立即生效 + 落盘**。返回 ``(是否成功, 说明)``。

        导入是"整体替换"而不是"合并" —— 用户导出就是为了把那一整套搬过去，
        合并只会得到两边都不像的结果。
        """
        try:
            result = portable.load_export(path)
        except portable.ConfigFileError as exc:
            return False, str(exc)

        self.settings = result.settings
        self.apply_live()
        saved = self.store.save(self.settings)
        log.info("已导入配置：%s（%s）", result.describe(), result.source_text())
        note = f"已导入 {result.describe()}。"
        if not saved:
            note += "（内存里已生效，但写入配置文件失败）"
        return True, note

    def capture_foreground_app(self, delay_ms: int = 0) -> Optional[WindowInfo]:
        """抓当前前台程序。``delay_ms`` > 0 时先等一会儿（用户要切窗口）。"""
        if delay_ms > 0:
            time.sleep(min(delay_ms, 5000) / 1000.0)
        foreground.invalidate_cache()
        info = foreground.query(use_cache=False)
        if info.valid and info.pid == os.getpid():
            return WindowInfo()      # 抓到自己没有意义
        return info

    # ---------------------------------------------------------------- 程序识别
    #
    # 界面**不直接碰 Win32**：这三个方法把平台层的结果包成中立数据再交出去，
    # 所以界面层可以脱离 Windows 单独测试（NullFacade 也能造预览数据）。

    def foreground_app(self) -> Optional[WindowInfo]:
        """当前前台程序（走缓存，1ms 级，界面可以每秒调）。"""
        info = foreground.query()
        if not info.valid or info.pid == os.getpid():
            return None
        return info

    def list_running_apps(self) -> List[object]:
        """所有有可见窗口的程序（供「选择程序」对话框用）。"""
        try:
            return processes.list_running_apps(exclude_pids=[os.getpid()])
        except Exception:      # noqa: BLE001
            log.exception("枚举运行中程序失败")
            return []

    def describe_scope(self, window: Optional[WindowInfo]) -> str:
        if self._pipeline is None:
            return "后台未运行"
        if window is None:
            return "没有前台程序"
        ok, reason = self._pipeline.app_filter.decide(window)
        state = "已生效" if ok else "未生效"
        return (f"当前程序：{window.process_name or '未知'}（{state}）"
                f" · {_SCOPE_HINT.get(reason, reason)}")

    def is_in_scope(self, window: Optional[WindowInfo]) -> bool:
        if self._pipeline is None or window is None:
            return False
        return self._pipeline.app_filter.matches(window)

    def self_pid(self) -> int:
        return os.getpid()

    def runtime_hint(self) -> str:
        """主页状态栏下面那行小字 —— 只在"有事"的时候说话。

        两种情况值得说：
          · 钩子掉了 / 被自动重挂（标点替换刚失效过）；
          · 有大量"本该转换却放行"的按键（修饰键卡住这类问题会刷出几百条）。
        平时返回空串，界面上那行就隐形，不打扰。
        """
        if self._hook_health is not None and (
            self._hook_health.repairs or self._hook_health.gave_up
        ):
            return f"⚠ {self._hook_health.status_text}"

        if self._pipeline is None:
            return ""
        top = self._pipeline.stats.top_blocks(1)
        if not top:
            return ""
        code, count = top[0]
        # 阈值刻意高：偶尔几次"正在组字""本来就是半角"完全正常，
        # 只有**成规模**的同类放行才值得提示（卡住修饰键一次就是几百条）。
        if count < 50 or code not in ("shortcut", "no_char"):
            return ""
        return (f"⚠ 最近有 {count} 次按键没能转换（{_REASON_CN.get(code, code)}）。"
                f"若并没按着 Ctrl/Alt/Win，试试退出后重新启动本程序。")

    def stats_text(self) -> str:
        if self._pipeline is None:
            return "后台未运行"
        s = self._pipeline.stats
        state = "运行中" if self.is_global_enabled() else "已暂停"
        lines = [
            f"状态：{state}    ·    受管程序 {len(self.settings.app_items)} 个"
            f"    ·    标点规则 {self._pipeline.punctuation.rule_count} 条"
            f"（启用 {self._pipeline.punctuation.enabled_count}）"
            f"    ·    快捷项 {self._pipeline.quick_actions.action_count} 条",
            f"累计：按键 {s.keys_seen}    拦截 {s.keys_swallowed}"
            f"    标点替换 {s.punctuation_replaced}    快捷补全 {s.quick_completed}",
        ]
        if self._hook_health is not None:
            lines.append(self._hook_health.status_text)
        blocked = " · ".join(
            f"{_REASON_CN.get(code, code)}×{count}" for code, count in s.top_blocks()
        )
        if blocked:
            lines.append(f"没转换的原因：{blocked}")
        if s.last_reason:
            lines.append(f"最近：{s.last_reason}")
        return "\n".join(lines)

    def notify(self, title: str, message: str) -> None:
        if self._tray is not None:
            self._tray.show_balloon(title, message)

    # ---------------------------------------------------------------- 界面动作
    #
    # 下面三个方法**都可能从别的线程被调用**（托盘菜单和全局热键跑在消息线程上）。
    # 而 tkinter 不是线程安全的：从别的线程直接 root.withdraw() / destroy()，
    # 调用的返回时间看着正常（几毫秒），但窗口其实没反应 ——
    # 实测"托盘右键退出"能一直卡住不退出。
    #
    # 所以这里一律只往事件桥里投一个事件，由界面线程的轮询去执行。
    # 投递是纯内存操作，任何线程调用都安全，最多晚 30 毫秒生效。

    def hide_to_tray(self) -> None:
        if self._window is None:
            return
        self.bridge.publish(UiBridge.HIDE_WINDOW)

    def show_window(self) -> None:
        if self._window is None:
            return
        self.bridge.publish(UiBridge.SHOW_WINDOW)

    def quit(self) -> None:
        if self._window is None:
            self.shutdown()            # 无界面（--headless）时直接收尾
            return
        self.bridge.publish(UiBridge.QUIT)
