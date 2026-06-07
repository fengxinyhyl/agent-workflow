"""TaskResultValidator — 校验 Agent 输出的 TaskResult。

校验项:
- JSON 可解析性
- 必需字段存在
- status 在允许值范围内
- decision 在 allowed_decisions 范围内
- execution metadata 完整
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import BaseValidator, ValidationResult
from ..tasks.result import VALID_STATUSES, VALID_DECISIONS, TaskResult


class TaskResultValidator(BaseValidator):
    """TaskResult 校验器。

    用法:
        validator = TaskResultValidator(allowed_decisions=["approve", "revise", "reject"])
        result = validator.validate_file("path/to/task_result.json")
    """

    name = "task_result"

    def __init__(self, allowed_decisions: list[str] | None = None):
        self.allowed_decisions = allowed_decisions

    def validate(self, data: dict[str, Any]) -> ValidationResult:
        """校验 TaskResult 字典。"""
        result = ValidationResult()

        # 1. schema_version
        if data.get("schema_version", 0) < 1:
            result.add_error("schema_version 必须 >= 1")

        # 2. 必需字段
        required = ["task_id", "state", "status", "decision", "summary", "execution"]
        for field in required:
            if field not in data or not data[field]:
                result.add_error(f"缺少必需字段: {field}")

        # 3. status
        status = data.get("status", "")
        if status and status not in VALID_STATUSES:
            result.add_warning(f"无效 status: '{status}'，允许值: {VALID_STATUSES}")

        # 4. decision
        decision = data.get("decision", "")
        if decision and decision not in VALID_DECISIONS:
            result.add_warning(f"无效 decision: '{decision}'，允许值: {VALID_DECISIONS}")

        # 5. allowed_decisions 检查
        if self.allowed_decisions and decision not in self.allowed_decisions:
            result.add_warning(
                f"decision '{decision}' 不在 allowed_decisions {self.allowed_decisions} 中，将走 default"
            )

        # 6. execution metadata
        execution = data.get("execution", {})
        if isinstance(execution, dict):
            if not execution.get("started_at"):
                result.add_error("execution.started_at 必填")
            if not execution.get("finished_at"):
                result.add_error("execution.finished_at 必填")
            if not execution.get("exit_code") and execution.get("exit_code") != 0:
                result.add_warning("execution.exit_code 缺失")

        # 7. artifacts
        artifacts = data.get("artifacts", [])
        for i, artifact in enumerate(artifacts):
            if isinstance(artifact, dict):
                if not artifact.get("name"):
                    result.add_warning(f"artifact[{i}] 缺少 name")
                if not artifact.get("staging_path"):
                    result.add_warning(f"artifact[{i}] 缺少 staging_path")

        return result

    def validate_file(self, path: str) -> ValidationResult:
        """从 JSON 文件加载并校验。"""
        if not os.path.exists(path):
            return ValidationResult(
                passed=False,
                errors=[f"TaskResult 文件不存在: {path}"],
            )

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return ValidationResult(
                passed=False,
                errors=[f"TaskResult JSON 解析失败: {e}"],
            )
        except IOError as e:
            return ValidationResult(
                passed=False,
                errors=[f"TaskResult 文件读取失败: {e}"],
            )

        return self.validate(data)
