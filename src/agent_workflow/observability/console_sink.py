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


# ── Box-drawing 字符集 ──
_BOX = {
    "h": "─", "v": "│",
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "tm": "┬", "bm": "┴", "ml": "├", "mr": "┤", "mm": "┼",
}


def _box_top(widths: list[int]) -> str:
    """┌────┬────┬────┐"""
    parts = [_BOX["tl"]]
    for i, w in enumerate(widths):
        parts.append(_BOX["h"] * (w + 2))
        parts.append(_BOX["tm"] if i < len(widths) - 1 else _BOX["tr"])
    return "".join(parts)


def _box_mid(widths: list[int]) -> str:
    """├────┼────┼────┤"""
    parts = [_BOX["ml"]]
    for i, w in enumerate(widths):
        parts.append(_BOX["h"] * (w + 2))
        parts.append(_BOX["mm"] if i < len(widths) - 1 else _BOX["mr"])
    return "".join(parts)


def _box_bottom(widths: list[int]) -> str:
    """└────┴────┴────┘"""
    parts = [_BOX["bl"]]
    for i, w in enumerate(widths):
        parts.append(_BOX["h"] * (w + 2))
        parts.append(_BOX["bm"] if i < len(widths) - 1 else _BOX["br"])
    return "".join(parts)


def _box_row(cells: list[str], widths: list[int], aligns: list[str] | None = None) -> str:
    """│ cell1 │ cell2 │ cell3 │"""
    if aligns is None:
        aligns = ["<"] * len(widths)
    parts = [_BOX["v"]]
    for cell, w, a in zip(cells, widths, aligns):
        # 用 format 对齐；CJK 字符暂不做宽度补偿（当前内容全为 ASCII）
        fmt = f"{{:{a}{w}}}"
        parts.append(f" {fmt.format(cell)} ")
        parts.append(_BOX["v"])
    return "".join(parts)


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
                # 列定义：名称、宽度、对齐方式
                cols = [
                    ("Stage", 12, "<"),
                    ("Agent", 18, "<"),
                    ("Decision", 10, "<"),
                    ("Duration", 10, ">"),
                    ("Tokens(in+out)", 18, ">"),
                ]
                headers = [c[0] for c in cols]
                widths = [c[1] for c in cols]
                aligns = [c[2] for c in cols]

                self._safe_write(f"  {_box_top(widths)}\n")
                self._safe_write(f"  {_box_row(headers, widths, aligns)}\n")
                self._safe_write(f"  {_box_mid(widths)}\n")

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
                    cells = [state_name, agent, decision, dur_str, token_str]
                    self._safe_write(f"  {_box_row(cells, widths, aligns)}\n")

                # 汇总行（合并前 3 列）
                self._safe_write(f"  {_box_mid(widths)}\n")
                total_token_str = f"{_format_tokens(total_in)}+{_format_tokens(total_out)}"
                merged_width = widths[0] + widths[1] + widths[2] + 6  # 6 = 3 个 │ 两边的空格
                footer_left = f"TOTAL"
                footer_dur = _format_duration(total_dur)
                self._safe_write(
                    f"  {_BOX['v']} {footer_left:<{merged_width}} "
                    f"{_BOX['v']} {footer_dur:>{widths[3]}} "
                    f"{_BOX['v']} {total_token_str:>{widths[4]}} {_BOX['v']}\n"
                )
                self._safe_write(f"  {_box_bottom(widths)}\n")
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
