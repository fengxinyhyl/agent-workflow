"""TaskResult — 每个 Agent task 必须输出的标准化结果。

TaskResult 是 Runner 做状态迁移的唯一语义输入。
Agent 不允许输出下一 state 名称；即使输出了，Runner 也必须忽略。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Literal


# 状态枚举
TaskStatus = Literal[
    "success",      # 成功完成
    "failed",       # 执行失败
    "blocked",      # 被阻塞（依赖不满足）
    "cancelled",    # 被取消
    "timeout",      # 超时
    "invalid_output",  # 输出格式无效
]

# 决策枚举
TaskDecision = Literal[
    "approve",   # 批准，继续
    "revise",    # 需要修改
    "reject",    # 拒绝
    "done",      # 完成
    "fail",      # 失败
    "blocked",   # 阻塞
    "no_op",     # 无操作
]

# P0 允许的 status 值
VALID_STATUSES = {"success", "failed", "blocked", "cancelled", "timeout", "invalid_output"}

# P0 允许的 decision 值
VALID_DECISIONS = {"approve", "revise", "reject", "done", "fail", "blocked", "no_op"}


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


@dataclass
class ArtifactRef:
    """产物流引用。"""

    name: str = ""
    staging_path: str = ""
    artifact_path: str = ""
    type: str = "markdown"  # markdown / json / yaml / code / other

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionMetadata:
    """执行元数据（必填）。"""

    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    attempt: int = 1
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Issue:
    """发现的问题。"""

    severity: str = "info"  # blocking / warning / info
    title: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResult:
    """Agent 任务执行结果。

    所有 Agent 必须输出此结构的 JSON。

    字段说明：
      schema_version: TaskResult schema 版本（当前为 1）
      task_id: 任务标识（如 review_plan）
      state: 执行此 task 时的 state 名称
      agent: 执行 Agent 名称
      status: 执行状态（success/failed/blocked/cancelled/timeout/invalid_output）
      decision: 语义决策（approve/revise/reject/done/fail/blocked/no_op）
      summary: 人类可读的摘要
      artifacts: 产物列表（staging 路径）
      execution: 执行元数据（必填）
      issues: 发现的问题列表
      next_inputs: 传递给下一状态的输入（可选）
    """

    schema_version: int = 1
    task_id: str = ""
    state: str = ""
    agent: str = ""
    status: TaskStatus = "success"
    decision: TaskDecision = "done"
    summary: str = ""
    artifacts: list[ArtifactRef] | list[dict[str, Any]] = field(default_factory=list)
    execution: ExecutionMetadata | dict[str, Any] = field(default_factory=ExecutionMetadata)
    issues: list[Issue] | list[dict[str, Any]] = field(default_factory=list)
    next_inputs: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """校验 TaskResult 的合法性，返回问题列表。"""
        issues = []

        if self.schema_version < 1:
            issues.append("schema_version 必须 >= 1")

        if not self.task_id:
            issues.append("task_id 不能为空")

        if self.status not in VALID_STATUSES:
            issues.append(f"无效 status: '{self.status}'，允许值: {VALID_STATUSES}")

        if self.decision not in VALID_DECISIONS:
            issues.append(f"无效 decision: '{self.decision}'，允许值: {VALID_DECISIONS}")

        # 校验 execution metadata
        exec_data = self.execution
        if isinstance(exec_data, dict):
            if not exec_data.get("started_at"):
                issues.append("execution.started_at 必填")
            if not exec_data.get("finished_at"):
                issues.append("execution.finished_at 必填")

        return issues

    def is_valid(self) -> bool:
        """是否通过校验。"""
        return len(self.validate()) == 0

    def get_decision(self) -> str:
        """获取决策字符串（规范化）。"""
        return str(self.decision).lower().strip()

    def get_artifacts(self) -> list[ArtifactRef]:
        """获取产物列表（标准化为 ArtifactRef 列表）。"""
        result = []
        for a in self.artifacts:
            if isinstance(a, ArtifactRef):
                result.append(a)
            elif isinstance(a, dict):
                result.append(ArtifactRef(**a))
        return result

    def get_execution(self) -> ExecutionMetadata:
        """获取执行元数据（标准化）。"""
        if isinstance(self.execution, ExecutionMetadata):
            return self.execution
        return ExecutionMetadata(**self.execution)

    def get_issues(self) -> list[Issue]:
        """获取问题列表（标准化）。"""
        result = []
        for i in self.issues:
            if isinstance(i, Issue):
                result.append(i)
            elif isinstance(i, dict):
                result.append(Issue(**i))
        return result

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "state": self.state,
            "agent": self.agent,
            "status": self.status,
            "decision": self.decision,
            "summary": self.summary,
            "artifacts": [
                a.to_dict() if isinstance(a, ArtifactRef) else a
                for a in self.artifacts
            ],
            "execution": (
                self.execution.to_dict()
                if isinstance(self.execution, ExecutionMetadata)
                else self.execution
            ),
            "issues": [
                i.to_dict() if isinstance(i, Issue) else i
                for i in self.issues
            ],
            "next_inputs": self.next_inputs,
        }

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskResult":
        """从字典反序列化。"""
        # 处理 artifacts
        artifacts = []
        for a in data.get("artifacts", []):
            if isinstance(a, dict):
                artifacts.append(ArtifactRef(**a))
            else:
                artifacts.append(a)

        # 处理 execution
        exec_data = data.get("execution", {})
        if isinstance(exec_data, dict):
            execution = ExecutionMetadata(**exec_data)
        else:
            execution = exec_data

        # 处理 issues
        issues = []
        for i in data.get("issues", []):
            if isinstance(i, dict):
                issues.append(Issue(**i))
            else:
                issues.append(i)

        return cls(
            schema_version=data.get("schema_version", 1),
            task_id=data.get("task_id", ""),
            state=data.get("state", ""),
            agent=data.get("agent", ""),
            status=data.get("status", "success"),
            decision=data.get("decision", "done"),
            summary=data.get("summary", ""),
            artifacts=artifacts,
            execution=execution,
            issues=issues,
            next_inputs=data.get("next_inputs", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "TaskResult":
        """从 JSON 字符串反序列化。"""
        return cls.from_dict(json.loads(json_str))
