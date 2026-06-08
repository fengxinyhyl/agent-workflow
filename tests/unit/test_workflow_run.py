"""WorkflowRun 单元测试。"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from workflow_run import WorkflowRun, RunStatus


class TestWorkflowRun:
    """WorkflowRun 数据类测试。"""

    def test_create_default(self):
        """默认创建 PENDING 状态。"""
        run = WorkflowRun(id="test_001", name="测试工作流")
        assert run.id == "test_001"
        assert run.name == "测试工作流"
        assert run.status == RunStatus.PENDING

    def test_create_explicit_status(self):
        """显式指定状态。"""
        run = WorkflowRun(id="test_002", name="已完成", status=RunStatus.COMPLETED)
        assert run.status == RunStatus.COMPLETED

    def test_status_enum_values(self):
        """验证 RunStatus 枚举值。"""
        assert RunStatus.PENDING.value == "PENDING"
        assert RunStatus.RUNNING.value == "RUNNING"
        assert RunStatus.PAUSED.value == "PAUSED"
        assert RunStatus.COMPLETED.value == "COMPLETED"
        assert RunStatus.FAILED.value == "FAILED"

    def test_equality(self):
        """相同字段的 WorkflowRun 应相等。"""
        r1 = WorkflowRun(id="w1", name="test")
        r2 = WorkflowRun(id="w1", name="test")
        assert r1 == r2

    def test_status_mutation(self):
        """状态应可直接修改。"""
        run = WorkflowRun(id="test", name="test")
        run.status = RunStatus.RUNNING
        assert run.status == RunStatus.RUNNING
        run.status = RunStatus.COMPLETED
        assert run.status == RunStatus.COMPLETED
