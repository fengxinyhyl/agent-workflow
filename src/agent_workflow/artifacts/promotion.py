"""Promotion — 从 staging 提升到正式 artifacts。

流程:
  1. Agent 写 staging/<state>/output.md
  2. Validator 校验 staging 内容
  3. 通过后 promote 到 artifacts/output.md
  4. 失败时 staging 保留，artifacts 不受污染
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any


@dataclass
class PromotionResult:
    """Promotion 结果。"""

    ok: bool = True
    artifact_name: str = ""
    staging_path: str = ""
    artifact_path: str = ""
    error: str = ""


def promote_artifact(
    staging_path: str,
    artifact_path: str,
    run_root: str,
    artifact_name: str = "",
) -> PromotionResult:
    """将 staging 文件提升到正式 artifacts。

    规则:
    - 检查 staging 文件是否存在
    - 复制到 artifacts 目录
    - 原 staging 文件保留用于排查
    - 返回 PromotionResult
    """
    # 解析路径（确保是绝对路径或相对于 run_root）
    if not os.path.isabs(staging_path):
        staging_path = os.path.join(run_root, staging_path)
    if not os.path.isabs(artifact_path):
        artifact_path = os.path.join(run_root, artifact_path)

    # 检查 staging 文件存在
    if not os.path.exists(staging_path):
        return PromotionResult(
            ok=False,
            artifact_name=artifact_name,
            staging_path=staging_path,
            artifact_path=artifact_path,
            error=f"staging 文件不存在: {staging_path}",
        )

    # 确保 artifacts 目录存在
    os.makedirs(os.path.dirname(artifact_path), exist_ok=True)

    # 复制（不删除 staging）
    try:
        shutil.copy2(staging_path, artifact_path)
    except OSError as e:
        return PromotionResult(
            ok=False,
            artifact_name=artifact_name,
            staging_path=staging_path,
            artifact_path=artifact_path,
            error=f"复制失败: {e}",
        )

    return PromotionResult(
        ok=True,
        artifact_name=artifact_name,
        staging_path=staging_path,
        artifact_path=artifact_path,
    )


def validate_and_promote(
    run_root: str,
    state_name: str,
    staging_filename: str,
    artifact_name: str,
    validator: callable | None = None,
) -> PromotionResult:
    """校验并 promote 一个 artifact。

    如果提供了 validator，先校验再 promote。
    """
    staging_path = os.path.join(run_root, "staging", state_name, staging_filename)
    artifact_path = os.path.join(run_root, "artifacts", staging_filename)

    # 可选校验
    if validator is not None:
        try:
            validation_ok = validator(staging_path)
            if not validation_ok:
                return PromotionResult(
                    ok=False,
                    artifact_name=artifact_name,
                    staging_path=staging_path,
                    artifact_path=artifact_path,
                    error="校验未通过",
                )
        except Exception as e:
            return PromotionResult(
                ok=False,
                artifact_name=artifact_name,
                staging_path=staging_path,
                artifact_path=artifact_path,
                error=f"校验异常: {e}",
            )

    return promote_artifact(
        staging_path=staging_path,
        artifact_path=artifact_path,
        run_root=run_root,
        artifact_name=artifact_name,
    )


def list_artifacts(run_root: str) -> dict[str, str]:
    """列出所有已 promote 的正式 artifacts。"""
    artifacts_dir = os.path.join(run_root, "artifacts")
    if not os.path.exists(artifacts_dir):
        return {}

    artifacts = {}
    for root, dirs, files in os.walk(artifacts_dir):
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, artifacts_dir)
            artifacts[rel_path] = full_path

    return artifacts
