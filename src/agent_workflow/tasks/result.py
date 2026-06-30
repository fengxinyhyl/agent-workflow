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

# 允许的 status 值（Runtime 层语义轴）。
# 可路由子集仅 {success, failed, blocked}；invalid_output 为 Runtime 内部瞬时态
# （Parser 无法解析结构化输出时产出，交由后续 Repair 闸口消解，不直接参与路由）；
# cancelled/timeout 由取消/超时路径单独处理。
VALID_STATUSES = {"success", "failed", "blocked", "cancelled", "timeout", "invalid_output"}

# 注意：decision 合法性不再由 Runtime 全局白名单校验，而是由各 task 的
# allowed_decisions 决定（Workflow 层语义轴）。Runtime 不认识业务词。


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
    pid: int | None = None  # 子进程 PID（对齐 legacy WorkerResult.pid）

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
      decision: 语义决策（Workflow 层；仅分支节点需要，合法值由 task 的 allowed_decisions 决定，可为 None）
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
    decision: str | None = None
    summary: str = ""
    artifacts: list[ArtifactRef] | list[dict[str, Any]] = field(default_factory=list)
    execution: ExecutionMetadata | dict[str, Any] = field(default_factory=ExecutionMetadata)
    issues: list[Issue] | list[dict[str, Any]] = field(default_factory=list)
    next_inputs: dict[str, Any] = field(default_factory=dict)
    # 新增：worker 运行时元数据（对齐 legacy WorkerResult）
    session_id: str = ""              # CLI session/thread ID（G2；Phase C/D 填充）
    token_usage: dict[str, int] = field(default_factory=dict)  # input/output/cache tokens（G1；Phase C/D 填充）
    log_path: str = ""                # stream 日志落盘路径（G3；Phase C/D 填充）
    packet_path: str = ""             # debug packet 路径（G4；Phase C/D 填充）

    def validate(self) -> list[str]:
        """校验 TaskResult 的合法性，返回问题列表。"""
        issues = []

        if self.schema_version < 1:
            issues.append("schema_version 必须 >= 1")

        if not self.task_id:
            issues.append("task_id 不能为空")

        if self.status not in VALID_STATUSES:
            issues.append(f"无效 status: '{self.status}'，允许值: {VALID_STATUSES}")

        # decision 合法性不再由 Runtime 校验（交由 task 的 allowed_decisions 决定），
        # 故 decision 为空或任意值都不在此报错。

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

    def get_decision(self) -> str | None:
        """获取决策字符串（规范化）。decision 为 None 时返回 None，不再兜底为字符串。"""
        if self.decision is None:
            return None
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
            "session_id": self.session_id,
            "token_usage": self.token_usage,
            "log_path": self.log_path,
            "packet_path": self.packet_path,
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
            decision=data.get("decision", None),
            summary=data.get("summary", ""),
            artifacts=artifacts,
            execution=execution,
            issues=issues,
            next_inputs=data.get("next_inputs", {}),
            session_id=data.get("session_id", ""),
            token_usage=data.get("token_usage", {}),
            log_path=data.get("log_path", ""),
            packet_path=data.get("packet_path", ""),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "TaskResult":
        """从 JSON 字符串反序列化。"""
        return cls.from_dict(json.loads(json_str))
