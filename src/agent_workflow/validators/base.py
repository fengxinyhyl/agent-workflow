"""BaseValidator — 校验器基类。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """校验结果。"""

    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def merge(self, other: "ValidationResult"):
        """合并另一个校验结果。"""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        if other.errors:
            self.passed = False
        self.metadata.update(other.metadata)


class BaseValidator:
    """校验器基类。

    所有校验器继承此类，实现 validate() 方法。
    """

    name: str = "base"

    def validate(self, *args, **kwargs) -> ValidationResult:
        """执行校验，返回 ValidationResult。"""
        raise NotImplementedError
