"""status — 运行状态查询。

输出格式（v4 计划 §10.5）：
  Current State:   claude_review_plan
  Running:         32m
  Last Heartbeat:  12s ago
  Transitions:     8
  Current Agent:   claude_review
  Last Decision:   revise
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta

from .heartbeat import check_stale


def _now() -> datetime:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz)


def get_status(run_id: str) -> str:
    """获取指定运行的状态摘要。

    返回可读的状态字符串。
    """
    run_root = os.path.join(".agent-workflow", "runs", run_id)

    # 检查运行目录是否存在
    if not os.path.exists(run_root):
        return f"[FAIL] 未找到运行: {run_id}\n路径: {run_root}"

    # 读取 workflow_state.json
    state_path = os.path.join(run_root, "workflow_state.json")
    state_data = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # 检查是否取消
    cancel_path = os.path.join(run_root, "cancelled")
    is_cancelled = os.path.exists(cancel_path)
    cancel_reason = ""
    if is_cancelled:
        try:
            with open(cancel_path, "r") as f:
                cancel_reason = f.read().strip()
        except Exception:
            pass

    # 提取字段
    current_state = state_data.get("current_state", "unknown")
    started_at = state_data.get("started_at", "")
    state_history = state_data.get("state_history", [])
    task_results = state_data.get("task_results", {})
    artifacts = state_data.get("artifacts", {})

    # 计算运行时长
    duration_str = "unknown"
    if started_at:
        try:
            start = datetime.fromisoformat(started_at)
            elapsed = _now() - start
            minutes = int(elapsed.total_seconds() / 60)
            if minutes < 60:
                duration_str = f"{minutes}m"
            else:
                hours = minutes // 60
                mins = minutes % 60
                duration_str = f"{hours}h {mins}m"
        except (ValueError, TypeError):
            pass

    # 检查心跳
    stale, stale_reason = check_stale(run_root)
    heartbeat_str = stale_reason

    # 最后 decision
    last_decision = "none"
    if task_results:
        last_state = state_history[-1] if state_history else ""
        last_result = task_results.get(last_state, {})
        last_decision = last_result.get("decision", "none")

    # 当前 task 和 agent — 优先从 workflow_variables 读取
    current_task = state_data.get("current_task", "") or ""
    wf_vars = state_data.get("workflow_variables", {})
    current_agent = (
        wf_vars.get("_current_agent", "") or
        task_results.get(current_state, {}).get("agent", "")
    )
    # 如果当前 state 没有 task_result，回退到最后一个有 agent 的 task_result
    if not current_agent and task_results:
        for s in reversed(state_history):
            agent = task_results.get(s, {}).get("agent", "")
            if agent:
                current_agent = agent
                break

    # 构建输出
    lines = []
    if is_cancelled:
        lines.append(f"[WARN] 已取消: {cancel_reason}" if cancel_reason else "[WARN] 已取消")

    lines.append(f"Current State:    {current_state}")
    lines.append(f"Running:          {duration_str}")
    lines.append(f"Last Heartbeat:   {heartbeat_str}")
    lines.append(f"Transitions:      {len(state_history)}")
    lines.append(f"Current Agent:    {current_agent}")
    lines.append(f"Last Decision:    {last_decision}")

    if stale:
        lines.append(f"[WARN] STALE: {stale_reason}")

    if state_history:
        lines.append(f"\nState History:")
        for i, s in enumerate(state_history, 1):
            marker = "← current" if s == current_state else ""
            lines.append(f"  {i}. {s} {marker}")

    if artifacts:
        lines.append(f"\nArtifacts:")
        for name, path in artifacts.items():
            lines.append(f"  - {name}: {path}")

    return "\n".join(lines)
