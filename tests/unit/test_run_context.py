"""测试 RunContext 和 AgentInput。"""

import json
import os
import pytest
import tempfile

from agent_workflow.context import RunContext, AgentInput, TaskConfig


class TestRunContext:
    """RunContext 单元测试。"""

    def test_create(self):
        ctx = RunContext.create(
            workflow_id="test",
            goal="测试目标",
            project_root="/tmp",
            run_id="run_001",
            run_root="/tmp/.agent-workflow/runs/run_001",
        )
        assert ctx.run_id == "run_001"
        assert ctx.workflow_id == "test"
        assert ctx.goal == "测试目标"
        assert ctx.started_at != ""

    def test_record_state_visit(self):
        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        ctx.record_state_visit("codex_plan")
        assert ctx.current_state == "codex_plan"
        assert "codex_plan" in ctx.state_history
        assert ctx.get_attempt("codex_plan") == 1

        ctx.record_state_visit("codex_plan")
        assert ctx.get_attempt("codex_plan") == 2

    def test_record_task_result(self):
        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        result = {"task_id": "plan", "decision": "done", "status": "success"}
        ctx.record_task_result("codex_plan", result)
        assert "codex_plan" in ctx.task_results
        assert ctx.task_results["codex_plan"]["decision"] == "done"

    def test_serialization(self):
        ctx = RunContext.create(
            workflow_id="test", goal="测试", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        ctx.record_state_visit("codex_plan")
        ctx.promote_artifact("plan_doc", "artifacts/plan.md")

        # 序列化
        data = ctx.to_dict()
        assert data["run_id"] == "run_001"

        # JSON 序列化
        json_str = ctx.to_json()
        assert "run_001" in json_str

        # 反序列化
        ctx2 = RunContext.from_json(json_str)
        assert ctx2.run_id == ctx.run_id
        assert ctx2.state_history == ctx.state_history
        assert ctx2.artifacts == ctx.artifacts

    def test_save_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            ctx = RunContext.create(
                workflow_id="test", goal="测试", project_root=tmpdir,
                run_id="run_001", run_root=run_root,
            )
            ctx.record_state_visit("codex_plan")
            ctx.save()

            ctx2 = RunContext.load(run_root)
            assert ctx2.run_id == ctx.run_id
            assert ctx2.state_history == ctx.state_history

    def test_save_is_atomic_no_temp_leftover(self):
        # 原子写成功后目录内不应残留 .tmp 临时文件
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_atomic")
            ctx = RunContext.create(
                workflow_id="test", goal="测试", project_root=tmpdir,
                run_id="run_atomic", run_root=run_root,
            )
            ctx.save()
            ctx.save()  # 覆盖写一次，验证 os.replace 可重复
            leftovers = [
                f for f in os.listdir(run_root)
                if f.startswith(".workflow_state.") and f.endswith(".tmp")
            ]
            assert leftovers == []
            assert RunContext.load(run_root).run_id == "run_atomic"

    def test_load_corrupted_raises_value_error(self):
        # 损坏的 JSON 应抛带路径上下文的 ValueError，而非裸 JSONDecodeError
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_bad")
            os.makedirs(run_root, exist_ok=True)
            with open(
                os.path.join(run_root, "workflow_state.json"),
                "w", encoding="utf-8",
            ) as f:
                f.write("{not valid json")
            with pytest.raises(ValueError, match="损坏"):
                RunContext.load(run_root)

    def test_workflow_variables(self):
        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        ctx.set_variable("retry_count", 3)
        assert ctx.get_variable("retry_count") == 3
        assert ctx.get_variable("nonexistent", "default") == "default"


class TestAgentInput:
    """AgentInput 单元测试。"""

    def test_build_prompt(self):
        ctx = RunContext.create(
            workflow_id="test", goal="实现登录功能", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        ctx.promote_artifact("plan_doc", "artifacts/plan.md")

        task = TaskConfig(
            name="review_plan",
            instruction="审查实现计划",
            agent="reviewer",
            inputs=["plan_doc"],
            output="review_doc",
        )

        agent_input = AgentInput(
            task=task,
            context=ctx,
            staging_paths={
                "review_doc": "/tmp/runs/run_001/staging/review_plan/review_doc.md",
                "task_result": "/tmp/runs/run_001/staging/review_plan/task_result.json",
            },
        )

        prompt = agent_input.build_prompt()
        assert "实现登录功能" in prompt
        assert "审查实现计划" in prompt
        assert "plan_doc" in prompt
        assert "staging" in prompt.lower()

    def test_build_prompt_with_skill(self):
        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        task = TaskConfig(
            name="plan",
            instruction="编写计划",
            agent="planner",
        )
        agent_input = AgentInput(
            task=task,
            context=ctx,
            skill_context="### test-skill\n测试技能指引",
            skill_policy={"allowed_decisions": ["done", "fail"]},
        )
        prompt = agent_input.build_prompt()
        assert "测试技能指引" in prompt
        assert "done" in prompt

    def test_build_prompt_no_allowed_decisions_no_done_example(self):
        """无 allowed_decisions 时示例不应诱导输出 decision: "done"，且说明 decision 可省略。"""
        from agent_workflow.tasks.result_schema import build_task_result_schema

        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        task = TaskConfig(name="plan", instruction="编写计划", agent="planner")
        agent_input = AgentInput(
            task=task,
            context=ctx,
            expected_task_result_schema=build_task_result_schema(None),
        )
        prompt = agent_input.build_prompt()
        # 示例中不得出现硬编码的 decision: "done"
        assert '"decision": "done"' not in prompt
        # 必需字段说明应标注 decision 可省略
        assert "可选" in prompt

    def test_build_prompt_allowed_decisions_example_uses_first(self):
        """有 allowed_decisions 时示例 decision 取第一个允许值。"""
        from agent_workflow.tasks.result_schema import build_task_result_schema

        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        task = TaskConfig(name="review", instruction="审查", agent="reviewer")
        agent_input = AgentInput(
            task=task,
            context=ctx,
            skill_policy={"allowed_decisions": ["approve", "revise", "reject"]},
            expected_task_result_schema=build_task_result_schema(
                ["approve", "revise", "reject"]
            ),
        )
        prompt = agent_input.build_prompt()
        assert '"decision": "approve"' in prompt
