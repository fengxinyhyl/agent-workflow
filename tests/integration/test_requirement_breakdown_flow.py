"""requirement-breakdown workflow 集成测试。"""

import os
import tempfile

import yaml

from agent_workflow.config.loader import load_workflow
from agent_workflow.state_machine.runner import Runner


EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows",
    "requirement-breakdown",
)


def _load_mock_script() -> dict:
    path = os.path.join(EXAMPLES_DIR, "mock_script.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("decision_script", data)


def _build_runner(tmpdir: str) -> Runner:
    wf = load_workflow(os.path.join(EXAMPLES_DIR, "workflow.yaml"))
    return Runner(
        wf,
        goal="把用户需求拆分成按顺序执行的子需求",
        project_root=tmpdir,
        agents=None,
        skills_dir=os.path.join(EXAMPLES_DIR, "skills"),
        mock_script=_load_mock_script(),
    )


class TestRequirementBreakdownFlow:
    """需求拆分 workflow 的 mock 链路验证。"""

    def test_load_workflow(self):
        wf = load_workflow(os.path.join(EXAMPLES_DIR, "workflow.yaml"))

        assert wf.name == "requirement-breakdown"
        assert wf.initial_state == "understand_requirements"
        assert list(wf.states) == [
            "understand_requirements",
            "review_breakdown",
            "give_advice",
            "done",
            "failed",
            "cancelled",
        ]

    def test_mock_flow_finishes_after_advice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            final_state = runner.run()

            assert final_state == "done"
            assert runner.context.state_history == [
                "understand_requirements",
                "review_breakdown",
                "give_advice",
            ]

    def test_artifacts_promoted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            runner.run()

            artifacts = runner.context.artifacts
            for name in (
                "requirements_plan",
                "breakdown_review",
                "breakdown_advice",
            ):
                assert name in artifacts
                assert os.path.exists(artifacts[name])
