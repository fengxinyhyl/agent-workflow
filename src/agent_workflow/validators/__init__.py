"""validators 模块 — 各类校验器。

P0 提供:
- TaskResultValidator: 校验 Agent 输出的 TaskResult JSON
- ArtifactValidator: 校验产物文件
- RepoValidator: 校验仓库状态
- CommandValidator: 校验 CLI 命令安全性
"""

from .base import BaseValidator, ValidationResult
from .task_result import TaskResultValidator
from .artifact import ArtifactValidator
from .repo import RepoValidator
from .command import CommandValidator, validate_command, COMMAND_ALLOWLIST

__all__ = [
    "BaseValidator",
    "ValidationResult",
    "TaskResultValidator",
    "ArtifactValidator",
    "RepoValidator",
    "CommandValidator",
    "validate_command",
    "COMMAND_ALLOWLIST",
]
