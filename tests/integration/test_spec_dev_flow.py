"""spec-dev 条件回流工作流集成测试。"""

from __future__ import annotations

import os
import tempfile

from agent_workflow.config.loader import load_agents_config, load_workflow
from agent_workflow.state_machine import StateMachine
from agent_workflow.state_machine.runner import Runner


WORKFLOW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows",
    "spec-dev",
)


def _workflow_path() -> str:
    return os.path.join(WORKFLOW_DIR, "workflow.yaml")


def _agents_path() -> str:
    return os.path.join(WORKFLOW_DIR, "agents.yaml")


def _build_workflow():
    return load_workflow(_workflow_path())


class TestSpecDevFlow:
    """spec-dev v2 应是条件回流，而不是固定双轮展开。"""

    def test_loads_as_conditional_feedback_workflow(self):
        wf = _build_workflow()

        assert wf.name == "spec-dev"
        assert wf.initial_state == "planning"
        assert list(wf.states) == [
            "planning",
            "plan_review",
            "plan_refinement",
            "execution",
            "output_review",
            "output_refinement",
            "validation",
            "retrospective",
            "done",
            "failed",
            "cancelled",
        ]

        assert all(not state_name.endswith(("_r1", "_r2")) for state_name in wf.states)

    def test_review_decisions_drive_transitions(self):
        sm = StateMachine(_build_workflow())

        assert sm.resolve_transition("planning", "success", "done").next_state == "plan_review"
        assert sm.resolve_transition("plan_review", "success", "approve").next_state == "execution"
        assert sm.resolve_transition("plan_review", "success", "revise").next_state == "plan_refinement"
        assert sm.resolve_transition("plan_review", "success", "reject").next_state == "failed"
        assert sm.resolve_transition("plan_refinement", "success", "done").next_state == "plan_review"

        assert sm.resolve_transition("execution", "success", "done").next_state == "output_review"
        assert sm.resolve_transition("output_review", "success", "approve").next_state == "validation"
        assert sm.resolve_transition("output_review", "success", "revise").next_state == "output_refinement"
        assert sm.resolve_transition("output_review", "success", "reject").next_state == "failed"
        assert sm.resolve_transition("output_refinement", "success", "done").next_state == "output_review"

        assert sm.resolve_transition("validation", "success", "approve").next_state == "retrospective"
        assert sm.resolve_transition("validation", "success", "revise").next_state == "output_refinement"

    def test_review_tasks_allow_approve_revise_reject(self):
        wf = _build_workflow()

        assert wf.tasks["plan_review"].allowed_decisions == [
            "approve",
            "revise",
            "reject",
            "fail",
            "blocked",
        ]
        assert wf.tasks["output_review"].allowed_decisions == [
            "approve",
            "revise",
            "reject",
            "fail",
            "blocked",
        ]

    def test_mock_script_can_revise_once_then_approve(self):
        wf = _build_workflow()

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                goal="实现一个带测试的 hello world CLI",
                project_root=tmpdir,
                skills_dir=os.path.join(WORKFLOW_DIR, "skills"),
                mock_script={
                    "planning": ["done"],
                    "plan_review": ["revise", "approve"],
                    "plan_refinement": ["done"],
                    "execution": ["done"],
                    "output_review": ["revise", "approve"],
                    "output_refinement": ["done"],
                    "validation": ["approve"],
                    "retrospective": ["done"],
                },
            )
            runner.start()
            final_state = runner.run()

            assert final_state == "done"
            assert runner.context.state_history == [
                "planning",
                "plan_review",
                "plan_refinement",
                "plan_review",
                "execution",
                "output_review",
                "output_refinement",
                "output_review",
                "validation",
                "retrospective",
            ]

            assert runner.context.get_attempt("plan_review") == 2
            assert runner.context.get_attempt("output_review") == 2

            for artifact_name in (
                "plan_doc",
                "plan_review_doc",
                "plan_refinement_doc",
                "execution_report",
                "output_review_doc",
                "output_refinement_doc",
                "test_report",
                "summary_report",
            ):
                assert artifact_name in runner.context.artifacts

    def test_agents_permissions_match_node_responsibilities(self):
        agents = load_agents_config(_agents_path())

        assert agents["cc-opus"].allowed_tools == "Read,Grep,Glob,Write"
        assert agents["cc-deepseek"].allowed_tools == "Read,Grep,Glob,Write"
        assert agents["codex"].sandbox == "workspace-write"
