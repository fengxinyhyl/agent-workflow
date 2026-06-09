"""QueueRunner 单元测试。"""

import os
import tempfile
import pytest

from agent_workflow.long_task.workflow_run import WorkflowRun, RunStatus
from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.event_log import EventLog
from agent_workflow.long_task.state_store import StateStore
from agent_workflow.long_task.queue_runner import QueueRunner


class TestQueueRunner:
    """QueueRunner 单元测试。"""

    def _make_env(self, tmpdir: str):
        """创建测试环境。"""
        run = WorkflowRun(id="wf_test", name="测试工作流")
        items = [
            WorkItem(id="step1", title="数据分析"),
            WorkItem(id="step2", title="因子构建", depends_on=["step1"]),
            WorkItem(id="step3", title="回测", depends_on=["step2"]),
        ]
        event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
        state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))
        return run, items, event_log, state_store

    def test_run_chain_success(self):
        """链式执行全部成功。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run, items, event_log, state_store = self._make_env(tmpdir)

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}_report.md"

            runner = QueueRunner(
                workflow_run=run,
                items=items,
                handler=handler,
                event_log=event_log,
                state_store=state_store,
            )
            runner.run()

            # 所有 item 应完成
            assert all(item.status == ItemStatus.COMPLETED for item in items)
            assert run.status == RunStatus.COMPLETED

            # 检查事件
            events = event_log.read_by_workflow("wf_test")
            event_types = [e.event_type for e in events]
            assert "WORKFLOW_CREATED" in event_types
            assert "ITEM_STARTED" in event_types
            assert "ITEM_COMPLETED" in event_types
            # 每个 item 都有 STARTED 和 COMPLETED
            assert event_types.count("ITEM_STARTED") == 3
            assert event_types.count("ITEM_COMPLETED") == 3

    def test_run_fail_fast(self):
        """step2 失败时 step3 不应执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run, items, event_log, state_store = self._make_env(tmpdir)

            def handler(item: WorkItem):
                if item.id == "step2":
                    raise RuntimeError("step2 执行失败")
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}_report.md"

            runner = QueueRunner(
                workflow_run=run,
                items=items,
                handler=handler,
                event_log=event_log,
                state_store=state_store,
            )
            runner.run()

            # step1 应完成，step2 失败，step3 保持 PENDING
            assert items[0].status == ItemStatus.COMPLETED  # step1
            assert items[1].status == ItemStatus.FAILED     # step2
            assert items[2].status == ItemStatus.PENDING    # step3
            assert run.status == RunStatus.FAILED

            # 检查事件
            events = event_log.read_by_workflow("wf_test")
            event_types = [e.event_type for e in events]
            assert "ITEM_FAILED" in event_types
            # step3 不应有 ITEM_STARTED
            step3_started = [
                e for e in events
                if e.event_type == "ITEM_STARTED" and e.item_id == "step3"
            ]
            assert len(step3_started) == 0

    def test_run_no_items(self):
        """空 item 列表应正常完成。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_test", name="空工作流")
            items: list[WorkItem] = []
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()
            assert run.status == RunStatus.COMPLETED

    def test_run_fifo_order(self):
        """ready 的 item 应按 FIFO 顺序执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_test", name="FIFO测试")
            items = [
                WorkItem(id="a", title="A"),
                WorkItem(id="b", title="B"),
                WorkItem(id="c", title="C"),
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

            # FIFO 顺序应与传入顺序一致（all PENDING, no deps）
            assert execution_order == ["a", "b", "c"]

    def test_run_handler_auto_complete(self):
        """handler 未设置 status 时 runner 应自动设为 COMPLETED。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_test", name="测试")
            items = [WorkItem(id="step1", title="数据分析")]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            def handler(item: WorkItem):
                # 忘记设置 status，但设置了 artifact_path
                item.artifact_path = "output/report.md"

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            assert items[0].status == ItemStatus.COMPLETED

    def test_constructor_naming_no_collision(self):
        """验证构造函数参数名 workflow_run 不与方法 run() 冲突。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_test", name="测试")
            items = [WorkItem(id="step1", title="step1")]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            # workflow_run 属性应指向 WorkflowRun，run() 方法应可调用
            assert isinstance(runner.workflow_run, WorkflowRun)
            assert callable(runner.run)
            runner.run()
            assert runner.workflow_run.status == RunStatus.COMPLETED
