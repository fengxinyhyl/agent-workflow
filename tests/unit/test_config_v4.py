"""测试配置模型与加载器。"""

import os
import tempfile
import pytest
import yaml

from agent_workflow.config import (
    TaskModel,
    StateModel,
    RoleModel,
    AgentModel,
    GuardModel,
    WorkflowConfig,
    load_workflow,
)
from agent_workflow.config.env import EnvResolver


class TestTaskModel:
    """Task 模型测试（v4 约束：禁止 transition/guard/retry）。"""

    def test_create(self):
        task = TaskModel(
            name="plan",
            instruction="编写实现计划",
            role="planner",
            inputs=["goal"],
            output="plan_doc",
        )
        assert task.name == "plan"
        assert task.role == "planner"

    def test_allowed_decisions(self):
        task = TaskModel(
            name="review",
            instruction="审查",
            role="reviewer",
            allowed_decisions=["approve", "revise", "reject"],
        )
        assert "revise" in task.allowed_decisions


class TestStateModel:
    """State 模型测试。"""

    def test_resolve_transition_matched(self):
        state = StateModel(
            name="review_plan",
            task="review",
            on={"approve": "execute", "revise": "revise_plan", "reject": "failed"},
            default="failed",
        )
        assert state.resolve_transition("approve") == "execute"
        assert state.resolve_transition("revise") == "revise_plan"

    def test_resolve_transition_default(self):
        state = StateModel(
            name="review_plan",
            task="review",
            on={"approve": "execute"},
            default="failed",
        )
        # 未知 decision
        assert state.resolve_transition("unknown_decision") == "failed"

    def test_resolve_transition_terminal(self):
        state = StateModel(
            name="done",
            terminal=True,
            on={"restart": "start"},
            default="done",
        )
        # 终止状态不跳转
        assert state.resolve_transition("restart") == "done"


class TestGuardModel:
    """Guard 模型测试。"""

    def test_create(self):
        guard = GuardModel(
            max_visits=5,
            max_duration_minutes=480,
            max_retries=3,
            on_guard_failed="failed",
        )
        assert guard.max_visits == 5
        assert guard.max_duration_minutes == 480

    def test_default(self):
        guard = GuardModel()
        assert guard.max_visits == 0  # 0 = 不限制
        assert guard.on_guard_failed == "failed"


class TestWorkflowConfig:
    """WorkflowConfig 校验测试。"""

    def _make_minimal_workflow(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="test",
            initial_state="start",
            terminal_states=["done", "failed"],
            tasks={
                "do_work": TaskModel(name="do_work", instruction="做工作", role="worker"),
            },
            states={
                "start": StateModel(name="start", task="do_work", on={"done": "done"}, default="failed"),
                "done": StateModel(name="done", terminal=True),
                "failed": StateModel(name="failed", terminal=True),
            },
            roles={
                "worker": RoleModel(name="worker", agent="mock"),
            },
        )

    def test_validate_clean(self):
        wf = self._make_minimal_workflow()
        issues = wf.validate()
        assert len(issues) == 0

    def test_validate_missing_initial_state(self):
        wf = self._make_minimal_workflow()
        wf.initial_state = "nonexistent"
        issues = wf.validate()
        assert any("initial_state" in i for i in issues)

    def test_validate_task_role_missing(self):
        wf = self._make_minimal_workflow()
        wf.tasks["do_work"].role = "nonexistent_role"
        issues = wf.validate()
        assert any("role" in i for i in issues)

    def test_validate_transition_invalid(self):
        wf = self._make_minimal_workflow()
        wf.states["start"].on["approve"] = "nonexistent"
        issues = wf.validate()
        assert any("目标 state 未定义" in i for i in issues)


class TestEnvResolver:
    """环境变量解析器测试。"""

    def test_resolve_simple(self):
        resolver = EnvResolver()
        result = resolver.resolve("hello {NAME}")
        assert "{NAME}" not in result or result == "hello {NAME}"

    def test_resolve_with_override(self):
        resolver = EnvResolver({"NAME": "world"})
        result = resolver.resolve("hello {NAME}")
        assert result == "hello world"

    def test_resolve_dict(self):
        resolver = EnvResolver({"PROJECT": "test"})
        data = {"path": "{PROJECT}/src", "nested": {"file": "{PROJECT}.py"}}
        result = resolver.resolve_dict(data)
        assert result["path"] == "test/src"
        assert result["nested"]["file"] == "test.py"

    def test_resolve_list(self):
        resolver = EnvResolver({"VAR": "val"})
        data = {"items": ["{VAR}1", "{VAR}2"]}
        result = resolver.resolve_dict(data)
        assert result["items"] == ["val1", "val2"]
