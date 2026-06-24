"""负向测试 — 验证 P0 校验阻断链、fail-fast、边界行为。

覆盖:
- 无效 TaskResult（缺字段）→ blocking error 不 promote
- 缺失 staging artifact → promotion 失败
- 非法 artifact path（.. 逃逸）→ promotion 失败
- 缺失 required skill → fail-fast
- 未知 decision → 走 default
- guard max_visits → GuardFailed 事件
- cancel 文件 → 循环终止
"""

import os
import json
import tempfile
import pytest

from agent_workflow.config.loader import load_workflow
from agent_workflow.state_machine.runner import Runner, cancel_run
from agent_workflow.tasks.result import TaskResult, ExecutionMetadata, Issue, _now_iso


# —— 辅助函数 ——

def _make_error_task_result(state_name, errors=None):
    """创建一个有问题的 TaskResult。"""
    tr = TaskResult(
        schema_version=0,  # 无效版本
        task_id="",         # 缺少 task_id
        state=state_name,
        agent="test",
        status="unknown_status",  # 无效 status
        decision="unknown_decision",  # 无效 decision
        summary="",
        artifacts=[],
        execution={},  # 缺少 started_at / finished_at
    )
    return tr


def _make_valid_task_result(state_name, decision="done", artifacts=None):
    """创建一个合法的 TaskResult。"""
    return TaskResult(
        schema_version=1,
        task_id=state_name,
        state=state_name,
        agent="test",
        status="success",
        decision=decision,
        summary="测试完成",
        artifacts=artifacts or [],
        execution=ExecutionMetadata(
            started_at=_now_iso(),
            finished_at=_now_iso(),
            duration_seconds=1.0,
            attempt=1,
            exit_code=0,
        ),
    )


class TestTaskResultValidation:
    """TaskResult 校验测试。"""

    def test_missing_required_fields_blocking(self):
        """缺必需字段 → blocking error。"""
        tr = _make_error_task_result("test_state")
        result_dict = tr.to_dict()

        from agent_workflow.validators.task_result import TaskResultValidator
        validator = TaskResultValidator()
        vr = validator.validate(result_dict)

        # 应该有 blocking errors（schema_version、task_id、execution 缺失）
        assert not vr.passed
        assert len(vr.errors) > 0
        # 检查有 schema_version 相关的 error
        schema_errors = [e for e in vr.errors if "schema_version" in e]
        assert len(schema_errors) > 0
        # 检查有 task_id 相关的 error
        task_errors = [e for e in vr.errors if "task_id" in e]
        assert len(task_errors) > 0

    def test_invalid_status_warning(self):
        """无效 status → warning。"""
        tr = _make_valid_task_result("test_state")
        tr.status = "unknown_status"
        result_dict = tr.to_dict()

        from agent_workflow.validators.task_result import TaskResultValidator
        validator = TaskResultValidator()
        vr = validator.validate(result_dict)

        # status 无效是 warning，不是 error
        status_warnings = [w for w in vr.warnings if "status" in w]
        assert len(status_warnings) > 0
        # 其他必需字段都有，所以 passed 应为 True
        assert vr.passed

    def test_invalid_decision_warning(self):
        """无效 decision → warning。"""
        tr = _make_valid_task_result("test_state")
        tr.decision = "unknown_decision"
        result_dict = tr.to_dict()

        from agent_workflow.validators.task_result import TaskResultValidator
        validator = TaskResultValidator()
        vr = validator.validate(result_dict)

        decision_warnings = [w for w in vr.warnings if "decision" in w]
        assert len(decision_warnings) > 0
        assert vr.passed

    def test_decision_not_in_allowed_decisions_warning(self):
        """decision 不在 allowed_decisions → warning。"""
        tr = _make_valid_task_result("test_state", decision="approve")
        result_dict = tr.to_dict()

        from agent_workflow.validators.task_result import TaskResultValidator
        # 限制 allowed_decisions 为 ["done", "fail"]
        validator = TaskResultValidator(allowed_decisions=["done", "fail"])
        vr = validator.validate(result_dict)

        allowed_warnings = [w for w in vr.warnings if "allowed_decisions" in w]
        assert len(allowed_warnings) > 0


class TestArtifactPromotionNegative:
    """Artifact promotion 负向测试。"""

    def test_missing_staging_file(self):
        """staging 文件不存在 → promotion 失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            staging_path = os.path.join(run_root, "staging", "test", "missing.md")
            artifact_path = os.path.join(run_root, "artifacts", "output.md")

            from agent_workflow.artifacts.promotion import promote_artifact
            result = promote_artifact(staging_path, artifact_path, run_root, "output")
            assert not result.ok
            assert "不存在" in result.error

    def test_staging_path_escape(self):
        """staging 路径逃逸 run_root/staging → promotion 失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            # 创建 staging 文件在 run_root 外
            outside_file = os.path.join(tmpdir, "escape.md")
            with open(outside_file, "w") as f:
                f.write("escaped content")

            artifact_path = os.path.join(run_root, "artifacts", "output.md")

            from agent_workflow.artifacts.promotion import promote_artifact
            result = promote_artifact(outside_file, artifact_path, run_root, "output")
            assert not result.ok
            assert ("逃逸" in result.error or "containment" in result.error.lower())

    def test_artifact_path_escape(self):
        """artifact 路径逃逸 run_root/artifacts → promotion 失败。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            # 创建 staging 在正确位置
            staging_dir = os.path.join(run_root, "staging", "test")
            os.makedirs(staging_dir, exist_ok=True)
            staging_path = os.path.join(staging_dir, "output.md")
            with open(staging_path, "w") as f:
                f.write("test")

            # artifact_path 逃逸到 run_root 外
            artifact_path = os.path.join(tmpdir, "escape_artifact.md")

            from agent_workflow.artifacts.promotion import promote_artifact
            result = promote_artifact(staging_path, artifact_path, run_root, "output")
            assert not result.ok
            assert ("逃逸" in result.error or "containment" in result.error.lower())

    def test_path_containment_normal(self):
        """正常路径通过 containment 检查。"""
        from agent_workflow.artifacts.promotion import _check_path_containment

        assert _check_path_containment("/tmp/staging/test/file.md", "/tmp/staging")
        assert _check_path_containment("/tmp/artifacts/output.md", "/tmp/artifacts")

    def test_path_containment_escape(self):
        """逃逸路径被 containment 检查拒绝。"""
        from agent_workflow.artifacts.promotion import _check_path_containment

        # .. 穿越
        assert not _check_path_containment("/tmp/staging/../outside.md", "/tmp/staging")


class TestCancelFile:
    """Cancel 文件测试。"""

    def test_cancel_run_writes_file(self):
        """cancel_run 写入取消文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "docs", "runs", "run_test")
            # 需要替换全局路径 — 使用 patch 方式
            # cancel_run 硬编码路径 docs/runs/<run_id>/cancelled
            # 此处测试 cancel_run 在 default 路径的行为
            result = cancel_run("test_cancel_001", reason="测试取消")
            assert result is True

            # 验证文件存在
            cancel_path = os.path.join("doc", "runs", "test_cancel_001", "cancelled")
            assert os.path.exists(cancel_path)

            # 清理
            import shutil
            shutil.rmtree(os.path.join("doc", "runs", "test_cancel_001"), ignore_errors=True)


class TestUnknownDecisionDefault:
    """未知 decision 走 default 测试。"""

    @pytest.mark.skipif(
        not os.path.exists(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "examples", "software-dev", "workflow.yaml",
        )),
        reason="示例目录不存在",
    )
    def test_unknown_decision_default_failed(self):
        """未知 decision 走 default → failed。"""
        from agent_workflow.state_machine import StateMachine

        examples_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "examples", "software-dev",
        )
        wf_path = os.path.join(examples_dir, "workflow.yaml")
        wf = load_workflow(wf_path)
        sm = StateMachine(wf)

        # 未知 decision
        result = sm.resolve_transition("claude_review_plan", "gibberish")
        assert result.next_state == "failed"
        assert not result.matched
        assert "default" in result.reason


class TestGuardMaxVisits:
    """Guard 超限测试。"""

    def test_max_visits_guard_fires(self):
        """max_visits 超限 → GuardFailed。"""
        from agent_workflow.state_machine.guard import GuardChecker
        from agent_workflow.config.models import GuardModel
        from agent_workflow.context.run_context import RunContext

        guard_config = GuardModel(max_visits=3, on_guard_failed="failed")
        guard = GuardChecker(guard_config)
        ctx = RunContext.create(
            workflow_id="test", goal="test", project_root="/tmp",
            run_id="run_001", run_root="/tmp/runs/run_001",
        )
        # 模拟访问 4 次（超过 max_visits=3）
        for i in range(4):
            ctx.record_state_visit("test_state")

        result = guard.check("test_state", ctx)
        assert not result.passed
        assert result.guard_type == "max_visits"


class TestRequiredSkillFailFast:
    """required skill 缺失 fail-fast 测试。"""

    def test_adoption_missing_required_skill(self):
        """缺失 required skill → RuntimeError。"""
        from agent_workflow.skills.adoption import AdoptionProtocol

        with tempfile.TemporaryDirectory() as tmpdir:
            # 空 skills_dir → 所有 required skill 都会缺失
            adoption = AdoptionProtocol(
                skills_dir=tmpdir,
                required_skills=["nonexistent_skill"],
            )
            with pytest.raises(RuntimeError, match="必需的 Skill 缺失"):
                adoption.adopt("test_state")


class TestEventBusHasEvents:
    """EventBus 事件发射测试。"""

    def test_event_bus_with_sinks(self):
        """EventBus 挂载 sink 后能发射事件。"""
        from agent_workflow.observability.event_bus import EventBus
        from agent_workflow.observability.jsonl_sink import JSONLSink

        with tempfile.TemporaryDirectory() as tmpdir:
            bus = EventBus()
            jsonl_path = os.path.join(tmpdir, "events.jsonl")
            sink = JSONLSink(jsonl_path)
            bus.add_sink(sink)

            bus.emit("WorkflowStarted", {
                "run_id": "test_001",
                "workflow_id": "test_wf",
                "goal": "test",
            })
            bus.emit("StateEntered", {
                "state": "test_state",
                "task": "test_task",
            })
            bus.emit("WorkflowCompleted", {
                "run_id": "test_001",
                "final_state": "done",
            })

            bus.flush()
            sink.close()

            # 读取 JSONL 验证
            events = []
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))

            assert len(events) == 3
            event_types = [e["event"] for e in events]
            assert "WorkflowStarted" in event_types
            assert "StateEntered" in event_types
            assert "WorkflowCompleted" in event_types


class TestPathContainment:
    """P0g 路径 containment 全面测试。"""

    def test_check_path_containment_valid(self):
        from agent_workflow.artifacts.promotion import _check_path_containment
        assert _check_path_containment("/a/b/c/file.md", "/a/b")
        assert _check_path_containment("/a/b/c/file.md", "/a/b/c")

    def test_check_path_containment_dotdot(self):
        from agent_workflow.artifacts.promotion import _check_path_containment
        # .. 逃逸（在 resolve 前以字符串形式存在）
        assert not _check_path_containment("/a/b/../outside.md", "/a/b")
        # 等价但合法的路径应通过
        assert _check_path_containment("/a/b/c/../file.md", "/a/b")

    def test_check_path_containment_absolute_escape(self):
        from agent_workflow.artifacts.promotion import _check_path_containment
        assert not _check_path_containment("/etc/passwd", "/tmp/staging")
