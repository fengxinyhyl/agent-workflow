"""explain — 状态解释。

输出当前等待项、allowed decisions、next states、Guard 状态。

输出格式（v4 计划 §10.6）：
  Current State:       claude_review_plan
  Waiting For:         TaskResult
  Allowed Decisions:   approve, revise, reject
  Transitions:         approve → codex_execute, revise → codex_revise_plan, reject → failed
  Default:             failed
  Guards:
    max_visits:        5
    max_duration_minutes: 360
    max_retries:       3
"""

from __future__ import annotations

import os
import json

from .heartbeat import check_stale


def get_explanation(run_id: str) -> str:
    """解释指定运行的当前状态和可能的后续步骤。

    返回可读的解释字符串。
    """
    run_root = os.path.join(".agent-workflow", "runs", run_id)

    if not os.path.exists(run_root):
        return f"❌ 未找到运行: {run_id}"

    # 读取 workflow_state.json
    state_path = os.path.join(run_root, "workflow_state.json")
    state_data = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # 读取原始 workflow 配置（如果有快照）
    wf_data = state_data.get("_workflow_snapshot", {})

    current_state = state_data.get("current_state", "unknown")
    states_data = wf_data.get("states", {})

    # 获取当前 state 配置
    state_config = states_data.get(current_state, {}) if states_data else {}
    task_name = state_config.get("task", "")
    on_map = state_config.get("on", {})
    default = state_config.get("default", "failed")

    # 获取 task 配置
    task_config = {}
    tasks_data = wf_data.get("tasks", {})
    if task_name and task_name in tasks_data:
        task_config = tasks_data[task_name]

    # 通过 event log 提取更多信息
    events_path = os.path.join(run_root, "logs", "events.jsonl")
    events = []
    if os.path.exists(events_path):
        try:
            with open(events_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except Exception:
            pass

    # 提取 allowed decisions
    allowed_decisions = task_config.get("allowed_decisions", [])
    if not allowed_decisions:
        allowed_decisions = list(on_map.keys()) if on_map else ["approve", "revise", "reject"]

    # 提取 Guard 配置
    guards = wf_data.get("guards", {})
    max_visits = guards.get("max_visits", 0)
    max_duration = guards.get("max_duration_minutes", 0)
    max_retries = guards.get("max_retries", 0)

    # 统计 visits
    state_history = state_data.get("state_history", [])
    attempts = state_data.get("attempts", {})

    # 检查心跳
    stale, stale_reason = check_stale(run_root)

    # 构建输出
    lines = [
        f"Current State:       {current_state}",
        f"Task:                {task_name}",
        f"Waiting For:         TaskResult",
    ]

    if stale:
        lines.append(f"⚠️  STALE: {stale_reason}")
    lines.append("")

    # Allowed Decisions
    lines.append(f"Allowed Decisions:   {', '.join(allowed_decisions) if allowed_decisions else '(any)'}")
    lines.append("")

    # Transitions
    lines.append("Transitions:")
    if on_map:
        for decision, next_state in sorted(on_map.items()):
            lines.append(f"  {decision:12s} → {next_state}")
    else:
        lines.append("  (none — terminal state)")
    lines.append(f"  {'default':12s} → {default}")
    lines.append("")

    # Guards
    lines.append("Guards:")
    lines.append(f"  max_visits:           {max_visits}" +
                 (f" (当前: {attempts.get(current_state, 0)})" if max_visits > 0 else ""))
    lines.append(f"  max_duration_minutes: {max_duration}" +
                 (" (未设置)" if max_duration == 0 else ""))
    lines.append(f"  max_retries:          {max_retries}" +
                 (f" (当前: {attempts.get(current_state, 0)}/{max_retries})" if max_retries > 0 else ""))
    lines.append("")

    # State 历史
    if state_history:
        lines.append(f"State History: {' → '.join(state_history)}")

    return "\n".join(lines)
