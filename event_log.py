"""WorkflowEvent + EventLog — JSONL 事件日志的追加与读取。

MVP 核心对象之一。Event Log 是唯一的过程真相源。
JSONL 行顺序即 replay 顺序。

MVP 事件类型（仅 7 种，不预设更多）：
  WORKFLOW_CREATED   — workflow 创建
  WORK_ITEM_CREATED  — work item 创建
  ITEM_STARTED       — item 开始执行
  ITEM_COMPLETED     — item 执行成功
  ITEM_FAILED        — item 执行失败
  WORKFLOW_PAUSED    — workflow 暂停（MVP 仅记录事件，不实现 pause API）
  WORKFLOW_RESUMED   — workflow 恢复（MVP 仅记录事件，不实现 resume API）
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta


# Asia/Shanghai 时区（UTC+8）
TZ_SHANGHAI = timezone(timedelta(hours=8))

# 合法的 MVP 事件类型
VALID_EVENT_TYPES = frozenset({
    "WORKFLOW_CREATED",
    "WORK_ITEM_CREATED",
    "ITEM_STARTED",
    "ITEM_COMPLETED",
    "ITEM_FAILED",
    "WORKFLOW_PAUSED",
    "WORKFLOW_RESUMED",
})


@dataclass
class WorkflowEvent:
    """工作流事件 — Event Log 中的一条记录。

    Attributes:
        event_type: 事件类型，必须是 VALID_EVENT_TYPES 之一
        workflow_id: 所属 workflow 的 id
        item_id: 关联的 work item id，workflow 级事件为 None
        payload: JSON-serializable 的附加数据
        created_at: 事件创建时间（Asia/Shanghai timezone-aware）
    """

    event_type: str
    workflow_id: str
    item_id: str | None
    payload: dict
    created_at: datetime


def _now() -> datetime:
    """返回当前 Asia/Shanghai 时间（timezone-aware）。"""
    return datetime.now(tz=TZ_SHANGHAI)


def _serialize_event(event: WorkflowEvent) -> dict:
    """将 WorkflowEvent 序列化为 JSON 字典。"""
    d = asdict(event)
    # created_at 使用 ISO 8601 格式
    if isinstance(event.created_at, datetime):
        d["created_at"] = event.created_at.isoformat()
    return d


def _deserialize_event(d: dict) -> WorkflowEvent:
    """从 JSON 字典反序列化为 WorkflowEvent。"""
    created_at_str = d.get("created_at", "")
    if created_at_str:
        # 支持带时区和不带时区的 ISO 格式
        created_at = datetime.fromisoformat(created_at_str)
        # naive datetime 视为 Asia/Shanghai
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=TZ_SHANGHAI)
    else:
        created_at = _now()
    return WorkflowEvent(
        event_type=d["event_type"],
        workflow_id=d["workflow_id"],
        item_id=d.get("item_id"),
        payload=d.get("payload", {}),
        created_at=created_at,
    )


class EventLog:
    """JSONL 格式的事件日志。

    职责：
    - 追加 WorkflowEvent 到 JSONL 文件
    - 读取全部或按 workflow_id 过滤的事件
    - 不负责事件验证（由调用方在 append 前检查）

    JSONL 行顺序即 replay 顺序。每行一条完整 event JSON。

    每次 append 都打开/写入/关闭文件，避免 Windows 文件锁定问题。
    """

    def __init__(self, path: str):
        """初始化 EventLog。

        Args:
            path: JSONL 文件路径。目录不存在时自动创建。
        """
        self.path = path
        # 确保目录存在
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

    def append(self, event: WorkflowEvent) -> None:
        """追加一条事件到 JSONL 文件。

        每次写入打开/写入/关闭文件，避免长时间持有文件句柄。

        Args:
            event: 要追加的 WorkflowEvent
        """
        d = _serialize_event(event)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def read_all(self) -> list[WorkflowEvent]:
        """读取全部事件。

        Returns:
            按写入顺序排列的 WorkflowEvent 列表。文件不存在时返回空列表。
        """
        if not os.path.exists(self.path):
            return []
        events: list[WorkflowEvent] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                events.append(_deserialize_event(d))
        return events

    def read_by_workflow(self, workflow_id: str) -> list[WorkflowEvent]:
        """按 workflow_id 过滤事件。

        Args:
            workflow_id: 要过滤的 workflow id

        Returns:
            匹配 workflow_id 的事件列表
        """
        all_events = self.read_all()
        return [e for e in all_events if e.workflow_id == workflow_id]

    def close(self) -> None:
        """关闭事件日志（兼容接口，当前为 no-op）。

        由于每次 append 都独立打开/关闭文件，不需要持久句柄。
        保留此方法是为了与 QueueRunner 的接口兼容。
        """
