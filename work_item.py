"""WorkItem — 长任务中的一个步骤。

MVP 核心对象之一。纯 dataclass，不引入 AgentTask。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ItemStatus(str, Enum):
    """WorkItem 的状态枚举。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class WorkItem:
    """长任务中的一个步骤。

    Attributes:
        id: 唯一标识，如 "step1"
        title: 人类可读描述，如 "数据分析"
        status: 当前状态，默认 PENDING
        depends_on: 依赖的 item id 列表，空列表表示无依赖
        artifact_path: 完成后的产物路径（markdown sidecar 或输出文件）
    """

    id: str
    title: str
    status: ItemStatus = ItemStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    artifact_path: str | None = None

    def __post_init__(self):
        """确保 depends_on 是独立副本，不受外部修改影响。"""
        self.depends_on = list(self.depends_on)
