"""长时运行冒烟测试。

P0 验证:
- 8h mock longrun 不丢 event（简化为短时验证）
- 12h resume 能恢复 RunContext（简化为序列化验证）
- 24h recovery 能识别 stale heartbeat（简化为阈值验证）
"""

import os
import json
import time
import tempfile
import pytest

from agent_workflow.context import RunContext
from agent_workflow.observability.event_bus import EventBus
from agent_workflow.observability.jsonl_sink import JSONLSink
from agent_workflow.observability.heartbeat import check_stale, HeartbeatEmitter


class TestLongrunSmoke:
    """长时运行简化测试（非真实 8h/12h/24h）。"""

    def test_event_bus_no_event_loss(self):
        """验证 EventBus 在大量事件时不丢事件。"""
        bus = EventBus()

        events_received = []
        def sink(event_type, event):
            events_received.append(event_type)

        bus.add_sink(sink)

        # 模拟 1000 个事件
        for i in range(1000):
            bus.emit("Heartbeat", {"i": i, "state": "running"})

        assert len(events_received) == 1000

    def test_jsonl_sink_large_volume(self):
        """验证 JSONL Sink 处理大量事件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            sink = JSONLSink(log_path)

            for i in range(500):
                sink.write("Heartbeat", {
                    "event": "Heartbeat",
                    "run_id": "run_001",
                    "state": "running",
                    "timestamp": f"2026-06-07T10:{i//60:02d}:{i%60:02d}+08:00",
                    "payload": {"i": i},
                })
            sink.flush()
            sink.close()

            # 验证所有事件都已写入
            with open(log_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 500

    def test_run_context_resume(self):
        """验证 RunContext 序列化后可以恢复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            ctx = RunContext.create(
                workflow_id="longrun_test",
                goal="长时间运行测试",
                project_root=tmpdir,
                run_id="run_001",
                run_root=run_root,
            )

            # 模拟多次状态转换
            states = ["codex_plan", "claude_review_plan", "codex_revise_plan",
                      "claude_review_plan", "codex_execute", "claude_audit", "done"]
            for s in states:
                ctx.record_state_visit(s)
                ctx.record_task_result(s, {
                    "task_id": s, "decision": "done", "status": "success",
                })

            # 保存
            ctx.save()

            # "崩溃"后恢复
            ctx2 = RunContext.load(run_root)
            assert ctx2.run_id == ctx.run_id
            assert ctx2.state_history == ctx.state_history
            assert len(ctx2.task_results) == len(ctx.task_results)

    def test_heartbeat_stale_detection(self):
        """验证 Stale 心跳检测。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            os.makedirs(run_root, exist_ok=True)

            # 写入一个"很久以前"的心跳
            heartbeat_path = os.path.join(run_root, "heartbeat.json")
            old_time = "2026-06-07T00:00:00+08:00"  # 远在过去
            with open(heartbeat_path, "w") as f:
                json.dump({
                    "run_id": "run_001",
                    "state": "claude_review_plan",
                    "elapsed_seconds": 1000,
                    "timestamp": old_time,
                }, f)

            stale, reason = check_stale(run_root, threshold_seconds=300)
            assert stale
            assert "s 前" in reason or "阈值" in reason

    def test_guard_terminates_loop(self):
        """验证 Guard 能终止无限循环。"""
        from agent_workflow.state_machine.guard import GuardChecker
        from agent_workflow.config.models import GuardModel

        guard = GuardChecker(GuardModel(max_visits=3))
        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )

        # 模拟 review 循环
        for i in range(4):
            ctx.record_state_visit("review_plan")

        result = guard.check("review_plan", ctx)
        assert not result.passed

    def test_event_bus_jsonl_sink_combo(self):
        """验证 EventBus + JSONLSink 组合使用。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "events.jsonl")
            sink = JSONLSink(log_path)
            bus = EventBus()
            bus.add_sink(sink)

            # 模拟完整 workflow 的事件序列
            bus.emit("WorkflowStarted", {"run_id": "run_001", "workflow_id": "test"})
            bus.emit("StateEntered", {"state": "codex_plan"})
            bus.emit("AgentStarted", {"state": "codex_plan", "agent": "mock", "task": "plan"})
            bus.emit("TaskFinished", {"state": "codex_plan", "decision": "done"})
            bus.emit("TransitionSelected", {"current_state": "codex_plan", "decision": "done", "next_state": "claude_review_plan"})
            bus.emit("WorkflowCompleted", {"run_id": "run_001", "final_state": "done"})

            bus.flush()
            sink.close()

            with open(log_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 6
            assert "WorkflowStarted" in lines[0]
            assert "WorkflowCompleted" in lines[-1]
