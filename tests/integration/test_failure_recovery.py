"""失败恢复集成测试：upstream 失败 → downstream 不执行 → state 可恢复。"""

import sys
import os
import tempfile
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from workflow_run import WorkflowRun, RunStatus
from work_item import WorkItem, ItemStatus
from event_log import EventLog
from state_store import StateStore
from queue_runner import QueueRunner


class TestFailureRecovery:
    """失败恢复测试。"""

    def test_step2_failure_blocks_step3(self):
        """step2 失败 → step3 不执行 → state 可加载。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_fail", name="失败恢复测试")
            items = [
                WorkItem(id="step1", title="数据分析"),
                WorkItem(id="step2", title="因子构建", depends_on=["step1"]),
                WorkItem(id="step3", title="回测验证", depends_on=["step2"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            def handler(item: WorkItem):
                if item.id == "step2":
                    raise RuntimeError("因子构建失败")
                item.status = ItemStatus.COMPLETED
                item.artifact_path = f"output/{item.id}.md"

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            # 验证状态
            assert items[0].status == ItemStatus.COMPLETED
            assert items[1].status == ItemStatus.FAILED
            assert items[2].status == ItemStatus.PENDING
            assert run.status == RunStatus.FAILED

            # 验证 event log 含 ITEM_FAILED
            events = event_log.read_by_workflow("wf_fail")
            failed_events = [e for e in events if e.event_type == "ITEM_FAILED"]
            assert len(failed_events) == 1
            assert failed_events[0].item_id == "step2"
            assert "因子构建失败" in failed_events[0].payload.get("error", "")

            # step3 不应有任何事件
            step3_events = [e for e in events if e.item_id == "step3"]
            assert len(step3_events) == 1  # 仅 WORK_ITEM_CREATED
            assert step3_events[0].event_type == "WORK_ITEM_CREATED"

    def test_state_reloadable_after_failure(self):
        """失败后 state 可重新加载并识别 failed_items。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "workflow_state.json")
            event_path = os.path.join(tmpdir, "events.jsonl")

            # Run 1: step2 失败
            run = WorkflowRun(id="wf_reload", name="重载测试")
            items = [
                WorkItem(id="s1", title="step1"),
                WorkItem(id="s2", title="step2", depends_on=["s1"]),
                WorkItem(id="s3", title="step3", depends_on=["s2"]),
            ]

            def failing_handler(item: WorkItem):
                if item.id == "s2":
                    raise RuntimeError("失败")
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=failing_handler,
                event_log=EventLog(event_path),
                state_store=StateStore(state_path),
            )
            runner.run()

            # 重新加载 state
            store2 = StateStore(state_path)
            state = store2.load()

            assert state["workflow_id"] == "wf_reload"
            assert state["status"] == "FAILED"
            assert "s1" in state["completed_items"]
            assert "s2" in state["failed_items"]
            assert "s3" not in state["completed_items"]
            assert "s3" not in state["failed_items"]

            # 可以重建 items
            rebuilt_items = []
            for item_id, item_data in state["items"].items():
                rebuilt_items.append(WorkItem(
                    id=item_id,
                    title=item_data["title"],
                    status=ItemStatus(item_data["status"]),
                    depends_on=item_data["depends_on"],
                    artifact_path=item_data["artifact_path"],
                ))

            # 验证重建的 items
            rebuilt_map = {item.id: item for item in rebuilt_items}
            assert rebuilt_map["s1"].status == ItemStatus.COMPLETED
            assert rebuilt_map["s2"].status == ItemStatus.FAILED
            assert rebuilt_map["s3"].status == ItemStatus.PENDING

    def test_event_log_preserves_failure_context(self):
        """失败事件的 payload 应记录错误信息。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run = WorkflowRun(id="wf_ctx", name="上下文测试")
            items = [
                WorkItem(id="step1", title="步骤1"),
                WorkItem(id="step2", title="步骤2", depends_on=["step1"]),
            ]
            event_log = EventLog(os.path.join(tmpdir, "events.jsonl"))
            state_store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            error_msg = "KeyError: 'missing_field' at line 42"

            def handler(item: WorkItem):
                if item.id == "step2":
                    raise KeyError(error_msg)
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )
            runner.run()

            events = event_log.read_by_workflow("wf_ctx")
            fail_event = next(e for e in events if e.event_type == "ITEM_FAILED")
            assert error_msg in fail_event.payload["error"]
            assert fail_event.payload["title"] == "步骤2"

    def test_consistency_check_on_rerun(self):
        """state 和 event log 不一致时 QueueRunner 应抛出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "workflow_state.json")
            event_path = os.path.join(tmpdir, "events.jsonl")

            # 手动写一个 state 说 step1 完成
            state = {
                "workflow_id": "wf_bad",
                "name": "不一致测试",
                "status": "RUNNING",
                "paused": False,
                "completed_items": ["step1"],
                "failed_items": [],
                "items": {
                    "step1": {
                        "title": "step1", "status": "COMPLETED",
                        "depends_on": [], "artifact_path": None,
                    },
                    "step2": {
                        "title": "step2", "status": "PENDING",
                        "depends_on": ["step1"], "artifact_path": None,
                    },
                },
            }
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)

            # event log 为空（无 ITEM_COMPLETED）
            event_log = EventLog(event_path)

            # 重建 runner 时应检测到不一致
            run = WorkflowRun(id="wf_bad", name="不一致测试")
            items = [
                WorkItem(id="step1", title="step1"),
                WorkItem(id="step2", title="step2", depends_on=["step1"]),
            ]
            state_store = StateStore(state_path)

            def handler(item: WorkItem):
                item.status = ItemStatus.COMPLETED

            runner = QueueRunner(
                workflow_run=run, items=items, handler=handler,
                event_log=event_log, state_store=state_store,
            )

            with pytest.raises(ValueError, match="不一致"):
                runner.run()
