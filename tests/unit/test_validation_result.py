"""测试 ValidResult 三态类型 + RouteShape NamedTuple。"""

import pytest

from agent_workflow.validators.validation_result import ValidResult, RouteShape


class TestRouteShape:
    """RouteShape NamedTuple 测试。"""

    def test_default_values(self):
        """默认构造：has_on/has_next 为 False，allowed_decisions 为空。"""
        rs = RouteShape()
        assert rs.has_on is False
        assert rs.has_next is False
        assert rs.allowed_decisions == ()

    def test_branch_node(self):
        """分支节点：has_on=True，allowed_decisions 非空。"""
        rs = RouteShape(has_on=True, allowed_decisions=("approve", "revise"))
        assert rs.has_on is True
        assert rs.has_next is False
        assert rs.allowed_decisions == ("approve", "revise")

    def test_linear_node(self):
        """线性节点：has_next=True，无 allowed_decisions。"""
        rs = RouteShape(has_next=True)
        assert rs.has_on is False
        assert rs.has_next is True
        assert rs.allowed_decisions == ()

    def test_immutable(self):
        """RouteShape 是 NamedTuple，天然 immutable。"""
        rs = RouteShape(has_on=True, allowed_decisions=("done",))
        with pytest.raises(AttributeError):
            rs.has_on = False  # type: ignore
        with pytest.raises(AttributeError):
            rs.allowed_decisions = ("other",)  # type: ignore


class TestValidResult:
    """ValidResult 三态测试。"""

    def test_valid_default(self):
        """默认构造：valid=True，repairable=False。"""
        vr = ValidResult()
        assert vr.valid is True
        assert vr.repairable is False
        assert vr.reason == ""
        assert vr.errors == []
        assert vr.warnings == []

    def test_valid_success(self):
        """全部通过：valid=True。"""
        vr = ValidResult(valid=True)
        assert vr.valid is True
        assert vr.repairable is False

    def test_repairable(self):
        """可修复：valid=False + repairable=True。"""
        vr = ValidResult(
            valid=False,
            repairable=True,
            reason="decision 非法",
            errors=["decision 'approve' 不在 allowed_decisions ['done'] 中"],
        )
        assert vr.valid is False
        assert vr.repairable is True
        assert vr.reason == "decision 非法"
        assert len(vr.errors) == 1

    def test_not_repairable(self):
        """不可修复：valid=False + repairable=False。"""
        vr = ValidResult(
            valid=False,
            repairable=False,
            reason="缺少必需字段: task_id",
            errors=["缺少必需字段: task_id"],
        )
        assert vr.valid is False
        assert vr.repairable is False
        assert "task_id" in vr.reason

    def test_with_warnings(self):
        """valid=True 但附带 warnings。"""
        vr = ValidResult(warnings=["execution.exit_code 缺失"])
        assert vr.valid is True
        assert len(vr.warnings) == 1

    def test_field_completeness(self):
        """确保所有字段可独立设置。"""
        vr = ValidResult(
            valid=False,
            repairable=True,
            reason="test reason",
            errors=["e1", "e2"],
            warnings=["w1"],
        )
        assert vr.valid is False
        assert vr.repairable is True
        assert vr.reason == "test reason"
        assert vr.errors == ["e1", "e2"]
        assert vr.warnings == ["w1"]

    def test_multiple_errors(self):
        """多个 errors 正确累积。"""
        vr = ValidResult(
            valid=False,
            errors=["err1", "err2", "err3"],
        )
        assert len(vr.errors) == 3
        assert vr.valid is False


class TestValidatePureFunction:
    """validate() 纯函数端到端测试。"""

    def _make_valid_data(self, **overrides):
        data = {
            "schema_version": 1,
            "task_id": "test",
            "state": "test_state",
            "status": "success",
            "summary": "all good",
            "execution": {
                "started_at": "2026-06-07T10:00:00+08:00",
                "finished_at": "2026-06-07T10:01:00+08:00",
                "exit_code": 0,
            },
        }
        data.update(overrides)
        return data

    def test_all_valid(self):
        """全部合法输入 → valid=True。"""
        from agent_workflow.validators.task_result import validate
        rs = RouteShape(has_on=True, allowed_decisions=("done",))
        vr = validate(self._make_valid_data(decision="done"), rs)
        assert vr.valid is True
        assert vr.repairable is False

    def test_schema_version_zero(self):
        """schema_version=0 → valid=False, repairable=False。"""
        from agent_workflow.validators.task_result import validate
        vr = validate(self._make_valid_data(schema_version=0), RouteShape())
        assert vr.valid is False
        assert vr.repairable is False
        assert "schema_version" in vr.errors[0]

    def test_missing_task_id(self):
        """缺少 task_id → valid=False, repairable=False。"""
        from agent_workflow.validators.task_result import validate
        data = self._make_valid_data()
        del data["task_id"]
        vr = validate(data, RouteShape())
        assert vr.valid is False
        assert vr.repairable is False
        assert any("task_id" in e for e in vr.errors)

    def test_missing_execution_started_at(self):
        """缺少 execution.started_at → valid=False, repairable=False。"""
        from agent_workflow.validators.task_result import validate
        data = self._make_valid_data()
        data["execution"] = {"finished_at": "2026-06-07T10:01:00+08:00"}
        vr = validate(data, RouteShape())
        assert vr.valid is False
        assert vr.repairable is False
        assert any("started_at" in e for e in vr.errors)

    def test_invalid_output_repairable(self):
        """status=invalid_output → valid=False, repairable=True。"""
        from agent_workflow.validators.task_result import validate
        vr = validate(self._make_valid_data(status="invalid_output"), RouteShape())
        assert vr.valid is False
        assert vr.repairable is True
        assert "invalid_output" in vr.reason

    def test_branch_node_decision_none_repairable(self):
        """分支节点 + decision=None → repairable=True。"""
        from agent_workflow.validators.task_result import validate
        rs = RouteShape(has_on=True, allowed_decisions=("approve", "revise"))
        vr = validate(self._make_valid_data(decision=None), rs)
        assert vr.valid is False
        assert vr.repairable is True
        assert "decision" in vr.reason

    def test_branch_node_decision_not_allowed_repairable(self):
        """分支节点 + decision 不在 allowed → repairable=True。"""
        from agent_workflow.validators.task_result import validate
        rs = RouteShape(has_on=True, allowed_decisions=("done", "fail"))
        vr = validate(self._make_valid_data(decision="approve"), rs)
        assert vr.valid is False
        assert vr.repairable is True
        assert "decision" in vr.reason

    def test_linear_node_decision_none_valid(self):
        """线性节点 + decision=None → valid=True（线性节点不需要 decision）。"""
        from agent_workflow.validators.task_result import validate
        rs = RouteShape(has_next=True)
        vr = validate(self._make_valid_data(decision=None), rs)
        assert vr.valid is True

    def test_linear_node_decision_not_empty_valid(self):
        """线性节点 + decision 非空 → valid=True（仅 nice-to-have warning，首版跳过）。"""
        from agent_workflow.validators.task_result import validate
        rs = RouteShape(has_next=True)
        vr = validate(self._make_valid_data(decision="done"), rs)
        assert vr.valid is True  # 线性节点不强制校验 decision

    def test_invalid_status_not_repairable(self):
        """status 不在 VALID_STATUSES → valid=False, repairable=False。"""
        from agent_workflow.validators.task_result import validate
        vr = validate(self._make_valid_data(status="not_a_status"), RouteShape())
        assert vr.valid is False
        assert vr.repairable is False
        assert "status" in vr.errors[0]

    def test_missing_execution_field_blocking(self):
        """缺少 execution 整体 → valid=False, repairable=False。"""
        from agent_workflow.validators.task_result import validate
        data = self._make_valid_data()
        del data["execution"]
        vr = validate(data, RouteShape())
        assert vr.valid is False
        assert vr.repairable is False

    def test_artifact_warnings_non_blocking(self):
        """artifact 缺少 name/staging_path → warning（非阻塞）。"""
        from agent_workflow.validators.task_result import validate
        data = self._make_valid_data()
        data["artifacts"] = [{"type": "markdown"}]  # 缺少 name 和 staging_path
        vr = validate(data, RouteShape())
        assert vr.valid is True
        assert any("name" in w for w in vr.warnings)
        assert any("staging_path" in w for w in vr.warnings)

    def test_repairable_false_direct_route_to_failed(self):
        """repairable=False → Runner 应直接 failed，不走 Repair。"""
        from agent_workflow.validators.task_result import validate
        vr = validate(self._make_valid_data(schema_version=0), RouteShape())
        assert vr.valid is False
        assert vr.repairable is False
        # Runner 检查此状态后直接设置 status=failed, decision=None

    def test_compound_error_repairable(self):
        """复合错误（invalid_output + decision=None）一次判定 repairable。"""
        from agent_workflow.validators.task_result import validate
        rs = RouteShape(has_on=True, allowed_decisions=("done", "fail"))
        vr = validate(
            self._make_valid_data(status="invalid_output", decision=None), rs
        )
        assert vr.valid is False
        assert vr.repairable is True
