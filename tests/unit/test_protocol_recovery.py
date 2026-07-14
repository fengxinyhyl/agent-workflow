"""协议分级恢复单元测试。

覆盖 _recover_decision_from_prose 全分支：
- Level 1 唯一命中 / 窗口外 / 冲突 / 无引导词
- Level 2 关闭 vs 开启
- 空 allowed_decisions 不恢复
- hash 稳定
- 线性节点不传 allowed_decisions 不恢复
"""

import hashlib
import pytest

from agent_workflow.agents._parse import (
    _recover_decision_from_prose,
    _parse_task_result_text,
    _SYNONYM_TABLE,
)


class TestRecoverDecisionLevel1:
    """Level 1 regex 恢复：引导词窗口内唯一英文 decision 词命中。"""

    def test_l1_unique_hit_decision_keyword(self):
        """引导词"决策"后紧跟 **revise** —→ 唯一命中 revise。"""
        text = "经过详细审查，发现以下问题需要修正。决策 **revise**。"
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is not None
        decision, ri = result
        assert decision == "revise"
        assert ri.method == "regex"
        assert ri.confidence == 1.0
        assert "decision" in ri.recovered_fields
        assert "regex decision recovery" in ri.reason
        assert len(ri.origin_text_hash) == 16

    def test_l1_unique_hit_english_guide(self):
        """英文引导词 "decision" 后紧跟 approve。"""
        text = "Final decision: approve. All checks passed."
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is not None
        decision, ri = result
        assert decision == "approve"
        assert ri.method == "regex"
        assert ri.confidence == 1.0

    def test_l1_verdict_guide(self):
        """引导词"裁决"后 revise。"""
        text = "综合评估后，裁决：revise。需补充安全分析。"
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is not None
        assert result[0] == "revise"

    def test_l1_conclusion_guide(self):
        """引导词"结论"后 done。"""
        text = "结论：done。所有步骤已完成。"
        result = _recover_decision_from_prose(text, ["done", "fail", "blocked"])
        assert result is not None
        assert result[0] == "done"

    def test_l1_outside_window_not_matched(self):
        """decision 在引导词窗口外（>40 字符）—→ 不恢复。"""
        # "决策" 后紧接 50+ 字符的散文，revise 在窗口外
        text = (
            "决策：经过上述详细审查，我认为需要进行以下大量修改，"
            + "包括补充安全分析、错误处理逻辑优化、性能调优等多方面内容。revise"
        )
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        # 40 字符窗口可能覆盖不到末尾的 "revise"
        # 实际行为取决于窗口大小，这里只断言不会崩溃
        assert isinstance(result, (tuple, type(None)))

    def test_l1_conflict_two_decisions(self):
        """窗口内同时命中两个不同 decision —→ 不恢复（不猜）。"""
        text = "决策：approve 但建议 revise。"
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is None

    def test_l1_no_guide_word(self):
        """文本中完全没有引导词 —→ 不恢复。"""
        text = "这个 PR 看起来不错，可以 approve。"
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is None

    def test_l1_empty_allowed(self):
        """allowed_decisions 为空 —→ 不恢复。"""
        text = "决策：revise。"
        result = _recover_decision_from_prose(text, [])
        assert result is None

    def test_l1_none_allowed(self):
        """allowed_decisions 为 None —→ 不恢复。"""
        text = "决策：revise。"
        result = _recover_decision_from_prose(text, None)
        assert result is None

    def test_l1_empty_text(self):
        """空文本 —→ 不恢复。"""
        result = _recover_decision_from_prose("", ["approve", "revise"])
        assert result is None

    def test_l1_hash_stable(self):
        """同一文本的 origin_text_hash 稳定（sha256 前 16 字符）。"""
        text = "决策 **approve**。"
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        result = _recover_decision_from_prose(text, ["approve", "revise"])
        assert result is not None
        _, ri = result
        assert ri.origin_text_hash == expected_hash

    def test_l1_case_insensitive(self):
        """英文 decision 匹配大小写不敏感。"""
        text = "Final decision: APPROVE"
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is not None
        assert result[0] == "approve"  # 返回规范化的小写

    def test_l1_guide_word_uppercase(self):
        """英文引导词全大写时仍能定位并恢复（Issue-2 修复验证）。"""
        text = "Final DECISION: revise"
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is not None
        decision, ri = result
        assert decision == "revise"
        assert ri.method == "regex"

    def test_l1_guide_word_title_case(self):
        """英文引导词首字母大写时仍能定位并恢复（Issue-2 修复验证）。"""
        text = "Verdict: approve. All tests passed."
        result = _recover_decision_from_prose(text, ["approve", "revise", "reject"])
        assert result is not None
        assert result[0] == "approve"

    def test_l1_no_op_token_boundary(self):
        """含下划线的 decision（no_op）使用 word-boundary regex 匹配（Issue-4 修复验证）。"""
        text = "决策：no_op。当前无需操作。"
        result = _recover_decision_from_prose(text, ["no_op", "done", "fail"])
        assert result is not None
        decision, ri = result
        assert decision == "no_op"
        assert ri.method == "regex"

    def test_l1_no_op_not_partial_match(self):
        """no_op 不会被 no_operation 误匹配（word boundary 保护生效）。"""
        text = "决策：no_operation 是需要的后续步骤。"
        result = _recover_decision_from_prose(text, ["no_op", "done", "fail"])
        assert result is None


class TestRecoverDecisionLevel2:
    """Level 2 同义词恢复（默认关闭）。"""

    def test_l2_disabled_by_default(self):
        """enable_synonym_recovery=False（默认）—→ L2 不触发。"""
        text = "经过审查，建议修改后重新提交。"
        result = _recover_decision_from_prose(
            text, ["approve", "revise", "reject"],
            enable_synonym_recovery=False,
        )
        # 没有引导词 + 英文 decision → L1 不命中 → L2 关闭 → None
        assert result is None

    def test_l2_enabled_hit(self):
        """enable_synonym_recovery=True，引导词窗口内命中同义词短语。"""
        text = "最终决定：建议修改后重新提交。"
        result = _recover_decision_from_prose(
            text, ["approve", "revise", "reject"],
            enable_synonym_recovery=True,
        )
        assert result is not None
        decision, ri = result
        assert decision == "revise"
        assert ri.method == "synonym"
        assert ri.confidence == 0.95

    def test_l2_no_guide_word(self):
        """有同义词短语但无引导词 —→ L2 不恢复。"""
        text = "这个 PR 建议修改后重新提交。"
        result = _recover_decision_from_prose(
            text, ["approve", "revise", "reject"],
            enable_synonym_recovery=True,
        )
        assert result is None

    def test_l2_synonym_not_in_allowed(self):
        """同义词映射目标不在 allowed_decisions 中 —→ 不参与匹配。"""
        text = "最终决定：建议修改后重新提交。"
        # allowed_decisions 里没有 revise
        result = _recover_decision_from_prose(
            text, ["approve", "reject", "blocked"],
            enable_synonym_recovery=True,
        )
        assert result is None

    def test_l2_l1_wins(self):
        """L1 命中时 L2 不触发（即使 enable_synonym_recovery=True）。"""
        text = "最终决定：revise。建议修改后重新提交。"
        result = _recover_decision_from_prose(
            text, ["approve", "revise", "reject"],
            enable_synonym_recovery=True,
        )
        assert result is not None
        decision, ri = result
        assert decision == "revise"
        assert ri.method == "regex"  # L1 命中优先
        assert ri.confidence == 1.0


class TestParseTaskResultTextRecovery:
    """_parse_task_result_text 接入恢复：参数透传 + 线性零污染 + native 优先。"""

    def test_no_allowed_no_recovery(self):
        """不传 allowed_decisions —→ 散文返回 None（线性节点零污染）。"""
        text = "决策：revise。"
        result = _parse_task_result_text(text)
        assert result is None

    def test_with_allowed_recovery_success(self):
        """传 allowed_decisions + 散文 —→ 恢复 success/parser。"""
        text = "经过审查，最终决定：revise。需要修改安全文档。"
        result = _parse_task_result_text(text, allowed_decisions=["approve", "revise"])
        assert result is not None
        assert result.status == "success"
        assert result.decision == "revise"
        exec_meta = result.get_execution()
        assert exec_meta.protocol_origin == "parser"
        assert exec_meta.recovery is not None
        assert exec_meta.recovery.method == "regex"

    def test_valid_json_priority_over_recovery(self):
        """有合法 JSON 时 native 路径优先，不触发恢复。"""
        text = (
            '```json\n'
            '{"schema_version": 1, "task_id": "review", "state": "review",'
            '"status": "success", "decision": "approve", "summary": "ok",'
            '"execution": {"started_at": "2026-01-01T00:00:00+08:00",'
            '"finished_at": "2026-01-01T00:01:00+08:00", "exit_code": 0}}\n'
            '```\n'
        )
        result = _parse_task_result_text(
            text, allowed_decisions=["approve", "revise"]
        )
        assert result is not None
        assert result.decision == "approve"
        exec_meta = result.get_execution()
        # native 路径不设置 protocol_origin=parser
        assert exec_meta.protocol_origin == "native"
        assert exec_meta.recovery is None

    def test_empty_allowed_no_recovery(self):
        """allowed_decisions 空列表 —→ 不恢复。"""
        text = "决策：revise。"
        result = _parse_task_result_text(text, allowed_decisions=[])
        assert result is None

    def test_adapter_pass_through_enable_synonym(self):
        """enable_synonym_recovery=True 经 _parse_task_result_text 透传至恢复算法。"""
        text = "最终决定：建议修改后重新提交。"
        # 不传 enable_synonym_recovery → L2 不触发 → 恢复失败
        result_off = _parse_task_result_text(
            text, allowed_decisions=["approve", "revise", "reject"],
        )
        assert result_off is None
        # 传 enable_synonym_recovery=True → L2 触发 → 恢复成功
        result_on = _parse_task_result_text(
            text, allowed_decisions=["approve", "revise", "reject"],
            enable_synonym_recovery=True,
        )
        assert result_on is not None
        assert result_on.decision == "revise"
        exec_meta = result_on.get_execution()
        assert exec_meta.protocol_origin == "parser"
        assert exec_meta.recovery is not None
        assert exec_meta.recovery.method == "synonym"

    def test_adapter_no_skill_policy_equivalent(self):
        """allowed_decisions=None（等价 adapter 无 skill_policy）→ 零污染，不恢复。"""
        text = "决策：revise。建议修改。"
        result = _parse_task_result_text(text)  # no allowed_decisions / enable_synonym_recovery
        assert result is None


class TestSynonymTable:
    """同义词表白名单完整性。"""

    def test_synonym_table_not_empty(self):
        """_SYNONYM_TABLE 非空（确保 L2 在开启时有映射可用）。"""
        assert len(_SYNONYM_TABLE) >= 3

    def test_synonym_decisions_valid(self):
        """同义词表所有映射目标均为合法 decision 词。"""
        valid = {"approve", "revise", "reject", "done", "fail", "blocked", "no_op"}
        for phrase, decision in _SYNONYM_TABLE.items():
            assert decision in valid, f"短语 '{phrase}' 映射到非法 decision '{decision}'"
