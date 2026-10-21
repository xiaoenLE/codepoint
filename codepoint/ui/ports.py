"""界面层需要的外部能力（由 app.py 的实现满足）。

UI 只声明它**需要什么**，不关心谁来实现——这样界面可以独立于业务/平台编译和测试。
注意这里**刻意不出现任何 platform_win 类型**：进程枚举、前台窗口这些能力都由
组装根（app.py）包成中立数据再交给界面。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Protocol, Tuple

from ..core.events import WindowInfo
from ..core.models import Settings


class AppFacade(Protocol):
    """主窗口要用到的全部应用能力。"""

    # —— 配置 ——
    def get_settings(self) -> Settings:
        ...

    def save(self) -> bool:
        """落盘保存，返回是否成功。"""

    def apply_live(self) -> None:
        """把当前内存中的设置立刻推给运行中的管道（不落盘）。"""

    def inspect_config(self, path: str) -> Tuple[bool, str]:
        """只看不改：读一个配置文件并回报里面有什么。返回 ``(是否可读, 说明)``。

        导出文件里本来就有"导出于什么时候、哪个版本、含多少条内容"，
        导入前先给用户看一眼，比事后猜"我刚导进去的东西在哪"强得多。
        """

    def export_config(self, path: str) -> Tuple[bool, str]:
        """导出当前配置。返回 ``(是否成功, 给用户看的一句话)``。"""

    def import_config(self, path: str) -> Tuple[bool, str]:
        """导入配置（整体替换 + 立即生效 + 落盘）。返回 ``(是否成功, 说明)``。"""

    # —— 运行控制 ——
    def set_global_enabled(self, enabled: bool) -> None:
        ...

    def is_global_enabled(self) -> bool:
        ...

    def set_autostart(self, enabled: bool) -> bool:
        ...

    # —— 程序识别（界面只拿结果，不碰 Win32）——
    def foreground_app(self) -> Optional[WindowInfo]:
        """当前前台程序（便宜、带缓存，可以每秒调）。"""

    def capture_foreground_app(self, delay_ms: int = 0) -> Optional[WindowInfo]:
        """抓取当前前台程序（用于"添加程序"）。"""

    def list_running_apps(self) -> List["RunningAppView"]:
        """所有有可见窗口的程序，供界面直接挑选（按 pid 去重）。"""

    def describe_scope(self, window: Optional[WindowInfo]) -> str:
        """给一句话说明"当前程序生不生效"，直接显示在主页上。"""

    def is_in_scope(self, window: Optional[WindowInfo]) -> bool:
        """当前程序是否处于生效范围。"""

    def self_pid(self) -> int:
        """本程序自己的 pid（界面用来把自己排除掉）。"""

    # —— 统计 / 提示 ——
    def stats_text(self) -> str:
        ...

    def runtime_hint(self) -> str:
        """一句话运行提示：**只在有事的时候**才返回内容，平时返回空串。

        给主页状态栏下面那行小字用 —— 标点替换静默失效时（钩子被系统摘掉、
        修饰键状态卡住），用户至少能看到"为什么不转换"，而不是一个个字试。
        """

    def notify(self, title: str, message: str) -> None:
        ...

    def test_ai(self) -> None:
        """真发一次请求测 AI 后端。**必须立刻返回**（内部自己开线程），
        否则点一下按钮整个窗口要卡几秒，用户会以为程序死了。"""
        ...

    def cancel_pending(self) -> None:
        """作废当前挂起的补全（预览窗倒计时到点时调用）。"""
        ...

    # —— 热键录制 ——
    def suspend_hotkeys(self) -> None:
        """录制对话框期间**临时注销全局热键**。

        不注销的话，按到已注册的组合键会被 RegisterHotKey 吃掉 key-down，
        对话框只收到 key-up，表现成"这个键没反应"。
        """
        ...

    def resume_hotkeys(self) -> None:
        """录制结束后重新注册。"""
        ...

    # —— 生命周期 ——
    def hide_to_tray(self) -> None:
        ...

    def quit(self) -> None:
        ...


class RunningAppView(Protocol):
    """界面要显示的"一个正在运行的程序"。"""

    pid: int
    process_name: str
    display_name: str
    path: str
    title: str

    def matches(self, text: str) -> bool:
        ...


class NullFacade:
    """无后台时的空实现，让界面可以单独跑起来看效果（也用于界面冒烟测试）。"""

    def __init__(self, preview_apps: bool = True) -> None:
        from ..core.config import build_default_settings
        self._settings = build_default_settings()
        self._preview = preview_apps

    # ------------------------------------------------------------ 配置

    def get_settings(self) -> Settings:            # noqa: D102
        return self._settings

    def save(self) -> bool:                        # noqa: D102
        return True

    def apply_live(self) -> None:                  # noqa: D102
        return None

    # 预览模式的导入导出走**真的** portable 模块（纯逻辑、不碰 Win32），
    # 这样界面截图里能点、能真存文件，不必再造一份假的。
    def inspect_config(self, path: str) -> Tuple[bool, str]:      # noqa: D102
        from ..core.portable import ConfigFileError, load_export
        try:
            result = load_export(path)
        except ConfigFileError as exc:
            return False, str(exc)
        return True, f"{result.source_text()}\n将导入：{result.describe()}"

    def export_config(self, path: str) -> Tuple[bool, str]:        # noqa: D102
        from ..core.portable import write_export
        try:
            target = write_export(self._settings, path)
        except OSError as exc:
            return False, f"写不了这个位置：{exc}"
        return True, f"已导出到 {target}（预览模式）"

    def import_config(self, path: str) -> Tuple[bool, str]:        # noqa: D102
        from ..core.portable import ConfigFileError, load_export
        try:
            result = load_export(path)
        except ConfigFileError as exc:
            return False, str(exc)
        self._settings = result.settings
        return True, f"已导入 {result.describe()}。（预览模式，未落盘）"

    # ------------------------------------------------------------ 运行控制

    def set_global_enabled(self, enabled: bool) -> None:   # noqa: D102
        self._settings.global_enabled = enabled

    def is_global_enabled(self) -> bool:           # noqa: D102
        return self._settings.global_enabled

    def set_autostart(self, enabled: bool) -> bool:        # noqa: D102
        self._settings.enable_auto_startup = enabled
        return True

    # ------------------------------------------------------------ 程序识别

    def foreground_app(self) -> Optional[WindowInfo]:      # noqa: D102
        if not self._preview:
            return None
        return WindowInfo(hwnd=1, pid=4242, process_name="codesys.exe",
                          process_path=r"C:\Program Files\CODESYS\CODESYS.exe",
                          window_title="CODESYS - 未命名工程")

    def capture_foreground_app(self, delay_ms: int = 0) -> Optional[WindowInfo]:   # noqa: D102
        return self.foreground_app()

    def list_running_apps(self) -> List[object]:   # noqa: D102
        if not self._preview:
            return []
        return [_PreviewApp("codesys.exe", "CODESYS", r"C:\Program Files\CODESYS\CODESYS.exe"),
                _PreviewApp("Invtmatic Studio.exe", "Invtmatic Studio",
                            r"C:\Program Files\Invt\Invtmatic Studio\Invtmatic Studio.exe"),
                _PreviewApp("Code.exe", "Visual Studio Code",
                            r"C:\Users\Administrator\AppData\Local\Programs\Microsoft VS Code\Code.exe"),
                _PreviewApp("notepad.exe", "记事本", r"C:\Windows\System32\notepad.exe")]

    def describe_scope(self, window: Optional[WindowInfo]) -> str:      # noqa: D102
        if window is None:
            return "（预览模式，后台未运行）"
        return f"当前程序：{window.process_name or '未知'}（预览模式）"

    def is_in_scope(self, window: Optional[WindowInfo]) -> bool:        # noqa: D102
        return False

    def self_pid(self) -> int:                     # noqa: D102
        return 0

    # ------------------------------------------------------------ 统计 / 提示

    def stats_text(self) -> str:                   # noqa: D102
        return "（预览模式，后台未运行）"

    def runtime_hint(self) -> str:                 # noqa: D102
        return ""

    def notify(self, title: str, message: str) -> None:    # noqa: D102
        return None

    def test_ai(self) -> None:                     # noqa: D102
        return None

    def cancel_pending(self) -> None:              # noqa: D102
        return None

    def suspend_hotkeys(self) -> None:             # noqa: D102
        return None

    def resume_hotkeys(self) -> None:              # noqa: D102
        return None

    def hide_to_tray(self) -> None:                # noqa: D102
        return None

    def quit(self) -> None:                        # noqa: D102
        return None


class _PreviewApp:
    """NullFacade 用的假程序（界面预览时把卡片布局显示出来）。"""

    def __init__(self, process_name: str, display_name: str, path: str) -> None:
        self.pid = abs(hash(process_name)) % 30000 + 1000
        self.process_name = process_name
        self.display_name = display_name
        self.path = path
        self.title = display_name

    def matches(self, text: str) -> bool:
        t = (text or "").strip().lower()
        return (not t) or t in self.process_name.lower() or t in self.display_name.lower() \
            or t in self.path.lower()


ConfirmFn = Callable[[str, str], bool]

