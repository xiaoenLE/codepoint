"""配置装配：路径解析、默认值合并、旧版本迁移。

只做"纯逻辑 + 文件系统路径"，不做 IO（IO 在 storage.py）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .. import APP_NAME
from .defaults import (
    default_app_items,
    default_punctuation_rules,
    default_quick_actions,
)
from .models import DETECT_CHAR, SCHEMA_VERSION, Settings

SETTINGS_FILENAME = "settings.json"
BACKUP_SUFFIX = ".bak"

#: v1.3.1 起不再支持的检测方式（「前缀 + 参数（AI）」，即 `//ai xxx` + Tab）。
#: 留着这个字面量只是为了在加载旧配置时认得它、把它清掉。
RETIRED_PREFIX_MODE = "prefix"


@dataclass(frozen=True)
class ConfigPaths:
    """所有可写位置集中在这里，便于测试注入与绿色版迁移。"""

    base_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.base_dir / SETTINGS_FILENAME

    @property
    def log_dir(self) -> Path:
        return self.base_dir / "logs"

    def ensure(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def portable_dir() -> Path:
    """便携模式目录：exe/脚本同级存在 portable.flag 时启用。"""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[2]
    return root


def default_config_dir() -> Path:
    """默认配置目录：``%APPDATA%\\CodePoint``（不往程序目录里写东西）。"""
    if os.name != "nt":
        return Path.home() / ".config" / APP_NAME
    appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_NAME


def resolve_config_dir(override: Optional[str] = None) -> Path:
    """优先级：显式参数 > 便携标记 > %APPDATA%。"""
    if override:
        return Path(override).expanduser().resolve()
    portable = portable_dir() / "portable.flag"
    if portable.exists():
        return portable_dir() / "data"
    return default_config_dir()


def build_default_settings() -> Settings:
    """全新安装时的默认设置。"""
    s = Settings()
    s.punctuation_rules = default_punctuation_rules()
    s.quick_actions = default_quick_actions()
    s.app_items = default_app_items()
    return s


def removed_retired_modes(raw: Dict[str, Any]) -> bool:
    """这次加载有没有真的清掉东西。给 storage 用：清了就顺手写回磁盘。

    不写回也能用（每次加载都会清），但用户打开 settings.json 还会看到
    那条 `//ai`，会以为没删干净。
    """
    if not isinstance(raw, dict):
        return False
    return _drop_retired_modes(raw) is not raw


def merge_with_defaults(raw: Dict[str, Any]) -> Settings:
    """把磁盘上的字典合并进默认设置：缺项补默认，未知项忽略。

    这样即使用户的 settings.json 是旧版本写的、字段不全，也不会崩。
    """
    merged = build_default_settings()
    src: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    # v4 → v5：清掉已废弃的「前缀 + 参数（AI）」规则。
    #
    # ⚠ 必须在 from_dict **之前**按原始字典过滤。from_dict 会把不认识的
    #   detect_mode 回落成「整词」，那样旧的 `//ai` 规则会变成
    #   "敲 //ai 就原样插入 ${prompt}" —— 比直接删掉更糟。
    src = _drop_retired_modes(src)
    loaded = Settings.from_dict(src)

    # 标量字段：磁盘上写了就用磁盘的，没写就保留默认
    for field_name in (
        "global_enabled",
        "enable_clipboard_replace",
        "enable_auto_startup",
        "start_hidden",
        "require_ime",
        "smart_guard_compose",
        "quick_action_enabled",
        "indicator_enabled",
        # ⚠ AI 这组必须列进来：merge 的规则是"白名单里的字段才从磁盘取"，
        #   漏一个就会让用户在界面上填好的地址/模型在下次启动时被打回默认值。
        "ai_enabled",
        "ai_provider",
        "ai_base_url",
        "ai_model",
        "ai_api_key",
        "ai_timeout",
        "ai_temperature",
        "ai_max_tokens",
        "ai_disable_thinking",
        "ai_hotkey",
        "ai_card_enabled",
        "ai_card_bg",
        "ai_card_fg",
        "ai_card_alpha",
        "ai_card_font_size",
        "ai_card_max_lines",
        "ai_card_width",
        "ai_card_timeout",
        "ai_context_lines",
        # FIM 这组同理：漏了就会表现为"界面上开了 FIM，重启又变回关"。
        "ai_fim_enabled",
        "ai_fim_strategy",
        "ai_fim_tokens",
        "ai_suffix_lines",
        "ai_prefix_max_chars",
        "ai_suffix_max_chars",
        "ai_insert_mode",
        "ai_scope_limited",
        "minimize_to_tray",
        "toggle_hotkey",
        "match_mode",
    ):
        if field_name in src:
            setattr(merged, field_name, getattr(loaded, field_name))

    # 列表字段：磁盘上"存在且是列表"就采用（空列表也尊重——用户可能全删了）
    for field_name in ("punctuation_rules", "quick_actions", "app_items"):
        if isinstance(src.get(field_name), list):
            setattr(merged, field_name, getattr(loaded, field_name))

    # ---- 旧版本迁移（靠 schema 版本号保证只跑一次）----
    try:
        from_schema = int(src.get("schema", 1) or 1)
    except (TypeError, ValueError):
        from_schema = 1
    if from_schema < 2:
        # v1 → v2 的迁移只对 v1 配置有意义（它本身是幂等的，但别让 v2
        # 的配置反复过一遍没必要的迁移）
        _migrate_from_v1(merged)
    # v2 → v3（性能监控）、v3 → v4（AI 配置）都只是新增字段，
    # from_dict 对缺键会给默认值，没有需要改写的旧数据。
    # v4 → v5（删掉前缀触发）走 _drop_retired_modes，而且必须早于 from_dict。
    merged.schema = SCHEMA_VERSION
    merged.normalize()
    return merged


def _migrate_from_v1(s: Settings) -> None:
    """v1 → v2：`:` → `:=` 这条单字符规则默认改为**停用**。

    v1 把它默认启用，而单字符触发会**永久劫持**那个字符 —— 冒号要靠
    Shift+; 才打得出来，于是在受管程序里按冒号永远变成 `:=`，
    CASE 标签 `0:`、变量声明 `x : INT;` 全部废掉（用户实测报障）。

    只动"还是出厂状态"的规则：内容被用户改过的说明真的在用，不碰。
    """
    for action in s.quick_actions:
        if (action.trigger == ":" and action.detect_mode == DETECT_CHAR
                and action.content == ":="):
            action.enabled = False


def _drop_retired_modes(src: Dict[str, Any]) -> Dict[str, Any]:
    """删掉已不再支持的检测方式对应的规则（目前只有 ``prefix``）。

    v1.3.1 删掉了「前缀 + 参数（AI）」这条触发方式（`//ai xxx` + Tab）：
    它和「选中一段 + 热键」功能重复，而且中文提示词只能到拼音
    （钩子看不到输入法候选结果）。旧配置里留着它的话，界面上会挂一条
    永远不触发的死规则 —— 直接在加载时清掉。

    幂等：删过一次之后磁盘上就没有这种东西了，所以不必挂 schema 版本号。
    """
    actions = src.get("quick_actions")
    if not isinstance(actions, list):
        return src
    kept = [
        item for item in actions
        if not (isinstance(item, dict)
                and str(item.get("detect_mode") or "").strip().lower() == RETIRED_PREFIX_MODE)
    ]
    if len(kept) == len(actions):
        return src
    cleaned = dict(src)
    cleaned["quick_actions"] = kept
    return cleaned
