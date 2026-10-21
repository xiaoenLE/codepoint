"""配置的导入 / 导出（纯逻辑，不碰界面、不碰注册表）。

**为什么要有这一层**：配置文件和程序是分开的，用户换电脑、重装、
或者想把"调好的那套标点表"给同事，只能手动去 ``%APPDATA%`` 里翻文件 ——
那个目录是隐藏的，很多人根本找不到。

导出文件长这样（多包了一层，为了能自证身份）::

    {
      "format": "codepoint-settings",     ← 认这个字段判断是不是我们的文件
      "version": 1,
      "app": "码点助手",
      "app_version": "1.0.0",
      "exported_at": "2026-10-06T23:40:12",
      "settings": { ... 和 settings.json 里一模一样 ... }
    }

**导入时是宽容的**：
  · 认得上面的封装格式；
  · 也认得**裸的 settings.json**（用户直接把 %APPDATA% 里那个文件拷过来最常见）；
  · 缺字段、多字段、类型不对都不报错 —— 缺的补默认值，多的忽略
    （旧版本导出的文件导进新版本必须能用，这是底线）。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import APP_DISPLAY_NAME, __version__
from .config import merge_with_defaults
from .models import Settings

#: 导出文件里用来辨认"这是我们导出的"的标记
EXPORT_FORMAT = "codepoint-settings"
EXPORT_VERSION = 1

#: 裸配置文件里认得的字段名。
#: 直接从 ``Settings`` 的 dataclass 字段推出来，以后加字段不用回来改这里
#: （写死一份清单的话，早晚会忘）。
_KNOWN_KEYS = frozenset(f.name for f in dataclass_fields(Settings))

#: 界面里文件对话框用的后缀
FILE_TYPES = (("配置文件", "*.json"), ("所有文件", "*.*"))
DEFAULT_FILENAME = "码点助手-配置.json"


class ConfigFileError(ValueError):
    """导入失败时抛出，``str(exc)`` 直接就是要显示给用户的中文说明。"""


@dataclass(frozen=True)
class ImportResult:
    """一次导入的结果（供界面显示"到底导进来了什么"）。"""

    settings: Settings
    counts: Dict[str, int]         # {"punctuation_rules": 33, ...}
    exported_at: str = ""
    app_version: str = ""
    from_bare_file: bool = False   # 来源是裸 settings.json 而不是我们的导出文件

    def describe(self) -> str:
        """一句话说明导入了多少内容。"""
        return "、".join(
            f"{n} 条{label}" for label, n in (
                ("标点规则", self.counts.get("punctuation_rules", 0)),
                ("快捷输入", self.counts.get("quick_actions", 0)),
                ("程序", self.counts.get("app_items", 0)),
            )
        )

    def source_text(self) -> str:
        """来源说明：导出时间 + 版本。"""
        if self.from_bare_file:
            return "来源：settings.json（旧格式或直接拷贝的配置文件）"
        bits = []
        if self.exported_at:
            bits.append(f"导出于 {self.exported_at}")
        if self.app_version:
            bits.append(f"版本 {self.app_version}")
        return "来源：" + ("　".join(bits) if bits else "配置文件")


# ------------------------------------------------------------------ 导出


def build_payload(settings: Settings, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """把设置打包成待写入的字典。"""
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "app": APP_DISPLAY_NAME,
        "app_version": __version__,
        "exported_at": stamp,
        "settings": settings.to_dict(),
    }


def write_export(settings: Settings, path: str | Path,
                 *, now: Optional[datetime] = None) -> Path:
    """导出到指定路径。

    先写临时文件再 ``os.replace`` —— 直接覆写的话，中途出错会把用户
    原有的备份文件毁掉一半（半截 JSON 谁都修不回来）。
    """
    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target.with_suffix(".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(build_payload(settings, now=now),
                      ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".export-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


# ------------------------------------------------------------------ 导入


def _extract_settings_dict(raw: Any) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """从读到的 JSON 里挖出真正的设置字典。

    返回 ``(设置字典, 外层元信息, 是否来自裸文件)``。
    """
    if not isinstance(raw, dict):
        raise ConfigFileError("这个文件不是一个配置对象（顶层应该是 JSON 大括号）。")

    fmt = raw.get("format")
    if fmt == EXPORT_FORMAT:
        inner = raw.get("settings")
        if not isinstance(inner, dict):
            raise ConfigFileError("文件里写着是我们导出的配置，但 settings 段缺失或损坏。")
        return inner, raw, False

    if fmt not in (None, ""):
        raise ConfigFileError(f"这是别的程序导出的配置（format={fmt!r}），不能导入。")

    # 没有任何已知字段 → 无论是不是 JSON，都不是配置
    if not (_KNOWN_KEYS & set(raw.keys())):
        raise ConfigFileError("文件里找不到任何已知配置项（标点规则 / 快捷输入 / 程序列表）。")

    return raw, {}, True


def load_export(path: str | Path) -> ImportResult:
    """读一个导出文件（或裸 settings.json）并解析成 :class:`Settings`。

    缺字段回落默认、类型不对的字段忽略 —— **能救回来就救**，
    只有在"根本不是配置文件"时才抛 :class:`ConfigFileError`。
    """
    target = Path(path)
    if not target.exists():
        raise ConfigFileError(f"找不到文件：{target}")
    try:
        text = target.read_text(encoding="utf-8-sig")     # 容忍记事本加的 BOM
    except UnicodeDecodeError:
        raise ConfigFileError("文件不是 UTF-8 文本，可能是别的东西改的后缀名。") from None
    except OSError as exc:
        raise ConfigFileError(f"读不了这个文件：{exc}") from None

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigFileError(f"JSON 格式错误（第 {exc.lineno} 行）：{exc.msg}") from None

    inner, meta, bare = _extract_settings_dict(raw)
    settings = merge_with_defaults(inner)

    counts = {
        "punctuation_rules": len(settings.punctuation_rules),
        "quick_actions": len(settings.quick_actions),
        "app_items": len(settings.app_items),
    }
    return ImportResult(
        settings=settings,
        counts=counts,
        exported_at=str(meta.get("exported_at") or ""),
        app_version=str(meta.get("app_version") or ""),
        from_bare_file=bare,
    )


__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_VERSION",
    "FILE_TYPES",
    "DEFAULT_FILENAME",
    "ConfigFileError",
    "ImportResult",
    "build_payload",
    "write_export",
    "load_export",
]
