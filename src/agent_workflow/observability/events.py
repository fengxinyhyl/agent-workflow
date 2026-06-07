"""事件类型定义。

P0 事件列表（v4 计划 §10.1）:
  WorkflowStarted, StateEntered, SkillAdoptionWritten,
  AgentStarted, AgentOutput, TaskResultWritten,
  ValidatorStarted, ValidatorFinished, ArtifactPromoted,
  TransitionSelected, GuardFailed,
  Heartbeat, WorkflowCompleted, WorkflowFailed, WorkflowCancelled
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class EventType(str, Enum):
    """P0 标准事件类型。"""

    # 工作流生命周期
    WorkflowStarted = "WorkflowStarted"
    WorkflowCompleted = "WorkflowCompleted"
    WorkflowFailed = "WorkflowFailed"
    WorkflowCancelled = "WorkflowCancelled"

    # 状态与任务
    StateEntered = "StateEntered"
    TaskFinished = "TaskFinished"

    # Agent
    AgentStarted = "AgentStarted"
    AgentOutput = "AgentOutput"
    TaskResultWritten = "TaskResultWritten"

    # Skill
    SkillAdoptionWritten = "SkillAdoptionWritten"

    # 校验
    ValidatorStarted = "ValidatorStarted"
    ValidatorFinished = "ValidatorFinished"

    # 产物流
    ArtifactPromoted = "ArtifactPromoted"

    # 迁移与 Guard
    TransitionSelected = "TransitionSelected"
    GuardFailed = "GuardFailed"

    # 心跳
    Heartbeat = "Heartbeat"


# 所有 P0 事件类型
ALL_EVENTS: list[EventType] = list(EventType)

# 事件注册表：{event_type: required_fields}
event_registry: dict[str, list[str]] = {
    EventType.WorkflowStarted: ["run_id", "workflow_id", "timestamp"],
    EventType.WorkflowCompleted: ["run_id", "final_state", "timestamp"],
    EventType.WorkflowFailed: ["run_id", "error", "timestamp"],
    EventType.WorkflowCancelled: ["run_id", "reason", "timestamp"],
    EventType.StateEntered: ["state", "timestamp"],
    EventType.TaskFinished: ["state", "decision", "timestamp"],
    EventType.AgentStarted: ["state", "task", "agent", "timestamp"],
    EventType.AgentOutput: ["agent", "content", "timestamp"],
    EventType.TaskResultWritten: ["state", "task_id", "timestamp"],
    EventType.SkillAdoptionWritten: ["state", "skills", "timestamp"],
    EventType.ValidatorStarted: ["state", "validator", "timestamp"],
    EventType.ValidatorFinished: ["state", "passed", "timestamp"],
    EventType.ArtifactPromoted: ["name", "artifact_path", "timestamp"],
    EventType.TransitionSelected: ["current_state", "decision", "next_state", "timestamp"],
    EventType.GuardFailed: ["state", "guard_type", "reason", "timestamp"],
    EventType.Heartbeat: ["run_id", "state", "elapsed_seconds", "timestamp"],
}


def validate_event(event_type: str, payload: dict[str, Any]) -> list[str]:
    """校验事件 payload，返回缺失字段列表。"""
    required = event_registry.get(event_type, [])
    missing = [f for f in required if f not in payload]
    return missing


def build_event(
    event_type: str,
    run_id: str,
    state: str = "",
    task: str = "",
    payload: dict[str, Any] | None = None,
    timestamp: str = "",
) -> dict[str, Any]:
    """构建标准事件字典。"""
    from datetime import datetime, timezone, timedelta

    if not timestamp:
        tz = timezone(timedelta(hours=8))
        timestamp = datetime.now(tz).isoformat()

    event = {
        "event": event_type,
        "run_id": run_id,
        "state": state,
        "task": task,
        "timestamp": timestamp,
        "payload": payload or {},
    }
    return event
