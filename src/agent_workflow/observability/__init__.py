"""observability 模块 — 可观测性 P0 核心。

提供:
- EventBus: 统一事件总线
- ConsoleSink: 实时终端输出
- JSONLSink: 事件事实表日志
- Heartbeat: 心跳机制
- status: 运行状态查询
- explain: 状态解释
"""

from .events import (
    EventType,
    event_registry,
    ALL_EVENTS,
)
from .event_bus import EventBus
from .console_sink import ConsoleSink
from .jsonl_sink import JSONLSink, read_log, read_tail
from .heartbeat import HeartbeatEmitter
from .status import get_status
from .explain import get_explanation

__all__ = [
    "EventType",
    "event_registry",
    "ALL_EVENTS",
    "EventBus",
    "ConsoleSink",
    "JSONLSink",
    "read_log",
    "read_tail",
    "HeartbeatEmitter",
    "get_status",
    "get_explanation",
]
