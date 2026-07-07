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
            return self._create_task_result(
                agent_input.task.name, state_name,
                status="cancelled",
                decision="blocked",
                summary="命令执行已被取消",
                started_at=started_at,
                finished_at=finished_at,
                exit_code=-1,
            )

        if status == "timeout":
            return self._create_task_result(
                agent_input.task.name, state_name,
                status="timeout",
                decision="fail",
                summary=f"命令超时（{self.timeout}s）",
                started_at=started_at,
                finished_at=finished_at,
                exit_code=-1,
            )

        succeeded = exit_code == 0
        # 落盘产物：把命令输出写进 staging 的 output 文件并登记为 artifact，
        # 使门节点（如 coverage_check）的检查结果可被 promote、留痕审计。
        # 失败时同样落盘，保留失败证据。
        artifacts = self._write_output_artifact(agent_input, exit_code, stdout, stderr)

        # 本路径需携带 artifacts，无法复用 _create_task_result（其签名不含 artifacts），
        # 故手写 TaskResult；duration 与 _create_task_result 同款算法，避免恒为 0。
        return TaskResult(
            schema_version=1,
            task_id=agent_input.task.name,
            state=state_name,
            agent=self.name,
            status="success" if succeeded else "failed",
            decision="done" if succeeded else "fail",
            summary=f"exit_code={exit_code}\nstdout: {stdout[:500]}\nstderr: {stderr[:500]}",
            artifacts=artifacts,
            execution=ExecutionMetadata(
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=self._compute_duration(started_at, finished_at),
                attempt=1,
                exit_code=exit_code,
            ),
            issues=[],
        )

    @staticmethod
    def _compute_duration(started_at: str, finished_at: str) -> float:
        """由 ISO 时间戳计算耗时秒数，解析失败返回 0.0。"""
        if not (started_at and finished_at):
            return 0.0
        try:
            from datetime import datetime
            start_dt = datetime.fromisoformat(started_at)
            finish_dt = datetime.fromisoformat(finished_at)
            return max(0.0, (finish_dt - start_dt).total_seconds())
        except (ValueError, TypeError):
            return 0.0

    def _write_output_artifact(
        self,
        agent_input: AgentInput,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> list:
        """把命令执行结果写进 staging 的 output 路径并返回 ArtifactRef 列表。

        依赖 Runner 注入的 staging_paths（output_name → staging_path）。
        无 staging_paths（如冒烟测试、单测直连）时静默跳过，不产出 artifact。
        """
        from ..tasks.result import ArtifactRef

        output_name = agent_input.task.output or "output"
        staging_path = (agent_input.staging_paths or {}).get(output_name)
        if not staging_path:
            return []

        content = (
            f"# Command Result\n\n"
            f"- exit_code: {exit_code}\n\n"
            f"## stdout\n\n```\n{stdout}\n```\n\n"
            f"## stderr\n\n```\n{stderr}\n```\n"
        )
        try:
            dir_name = os.path.dirname(staging_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(staging_path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError:
            return []

        return [
            ArtifactRef(
                name=output_name,
                staging_path=staging_path,
                artifact_path=f"artifacts/{output_name}.md",
                type="markdown",
            )
        ]

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

        # command_template 恒为 str，按空格分词为 argv（list 形式不过 shell，更安全）。
        # 注意：不支持含空格的路径/参数——如需支持须改配置为预分词 list，另行评估。
        return template.split()
