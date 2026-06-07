"""ConsoleSink — 实时终端输出。

提供工作流运行时的最小实时感知：
  [STATE] Enter: codex_plan
  [AGENT] Start: codex_plan
  [RESULT] decision=revise status=success duration=192s
  [TRANSITION] claude_review_plan -> codex_revise_plan

ConsoleSink 不替代 execution log，只提供实时感知。
"""

from __future__ import annotations

import sys
from typing import Any


class ConsoleSink:
    """控制台实时输出 sink。

    根据事件类型输出不同格式的行到 stderr（避免与 Agent 输出到 stdout 混在一起）。
    """

    # 事件类型 → 输出格式（使用 ASCII 标记以兼容 Windows 默认终端编码）
    FORMATS = {
        "WorkflowStarted": "[WORKFLOW] Start: {workflow_id} -> {goal}",
        "WorkflowCompleted": "[WORKFLOW] [OK] Completed: {run_id}",
        "WorkflowFailed": "[WORKFLOW] [FAIL] Failed: {error}",
        "WorkflowCancelled": "[WORKFLOW] [STOP] Cancelled: {reason}",
        "StateEntered": "[STATE] Enter: {state}",
        "TaskFinished": "[RESULT] decision={decision} status={status}",
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
    }

    def __init__(self, stream=None, show_heartbeat: bool = False):
        self.stream = stream or sys.stderr
        self.show_heartbeat = show_heartbeat
        self._state_count = 0

    def write(self, event_type: str, event: dict[str, Any]):
        """处理一个事件并输出到控制台。"""
        if event_type == "Heartbeat" and not self.show_heartbeat:
            return

        fmt = self.FORMATS.get(event_type)
        if fmt is None:
            # 未知事件类型，输出基本信息
            payload = event.get("payload", {})
            self._safe_write(f"[{event_type}] {payload}\n")
            return

        # 构建格式化上下文
        payload = event.get("payload", {})
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
