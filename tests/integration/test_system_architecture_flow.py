"""system-architecture 工作流集成测试。"""

from __future__ import annotations

import os
import tempfile

import yaml

from agent_workflow.config.loader import load_agents_config, load_workflow
from agent_workflow.state_machine import StateMachine
from agent_workflow.state_machine.runner import Runner


WORKFLOW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows",
    "system-architecture",
)


def _workflow_path() -> str:
    return os.path.join(WORKFLOW_DIR, "workflow.yaml")


def _agents_path() -> str:
    return os.path.join(WORKFLOW_DIR, "agents.yaml")


def _load_mock_script() -> dict:
    path = os.path.join(WORKFLOW_DIR, "mock_script.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("decision_script", data)


def _build_workflow():
    return load_workflow(_workflow_path())


class TestSystemArchitectureFlow:
    """系统架构 workflow 的 mock 链路验证。"""

    def test_load_workflow_shape(self):
        wf = _build_workflow()

        assert wf.name == "system-architecture"
        assert wf.initial_state == "extract_drivers"
        assert list(wf.states) == [
            "extract_drivers",
            "structure_constraints_objectives",
            "draft_architecture",
            "evaluation_gate",
            "conflict_revision",
            "architecture_freeze",
            "done",
            "failed",
            "cancelled",
        ]
        assert wf.guards.max_visits == 3
        assert all(not state_name.endswith(("_r1", "_r2")) for state_name in wf.states)

    def test_review_decisions_drive_transitions(self):
        sm = StateMachine(_build_workflow())

        assert (
            sm.resolve_transition("extract_drivers", "done").next_state
            == "structure_constraints_objectives"
        )
        assert (
            sm.resolve_transition("structure_constraints_objectives", "done").next_state
            == "draft_architecture"
        )
        assert sm.resolve_transition("draft_architecture", "done").next_state == "evaluation_gate"
        assert sm.resolve_transition("evaluation_gate", "approve").next_state == "architecture_freeze"
        assert sm.resolve_transition("evaluation_gate", "revise").next_state == "conflict_revision"
        assert sm.resolve_transition("evaluation_gate", "reject").next_state == "failed"
        assert sm.resolve_transition("conflict_revision", "done").next_state == "evaluation_gate"
        assert sm.resolve_transition("architecture_freeze", "done").next_state == "done"

    def test_review_task_allows_review_decisions(self):
        wf = _build_workflow()

        assert wf.tasks["evaluation_gate"].allowed_decisions == [
            "approve",
            "revise",
            "reject",
            "fail",
            "blocked",
        ]
        assert wf.tasks["draft_architecture"].version_strategy == "increment"
        assert wf.tasks["conflict_revision"].version_strategy == "increment"
        assert wf.tasks["structure_constraints_objectives"].output == "constraints_objectives"
        assert wf.tasks["architecture_freeze"].output == "final_architecture"

    def test_mock_flow_revises_once_then_finishes(self):
        wf = _build_workflow()

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = Runner(
                workflow=wf,
                goal="根据 final_requirement 设计一个活动运营系统架构",
                project_root=tmpdir,
                skills_dir=os.path.join(WORKFLOW_DIR, "skills"),
                mock_script=_load_mock_script(),
            )
            runner.start()
            final_state = runner.run()

            assert final_state == "done"
            assert runner.context.state_history == [
                "extract_drivers",
                "structure_constraints_objectives",
                "draft_architecture",
                "evaluation_gate",
                "conflict_revision",
                "evaluation_gate",
                "architecture_freeze",
            ]
            assert runner.context.get_attempt("evaluation_gate") == 2

            for artifact_name in (
                "architecture_drivers",
                "constraints_objectives",
                "architecture_draft",
                "evaluation_report",
                "conflict_revision_doc",
                "final_architecture",
            ):
                assert artifact_name in runner.context.artifacts
                assert os.path.exists(runner.context.artifacts[artifact_name])

    def test_agents_permissions_match_node_responsibilities(self):
        agents = load_agents_config(_agents_path())

        assert agents["cc-opus"].allowed_tools == "Read,Grep,Glob,Write"
        assert agents["cc-deepseek"].allowed_tools == "Read,Grep,Glob,Write"
        assert agents["codex"].sandbox == "workspace-write"
