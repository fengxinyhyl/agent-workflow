"""StateStore — workflow_state.json 的原子读写。

支撑模块（非核心对象）。workflow_state.json 是恢复锚点；Event Log 是过程真相源。

若二者冲突（状态不一致），QueueRunner 应在启动时调用 check_consistency() 检测并报告，
不自动修复。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from agent_workflow.long_task.workflow_run import WorkflowRun, RunStatus
from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.event_log import WorkflowEvent


class StateStore:
    """workflow_state.json 的持久化存储。

    职责：
    - save: 原子写入（先写临时文件再 rename）
    - load: 读取并返回 dict

    workflow_state.json 最小结构：
    {
      "workflow_id": "...",
      "name": "...",
      "status": "RUNNING",
      "paused": false,
      "completed_items": ["step1"],
      "failed_items": [],
      "items": {
        "step1": {
          "title": "数据分析",
          "status": "COMPLETED",
          "depends_on": [],
          "artifact_path": "output/step1_report.md"
        }
      }
    }
    """

    def __init__(self, path: str):
        """初始化 StateStore。

        Args:
            path: workflow_state.json 文件路径。目录不存在时自动创建。
        """
        self.path = path
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

    def save(self, workflow_run: WorkflowRun, items: list[WorkItem], paused: bool = False) -> None:
        """原子写入 workflow_state.json。

        先写入同目录下的临时文件，然后 rename 到目标路径，
        确保外部观察者不会读到不完整的文件（Windows 上 rename 是原子的，因为是同目录）。

        Args:
            workflow_run: 当前 WorkflowRun 实例
            items: 当前所有 WorkItem 列表
            paused: 是否处于暂停状态
        """
        data = {
            "workflow_id": workflow_run.id,
            "name": workflow_run.name,
            "status": workflow_run.status.value,
            "paused": paused,
            "completed_items": [
                item.id for item in items if item.status == ItemStatus.COMPLETED
            ],
            "failed_items": [
                item.id for item in items if item.status == ItemStatus.FAILED
            ],
            "items": {},
        }
        for item in items:
            data["items"][item.id] = {
                "title": item.title,
                "status": item.status.value,
                "depends_on": item.depends_on,
                "artifact_path": item.artifact_path,
            }

        # 原子写入：先写临时文件，再 rename
        dirname = os.path.dirname(self.path) or "."
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp", prefix="workflow_state_", dir=dirname
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)  # Windows 上也保证原子
        except Exception:
            # 清理残留临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load(self) -> dict[str, Any]:
        """加载 workflow_state.json。

        Returns:
            状态字典。文件不存在时返回空 dict。
        """
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)


def check_consistency(
    state: dict[str, Any],
    events: list[WorkflowEvent],
) -> list[str]:
    """检查 workflow_state.json 与 Event Log 的一致性。

    最小一致性检查规则：
    1. workflow_id 必须匹配
    2. completed_items 中的每个 item 必须有对应的 ITEM_COMPLETED 事件
    3. failed_items 中的每个 item 必须有对应的 ITEM_FAILED 事件
    4. item 状态不能从 COMPLETED/FAILED 回退到 PENDING/RUNNING
    5. workflow 状态不能从 COMPLETED/FAILED 回退到 PENDING/RUNNING

    Args:
        state: StateStore.load() 返回的状态字典
        events: EventLog 中该 workflow 的全部事件

    Returns:
        不一致信息列表，空列表表示一致。
    """
    if not state:
        return []  # 无状态则无可检查

    errors: list[str] = []

    workflow_id = state.get("workflow_id", "")

    # 1. workflow_id 匹配
    for event in events:
        if event.workflow_id != workflow_id:
            errors.append(
                f"Event workflow_id '{event.workflow_id}' 与 state workflow_id "
                f"'{workflow_id}' 不匹配 (event_type={event.event_type})"
            )

    # 2. completed_items 必须有 ITEM_COMPLETED 事件
    completed_ids = set(state.get("completed_items", []))
    completed_event_ids = {
        e.item_id for e in events if e.event_type == "ITEM_COMPLETED"
    }
    for item_id in completed_ids:
        if item_id not in completed_event_ids:
            errors.append(
                f"State 中 item '{item_id}' 标记为 completed，但未找到对应的 "
                f"ITEM_COMPLETED 事件"
            )

    # 3. failed_items 必须有 ITEM_FAILED 事件
    failed_ids = set(state.get("failed_items", []))
    failed_event_ids = {
        e.item_id for e in events if e.event_type == "ITEM_FAILED"
    }
    for item_id in failed_ids:
        if item_id not in failed_event_ids:
            errors.append(
                f"State 中 item '{item_id}' 标记为 failed，但未找到对应的 "
                f"ITEM_FAILED 事件"
            )

    # 4. 检查 item 状态从 Event Log 推导的一致性
    #    构建每个 item 的终态事件序列
    item_terminal: dict[str, tuple[str, int]] = {}  # item_id -> (event_type, index)
    for i, event in enumerate(events):
        if event.item_id and event.event_type in ("ITEM_COMPLETED", "ITEM_FAILED"):
            item_terminal[event.item_id] = (event.event_type, i)

    for item_id, (terminal_type, _) in item_terminal.items():
        # 如果 state 中有这个 item，检查状态是否与事件终态一致
        items_data = state.get("items", {})
        if item_id in items_data:
            state_status = items_data[item_id].get("status", "")
            if terminal_type == "ITEM_COMPLETED" and state_status not in (
                "COMPLETED",
                "SKIPPED",
            ):
                errors.append(
                    f"State 中 item '{item_id}' status={state_status}，"
                    f"但 Event Log 中有 ITEM_COMPLETED 事件"
                )
            if terminal_type == "ITEM_FAILED" and state_status != "FAILED":
                errors.append(
                    f"State 中 item '{item_id}' status={state_status}，"
                    f"但 Event Log 中有 ITEM_FAILED 事件"
                )

    # 5. workflow 状态不能从终止态回退
    state_status = state.get("status", "")
    terminal_workflow_events = {
        e.event_type for e in events
        if e.event_type in ("WORKFLOW_CREATED",)
    }

    return errors
