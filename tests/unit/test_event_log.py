"""EventLog 单元测试。"""

import json
import os
import tempfile
import pytest

from agent_workflow.long_task.event_log import (
    WorkflowEvent, EventLog, TZ_SHANGHAI, VALID_EVENT_TYPES,
    _serialize_event, _deserialize_event,
)
from datetime import datetime, timezone, timedelta


class TestWorkflowEvent:
    """WorkflowEvent dataclass 测试。"""

    def test_create_basic(self):
        """创建基本事件。"""
        ts = datetime(2026, 6, 8, 10, 0, 0, tzinfo=TZ_SHANGHAI)
        event = WorkflowEvent(
            event_type="WORKFLOW_CREATED",
            workflow_id="wf_001",
            item_id=None,
            payload={"name": "测试"},
            created_at=ts,
        )
        assert event.event_type == "WORKFLOW_CREATED"
        assert event.workflow_id == "wf_001"
        assert event.item_id is None
        assert event.payload == {"name": "测试"}
        assert event.created_at == ts

    def test_create_with_item_id(self):
        """创建 item 级事件。"""
        event = WorkflowEvent(
            event_type="ITEM_STARTED",
            workflow_id="wf_001",
            item_id="step1",
            payload={},
            created_at=datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI),
        )
        assert event.item_id == "step1"

    def test_serialize_deserialize_roundtrip(self):
        """序列化后反序列化应还原。"""
        ts = datetime(2026, 6, 8, 15, 30, 0, tzinfo=TZ_SHANGHAI)
        event = WorkflowEvent(
            event_type="ITEM_COMPLETED",
            workflow_id="wf_001",
            item_id="step2",
            payload={"artifact_path": "output/r.md"},
            created_at=ts,
        )
        d = _serialize_event(event)
        restored = _deserialize_event(d)
        assert restored.event_type == event.event_type
        assert restored.workflow_id == event.workflow_id
        assert restored.item_id == event.item_id
        assert restored.payload == event.payload
        assert restored.created_at == event.created_at

    def test_deserialize_naive_datetime_assumes_shanghai(self):
        """naive datetime 应被视为 Asia/Shanghai。"""
        d = {
            "event_type": "ITEM_STARTED",
            "workflow_id": "wf_001",
            "item_id": "step1",
            "payload": {},
            "created_at": "2026-06-08T10:00:00",
        }
        event = _deserialize_event(d)
        assert event.created_at.tzinfo is not None
        assert event.created_at.utcoffset() == timedelta(hours=8)

    def test_deserialize_timezone_aware(self):
        """带时区的 ISO 8601 应正确解析。"""
        d = {
            "event_type": "ITEM_STARTED",
            "workflow_id": "wf_001",
            "item_id": "step1",
            "payload": {},
            "created_at": "2026-06-08T10:00:00+08:00",
        }
        event = _deserialize_event(d)
        assert event.created_at.tzinfo is not None
        assert event.created_at.utcoffset() == timedelta(hours=8)


class TestEventLog:
    """EventLog JSONL 读写测试。"""

    def test_append_and_read_all(self):
        """追加事件后读取全部。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            log = EventLog(log_path)

            ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
            log.append(WorkflowEvent(
                "WORKFLOW_CREATED", "wf_001", None, {"name": "test"}, ts,
            ))
            log.append(WorkflowEvent(
                "WORK_ITEM_CREATED", "wf_001", "step1", {"title": "data"}, ts,
            ))
            log.close()

            events = EventLog(log_path).read_all()
            assert len(events) == 2
            assert events[0].event_type == "WORKFLOW_CREATED"
            assert events[1].event_type == "WORK_ITEM_CREATED"

    def test_read_all_nonexistent(self):
        """不存在的文件返回空列表。"""
        log = EventLog("/nonexistent/path/events.jsonl")
        events = log.read_all()
        assert events == []

    def test_read_by_workflow(self):
        """按 workflow_id 过滤。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            log = EventLog(log_path)

            ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
            log.append(WorkflowEvent("WORKFLOW_CREATED", "wf_a", None, {}, ts))
            log.append(WorkflowEvent("ITEM_STARTED", "wf_a", "s1", {}, ts))
            log.append(WorkflowEvent("WORKFLOW_CREATED", "wf_b", None, {}, ts))
            log.close()

            a_events = EventLog(log_path).read_by_workflow("wf_a")
            assert len(a_events) == 2
            assert all(e.workflow_id == "wf_a" for e in a_events)

            b_events = EventLog(log_path).read_by_workflow("wf_b")
            assert len(b_events) == 1

    def test_jsonl_format_valid(self):
        """每行应是合法 JSON。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            log = EventLog(log_path)

            ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
            log.append(WorkflowEvent("ITEM_COMPLETED", "wf_001", "step1",
                                     {"artifact_path": "out/r.md"}, ts))
            log.close()

            with open(log_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["event_type"] == "ITEM_COMPLETED"
            assert data["workflow_id"] == "wf_001"

    def test_append_multiple_workflows(self):
        """多个 workflow 的事件应共存于同一文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            log = EventLog(log_path)

            ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
            for wf_id in ["wf_1", "wf_2", "wf_3"]:
                log.append(WorkflowEvent("WORKFLOW_CREATED", wf_id, None, {}, ts))
            log.close()

            events = EventLog(log_path).read_all()
            assert len(events) == 3

    def test_auto_create_directory(self):
        """目录不存在时自动创建。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "subdir", "events.jsonl")
            log = EventLog(log_path)
            ts = datetime(2026, 6, 8, tzinfo=TZ_SHANGHAI)
            log.append(WorkflowEvent("WORKFLOW_CREATED", "wf_001", None, {}, ts))
            log.close()
            assert os.path.exists(log_path)
