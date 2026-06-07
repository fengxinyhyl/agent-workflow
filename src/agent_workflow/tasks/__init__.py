"""tasks 模块 — Task 配置、TaskResult、JSON Schema。"""

from .result import TaskResult, ArtifactRef, ExecutionMetadata, Issue
from .result_schema import TASK_RESULT_SCHEMA, build_task_result_schema

__all__ = [
    "TaskResult",
    "ArtifactRef",
    "ExecutionMetadata",
    "Issue",
    "TASK_RESULT_SCHEMA",
    "build_task_result_schema",
]
