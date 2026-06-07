"""MockAgent — Mock Agent（用于测试和 dry-run）。

Mock Agent 不调用外部 CLI，直接生成 mock TaskResult。
输出写入 staging，用于验证 Pipeline 正确性。
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import BaseAgent
from ..context.agent_input import AgentInput
from ..tasks.result import TaskResult, ExecutionMetadata, Issue, _now_iso


class MockAgent(BaseAgent):
    """Mock Agent 适配器。

    不调用任何外部 CLI，直接生成 mock 输出:
    - 将 AgentInput 中的 task instruction 写入 staging
    - 生成合法的 TaskResult JSON
    - 用于测试整个 Pipeline 的正确性

    用法:
        agent = MockAgent({"name": "mock_planner"})
        result = agent.execute(agent_input)
    """

    name = "mock"
    provider = "mock"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.name = config.get("name", "mock") if config else "mock"
        self._mock_decision = config.get("mock_decision", "done") if config else "done"

    def execute(self, agent_input: AgentInput) -> TaskResult:
        """执行 mock 任务。"""
        state_name = agent_input.state_name or agent_input.context.current_state or agent_input.task.name
        task_id = agent_input.task.name
        instruction = agent_input.task.instruction

        started_at = _now_iso()

        # 写入 staging 产物流
        artifacts = self._write_staging_artifacts(agent_input, state_name, instruction)

        finished_at = _now_iso()

        # 构建 TaskResult
        issues = []
        if agent_input.skill_policy:
            policy = agent_input.skill_policy
            if policy.get("forbidden_actions"):
                issues.append(Issue(
                    severity="info",
                    title="Skill 策略约束",
                    detail=f"禁止操作: {', '.join(policy['forbidden_actions'])}",
                ))

        return TaskResult(
            schema_version=1,
            task_id=task_id,
            state=state_name,
            agent=self.name,
            status="success",
            decision=self._resolve_decision(agent_input),
            summary=f"Mock 执行完成: {instruction[:100]}",
            artifacts=[{
                "name": a.get("name", "output"),
                "staging_path": a.get("staging_path", ""),
                "artifact_path": a.get("artifact_path", ""),
                "type": "markdown",
            } for a in artifacts],
            execution=ExecutionMetadata(
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0.1,
                attempt=1,
                exit_code=0,
            ),
            issues=issues,
            next_inputs={},
        )

    def _resolve_decision(self, agent_input: AgentInput) -> str:
        """解析 mock decision。

        如果 AgentInput 中有 skill_policy 限制 allowed_decisions，
        则从 allowed_decisions 中选择第一个非拒绝的 decision。
        """
        policy = agent_input.skill_policy or {}
        allowed = policy.get("allowed_decisions", [])

        if allowed and self._mock_decision not in allowed:
            # 选择第一个非 reject/fail 的 decision
            for d in allowed:
                if d not in ("reject", "fail"):
                    return d
            return allowed[0] if allowed else "done"

        return self._mock_decision

    def _write_staging_artifacts(
        self,
        agent_input: AgentInput,
        state_name: str,
        instruction: str,
    ) -> list[dict[str, Any]]:
        """写入 staging 产物流。"""
        artifacts = []

        staging_paths = agent_input.staging_paths
        for output_name, staging_path in staging_paths.items():
            if output_name == "task_result":
                # TaskResult 由 Runner 写入
                continue

            # 确保目录存在
            os.makedirs(os.path.dirname(staging_path), exist_ok=True)

            # 写入 mock 内容
            content = self._generate_mock_content(state_name, instruction, output_name)
            with open(staging_path, "w", encoding="utf-8") as f:
                f.write(content)

            # 计算 artifact_path（直接构造 artifacts/ 路径，不依赖字符串 replace）
            filename = os.path.basename(staging_path)
            artifact_path = os.path.join(agent_input.context.run_root, "artifacts", filename)

            artifacts.append({
                "name": output_name,
                "staging_path": staging_path,
                "artifact_path": artifact_path,
                "type": "markdown",
            })

        return artifacts

    def _generate_mock_content(
        self,
        state_name: str,
        instruction: str,
        output_name: str,
    ) -> str:
        """生成 mock 产物流内容。"""
        return (
            f"# Mock Output: {state_name}\n\n"
            f"## 任务\n\n{instruction}\n\n"
            f"## Mock 输出\n\n"
            f"这是 Mock Agent ({self.name}) 生成的模拟输出。\n\n"
            f"产出名称: {output_name}\n"
            f"状态: {state_name}\n"
        )

    def smoke_test(self) -> bool:
        """Mock Agent 冒烟测试。"""
        return True  # Mock Agent 永远可用
