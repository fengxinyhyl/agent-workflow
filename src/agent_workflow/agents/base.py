"""BaseAgent — Agent 适配器基类。

所有 Agent 适配器继承此类，实现 execute() 方法。
Agent 适配器只接收 AgentInput，不接收散落参数。
"""

from __future__ import annotations

from typing import Any

from ..context.agent_input import AgentInput
from ..tasks.result import TaskResult


class BaseAgent:
    """Agent 适配器基类。

    子类需实现:
    - execute(agent_input) → TaskResult
    - smoke_test() → bool

    可重写:
    - build_command(agent_input) → str: 构建 CLI 命令
    """

    name: str = "base"
    provider: str = "base"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def execute(self, agent_input: AgentInput) -> TaskResult:
        """执行 Agent 任务。

        参数:
          agent_input: 统一的 AgentInput（Task + RunContext + Skill）

        返回:
          TaskResult（包含 decision、artifacts、execution metadata）
        """
        raise NotImplementedError

    def smoke_test(self) -> bool:
        """冒烟测试：验证 Agent 是否可用。"""
        try:
            # 创建一个最小化的 AgentInput 进行测试
            from ..context.agent_input import AgentInput, TaskConfig
            test_input = AgentInput(
                task=TaskConfig(
                    name="smoke_test",
                    instruction="回复 'ok' 并确认 Agent 可用。",
                    role="test",
                ),
            )
            result = self.execute(test_input)
            return result is not None and result.status in ("success", "done")
        except Exception:
            return False

    def build_command(self, agent_input: AgentInput) -> str:
        """构建 CLI 命令（供子类重写）。"""
        return ""

    def _create_task_result(
        self,
        task_id: str,
        state: str,
        status: str = "success",
        decision: str = "done",
        summary: str = "",
    ) -> TaskResult:
        """创建 TaskResult 的工具方法。"""
        from ..tasks.result import TaskResult, ExecutionMetadata, Issue, _now_iso

        return TaskResult(
            schema_version=1,
            task_id=task_id,
            state=state,
            agent=self.name,
            status=status,
            decision=decision,
            summary=summary,
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=0,
                attempt=1,
                exit_code=0 if status == "success" else 1,
            ),
            issues=[],
        )
