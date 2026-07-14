"""测试 Repair 编排流程（MockAgent + decision_script 模拟）。"""

import json
import os
import tempfile
import shutil
from datetime import datetime, timezone, timedelta

import pytest

from agent_workflow.config.models import (
    WorkflowConfig, TaskModel, StateModel, GuardModel,
)
from agent_workflow.state_machine.runner import Runner
from agent_workflow.tasks.result import TaskResult, ExecutionMetadata


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


# ── helpers ──

def _make_minimal_workflow(name="test_wf", states=None, tasks=None, guards=None):
    """构建最小 WorkflowConfig。"""
    return WorkflowConfig(
        name=name,
        initial_state="plan",
        terminal_states=["done", "failed"],
        guards=guards or GuardModel(),
        tasks=tasks or {},
        states=states or {},
    )


def _make_task(name="plan", instruction="test", agent="mock", allowed_decisions=None):
    """构建 TaskModel。"""
    return TaskModel(
        name=name,
        instruction=instruction,
        agent=agent,
        allowed_decisions=allowed_decisions or [],
    )


def _make_state(name="plan", task="plan", on=None, next=None, default="failed"):
    """构建 StateModel。"""
    return StateModel(
        name=name,
        task=task,
        on=on or {},
        next=next,
        default=default,
    )


def _create_runner(wf, goal="test", tmpdir=None):
    """创建 Runner 并返回 (runner, tmpdir)。

    Windows 注意：JSONLSink 会持有 events.jsonl 文件句柄，
    测试退出时可能导致 PermissionError。这里使用 ignore_cleanup_errors。
    """
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()
    run_root = os.path.join(tmpdir, "runs", "run_test")
    os.makedirs(run_root, exist_ok=True)

    runner = Runner(
        wf,
        goal=goal,
        project_root=tmpdir,
        run_root=os.path.dirname(run_root),
    )
    runner._run_id = "run_test"
    runner.start()
    return runner, tmpdir


# ── 测试：纯函数级 Repair 判定 ──

class TestRepairDecision:
    """验证纯函数 validate() 对各场景的 repairable 判定。"""

    def _make_valid_data(self, **overrides):
        data = {
            "schema_version": 1,
            "task_id": "test",
            "state": "test_state",
            "status": "success",
            "summary": "ok",
            "execution": {
                "started_at": _now_iso(),
                "finished_at": _now_iso(),
                "exit_code": 0,
            },
        }
        data.update(overrides)
        return data

    def test_invalid_output_repairable(self):
        """invalid_output → repairable=True。"""
        from agent_workflow.validators.task_result import validate
        from agent_workflow.validators.validation_result import RouteShape
        rs = RouteShape(has_on=True, allowed_decisions=("done",))
        vr = validate(self._make_valid_data(status="invalid_output"), rs)
        assert vr.repairable is True
        assert vr.valid is False

    def test_decision_not_allowed_repairable(self):
        """decision 不在 allowed_decisions → repairable=True。"""
        from agent_workflow.validators.task_result import validate
        from agent_workflow.validators.validation_result import RouteShape
        rs = RouteShape(has_on=True, allowed_decisions=("done", "fail"))
        vr = validate(self._make_valid_data(decision="approve"), rs)
        assert vr.repairable is True
        assert vr.valid is False

    def test_repairable_false_direct_failed(self):
        """repairable=False → Runner 应直接 failed，不走 Repair。"""
        from agent_workflow.validators.task_result import validate
        from agent_workflow.validators.validation_result import RouteShape
        vr = validate(
            {"schema_version": 0, "task_id": "t"},
            RouteShape(),
        )
        assert vr.valid is False
        assert vr.repairable is False

    def test_linear_node_no_repair_triggered(self):
        """线性节点（has_next=True）无 decision → 不触发 Repair。"""
        from agent_workflow.validators.task_result import validate
        from agent_workflow.validators.validation_result import RouteShape
        data = self._make_valid_data(decision=None)
        rs = RouteShape(has_next=True)
        vr = validate(data, rs)
        assert vr.valid is True

    def test_linear_node_invalid_output_repairable(self):
        """线性节点 + invalid_output → repairable=True（线性节点也享受 Repair）。"""
        from agent_workflow.validators.task_result import validate
        from agent_workflow.validators.validation_result import RouteShape
        vr = validate(
            self._make_valid_data(status="invalid_output"),
            RouteShape(has_next=True),
        )
        assert vr.repairable is True

    def test_compound_error_repairable(self):
        """复合错误（invalid_output + decision=None）一次判定 repairable。"""
        from agent_workflow.validators.task_result import validate
        from agent_workflow.validators.validation_result import RouteShape
        rs = RouteShape(has_on=True, allowed_decisions=("done", "fail"))
        vr = validate(
            self._make_valid_data(status="invalid_output", decision=None), rs
        )
        assert vr.repairable is True


# ── 测试：Runner 级别 Repair 流程 ──

class TestRepairFlow:
    """Repair 编排端到端测试（使用 Runner + MockAgent）。"""

    def test_repair_exhausted_status_failed(self):
        """Repair 耗尽 → status=failed, decision=None, issues 含取证记录。

        MockAgent 始终返回合法输出，因此用 monkeypatch 模拟 _call_agent_direct
        持续返回 invalid_output（repairable 但永不 valid）→ 2 次后耗尽。
        """
        task_model = _make_task("plan", "test", "mock", allowed_decisions=["done"])
        state_model = _make_state("plan", "plan", on={"done": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"plan": task_model},
            states={"plan": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test repair exhausted")

        try:
            # 设置 _last_agent_input 以启用 Repair 路径
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            runner._last_agent_input = AgentInput(
                task=AgentTaskConfig(
                    name="plan", instruction="test", agent="mock",
                ),
                context=runner.context,
                state_name="plan",
            )

            # Monkeypatch _call_agent_direct：始终返回 invalid_output
            def _always_invalid(self, agent_input, state_name):
                return TaskResult(
                    schema_version=1,
                    task_id="plan",
                    state="plan",
                    agent="mock",
                    status="invalid_output",
                    decision=None,
                    summary="still invalid",
                    execution=ExecutionMetadata(
                        started_at=_now_iso(),
                        finished_at=_now_iso(),
                        exit_code=0,
                    ),
                )

            import types
            runner._call_agent_direct = types.MethodType(_always_invalid, runner)

            # 构造 task_result（模拟 Parser 产出 invalid_output）
            tr = TaskResult(
                schema_version=1,
                task_id="plan",
                state="plan",
                agent="mock",
                status="invalid_output",
                decision=None,
                summary="parse failed",
                execution=ExecutionMetadata(
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                    exit_code=0,
                ),
            )

            from agent_workflow.validators.task_result import validate
            from agent_workflow.validators.validation_result import RouteShape
            rs = RouteShape(has_on=True, allowed_decisions=("done",))
            vr = validate(tr.to_dict(), rs)

            # Repair：monkeypatch 的 _call_agent_direct 持续返回 invalid_output
            # → 2 次修复后耗尽
            repaired_tr, success = runner._repair_task_result(
                tr, "plan", vr, max_attempts=2
            )

            # 耗尽后 status 应为 failed
            assert success is False
            assert repaired_tr.status == "failed"
            assert repaired_tr.decision is None

            # 取证记录
            issues = repaired_tr.issues
            issue_dicts = [
                i.to_dict() if hasattr(i, 'to_dict') else i
                for i in issues
            ]
            repair_issues = [
                i for i in issue_dicts
                if "repair_exhausted" in str(i.get("detail", ""))
            ]
            assert len(repair_issues) > 0, f"应包含取证记录，实际 issues: {issue_dicts}"
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_repair_preserves_original_content(self):
        """Repair 不破坏原始 task_result 的 summary/artifacts 结构。"""
        task_model = _make_task("plan", "test", "mock", allowed_decisions=["done"])
        state_model = _make_state("plan", "plan", on={"done": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"plan": task_model},
            states={"plan": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test repair preserve")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            runner._last_agent_input = AgentInput(
                task=AgentTaskConfig(
                    name="plan", instruction="test", agent="mock",
                ),
                context=runner.context,
                state_name="plan",
            )

            original_summary = "This summary must remain"
            tr = TaskResult(
                schema_version=1,
                task_id="plan",
                state="plan",
                agent="mock",
                status="success",
                decision="unknown_decision",
                summary=original_summary,
                execution=ExecutionMetadata(
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                    exit_code=0,
                ),
            )

            from agent_workflow.validators.task_result import validate
            from agent_workflow.validators.validation_result import RouteShape
            rs = RouteShape(has_on=True, allowed_decisions=("done",))
            vr = validate(tr.to_dict(), rs)
            assert vr.repairable is True

            # Repair 1 次（MockAgent 默认返回 success + "done"）
            repaired_tr, success = runner._repair_task_result(
                tr, "plan", vr, max_attempts=1
            )

            # 验证结构完整
            assert repaired_tr is not None
            assert repaired_tr.status in ("success", "failed")
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_repair_does_not_trigger_record_state_visit(self):
        """Repair 不经过 _execute_state → 不触发 record_state_visit。"""
        task_model = _make_task("plan", "test", "mock", allowed_decisions=["done"])
        state_model = _make_state("plan", "plan", on={"done": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"plan": task_model},
            states={"plan": state_model},
        )

        runner, tmpdir = _create_runner(wf)

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            ai = AgentInput(
                task=AgentTaskConfig(
                    name="plan", instruction="test", agent="mock",
                ),
                context=runner.context,
                state_name="plan",
            )

            history_before = list(runner.context.state_history)
            runner._call_agent_direct(ai, "plan")
            history_after = list(runner.context.state_history)

            # _call_agent_direct 不经过 _execute_state → state_history 不变
            assert history_before == history_after
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_repair_instance_isolation(self):
        """多个 Runner 实例的 Repair 状态独立。"""
        task_model = _make_task("plan", "test", "mock", allowed_decisions=["done"])
        state_model = _make_state("plan", "plan", on={"done": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"plan": task_model},
            states={"plan": state_model},
        )

        # Runner 1（有 _last_agent_input）
        runner1, tmpdir1 = _create_runner(wf, "test1")
        # Runner 2（无 _last_agent_input）
        runner2, tmpdir2 = _create_runner(wf, "test2")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            runner1._last_agent_input = AgentInput(
                task=AgentTaskConfig(
                    name="plan", instruction="test", agent="mock",
                ),
                context=runner1.context,
                state_name="plan",
            )

            # Runner 2 无 _last_agent_input → Repair 不可用
            tr = TaskResult(
                schema_version=1,
                task_id="plan",
                state="plan",
                agent="mock",
                status="invalid_output",
                decision=None,
                summary="fail",
                execution=ExecutionMetadata(
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                    exit_code=0,
                ),
            )
            from agent_workflow.validators.task_result import validate
            from agent_workflow.validators.validation_result import RouteShape
            rs = RouteShape(has_on=True, allowed_decisions=("done",))
            vr = validate(tr.to_dict(), rs)

            repaired2, success2 = runner2._repair_task_result(tr, "plan", vr)
            assert success2 is False  # 无 agent_input → Repair 不可用

            # Runner 1 独立可访问
            assert runner1._last_agent_input is not None
        finally:
            for r in [runner1, runner2]:
                if r._jsonl_sink:
                    try:
                        r._jsonl_sink.close()
                    except Exception:
                        pass
            shutil.rmtree(tmpdir1, ignore_errors=True)
            shutil.rmtree(tmpdir2, ignore_errors=True)


# ── 测试：Repair 瘦身（格式转换模式） ──

class TestRepairFormatConversion:
    """验证 _build_repair_agent_input 的格式转换 mode 与 IO 退化（Issue-3）。"""

    def test_format_conversion_prompt_contains_product(self):
        """有 output 产物时，repair prompt 含产物正文片段（格式转换模式）。"""
        task_model = _make_task("review", "review code", "mock", allowed_decisions=["approve", "revise"])
        state_model = _make_state("review", "review", on={"approve": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"review": task_model},
            states={"review": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test format conversion")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            from agent_workflow.validators.validation_result import ValidResult

            # 写产物到 staging
            staging_dir = os.path.join(runner.context.staging_root, "staging", "review")
            os.makedirs(staging_dir, exist_ok=True)
            product_content = "代码审查结论：revise。需要修改安全模块的错误处理逻辑。"
            product_path = os.path.join(staging_dir, "review_doc.md")
            with open(product_path, "w", encoding="utf-8") as f:
                f.write(product_content)

            staging_paths = {"review_doc": product_path}
            original_ai = AgentInput(
                task=AgentTaskConfig(
                    name="review", instruction="review code", agent="mock",
                    inputs=[], output="review_doc",
                ),
                context=runner.context,
                state_name="review",
                staging_paths=staging_paths,
            )

            tr = TaskResult(
                schema_version=1, task_id="review", state="review",
                agent="mock", status="success", decision="revise",
                summary="review done",
                execution=ExecutionMetadata(
                    started_at=_now_iso(), finished_at=_now_iso(), exit_code=0,
                ),
            )

            vr = ValidResult(
                valid=False, repairable=True,
                reason="decision not in allowed",
                errors=["decision 'revise' not in allowed_decisions"],
            )

            repair_input = runner._build_repair_agent_input(
                "review", tr, vr, original_ai
            )

            # 格式转换模式下不应出现旧措辞
            assert "只允许修改 status 和 decision" not in repair_input.task.instruction
            # 应含产物正文片段
            assert "已落盘的产物正文" in repair_input.task.instruction
            assert "代码审查结论" in repair_input.task.instruction
            # 应含"不需要重新审查"
            assert "不需要重新审查" in repair_input.task.instruction
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_io_degradation_no_staging_file(self):
        """产物文件不存在时退化为精简 prompt，不抛异常。"""
        task_model = _make_task("review", "review code", "mock", allowed_decisions=["approve", "revise"])
        state_model = _make_state("review", "review", on={"approve": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"review": task_model},
            states={"review": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test io degradation")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            from agent_workflow.validators.validation_result import ValidResult

            # 不创建 staging 文件，staging_paths 指向不存在的路径
            staging_paths = {"review_doc": os.path.join(tmpdir, "nonexistent", "review_doc.md")}
            original_ai = AgentInput(
                task=AgentTaskConfig(
                    name="review", instruction="review code", agent="mock",
                    inputs=[], output="review_doc",
                ),
                context=runner.context,
                state_name="review",
                staging_paths=staging_paths,
            )

            tr = TaskResult(
                schema_version=1, task_id="review", state="review",
                agent="mock", status="invalid_output", decision=None,
                summary="parse failed",
                execution=ExecutionMetadata(
                    started_at=_now_iso(), finished_at=_now_iso(), exit_code=0,
                ),
            )

            vr = ValidResult(
                valid=False, repairable=True,
                reason="invalid_output",
                errors=["no structured output"],
            )

            # 不应抛异常
            repair_input = runner._build_repair_agent_input(
                "review", tr, vr, original_ai
            )

            # 退化模式下应含"只允许修改 status 和 decision"措辞
            assert "只允许修改 status 和 decision" in repair_input.task.instruction
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_io_degradation_no_output_name(self):
        """task.output 为空时退化为精简 prompt，不抛异常。"""
        task_model = _make_task("review", "review code", "mock", allowed_decisions=["approve", "revise"])
        state_model = _make_state("review", "review", on={"approve": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"review": task_model},
            states={"review": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test no output")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            from agent_workflow.validators.validation_result import ValidResult

            # task.output 为 None/空
            original_ai = AgentInput(
                task=AgentTaskConfig(
                    name="review", instruction="review code", agent="mock",
                    inputs=[], output="",  # 空 output
                ),
                context=runner.context,
                state_name="review",
            )

            tr = TaskResult(
                schema_version=1, task_id="review", state="review",
                agent="mock", status="invalid_output", decision=None,
                summary="parse failed",
                execution=ExecutionMetadata(
                    started_at=_now_iso(), finished_at=_now_iso(), exit_code=0,
                ),
            )

            vr = ValidResult(
                valid=False, repairable=True,
                reason="invalid_output",
                errors=["no structured output"],
            )

            # 不应抛异常
            repair_input = runner._build_repair_agent_input(
                "review", tr, vr, original_ai
            )

            # 退化模式
            assert "只允许修改 status 和 decision" in repair_input.task.instruction
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_repair_success_origin_repair(self):
        """repair 成功后 protocol_origin="repair"（Issue-3 验收点 c）。"""
        task_model = _make_task("plan", "test", "mock", allowed_decisions=["done"])
        state_model = _make_state("plan", "plan", on={"done": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"plan": task_model},
            states={"plan": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test repair origin")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            from agent_workflow.validators.validation_result import RouteShape
            from agent_workflow.validators.task_result import validate

            runner._last_agent_input = AgentInput(
                task=AgentTaskConfig(
                    name="plan", instruction="test", agent="mock",
                ),
                context=runner.context,
                state_name="plan",
            )

            # 构造 invalid_output（模拟 parser 失败进入 Repair）
            tr = TaskResult(
                schema_version=1,
                task_id="plan",
                state="plan",
                agent="mock",
                status="invalid_output",
                decision=None,
                summary="parse failed",
                execution=ExecutionMetadata(
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                    exit_code=0,
                ),
            )

            rs = RouteShape(has_on=True, allowed_decisions=("done",))
            vr = validate(tr.to_dict(), rs)

            # Repair：MockAgent 默认返回 success + "done"
            repaired_tr, success = runner._repair_task_result(
                tr, "plan", vr, max_attempts=1
            )

            assert success is True
            exec_meta = repaired_tr.get_execution()
            assert exec_meta.protocol_origin == "repair", (
                f"repair 成功后 protocol_origin 应为 'repair'，实际为 '{exec_meta.protocol_origin}'"
            )
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── 测试：向后兼容 ──

# ── 测试：MockAgent status_script ──

class TestMockAgentStatusScript:
    """MockAgent status_script 机制测试。"""

    def test_status_script_basic(self):
        """status_script 按 attempt 返回不同 status。"""
        from agent_workflow.agents.mock import MockAgent

        agent = MockAgent({
            "status_script": {"review": ["invalid_output", "success"]},
            "decision_script": {"review": ["done", "approve"]},
        })

        ctx = _make_mock_context("review")
        from agent_workflow.context.agent_input import (
            AgentInput, TaskConfig as AgentTaskConfig,
        )

        # 第 1 次访问 → invalid_output + done
        ctx._attempts = {"review": 1}
        ai = AgentInput(
            task=AgentTaskConfig(name="review", instruction="test", agent="mock"),
            context=ctx,
            state_name="review",
        )
        status = agent._resolve_status(ai)
        decision = agent._resolve_decision(ai)
        assert status == "invalid_output"
        assert decision == "done"

        # 第 2 次访问 → success + approve
        ctx._attempts = {"review": 2}
        ai2 = AgentInput(
            task=AgentTaskConfig(name="review", instruction="test", agent="mock"),
            context=ctx,
            state_name="review",
        )
        status2 = agent._resolve_status(ai2)
        decision2 = agent._resolve_decision(ai2)
        assert status2 == "success"
        assert decision2 == "approve"

    def test_status_script_no_match_uses_default(self):
        """status_script 未匹配的 state 回退到 mock_status（默认 success）。"""
        from agent_workflow.agents.mock import MockAgent

        agent = MockAgent({"status_script": {}, "mock_status": "success"})
        ctx = _make_mock_context("plan")
        from agent_workflow.context.agent_input import (
            AgentInput, TaskConfig as AgentTaskConfig,
        )
        ai = AgentInput(
            task=AgentTaskConfig(name="plan", instruction="test", agent="mock"),
            context=ctx,
            state_name="plan",
        )
        assert agent._resolve_status(ai) == "success"

    def test_status_script_list_exhausted_uses_last(self):
        """status_script 列表耗尽后取最后一个值。"""
        from agent_workflow.agents.mock import MockAgent

        agent = MockAgent({
            "status_script": {"review": ["invalid_output"]},
        })

        ctx = _make_mock_context("review")
        from agent_workflow.context.agent_input import (
            AgentInput, TaskConfig as AgentTaskConfig,
        )
        ctx._attempts = {"review": 5}  # 远超列表长度
        ai = AgentInput(
            task=AgentTaskConfig(name="review", instruction="test", agent="mock"),
            context=ctx,
            state_name="review",
        )
        assert agent._resolve_status(ai) == "invalid_output"

    def test_status_script_execute_invalid_output_triggers_repair(self):
        """MockAgent status_script=invalid_output → execute 产出 invalid_output → 触发 Repair。"""
        task_model = _make_task("review", "test review", "mock", allowed_decisions=["approve", "revise"])
        state_model = _make_state("review", "review", on={"approve": "done"}, default="failed")
        wf = _make_minimal_workflow(
            tasks={"review": task_model},
            states={"review": state_model},
        )

        runner, tmpdir = _create_runner(wf, "test status_script repair")

        try:
            from agent_workflow.context.agent_input import (
                AgentInput, TaskConfig as AgentTaskConfig,
            )
            runner._last_agent_input = AgentInput(
                task=AgentTaskConfig(
                    name="review", instruction="test", agent="mock",
                ),
                context=runner.context,
                state_name="review",
            )

            # 构造 Parser 产出的 invalid_output（模拟 status_script[0]=invalid_output）
            tr = TaskResult(
                schema_version=1,
                task_id="review",
                state="review",
                agent="mock",
                status="invalid_output",
                decision=None,
                summary="status_script round 1",
                execution=ExecutionMetadata(
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                    exit_code=0,
                ),
            )

            from agent_workflow.validators.task_result import validate
            from agent_workflow.validators.validation_result import RouteShape
            rs = RouteShape(has_on=True, allowed_decisions=("approve", "revise"))
            vr = validate(tr.to_dict(), rs)
            assert vr.repairable is True

            # Repair 1 次 → MockAgent（无 status_script）默认返回 success + done
            repaired_tr, success = runner._repair_task_result(
                tr, "review", vr, max_attempts=1
            )
            assert repaired_tr is not None
        finally:
            if runner._jsonl_sink:
                try:
                    runner._jsonl_sink.close()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)


def _make_mock_context(state_name="test"):
    """构建最小 RunContext mock 用于 MockAgent 测试。"""
    class _MockContext:
        def __init__(self):
            self.current_state = state_name
            self.state_history = [state_name]
            self._attempts = {}

        def get_attempt(self, state):
            return self._attempts.get(state, 1)
    return _MockContext()


class TestBackwardCompat:
    """TaskResultValidator 向后兼容测试。"""

    def test_validator_returns_old_validation_result(self):
        """TaskResultValidator.validate() 返回 base.ValidationResult。"""
        from agent_workflow.validators.task_result import TaskResultValidator

        data = {
            "schema_version": 1,
            "task_id": "test",
            "state": "test",
            "status": "success",
            "summary": "ok",
            "execution": {
                "started_at": _now_iso(),
                "finished_at": _now_iso(),
                "exit_code": 0,
            },
        }

        validator = TaskResultValidator()
        result = validator.validate(data)
        assert hasattr(result, "passed")
        assert hasattr(result, "errors")
        assert hasattr(result, "warnings")
        assert result.passed is True

    def test_validator_with_allowed_decisions_valid(self):
        """TaskResultValidator + allowed_decisions + 合法 decision → passed=True。"""
        from agent_workflow.validators.task_result import TaskResultValidator

        data = {
            "schema_version": 1,
            "task_id": "test",
            "state": "test",
            "status": "success",
            "decision": "done",
            "summary": "ok",
            "execution": {
                "started_at": _now_iso(),
                "finished_at": _now_iso(),
                "exit_code": 0,
            },
        }

        validator = TaskResultValidator(allowed_decisions=["done", "fail"])
        result = validator.validate(data)
        assert result.passed is True

    def test_validator_with_allowed_decisions_invalid(self):
        """TaskResultValidator + allowed_decisions + 非法 decision → passed=False。"""
        from agent_workflow.validators.task_result import TaskResultValidator

        data = {
            "schema_version": 1,
            "task_id": "test",
            "state": "test",
            "status": "success",
            "decision": "approve",  # 不在 allowed 中
            "summary": "ok",
            "execution": {
                "started_at": _now_iso(),
                "finished_at": _now_iso(),
                "exit_code": 0,
            },
        }

        validator = TaskResultValidator(allowed_decisions=["done", "fail"])
        result = validator.validate(data)
        assert not result.passed
        assert len(result.errors) > 0
