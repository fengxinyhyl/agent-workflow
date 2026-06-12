"""测试 _loop 展开功能。

验证 load_workflow 正确将 _loop 块展开为线性 state 序列。
"""

import os
import tempfile

import pytest

from agent_workflow.config.loader import load_workflow, _unroll_loops
from agent_workflow.config.models import StateModel


# ── 辅助函数 ─────────────────────────────────────────────

def _make_states(loop_state_names: list[str]) -> dict[str, StateModel]:
    """构造模拟 states，供 _unroll_loops 单元测试使用。"""
    states = {}
    for name in loop_state_names:
        states[name] = StateModel(name=name, task=name)
    # 循环外的 states
    states["execute"] = StateModel(name="execute", task="execute")
    states["summary"] = StateModel(name="summary", task="summary")
    states["done"] = StateModel(name="done", terminal=True)
    states["failed"] = StateModel(name="failed", terminal=True)
    return states


def _make_yaml(loop_config: str, extra_states: str = "") -> str:
    """构造完整的 workflow YAML 字符串。"""
    return f"""
name: test-loop
version: "1"
initial_state: plan

terminal_states: [done, failed]

tasks:
  plan:
    instruction: "编写计划"
    role: planner
    output: plan_doc
    allowed_decisions: [done, fail]
  review:
    instruction: "审核计划"
    role: reviewer
    output: review_doc
    allowed_decisions: [done, fail]
  advise:
    instruction: "决定"
    role: advisor
    output: advise_doc
    allowed_decisions: [approve, revise, reject]
  execute:
    instruction: "执行"
    role: executor
    output: execution_report
    allowed_decisions: [done, fail]
  summary:
    instruction: "总结"
    role: summarizer
    output: summary_report
    allowed_decisions: [done, fail]

roles:
  planner: mock
  reviewer: mock
  advisor: mock
  executor: mock
  summarizer: mock

states:
  plan:
    task: plan
    on:
      done: review
    default: failed

  review:
    task: review
    on:
      done: advise
    default: failed

  advise:
    task: advise
    on:
      approve: execute
      revise: plan
      reject: failed
    default: failed

  execute:
    task: execute
    on:
      done: summary
    default: failed

  summary:
    task: summary
    on:
      done: done
    default: failed

  done:
    terminal: true

  failed:
    terminal: true

{loop_config}
{extra_states}
"""


# ── 单元测试：_unroll_loops 函数 ──────────────────────────

class TestUnrollLoopsUnit:
    """直接测试 _unroll_loops 函数。"""

    def test_repeat_3_generates_9_states(self):
        """repeat=3，3 个 state → 展开为 9 个 _r1/_r2/_r3。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 3,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 展开的 9 个 state
        for r in range(1, 4):
            for name in ["plan", "review", "advise"]:
                assert f"{name}_r{r}" in result

        # 原始循环体内的 state 名已移除
        for name in ["plan", "review", "advise"]:
            assert name not in result

        # 循环外的 state 保留
        assert "execute" in result
        assert "summary" in result
        assert "done" in result
        assert "failed" in result

    def test_initial_state_redirected_to_r1(self):
        """initial_state 为循环首个 state 时自动指向 _r1。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        _unroll_loops(resolved, states)
        assert resolved["initial_state"] == "plan_r1"

    def test_done_transitions_chain_correctly(self):
        """plan_r1.done → review_r1, review_r1.done → advise_r1。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        assert result["plan_r1"].on["done"] == "review_r1"
        assert result["review_r1"].on["done"] == "advise_r1"
        assert result["plan_r2"].on["done"] == "review_r2"
        assert result["review_r2"].on["done"] == "advise_r2"

    def test_advise_last_round_no_revise(self):
        """最后一轮 advise 不设 revise 决策。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 3,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 前两轮有 revise
        assert "revise" in result["advise_r1"].on
        assert result["advise_r1"].on["revise"] == "plan_r2"
        assert result["advise_r2"].on["revise"] == "plan_r3"

        # 最后一轮无 revise
        assert "revise" not in result["advise_r3"].on
        assert result["advise_r3"].on["approve"] == "execute"

    def test_approve_early_exit(self):
        """前几轮的 approve 直接跳转到 on_break。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 3,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        assert result["advise_r1"].on["approve"] == "execute"
        assert result["advise_r2"].on["approve"] == "execute"
        assert result["advise_r3"].on["approve"] == "execute"

    def test_no_loop_block_returns_unchanged(self):
        """无 _loop 块时原样返回 states。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {"initial_state": "plan"}

        result = _unroll_loops(resolved, states)
        assert result is states  # 同一对象

    def test_repeat_1_minimal(self):
        """repeat=1 时只展开一轮。"""
        states = _make_states(["plan", "review", "advise"])
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 1,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        for name in ["plan_r1", "review_r1", "advise_r1"]:
            assert name in result
        # 无第二轮
        assert "plan_r2" not in result
        # 最后一轮无 revise
        assert "revise" not in result["advise_r1"].on

    def test_unknown_state_in_loop_raises(self):
        """_loop.states 中引用未定义 state 时抛异常。"""
        states = _make_states(["plan", "review"])  # 缺少 advise
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        with pytest.raises(ValueError, match="未定义"):
            _unroll_loops(resolved, states)

    def test_unknown_on_break_raises(self):
        """on_break 目标不存在时抛异常。"""
        states = _make_states(["plan", "review", "advise"])
        states.pop("execute")  # 移除 execute
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        with pytest.raises(ValueError, match="on_break"):
            _unroll_loops(resolved, states)

    def test_external_reference_redirected_to_r1(self):
        """循环外 state 的 transition 指向循环内 state 时，自动修正为 _r1。"""
        states = _make_states(["review", "advise"])
        states["plan"] = StateModel(
            name="plan",
            task="plan",
            on={"done": "review"},     # ← 指向循环内
            default="failed",
        )
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # plan 保留，但其 on.done 修正为 review_r1
        assert "plan" in result
        assert result["plan"].on["done"] == "review_r1"

    def test_external_default_redirected_to_r1(self):
        """循环外 state 的 default 指向循环内 state 时也修正。"""
        states = _make_states(["review", "advise"])
        states["plan"] = StateModel(
            name="plan",
            task="plan",
            on={"done": "review"},
            default="review",           # ← default 也指向循环内
        )
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["review", "advise"],
                "repeat": 1,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        assert result["plan"].default == "review_r1"


# ── 集成测试：通过 YAML 文件完整加载 ──────────────────────

class TestUnrollLoopsIntegration:
    """通过 load_workflow 验证完整 YAML 加载链路。"""

    def test_load_workflow_with_loop(self):
        """完整 YAML 加载 → _loop 自动展开。"""
        loop_yaml = """
_loop:
  states: [plan, review, advise]
  repeat: 3
  on_break: execute
"""
        yaml_content = _make_yaml(loop_yaml)

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = os.path.join(tmpdir, "workflow.yaml")
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)

            wf = load_workflow(yaml_path)

            # 展开后的 state 存在
            assert "plan_r1" in wf.states
            assert "plan_r3" in wf.states
            assert "advise_r3" in wf.states

            # 原始循环内 state 不存在
            assert "plan" not in wf.states
            assert "advise" not in wf.states

            # 循环外 state 保留
            assert "execute" in wf.states
            assert "summary" in wf.states

            # initial_state 指向 _r1
            assert wf.initial_state == "plan_r1"

            # 最后一轮 advise 无 revise
            assert "revise" not in wf.states["advise_r3"].on

            # 状态机 DAG：无回环
            sm_issues = wf.validate()
            assert len(sm_issues) == 0, f"状态机校验失败: {sm_issues}"

    def test_no_loop_preserves_behavior(self):
        """无 _loop 块时行为与之前完全一致。"""
        yaml_content = _make_yaml("")

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = os.path.join(tmpdir, "workflow.yaml")
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)

            wf = load_workflow(yaml_path)

            # 原始 state 名保持不变
            assert "plan" in wf.states
            assert "review" in wf.states
            assert "advise" in wf.states
            assert "execute" in wf.states
            assert wf.initial_state == "plan"
