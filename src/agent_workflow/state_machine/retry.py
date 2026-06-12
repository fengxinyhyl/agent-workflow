"""Retry — 重试逻辑。

默认 dry-run 只读预览（不执行规范化变更）。
必须显式 dispatch 才执行真实重试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetryResult:
    """重试结果。"""

    ok: bool = True
    error: str = ""
    run_id: str = ""
    from_state: str = ""
    dry_run: bool = True
    steps: list[dict[str, Any]] = field(default_factory=list)


def retry_run(
    run_id: str,
    from_state: str | None = None,
    dry_run: bool = True,
    run_root: str | None = None,
) -> dict[str, Any]:
    """重试一个运行。

    参数：
      run_id: 要重试的 Run ID
      from_state: 从哪个 state 开始重试（None = 从最后失败 state）
      dry_run: True = 只读预览，False = 真实执行
      run_root: 运行根目录（可选，默认从 .agent-workflow/runs/ 查找）

    dry-run 模式：
      - 读取当前 run 的状态
      - 列出将要重试的 steps
      - 不执行任何外部 CLI 调用
      - 不修改任何规范化 sidecar
    """
    if dry_run:
        return {
            "ok": True,
            "run_id": run_id,
            "from_state": from_state or "auto-detect",
            "dry_run": True,
            "steps": [
                {"action": "load_workflow_state", "status": "would_execute"},
                {"action": "resolve_from_state", "status": "would_execute"},
                {"action": "validate_guard_before_retry", "status": "would_execute"},
                {"action": "replay_from_state", "status": "would_execute"},
            ],
        }

    # 真实执行（P1 完善）
    return {
        "ok": False,
        "error": "真实 retry 功能将在 P1 实现",
        "run_id": run_id,
        "from_state": from_state or "auto-detect",
        "dry_run": False,
    }
