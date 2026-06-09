"""链式 workflow 集成测试：step1 → step2 → step3。"""

import os
import tempfile

from agent_workflow.long_task.workflow_run import WorkflowRun, RunStatus
from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.event_log import EventLog
from agent_workflow.long_task.state_store import StateStore
from agent_workflow.long_task.queue_runner import QueueRunner


class TestChainWorkflow:
    """step1 → step2 → step3 链式执行全流程。"""

    def test_chain_execution_order(self):
        """验证执行顺序与 event log 完整性。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_chain", name="链式测试")
            items = [
                WorkItem(id="step1", title="数据分析"),
                WorkItem(id="step2", title="因子构建", depends_on=["step1"]),
                WorkItem(id="step3", title="回测验证", depends_on=["step2"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            execution_order = []

            def handler(item: WorkItem):
                execution_order.append(item.id)
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}_report.md"

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            # 1. 执行顺序
            assert execution_order == ["step1", "step2", "step3"]

            # 2. 所有 item 完成
            assert all(item.status == ItemStatus.COMPLETED for item in items)
            assert run.status == RunStatus.COMPLETED

            # 3. event log 完整
            events = event_log.read_all()
            event_types = [e.event_type for e in events]

            # WORKFLOW_CREATED, 3 × WORK_ITEM_CREATED, 3 × ITEM_STARTED, 3 × ITEM_COMPLETED
            assert event_types.count("WORK_ITEM_CREATED") == 3
            assert event_types.count("ITEM_STARTED") == 3
            assert event_types.count("ITEM_COMPLETED") == 3

            # 4. 事件顺序：step1_S, step1_C, step2_S, step2_C, step3_S, step3_C
            item_events = [
                (e.event_type, e.item_id)
                for e in events
                if e.event_type in ("ITEM_STARTED", "ITEM_COMPLETED")
            ]
            assert item_events == [
                ("ITEM_STARTED", "step1"),
                ("ITEM_COMPLETED", "step1"),
                ("ITEM_STARTED", "step2"),
                ("ITEM_COMPLETED", "step2"),
                ("ITEM_STARTED", "step3"),
                ("ITEM_COMPLETED", "step3"),
            ]

            # 5. artifact_path 已记录
            assert items[0].artifact_path == "output/step1_report.md"
            assert items[1].artifact_path == "output/step2_report.md"
            assert items[2].artifact_path == "output/step3_report.md"

    def test_chain_state_persistence(self):
        """验证每次 item 完成后 state 都写入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_chain", name="持久化测试")
            items = [
                WorkItem(id="s1", title="step1"),
                WorkItem(id="s2", title="step2", depends_on=["s1"]),
                WorkItem(id="s3", title="step3", depends_on=["s2"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}.md"

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            # 最终 state 应记录所有 item 为完成
            state = state_store.load()
            assert state["status"] == "COMPLETED"
            assert state["completed_items"] == ["s1", "s2", "s3"]
            assert state["items"]["s1"]["status"] == "COMPLETED"
            assert state["items"]["s2"]["status"] == "COMPLETED"
            assert state["items"]["s3"]["status"] == "COMPLETED"

            # 各 item 的 artifact_path 应持久化
            assert state["items"]["s1"]["artifact_path"] == "output/s1.md"
            assert state["items"]["s2"]["artifact_path"] == "output/s2.md"
            assert state["items"]["s3"]["artifact_path"] == "output/s3.md"
