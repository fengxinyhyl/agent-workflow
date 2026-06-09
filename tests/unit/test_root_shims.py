"""根目录临时 shim 的兼容性测试。"""

import importlib


def test_root_long_task_shims_reexport_packaged_symbols():
    """旧式 root import 应继续转发到 agent_workflow.long_task。"""
    cases = [
        ("workflow_run", "agent_workflow.long_task.workflow_run", ["WorkflowRun", "RunStatus"]),
        ("work_item", "agent_workflow.long_task.work_item", ["WorkItem", "ItemStatus"]),
        ("dependency_graph", "agent_workflow.long_task.dependency_graph", ["DependencyGraph"]),
        (
            "event_log",
            "agent_workflow.long_task.event_log",
            ["EventLog", "WorkflowEvent", "TZ_SHANGHAI", "VALID_EVENT_TYPES"],
        ),
        ("state_store", "agent_workflow.long_task.state_store", ["StateStore", "check_consistency"]),
        ("queue_runner", "agent_workflow.long_task.queue_runner", ["QueueRunner", "ItemHandler"]),
    ]

    for shim_name, package_name, symbols in cases:
        shim = importlib.import_module(shim_name)
        packaged = importlib.import_module(package_name)
        for symbol in symbols:
            assert getattr(shim, symbol) is getattr(packaged, symbol)
