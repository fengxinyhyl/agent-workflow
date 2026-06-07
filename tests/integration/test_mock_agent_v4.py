"""测试 Mock Agent 适配器和完整的 AgentInput → TaskResult 流程。"""

import os
import json
import tempfile
import pytest

from agent_workflow.context import RunContext, AgentInput, TaskConfig
from agent_workflow.agents import MockAgent, AgentRegistry
from agent_workflow.tasks.result import TaskResult


class TestMockAgent:
    """Mock Agent 集成测试。"""

    def _make_context(self, tmpdir: str) -> RunContext:
        run_root = os.path.join(tmpdir, "runs", "run_001")
        return RunContext.create(
            workflow_id="test",
            goal="测试目标",
            project_root=tmpdir,
            run_id="run_001",
            run_root=run_root,
        )

    def test_execute_basic(self):
        """Mock Agent 基本执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = self._make_context(tmpdir)
            ctx.record_state_visit("codex_plan")

            # 确保 staging 目录存在
            staging_dir = os.path.join(ctx.run_root, "staging", "codex_plan")
            os.makedirs(staging_dir, exist_ok=True)

            agent_input = AgentInput(
                task=TaskConfig(
                    name="codex_plan",
                    instruction="编写实现计划",
                    role="planner",
                    output="plan_doc",
                ),
                context=ctx,
                staging_paths={
                    "plan_doc": os.path.join(staging_dir, "plan_doc.md"),
                    "task_result": os.path.join(staging_dir, "task_result.json"),
                },
            )

            agent = MockAgent({"name": "mock_planner"})
            result = agent.execute(agent_input)

            assert isinstance(result, TaskResult)
            assert result.task_id == "codex_plan"
            assert result.status == "success"
            assert result.decision == "done"
            assert result.schema_version == 1

            # 验证 staging 文件已写入
            plan_path = os.path.join(staging_dir, "plan_doc.md")
            assert os.path.exists(plan_path)

    def test_execute_with_policy(self):
        """Mock Agent 在策略约束下的执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = self._make_context(tmpdir)
            ctx.record_state_visit("claude_review_plan")

            staging_dir = os.path.join(ctx.run_root, "staging", "claude_review_plan")
            os.makedirs(staging_dir, exist_ok=True)

            agent_input = AgentInput(
                task=TaskConfig(
                    name="claude_review_plan",
                    instruction="审查计划",
                    role="reviewer",
                    output="review_doc",
                ),
                context=ctx,
                skill_policy={"allowed_decisions": ["approve", "revise", "reject"]},
                staging_paths={
                    "review_doc": os.path.join(staging_dir, "review_doc.md"),
                    "task_result": os.path.join(staging_dir, "task_result.json"),
                },
            )

            agent = MockAgent({"name": "mock_reviewer", "mock_decision": "revise"})
            result = agent.execute(agent_input)

            # Mock Agent 应从 allowed_decisions 中选择 revision
            assert result.decision in ("approve", "revise", "reject")

    def test_smoke_test(self):
        """Mock Agent 冒烟测试。"""
        agent = MockAgent({"name": "mock"})
        assert agent.smoke_test() is True


class TestAgentRegistry:
    """Agent Registry 集成测试。"""

    def test_resolve_mock(self):
        registry = AgentRegistry({})
        agent = registry.resolve("any_unknown_agent")
        assert isinstance(agent, MockAgent)

    def test_list_agents(self):
        from agent_workflow.config.models import AgentModel
        config = {
            "codex_plan": AgentModel(name="codex_plan", provider="codex"),
            "claude_review": AgentModel(name="claude_review", provider="claude"),
        }
        registry = AgentRegistry(config)
        agents = registry.list_agents()
        assert "codex_plan" in agents
        assert "claude_review" in agents
