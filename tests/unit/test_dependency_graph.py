"""DependencyGraph 单元测试。"""

import pytest

from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.dependency_graph import DependencyGraph


# ============================================================
# Fixtures
# ============================================================

def _make_items(*ids: str) -> list[WorkItem]:
    """快速创建一组 PENDING item（无依赖）。"""
    return [WorkItem(id=i, title=f"title_{i}") for i in ids]


# ============================================================
# validate 测试
# ============================================================

class TestValidate:
    """DependencyGraph.validate 测试。"""

    def test_valid_chain(self):
        """合法链式依赖。"""
        items = [
            WorkItem(id="s1", title="step1"),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
            WorkItem(id="s3", title="step3", depends_on=["s2"]),
        ]
        errors = DependencyGraph.validate(items)
        assert errors == []

    def test_valid_diamond(self):
        """合法菱形依赖。"""
        items = [
            WorkItem(id="s1", title="step1"),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
            WorkItem(id="s3", title="step3", depends_on=["s1"]),
            WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
        ]
        errors = DependencyGraph.validate(items)
        assert errors == []

    def test_valid_no_deps(self):
        """无依赖的 items。"""
        items = [
            WorkItem(id="s1", title="step1"),
            WorkItem(id="s2", title="step2"),
        ]
        errors = DependencyGraph.validate(items)
        assert errors == []

    def test_valid_empty(self):
        """空列表合法。"""
        errors = DependencyGraph.validate([])
        assert errors == []

    def test_self_dependency_rejected(self):
        """自依赖应被拒绝。"""
        items = [
            WorkItem(id="s1", title="step1", depends_on=["s1"]),
        ]
        errors = DependencyGraph.validate(items)
        assert len(errors) > 0
        assert any("不允许依赖自身" in e for e in errors)

    def test_missing_dependency_rejected(self):
        """缺失依赖应被拒绝。"""
        items = [
            WorkItem(id="s1", title="step1"),
            WorkItem(id="s2", title="step2", depends_on=["nonexistent"]),
        ]
        errors = DependencyGraph.validate(items)
        assert len(errors) > 0
        assert any("不存在" in e for e in errors)

    def test_simple_cycle_rejected(self):
        """简单环 A→B→A 应被拒绝。"""
        items = [
            WorkItem(id="a", title="A", depends_on=["b"]),
            WorkItem(id="b", title="B", depends_on=["a"]),
        ]
        errors = DependencyGraph.validate(items)
        assert len(errors) > 0
        assert any("依赖环" in e for e in errors)

    def test_three_node_cycle_rejected(self):
        """三节点环 A→B→C→A 应被拒绝。"""
        items = [
            WorkItem(id="a", title="A", depends_on=["c"]),
            WorkItem(id="b", title="B", depends_on=["a"]),
            WorkItem(id="c", title="C", depends_on=["b"]),
        ]
        errors = DependencyGraph.validate(items)
        assert len(errors) > 0
        assert any("依赖环" in e for e in errors)


# ============================================================
# ready_items 测试
# ============================================================

class TestReadyItems:
    """DependencyGraph.ready_items 测试。"""

    def test_no_deps_all_ready(self):
        """无依赖的 PENDING item 全部就绪。"""
        items = _make_items("s1", "s2", "s3")
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 3

    def test_dep_not_completed_not_ready(self):
        """依赖未完成的 item 不就绪。"""
        items = [
            WorkItem(id="s1", title="step1"),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
        ]
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 1
        assert ready[0].id == "s1"

    def test_dep_completed_then_ready(self):
        """依赖完成后的 item 就绪。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.COMPLETED),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
        ]
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 1
        assert ready[0].id == "s2"

    def test_dep_failed_item_not_ready(self):
        """依赖失败的 item 不就绪。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.FAILED),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
        ]
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 0

    def test_already_completed_not_ready(self):
        """已完成的 item 不再出现在 ready 中。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.COMPLETED),
        ]
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 0

    def test_already_failed_not_ready(self):
        """已失败的 item 不再出现在 ready 中。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.FAILED),
        ]
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 0

    def test_diamond_middle_ready(self):
        """菱形依赖中 step2 和 step3 同时就绪。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.COMPLETED),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
            WorkItem(id="s3", title="step3", depends_on=["s1"]),
            WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
        ]
        ready = DependencyGraph.ready_items(items)
        ready_ids = {item.id for item in ready}
        assert ready_ids == {"s2", "s3"}

    def test_diamond_final_ready_after_both_complete(self):
        """菱形依赖中 s2 和 s3 都完成后 s4 才就绪。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.COMPLETED),
            WorkItem(id="s2", title="step2", status=ItemStatus.COMPLETED, depends_on=["s1"]),
            WorkItem(id="s3", title="step3", status=ItemStatus.COMPLETED, depends_on=["s1"]),
            WorkItem(id="s4", title="step4", depends_on=["s2", "s3"]),
        ]
        ready = DependencyGraph.ready_items(items)
        assert len(ready) == 1
        assert ready[0].id == "s4"


# ============================================================
# blocked_items 测试
# ============================================================

class TestBlockedItems:
    """DependencyGraph.blocked_items 测试。"""

    def test_dep_pending_blocks_downstream(self):
        """上游 PENDING 阻塞下游。"""
        items = [
            WorkItem(id="s1", title="step1"),
            WorkItem(id="s2", title="step2", depends_on=["s1"]),
        ]
        blocked = DependencyGraph.blocked_items(items)
        # s1 是 ready 的，s2 被阻塞
        blocked_ids = {item.id for item in blocked}
        assert blocked_ids == {"s2"}

    def test_no_deps_nothing_blocked(self):
        """无依赖的 item 全部就绪，不存在阻塞。"""
        items = _make_items("s1", "s2")
        blocked = DependencyGraph.blocked_items(items)
        assert len(blocked) == 0

    def test_all_completed_nothing_blocked(self):
        """全部完成时不存在阻塞。"""
        items = [
            WorkItem(id="s1", title="step1", status=ItemStatus.COMPLETED),
            WorkItem(id="s2", title="step2", status=ItemStatus.COMPLETED, depends_on=["s1"]),
        ]
        blocked = DependencyGraph.blocked_items(items)
        assert len(blocked) == 0
