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


class TestGateState:
    """Gate 状态功能测试。"""

    def _make_workflow_with_gate(self) -> WorkflowConfig:
        """创建含 Gate 状态的测试 workflow。"""
        return WorkflowConfig(
            name="test_gate",
            initial_state="plan",
            terminal_states=["done", "failed"],
            tasks={
                "make_plan": TaskModel(name="make_plan", instruction="制定计划", role="planner"),
                "request_approval": TaskModel(
                    name="request_approval",
                    instruction="生成审批请求文档",
                    role="approver",
                    allowed_decisions=["request_approval"],
                ),
                "execute_work": TaskModel(name="execute_work", instruction="执行工作", role="worker"),
            },
            states={
                "plan": StateModel(
                    name="plan", task="make_plan",
                    on={"done": "human_approval"},
                    default="failed",
                ),
                "human_approval": StateModel(
                    name="human_approval", task="request_approval",
                    on={"approve": "execute", "reject": "failed"},
                    default="failed",
                    gate=True,  # ← Gate 状态标记
                ),
                "execute": StateModel(
                    name="execute", task="execute_work",
                    on={"done": "done"},
                    default="failed",
                ),
                "done": StateModel(name="done", terminal=True),
                "failed": StateModel(name="failed", terminal=True),
            },
            roles={
                "planner": RoleModel(name="planner", agent="mock"),
                "approver": RoleModel(name="approver", agent="mock"),
                "worker": RoleModel(name="worker", agent="mock"),
            },
        )

    def test_is_gate_state_returns_true(self):
        """is_gate_state() 对 gate=True 的 state 返回 True。"""
        wf = self._make_workflow_with_gate()
        sm = StateMachine(wf)
        assert sm.is_gate_state("human_approval") is True

    def test_is_gate_state_returns_false_for_normal(self):
        """is_gate_state() 对普通 state 返回 False。"""
        wf = self._make_workflow_with_gate()
        sm = StateMachine(wf)
        assert sm.is_gate_state("plan") is False
        assert sm.is_gate_state("execute") is False

    def test_is_gate_state_returns_false_for_terminal(self):
        """is_gate_state() 对终止状态返回 False。"""
        wf = self._make_workflow_with_gate()
        sm = StateMachine(wf)
        assert sm.is_gate_state("done") is False
        assert sm.is_gate_state("failed") is False

    def test_is_gate_state_returns_false_for_unknown(self):
        """is_gate_state() 对不存在的 state 返回 False。"""
        wf = self._make_workflow_with_gate()
        sm = StateMachine(wf)
        assert sm.is_gate_state("nonexistent") is False

    def test_gate_field_in_to_dict(self):
        """StateModel.to_dict() 包含 gate 字段。"""
        state = StateModel(name="approval", gate=True, on={"approve": "next"})
        d = state.to_dict()
        assert "gate" in d
        assert d["gate"] is True

    def test_gate_field_defaults_false(self):
        """普通 StateModel 的 gate 默认为 False。"""
        state = StateModel(name="normal", on={"done": "next"})
        assert state.gate is False

    def test_gate_in_workflow_config_to_dict(self):
        """WorkflowConfig.to_dict() 中 states 含 gate 字段。"""
        wf = self._make_workflow_with_gate()
        d = wf.to_dict()
        approval_state = d["states"]["human_approval"]
        assert approval_state["gate"] is True
        plan_state = d["states"]["plan"]
        assert "gate" in plan_state
        assert plan_state["gate"] is False

    def test_gate_resolve_transition_still_works(self):
        """Gate 状态的 resolve_transition 仍正常工作（不影响 transition 规则）。"""
        wf = self._make_workflow_with_gate()
        sm = StateMachine(wf)
        # Gate state 的 transition 规则不受影响
        result = sm.resolve_transition("human_approval", "approve")
        assert result.next_state == "execute"
        assert result.matched is True

        result2 = sm.resolve_transition("human_approval", "reject")
        assert result2.next_state == "failed"
        assert result2.matched is True
