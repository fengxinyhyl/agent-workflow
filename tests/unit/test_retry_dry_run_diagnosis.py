"""retry dry-run 诊断步集成测试 — 验证 _build_dry_run_steps 正确插入诊断 step。

用 tmp_path 构造最小可加载 RunContext + events.jsonl，调用 retry_run 断言。
"""

import json
import os

import pytest
from agent_workflow.context.run_context import RunContext
from agent_workflow.state_machine.retry import retry_run

# ── 最简 Workflow 快照 ────────────────────────────────────────────────
# 集中维护，以应对未来 WorkflowConfig.from_dict schema 变化。
MINIMAL_WORKFLOW_SNAPSHOT = {
    "name": "diag-test",
    "initial_state": "s1",
    "terminal_states": ["done", "failed"],
    "tasks": {
        "t1": {"instruction": "noop", "agent": "mock"},
    },
    "states": {
        "s1": {"task": "t1", "on": {"done": "done", "fail": "failed"}, "default": "failed"},
        "done": {},
        "failed": {},
    },
}


def _write_state_and_events(tmp_path, run_id="test-run", events=None):
    """在 tmp_path 下构造可加载的 RunContext + events.jsonl。

    返回 run_root 路径。
    """
    run_root = str(tmp_path / run_id)

    # 创建 RunContext
    ctx = RunContext(
        run_id=run_id,
        workflow_id="diag-test",
        goal="test",
        project_root=str(tmp_path),
        run_root=run_root,
        current_state="s1",
        workflow_variables={
            "_workflow_snapshot": MINIMAL_WORKFLOW_SNAPSHOT,
        },
        state_history=["s1"],
        attempts={"s1": 1},
    )

    # 保存 workflow_state.json
    os.makedirs(run_root, exist_ok=True)
    ctx_path = os.path.join(run_root, "workflow_state.json")
    with open(ctx_path, "w", encoding="utf-8") as f:
        f.write(ctx.to_json())

    # 写 events.jsonl
    if events is not None:
        logs_dir = os.path.join(run_root, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        events_path = os.path.join(logs_dir, "events.jsonl")
        with open(events_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    return run_root


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


# ── 用例 1: 日志含 ValidatorFinished(passed=false) → 诊断 step 含 validator_block ──

def test_dry_run_diagnose_validator_block(tmp_path):
    """用 tmp_path 构造运行目录，调用 retry_run(dry_run=True)，
    断言 steps 中 diagnose_last_failure step 的 kind=validator_block。"""
    events = [
        _make_event("WorkflowStarted", state=""),
        _make_event("StateEntered", state="s1"),
        _make_event("AgentStarted", state="s1", payload={"agent": "mock"}),
        _make_event("ValidatorFinished", state="s1", payload={
            "passed": False,
            "errors": ["产物缺失"],
        }),
    ]
    run_root = _write_state_and_events(tmp_path, run_id="test-vb", events=events)

    result = retry_run("test-vb", dry_run=True, run_root=run_root)

    assert result["ok"] is True
    steps = result.get("steps", [])
    diag_steps = [s for s in steps if s["action"] == "diagnose_last_failure"]
    assert len(diag_steps) == 1, f"期望 1 个 diagnose_last_failure step，实际 steps: {[s['action'] for s in steps]}"

    diag = diag_steps[0]
    assert diag["detail"]["kind"] == "validator_block"
    assert diag["detail"]["retry_recommended"] is True
    assert diag["detail"]["detail"]["errors"] == ["产物缺失"]


# ── 用例 2: events.jsonl 不存在 → 仍含 diagnose step，kind=unknown ──

def test_dry_run_diagnose_no_events_file(tmp_path):
    """日志文件不存在时仍诊断，kind=unknown，retry_recommended=True。"""
    run_root = _write_state_and_events(tmp_path, run_id="test-noevt", events=None)

    result = retry_run("test-noevt", dry_run=True, run_root=run_root)

    assert result["ok"] is True
    steps = result.get("steps", [])
    diag_steps = [s for s in steps if s["action"] == "diagnose_last_failure"]
    assert len(diag_steps) == 1

    diag = diag_steps[0]
    assert diag["detail"]["kind"] == "unknown"
    assert diag["detail"]["retry_recommended"] is True
