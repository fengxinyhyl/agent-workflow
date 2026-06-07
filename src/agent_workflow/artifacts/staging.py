"""Staging — Agent 产物的临时存放区。

目录结构（v4 计划 §9）:
  .agent-workflow/runs/<run_id>/
    staging/
      <state>/
        output.md
        task_result.json
    artifacts/
      plan.md
      review.md
      ...
    logs/
    events.jsonl
    workflow_state.json
"""

from __future__ import annotations

import os


def ensure_staging_dir(run_root: str, state_name: str) -> str:
    """确保 staging 目录存在，返回目录路径。"""
    staging_dir = os.path.join(run_root, "staging", state_name)
    os.makedirs(staging_dir, exist_ok=True)
    return staging_dir


def get_staging_path(run_root: str, state_name: str, filename: str) -> str:
    """获取 staging 文件路径（不创建目录）。"""
    return os.path.join(run_root, "staging", state_name, filename)


def get_staging_task_result_path(run_root: str, state_name: str) -> str:
    """获取 TaskResult 的 staging 路径。"""
    return get_staging_path(run_root, state_name, "task_result.json")


def list_staging_files(run_root: str, state_name: str) -> list[str]:
    """列出某个 state 的 staging 目录中的所有文件。"""
    staging_dir = os.path.join(run_root, "staging", state_name)
    if not os.path.exists(staging_dir):
        return []
    return os.listdir(staging_dir)
