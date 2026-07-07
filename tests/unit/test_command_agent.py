"""测试 CommandAgent 接线：registry 解析、enabled 开关、exit_code→decision、artifact 落盘。"""

import os
import tempfile

from agent_workflow.agents.registry import AgentRegistry
from agent_workflow.agents.command import CommandAgent
from agent_workflow.agents.mock import MockAgent
from agent_workflow.config.models import AgentModel
from agent_workflow.config.loader import load_agent
from agent_workflow.context.agent_input import AgentInput, TaskConfig
from agent_workflow.context.run_context import RunContext


# ── registry 解析 ────────────────────────────────────────────────────────────

class TestRegistryResolvesCommand:
    def test_builtin_class_registered(self):
        reg = AgentRegistry()
        assert reg._agent_classes.get("command") is CommandAgent

    def test_resolve_command_provider_not_mock(self):
        cfg = {"cov": AgentModel(name="cov", provider="command", command="echo ok", enabled=True)}
        reg = AgentRegistry(cfg)
        agent = reg.resolve("cov")
        assert isinstance(agent, CommandAgent)
        assert not isinstance(agent, MockAgent)

    def test_enabled_passed_through(self):
        cfg = {"cov": AgentModel(name="cov", provider="command", command="echo ok", enabled=True)}
        reg = AgentRegistry(cfg)
        agent = reg.resolve("cov")
        assert agent.enabled is True

    def test_disabled_by_default(self):
        cfg = {"cov": AgentModel(name="cov", provider="command", command="echo ok")}
        reg = AgentRegistry(cfg)
        agent = reg.resolve("cov")
        assert agent.enabled is False


# ── loader 解析 enabled ───────────────────────────────────────────────────────

class TestLoaderEnabled:
    def test_enabled_string_true(self):
        # loader 禁用了 YAML bool 自动转换，enabled 以字符串进入
        m = load_agent({"name": "cov", "provider": "command", "enabled": "true"})
        assert m.enabled is True

    def test_enabled_default_false(self):
        m = load_agent({"name": "cov", "provider": "command"})
        assert m.enabled is False


# ── 执行语义 ──────────────────────────────────────────────────────────────────

def _make_input(root, output="coverage_report", with_staging=True):
    state = "coverage_check"
    staging_dir = os.path.join(root, "staging", state)
    ctx = RunContext(
        run_id="t", project_root=root, run_root=root, current_state=state,
    )
    staging_paths = {}
    if with_staging:
        staging_paths = {
            output: os.path.join(staging_dir, f"{output}.md"),
            "task_result": os.path.join(staging_dir, "task_result.json"),
        }
    return AgentInput(
        task=TaskConfig(name="coverage_check", instruction="", output=output),
        context=ctx,
        state_name=state,
        staging_paths=staging_paths,
    )


class TestExecuteSemantics:
    def test_disabled_returns_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            agent = CommandAgent({"command": "echo ok"})
            result = agent.execute(_make_input(root))
            assert result.status == "blocked"
            assert result.decision == "blocked"

    def test_success_exit0_done(self):
        with tempfile.TemporaryDirectory() as root:
            agent = CommandAgent({"command": "python --version", "enabled": True})
            result = agent.execute(_make_input(root))
            assert result.status == "success"
            assert result.decision == "done"
            assert result.get_execution().exit_code == 0
            # duration 由 started/finished 计算，不应恒为 0（>=0 且字段被填充）
            assert result.get_execution().duration_seconds >= 0.0

    def test_compute_duration_from_timestamps(self):
        # 用固定时间戳验证 duration 计算，避免依赖真实执行耗时的偶发
        d = CommandAgent._compute_duration(
            "2026-07-07T10:00:00+08:00", "2026-07-07T10:00:03.5+08:00"
        )
        assert d == 3.5

    def test_compute_duration_bad_input_returns_zero(self):
        assert CommandAgent._compute_duration("", "") == 0.0
        assert CommandAgent._compute_duration("not-a-date", "also-bad") == 0.0

    def test_nonzero_exit_fail(self):
        with tempfile.TemporaryDirectory() as root:
            # 白名单内命令但退出码非 0（不存在的模块）→ 门失败
            agent = CommandAgent({"command": "python -m nonexistent_module_xyz", "enabled": True})
            result = agent.execute(_make_input(root))
            assert result.status == "failed"
            assert result.decision == "fail"
            assert result.get_execution().exit_code != 0

    def test_artifact_written_and_registered(self):
        with tempfile.TemporaryDirectory() as root:
            agent = CommandAgent({"command": "python --version", "enabled": True})
            result = agent.execute(_make_input(root))
            artifacts = result.get_artifacts()
            assert len(artifacts) == 1
            art = artifacts[0]
            assert art.name == "coverage_report"
            assert art.artifact_path == "artifacts/coverage_report.md"
            assert os.path.exists(art.staging_path)
            content = open(art.staging_path, encoding="utf-8").read()
            assert "exit_code: 0" in content

    def test_no_staging_paths_no_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            agent = CommandAgent({"command": "python --version", "enabled": True})
            result = agent.execute(_make_input(root, with_staging=False))
            assert result.get_artifacts() == []
