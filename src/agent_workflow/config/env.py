"""环境变量解析器。

支持配置文件中使用 {VAR_NAME} 占位符引用环境变量。
也负责管理项目级别的变量（如 run_root、project_root 等）。
"""

from __future__ import annotations

import os
import re
from typing import Any


# 变量占位符正则: {VAR_NAME}
_VAR_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class EnvResolver:
    """环境变量解析器。

    支持：
    - 系统环境变量
    - 运行时变量覆盖（如 project_root、run_id 等）
    - .env 文件加载（可选）
    """

    def __init__(self, overrides: dict[str, str] | None = None):
        self._overrides = overrides or {}

    def set(self, key: str, value: str):
        """设置运行时变量。"""
        self._overrides[key] = value

    def get(self, key: str, default: str = "") -> str:
        """获取变量值（优先 overrides → 环境变量 → default）。"""
        if key in self._overrides:
            return self._overrides[key]
        return os.environ.get(key, default)

    def resolve(self, value: str) -> str:
        """展开字符串中的 {VAR_NAME} 占位符。"""
        if not isinstance(value, str):
            return value

        def _replace(match):
            var_name = match.group(1)
            return self.get(var_name, match.group(0))

        return _VAR_PATTERN.sub(_replace, value)

    def resolve_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """递归展开字典中所有字符串值。"""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.resolve(value)
            elif isinstance(value, dict):
                result[key] = self.resolve_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.resolve(item) if isinstance(item, str)
                    else self.resolve_dict(item) if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result
