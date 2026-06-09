"""恢复测试：从持久化 workflow_state.json + Event Log 恢复继续执行。"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
import pytest

from agent_workflow.long_task.workflow_run import WorkflowRun, RunStatus
from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.event_log import EventLog, WorkflowEvent, TZ_SHANGHAI
from agent_workflow.long_task.state_store import StateStore, check_consistency
from agent_workflow.long_task.queue_runner import QueueRunner


class TestRecovery:
    """从持久化 state 恢复测试。"""

    def test_recover_from_persisted_state(self):
        """从 workflow_state.json + Event Log 恢复并继续执行。"""
        tmp = tempfile.mkdtemp()
        try:
            state_path = os.path.join(tmp, "workflow_state.json")
            event_path = os.path.join(tmp, "events.jsonl")

            # Phase 1: 模拟已完成 step1 的状态
            state_store = StateStore(state_path)
            event_log = EventLog(event_path)

            run = WorkflowRun(id="recovery_test", name="恢复测试")
            items = [
                WorkItem(id="step1", title="第一步"),
                WorkItem(id="step2", title="第二步", depends_on=["step1"]),
            ]

            # 手动设置状态：step1 已完成
            items[0].status = ItemStatus.COMPLETED
            items[0].artifact_path = os.path.join(tmp, "step1_output.md")
            run.status = RunStatus.RUNNING

            # 写入持久化 state
            state_store.save(run, items)
            # 写入事件
            event_log.append(WorkflowEvent(
                event_type="WORKFLOW_CREATED",
                workflow_id="recovery_test",
                item_id=None,
                payload={"name": "恢复测试"},
                created_at=datetime.fromisoformat("2026-06-09T00:00:00+08:00"),
            ))
            event_log.append(WorkflowEvent(
                event_type="ITEM_COMPLETED",
                workflow_id="recovery_test",
                item_id="step1",
                payload={"artifact_path": items[0].artifact_path},
                created_at=datetime.fromisoformat("2026-06-09T00:01:00+08:00"),
            ))

            # Phase 2: 恢复执行
            restored_state = state_store.load()
            restored_events = event_log.read_by_workflow("recovery_test")

            # 验证一致性
            issues = check_consistency(restored_state, restored_events)
            assert len(issues) == 0, f"一致性检查失败: {issues}"

            # 验证恢复的 state
            assert restored_state["workflow_id"] == "recovery_test"
            assert "step1" in restored_state["completed_items"]
            assert restored_state["items"]["step1"]["status"] == "COMPLETED"

            # Phase 3: 继续执行 step2
            restored_run = WorkflowRun(id="recovery_test", name="恢复测试", status=RunStatus.RUNNING)
            restored_items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED,
                         artifact_path=items[0].artifact_path),
                WorkItem(id="step2", title="第二步", depends_on=["step1"],
                         status=ItemStatus.PENDING),
            ]

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED
                item.artifact_path = os.path.join(tmp, f"{item.id}_output.md")

            runner = QueueRunner(
                workflow_run=restored_run,
                items=restored_items,
                handler=handler,
                event_log=event_log,
                state_store=state_store,
            )
            runner.run()

            # 验证 step2 也完成了
            assert restored_run.status == RunStatus.COMPLETED
            assert restored_items[1].status == ItemStatus.COMPLETED
            assert restored_items[1].artifact_path

        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_recovery_detects_inconsistency(self):
        """检测 state 与 event log 不一致。"""
        tmp = tempfile.mkdtemp()
        try:
            state_path = os.path.join(tmp, "workflow_state.json")
            event_path = os.path.join(tmp, "events.jsonl")

            state_store = StateStore(state_path)
            event_log = EventLog(event_path)

            run = WorkflowRun(id="inconsistency_test", name="不一致测试")
            items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED),
            ]

            # 写入 state（step1 completed），但不写对应事件
            state_store.save(run, items)

            # 检测不一致
            issues = check_consistency(state_store.load(), [])
            assert len(issues) > 0  # step1 completed 但没有 ITEM_COMPLETED 事件
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_recovery_consistency_prevents_run(self):
        """不一致状态阻止执行。"""
        tmp = tempfile.mkdtemp()
        try:
            state_path = os.path.join(tmp, "workflow_state.json")
            event_path = os.path.join(tmp, "events.jsonl")

            state_store = StateStore(state_path)
            event_log = EventLog(event_path)

            run = WorkflowRun(id="block_test", name="阻止执行测试")
            items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED),
                WorkItem(id="step2", title="第二步", depends_on=["step1"]),
            ]

            # 写入不一致 state（step1 completed 但没有事件）
            state_store.save(run, items)

            # 构造不同的 workflow_id 事件
            event_log.append(WorkflowEvent(
                event_type="WORKFLOW_CREATED",
                workflow_id="other_workflow",
                item_id=None,
                payload={},
                created_at=datetime.fromisoformat("2026-06-09T00:00:00+08:00"),
            ))

            restored_run = WorkflowRun(id="block_test", name="阻止执行测试")
            restored_items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED),
                WorkItem(id="step2", title="第二步", depends_on=["step1"]),
            ]

            runner = QueueRunner(
                workflow_run=restored_run,
                items=restored_items,
                handler=lambda _: None,
                event_log=event_log,
                state_store=state_store,
            )

            # 应该抛出 ValueError（因为 step1 completed 但没有对应事件）
            with pytest.raises(ValueError, match="不一致"):
                runner.run()
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
