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


def _check_staging_sandbox(staging_path: str, sandbox_roots: list[str]) -> bool:
    """检查 staging 文件是否合法落在某个沙箱根内、且路径含 staging 段。

    worktree 模式下 agent 在 project_root 沙箱写文件，run_root 在主仓，
    两者是不同的树。staging 文件落在任一根下都合法，但必须经由 staging/
    目录（防止 agent 把任意源码文件登记为产物）。

    Args:
        staging_path: 待检查的 staging 路径（应为绝对路径）
        sandbox_roots: 允许的沙箱根列表（如 [project_root, run_root]）

    Returns:
        True 如果 staging_path 在任一沙箱根内且路径含 "staging" 段。
    """
    try:
        resolved = os.path.realpath(os.path.abspath(staging_path))
    except (ValueError, OSError):
        return False

    # 路径必须含 staging 段（区分大小写按 OS 处理已由 realpath 规范化）
    parts = resolved.replace("\\", "/").split("/")
    if "staging" not in parts:
        return False

    for root in sandbox_roots:
        if not root:
            continue
        try:
            resolved_root = os.path.realpath(os.path.abspath(root))
            if os.path.commonpath([resolved, resolved_root]) == resolved_root:
                return True
        except (ValueError, OSError):
            continue
    return False



def promote_artifact(
    staging_path: str,
    artifact_path: str,
    run_root: str,
    artifact_name: str = "",
    staging_root: str | None = None,
) -> PromotionResult:
    """将 staging 文件提升到正式 artifacts。

    规则:
    - 检查 staging 文件是否存在
    - P0g: 检查 staging/artifact 路径 containment（防止路径穿越）
    - 复制到 artifacts 目录
    - 原 staging 文件保留用于排查
    - 返回 PromotionResult

    Args:
        staging_root: staging 文件所在的沙箱根。worktree 模式下 agent 在
            project_root 写 staging，与 run_root（主仓）不在同一棵树，需显式传入。
            为 None 时回退到 run_root（普通模式 staging 在 run_root/staging 下）。
    """
    # staging_root 用于解析相对 staging_path 并界定沙箱；默认与 run_root 同
    staging_root = staging_root or run_root

    # 解析路径（确保是绝对路径或相对于对应根）
    if not os.path.isabs(staging_path):
        staging_path = os.path.join(staging_root, staging_path)
    if not os.path.isabs(artifact_path):
        artifact_path = os.path.join(run_root, artifact_path)

    # P0g: 路径 containment 检查
    artifacts_base = os.path.join(run_root, "artifacts")

    # staging 文件可落在 project_root 或 run_root 沙箱，但必须经由 staging/ 段
    sandbox_roots = [staging_root]
    if run_root not in sandbox_roots:
        sandbox_roots.append(run_root)
    if not _check_staging_sandbox(staging_path, sandbox_roots):
        return PromotionResult(
            ok=False,
            artifact_name=artifact_name,
            staging_path=staging_path,
            artifact_path=artifact_path,
            error=f"staging 路径逃逸沙箱: {staging_path}（必须在 {sandbox_roots} 的 staging/ 之下）",
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
