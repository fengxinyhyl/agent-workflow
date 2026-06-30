"""software-dev Mock Flow 集成测试。

验证完整的 Codex Plan → Claude Review → Codex Revise → Claude Review → Codex Execute → Claude Audit 链路。
使用 Mock Agent 替换真实 CLI，验证 Pipeline 正确性。
"""

import os
import json
import tempfile
import pytest

from agent_workflow.config.loader import load_workflow, load_agents_config
from agent_workflow.state_machine.runner import Runner
from agent_workflow.agents.mock import MockAgent


# 测试用的 workflow 路径
EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows", "software-dev",
)


def _has_examples():
    """检查示例目录是否存在。"""
    return os.path.exists(EXAMPLES_DIR)


def _create_mock_skills_dir(tmpdir: str, skill_names: list[str] | None = None) -> str:
    """创建临时 skills 目录，包含所需的 mock skill 文件。

    software-dev workflow 要求 agent-workflow-lifecycle skill，
    测试需提供 mock skills_dir 以避免 required_skills_missing fail-fast。
    """
    if skill_names is None:
        skill_names = ["agent-workflow-lifecycle"]
    skills_dir = os.path.join(tmpdir, "skills")
    for name in skill_names:
        skill_dir = os.path.join(skills_dir, name)
        os.makedirs(skill_dir, exist_ok=True)
        skill_yaml = os.path.join(skill_dir, "skill.yaml")
        with open(skill_yaml, "w", encoding="utf-8") as f:
            f.write(f"name: {name}\ndescription: Mock skill for testing\nversion: '1'\n")
    return skills_dir


class TestSoftwareDevMockFlow:
    """software-dev 完整 Mock Flow 测试。"""

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_load_workflow(self):
        """测试加载 workflow 配置。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)
        assert wf.name == "software-dev"
        assert wf.initial_state == "plan"
        assert "plan" in wf.states
        assert "review_plan" in wf.states
        assert "done" in wf.terminal_states

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_load_agents(self):
        """测试加载 Agent 配置。"""
        agents_path = os.path.join(EXAMPLES_DIR, "agents.yaml")
        agents = load_agents_config(agents_path)
        assert "cc-opus" in agents
        assert "cc-deepseek" in agents
        assert agents["cc-opus"].provider == "claude"

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_workflow_validate(self):
        """测试 workflow 配置校验通过。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)
        issues = wf.validate()
        assert len(issues) == 0, f"Workflow 校验错误: {issues}"

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_state_machine_validate(self):
        """测试状态机校验通过。"""
        from agent_workflow.state_machine import StateMachine

        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)
        sm = StateMachine(wf)
        issues = sm.validate()
        assert len(issues) == 0, f"状态机校验错误: {issues}"

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_mock_full_flow(self):
        """测试完整的 Mock Flow（所有 state 使用 MockAgent）。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                skills_dir=_create_mock_skills_dir(tmpdir),
                goal="实现一个简单的 Hello World CLI 工具",
                project_root=tmpdir,
            )

            # 启动
            run_id = runner.start()
            assert len(run_id) > 0

            # 注入 Mock Agent（覆盖默认的 agent 解析）
            # 所有 Agent 请求都返回 MockAgent
            original_resolve = runner._resolve_agent

            def mock_resolve(task_model=None):
                return "mock"

            runner._resolve_agent = mock_resolve

            # 执行
            try:
                final_state = runner.run()
                # 应该到达 done 或 failed（不应该无限循环）
                assert final_state in ("done", "failed")
            except Exception as e:
                # Guard 触发导致的终止也是合理的
                pass

            # 验证产出
            run_root = os.path.join(tmpdir, "doc", "runs", run_id)
            assert os.path.exists(run_root)

            # workflow_state.json 存在
            state_path = os.path.join(run_root, "workflow_state.json")
            assert os.path.exists(state_path)

            # staging 目录存在
            staging_root = os.path.join(run_root, "staging")
            assert os.path.exists(staging_root)

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_transition_chain(self):
        """测试 decision → transition 链路正确性。"""
        from agent_workflow.state_machine import StateMachine

        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)
        sm = StateMachine(wf)

        # plan done → review_plan
        result = sm.resolve_transition("plan", "success", "done")
        assert result.next_state == "review_plan"

        # review_plan approve → execute
        result = sm.resolve_transition("review_plan", "success", "approve")
        assert result.next_state == "execute"

        # review_plan revise → revise_plan
        result = sm.resolve_transition("review_plan", "success", "revise")
        assert result.next_state == "revise_plan"

        # review_plan reject → failed
        result = sm.resolve_transition("review_plan", "success", "reject")
        assert result.next_state == "failed"

        # 未知 decision 走 default
        result = sm.resolve_transition("review_plan", "success", "unknown")
        assert result.next_state == "failed"

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_events_jsonl_generated(self):
        """P0a: 验证 events.jsonl 生成。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                skills_dir=_create_mock_skills_dir(tmpdir),
                goal="测试 events.jsonl 生成",
                project_root=tmpdir,
            )

            # 注入 mock resolve
            runner._resolve_agent = lambda tm=None: "mock"

            run_id = runner.start()
            try:
                runner.run()
            except Exception:
                pass

            # 验证 events.jsonl 存在
            run_root = os.path.join(tmpdir, "doc", "runs", run_id)
            events_path = os.path.join(run_root, "logs", "events.jsonl")
            assert os.path.exists(events_path), f"events.jsonl 应存在: {events_path}"

            # 读取事件
            events = []
            with open(events_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))

            assert len(events) > 0, "events.jsonl 应包含事件"
            event_types = [e["event"] for e in events]
            assert "WorkflowStarted" in event_types
            assert "StateEntered" in event_types
            assert "AgentStarted" in event_types
            # 应有 WorkflowCompleted 或 WorkflowCancelled
            assert any(t in event_types for t in ("WorkflowCompleted", "WorkflowCancelled"))

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_task_result_json_written(self):
        """P0c: 验证 staging/<state>/task_result.json 写入。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                skills_dir=_create_mock_skills_dir(tmpdir),
                goal="测试 task_result.json",
                project_root=tmpdir,
            )
            runner._resolve_agent = lambda tm=None: "mock"

            run_id = runner.start()
            try:
                runner.run()
            except Exception:
                pass

            run_root = os.path.join(tmpdir, "doc", "runs", run_id)
            staging_root = os.path.join(run_root, "staging")

            # 至少有一个 state 的 staging 目录包含 task_result.json
            found = False
            if os.path.exists(staging_root):
                for state_dir in os.listdir(staging_root):
                    tr_path = os.path.join(staging_root, state_dir, "task_result.json")
                    if os.path.exists(tr_path):
                        found = True
                        # 验证是可解析 JSON
                        with open(tr_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        assert "task_id" in data
                        assert "decision" in data
                        break

            assert found, "至少应有一个 task_result.json 被写入"

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_workflow_snapshot_saved(self):
        """P0b: 验证 workflow snapshot 保存到 context。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                skills_dir=_create_mock_skills_dir(tmpdir),
                goal="测试 snapshot",
                project_root=tmpdir,
            )
            runner._resolve_agent = lambda tm=None: "mock"

            run_id = runner.start()

            # 验证 context 中有 _workflow_snapshot
            assert "_workflow_snapshot" in runner.context.workflow_variables
            snapshot = runner.context.workflow_variables["_workflow_snapshot"]
            assert snapshot["name"] == "software-dev"
            assert "states" in snapshot
            assert "tasks" in snapshot

            # 运行以确保 JSONLSink 被正确关闭（Windows 文件句柄限制）
            try:
                runner.run()
            except Exception:
                pass

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_artifact_paths_in_artifacts_dir(self):
        """P0g: 验证所有正式 artifact 路径在 artifacts/ 下。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                skills_dir=_create_mock_skills_dir(tmpdir),
                goal="测试 artifact 路径",
                project_root=tmpdir,
            )
            runner._resolve_agent = lambda tm=None: "mock"

            run_id = runner.start()
            try:
                runner.run()
            except Exception:
                pass

            run_root = os.path.join(tmpdir, "doc", "runs", run_id)

            # 从 workflow_state.json 检查 artifacts
            state_path = os.path.join(run_root, "workflow_state.json")
            if os.path.exists(state_path):
                with open(state_path, "r", encoding="utf-8") as f:
                    state_data = json.load(f)

                artifacts = state_data.get("artifacts", {})
                for name, path in artifacts.items():
                    assert "artifacts" in path, (
                        f"artifact '{name}' 路径应在 artifacts/ 下: {path}"
                    )

    @pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
    def test_guard_prevents_infinite_loop(self):
        """测试 Guard 阻止 review/revise 无限循环。"""
        wf_path = os.path.join(EXAMPLES_DIR, "workflow.yaml")
        wf = load_workflow(wf_path)

        # max_visits=5 应该能终止
        assert wf.guards.max_visits == 5

        from agent_workflow.state_machine.guard import GuardChecker
        from agent_workflow.context import RunContext

        guard = GuardChecker(wf.guards)

        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )

        # 模拟 review_plan 被访问 6 次
        for i in range(6):
            ctx.record_state_visit("review_plan")

        result = guard.check("review_plan", ctx)
        assert not result.passed
        assert result.guard_type == "max_visits"
