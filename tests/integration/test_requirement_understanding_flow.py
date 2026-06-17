"""requirement-understanding workflow 集成测试。"""

import os
import tempfile

import yaml

from agent_workflow.cli import build_parser
from agent_workflow.config.loader import load_workflow
from agent_workflow.state_machine.runner import Runner


WORKFLOW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "workflows",
    "requirement-understanding",
)


def _load_mock_script() -> dict:
    path = os.path.join(WORKFLOW_DIR, "mock_script.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("decision_script", data)


def _build_runner(tmpdir: str) -> Runner:
    wf = load_workflow(os.path.join(WORKFLOW_DIR, "workflow.yaml"))
    return Runner(
        wf,
        goal="理解一个产品运营需求，不做技术方案建议",
        project_root=tmpdir,
        agents=None,
        skills_dir=os.path.join(WORKFLOW_DIR, "skills"),
        mock_script=_load_mock_script(),
    )


class TestRequirementUnderstandingFlow:
    """纯需求理解 workflow 的 mock 链路验证。"""

    def test_load_workflow_shape(self):
        wf = load_workflow(os.path.join(WORKFLOW_DIR, "workflow.yaml"))

        assert wf.name == "requirement-understanding"
        assert wf.initial_state == "understand_deepseek"
        assert list(wf.states) == [
            "understand_deepseek",
            "understand_claude",
            "understand_codex",
            "review_by_claude",
            "review_by_codex",
            "review_by_deepseek",
            "combine_consensus",
            "generate_clarification_questions",
            "human_clarification_gate",
            "final_requirement_synthesis",
            "done",
            "failed",
            "cancelled",
        ]
        assert wf.states["human_clarification_gate"].gate is True
        assert wf.states["human_clarification_gate"].on["approve"] == "final_requirement_synthesis"
        assert wf.states["human_clarification_gate"].on["reject"] == "failed"
        assert "advice" not in "\n".join(wf.tasks)

    def test_mock_flow_pauses_at_human_clarification_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            final_state = runner.run()

            assert final_state == "human_clarification_gate"
            assert runner.context.workflow_variables["_run_status"] == "waiting_human_approval"
            assert runner.context.workflow_variables["_paused_at_gate"] == "human_clarification_gate"
            assert runner.context.state_history == [
                "understand_deepseek",
                "understand_claude",
                "understand_codex",
                "review_by_claude",
                "review_by_codex",
                "review_by_deepseek",
                "combine_consensus",
                "generate_clarification_questions",
                "human_clarification_gate",
            ]

    def test_artifacts_promoted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            runner.start()
            runner.run()

            artifacts = runner.context.artifacts
            for name in (
                "understanding_deepseek",
                "understanding_claude",
                "understanding_codex",
                "review_claude",
                "review_codex",
                "review_deepseek",
                "consensus_report",
                "clarification_questions",
                "human_clarification_request",
            ):
                assert name in artifacts
                assert os.path.exists(artifacts[name])

    def test_continue_command_exists(self):
        parser = build_parser()

        args = parser.parse_args([
            "continue",
            "--run-id",
            "run_x",
            "--workflow",
            os.path.join(WORKFLOW_DIR, "workflow.yaml"),
            "--approve",
        ])

        assert args.command == "continue"
        assert args.approve is True

    def test_continue_from_gate_promotes_human_clarification_and_finishes(self):
        from agent_workflow.cli import cmd_continue

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = _build_runner(tmpdir)
            run_id = runner.start()
            final_state = runner.run()
            assert final_state == "human_clarification_gate"

            clarification_path = os.path.join(tmpdir, "human_clarification.md")
            with open(clarification_path, "w", encoding="utf-8") as f:
                f.write("# Human Clarification\n\n确认目标用户为运营人员。\n")
            empty_agents_path = os.path.join(tmpdir, "agents.empty.yaml")
            with open(empty_agents_path, "w", encoding="utf-8") as f:
                f.write("agents: {}\n")

            args = build_parser().parse_args([
                "continue",
                "--run-id",
                run_id,
                "--workflow",
                os.path.join(WORKFLOW_DIR, "workflow.yaml"),
                "--agents",
                empty_agents_path,
                "--project-root",
                tmpdir,
                "--approve",
                "--input",
                clarification_path,
            ])

            assert cmd_continue(args) == 0

            resumed = Runner.attach_existing(
                runner.context.run_root,
                load_workflow(os.path.join(WORKFLOW_DIR, "workflow.yaml")),
                project_root=tmpdir,
                skills_dir=os.path.join(WORKFLOW_DIR, "skills"),
            )
            artifacts = resumed.context.artifacts
            assert "human_clarification" in artifacts
            assert "final_requirement" in artifacts
            assert os.path.exists(artifacts["human_clarification"])
            assert os.path.exists(artifacts["final_requirement"])
            assert resumed.context.current_state == "done"
