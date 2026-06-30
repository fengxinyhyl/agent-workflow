"""测试状态机核心：StateMachine、Transition、Guard。"""

import pytest

from agent_workflow.config.models import (
    WorkflowConfig, TaskModel, StateModel, GuardModel,
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
                "work": TaskModel(name="work", instruction="做工作", agent="mock", allowed_decisions=["done", "fail"]),
                "review": TaskModel(name="review", instruction="审查", agent="mock", allowed_decisions=["approve", "revise", "reject"]),
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
        )

    def test_validate_clean(self):
        sm = StateMachine(self._make_test_workflow())
        issues = sm.validate()
        assert len(issues) == 0

    def test_resolve_transition_matched(self):
        sm = StateMachine(self._make_test_workflow())
        result = sm.resolve_transition("start", "success", "done")
        assert result.next_state == "review"
        assert result.matched is True
        assert result.route_by == "decision"

    def test_resolve_transition_default(self):
        sm = StateMachine(self._make_test_workflow())
        # 未知 decision 走 default
        result = sm.resolve_transition("start", "success", "unknown")
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
                "make_plan": TaskModel(name="make_plan", instruction="制定计划", agent="mock", allowed_decisions=["done"]),
                "request_approval": TaskModel(
                    name="request_approval",
                    instruction="生成审批请求文档",
                    agent="mock",
                    allowed_decisions=["approve", "reject"],
                ),
                "execute_work": TaskModel(name="execute_work", instruction="执行工作", agent="mock", allowed_decisions=["done"]),
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
        result = sm.resolve_transition("human_approval", "success", "approve")
        assert result.next_state == "execute"
        assert result.matched is True

        result2 = sm.resolve_transition("human_approval", "success", "reject")
        assert result2.next_state == "failed"
        assert result2.matched is True


# ── Runtime v2: 两段式路由测试 ──

class TestTwoStageRouting:
    """两段式路由（status + decision）测试。"""

    def _make_wf(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="test",
            initial_state="linear",
            terminal_states=["done", "failed"],
            tasks={
                "line": TaskModel(name="line", instruction="线性", agent="mock"),
                "branch": TaskModel(name="branch", instruction="分支", agent="mock", allowed_decisions=["approve", "revise"]),
                "gate": TaskModel(name="gate", instruction="gate", agent="mock", allowed_decisions=["approve", "reject"]),
            },
            states={
                "linear": StateModel(
                    name="linear", task="line",
                    next="done",
                    default="failed",
                ),
                "branch": StateModel(
                    name="branch", task="branch",
                    on={"approve": "done", "revise": "linear"},
                    default="failed",
                ),
                "gate": StateModel(
                    name="gate", task="gate",
                    on={"approve": "done", "reject": "failed"},
                    on_status={"blocked": "linear"},
                    default="failed",
                ),
                "done": StateModel(name="done", terminal=True),
                "failed": StateModel(name="failed", terminal=True),
            },
        )

    def test_success_next_route_by_next(self):
        """status=success + state.next → 走 next, route_by='next'。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("linear", "success", None)
        assert result.next_state == "done"
        assert result.matched is True
        assert result.route_by == "next"
        assert result.status == "success"

    def test_success_on_matched_route_by_decision(self):
        """status=success + decision 匹配 on → route_by='decision'。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("branch", "success", "approve")
        assert result.next_state == "done"
        assert result.matched is True
        assert result.route_by == "decision"

    def test_success_on_unmatched_default(self):
        """status=success + decision 不匹配 on → 走 default, route_by='decision'。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("branch", "success", "unknown")
        assert result.next_state == "failed"
        assert result.matched is False
        assert result.route_by == "decision"

    def test_failed_to_default(self):
        """status=failed + 无 on_status → 走 default, route_by='status'。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("linear", "failed", None)
        assert result.next_state == "failed"
        assert result.matched is False
        assert result.route_by == "status"

    def test_blocked_to_on_status(self):
        """status=blocked + on_status 有 blocked → 走 on_status, route_by='status'。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("gate", "blocked", None)
        assert result.next_state == "linear"
        assert result.matched is True
        assert result.route_by == "status"

    def test_decision_none_with_next(self):
        """decision=None 在 next 节点正确路由。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("linear", "success", None)
        assert result.next_state == "done"
        assert result.route_by == "next"

    def test_transition_result_has_status_and_route_by(self):
        """TransitionResult 携带 status 和 route_by 字段。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("branch", "success", "approve")
        assert result.status == "success"
        assert result.route_by == "decision"
        d = result.to_event_dict()
        assert d["status"] == "success"
        assert d["route_by"] == "decision"

    def test_unknown_state_returns_failed(self):
        """未知 state → failed。"""
        sm = StateMachine(self._make_wf())
        result = sm.resolve_transition("nonexistent", "success", "done")
        assert result.next_state == "failed"
        assert result.matched is False
        assert result.route_by == "status"


# ── Runtime v2: 护栏测试 ──

class TestGuardrails:
    """新增两条 validate 护栏测试。"""

    def test_missing_success_exit_detected(self):
        """非终止节点无 on 无 next → 报错。"""
        wf = WorkflowConfig(
            name="test",
            initial_state="bad",
            terminal_states=["done"],
            tasks={"t": TaskModel(name="t", instruction="x", agent="mock")},
            states={
                "bad": StateModel(name="bad", task="t", default="done"),
                "done": StateModel(name="done", terminal=True),
            },
        )
        sm = StateMachine(wf)
        issues = sm.validate()
        assert any("未定义成功出口" in i for i in issues)

    def test_both_next_and_on_detected(self):
        """同时有 on 和 next → 报错。"""
        wf = WorkflowConfig(
            name="test",
            initial_state="bad",
            terminal_states=["done"],
            tasks={"t": TaskModel(name="t", instruction="x", agent="mock", allowed_decisions=["done"])},
            states={
                "bad": StateModel(name="bad", task="t", next="done", on={"done": "done"}, default="done"),
                "done": StateModel(name="done", terminal=True),
            },
        )
        sm = StateMachine(wf)
        issues = sm.validate()
        assert any("同时定义了 on 和 next" in i for i in issues)

    def test_on_without_allowed_decisions_detected(self):
        """有 on 但 task 无 allowed_decisions → 报错。"""
        wf = WorkflowConfig(
            name="test",
            initial_state="bad",
            terminal_states=["done"],
            tasks={"t": TaskModel(name="t", instruction="x", agent="mock")},
            states={
                "bad": StateModel(name="bad", task="t", on={"approve": "done"}, default="done"),
                "done": StateModel(name="done", terminal=True),
            },
        )
        sm = StateMachine(wf)
        issues = sm.validate()
        assert any("allowed_decisions" in i for i in issues)


# ── Runtime v2: Traversal 补全测试 ──

class TestTraversalCompleteness:
    """_find_reachable / get_state_names 通过 next/on_status 发现 state。"""

    def _make_wf_with_next_and_on_status(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="test",
            initial_state="start",
            terminal_states=["done", "failed"],
            tasks={
                "t1": TaskModel(name="t1", instruction="x", agent="mock"),
                "t2": TaskModel(name="t2", instruction="x", agent="mock"),
                "t3": TaskModel(name="t3", instruction="x", agent="mock"),
            },
            states={
                "start": StateModel(name="start", task="t1", next="mid", default="failed"),
                "mid": StateModel(name="mid", task="t2", on_status={"blocked": "recovery"}, default="failed"),
                "recovery": StateModel(name="recovery", task="t3", next="done", default="failed"),
                "done": StateModel(name="done", terminal=True),
                "failed": StateModel(name="failed", terminal=True),
            },
        )

    def test_find_reachable_via_next(self):
        """_find_reachable 通过 next 发现 state。"""
        sm = StateMachine(self._make_wf_with_next_and_on_status())
        reachable = sm._find_reachable()
        assert "mid" in reachable  # 通过 start.next 可达

    def test_find_reachable_via_on_status(self):
        """_find_reachable 通过 on_status 发现 state。"""
        sm = StateMachine(self._make_wf_with_next_and_on_status())
        reachable = sm._find_reachable()
        assert "recovery" in reachable  # 通过 mid.on_status["blocked"] 可达

    def test_get_state_names_includes_next_path(self):
        """get_state_names 包含 next 路径的 state。"""
        sm = StateMachine(self._make_wf_with_next_and_on_status())
        names = sm.get_state_names()
        assert "mid" in names
        assert "recovery" in names

    def test_terminal_auto_detect_with_next_only(self):
        """仅有 next 无 on 的 state 不被误判为 terminal。"""
        sm = StateMachine(self._make_wf_with_next_and_on_status())
        terminals = sm.get_terminal_states()
        assert "start" not in terminals  # 有 next，不是 terminal
        assert "mid" not in terminals  # 有 on_status，不是 terminal

    def test_blocked_to_non_default_on_status(self):
        """blocked 去往不同于 default 的 on_status 目标。"""
        sm = StateMachine(self._make_wf_with_next_and_on_status())
        result = sm.resolve_transition("mid", "blocked", None)
        assert result.next_state == "recovery"
        assert result.route_by == "status"


# ── Runtime v2: 序列化测试 ──

class TestStateModelSerialization:
    """StateModel next/on_status 序列化往返。"""

    def test_next_field_in_to_dict(self):
        state = StateModel(name="test", next="done")
        d = state.to_dict()
        assert d["next"] == "done"

    def test_on_status_field_in_to_dict(self):
        state = StateModel(name="test", on_status={"blocked": "recovery"})
        d = state.to_dict()
        assert d["on_status"] == {"blocked": "recovery"}

    def test_next_field_from_dict(self):
        wf = WorkflowConfig.from_dict({
            "name": "test",
            "states": {
                "linear": {"name": "linear", "next": "done", "task": "t1", "default": "failed"},
            },
            "tasks": {"t1": {"name": "t1"}},
        })
        assert wf.states["linear"].next == "done"

    def test_on_status_field_from_dict(self):
        wf = WorkflowConfig.from_dict({
            "name": "test",
            "states": {
                "gate": {"name": "gate", "on_status": {"blocked": "audit"}, "task": "t1", "default": "failed"},
            },
            "tasks": {"t1": {"name": "t1"}},
        })
        assert wf.states["gate"].on_status == {"blocked": "audit"}

    def test_workflow_config_validate_checks_next_target(self):
        """WorkflowConfig.validate 检查 next 目标存在性。"""
        wf = WorkflowConfig(
            name="test",
            initial_state="bad",
            states={
                "bad": StateModel(name="bad", next="nonexistent", default="bad"),
            },
        )
        issues = wf.validate()
        assert any("next" in i and "nonexistent" in i for i in issues)

    def test_workflow_config_validate_checks_on_status_target(self):
        """WorkflowConfig.validate 检查 on_status 目标存在性。"""
        wf = WorkflowConfig(
            name="test",
            initial_state="bad",
            states={
                "bad": StateModel(name="bad", on_status={"blocked": "nonexistent"}, default="bad"),
            },
        )
        issues = wf.validate()
        assert any("on_status" in i and "nonexistent" in i for i in issues)
