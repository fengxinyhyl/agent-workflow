"""测试 agent override map：解析、校验、Runner._resolve_agent。"""

import pytest

from agent_workflow.cli import _parse_agent_map, _validate_agent_overrides
from agent_workflow.config.models import WorkflowConfig, TaskModel, StateModel
from agent_workflow.state_machine.runner import Runner


# ── 辅助 ────────────────────────────────────────────────────────────────────

def _make_wf(extra_tasks=None, extra_states=None) -> WorkflowConfig:
    tasks = {
        "plan": TaskModel(name="plan", instruction="plan", agent="cc-opus"),
        "review": TaskModel(name="review", instruction="review", agent="cc-opus"),
    }
    if extra_tasks:
        tasks.update(extra_tasks)
    states = {
        "plan": StateModel(name="plan", task="plan", on={"done": "review"}, default="failed"),
        "review": StateModel(name="review", task="review", on={"approve": "done"}, default="failed"),
        "done": StateModel(name="done", terminal=True),
        "failed": StateModel(name="failed", terminal=True),
    }
    if extra_states:
        states.update(extra_states)
    return WorkflowConfig(
        name="test", initial_state="plan", terminal_states=["done", "failed"],
        tasks=tasks, states=states,
    )


def _make_runner(overrides=None) -> Runner:
    return Runner(_make_wf(), agent_overrides=overrides)


# ── _parse_agent_map ─────────────────────────────────────────────────────────

class TestParseAgentMap:
    def test_empty(self):
        assert _parse_agent_map("") == {}

    def test_valid_mixed(self):
        result = _parse_agent_map("state:review=cc-haiku,task:plan=codex")
        assert result == {"state:review": "cc-haiku", "task:plan": "codex"}

    def test_missing_equals(self):
        with pytest.raises(ValueError, match="缺少"):
            _parse_agent_map("state:review")

    def test_empty_key(self):
        with pytest.raises(ValueError, match="key 不能为空"):
            _parse_agent_map("=cc-opus")

    def test_empty_value(self):
        with pytest.raises(ValueError, match="value 不能为空"):
            _parse_agent_map("state:review=")

    def test_missing_namespace(self):
        with pytest.raises(ValueError, match="'state:' 或 'task:'"):
            _parse_agent_map("review=cc-opus")

    def test_duplicate_key(self):
        with pytest.raises(ValueError, match="重复 key"):
            _parse_agent_map("state:review=cc-opus,state:review=cc-haiku")


# ── _validate_agent_overrides ────────────────────────────────────────────────

class TestValidateAgentOverrides:
    def test_valid(self):
        wf = _make_wf()
        _validate_agent_overrides({"task:plan": "cc-opus"}, wf, {"cc-opus": object()})

    def test_unknown_state(self):
        wf = _make_wf()
        with pytest.raises(ValueError, match="不存在的 state"):
            _validate_agent_overrides({"state:nonexistent": "cc-opus"}, wf, {})

    def test_unknown_task(self):
        wf = _make_wf()
        with pytest.raises(ValueError, match="不存在的 task"):
            _validate_agent_overrides({"task:nonexistent": "cc-opus"}, wf, {})

    def test_unknown_agent_fails(self):
        wf = _make_wf()
        with pytest.raises(ValueError, match="未注册的 agent"):
            _validate_agent_overrides({"task:plan": "unknown-agent"}, wf, {"cc-opus": object()})

    def test_explicit_mock_allowed(self):
        wf = _make_wf()
        # mock 不受 agent 注册校验约束
        _validate_agent_overrides({"task:plan": "mock"}, wf, {"cc-opus": object()})

    def test_no_agents_dict_skips_agent_check(self):
        wf = _make_wf()
        # agents_dict 为空时跳过 agent 校验
        _validate_agent_overrides({"task:plan": "any-agent"}, wf, {})


# ── Runner._resolve_agent ────────────────────────────────────────────────────

class TestResolveAgent:
    def test_no_override_uses_task_agent(self):
        r = _make_runner()
        task = TaskModel(name="plan", instruction="", agent="cc-opus")
        assert r._resolve_agent("plan", task) == "cc-opus"

    def test_task_override_wins(self):
        r = _make_runner({"task:plan": "codex"})
        task = TaskModel(name="plan", instruction="", agent="cc-opus")
        assert r._resolve_agent("plan", task) == "codex"

    def test_state_override_wins_over_task(self):
        r = _make_runner({"task:plan": "codex", "state:plan": "cc-haiku"})
        task = TaskModel(name="plan", instruction="", agent="cc-opus")
        assert r._resolve_agent("plan", task) == "cc-haiku"

    def test_task_model_none_returns_mock(self):
        r = _make_runner({"task:plan": "codex"})
        assert r._resolve_agent("plan", None) == "mock"

    def test_fallback_mock(self):
        r = _make_runner()
        task = TaskModel(name="plan", instruction="", agent="")
        assert r._resolve_agent("plan", task) == "mock"

    def test_overrides_stored_in_init(self):
        overrides = {"task:plan": "codex"}
        r = _make_runner(overrides)
        assert r._agent_overrides == overrides
