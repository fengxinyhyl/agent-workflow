"""Transition — 状态迁移逻辑。

所有迁移由 Runner 根据 TaskResult.decision 决定。
Agent 不允许输出下一 state 名称；即使输出了也会被忽略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransitionResult:
    """Transition 解析结果。"""

    current_state: str = ""
    decision: str = ""
    next_state: str = ""
    matched: bool = True  # decision/status 是否匹配到 on/on_status
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # Runtime v2: 两段式路由新增字段
    status: str = ""       # 触发本次路由的 status（success/failed/blocked）
    route_by: str = ""     # "status" | "decision" | "next" — 路由驱动因素

    def is_terminal(self, terminal_states: set[str]) -> bool:
        """判断下一状态是否为终止状态。"""
        return self.next_state in terminal_states

    def to_event_dict(self) -> dict[str, Any]:
        """转为 observability event 的 payload。"""
        return {
            "current_state": self.current_state,
            "decision": self.decision,
            "next_state": self.next_state,
            "matched": self.matched,
            "reason": self.reason,
            "status": self.status,
            "route_by": self.route_by,
        }


def resolve_transition(
    state_on: dict[str, str],
    decision: str,
    default: str = "failed",
    state_name: str = "",
) -> TransitionResult:
    """纯函数：根据 state.on 和 decision 解析下一状态。

    规则：
    1. decision 在 state.on 中 → 直接匹配
    2. 否则 → 走 default
    3. 未知 decision 记录 unmatched=True
    """
    if decision in state_on:
        return TransitionResult(
            current_state=state_name,
            decision=decision,
            next_state=state_on[decision],
            matched=True,
            reason=f"匹配 on['{decision}']",
        )

    return TransitionResult(
        current_state=state_name,
        decision=decision,
        next_state=default,
        matched=False,
        reason=f"决策 '{decision}' 未匹配 on 表，走 default → '{default}'",
    )
