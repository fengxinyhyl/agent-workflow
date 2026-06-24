"""JSONLSink — 事件事实表日志。

每个 event 写入一行 JSON 到 events.jsonl：
  {"timestamp": "...", "run_id": "...", "state": "...", "task": "...", "event": "...", "payload": {...}}

events.jsonl 是事件事实表，可用于事后排查、回放和分析。
"""

from __future__ import annotations

import json
import os
from typing import Any


class JSONLSink:
    """JSONL 持久化 sink。

    所有事件以 JSONL 格式写入 events.jsonl。
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._file = open(path, "a", encoding="utf-8")
        self._count = 0

    def write(self, event_type: str, event: dict[str, Any]):
        """写入一条事件到 JSONL 文件。"""
        # 构建标准 record
        record = {
            "event": event_type,
            "timestamp": event.get("timestamp", ""),
            "run_id": event.get("run_id", ""),
            "state": event.get("state", ""),
            "task": event.get("task", ""),
            "payload": event.get("payload", {}),
        }

        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._count += 1

        # 每 50 条事件 flush 一次
        if self._count % 50 == 0:
            self._file.flush()

    def flush(self):
        """刷新文件缓冲。"""
        self._file.flush()

    def close(self):
        """关闭文件。"""
        self._file.close()

    @property
    def count(self) -> int:
        return self._count


def read_log(run_id: str, summary: bool = False, run_root: str | None = None) -> list[dict[str, Any]] | str:
    """读取某次运行的事件日志。

    参数:
      run_id: 运行 ID
      summary: True 时返回摘要字符串而非事件列表
      run_root: 运行根目录（可选，默认从 .agent-workflow/runs/ 查找）
    """
    # 查找 events.jsonl
    if run_root is None:
        run_root = os.path.join("docs", "runs", run_id)
    log_path = os.path.join(run_root, "logs", "events.jsonl")

    if not os.path.exists(log_path):
        if summary:
            return f"未找到运行 {run_id} 的日志"
        return []

    events = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if summary:
        return _build_summary(run_id, events)

    return events


def _build_summary(run_id: str, events: list[dict[str, Any]]) -> str:
    """根据事件列表生成运行摘要。"""
    if not events:
        return f"Run {run_id}: 无事件记录"

    first_ts = events[0].get("timestamp", "")
    last_ts = events[-1].get("timestamp", "")
    total = len(events)

    # 统计事件类型
    event_counts = {}
    for e in events:
        et = e.get("event", "unknown")
        event_counts[et] = event_counts.get(et, 0) + 1

    # 提取状态序列
    states = []
    for e in events:
        if e.get("event") == "StateEntered":
            states.append(e.get("state", "?"))

    lines = [
        f"Run: {run_id}",
        f"Events: {total}",
        f"Duration: {first_ts} → {last_ts}",
        f"States: {' → '.join(states)}" if states else "States: (none)",
        "",
        "Event counts:",
    ]
    for et, count in sorted(event_counts.items()):
        lines.append(f"  {et}: {count}")

    return "\n".join(lines)


def read_tail(
    run_id: str,
    state: str | None = None,
    lines: int = 80,
    run_root: str | None = None,
) -> list[str]:
    """读取指定 state 的最近 N 条日志行。

    参数:
      run_id: 运行 ID
      state: 过滤指定 state 的事件（None = 不过滤）
      lines: 返回的行数
      run_root: 运行根目录（可选，默认从 .agent-workflow/runs/ 查找）
    """
    events = read_log(run_id, run_root=run_root)
    if isinstance(events, str):
        return [events]
    if not events:
        return [f"未找到运行 {run_id} 的日志"]

    # 过滤
    if state:
        events = [e for e in events if e.get("state") == state]

    # 取最近 N 条
    events = events[-lines:]

    # 格式化
    result = []
    for e in events:
        result.append(
            f"[{e.get('timestamp', '?')}] {e.get('event', '?')}"
            f"  state={e.get('state', '')}"
            f"  {json.dumps(e.get('payload', {}), ensure_ascii=False)}"
        )

    return result
