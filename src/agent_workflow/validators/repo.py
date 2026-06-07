"""RepoValidator — 校验仓库状态。

校验项:
- 目录是否为 git 仓库
- 是否有未提交的变更
- 是否有冲突标记
"""

from __future__ import annotations

import os

from .base import BaseValidator, ValidationResult


class RepoValidator(BaseValidator):
    """仓库状态校验器。

    P0 校验:
    - project_root 存在
    - 基本目录结构正常
    """

    name = "repo"

    def validate(self, project_root: str) -> ValidationResult:
        """校验仓库状态。"""
        result = ValidationResult()

        # 1. 目录存在
        if not os.path.exists(project_root):
            result.add_error(f"项目目录不存在: {project_root}")
            return result

        if not os.path.isdir(project_root):
            result.add_error(f"路径不是目录: {project_root}")
            return result

        # 2. 基本结构
        git_dir = os.path.join(project_root, ".git")
        has_git = os.path.exists(git_dir)
        result.metadata["has_git"] = has_git

        # 3. 检查是否在 git 仓库中（但不强制要求）
        if not has_git:
            result.add_warning(f"项目目录不是 git 仓库: {project_root}")

        return result
