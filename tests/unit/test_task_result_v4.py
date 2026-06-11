"""测试 TaskResult 模型和校验。"""

import json
import pytest

from agent_workflow.tasks.result import (
    TaskResult,
    ArtifactRef,
    ExecutionMetadata,
    Issue,
    VALID_STATUSES,
    VALID_DECISIONS,
)


class TestTaskResult:
    """TaskResult 单元测试。"""

    def _make_minimal_result(self) -> TaskResult:
        return TaskResult(
            schema_version=1,
            task_id="test_task",
            state="test_state",
            agent="mock",
            status="success",
            decision="done",
            summary="测试完成",
            execution=ExecutionMetadata(
                started_at="2026-06-07T10:00:00+08:00",
                finished_at="2026-06-07T10:01:00+08:00",
                duration_seconds=60,
                attempt=1,
                exit_code=0,
            ),
        )

    def test_create_valid(self):
        result = self._make_minimal_result()
        assert result.is_valid()
        assert result.get_decision() == "done"

    def test_validate_missing_task_id(self):
        result = self._make_minimal_result()
        result.task_id = ""
        issues = result.validate()
        assert any("task_id" in i for i in issues)

    def test_validate_invalid_status(self):
        result = self._make_minimal_result()
        result.status = "unknown_status"
        issues = result.validate()
        assert any("status" in i for i in issues)

    def test_validate_invalid_decision(self):
        result = self._make_minimal_result()
        result.decision = "unknown_decision"
        issues = result.validate()
        assert any("decision" in i for i in issues)

    def test_serialization(self):
        result = self._make_minimal_result()
        result.artifacts = [
            ArtifactRef(
                name="output",
                staging_path="staging/test/output.md",
                artifact_path="artifacts/output.md",
                type="markdown",
            )
        ]
        result.issues = [
            Issue(severity="warning", title="测试问题", detail="这是一个测试问题")
        ]

        # 序列化
        data = result.to_dict()
        assert data["task_id"] == "test_task"
        assert len(data["artifacts"]) == 1

        # JSON
        json_str = result.to_json()
        assert "test_task" in json_str

        # 反序列化
        result2 = TaskResult.from_json(json_str)
        assert result2.task_id == result.task_id
        assert result2.status == result.status
        assert result2.decision == result.decision
        assert len(result2.get_artifacts()) == 1
        assert len(result2.get_issues()) == 1

    def test_get_artifacts_mixed_types(self):
        """测试 artifacts 混合类型（dict 和 ArtifactRef）。"""
        result = self._make_minimal_result()
        result.artifacts = [
            {"name": "a1", "staging_path": "s1", "artifact_path": "a1", "type": "md"},
            ArtifactRef(name="a2", staging_path="s2", artifact_path="a2", type="json"),
        ]
        artifacts = result.get_artifacts()
        assert len(artifacts) == 2
        assert artifacts[0].name == "a1"
        assert artifacts[1].name == "a2"

    def test_get_execution_mixed_types(self):
        """测试 execution 混合类型（dict 和 ExecutionMetadata）。"""
        result = self._make_minimal_result()
        exec_data = result.get_execution()
        assert exec_data.exit_code == 0
        assert exec_data.attempt == 1

    # ── 新增：Phase A 字段扩展测试 ──

    def test_execution_metadata_pid_roundtrip(self):
        """ExecutionMetadata pid 字段 round-trip 序列化。"""
        from agent_workflow.tasks.result import ExecutionMetadata
        meta = ExecutionMetadata(
            started_at="2026-06-11T10:00:00+08:00",
            finished_at="2026-06-11T10:01:00+08:00",
            duration_seconds=60.0,
            attempt=1,
            exit_code=0,
            pid=1234,
        )
        data = meta.to_dict()
        assert data["pid"] == 1234
        # 通过 from_dict 反序列化（TaskResult.from_dict 中 ExecutionMetadata(**exec_data)）
        restored = ExecutionMetadata(**data)
        assert restored.pid == 1234

    def test_task_result_new_fields_roundtrip(self):
        """TaskResult 四个新字段 round-trip 序列化。"""
        result = TaskResult(
            schema_version=1,
            task_id="test",
            state="plan",
            agent="claude",
            status="success",
            decision="done",
            summary="ok",
            session_id="s1",
            token_usage={"input_tokens": 100, "output_tokens": 50},
            log_path="/tmp/log.jsonl",
            packet_path="/tmp/packet.md",
        )
        data = result.to_dict()
        assert data["session_id"] == "s1"
        assert data["token_usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert data["log_path"] == "/tmp/log.jsonl"
        assert data["packet_path"] == "/tmp/packet.md"

        restored = TaskResult.from_dict(data)
        assert restored.session_id == "s1"
        assert restored.token_usage == {"input_tokens": 100, "output_tokens": 50}
        assert restored.log_path == "/tmp/log.jsonl"
        assert restored.packet_path == "/tmp/packet.md"

    def test_backward_compat_missing_new_fields(self):
        """旧格式 JSON（无新字段）反序列化成功，新字段为默认值。"""
        old_json = {
            "schema_version": 1,
            "task_id": "old_task",
            "state": "old_state",
            "agent": "old_agent",
            "status": "success",
            "decision": "done",
            "summary": "old",
            "execution": {
                "started_at": "2026-01-01T00:00:00+08:00",
                "finished_at": "2026-01-01T00:01:00+08:00",
                "duration_seconds": 60,
                "attempt": 1,
                "exit_code": 0,
                # 无 pid 字段
            },
            # 无 session_id / token_usage / log_path / packet_path
        }
        result = TaskResult.from_dict(old_json)
        assert result.session_id == ""
        assert result.token_usage == {}
        assert result.log_path == ""
        assert result.packet_path == ""
        exec_meta = result.get_execution()
        assert exec_meta.pid is None

    def test_token_usage_default_factory(self):
        """两个 TaskResult 实例不共享 token_usage dict。"""
        r1 = TaskResult(task_id="t1")
        r2 = TaskResult(task_id="t2")
        r1.token_usage["input_tokens"] = 100
        assert r2.token_usage == {}
        assert r1.token_usage == {"input_tokens": 100}


class TestTaskResultSchema:
    """TaskResult JSON Schema 测试。"""

    def test_schema_export(self):
        from agent_workflow.tasks.result_schema import TASK_RESULT_SCHEMA
        assert TASK_RESULT_SCHEMA["title"] == "TaskResult"
        assert "schema_version" in TASK_RESULT_SCHEMA["required"]

    def test_build_schema_with_allowed_decisions(self):
        from agent_workflow.tasks.result_schema import build_task_result_schema
        schema = build_task_result_schema(["approve", "revise", "reject"])
        decision_schema = schema["properties"]["decision"]
        assert "enum" in decision_schema
        assert decision_schema["enum"] == ["approve", "revise", "reject"]

    # ── 新增：A4 schema 完整性测试 ──

    def test_schema_includes_new_fields(self):
        """TASK_RESULT_SCHEMA 包含所有新增字段定义。"""
        from agent_workflow.tasks.result_schema import TASK_RESULT_SCHEMA
        props = TASK_RESULT_SCHEMA["properties"]
        # root-level 四个新字段
        assert "session_id" in props
        assert "token_usage" in props
        assert "log_path" in props
        assert "packet_path" in props
        # execution.properties 包含 pid
        exec_props = props["execution"]["properties"]
        assert "pid" in exec_props

    def test_schema_required_unchanged(self):
        """TASK_RESULT_SCHEMA required 列表不包含新字段。"""
        from agent_workflow.tasks.result_schema import TASK_RESULT_SCHEMA
        required = TASK_RESULT_SCHEMA["required"]
        assert "session_id" not in required
        assert "token_usage" not in required
        assert "log_path" not in required
        assert "packet_path" not in required
