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
