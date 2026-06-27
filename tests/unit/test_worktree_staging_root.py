"""测试 worktree 模式下 staging 根的解析（治本回归）。

回归背景：worktree 模式下 run_root 在主仓、agent cwd（project_root）在 worktree，
两棵不同的树。引擎曾把 staging_path 一律基于 run_root 拼接告知 agent，但 agent 被
`--add-dir` 沙箱锁在 project_root，根本写不进主仓 run_root —— 它只能把路径尾部重新
挂到自己 cwd，导致引擎按 run_root/staging 找不到文件，promotion/校验失败。

正确行为：agent 可写的 staging 根（staging_root）在 worktree 模式应为 project_root
（agent 沙箱），普通模式仍为 run_root。artifacts 仍 promote 到 run_root/artifacts。
"""

from __future__ import annotations

import os

from agent_workflow.config.models import WorkflowConfig, TaskModel, StateModel
from agent_workflow.context import RunContext
from agent_workflow.state_machine import Runner


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="t",
        initial_state="plan",
        terminal_states=["done", "failed"],
        tasks={
            "plan": TaskModel(
                name="plan", instruction="做计划", agent="mock",
                output="plan_doc", allowed_decisions=["done", "fail"],
            ),
        },
        states={
            "plan": StateModel(name="plan", task="plan", on={"done": "done"}, default="failed"),
            "done": StateModel(name="done", terminal=True),
            "failed": StateModel(name="failed", terminal=True),
        },
    )


class TestStagingRootProperty:
    """RunContext.staging_root 归类逻辑。"""

    def test_normal_mode_run_root_under_project_root(self):
        """普通模式：run_root 在 project_root 内 → staging_root == run_root。"""
        ctx = RunContext.create(
            workflow_id="t", goal="g",
            project_root="/tmp/proj",
            run_id="r1", run_root="/tmp/proj/docs/runs/r1",
        )
        assert ctx.staging_root == ctx.run_root

    def test_worktree_mode_run_root_outside_project_root(self):
        """worktree 模式：run_root 在主仓、project_root 在 worktree（不同树）
        → staging_root == project_root（agent 沙箱可写）。"""
        ctx = RunContext.create(
            workflow_id="t", goal="g",
            project_root="/tmp/aw-wt/sp1",
            run_id="r1", run_root="/tmp/mainrepo/docs/runs/r1",
        )
        assert ctx.staging_root == ctx.project_root
        assert ctx.staging_root != ctx.run_root


class TestBuildAgentInputStagingPaths:
    """_build_agent_input 给 agent 的 staging 路径必须落在 agent 沙箱（project_root）内。"""

    def _runner_with_context(self, project_root: str, run_root: str) -> Runner:
        runner = Runner(_make_workflow(), goal="g", project_root=project_root)
        runner.context = RunContext.create(
            workflow_id="t", goal="g",
            project_root=project_root,
            run_id="r1", run_root=run_root,
        )
        return runner

    def test_worktree_staging_paths_under_project_root(self):
        """worktree 模式：告知 agent 的 output 路径必须在 project_root 之下，
        否则 agent（沙箱）写不进去。"""
        project_root = os.path.abspath("/tmp/aw-wt/sp1")
        run_root = os.path.abspath("/tmp/mainrepo/docs/runs/r1")
        runner = self._runner_with_context(project_root, run_root)

        task_model = runner.workflow.get_task("plan")
        ai = runner._build_agent_input("plan", task_model, "mock")

        out_path = os.path.abspath(ai.staging_paths["plan_doc"])
        # 路径必须落在 agent 沙箱 project_root 内
        assert os.path.commonpath([out_path, project_root]) == project_root
        # 不应落在主仓 run_root 树（agent 写不进去）
        assert os.path.commonpath([out_path, run_root]) != run_root

    def test_normal_mode_staging_paths_under_run_root(self):
        """普通模式：staging 路径仍在 run_root 之下（向后兼容）。"""
        project_root = os.path.abspath("/tmp/proj")
        run_root = os.path.abspath("/tmp/proj/docs/runs/r1")
        runner = self._runner_with_context(project_root, run_root)

        task_model = runner.workflow.get_task("plan")
        ai = runner._build_agent_input("plan", task_model, "mock")

        out_path = os.path.abspath(ai.staging_paths["plan_doc"])
        assert os.path.commonpath([out_path, run_root]) == run_root
