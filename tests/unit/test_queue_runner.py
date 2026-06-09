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


class TestQueueRunnerHydrate:
    """QueueRunner.hydrate() 单元测试 — 自动 hydration 能力验证。"""

    def _make_env(self, tmpdir: str, workflow_id: str = "wf_test"):
        """创建测试环境。"""
        event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
        state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))
        return event_log, state_store

    def test_hydrate_from_fresh_state(self):
        """从刚保存的完整 state 恢复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log, state_store = self._make_env(tmpdir)

            # Phase 1: 创建完整 state
            run = WorkflowRun(id="wf_fresh", name="新鲜状态")
            items = [
                WorkItem(id="step1", title="数据分析"),
                WorkItem(id="step2", title="因子构建", depends_on=["step1"]),
                WorkItem(id="step3", title="回测", depends_on=["step2"]),
            ]
            state_store.save(run, items)

            # Phase 2: hydrate
            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}_report.md"

            hydrated = QueueRunner.hydrate(state_store, event_log, handler)

            # 验证恢复正确
            assert hydrated.workflow_run.id == "wf_fresh"
            assert hydrated.workflow_run.name == "新鲜状态"
            assert hydrated.workflow_run.status == RunStatus.PENDING
            assert len(hydrated.items) == 3
            assert hydrated.items[0].status == ItemStatus.PENDING
            assert hydrated.items[1].depends_on == ["step1"]

            # 可以正常执行
            hydrated.run()
            assert hydrated.workflow_run.status == RunStatus.COMPLETED
            assert all(item.status == ItemStatus.COMPLETED for item in hydrated.items)

    def test_hydrate_from_mid_run_state(self):
        """从部分完成的 state 恢复并继续执行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log, state_store = self._make_env(tmpdir, "wf_mid")

            # Phase 1: 模拟 step1 已完成
            run = WorkflowRun(id="wf_mid", name="中途恢复", status=RunStatus.RUNNING)
            items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED,
                         artifact_path="output/step1.md"),
                WorkItem(id="step2", title="第二步", depends_on=["step1"]),
            ]
            state_store.save(run, items)
            # 写入对应事件
            from agent_workflow.long_task.event_log import WorkflowEvent
            from datetime import datetime, timezone, timedelta
            tz = timezone(timedelta(hours=8))
            event_log.append(WorkflowEvent(
                event_type="WORKFLOW_CREATED", workflow_id="wf_mid",
                item_id=None, payload={"name": "中途恢复"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="WORK_ITEM_CREATED", workflow_id="wf_mid",
                item_id="step1", payload={"title": "第一步"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="WORK_ITEM_CREATED", workflow_id="wf_mid",
                item_id="step2", payload={"title": "第二步"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="ITEM_COMPLETED", workflow_id="wf_mid",
                item_id="step1", payload={"artifact_path": "output/step1.md"},
                created_at=datetime.now(tz),
            ))

            # Phase 2: hydrate 并继续执行
            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            hydrated = QueueRunner.hydrate(state_store, event_log, handler)

            assert hydrated.workflow_run.status == RunStatus.RUNNING
            assert hydrated.items[0].status == ItemStatus.COMPLETED
            assert hydrated.items[1].status == ItemStatus.PENDING

            # 继续执行 step2
            hydrated.run()
            assert hydrated.workflow_run.status == RunStatus.COMPLETED
            assert hydrated.items[1].status == ItemStatus.COMPLETED

    def test_hydrate_from_paused_state(self):
        """从 paused state 恢复，应保持暂停标记。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log, state_store = self._make_env(tmpdir, "wf_paused")

            run = WorkflowRun(id="wf_paused", name="暂停状态", status=RunStatus.PAUSED)
            items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED),
                WorkItem(id="step2", title="第二步", depends_on=["step1"]),
            ]
            state_store.save(run, items, paused=True)

            # 写入必要事件以通过一致性检查
            from agent_workflow.long_task.event_log import WorkflowEvent
            from datetime import datetime, timezone, timedelta
            tz = timezone(timedelta(hours=8))
            event_log.append(WorkflowEvent(
                event_type="WORKFLOW_CREATED", workflow_id="wf_paused",
                item_id=None, payload={"name": "暂停状态"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="ITEM_COMPLETED", workflow_id="wf_paused",
                item_id="step1", payload={},
                created_at=datetime.now(tz),
            ))

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            hydrated = QueueRunner.hydrate(state_store, event_log, handler)

            # 验证 paused 状态已恢复
            assert hydrated._paused is True

            # run() 应该因为暂停而停止（step2 未被处理）
            hydrated.run()
            assert hydrated.workflow_run.status == RunStatus.PAUSED
            assert hydrated.items[1].status == ItemStatus.PENDING

            # 验证 paused 状态已恢复
            assert hydrated._paused is True

            # run() 应该因为暂停而停止（step2 未被处理）
            hydrated.run()
            assert hydrated.workflow_run.status == RunStatus.PAUSED
            assert hydrated.items[1].status == ItemStatus.PENDING

    def test_hydrate_missing_state_file(self):
        """workflow_state.json 不存在时应抛出 FileNotFoundError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log, state_store = self._make_env(tmpdir)

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            with pytest.raises(FileNotFoundError, match="workflow_state.json"):
                QueueRunner.hydrate(state_store, event_log, handler)

    def test_hydrate_inconsistent_state(self):
        """state 与 event log 不一致时应抛出 ValueError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log, state_store = self._make_env(tmpdir, "wf_inconsistent")

            # state 说 step1 completed，但没有对应事件
            run = WorkflowRun(id="wf_inconsistent", name="不一致")
            items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED),
            ]
            state_store.save(run, items)

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            with pytest.raises(ValueError, match="不一致"):
                QueueRunner.hydrate(state_store, event_log, handler)

    def test_hydrate_no_duplicate_events(self):
        """hydrate 后 run() 不应重复 emit 已有事件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_log, state_store = self._make_env(tmpdir, "wf_no_dup")

            # 已有 WORKFLOW_CREATED + WORK_ITEM_CREATED + ITEM_COMPLETED 事件
            from agent_workflow.long_task.event_log import WorkflowEvent
            from datetime import datetime, timezone, timedelta
            tz = timezone(timedelta(hours=8))
            event_log.append(WorkflowEvent(
                event_type="WORKFLOW_CREATED", workflow_id="wf_no_dup",
                item_id=None, payload={"name": "无重复"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="WORK_ITEM_CREATED", workflow_id="wf_no_dup",
                item_id="step1", payload={"title": "第一步"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="WORK_ITEM_CREATED", workflow_id="wf_no_dup",
                item_id="step2", payload={"title": "第二步"},
                created_at=datetime.now(tz),
            ))
            event_log.append(WorkflowEvent(
                event_type="ITEM_COMPLETED", workflow_id="wf_no_dup",
                item_id="step1", payload={"artifact_path": "output/step1.md"},
                created_at=datetime.now(tz),
            ))

            run = WorkflowRun(id="wf_no_dup", name="无重复", status=RunStatus.RUNNING)
            items = [
                WorkItem(id="step1", title="第一步", status=ItemStatus.COMPLETED,
                         artifact_path="output/step1.md"),
                WorkItem(id="step2", title="第二步", depends_on=["step1"]),
            ]
            state_store.save(run, items)

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            hydrated = QueueRunner.hydrate(state_store, event_log, handler)
            hydrated.run()

            # 检查事件：不应有重复的 WORKFLOW_CREATED 或 WORK_ITEM_CREATED
            events = event_log.read_by_workflow("wf_no_dup")
            wf_created_count = sum(1 for e in events if e.event_type == "WORKFLOW_CREATED")
            assert wf_created_count == 1, f"WORKFLOW_CREATED 重复: {wf_created_count} 次"
            item_created_step1 = sum(
                1 for e in events if e.event_type == "WORK_ITEM_CREATED" and e.item_id == "step1"
            )
            assert item_created_step1 == 1, f"step1 WORK_ITEM_CREATED 重复: {item_created_step1} 次"
