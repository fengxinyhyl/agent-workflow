"""WorkflowRun — 一个完整长任务的运行实例。

MVP 核心对象之一。纯 dataclass，状态流转由 QueueRunner 驱动。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunStatus(str, Enum):
    """WorkflowRun 的状态枚举。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class WorkflowRun:
    """一个完整的长任务运行实例。

    Attributes:
        id: 唯一标识，如 "workflow_agent_workflow_mvp_20260608"
        name: 人类可读名称，如 "研究 AI 抱团现象"
        status: 当前状态，默认 PENDING
    """

    id: str
    name: str
    status: RunStatus = RunStatus.PENDING
