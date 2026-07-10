"""测试 artifacts 登记兜底（_backfill_artifact_from_staging）。

根因回归：agent 首次未在 stdout 输出合法 TaskResult 时，引擎兜底成
invalid_output/artifacts=[]，Repair 只允许改 status/decision、禁止改 artifacts，
导致正文产物已落盘 staging 却永远补不回 artifacts 登记 → promote 空转 → 产物丢失
却以 success 蒙混过关。本测试验证 backfill 在 artifacts 为空且产物文件存在时兜底登记。
"""

import os
import shutil
import tempfile
from datetime import datetime, timezone, timedelta

from agent_workflow.config.models import (
    WorkflowConfig, TaskModel, StateModel, GuardModel,
)
from agent_workflow.state_machine.runner import Runner
from agent_workflow.tasks.result import TaskResult, ExecutionMetadata


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


def _make_wf(output="plan_doc", allowed_decisions=None):
    task = TaskModel(
        name="plan",
        instruction="test",
        agent="mock",
        output=output,
        allowed_decisions=allowed_decisions or ["done"],
    )
    state = StateModel(
        name="plan", task="plan", on={"done": "done"}, default="failed",
    )
    return WorkflowConfig(
        name="wf", initial_state="plan", terminal_states=["done", "failed"],
        guards=GuardModel(), tasks={"plan": task}, states={"plan": state},
    )


def _create_runner(wf):
    tmpdir = tempfile.mkdtemp()
    run_root = os.path.join(tmpdir, "runs")
    os.makedirs(run_root, exist_ok=True)
    runner = Runner(wf, goal="t", project_root=tmpdir, run_root=run_root)
    runner._run_id = "run_test"
    runner.start()
    return runner, tmpdir


def _cleanup(runner, tmpdir):
    if runner._jsonl_sink:
        try:
            runner._jsonl_sink.close()
        except Exception:
            pass
    shutil.rmtree(tmpdir, ignore_errors=True)


def _invalid_tr():
    """模拟 Parser 兜底产出：invalid_output + artifacts 为空。"""
    return TaskResult(
        schema_version=1, task_id="plan", state="plan", agent="mock",
        status="invalid_output", decision=None, summary="parse failed",
        execution=ExecutionMetadata(
            started_at=_now_iso(), finished_at=_now_iso(), exit_code=0,
        ),
    )


def _write_staging_output(runner, output_name, content="# doc\n正文"):
    staging_dir = os.path.join(runner.context.staging_root, "staging", "plan")
    os.makedirs(staging_dir, exist_ok=True)
    path = os.path.join(staging_dir, f"{output_name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestArtifactBackfill:

    def test_backfill_when_empty_and_file_exists(self):
        """artifacts=[] 且 staging 产物文件存在 → 自动补登记一条 artifact。"""
        wf = _make_wf(output="plan_doc")
        runner, tmpdir = _create_runner(wf)
        try:
            _write_staging_output(runner, "plan_doc")
            tr = _invalid_tr()
            assert tr.get_artifacts() == []

            runner._validate_task_result(tr, "plan")

            arts = tr.get_artifacts()
            assert len(arts) == 1
            assert arts[0].name == "plan_doc"
            assert arts[0].artifact_path == "artifacts/plan_doc.md"
            assert os.path.exists(arts[0].staging_path)
        finally:
            _cleanup(runner, tmpdir)

    def test_no_backfill_when_file_missing(self):
        """artifacts=[] 但 staging 无产物文件 → 不补登记（不凭空造 artifact）。"""
        wf = _make_wf(output="plan_doc")
        runner, tmpdir = _create_runner(wf)
        try:
            tr = _invalid_tr()
            runner._validate_task_result(tr, "plan")
            assert tr.get_artifacts() == []
        finally:
            _cleanup(runner, tmpdir)

    def test_no_backfill_when_artifacts_present(self):
        """artifacts 非空 → 不触发 backfill（对正常节点零影响）。"""
        wf = _make_wf(output="plan_doc")
        runner, tmpdir = _create_runner(wf)
        try:
            # 即便 staging 里另有 output 文件，也不应因 backfill 多出一条
            _write_staging_output(runner, "plan_doc")
            existing_path = _write_staging_output(runner, "custom_art")
            tr = TaskResult(
                schema_version=1, task_id="plan", state="plan", agent="mock",
                status="success", decision="done", summary="ok",
                artifacts=[{
                    "name": "custom_art",
                    "staging_path": existing_path,
                    "artifact_path": "artifacts/custom_art.md",
                    "type": "markdown",
                }],
                execution=ExecutionMetadata(
                    started_at=_now_iso(), finished_at=_now_iso(), exit_code=0,
                ),
            )
            runner._validate_task_result(tr, "plan")
            arts = tr.get_artifacts()
            assert len(arts) == 1
            assert arts[0].name == "custom_art"
        finally:
            _cleanup(runner, tmpdir)

    def test_backfilled_artifact_promotes(self):
        """backfill 后 promote 能真正落地 artifacts（端到端验证产物不再丢失）。"""
        wf = _make_wf(output="plan_doc")
        runner, tmpdir = _create_runner(wf)
        try:
            _write_staging_output(runner, "plan_doc", content="# 架构\n内容")
            tr = _invalid_tr()
            runner._validate_task_result(tr, "plan")
            runner._promote_artifacts(tr)

            promoted = os.path.join(
                runner.context.run_root, "artifacts", "plan_doc.md"
            )
            assert os.path.exists(promoted), "backfill 的产物应成功 promote 到 artifacts"
        finally:
            _cleanup(runner, tmpdir)
