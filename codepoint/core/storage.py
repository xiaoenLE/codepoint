"""配置持久化：原子写入 + 自动备份，读写失败不致命。"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import (
    ConfigPaths,
    build_default_settings,
    merge_with_defaults,
    removed_retired_modes,
)
from .models import Settings


class SettingsStore:
    """settings.json 的读写门面。

    设计要点：
    - **原子写**：先写临时文件再 ``os.replace``，断电/崩溃不会留下半截 JSON。
    - **一次备份**：写之前把旧文件复制成 ``settings.json.bak``。
    - **永不抛异常**：读失败→返回默认值并记录原因；写失败→返回 False。
    """

    def __init__(self, paths: ConfigPaths) -> None:
        self._paths = paths

    @property
    def path(self) -> Path:
        return self._paths.settings_file

    # ---------------------------------------------------------- 读

    def load(self) -> Tuple[Settings, Optional[str]]:
        """返回 (设置, 错误说明)。错误说明为 None 表示一切正常。"""
        p = self.path
        if not p.exists():
            return build_default_settings(), None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 —— 配置损坏不应导致程序打不开
            backup = p.with_suffix(p.suffix + ".corrupt")
            try:
                shutil.copy2(p, backup)
            except Exception:
                backup = None
            msg = f"配置文件解析失败({exc.__class__.__name__})，已用默认设置启动"
            if backup:
                msg += f"，原文件备份为 {backup.name}"
            return build_default_settings(), msg
        if not isinstance(raw, dict):
            return build_default_settings(), "配置文件格式不是对象，已用默认设置启动"
        merged = merge_with_defaults(raw)
        if removed_retired_modes(raw):
            # 清掉了已废弃的规则（例如 v1.3.1 删掉的 //ai）—— 顺手写回磁盘，
            # 否则用户每次打开 settings.json 都还能看见它，以为没删干净。
            self.save(merged)
        return merged, None

    # ---------------------------------------------------------- 写

    def save(self, settings: Settings) -> bool:
        """原子写入。成功返回 True。"""
        p = self.path
        try:
            self._paths.ensure()
            payload: Dict[str, Any] = settings.to_dict()
            text = json.dumps(payload, ensure_ascii=False, indent=2)

            fd, tmp_name = tempfile.mkstemp(
                prefix=".settings-", suffix=".tmp", dir=str(p.parent)
            )
            try:
                with open(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                    fh.flush()
                if p.exists():
                    try:
                        shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
                    except Exception:
                        pass
                Path(tmp_name).replace(p)
            finally:
                tmp = Path(tmp_name)
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    # ---------------------------------------------------------- 重置

    def reset_to_defaults(self) -> Settings:
        s = build_default_settings()
        self.save(s)
        return s
