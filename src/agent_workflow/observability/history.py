"""事件历史渲染器 — 将事件日志渲染为因果时间线。

提供两个对外入口：
  render_history() → 主干事件时间线
  render_why()   → 反查某个 state 的进入原因链

内部暴露 _render_events(events, show_all) 供单元测试传入内存事件列表。
"""

from __future__ import annotations

from typing import Any

# ── 主干事件白名单（默认过滤模式） ────────────────────────────────────
# 不含 ValidatorStarted（降噪）、Heartbeat（高频）、AgentOutput（冗余）
MAIN_EVENT_TYPES = {
    "WorkflowStarted",
    "StateEntered",
    "AgentStarted",
    "TaskResultWritten",
    "ValidatorFinished",
    "ArtifactPromoted",
    "TransitionSelected",
    "GuardFailed",
    "TaskFinished",
    "WorkflowCompleted",
    "WorkflowFailed",
    "WorkflowCancelled",
    "SkillAdoptionWritten",
}


def _filter_main_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤出主干事件（白名单内的事件类型）。"""
    return [e for e in events if e.get("event", "") in MAIN_EVENT_TYPES]


def _format_event_line(event: dict[str, Any]) -> str:
    """将单条事件格式化为一行文本。

    格式: [timestamp] EventName  state=<state>  key1=value1  key2=value2 ...
    未识别的 payload 字段降级为通用打印。
    """
    ts = event.get("timestamp", "?")[:19]  # 截断到秒级别
    evt = event.get("event", "unknown")
    state = event.get("state", "")
    task = event.get("task", "")
    payload = event.get("payload", {})

    parts = [f"[{ts}] {evt}"]
    if state:
        parts.append(f"state={state}")
    if task:
        parts.append(f"task={task}")

    # 根据事件类型提取关键 payload 字段
    if evt == "TransitionSelected":
        cur = payload.get("current_state", "")
        dec = payload.get("decision", "")
        nxt = payload.get("next_state", "")
        if cur and dec and nxt:
            parts.append(f"{cur} --{dec}--> {nxt}")
    elif evt == "ValidatorFinished":
        passed = payload.get("passed", "")
        errors = payload.get("errors", [])
        parts.append(f"passed={passed}")
        if errors:
            parts.append(f"errors={len(errors)}")
    elif evt == "GuardFailed":
        gtype = payload.get("guard_type", "")
        reason = payload.get("reason", "")
        if gtype:
            parts.append(f"guard_type={gtype}")
        if reason:
            parts.append(f"reason=\"{reason[:80]}\"")
    elif evt == "AgentStarted":
        agent = payload.get("agent", "")
        if agent:
            parts.append(f"agent={agent}")
    elif evt == "TaskResultWritten":
        decision = payload.get("decision", "")
        status = payload.get("status", "")
        if decision:
            parts.append(f"decision={decision}")
        if status:
            parts.append(f"status={status}")
    elif evt == "ArtifactPromoted":
        name = payload.get("name", "")
        path = payload.get("artifact_path", "")
        if name:
            parts.append(f"name={name}")
        if path:
            parts.append(f"path={path}")
    elif evt == "WorkflowFailed":
        error = payload.get("error", "")
        if error:
            parts.append(f"error=\"{error[:80]}\"")
    elif evt == "WorkflowCancelled":
        reason = payload.get("reason", "")
        if reason:
            parts.append(f"reason=\"{reason[:80]}\"")

    return "  ".join(parts)


def _render_events(events: list[dict[str, Any]], show_all: bool = False) -> str:
    """将事件列表渲染为时间线文本（内部纯函数，便于测试）。

    参数:
      events: 事件字典列表
      show_all: True 时不过滤，显示所有事件
    """
    if not events:
        return "(无事件记录)"

    filtered = events if show_all else _filter_main_events(events)
    if not filtered:
        return "(无主干事件 — 使用 --all 查看全部事件)"

    lines = []
    for event in filtered:
        lines.append(_format_event_line(event))

    return "\n".join(lines)


def render_history(
    run_id: str,
    run_root: str | None = None,
    show_all: bool = False,
) -> str:
    """读取事件日志，渲染主干因果时间线。

    参数:
      run_id: 运行 ID
      run_root: 运行根目录（可选）
      show_all: True 时显示所有事件（不过滤心跳/输出行）
    """
    from .jsonl_sink import read_log

    events = read_log(run_id, run_root=run_root)
    if isinstance(events, str):
        return events

    header = f"=== 时间线: {run_id} ===\n"
    if show_all:
        header += "(显示全部事件)\n"
    else:
        header += f"(主干事件，共 {len(events)} 条事件，{len(_filter_main_events(events))} 条主干)\n"
    header += "-" * 60

    return header + "\n" + _render_events(events, show_all=show_all)


def _render_why_from_events(
    events: list[dict[str, Any]],
    target_state: str,
    run_id: str = "",
) -> str:
    """从事件列表反查某个 state 的进入原因链（内部纯函数，便于测试）。

    参数:
      events: 事件字典列表
      target_state: 要反查的目标 state
      run_id: 运行 ID（仅用于输出文本标注）
    """
    if not events:
        return f"未找到运行 {run_id} 的日志" if run_id else "事件列表为空"

    # 统计目标 state 总进入次数
    total_entries = sum(
        1 for e in events
        if e.get("event") == "TransitionSelected"
        and e.get("payload", {}).get("next_state") == target_state
    )

    # 构建反查链（从 target_state 开始回溯）
    chain = [target_state]
    cursor = target_state
    seen = {target_state}

    while True:
        # 取按时序最后一条进入 cursor 的 TransitionSelected
        matching = [
            e for e in events
            if e.get("event") == "TransitionSelected"
            and e.get("payload", {}).get("next_state") == cursor
        ]
        if not matching:
            break  # 找不到进入路径

        prev = matching[-1].get("payload", {}).get("current_state", "")

        # 防回环：gate→resume / 自循环 state
        if not prev or prev in seen:
            break

        chain.append(prev)
        seen.add(prev)
        cursor = prev

    # 构建输出文本
    header = f"=== 反查: {target_state} 的进入原因链"
    if run_id:
        header += f" (run {run_id})"
    lines = [header + " ==="]

    if total_entries == 0:
        lines.append(f"状态 \"{target_state}\" 在该 run 中从未被进入（无匹配的 TransitionSelected 事件）。")
    else:
        if total_entries > 1:
            lines.append(f"（该状态共被进入 {total_entries} 次，以下显示最近一次进入链）")
        lines.append("")
        # 反转 chain 为时间顺序
        ordered_chain = list(reversed(chain))
        lines.append(" → ".join(ordered_chain))

        # 补充每一步的 decision 信息
        lines.append("")
        lines.append("详细跳转:")
        for i in range(len(ordered_chain) - 1):
            src = ordered_chain[i]
            dst = ordered_chain[i + 1]
            # 找到对应的 TransitionSelected 事件（取最后一次）
            ts = None
            for e in events:
                p = e.get("payload", {})
                if (e.get("event") == "TransitionSelected"
                        and p.get("current_state") == src
                        and p.get("next_state") == dst):
                    ts = e  # 持续覆盖，取最后一条
            if ts:
                p = ts.get("payload", {})
                dec = p.get("decision", "?")
                lines.append(f"  {src} --{dec}--> {dst}")
            else:
                lines.append(f"  {src} --> {dst}")

        if len(chain) == 1:
            lines.append(f"（仅找到目标 state \"{target_state}\"，无法追溯到上游——可能是 initial_state 或事件日志不完整）")

        # 链头标注
        head = chain[-1]
        first_evt = events[0].get("event", "") if events else ""
        if first_evt == "WorkflowStarted":
            lines.append(f"\n  ({head} 是该 run 的初始状态或首次进入点)")

    return "\n".join(lines)


def render_why(
    run_id: str,
    run_root: str | None,
    target_state: str,
) -> str:
    """读取事件日志，反查某个 state 是如何被进入的。

    从最后一次进入 target_state 的 TransitionSelected 事件倒推，
    沿 current_state → 上一个 TransitionSelected(next_state=current_state) 链路回溯，
    直到追溯到 WorkflowStarted 或链路断开。

    防回环：用 seen 集合记录已访问 state，遇回环时停止。

    参数:
      run_id: 运行 ID
      run_root: 运行根目录（可选）
      target_state: 要反查的目标 state
    """
    from .jsonl_sink import read_log

    events = read_log(run_id, run_root=run_root)
    if isinstance(events, str):
        return events

    return _render_why_from_events(events, target_state, run_id=run_id)
