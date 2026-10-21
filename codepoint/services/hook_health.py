"""键盘钩子存活看护。

**为什么需要它**：Windows 对超时的低级钩子不做任何通知，直接移除
（`LowLevelKeyboardProc` 文档原话：Windows 7 及以后静默移除，程序无从得知）。
钩子一旦没了，前台探测、界面、托盘全都照常工作 —— 只有"按键不再经过我们"，
用户看到的就是"能识别到程序，就是一个字都不转换，重启才好"。

所以这里做一件事：**怀疑它掉了就探一下，真掉了就地重挂**，不必重启程序。

判定逻辑（刻意的保守：探活会往焦点程序注入一个无害键，不能乱探）：

    ├─ 钩子本该在，却不在           → 直接重挂
    ├─ 距上次回调 < idle_seconds    → 活着（正常打字中，什么都不做）
    ├─ 距上次探活 < cooldown        → 跳过（不刷探）
    ├─ 用户没在动（GetLastInputInfo）→ 跳过（钩子安静是应该的，不是掉线）
    ├─ 前台不在生效范围内           → 跳过（这段本来就不归我们管，别去打扰）
    └─ 以上都不成立 → 探一下：认领了就是活着；没认领就是掉了 → 重挂 → 复探确认

连续失败到 ``max_failures`` 次就停手（避免"重挂→失败→再探"变成探活风暴），
并把状态留给界面显示。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("codepoint.hook_health")


@dataclass(frozen=True)
class HealthReport:
    """一次看护的结果（给界面/日志用）。"""

    probed: bool = False
    reinstalled: bool = False
    healed: bool = False           # 重挂之后复探确认恢复了
    reason: str = "ok"
    detail: str = ""

    @property
    def noteworthy(self) -> bool:
        """是否值得惊动用户（重挂过，或者救不回来）。"""
        return self.reinstalled or self.reason == "give_up"


class HookHealthMonitor:
    """按 tick 调用的看护器（不自己起线程）。"""

    def __init__(
        self,
        hook,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = 2.5,
        cooldown: float = 10.0,
        miss_limit: int = 2,
        max_failures: int = 3,
        on_heal: Optional[Callable[[bool], None]] = None,
        user_input_age: Optional[Callable[[], float]] = None,
        in_scope: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._hook = hook
        self._now = clock
        self._idle = idle_seconds
        self._cooldown = cooldown
        self._miss_limit = miss_limit
        self._max_failures = max_failures
        self._on_heal = on_heal
        self._user_input_age = user_input_age
        self._in_scope = in_scope
        self._last_probe = 0.0
        self._misses = 0            # 连续"静默但用户在动"的轮次
        self._failures = 0          # 连续重挂失败次数
        self._give_up = False
        self.repairs = 0            # 累计成功救回次数

    # ------------------------------------------------------------ 状态

    @property
    def gave_up(self) -> bool:
        return self._give_up

    @property
    def status_text(self) -> str:
        if self._give_up:
            return f"键盘钩子可能已失效，自动重挂连续失败 {self._failures} 次"
        if self.repairs:
            return f"键盘钩子正常 · 已自动重挂 {self.repairs} 次"
        return "键盘钩子正常"

    # ------------------------------------------------------------ 主流程

    def tick(self) -> HealthReport:
        """一次看护。异常一律吞掉 —— 看护绝不能拖垮主循环。"""
        try:
            return self._tick()
        except Exception:      # noqa: BLE001
            log.exception("键盘钩子看护异常")
            return HealthReport(reason="error")

    def _tick(self) -> HealthReport:
        if self._give_up:
            return HealthReport(reason="give_up")

        if not self._hook.installed:
            return self._repair("not_installed")

        # 最近还有回调 → 活着，顺手清掉可疑计数
        if self._hook.event_age() < self._idle:
            self._misses = 0
            return HealthReport(reason="active")

        now = self._now()
        if now - self._last_probe < self._cooldown:
            return HealthReport(reason="cooldown")

        if self._in_scope is not None and not self._in_scope():
            self._misses = 0
            return HealthReport(reason="out_of_scope")

        if self._user_input_age is not None:
            age = self._user_input_age()
            if age is None or age > self._idle:
                # 用户没在动（键盘鼠标都没动）→ 钩子安静是正常的
                self._misses = 0
                return HealthReport(reason="idle_user")

        self._misses += 1
        if self._misses < self._miss_limit:
            # 第一轮先不探：鼠标划一下就"静默"是常事，要连着两轮才算可疑
            return HealthReport(reason="suspect")

        self._last_probe = now
        self._misses = 0

        if self._hook.probe():
            self._failures = 0
            return HealthReport(probed=True, reason="alive")

        log.warning("探活没等到钩子回调（疑似被系统摘掉），尝试重挂")
        return self._repair("probe_failed")

    # ------------------------------------------------------------ 内部

    def _repair(self, reason: str) -> HealthReport:
        if not self._hook.reinstall():
            self._failures += 1
            log.error("键盘钩子重挂失败（第 %d 次）：%s", self._failures,
                      getattr(self._hook, "last_error", ""))
            if self._failures >= self._max_failures:
                self._give_up = True
                self._notify(False)
                return HealthReport(reinstalled=True, reason="give_up",
                                    detail="重挂连续失败，功能可能不可用")
            return HealthReport(reinstalled=True, reason="reinstall_failed")

        self._failures = 0
        self.repairs += 1
        # 复探一次：重挂本身可能"看起来成功"（句柄拿到了但依旧收不到消息）
        healed = bool(self._hook.probe())
        log.warning("键盘钩子已重挂（原因：%s），复探%s", reason,
                    "通过" if healed else "仍未通过")
        self._notify(healed)
        return HealthReport(probed=True, reinstalled=True, healed=healed,
                            reason="reinstalled" if healed else "reinstalled_unverified")

    def _notify(self, ok: bool) -> None:
        if self._on_heal is None:
            return
        try:
            self._on_heal(ok)
        except Exception:      # noqa: BLE001
            log.exception("钩子重挂回调异常")


__all__ = ["HookHealthMonitor", "HealthReport"]
