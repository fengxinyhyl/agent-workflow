"""standard-dev 工作流集成测试。"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent_workflow.config.loader import load_workflow
from agent_workflow.state_machine import StateMachine
from agent_workflow.state_machine.runner import Runner


EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows",
    "standard-dev",
)


def _has_examples() -> bool:
    return os.path.exists(os.path.join(EXAMPLES_DIR, "workflow.yaml"))


def _build_workflow():
    return load_workflow(os.path.join(EXAMPLES_DIR, "workflow.yaml"))


@pytest.mark.skipif(not _has_examples(), reason="standard-dev 示例目录不存在")
class TestStandardDevFlow:
    def test_load_and_validate(self):
        wf = _build_workflow()
        assert wf.name == "standard-dev"
        assert wf.initial_state == "plan"
        for state in (
            "plan",
            "review",
            "adoption",
            "implement",
            "code_audit",
            "unit_test",
            "summary",
        ):
            assert state in wf.states

        issues = wf.validate()
        assert issues == []

        sm = StateMachine(wf)
        assert sm.validate() == []

    def test_task_specific_skills_loaded(self):
        wf = _build_workflow()
        assert wf.tasks["plan"].skills == ["dev-plan"]
        assert wf.tasks["review"].skills == ["dev-review"]
        assert wf.tasks["adoption"].skills == ["review-adoption"]
        assert wf.tasks["implement"].skills == ["code-implementation"]
        assert wf.tasks["code_audit"].skills == ["code-audit"]
        assert wf.tasks["unit_test"].skills == ["unit-test"]
        assert wf.tasks["summary"].skills == ["workflow-summary"]

    def test_transition_chain(self):
        sm = StateMachine(_build_workflow())

        assert sm.resolve_transition("plan", "done").next_state == "review"
        assert sm.resolve_transition("review", "done").next_state == "adoption"
        assert sm.resolve_transition("adoption", "approve").next_state == "implement"
        assert sm.resolve_transition("adoption", "revise").next_state == "plan"
        assert sm.resolve_transition("implement", "done").next_state == "code_audit"
        assert sm.resolve_transition("code_audit", "approve").next_state == "unit_test"
        assert sm.resolve_transition("code_audit", "revise").next_state == "implement"
        assert sm.resolve_transition("unit_test", "approve").next_state == "summary"
        assert sm.resolve_transition("unit_test", "revise").next_state == "implement"
        assert sm.resolve_transition("summary", "done").next_state == "done"

    def test_mock_happy_path(self):
        wf = _build_workflow()
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                goal="实现一个 hello world CLI",
                project_root=tmpdir,
                skills_dir=os.path.join(EXAMPLES_DIR, "skills"),
                mock_script={
                    "plan": ["done"],
                    "review": ["done"],
                    "adoption": ["approve"],
                    "implement": ["done"],
                    "code_audit": ["approve"],
                    "unit_test": ["approve"],
                    "summary": ["done"],
                },
            )
            runner.start()
            final_state = runner.run()

            assert final_state == "done"
            assert runner.context.state_history == [
                "plan",
                "review",
                "adoption",
                "implement",
                "code_audit",
                "unit_test",
                "summary",
            ]
            for artifact_name in (
                "plan_doc",
                "review_doc",
                "adoption_doc",
                "implementation_report",
                "code_audit_report",
                "test_report",
                "summary_report",
            ):
                assert artifact_name in runner.context.artifacts

            assert "skill_adoption:unit_test" in runner.context.artifacts
