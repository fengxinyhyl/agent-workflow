"""Agent Workflow Core — 通用 Agent 编排引擎。

调度多个 AI Agent 按预定义工作流协作，支持：
- 长任务运行（8h/12h/24h）
- 可观测性（EventBus + Heartbeat + status + explain）
- 产物流管理（staging → validation → promotion）
- Guard 机制（max_visits / max_duration_minutes / max_retries）
"""

__version__ = "0.1.0"
