"""开机自启（写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run）。

只碰当前用户注册表，不需要管理员权限，也不动系统其它位置。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CodePoint"


def _winreg():
    try:
        import winreg       # type: ignore
        return winreg
    except ImportError:      # pragma: no cover - 非 Windows
        return None


def _pythonw() -> str:
    """优先用 pythonw.exe，避免自启时弹黑窗。"""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def default_command() -> str:
    """当前运行方式对应的自启命令行（带引号）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    entry = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{_pythonw()}" "{entry}" --minimized'


def current_value() -> Optional[str]:
    winreg = _winreg()
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_enabled() -> bool:
    return current_value() is not None


def enable(command: Optional[str] = None) -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    cmd = command or default_command()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
        return True
    except OSError:
        return False


def disable() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return False


def sync(enabled: bool) -> bool:
    """让注册表状态与期望一致。返回是否与期望一致。"""
    if enabled:
        if is_enabled() and current_value() == default_command():
            return True
        return enable()
    if not is_enabled():
        return True
    return disable()
