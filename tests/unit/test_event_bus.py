"""测试 EventBus 和 Sink。"""

import os
import json
import tempfile
import pytest

from agent_workflow.observability import EventBus, ConsoleSink, JSONLSink


class TestEventBus:
    """EventBus 单元测试。"""

    def test_emit_basic(self):
        bus = EventBus()
        bus.emit("StateEntered", {"state": "test", "task": "work"})
        assert bus.event_count == 1

    def test_multi_sink(self):
        events = []

        def sink(event_type, event):
            events.append((event_type, event))

        bus = EventBus()
        bus.add_sink(sink)
        bus.emit("StateEntered", {"state": "start"})
        bus.emit("TransitionSelected", {"current_state": "start", "decision": "done", "next_state": "review"})

        assert len(events) == 2
        assert events[0][0] == "StateEntered"
        assert events[1][0] == "TransitionSelected"

    def test_sink_error_does_not_break(self):
        def bad_sink(event_type, event):
            raise RuntimeError("sink error")

        good_events = []

        def good_sink(event_type, event):
            good_events.append(event_type)

        bus = EventBus()
        bus.add_sink(bad_sink)
        bus.add_sink(good_sink)
        bus.emit("Heartbeat", {"state": "test"})

        assert len(good_events) == 1

    def test_flush(self):
        bus = EventBus()
        bus.emit("WorkflowStarted", {"run_id": "test"})
        bus.flush()  # 不应抛异常
        assert bus.event_count == 1


class TestJSONLSink:
    """JSONL Sink 测试。"""

    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            sink = JSONLSink(log_path)

            sink.write("StateEntered", {
                "event": "StateEntered",
                "state": "start",
                "run_id": "run_001",
                "timestamp": "2026-06-07T10:00:00+08:00",
                "payload": {"task": "work"},
            })
            sink.write("TransitionSelected", {
                "event": "TransitionSelected",
                "state": "start",
                "run_id": "run_001",
                "timestamp": "2026-06-07T10:01:00+08:00",
                "payload": {"decision": "done"},
            })
            sink.flush()
            sink.close()

            # 读取验证
            with open(log_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 2

            record = json.loads(lines[0])
            assert record["event"] == "StateEntered"


class TestConsoleSink:
    """ConsoleSink 测试。"""

    def test_formats(self):
        import io
        stream = io.StringIO()
        sink = ConsoleSink(stream=stream)

        sink.write("StateEntered", {
            "event": "StateEntered",
            "state": "codex_plan",
            "payload": {"state": "codex_plan", "task": "plan"},
        })
        output = stream.getvalue()
        assert "codex_plan" in output

    def test_heartbeat_suppressed(self):
        import io
        stream = io.StringIO()
        sink = ConsoleSink(stream=stream, show_heartbeat=False)

        sink.write("Heartbeat", {
            "event": "Heartbeat",
            "state": "running",
            "payload": {},
        })
        output = stream.getvalue()
        assert output == ""  # 不打印心跳


class TestProtocolRecoveryEvent:
    """ProtocolRecovery 事件类型与 registry 完整性。"""

    def test_event_type_exists(self):
        """EventType.ProtocolRecovery 已注册。"""
        from agent_workflow.observability.events import EventType
        assert hasattr(EventType, "ProtocolRecovery")
        assert EventType.ProtocolRecovery == "ProtocolRecovery"

    def test_registry_has_entry(self):
        """ProtocolRecovery 在 event_registry 中有条目。"""
        from agent_workflow.observability.events import event_registry
        assert "ProtocolRecovery" in event_registry

    def test_registry_required_fields(self):
        """ProtocolRecovery registry 包含所有必要字段（含 origin_text_hash）。"""
        from agent_workflow.observability.events import event_registry
        required = event_registry["ProtocolRecovery"]
        assert "state" in required
        assert "agent" in required
        assert "method" in required
        assert "confidence" in required
        assert "recovered_fields" in required
        assert "reason" in required
        assert "origin_text_hash" in required
        assert "timestamp" in required

    def test_validate_event_missing_field(self):
        """validate_event 能检测缺失字段。"""
        from agent_workflow.observability.events import validate_event
        payload = {"state": "review"}  # 缺少多数字段
        missing = validate_event("ProtocolRecovery", payload)
        assert len(missing) > 0
        assert "agent" in missing
        assert "timestamp" in missing

    def test_validate_event_complete(self):
        """所有字段齐全时 validate_event 返回空列表。"""
        from agent_workflow.observability.events import validate_event
        payload = {
            "state": "review", "agent": "claude-opus",
            "method": "regex", "confidence": 1.0,
            "recovered_fields": ["decision"],
            "reason": "test", "origin_text_hash": "abc123",
            "timestamp": "2026-01-01T00:00:00+08:00",
        }
        missing = validate_event("ProtocolRecovery", payload)
        assert missing == []
