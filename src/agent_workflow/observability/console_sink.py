"""ConsoleSink — 实时终端输出。

提供工作流运行时的最小实时感知：
  [STATE] Enter: plan
  [AGENT] Start: plan <- claude_plan
  [RESULT] plan | agent=claude_plan decision=done duration=175s tokens=51764+13964
  [TRANSITION] plan -> review

ConsoleSink 不替代 execution log，只提供实时感知。
"""

from __future__ import annotations

import sys
from typing import Any


def _format_duration(seconds: float) -> str:
    """格式化耗时为人类可读形式。"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m"


def _format_tokens(n: int) -> str:
    """格式化 token 数为人类可读形式。"""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


class ConsoleSink:
    """控制台实时输出 sink。

    根据事件类型输出不同格式的行到 stderr（避免与 Agent 输出到 stdout 混在一起）。
    """

    # 事件类型 → 输出格式（使用 ASCII 标记以兼容 Windows 默认终端编码）
    FORMATS = {
        "WorkflowStarted": "[WORKFLOW] Start: {workflow_id} -> {goal}",
        "WorkflowCompleted": "",  # 由 write() 特殊处理，输出汇总表
        "WorkflowFailed": "",     # 由 write() 特殊处理，输出汇总表
        "WorkflowCancelled": "[WORKFLOW] [STOP] Cancelled: {reason}",
        "StateEntered": "[STATE] Enter: {state}",
        "AgentStarted": "[AGENT] Start: {task} <- {agent}",
        "AgentOutput": "[{agent}] {content}",
        "TaskResultWritten": "[RESULT] TaskResult written: {state}",
        "SkillAdoptionWritten": "[SKILL] Adoption: {state} <- {skills}",
        "ValidatorStarted": "[VALIDATOR] Start: {validator} -> {state}",
        "ValidatorFinished": "[VALIDATOR] [{status_text}]: {state}",
        "ArtifactPromoted": "[ARTIFACT] Promoted: {name} -> {artifact_path}",
        "TransitionSelected": "[TRANSITION] {current_state} -> {next_state}",
        "GuardFailed": "[GUARD] [FAIL] {guard_type}: {reason}",
        "Heartbeat": "",  # 不打印心跳
        "TaskFinished": "",  # 由 write() 特殊处理，格式化输出
    }

    def __init__(self, stream=None, show_heartbeat: bool = False):
        self.stream = stream or sys.stderr
        self.show_heartbeat = show_heartbeat
        self._state_count = 0

    def write(self, event_type: str, event: dict[str, Any]):
        """处理一个事件并输出到控制台。"""
        if event_type == "Heartbeat" and not self.show_heartbeat:
            return

        payload = event.get("payload", {})

        # ── TaskFinished: 实时行内展示耗时/token/agent ──
        if event_type == "TaskFinished":
            # state/run_id 可能被 event_bus pop 到 event 顶层
            state = payload.get("state", "") or event.get("state", "")
            agent = payload.get("agent", "") or ""
            decision = payload.get("decision", "")
            status = payload.get("status", "")
            duration = payload.get("duration_seconds", 0) or 0
            it = payload.get("input_tokens", 0) or 0
            ot = payload.get("output_tokens", 0) or 0

            dur_str = _format_duration(duration)
            token_str = f"{_format_tokens(it)}+{_format_tokens(ot)}"
            line = (
                f"[RESULT] {state} | agent={agent} decision={decision}"
                f" status={status} duration={dur_str} tokens={token_str}"
            )
            self._safe_write(f"  {line}\n")
            return

        # ── WorkflowCompleted / WorkflowFailed: 输出汇总表 ──
        if event_type in ("WorkflowCompleted", "WorkflowFailed"):
            status_text = "OK" if event_type == "WorkflowCompleted" else "FAIL"
            run_id = event.get("run_id", "") or payload.get("run_id", "")
            self._safe_write(f"\n{'='*72}\n")
            self._safe_write(f"  Workflow [{status_text}]: {run_id}\n")
            self._safe_write(f"{'='*72}\n")

            stage_summary = payload.get("stage_summary", [])
            if stage_summary:
                # 表头
                self._safe_write(
                    f"  {'Stage':<12} {'Agent':<16} {'Decision':<10} "
                    f"{'Duration':>9} {'Tokens(in+out)':>16}\n"
                )
                self._safe_write(f"  {'-'*68}\n")

                total_in = 0
                total_out = 0
                total_dur = 0.0
                for s in stage_summary:
                    state_name = s.get("state", "")
                    agent = s.get("agent", "") or ""
                    decision = s.get("decision", "")
                    dur = s.get("duration_seconds", 0) or 0
                    it = s.get("input_tokens", 0) or 0
                    ot = s.get("output_tokens", 0) or 0

                    total_in += it
                    total_out += ot
                    total_dur += dur

                    dur_str = _format_duration(dur)
                    token_str = f"{_format_tokens(it)}+{_format_tokens(ot)}"
                    self._safe_write(
                        f"  {state_name:<12} {agent:<16} {decision:<10} "
                        f"{dur_str:>9} {token_str:>16}\n"
                    )

                # 汇总行
                self._safe_write(f"  {'-'*68}\n")
                total_token_str = f"{_format_tokens(total_in)}+{_format_tokens(total_out)}"
                self._safe_write(
                    f"  {'TOTAL':<12} {'':<16} {'':<10} "
                    f"{_format_duration(total_dur):>9} {total_token_str:>16}\n"
                )
                self._safe_write(f"{'='*72}\n")
            return

        # ── 通用格式处理 ──
        fmt = self.FORMATS.get(event_type)
        if fmt is None:
            # 未知事件类型，输出基本信息
            self._safe_write(f"[{event_type}] {payload}\n")
            return

        # 构建格式化上下文
        ctx = dict(payload)
        ctx.update(event)

        try:
            line = fmt.format(**ctx)
        except KeyError:
            line = f"[{event_type}] {payload}"

        if not line:
            return

        # 状态计数前缀
        if event_type == "StateEntered":
            self._state_count += 1
            prefix = f"[{self._state_count}] "
        else:
            prefix = "  "

        self._safe_write(f"{prefix}{line}\n")

    def _safe_write(self, text: str):
        """安全写入，fallback 到 ASCII-only 以兼容 Windows 默认终端编码。"""
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            # Fallback: 替换不可编码字符
            safe = text.encode(self.stream.encoding or 'ascii', errors='replace').decode(
                self.stream.encoding or 'ascii', errors='replace'
            )
            try:
                self.stream.write(safe)
            except Exception:
                # 最终 fallback: 纯 ASCII
                ascii_safe = text.encode('ascii', errors='replace').decode('ascii')
                self.stream.write(ascii_safe)
        self.stream.flush()

    def flush(self):
        """刷新输出流。"""
        self.stream.flush()
