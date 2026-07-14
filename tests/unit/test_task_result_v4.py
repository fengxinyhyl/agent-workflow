"""测试 TaskResult 模型和校验。"""

import json
import pytest

from agent_workflow.tasks.result import (
    TaskResult,
    ArtifactRef,
    ExecutionMetadata,
    Issue,
    RecoveryInfo,
    VALID_STATUSES,
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

    def test_validate_decision_not_checked_by_runtime(self):
        """decision 合法性不再由 Runtime 校验：任意 decision（含未知值）都不报错。"""
        result = self._make_minimal_result()
        result.decision = "unknown_decision"
        issues = result.validate()
        assert not any("decision" in i for i in issues)
        assert result.is_valid()

    # ── 新增：decision Optional 契约测试 ──

    def test_decision_default_none(self):
        """TaskResult 默认 decision 为 None。"""
        result = TaskResult(task_id="t")
        assert result.decision is None

    def test_get_decision_returns_none(self):
        """decision 为 None 时 get_decision() 返回 None，不兜底为字符串。"""
        result = TaskResult(task_id="t")
        assert result.get_decision() is None

    def test_decision_none_is_valid(self):
        """decision=None 的 TaskResult 通过校验。"""
        result = self._make_minimal_result()
        result.decision = None
        assert result.is_valid()

    def test_decision_none_roundtrip(self):
        """decision=None 经 to_dict/from_dict round-trip 保持 None。"""
        result = self._make_minimal_result()
        result.decision = None
        restored = TaskResult.from_dict(result.to_dict())
        assert restored.decision is None
        # 从缺省 decision 字段的字典反序列化也应为 None
        data = result.to_dict()
        del data["decision"]
        assert TaskResult.from_dict(data).decision is None

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
        # 业务决策值全部在 enum 中
        assert {"approve", "revise", "reject"}.issubset(set(decision_schema["enum"]))
        # decision 为 Optional：enum 同时允许 None，type 接受 null
        assert None in decision_schema["enum"]
        assert decision_schema["type"] == ["string", "null"]

    def test_decision_schema_accepts_null(self):
        """decision=None 的 TaskResult 应符合 schema（type 接受 null）。"""
        from agent_workflow.tasks.result_schema import (
            TASK_RESULT_SCHEMA,
            build_task_result_schema,
        )
        from agent_workflow.tasks.result import TaskResult, ExecutionMetadata

        decision_type = TASK_RESULT_SCHEMA["properties"]["decision"]["type"]
        assert decision_type == ["string", "null"]

        tr = TaskResult(
            task_id="t", state="s", status="success", decision=None,
            execution=ExecutionMetadata(started_at="2026-01-01", finished_at="2026-01-01"),
        )
        # to_dict 真实输出 decision=null，schema 应接受
        assert tr.to_dict()["decision"] is None
        try:
            import jsonschema  # type: ignore
        except ImportError:
            return  # 无 jsonschema 时仅校验 type 声明
        jsonschema.validate(tr.to_dict(), build_task_result_schema(["done", "fail"]))
        jsonschema.validate(tr.to_dict(), build_task_result_schema(None))

    def test_decision_not_required(self):
        """decision 已移出 required 列表（不再必填）。"""
        from agent_workflow.tasks.result_schema import TASK_RESULT_SCHEMA
        assert "decision" not in TASK_RESULT_SCHEMA["required"]

    def test_build_schema_without_allowed_decisions_no_enum(self):
        """无 allowed_decisions 时 decision 为自由字符串，不注入 enum。"""
        from agent_workflow.tasks.result_schema import build_task_result_schema
        schema = build_task_result_schema(None)
        assert "enum" not in schema["properties"]["decision"]

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


class TestExecutionMetadataProtocolAxis:
    """ExecutionMetadata 协议轴字段：protocol_origin + recovery。"""

    def test_default_protocol_origin_native(self):
        """默认 protocol_origin 为 "native"。"""
        meta = ExecutionMetadata()
        assert meta.protocol_origin == "native"
        assert meta.recovery is None

    def test_explicit_protocol_origin_parser(self):
        """显式设置 protocol_origin="parser" 并带 recovery。"""
        ri = RecoveryInfo(
            method="regex", confidence=1.0,
            recovered_fields=["decision"],
            reason="test", origin_text_hash="abc123",
        )
        meta = ExecutionMetadata(protocol_origin="parser", recovery=ri)
        assert meta.protocol_origin == "parser"
        assert meta.recovery is not None
        assert meta.recovery.method == "regex"

    def test_to_dict_no_recovery(self):
        """recovery=None 时 to_dict 输出 None（不抛异常）。"""
        meta = ExecutionMetadata(protocol_origin="native")
        d = meta.to_dict()
        assert d["protocol_origin"] == "native"
        assert d["recovery"] is None

    def test_to_dict_with_recovery(self):
        """recovery 非 None 时 to_dict 嵌套序列化。"""
        ri = RecoveryInfo(
            method="regex", confidence=1.0,
            recovered_fields=["decision"],
            reason="JSON missing", origin_text_hash="deadbeef12345678",
        )
        meta = ExecutionMetadata(protocol_origin="parser", recovery=ri)
        d = meta.to_dict()
        assert d["protocol_origin"] == "parser"
        assert d["recovery"]["method"] == "regex"
        assert d["recovery"]["confidence"] == 1.0
        assert d["recovery"]["recovered_fields"] == ["decision"]
        assert d["recovery"]["origin_text_hash"] == "deadbeef12345678"

    def test_roundtrip_with_recovery(self):
        """ExecutionMetadata + RecoveryInfo to_dict → from_dict round-trip。"""
        ri = RecoveryInfo(
            method="regex", confidence=1.0,
            recovered_fields=["decision"],
            reason="test", origin_text_hash="abc123",
        )
        meta = ExecutionMetadata(
            started_at="2026-01-01T00:00:00+08:00",
            finished_at="2026-01-01T00:01:00+08:00",
            protocol_origin="parser", recovery=ri,
        )
        restored = ExecutionMetadata.from_dict(meta.to_dict())
        assert restored.protocol_origin == "parser"
        assert restored.recovery is not None
        assert restored.recovery.method == "regex"
        assert restored.recovery.confidence == 1.0
        assert restored.recovery.recovered_fields == ["decision"]
        assert restored.recovery.origin_text_hash == "abc123"

    def test_from_dict_old_data_no_protocol_fields(self):
        """老字典无 protocol_origin/recovery —→ 缺省 native/None。"""
        old = {"started_at": "2026-01-01", "finished_at": "2026-01-01",
               "duration_seconds": 10, "attempt": 1, "exit_code": 0}
        meta = ExecutionMetadata.from_dict(old)
        assert meta.protocol_origin == "native"
        assert meta.recovery is None

    def test_from_dict_empty(self):
        """空 dict —→ 全默认值。"""
        meta = ExecutionMetadata.from_dict({})
        assert meta.protocol_origin == "native"
        assert meta.recovery is None

    def test_from_dict_none(self):
        """None —→ 全默认值。"""
        meta = ExecutionMetadata.from_dict(None)
        assert meta.protocol_origin == "native"
        assert meta.recovery is None


class TestRecoveryInfo:
    """RecoveryInfo 序列化/反序列化。"""

    def test_default_values(self):
        ri = RecoveryInfo()
        assert ri.method == "native"
        assert ri.confidence == 1.0
        assert ri.recovered_fields == []
        assert ri.reason == ""
        assert ri.origin_text_hash == ""

    def test_to_dict(self):
        ri = RecoveryInfo(
            method="synonym", confidence=0.95,
            recovered_fields=["decision"], reason="L2 hit",
            origin_text_hash="hash123",
        )
        d = ri.to_dict()
        assert d["method"] == "synonym"
        assert d["confidence"] == 0.95
        assert d["recovered_fields"] == ["decision"]
        assert d["origin_text_hash"] == "hash123"

    def test_from_dict_valid(self):
        d = {"method": "regex", "confidence": 1.0,
             "recovered_fields": ["decision", "status"],
             "reason": "test", "origin_text_hash": "abc"}
        ri = RecoveryInfo.from_dict(d)
        assert ri is not None
        assert ri.method == "regex"
        assert ri.recovered_fields == ["decision", "status"]

    def test_from_dict_none(self):
        """None 输入 —→ 返回 None。"""
        assert RecoveryInfo.from_dict(None) is None

    def test_from_dict_partial(self):
        """部分字段缺失 —→ 默认值补齐。"""
        ri = RecoveryInfo.from_dict({"method": "regex"})
        assert ri is not None
        assert ri.method == "regex"
        assert ri.confidence == 1.0
        assert ri.recovered_fields == []
        assert ri.origin_text_hash == ""


class TestTaskResultProtocolAxis:
    """TaskResult 内 protocol_origin/recovery 端到端序列化。"""

    def test_roundtrip_with_recovery(self):
        """TaskResult 含 parser 恢复信息 round-trip。"""
        ri = RecoveryInfo(
            method="regex", confidence=1.0,
            recovered_fields=["decision"], reason="test", origin_text_hash="abc",
        )
        exec_meta = ExecutionMetadata(
            started_at="2026-01-01T00:00:00+08:00",
            finished_at="2026-01-01T00:01:00+08:00",
            protocol_origin="parser", recovery=ri,
        )
        tr = TaskResult(
            task_id="review", state="review", agent="claude",
            status="success", decision="revise",
            summary="parser 恢复", execution=exec_meta,
        )
        d = tr.to_dict()
        restored = TaskResult.from_dict(d)
        assert restored.status == "success"
        assert restored.decision == "revise"
        exec_r = restored.get_execution()
        assert exec_r.protocol_origin == "parser"
        assert exec_r.recovery is not None
        assert exec_r.recovery.method == "regex"

    def test_old_taskresult_no_protocol_fields(self):
        """老 TaskResult 反序列化后 protocol_origin=native、recovery=None。"""
        old_json = {
            "schema_version": 1,
            "task_id": "old_task",
            "state": "old_state",
            "status": "success",
            "decision": "done",
            "summary": "old",
            "execution": {
                "started_at": "2026-01-01T00:00:00+08:00",
                "finished_at": "2026-01-01T00:01:00+08:00",
                "duration_seconds": 60, "attempt": 1, "exit_code": 0,
            },
        }
        tr = TaskResult.from_dict(old_json)
        exec_meta = tr.get_execution()
        assert exec_meta.protocol_origin == "native"
        assert exec_meta.recovery is None
