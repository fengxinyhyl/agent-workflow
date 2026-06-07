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


def _check_path_containment(target_path: str, allowed_base: str) -> bool:
    """P0g: 检查 target_path resolve 后是否在 allowed_base 之下。

    Args:
        target_path: 要检查的路径（绝对或相对）
        allowed_base: 允许的基目录（绝对路径）

    Returns:
        True 如果 target_path 在 allowed_base 内，False 如果路径逃逸。
    """
    try:
        resolved_target = os.path.realpath(os.path.abspath(target_path))
        resolved_base = os.path.realpath(os.path.abspath(allowed_base))
        # 规范化后必须以 allowed_base 开头
        common = os.path.commonpath([resolved_target, resolved_base])
        return common == resolved_base
    except (ValueError, OSError):
        return False


def promote_artifact(
    staging_path: str,
    artifact_path: str,
    run_root: str,
    artifact_name: str = "",
) -> PromotionResult:
    """将 staging 文件提升到正式 artifacts。

    规则:
    - 检查 staging 文件是否存在
    - P0g: 检查 staging/artifact 路径 containment（防止路径穿越）
    - 复制到 artifacts 目录
    - 原 staging 文件保留用于排查
    - 返回 PromotionResult
    """
    # 解析路径（确保是绝对路径或相对于 run_root）
    if not os.path.isabs(staging_path):
        staging_path = os.path.join(run_root, staging_path)
    if not os.path.isabs(artifact_path):
        artifact_path = os.path.join(run_root, artifact_path)

    # P0g: 路径 containment 检查
    staging_base = os.path.join(run_root, "staging")
    artifacts_base = os.path.join(run_root, "artifacts")

    if not _check_path_containment(staging_path, staging_base):
        return PromotionResult(
            ok=False,
            artifact_name=artifact_name,
            staging_path=staging_path,
            artifact_path=artifact_path,
            error=f"staging 路径逃逸 run_root: {staging_path}（必须在 {staging_base} 之下）",
        )

    if not _check_path_containment(artifact_path, artifacts_base):
        return PromotionResult(
            ok=False,
            artifact_name=artifact_name,
            staging_path=staging_path,
            artifact_path=artifact_path,
            error=f"artifact 路径逃逸 run_root: {artifact_path}（必须在 {artifacts_base} 之下）",
        )

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
