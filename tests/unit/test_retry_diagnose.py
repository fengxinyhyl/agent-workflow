"""retry_diagnose 模块单元测试 — 8 个用例覆盖所有诊断分支与边界。"""

import pytest
from agent_workflow.state_machine.retry_diagnose import (
    diagnose_last_failure,
    KIND_VALIDATOR_BLOCK,
    KIND_GUARD_LOOP,
    KIND_GUARD_TIMEOUT,
    KIND_AGENT_CRASH,
    KIND_UNKNOWN,
)


# ── 辅助工厂函数 ──────────────────────────────────────────────────────

def _make_event(event: str, state: str = "", task: str = "",
                timestamp: str = "2026-06-26T10:00:00", payload: dict | None = None) -> dict:
    """快捷构造事件字典。"""
    return {
        "event": event,
        "timestamp": timestamp,
        "run_id": "test-run",
        "state": state,
        "task": task,
        "payload": payload or {},
    }


def _make_ts(payload: dict) -> dict:
    """快捷构造 TransitionSelected 事件。"""
    return _make_event("TransitionSelected", payload=payload)


# ── 用例 1: validator_block ───────────────────────────────────────────

def test_validator_block():
    """末尾 ValidatorFinished(passed=false, errors=[...]) → validator_block，errors 透传。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="review"),
        _make_event("AgentStarted", state="review", payload={"agent": "claude"}),
        _make_event("ValidatorFinished", state="review", payload={
            "passed": False,
            "errors": ["产物缺失: plan_doc", "格式不符"],
            "status_text": "2 项阻断错误",
            "blocking": True,
        }),
    ]
    result = diagnose_last_failure(events)
    assert result["kind"] == KIND_VALIDATOR_BLOCK
    assert result["retry_recommended"] is True
    assert result["detail"]["state"] == "review"
    assert result["detail"]["errors"] == ["产物缺失: plan_doc", "格式不符"]


# ── 用例 2: guard_loop max_visits ─────────────────────────────────────

def test_guard_loop_max_visits():
    """GuardFailed(max_visits) → guard_loop，retry_recommended=False。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="plan"),
        _make_event("GuardFailed", state="plan", payload={
            "passed": False,
            "guard_type": "max_visits",
            "reason": "state 'plan' 访问次数 6 > max_visits 5",
            "current_value": 6,
            "threshold": 5,
        }),
    ]
    result = diagnose_last_failure(events)
    assert result["kind"] == KIND_GUARD_LOOP
    assert result["retry_recommended"] is False
    assert result["detail"]["guard_type"] == "max_visits"


# ── 用例 3: guard_loop max_retries ────────────────────────────────────

def test_guard_loop_max_retries():
    """GuardFailed(max_retries) → guard_loop，retry_recommended=False。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("GuardFailed", state="build", payload={
            "passed": False,
            "guard_type": "max_retries",
            "reason": "state 'build' 重试次数 4 > max_retries 3",
            "current_value": 4,
            "threshold": 3,
        }),
    ]
    result = diagnose_last_failure(events)
    assert result["kind"] == KIND_GUARD_LOOP
    assert result["retry_recommended"] is False
    assert result["detail"]["guard_type"] == "max_retries"


# ── 用例 4: guard_timeout max_duration_minutes（新增） ─────────────────

def test_guard_timeout_max_duration_minutes():
    """GuardFailed(max_duration_minutes) → guard_timeout，retry_recommended=True。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("GuardFailed", state="execute", payload={
            "passed": False,
            "guard_type": "max_duration_minutes",
            "reason": "运行时长 485.3min > max_duration_minutes 480min",
            "current_value": 485.3,
            "threshold": 480,
        }),
    ]
    result = diagnose_last_failure(events)
    assert result["kind"] == KIND_GUARD_TIMEOUT
    assert result["retry_recommended"] is True
    assert result["detail"]["guard_type"] == "max_duration_minutes"


# ── 用例 5: agent_crash ───────────────────────────────────────────────

def test_agent_crash():
    """AgentStarted 后仅 Heartbeat → agent_crash，retry_recommended=True。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="build"),
        _make_event("AgentStarted", state="build", payload={"agent": "codex"}),
        _make_event("Heartbeat", state="build", payload={"elapsed_seconds": 120}),
    ]
    result = diagnose_last_failure(events)
    assert result["kind"] == KIND_AGENT_CRASH
    assert result["retry_recommended"] is True
    assert result["detail"]["state"] == "build"
    assert result["detail"]["agent"] == "codex"


# ── 用例 6: agent_crash 多 state（新增） ───────────────────────────────

def test_agent_crash_multi_state():
    """s1 正常完成 + s2 AgentStarted 无完成信号 → agent_crash，detail.state=s2。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="s1"),
        _make_event("AgentStarted", state="s1", payload={"agent": "mock"}),
        _make_event("TaskResultWritten", state="s1", payload={"decision": "done"}),
        _make_event("TransitionSelected", state="s1", payload={
            "current_state": "s1", "decision": "done", "next_state": "s2",
        }),
        _make_event("StateEntered", state="s2"),
        _make_event("AgentStarted", state="s2", payload={"agent": "claude"}),
        _make_event("Heartbeat", state="s2", payload={"elapsed_seconds": 60}),
    ]
    result = diagnose_last_failure(events)
    assert result["kind"] == KIND_AGENT_CRASH
    assert result["retry_recommended"] is True
    assert result["detail"]["state"] == "s2"  # 不是 s1


# ── 用例 7: ValidatorFinished(passed=true) 不误判（新增） ─────────────

def test_validator_passed_falls_through():
    """末尾 ValidatorFinished(passed=true) + AgentStarted 无后续 → agent_crash。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="review"),
        _make_event("AgentStarted", state="review", payload={"agent": "claude"}),
        _make_event("ValidatorFinished", state="review", payload={
            "passed": True,
            "warnings": ["格式建议: 使用二级标题"],
        }),
        _make_event("StateEntered", state="execute"),
        _make_event("AgentStarted", state="execute", payload={"agent": "codex"}),
        # execute 的 AgentStarted 后无完成信号
    ]
    result = diagnose_last_failure(events)
    # passed=true 不阻塞 → 不应判定为 validator_block
    assert result["kind"] != KIND_VALIDATOR_BLOCK
    assert result["kind"] == KIND_AGENT_CRASH
    assert result["detail"]["state"] == "execute"


# ── 用例 8: unknown + 空事件 ──────────────────────────────────────────

def test_unknown_and_empty():
    """空列表 + 仅 WorkflowStarted → unknown，不抛异常。"""
    # 空列表
    result_empty = diagnose_last_failure([])
    assert result_empty["kind"] == KIND_UNKNOWN
    assert result_empty["retry_recommended"] is True
    assert result_empty["detail"] == {}

    # 仅正常事件，无失败信号
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="plan"),
        _make_event("AgentStarted", state="plan"),
        _make_event("TaskResultWritten", state="plan", payload={"decision": "done"}),
        _make_event("TransitionSelected", state="plan", payload={
            "current_state": "plan", "decision": "done", "next_state": "done",
        }),
        _make_event("WorkflowCompleted", state="done"),
    ]
    result_normal = diagnose_last_failure(events)
    assert result_normal["kind"] == KIND_UNKNOWN
    assert result_normal["retry_recommended"] is True
