"""WorkItem 单元测试。"""

import pytest

from agent_workflow.long_task.work_item import WorkItem, ItemStatus


class TestWorkItem:
    """WorkItem 数据类测试。"""

    def test_create_default(self):
        """默认创建 PENDING 状态，空依赖。"""
        item = WorkItem(id="step1", title="数据分析")
        assert item.id == "step1"
        assert item.title == "数据分析"
        assert item.status == ItemStatus.PENDING
        assert item.depends_on == []
        assert item.artifact_path is None

    def test_create_with_depends_on(self):
        """带依赖创建。"""
        item = WorkItem(
            id="step2",
            title="因子构建",
            depends_on=["step1"],
        )
        assert item.depends_on == ["step1"]

    def test_create_with_artifact_path(self):
        """带产物路径创建。"""
        item = WorkItem(
            id="step1",
            title="数据分析",
            artifact_path="output/report.md",
        )
        assert item.artifact_path == "output/report.md"

    def test_default_depends_on_is_empty(self):
        """未指定 depends_on 时默认为空列表。"""
        item = WorkItem(id="step1", title="test")
        assert item.depends_on == []
        assert isinstance(item.depends_on, list)

    def test_status_enum_values(self):
        """验证 ItemStatus 枚举值。"""
        assert ItemStatus.PENDING.value == "PENDING"
        assert ItemStatus.RUNNING.value == "RUNNING"
        assert ItemStatus.COMPLETED.value == "COMPLETED"
        assert ItemStatus.FAILED.value == "FAILED"
        assert ItemStatus.SKIPPED.value == "SKIPPED"

    def test_equality(self):
        """相同字段的 WorkItem 应相等。"""
        i1 = WorkItem(id="s1", title="test")
        i2 = WorkItem(id="s1", title="test")
        assert i1 == i2

    def test_depends_on_independent_instances(self):
        """depends_on 列表应独立于传入参数。"""
        deps = ["step1", "step2"]
        item = WorkItem(id="step3", title="test", depends_on=deps)
        deps.append("step4")
        # 创建后的列表不应受外部修改影响（dataclass field default_factory 已处理）
        assert len(item.depends_on) == 2
