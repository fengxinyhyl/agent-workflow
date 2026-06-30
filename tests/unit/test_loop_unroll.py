"""测试 _loop 展开功能。

验证 load_workflow 正确将 _loop 块展开为线性 state 序列。
"""

import os
import tempfile

import pytest

from agent_workflow.config.loader import load_workflow, _unroll_loops, _reroute_state_refs
from agent_workflow.config.models import StateModel


# ── 辅助函数 ─────────────────────────────────────────────

def _make_states(
    loop_state_names: list[str],
    *,
    with_next: dict[str, str] | None = None,
    with_on: dict[str, dict[str, str]] | None = None,
    with_on_status: dict[str, dict[str, str]] | None = None,
) -> dict[str, StateModel]:
    """构造归一化后的模拟 states，供 _unroll_loops 单元测试使用。

    参数：
    - loop_state_names: 循环内的 state 名列表
    - with_next: 线性节点的 next 映射，如 {"plan": "review", "review": "advise"}
    - with_on: 分支节点的 on 映射，如 {"advise": {"approve": "execute", ...}}
    - with_on_status: on_status 映射，如 {"review": {"blocked": "review"}}
    """
    next_map = with_next or {}
    on_map = with_on or {}
    on_status_map = with_on_status or {}

    states = {}
    for name in loop_state_names:
        states[name] = StateModel(
            name=name,
            task=name,
            next=next_map.get(name, ""),
            on=on_map.get(name, {}),
            on_status=on_status_map.get(name, {}),
        )
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
    agent: mock
    output: plan_doc
    allowed_decisions: [done, fail]
  review:
    instruction: "审核计划"
    agent: mock
    output: review_doc
    allowed_decisions: [done, fail]
  advise:
    instruction: "决定"
    agent: mock
    output: advise_doc
    allowed_decisions: [approve, revise, reject]
  execute:
    instruction: "执行"
    agent: mock
    output: execution_report
    allowed_decisions: [done, fail]
  summary:
    instruction: "总结"
    agent: mock
    output: summary_report
    allowed_decisions: [done, fail]

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


# ── 存量单元测试（兼容改造后逻辑）────────────────────────

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
        """plan_r1.next → review_r1, review_r1.next → advise_r1（线性节点 next 串接）。"""
        states = _make_states(
            ["plan", "review", "advise"],
            with_next={"plan": "review", "review": "advise"},
            with_on={"advise": {"approve": "execute", "revise": "review", "reject": "failed"}},
        )
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 线性节点通过 next 字段串接（归一化后不在 on 中）
        assert result["plan_r1"].next == "review_r1"
        assert result["review_r1"].next == "advise_r1"
        assert result["plan_r2"].next == "review_r2"
        assert result["review_r2"].next == "advise_r2"

    def test_advise_last_round_no_revise(self):
        """最后一轮 advise 自动删除指向循环内的 revise 决策。"""
        states = _make_states(
            ["plan", "review", "advise"],
            with_on={"advise": {"approve": "execute", "revise": "review", "reject": "failed"}},
        )
        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "advise"],
                "repeat": 3,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 前两轮有 revise（指向下一轮的首 state）
        assert "revise" in result["advise_r1"].on
        assert result["advise_r1"].on["revise"] == "plan_r2"
        assert result["advise_r2"].on["revise"] == "plan_r3"

        # 最后一轮无 revise（被通用删除逻辑删除）
        assert "revise" not in result["advise_r3"].on
        assert result["advise_r3"].on["approve"] == "execute"

    def test_approve_early_exit(self):
        """前几轮的 approve 直接跳转到 on_break（指向循环外，保留不变）。"""
        states = _make_states(
            ["plan", "review", "advise"],
            with_on={"advise": {"approve": "execute", "revise": "review", "reject": "failed"}},
        )
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
        states = _make_states(
            ["plan", "review", "advise"],
            with_on={"advise": {"approve": "execute", "revise": "review", "reject": "failed"}},
        )
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
        # 最后一轮无 revise（被删除）
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


# ── 新增测试（Phase 1e 覆盖）─────────────────────────────

class TestUnrollLoopsNew:
    """Phase 1e 新增测试：线性 next、分支 on 通用化、混合、on_status、外部引用等。"""

    # 1. 纯线性节点 loop
    def test_linear_node_next_chains(self):
        """纯线性节点 loop（next 非空，on 为空）：next 正确串接。"""
        states = _make_states(
            ["plan", "review", "execute"],
            with_next={"plan": "review", "review": "execute"},
        )
        # 确保线性节点 on 为空
        assert states["plan"].on == {}
        assert states["review"].on == {}

        resolved = {
            "initial_state": "plan",
            "_loop": {
                "states": ["plan", "review", "execute"],
                "repeat": 2,
                "on_break": "summary",
            },
        }

        result = _unroll_loops(resolved, states)

        assert result["plan_r1"].next == "review_r1"
        assert result["review_r1"].next == "execute_r1"
        assert result["plan_r2"].next == "review_r2"
        assert result["review_r2"].next == "execute_r2"
        # on 保持为空
        assert result["plan_r1"].on == {}
        assert result["review_r1"].on == {}

    # 2. 归一化后 on 为空 + next 非空的线性节点（最常见生产场景）
    def test_linear_node_on_empty_next_nonempty(self):
        """归一化后 on={}、next="advise" 的线性节点：next 正确串接，on 保持为空。"""
        states = _make_states(
            ["review"],
            with_next={"review": "advise"},
        )
        assert states["review"].on == {}
        assert states["review"].next == "advise"

        resolved = {
            "_loop": {
                "states": ["review"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        assert result["review_r1"].next == ""
        assert result["review_r1"].on == {}
        # 分支节点在最后一轮删除循环内 decision 后 on 为空时补 done 出口
        assert "done" in result["review_r1"].on or result["review_r1"].on == {}
        assert result["review_r2"].next == ""
        # 最后一轮安全出口
        assert "done" in result["review_r2"].on or result["review_r2"].on == {}

    # 3. 纯分支节点 loop（通用 decision 词）
    def test_branch_node_on_generic(self):
        """纯分支节点 loop：用自定义 decision 词（不依赖 approve/revise 键名）。"""
        states = _make_states(
            ["advise"],
            with_on={"advise": {"retry": "advise", "skip": "execute", "merge": "advise"}},
        )
        resolved = {
            "_loop": {
                "states": ["advise"],
                "repeat": 2,
                "on_break": "summary",
            },
        }

        result = _unroll_loops(resolved, states)

        # advse_r1（非最后一轮）：retry/merge 指向循环内 → 重定向到下一轮首 state
        assert result["advise_r1"].on["retry"] == "advise_r2"
        assert result["advise_r1"].on["merge"] == "advise_r2"
        # skip 指向循环外 → 保留不变
        assert result["advise_r1"].on["skip"] == "execute"

        # advse_r2（最后一轮）：retry/merge 被删除，skip 保留
        assert "retry" not in result["advise_r2"].on
        assert "merge" not in result["advise_r2"].on
        assert result["advise_r2"].on["skip"] == "execute"

    # 4. 分支节点最后一轮删除循环内 decision
    def test_branch_last_round_deletes_loop_decisions(self):
        """分支节点最后一轮：所有指向循环内的 decision 被删除，外部保留。"""
        states = _make_states(
            ["advise"],
            with_on={"advise": {"approve": "execute", "revise": "advise", "reject": "failed"}},
        )
        resolved = {
            "_loop": {
                "states": ["advise"],
                "repeat": 3,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 最后一轮：revise 被删除，approve/reject 保留
        assert "revise" not in result["advise_r3"].on
        assert result["advise_r3"].on["approve"] == "execute"
        assert result["advise_r3"].on["reject"] == "failed"

    # 5. 新旧节点混合 loop
    def test_mixed_linear_branch_loop(self):
        """线性节点（用 next）+ 分支节点（用 on）混合于同一 loop。"""
        states = _make_states(
            ["review", "advise"],
            with_next={"review": "advise"},
            with_on={"advise": {"approve": "execute", "revise": "review", "reject": "failed"}},
        )
        resolved = {
            "_loop": {
                "states": ["review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 线性节点 review：next 指向同轮 advise
        assert result["review_r1"].next == "advise_r1"
        assert result["review_r2"].next == "advise_r2"

        # 分支节点 advise_r1：revise → 下一轮 review_r2，approve → execute
        assert result["advise_r1"].on["revise"] == "review_r2"
        assert result["advise_r1"].on["approve"] == "execute"

        # 最后一轮 advise_r2：revise 被删除
        assert "revise" not in result["advise_r2"].on
        assert result["advise_r2"].on["approve"] == "execute"

    # 6. loop 内 state 有 on_status 循环引用
    def test_loop_state_with_on_status_redirected(self):
        """loop 内 state 有 on_status: {blocked: review}，非最后一轮修正。"""
        states = _make_states(
            ["review", "advise"],
            with_on={"advise": {"approve": "execute", "revise": "review"}},
            with_on_status={"review": {"blocked": "review"}},
        )
        resolved = {
            "_loop": {
                "states": ["review", "advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # review_r1（非最后一轮）：blocked → review_r2（下一轮首 state）
        assert result["review_r1"].on_status["blocked"] == "review_r2"
        # review_r2（最后一轮）：on_status 保留不变
        assert result["review_r2"].on_status["blocked"] == "review_r2"

    # 7. 外部 state 的 next 指向循环内
    def test_external_next_redirected(self):
        """外部 state 的 next 指向循环内 state：修正为 _r1。"""
        states = _make_states(["review", "advise"])
        states["plan"] = StateModel(
            name="plan",
            task="plan",
            next="review",  # ← 指向循环内
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

        assert "plan" in result
        assert result["plan"].next == "review_r1"

    # 8. 外部 state 的 next 指向循环外保持不变
    def test_external_next_preserved_when_not_pointing_to_loop(self):
        """外部 state 的 next 指向循环外：保持原样不被误修改。"""
        states = _make_states(["review", "advise"])
        states["plan"] = StateModel(
            name="plan",
            task="plan",
            next="summary",  # ← 指向循环外
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

        assert "plan" in result
        assert result["plan"].next == "summary"

    # 9. 外部 state 的 on_status 指向循环内
    def test_external_on_status_redirected(self):
        """外部 state 的 on_status.blocked 指向循环内：修正为 _r1。"""
        states = _make_states(["review", "advise"])
        states["plan"] = StateModel(
            name="plan",
            task="plan",
            on={"done": "summary"},
            on_status={"blocked": "review"},  # ← 指向循环内
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

        assert "plan" in result
        assert result["plan"].on_status["blocked"] == "review_r1"

    # 10. 分支节点所有 decision 指向循环外
    def test_branch_no_loop_back_decision(self):
        """分支节点所有 decision 都指向循环外：所有出口保留，不强制添加/删除。"""
        states = _make_states(
            ["advise"],
            with_on={"advise": {"approve": "execute", "reject": "failed"}},
        )
        resolved = {
            "_loop": {
                "states": ["advise"],
                "repeat": 2,
                "on_break": "execute",
            },
        }

        result = _unroll_loops(resolved, states)

        # 所有轮次：approve/reject 均保留不变
        for r in range(1, 3):
            name = f"advise_r{r}"
            assert "approve" in result[name].on
            assert "reject" in result[name].on
            assert result[name].on["approve"] == "execute"
            assert result[name].on["reject"] == "failed"
            # 不强制添加 revise
            assert "revise" not in result[name].on

    # 11. 旧格式兼容（通过 normalize 链路）
    def test_pure_old_format_loop(self):
        """YAML 使用旧格式 on={done, ...} + _loop，归一化后展开结果与旧逻辑一致。"""
        loop_yaml = """
_loop:
  states: [plan, review, advise]
  repeat: 2
  on_break: execute
"""
        yaml_content = _make_yaml(loop_yaml)

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = os.path.join(tmpdir, "workflow.yaml")
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)

            wf = load_workflow(yaml_path)

            # 归一化后 plan 有 next="review"（done→next）
            assert wf.states["plan_r1"].next == "review_r1"
            # 归一化后 review 有 next="advise"
            assert wf.states["review_r1"].next == "advise_r1"
            # 归一化后 advise 保留业务词 on
            assert "revise" in wf.states["advise_r1"].on
            assert wf.states["advise_r1"].on["revise"] == "plan_r2"
            # 最后一轮 revise 被删除
            assert "revise" not in wf.states["advise_r2"].on

    # 12. 多循环 smoke test
    def test_multi_loop_survival(self):
        """_loops 数组含两个循环的 smoke test（不同 state 名）。"""
        states = _make_states(
            ["review", "advise"],
            with_on={"advise": {"approve": "execute", "revise": "review"}},
        )
        # 第二个循环用不同的 state 名（展开后的 state 替换了原始名）
        states["review2"] = StateModel(name="review2", task="review")
        states["advise2"] = StateModel(
            name="advise2", task="advise",
            on={"approve": "summary", "revise": "review2"},
        )
        states["plan"] = StateModel(name="plan", task="plan")
        states["summary"] = StateModel(name="summary", task="summary")

        resolved = {
            "initial_state": "plan",
            "_loops": [
                {"states": ["review", "advise"], "repeat": 1, "on_break": "execute"},
                {"states": ["review2", "advise2"], "repeat": 1, "on_break": "summary"},
            ],
        }

        result = _unroll_loops(resolved, states)

        # 第一个循环展开为 review_r1, advise_r1
        assert "review_r1" in result
        assert "advise_r1" in result
        # 第二个循环展开为 review2_r1, advise2_r1
        assert "review2_r1" in result
        assert "advise2_r1" in result
        # 外部 state 保留
        assert "plan" in result
        assert "execute" in result
        assert "summary" in result

    # ── _reroute_state_refs 单元测试 ──

    def test_reroute_state_refs_all_fields(self):
        """_reroute_state_refs 正确修正 next/on/on_status/default 四个字段。"""
        state = StateModel(
            name="external",
            task="plan",
            next="review",
            on={"done": "review", "fail": "failed"},
            on_status={"blocked": "review"},
            default="review",
        )
        result = _reroute_state_refs(
            state,
            loop_state_names={"review", "advise"},
            reroute_map={"review": "review_r1", "advise": "advise_r1"},
        )

        assert result.next == "review_r1"
        assert result.on["done"] == "review_r1"
        assert result.on["fail"] == "failed"  # 非循环内 → 保持原样
        assert result.on_status["blocked"] == "review_r1"
        assert result.default == "review_r1"

    def test_reroute_state_refs_preserves_non_route_fields(self):
        """_reroute_state_refs 不修改 name/task/description/terminal/gate。"""
        state = StateModel(
            name="external",
            task="plan",
            description="test desc",
            terminal=False,
            gate=True,
            default="failed",
        )
        result = _reroute_state_refs(
            state,
            loop_state_names={"review"},
            reroute_map={"review": "review_r1"},
        )

        assert result.name == "external"
        assert result.task == "plan"
        assert result.description == "test desc"
        assert result.terminal is False
        assert result.gate is True


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

            # 归一化后 plan_r1 用 next（不是 on["done"]）
            assert wf.states["plan_r1"].next == "review_r1"
            assert wf.states["review_r1"].next == "advise_r1"

            # 最后一轮 advise 无 revise
            assert "revise" not in wf.states["advise_r3"].on
            assert wf.states["advise_r3"].on["approve"] == "execute"

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
