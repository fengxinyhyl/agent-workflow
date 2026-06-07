"""EventBus — 统一事件总线。

支持多 sink 同时输出（ConsoleSink + JSONLSink）。
所有关键事件通过 EventBus 统一分发，确保：
- 实时感知（ConsoleSink）
- 事后排查（JSONLSink）
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from .events import EventType


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


class EventBus:
    """统一事件总线。

    支持多 sink 注册，emit 时广播到所有注册的 sink。

    用法:
        bus = EventBus()
        bus.add_sink(ConsoleSink())
        bus.add_sink(JSONLSink("events.jsonl"))
        bus.emit(EventType.StateEntered, {"state": "codex_plan", "timestamp": ...})
    """

    def __init__(self):
        self._sinks: list[Callable[[str, dict[str, Any]], None]] = []
        self._lock = threading.Lock()
        self._event_count = 0

    def add_sink(self, sink: Any):
        """添加一个 sink（callable 或有 write 方法的对象）。"""
        if callable(sink):
            self._sinks.append(sink)
        elif hasattr(sink, "write"):
            self._sinks.append(sink.write)

    def remove_sink(self, sink: Any):
        """移除一个 sink。"""
        with self._lock:
            if callable(sink):
                self._sinks = [s for s in self._sinks if s is not sink]
            elif hasattr(sink, "write"):
                self._sinks = [s for s in self._sinks if s is not sink.write]

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        **kwargs,
    ):
        """发射一个事件到所有 sink。

        参数:
          event_type: 事件类型（如 "StateEntered"）
          payload: 事件 payload 字典
          **kwargs: 额外字段（会合并到标准事件结构）
        """
        if payload is None:
            payload = {}

        # 构建标准事件
        event = {
            "event": event_type,
            "timestamp": payload.pop("timestamp", _now_iso()),
            "payload": payload,
        }

        # 从 payload 提取标准字段
        for key in ("run_id", "state", "task"):
            if key in payload:
                event[key] = payload.pop(key)

        # 合并 kwargs
        event["payload"].update(kwargs)

        self._event_count += 1

        # 广播到所有 sink
        with self._lock:
            for sink in self._sinks:
                try:
                    sink(event_type, event)
                except Exception:
                    # Sink 错误不影响主流程
                    pass

    @property
    def event_count(self) -> int:
        return self._event_count

    def flush(self):
        """刷新所有 sink（如果有 flush 方法）。"""
        with self._lock:
            for sink in self._sinks:
                if hasattr(sink, "flush"):
                    try:
                        sink.flush()
                    except Exception:
                        pass
