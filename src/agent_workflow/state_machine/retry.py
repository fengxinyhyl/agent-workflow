"""Retry — 重试逻辑。

默认 dry-run 只读预览（不执行规范化变更）。
必须显式 dispatch 才执行真实重试。

核心流程：
  1. 从 workflow_state.json 加载 RunContext
  2. 从 _workflow_snapshot 重建 WorkflowConfig
  3. 确定重试起点 state
  4. dry-run: 返回预览步骤
  5. dispatch: 重置状态、清理 staging、恢复 Runner、重新进入主循环
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from ..config.models import WorkflowConfig
from ..context.run_context import RunContext


@dataclass
class RetryResult:
    """重试结果。"""

    ok: bool = True
    error: str = ""
    run_id: str = ""
    from_state: str = ""
    dry_run: bool = True
    final_state: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)


def _resolve_retry_state(context: RunContext, from_state: str | None) -> str:
    """确定重试起点 state。

    优先级：
      1. from_state 显式指定
      2. 若当前停在 gate 状态，从该 gate 重试
      3. 若当前是终止状态（done/failed/cancelled），回退到 state_history 最后一条
      4. 否则从 current_state 重试
    """
    if from_state:
        return from_state

    current = context.current_state

    # 停在 gate 状态（_paused_at_gate 标记存在）
    if context.workflow_variables.get("_paused_at_gate"):
        return current

    # 终止状态 → 回退到最后执行的 state
    if current in ("done", "failed", "cancelled"):
        if context.state_history:
            return context.state_history[-1]
        return current

    # 普通非终止状态
    return current


def _build_dry_run_steps(
    context: RunContext,
    retry_state: str,
    workflow: WorkflowConfig,
) -> list[dict[str, Any]]:
    """构建 dry-run 预览步骤列表。"""
    steps: list[dict[str, Any]] = []

    # 1. 当前状态概览
    steps.append({
        "action": "load_workflow_state",
        "status": "ok",
        "detail": {
            "run_id": context.run_id,
            "current_state": context.current_state,
            "state_history": context.state_history,
            "artifacts": dict(context.artifacts),
            "gate_paused": context.workflow_variables.get("_paused_at_gate", ""),
        },
    })

    # 2. 解析重试起点
    state = workflow.get_state(retry_state)
    task = workflow.get_task(state.task) if state and state.task else None
    steps.append({
        "action": "resolve_from_state",
        "status": "ok",
        "detail": {
            "retry_state": retry_state,
            "task": state.task if state else None,
            "task_instruction": task.instruction[:100] if task and task.instruction else "",
            "previous_attempts": context.get_attempt(retry_state),
            "has_previous_result": retry_state in context.task_results,
        },
    })

    # 2.5 上次失败诊断（从事件日志分析失败原因）
    from ..observability.jsonl_sink import read_log
    from .retry_diagnose import diagnose_last_failure

    events = read_log(context.run_id, run_root=context.run_root)
    if not isinstance(events, list) or not events:
        diagnosis = {
            "kind": "unknown",
            "reason": "无事件日志可供诊断",
            "retry_recommended": True,
            "detail": {},
        }
    else:
        diagnosis = diagnose_last_failure(events)

    steps.append({
        "action": "diagnose_last_failure",
        "status": "ok" if diagnosis.get("retry_recommended", True) else "would_block",
        "detail": diagnosis,
    })

    # 3. Guard 检查预览
    guard_config = workflow.guards
    guard_checks = []
    if guard_config.max_visits > 0:
        visits = context.get_attempt(retry_state)
        guard_checks.append({
            "type": "max_visits",
            "current": visits,
            "max": guard_config.max_visits,
            "would_pass": visits < guard_config.max_visits,
        })
    if guard_config.max_retries > 0:
        attempts = context.get_attempt(retry_state)
        guard_checks.append({
            "type": "max_retries",
            "current": attempts,
            "max": guard_config.max_retries,
            "would_pass": attempts <= guard_config.max_retries,
        })
    steps.append({
        "action": "validate_guard_before_retry",
        "status": "ok" if all(g["would_pass"] for g in guard_checks) else "would_block",
        "detail": guard_checks,
    })

    # 4. 将执行的操作
    ops = []
    if retry_state in context.task_results:
        ops.append(f"清除 task_results['{retry_state}']")
    if context.get_attempt(retry_state) > 0:
        ops.append(f"重置 attempts['{retry_state}'] (当前 {context.get_attempt(retry_state)})")
    staging_dir = os.path.join(context.run_root, "staging", retry_state)
    if os.path.exists(staging_dir):
        ops.append(f"清理 {staging_dir}")
    if context.workflow_variables.get("_paused_at_gate"):
        ops.append("清除 gate 暂停标记")
    steps.append({
        "action": "replay_from_state",
        "status": "would_execute",
        "detail": {
            "operations": ops,
            "next_states": list(state.on.values()) if state else [],
            "default_next": state.default if state else "failed",
        },
    })

    return steps


def _reset_state_for_retry(context: RunContext, state_name: str):
    """重置上下文以重试指定 state。

    执行以下操作：
      - 清除该 state 的 task_result
      - 重置 attempt 计数（使 guard 检查通过）
      - 清除 gate 暂停标记
      - 清理 staging/<state>/ 目录
    """
    # 清除旧 task result
    context.task_results.pop(state_name, None)

    # 重置 attempt 计数
    context.attempts[state_name] = 0

    # 清除 gate 标记
    context.workflow_variables.pop("_paused_at_gate", None)
    context.workflow_variables.pop("_run_status", None)

    # 清理 staging 目录
    staging_dir = os.path.join(context.run_root, "staging", state_name)
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir)

    # 移除取消标记文件
    cancel_path = os.path.join(context.run_root, "cancelled")
    if os.path.exists(cancel_path):
        try:
            os.remove(cancel_path)
        except OSError:
            pass

    context.save()


def retry_run(
    run_id: str,
    from_state: str | None = None,
    dry_run: bool = True,
    run_root: str | None = None,
    project_root: str | None = None,
    agents: dict[str, Any] | None = None,
    skills_dir: str | None = None,
) -> dict[str, Any]:
    """重试一个运行。

    参数：
      run_id: 要重试的 Run ID
      from_state: 从哪个 state 开始重试（None = 自动检测中断点）
      dry_run: True = 只读预览，False = 真实执行
      run_root: 运行根目录（可选，默认从 .agent-workflow/runs/ 查找）
      project_root: 项目根目录（用于 Runner 恢复）。None 时回退到快照中的
                    context.project_root（如原 run 跑在 worktree，则续在同一 worktree）。
      agents: Agent registry 字典（可选，不提供则 fallback 到 mock）
      skills_dir: Skills 目录路径（可选）

    dry-run 模式：
      - 读取当前 run 的状态
      - 列出将要重试的 steps
      - 不执行任何外部 CLI 调用
      - 不修改任何规范化 sidecar

    dispatch 模式：
      - 加载上下文和工作流配置
      - 重置目标 state 的执行记录
      - 清理 staging 目录
      - 恢复 Runner 并重新进入主循环
    """
    # ── 1. 加载上下文 ──────────────────────────────────────
    if not run_root:
        return {
            "ok": False,
            "error": "run_root 未指定且无法自动发现",
            "run_id": run_id,
            "from_state": from_state or "auto-detect",
            "dry_run": dry_run,
        }

    try:
        context = RunContext.load(run_root)
    except FileNotFoundError:
        return {
            "ok": False,
            "error": f"workflow_state.json 未找到: {run_root}",
            "run_id": run_id,
            "from_state": from_state or "auto-detect",
            "dry_run": dry_run,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"加载 workflow_state.json 失败: {e}",
            "run_id": run_id,
            "from_state": from_state or "auto-detect",
            "dry_run": dry_run,
        }

    # ── 2. 确定重试起点 ────────────────────────────────────
    retry_state = _resolve_retry_state(context, from_state)

    # ── 3. 重建工作流配置 ──────────────────────────────────
    snapshot = context.workflow_variables.get("_workflow_snapshot", {})
    if not snapshot:
        return {
            "ok": False,
            "error": "workflow snapshot 缺失（_workflow_snapshot 为空），无法重试",
            "run_id": run_id,
            "from_state": retry_state,
            "dry_run": dry_run,
        }

    try:
        workflow = WorkflowConfig.from_dict(snapshot)
    except Exception as e:
        return {
            "ok": False,
            "error": f"从 snapshot 重建 WorkflowConfig 失败: {e}",
            "run_id": run_id,
            "from_state": retry_state,
            "dry_run": dry_run,
        }

    # 校验 retry_state 在 workflow 中存在
    if retry_state not in workflow.states:
        return {
            "ok": False,
            "error": f"state '{retry_state}' 不在 workflow 中（可用 states: {list(workflow.states.keys())}）",
            "run_id": run_id,
            "from_state": retry_state,
            "dry_run": dry_run,
        }

    # ── 4. Dry-run: 返回预览 ──────────────────────────────
    if dry_run:
        steps = _build_dry_run_steps(context, retry_state, workflow)
        return {
            "ok": True,
            "run_id": run_id,
            "from_state": retry_state,
            "dry_run": True,
            "steps": steps,
        }

    # ── 5. Dispatch: 真实执行 ─────────────────────────────
    # 5a. 重置上下文
    _reset_state_for_retry(context, retry_state)

    # 5b. 创建 Runner（从既有上下文恢复）
    try:
        from .runner import Runner

        runner = Runner.attach_existing(
            run_root=run_root,
            workflow=workflow,
            goal=context.goal,
            project_root=project_root or context.project_root,
            agents=agents,
            skills_dir=skills_dir,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"恢复 Runner 失败: {e}",
            "run_id": run_id,
            "from_state": retry_state,
            "dry_run": False,
        }

    # 5c. 挂载 observability sink（attach_existing 不会自动挂载）
    runner._mount_observability_sinks()

    # 5d. 设置当前状态为重试起点
    runner.context.current_state = retry_state

    # 5e. 持久化并重新进入主循环
    runner.context.save()

    try:
        final_state = runner.run()
    except Exception as e:
        return {
            "ok": False,
            "error": f"重试执行异常: {e}",
            "run_id": run_id,
            "from_state": retry_state,
            "dry_run": False,
        }

    return {
        "ok": final_state not in ("failed",),
        "run_id": run_id,
        "from_state": retry_state,
        "dry_run": False,
        "final_state": final_state,
    }
