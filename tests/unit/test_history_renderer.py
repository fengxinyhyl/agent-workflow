"""history 渲染器单元测试 — 覆盖主干过滤、show_all、render_why 链式反查。"""

import pytest
from agent_workflow.observability.history import (
    _filter_main_events,
    _format_event_line,
    _render_events,
    _render_why_from_events,
    MAIN_EVENT_TYPES,
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


# ── 用例 1: 主干过滤 ──────────────────────────────────────────────────

def test_render_history_main_events_only():
    """render_history 主干模式：含 Heartbeat/AgentOutput/ValidatorStarted 的混合输入 → 输出只含 MAIN_EVENT_TYPES。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="plan"),
        _make_event("AgentStarted", state="plan", payload={"agent": "claude"}),
        _make_event("AgentOutput", state="plan", payload={"content": "output chunk"}),
        _make_event("Heartbeat", state="plan", payload={"elapsed_seconds": 30}),
        _make_event("ValidatorStarted", state="plan", payload={"validator": "task_result"}),
        _make_event("ValidatorFinished", state="plan", payload={"passed": True}),
        _make_event("TransitionSelected", state="plan", payload={
            "current_state": "plan", "decision": "done", "next_state": "done",
        }),
        _make_event("WorkflowCompleted", state="done"),
    ]

    filtered = _filter_main_events(events)
    filtered_events = [e["event"] for e in filtered]

    # 主干应包含
    assert "WorkflowStarted" in filtered_events
    assert "StateEntered" in filtered_events
    assert "AgentStarted" in filtered_events
    assert "ValidatorFinished" in filtered_events
    assert "TransitionSelected" in filtered_events
    assert "WorkflowCompleted" in filtered_events

    # 非主干不应出现
    assert "AgentOutput" not in filtered_events
    assert "Heartbeat" not in filtered_events
    assert "ValidatorStarted" not in filtered_events


# ── 用例 2: show_all ──────────────────────────────────────────────────

def test_render_history_show_all():
    """render_history show_all=True：所有事件都出现。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="plan"),
        _make_event("Heartbeat", state="plan", payload={"elapsed_seconds": 10}),
    ]
    output = _render_events(events, show_all=True)
    assert "WorkflowStarted" in output
    assert "StateEntered" in output
    assert "Heartbeat" in output


# ── 用例 3: render_why 链式反查 ───────────────────────────────────────

def test_render_why_chain():
    """构造 a→b→c 的 TransitionSelected 链 → render_why(target='c') 输出 a → b → c。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("TransitionSelected", payload={
            "current_state": "init", "decision": "done", "next_state": "a",
        }),
        _make_event("TransitionSelected", payload={
            "current_state": "a", "decision": "done", "next_state": "b",
        }),
        _make_event("TransitionSelected", payload={
            "current_state": "b", "decision": "done", "next_state": "c",
        }),
    ]
    output = _render_why_from_events(events, target_state="c", run_id="test-run")
    assert "a" in output
    assert "b" in output
    assert "c" in output
    assert "a → b → c" in output


# ── 用例 4: render_why 防回环 ─────────────────────────────────────────

def test_render_why_cycle_prevention():
    """a→b→a→c 事件序列、target=c → 不死循环且输出可读。"""
    events = [
        _make_event("TransitionSelected", payload={
            "current_state": "init", "decision": "done", "next_state": "a",
        }),
        _make_event("TransitionSelected", payload={
            "current_state": "a", "decision": "done", "next_state": "b",
        }),
        _make_event("TransitionSelected", payload={
            "current_state": "b", "decision": "done", "next_state": "a",  # 回环
        }),
        _make_event("TransitionSelected", payload={
            "current_state": "a", "decision": "done", "next_state": "c",
        }),
    ]
    output = _render_why_from_events(events, target_state="c")
    # 不应崩溃，输出应包含链
    assert "a" in output
    assert "c" in output
    # 链应为 b → a → c（回环处从 b 跳回 a 后 seen 阻止继续）
    assert "b → a → c" in output


# ── 用例 5: render_why 未找到目标 state ───────────────────────────────

def test_render_why_target_never_entered():
    """target=z 但无 TransitionSelected → 输出包含 '从未被进入' 或等效友好提示。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("TransitionSelected", payload={
            "current_state": "a", "decision": "done", "next_state": "b",
        }),
    ]
    output = _render_why_from_events(events, target_state="z")
    assert "从未被进入" in output or "未找到" in output


# ── 用例 6: _format_event_line 各事件类型快照 ───────────────────────────

def test_format_event_line_coverage():
    """覆盖各事件类型的格式化输出，确保不抛异常。"""
    test_cases = [
        (_make_event("WorkflowStarted", state=""), "WorkflowStarted"),
        (_make_event("StateEntered", state="plan"), "state=plan"),
        (_make_event("AgentStarted", state="plan", payload={"agent": "claude"}), "agent=claude"),
        (_make_event("TaskResultWritten", state="plan", payload={
            "decision": "done", "status": "success"}), "decision=done"),
        (_make_event("ValidatorFinished", state="plan", payload={
            "passed": False, "errors": ["err1", "err2"]}), "passed=False"),
        (_make_event("ArtifactPromoted", state="plan", payload={
            "name": "plan_doc", "artifact_path": "artifacts/plan_doc.md"}), "name=plan_doc"),
        (_make_event("TransitionSelected", state="plan", payload={
            "current_state": "plan", "decision": "done", "next_state": "review"}), "plan"),
        (_make_event("GuardFailed", state="plan", payload={
            "guard_type": "max_visits", "reason": "访问次数 6 > 5"}), "guard_type=max_visits"),
        (_make_event("WorkflowCompleted", state="done"), "WorkflowCompleted"),
        (_make_event("WorkflowFailed", state="failed", payload={
            "error": "some error"}), "WorkflowFailed"),
        (_make_event("WorkflowCancelled", state="cancelled", payload={
            "reason": "user cancelled"}), "WorkflowCancelled"),
        (_make_event("SkillAdoptionWritten", state="plan"), "SkillAdoptionWritten"),
    ]

    for event, expected_substr in test_cases:
        line = _format_event_line(event)
        assert expected_substr in line, f"期望 '{expected_substr}' 在行内: {line}"

    # 空事件测试
    empty_output = _render_events([], show_all=False)
    assert "无事件" in empty_output

    # 空主干测试
    only_noise = [_make_event("Heartbeat", state="plan")]
    noise_output = _render_events(only_noise, show_all=False)
    assert "无主干事件" in noise_output


# ── 用例 7: Runtime v2 route_by 展示 ──────────────────────────────────────

def test_format_transition_with_route_by_status():
    """TransitionSelected 含 status + route_by → 输出标注 [status] 或 [decision]。"""
    event = _make_event("TransitionSelected", payload={
        "current_state": "plan", "decision": "", "next_state": "failed",
        "status": "failed", "route_by": "status",
    })
    line = _format_event_line(event)
    assert "plan" in line
    assert "failed" in line
    assert "[status]" in line


def test_format_transition_with_route_by_decision():
    """TransitionSelected route_by=decision → 输出标注 [decision]。"""
    event = _make_event("TransitionSelected", payload={
        "current_state": "review", "decision": "approve", "next_state": "execute",
        "status": "success", "route_by": "decision",
    })
    line = _format_event_line(event)
    assert "review" in line
    assert "approve" in line
    assert "[decision]" in line


def test_format_transition_with_route_by_next():
    """TransitionSelected route_by=next → 输出标注 [next]。"""
    event = _make_event("TransitionSelected", payload={
        "current_state": "execute", "decision": "", "next_state": "summary",
        "status": "success", "route_by": "next",
    })
    line = _format_event_line(event)
    assert "execute" in line
    assert "summary" in line
    assert "[next]" in line


def test_format_transition_no_route_by_legacy():
    """旧格式 TransitionSelected 无 route_by → 不崩溃，正常显示。"""
    event = _make_event("TransitionSelected", payload={
        "current_state": "old", "decision": "done", "next_state": "next",
    })
    line = _format_event_line(event)
    assert "old" in line
    assert "next" in line
    # 旧格式无 route_by，不应有方括号标签
    assert "[status]" not in line
    assert "[decision]" not in line
