"""Guard — 保护机制。

P0 Guard 支持三类：
- max_visits: 限制某 state 被进入的次数
- max_duration_minutes: 限制 workflow 的最长运行时间
- max_retries: 限制同一 state/task 的重试次数

Guard 失败后默认进入 failed 状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from ..config.models import GuardModel
from ..context.run_context import RunContext


def _now() -> datetime:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz)


@dataclass
class GuardResult:
    """Guard 检查结果。"""

    passed: bool = True
    reason: str = ""
    guard_type: str = ""
    current_value: int | float = 0
    threshold: int | float = 0
    next_state_if_failed: str = "failed"


class GuardChecker:
    """Guard 检查器。

    在每个 state 进入前检查以下条件：
    1. max_visits: state 访问次数是否超限
    2. max_duration_minutes: 总运行时间是否超限
    3. max_retries: 同一 state 重试次数是否超限
    """

    def __init__(self, guard_config: GuardModel):
        self.config = guard_config
        self._start_time: datetime | None = None

    def set_start_time(self, start_time: datetime):
        """设置工作流启动时间（用于 max_duration 检查）。"""
        self._start_time = start_time

    def check(
        self,
        state_name: str,
        context: RunContext,
    ) -> GuardResult:
        """检查所有 Guard 条件。

        返回第一个失败的 Guard，全部通过则返回 passed=True。
        """
        # 1. max_visits
        if self.config.max_visits > 0:
            visits = context.get_attempt(state_name) + 1  # +1 因为已经进入了一次
            if visits > self.config.max_visits:
                return GuardResult(
                    passed=False,
                    reason=f"state '{state_name}' 访问次数 {visits} > max_visits {self.config.max_visits}",
                    guard_type="max_visits",
                    current_value=visits,
                    threshold=self.config.max_visits,
                    next_state_if_failed=self.config.on_guard_failed,
                )

        # 2. max_duration_minutes
        if self.config.max_duration_minutes > 0 and self._start_time is not None:
            elapsed = (_now() - self._start_time).total_seconds() / 60.0
            if elapsed > self.config.max_duration_minutes:
                return GuardResult(
                    passed=False,
                    reason=f"运行时长 {elapsed:.1f}min > max_duration_minutes {self.config.max_duration_minutes}min",
                    guard_type="max_duration_minutes",
                    current_value=elapsed,
                    threshold=self.config.max_duration_minutes,
                    next_state_if_failed=self.config.on_guard_failed,
                )

        # 3. max_retries
        if self.config.max_retries > 0:
            attempts = context.get_attempt(state_name)
            if attempts > self.config.max_retries:
                return GuardResult(
                    passed=False,
                    reason=f"state '{state_name}' 重试次数 {attempts} > max_retries {self.config.max_retries}",
                    guard_type="max_retries",
                    current_value=attempts,
                    threshold=self.config.max_retries,
                    next_state_if_failed=self.config.on_guard_failed,
                )

        return GuardResult(passed=True)

    def check_all(self, context: RunContext) -> list[GuardResult]:
        """检查所有活跃 Guard 条件（返回所有结果，用于 explain）。"""
        results = []

        # max_visits - 对所有访问过的 state
        if self.config.max_visits > 0:
            for state_name, visits in context.attempts.items():
                if visits >= self.config.max_visits:
                    results.append(GuardResult(
                        passed=False,
                        reason=f"state '{state_name}' 已达 max_visits {self.config.max_visits}",
                        guard_type="max_visits",
                        current_value=visits,
                        threshold=self.config.max_visits,
                    ))

        # max_duration
        if self.config.max_duration_minutes > 0 and self._start_time is not None:
            elapsed = (_now() - self._start_time).total_seconds() / 60.0
            remaining = self.config.max_duration_minutes - elapsed
            results.append(GuardResult(
                passed=remaining > 0,
                reason=f"已运行 {elapsed:.1f}min / {self.config.max_duration_minutes}min（剩余 {max(0, remaining):.1f}min）",
                guard_type="max_duration_minutes",
                current_value=elapsed,
                threshold=self.config.max_duration_minutes,
            ))

        # max_retries
        if self.config.max_retries > 0:
            for state_name, attempts in context.attempts.items():
                remaining = self.config.max_retries - attempts
                results.append(GuardResult(
                    passed=remaining > 0,
                    reason=f"state '{state_name}' 重试 {attempts}/{self.config.max_retries}",
                    guard_type="max_retries",
                    current_value=attempts,
                    threshold=self.config.max_retries,
                ))

        return results
