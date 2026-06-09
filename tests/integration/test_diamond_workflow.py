"""菱形 workflow 集成测试：step1 → step2/step3 → step4。"""

import os
import tempfile

from agent_workflow.long_task.workflow_run import WorkflowRun, RunStatus
from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.event_log import EventLog
from agent_workflow.long_task.state_store import StateStore
from agent_workflow.long_task.queue_runner import QueueRunner


class TestDiamondWorkflow:
    """菱形依赖执行测试：step1 → step2/step3 → step4。"""

    def test_diamond_all_success(self):
        """菱形依赖全部成功执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_diamond", name="菱形测试")
            items = [
                WorkItem(id="s1", title="step1"),
                WorkItem(id="s2", title="step2", depends_on=["s1"]),
                WorkItem(id="s3", title="step3", depends_on=["s1"]),
                WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            execution_order = []

            def handler(item: WorkItem):
                execution_order.append(item.id)
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}.md"

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            # 1. s1 必须先执行，s4 最后执行
            assert execution_order[0] == "s1"
            assert execution_order[-1] == "s4"

            # 2. s2 和 s3 在 s1 之后、s4 之前执行
            s1_idx = execution_order.index("s1")
            s4_idx = execution_order.index("s4")
            s2_idx = execution_order.index("s2")
            s3_idx = execution_order.index("s3")

            assert s2_idx > s1_idx and s2_idx < s4_idx
            assert s3_idx > s1_idx and s3_idx < s4_idx

            # 3. 全部完成
            assert all(item.status == ItemStatus.COMPLETED for item in items)
            assert run.status == RunStatus.COMPLETED

            # 4. event log 完整
            events = event_log.read_all()
            event_types = [e.event_type for e in events]
            assert event_types.count("ITEM_STARTED") == 4
            assert event_types.count("ITEM_COMPLETED") == 4

    def test_diamond_fifo_selection(self):
        """菱形依赖中 s2 和 s3 都就绪时 FIFO 选第一个。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_diamond", name="FIFO测试")
            # s2 在列表中先于 s3
            items = [
                WorkItem(id="s1", title="step1"),
                WorkItem(id="s2", title="step2", depends_on=["s1"]),
                WorkItem(id="s3", title="step3", depends_on=["s1"]),
                WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            execution_order = []

            def handler(item: WorkItem):
                execution_order.append(item.id)
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            # s2 在 s3 之前执行（列表顺序决定 FIFO 优先级）
            s2_idx = execution_order.index("s2")
            s3_idx = execution_order.index("s3")
            assert s2_idx < s3_idx

    def test_diamond_s3_fails(self):
        """菱形依赖中 s3 失败 → s4 不执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_diamond_fail", name="菱形失败测试")
            items = [
                WorkItem(id="s1", title="step1"),
                WorkItem(id="s2", title="step2", depends_on=["s1"]),
                WorkItem(id="s3", title="step3", depends_on=["s1"]),
                WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            def handler(item: WorkItem):
                if item.id == "s3":
                    raise RuntimeError("s3 失败")
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            # s1, s2 应完成；s3 失败；s4 保持 PENDING
            assert items[0].status == ItemStatus.COMPLETED  # s1
            assert items[1].status == ItemStatus.COMPLETED  # s2
            assert items[2].status == ItemStatus.FAILED     # s3
            assert items[3].status == ItemStatus.PENDING    # s4
            assert run.status == RunStatus.FAILED

    def test_diamond_state_after_run(self):
        """菱形依赖执行后 state 正确记录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_diamond", name="菱形状态测试")
            items = [
                WorkItem(id="s1", title="step1"),
                WorkItem(id="s2", title="step2", depends_on=["s1"]),
                WorkItem(id="s3", title="step3", depends_on=["s1"]),
                WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
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

            state = state_store.load()
            assert state["completed_items"] == ["s1", "s2", "s3", "s4"]
            for item_id in ["s1", "s2", "s3", "s4"]:
                assert state["items"][item_id]["status"] == "COMPLETED"
                assert state["items"][item_id]["artifact_path"] == f"output/{item_id}.md"
