"""重试失败诊断 — 读取事件日志判定上次失败原因。

纯函数模块，与 retry 流程解耦，便于单元测试。
输入为 read_log() 返回的事件列表，输出诊断结论 dict。
"""

from __future__ import annotations

from typing import Any

# ── 诊断结论常量 ──────────────────────────────────────────────────────

# ValidatorFinished{passed=false} 导致中断 — 校验阻塞。重试有意义但需先修产物。
KIND_VALIDATOR_BLOCK = "validator_block"
# GuardFailed{guard_type ∈ {max_visits, max_retries}} — 回流/重试次数上限。重试无意义。
KIND_GUARD_LOOP = "guard_loop"
# GuardFailed{guard_type == max_duration_minutes} — 运行时长超限。重试会重置计时器。
KIND_GUARD_TIMEOUT = "guard_timeout"
# AgentStarted 后无完成事件 — Agent 进程崩溃。重试有意义。
KIND_AGENT_CRASH = "agent_crash"
# 无法识别失败类型。
KIND_UNKNOWN = "unknown"

# 完成信号集合：出现以下任一事件即判定 Agent 正常结束
_COMPLETION_EVENTS = {
    "TaskResultWritten",
    "ValidatorFinished",
    "TransitionSelected",
    "TaskFinished",
}

# 可忽略的中间事件（不影响崩溃判定）
_IGNORABLE_EVENTS = {
    "Heartbeat",
    "AgentOutput",
}


def diagnose_last_failure(events: list[dict[str, Any]]) -> dict[str, Any]:
    """分析事件日志，诊断上次运行的失败原因。

    匹配优先级（取第一个命中）：
      1. 最后一条 ValidatorFinished{passed=false} → validator_block
      2. 最后一条 GuardFailed → guard_loop / guard_timeout
      3. 最后一条 AgentStarted 后无完成信号 → agent_crash
      4. 否则 → unknown

    参数:
      events: read_log() 返回的事件字典列表（按时间顺序排列）

    返回:
      {
        "kind": str,              # 诊断类型常量
        "reason": str,            # 人类可读原因描述
        "retry_recommended": bool,  # 是否建议重试
        "detail": {               # 额外诊断细节
          "state": str,           # 相关 state
          "errors": [...],        # validator_block 时透传校验错误
          "guard_type": str,      # guard_loop/timeout 时透传 guard 类型
          ...
        },
      }
    """
    # 防御：空列表直接返回 unknown
    if not events:
        return {
            "kind": KIND_UNKNOWN,
            "reason": "无事件日志可供诊断",
            "retry_recommended": True,
            "detail": {},
        }

    # ── 1. 查找最后一条 ValidatorFinished{passed=false} ──────────
    validator_fail = None
    for e in reversed(events):
        if e.get("event") == "ValidatorFinished":
            payload = e.get("payload", {})
            if payload.get("passed") is False:
                validator_fail = e
                break

    if validator_fail is not None:
        payload = validator_fail.get("payload", {})
        errors = payload.get("errors", [])
        state = validator_fail.get("state", "") or payload.get("state", "")
        return {
            "kind": KIND_VALIDATOR_BLOCK,
            "reason": f"校验未通过（state={state}）",
            "retry_recommended": True,
            "detail": {
                "state": state,
                "errors": errors,
                "status_text": payload.get("status_text", ""),
                "blocking": payload.get("blocking", True),
            },
        }

    # ── 2. 查找最后一条 GuardFailed ─────────────────────────────
    guard_fail = None
    for e in reversed(events):
        if e.get("event") == "GuardFailed":
            guard_fail = e
            break

    if guard_fail is not None:
        payload = guard_fail.get("payload", {})
        guard_type = payload.get("guard_type", "")
        state = guard_fail.get("state", "") or payload.get("state", "")
        reason = payload.get("reason", "")

        if guard_type in ("max_visits", "max_retries"):
            return {
                "kind": KIND_GUARD_LOOP,
                "reason": f"回流/重试次数已达上限，重试无意义: {reason}",
                "retry_recommended": False,
                "detail": {
                    "state": state,
                    "guard_type": guard_type,
                    "current_value": payload.get("current_value"),
                    "threshold": payload.get("threshold"),
                },
            }
        elif guard_type == "max_duration_minutes":
            return {
                "kind": KIND_GUARD_TIMEOUT,
                "reason": f"运行时长超限，重试将重置计时器: {reason}",
                "retry_recommended": True,
                "detail": {
                    "state": state,
                    "guard_type": guard_type,
                    "current_value": payload.get("current_value"),
                    "threshold": payload.get("threshold"),
                },
            }

    # ── 3. 查找最后一条 AgentStarted 后是否有完成信号 ──────────
    last_agent_started_idx = None
    last_agent_started = None
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("event") == "AgentStarted":
            last_agent_started_idx = i
            last_agent_started = events[i]
            break

    if last_agent_started_idx is not None:
        # 检查该 AgentStarted 之后是否有完成信号
        has_completion = False
        for j in range(last_agent_started_idx + 1, len(events)):
            evt = events[j].get("event", "")
            if evt in _COMPLETION_EVENTS:
                has_completion = True
                break
            elif evt in _IGNORABLE_EVENTS:
                continue
            # 遇到其他非忽略事件（如新的 StateEntered 或 GuardFailed）
            # 说明流程已推进，Agent 正常结束
            has_completion = True
            break

        if not has_completion:
            state = last_agent_started.get("state", "")
            return {
                "kind": KIND_AGENT_CRASH,
                "reason": f"Agent 进程异常终止（state={state}，AgentStarted 后无完成信号）",
                "retry_recommended": True,
                "detail": {
                    "state": state,
                    "agent": last_agent_started.get("payload", {}).get("agent", ""),
                    "task": last_agent_started.get("task", ""),
                },
            }

    # ── 4. 无法识别 → unknown ───────────────────────────────────
    return {
        "kind": KIND_UNKNOWN,
        "reason": "未能从事件日志中识别明确的失败原因",
        "retry_recommended": True,
        "detail": {},
    }
