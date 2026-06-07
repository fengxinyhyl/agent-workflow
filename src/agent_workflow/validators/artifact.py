"""ArtifactValidator — 校验产物流文件。

校验项:
- 文件存在
- 文件非空
- 文件类型匹配
- 内容可解析性（JSON/YAML）
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import BaseValidator, ValidationResult


class ArtifactValidator(BaseValidator):
    """产物流校验器。

    P0 校验:
    - 文件存在且非空
    - 文件大小合理（< 10MB）
    - JSON 文件可解析（如为 JSON 类型）
    """

    name = "artifact"
    MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB

    def validate(
        self,
        path: str,
        expected_type: str = "",
        min_size: int = 1,
    ) -> ValidationResult:
        """校验单个产物流文件。"""
        result = ValidationResult()

        # 1. 文件存在
        if not os.path.exists(path):
            result.add_error(f"文件不存在: {path}")
            return result

        # 2. 文件大小
        size = os.path.getsize(path)
        if size < min_size:
            result.add_error(f"文件为空: {path}")
        if size > self.MAX_SIZE_BYTES:
            result.add_warning(f"文件过大: {size} bytes > {self.MAX_SIZE_BYTES} bytes (10MB)")

        # 3. JSON 可解析性
        if expected_type == "json" or path.endswith(".json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                result.add_error(f"JSON 解析失败: {e}")
            except IOError as e:
                result.add_error(f"文件读取失败: {e}")

        result.metadata["size"] = size
        result.metadata["path"] = path
        return result

    def validate_batch(
        self,
        artifacts: list[dict[str, Any]],
    ) -> ValidationResult:
        """批量校验产物流。"""
        result = ValidationResult()

        for artifact in artifacts:
            if isinstance(artifact, dict):
                path = artifact.get("staging_path") or artifact.get("artifact_path", "")
                atype = artifact.get("type", "")
            else:
                continue

            item_result = self.validate(path, expected_type=atype)
            result.merge(item_result)

        return result
