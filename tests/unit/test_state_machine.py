"""测试状态机核心：StateMachine、Transition、Guard。"""

import pytest

from agent_workflow.config.models import (
    WorkflowConfig, TaskModel, StateModel, RoleModel, GuardModel,
)
from agent_workflow.state_machine import StateMachine, GuardChecker, resolve_transition
from agent_workflow.context import RunContext


class TestStateMachine:
    """StateMachine 单元测试。"""

    def _make_test_workflow(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="test",
            initial_state="start",
            terminal_states=["done", "failed"],
            tasks={
                "work": TaskModel(name="work", instruction="做工作", role="worker"),
                "review": TaskModel(name="review", instruction="审查", role="reviewer"),
            },
            states={
                "start": StateModel(
                    name="start", task="work",
                    on={"done": "review", "fail": "failed"},
                    default="failed",
                ),
                "review": StateModel(
                    name="review", task="review",
                    on={"approve": "done", "revise": "start", "reject": "failed"},
                    default="failed",
                ),
                "done": StateModel(name="done", terminal=True),
                "failed": StateModel(name="failed", terminal=True),
            },
            roles={
                "worker": RoleModel(name="worker", agent="mock"),
                "reviewer": RoleModel(name="reviewer", agent="mock"),
            },
        )

    def test_validate_clean(self):
        sm = StateMachine(self._make_test_workflow())
        issues = sm.validate()
        assert len(issues) == 0

    def test_resolve_transition_matched(self):
        sm = StateMachine(self._make_test_workflow())
        result = sm.resolve_transition("start", "done")
        assert result.next_state == "review"
        assert result.matched is True

    def test_resolve_transition_default(self):
        sm = StateMachine(self._make_test_workflow())
        # 未知 decision 走 default
        result = sm.resolve_transition("start", "unknown")
        assert result.next_state == "failed"
        assert result.matched is False

    def test_is_terminal(self):
        sm = StateMachine(self._make_test_workflow())
        assert sm.is_terminal("done")
        assert sm.is_terminal("failed")
        assert not sm.is_terminal("start")

    def test_get_state_names(self):
        sm = StateMachine(self._make_test_workflow())
        names = sm.get_state_names()
        assert "start" in names
        assert "review" in names

    def test_validate_missing_default(self):
        wf = self._make_test_workflow()
        wf.states["start"].default = ""
        sm = StateMachine(wf)
        issues = sm.validate()
        assert any("default" in i for i in issues)


class TestTransition:
    """Transition 纯函数测试。"""

    def test_resolve_matched(self):
        result = resolve_transition(
            {"approve": "execute", "revise": "revise_plan"},
            "approve",
            default="failed",
        )
        assert result.next_state == "execute"
        assert result.matched is True

    def test_resolve_unknown(self):
        result = resolve_transition(
            {"approve": "execute"},
            "revise",
            default="failed",
        )
        assert result.next_state == "failed"
        assert result.matched is False


class TestGuardChecker:
    """Guard 检查器测试。"""

    def _make_context(self) -> RunContext:
        return RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )

    def test_max_visits_pass(self):
        guard = GuardChecker(GuardModel(max_visits=5))
        ctx = self._make_context()
        ctx.record_state_visit("review_plan")
        result = guard.check("review_plan", ctx)
        assert result.passed

    def test_max_visits_fail(self):
        guard = GuardChecker(GuardModel(max_visits=2))
        ctx = self._make_context()
        ctx.record_state_visit("review_plan")
        ctx.record_state_visit("review_plan")
        ctx.record_state_visit("review_plan")  # 第 3 次
        result = guard.check("review_plan", ctx)
        # +1 because check() adds 1 for the current visit
        assert not result.passed
        assert result.guard_type == "max_visits"

    def test_max_retries_pass(self):
        guard = GuardChecker(GuardModel(max_retries=3))
        ctx = self._make_context()
        result = guard.check("test_state", ctx)
        assert result.passed

    def test_max_retries_fail(self):
        guard = GuardChecker(GuardModel(max_retries=1))
        ctx = self._make_context()
        ctx.record_state_visit("test_state")
        ctx.record_state_visit("test_state")
        result = guard.check("test_state", ctx)
        assert not result.passed
        assert result.guard_type == "max_retries"
