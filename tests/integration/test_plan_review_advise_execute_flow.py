"""plan-review-advise-execute Mock Flow 集成测试。

验证完整四阶段链路 Plan → Review → Advise → Execute，重点验证
mock decision 脚本驱动的回流分支:
  plan → review(advise) → advise → plan → review(approve) → execute → done

使用 MockAgent + decision_script 替换真实 CLI，验证状态机回流正确性。
"""

import os
import tempfile

import pytest
import yaml

from agent_workflow.config.loader import load_workflow
from agent_workflow.state_machine.runner import Runner


EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows", "plan-review-advise-execute",
)


def _has_examples():
    return os.path.exists(os.path.join(EXAMPLES_DIR, "workflow.yaml"))


def _load_mock_script() -> dict:
    """加载示例目录下的 mock_script.yaml。"""
    path = os.path.join(EXAMPLES_DIR, "mock_script.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("decision_script", data)


def _build_runner(tmpdir: str) -> Runner:
    """构造一个使用真实示例配置 + mock_script 的 Runner。"""
    wf = load_workflow(os.path.join(EXAMPLES_DIR, "workflow.yaml"))

    return Runner(
        wf,
        goal="build a hello world CLI",
        project_root=tmpdir,
        agents=None,  # 不注册真实 agent → 全部 fallback 到 MockAgent
        skills_dir=os.path.join(EXAMPLES_DIR, "skills"),
        mock_script=_load_mock_script(),
    )


@pytest.mark.skipif(not _has_examples(), reason="示例目录不存在")
class TestPlanReviewAdviseExecuteFlow:
    """plan-review-advise-execute 完整 Mock Flow 测试。"""

    def test_load_workflow(self):
        wf = load_workflow(os.path.join(EXAMPLES_DIR, "workflow.yaml"))
        assert wf.name == "plan-review-advise-execute"
        assert wf.initial_state == "plan"
        for s in ("plan", "review", "advise", "execute", "done", "failed"):
            assert s in wf.states

    def test_mock_script_drives_advise_loop(self):
        """验证 mock_script 驱动 review→advise→plan 回流后再 approve。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            final_state = runner.run()

            assert final_state == "done"

            history = runner.context.state_history
            # 完整回流序列：plan → review → advise → plan → review → execute
            # （terminal 状态 done 不计入 state_history）
            assert history == [
                "plan", "review", "advise", "plan", "review", "execute",
            ], f"实际 state_history={history}"

    def test_visit_counts(self):
        """验证回流导致 plan/review 各访问 2 次，advise/execute 各 1 次。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            runner.run()

            assert runner.context.get_attempt("plan") == 2
            assert runner.context.get_attempt("review") == 2
            assert runner.context.get_attempt("advise") == 1
            assert runner.context.get_attempt("execute") == 1

    def test_artifacts_promoted(self):
        """验证四类产物流全部 promote 到 artifacts/。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            runner.run()

            artifacts = runner.context.artifacts
            for name in ("plan_doc", "review_doc", "advice_doc", "execution_report"):
                assert name in artifacts, f"缺少产物流 {name}: {list(artifacts.keys())}"
                assert os.path.exists(artifacts[name]), f"产物文件不存在: {artifacts[name]}"

    def test_review_decisions_sequence(self):
        """验证 review 两次访问的 decision 分别为 advise / approve。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            final_state = runner.run()

            # review 的最后一次 task_result 应为 approve（成功进入 execute）
            review_result = runner.context.task_results.get("review")
            assert review_result is not None
            assert review_result["decision"] == "approve"
            assert final_state == "done"
