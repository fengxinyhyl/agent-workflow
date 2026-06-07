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
