"""StateStore 单元测试。"""

import sys
import os
import json
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from workflow_run import WorkflowRun, RunStatus
from work_item import WorkItem, ItemStatus
from state_store import StateStore, check_consistency
from event_log import WorkflowEvent, TZ_SHANGHAI
from datetime import datetime


# ============================================================
# StateStore 测试
# ============================================================

class TestStateStore:
    """StateStore 读写测试。"""

    def test_save_and_load_roundtrip(self):
        """save 后 load 应返回相同数据。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "workflow_state.json")
            store = StateStore(state_path)

            run = WorkflowRun(id="wf_001", name="测试", status=RunStatus.RUNNING)
            items = [
                WorkItem(id="step1", title="数据分析", status=ItemStatus.COMPLETED,
                         artifact_path="output/step1.md"),
                WorkItem(id="step2", title="因子构建", depends_on=["step1"]),
            ]

            store.save(run, items, paused=False)

            # 验证文件存在
            assert os.path.exists(state_path)

            # load
            data = store.load()
            assert data["workflow_id"] == "wf_001"
            assert data["name"] == "测试"
            assert data["status"] == "RUNNING"
            assert data["paused"] is False
            assert data["completed_items"] == ["step1"]
            assert data["failed_items"] == []

            # item 字段
            assert data["items"]["step1"]["title"] == "数据分析"
            assert data["items"]["step1"]["status"] == "COMPLETED"
            assert data["items"]["step1"]["depends_on"] == []
            assert data["items"]["step1"]["artifact_path"] == "output/step1.md"

            assert data["items"]["step2"]["title"] == "因子构建"
            assert data["items"]["step2"]["status"] == "PENDING"
            assert data["items"]["step2"]["depends_on"] == ["step1"]
            assert data["items"]["step2"]["artifact_path"] is None

    def test_load_nonexistent(self):
        """不存在的文件返回空 dict。"""
        store = StateStore("/nonexistent/path/state.json")
        data = store.load()
        assert data == {}

    def test_save_with_failed_items(self):
        """failed_items 应正确记录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(os.path.join(tmpdir, "workflow_state.json"))

            run = WorkflowRun(id="wf_001", name="测试", status=RunStatus.FAILED)
            items = [
                WorkItem(id="step1", title="数据分析", status=ItemStatus.COMPLETED),
                WorkItem(id="step2", title="因子构建", status=ItemStatus.FAILED,
                         depends_on=["step1"]),
            ]

            store.save(run, items, paused=False)
            data = store.load()

            assert data["completed_items"] == ["step1"]
            assert data["failed_items"] == ["step2"]
            assert data["status"] == "FAILED"

    def test_atomic_write_no_tmp_residue(self):
        """原子写入后不应残留 .tmp 文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "workflow_state.json")
            store = StateStore(state_path)

            run = WorkflowRun(id="wf_001", name="测试")
            items = [WorkItem(id="step1", title="数据分析")]

            store.save(run, items)

            # 检查同目录下无 .tmp 文件
            tmp_files = [
                f for f in os.listdir(tmpdir)
                if f.endswith(".tmp")
            ]
            assert len(tmp_files) == 0

    def test_save_with_paused(self):
        """paused 状态应持久化。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(os.path.join(tmpdir, "workflow_state.json"))
            run = WorkflowRun(id="wf_001", name="测试")

            store.save(run, [], paused=True)
            data = store.load()
            assert data["paused"] is True

    def test_auto_create_directory(self):
        """目录不存在时自动创建。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "subdir", "state.json")
            store = StateStore(state_path)
            run = WorkflowRun(id="wf_001", name="测试")
            store.save(run, [], paused=False)
            assert os.path.exists(state_path)


# ============================================================
# check_consistency 测试
# ============================================================

class TestCheckConsistency:
    """check_consistency 状态/事件一致性检查测试。"""

    def test_empty_state_consistent(self):
        """空 state 应与任何事件一致（无数据可检）。"""
        errors = check_consistency({}, [])
        assert errors == []

    def test_consistent_state(self):
        """一致的 state 和 events 应无错误。"""
        ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
        state = {
            "workflow_id": "wf_001",
            "name": "测试",
            "status": "COMPLETED",
            "paused": False,
            "completed_items": ["step1"],
            "failed_items": [],
            "items": {
                "step1": {"title": "数据分析", "status": "COMPLETED",
                          "depends_on": [], "artifact_path": "out/r.md"},
            },
        }
        events = [
            WorkflowEvent("WORKFLOW_CREATED", "wf_001", None, {}, ts),
            WorkflowEvent("WORK_ITEM_CREATED", "wf_001", "step1", {}, ts),
            WorkflowEvent("ITEM_STARTED", "wf_001", "step1", {}, ts),
            WorkflowEvent("ITEM_COMPLETED", "wf_001", "step1",
                          {"artifact_path": "out/r.md"}, ts),
        ]
        errors = check_consistency(state, events)
        assert errors == []

    def test_state_completed_missing_event(self):
        """state 中 completed 但无 ITEM_COMPLETED 事件应报错。"""
        ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
        state = {
            "workflow_id": "wf_001",
            "completed_items": ["step1"],
            "failed_items": [],
            "items": {"step1": {}},
        }
        events = [
            WorkflowEvent("ITEM_STARTED", "wf_001", "step1", {}, ts),
        ]
        errors = check_consistency(state, events)
        assert len(errors) > 0
        assert any("未找到对应的 ITEM_COMPLETED 事件" in e for e in errors)

    def test_state_failed_missing_event(self):
        """state 中 failed 但无 ITEM_FAILED 事件应报错。"""
        ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
        state = {
            "workflow_id": "wf_001",
            "completed_items": [],
            "failed_items": ["step1"],
            "items": {"step1": {}},
        }
        events = [
            WorkflowEvent("ITEM_STARTED", "wf_001", "step1", {}, ts),
        ]
        errors = check_consistency(state, events)
        assert len(errors) > 0
        assert any("未找到对应的 ITEM_FAILED 事件" in e for e in errors)

    def test_workflow_id_mismatch(self):
        """event 的 workflow_id 与 state 不匹配应报错。"""
        ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
        state = {
            "workflow_id": "wf_001",
            "completed_items": [],
            "failed_items": [],
            "items": {},
        }
        events = [
            WorkflowEvent("WORKFLOW_CREATED", "wf_002", None, {}, ts),
        ]
        errors = check_consistency(state, events)
        assert len(errors) > 0
        assert any("不匹配" in e for e in errors)
