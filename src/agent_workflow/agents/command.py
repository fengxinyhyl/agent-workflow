"""CommandAgent — 通用命令代理（默认禁用）。

P0: command agent 默认禁用，需要在配置中显式启用。
原因：执行任意命令存在安全风险。
"""

from __future__ import annotations

import os
from typing import Any

from .base import BaseAgent
from ..context.agent_input import AgentInput
from ..tasks.result import TaskResult, ExecutionMetadata, _now_iso


class CommandAgent(BaseAgent):
    """通用命令代理。

    ⚠️ 默认禁用。
    仅用于运行受信任的命令（如 lint、test 脚本）。
    必须在 agents 配置中显式设置 enabled: true。

    安全检查:
    - 命令必须在 allowlist 中
    - cwd 限制在 project_root 内
    - 超时控制
    """

    name = "command"
    provider = "command"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.command_template = config.get("command", "") if config else ""
        self.cwd = config.get("cwd", "{project_root}") if config else "{project_root}"
        self.timeout = config.get("timeout_seconds", 300) if config else 300
        self.enabled = config.get("enabled", False) if config else False

    def execute(self, agent_input: AgentInput) -> TaskResult:
        """执行命令。"""
        state_name = agent_input.state_name or agent_input.context.current_state or agent_input.task.name
        if not self.enabled:
            return self._create_task_result(
                agent_input.task.name, state_name,
                status="blocked",
                decision="blocked",
                summary="CommandAgent 默认禁用，需在配置中设置 enabled: true",
            )

        started_at = _now_iso()

        # 构建命令
        cmd = self._build_command(agent_input)
        if not cmd:
            return self._create_task_result(
                agent_input.task.name, state_name,
                status="invalid_output",
                decision="fail",
                summary="命令为空",
            )

        # 校验命令安全性（传入 list 形式，不经过 shell 解析，更安全）
        try:
            from ..validators.command import validate_command
            validation = validate_command(cmd, allow_write=True)
            if not validation.passed:
                return self._create_task_result(
                    agent_input.task.name, state_name,
                    status="blocked",
                    decision="blocked",
                    summary=f"命令校验失败: {', '.join(validation.errors)}",
                )
        except ImportError:
            pass

        # 解析 cwd
        cwd = self.cwd.replace(
            "{project_root}", agent_input.context.project_root
        )
        cwd = os.path.abspath(cwd)

        # 执行（带取消轮询）
        process, status, exit_code, stdout, stderr = self._run_with_cancel_poll(
            cmd if isinstance(cmd, list) else cmd.split(),
            cwd=cwd,
            timeout=self.timeout,
            agent_input=agent_input,
        )

        finished_at = _now_iso()

        if status == "cancelled":
            return TaskResult(
                schema_version=1,
                task_id=agent_input.task.name,
                state=state_name,
                agent=self.name,
                status="cancelled",
                decision="blocked",
                summary="命令执行已被取消",
                execution=ExecutionMetadata(
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=0,
                    attempt=1,
                    exit_code=-1,
                ),
                issues=[],
            )

        if status == "timeout":
            return self._create_task_result(
                agent_input.task.name, state_name,
                status="timeout",
                decision="fail",
                summary=f"命令超时（{self.timeout}s）",
            )

        return TaskResult(
            schema_version=1,
            task_id=agent_input.task.name,
            state=state_name,
            agent=self.name,
            status="success" if exit_code == 0 else "failed",
            decision="done" if exit_code == 0 else "fail",
            summary=f"exit_code={exit_code}\nstdout: {stdout[:500]}\nstderr: {stderr[:500]}",
            execution=ExecutionMetadata(
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0,
                attempt=1,
                exit_code=exit_code,
            ),
            issues=[],
        )

    def _build_command(self, agent_input: AgentInput) -> list[str] | None:
        """构建命令。"""
        template = self.command_template
        if not template:
            return None

        # 替换变量
        ctx = agent_input.context
        template = template.replace("{project_root}", ctx.project_root)
        template = template.replace("{run_root}", ctx.run_root)
        template = template.replace("{goal}", ctx.goal)

        if isinstance(template, str):
            return template.split()
        return template
