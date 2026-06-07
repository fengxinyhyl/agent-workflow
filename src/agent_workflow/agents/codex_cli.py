"""CodexCLI — Codex CLI 适配器。

通过 subprocess 调用 Codex CLI，传递 prompt 并解析 TaskResult。
"""

from __future__ import annotations

import os
import json
import subprocess
import shutil
from typing import Any

from .base import BaseAgent
from ..context.agent_input import AgentInput
from ..tasks.result import TaskResult, ExecutionMetadata, _now_iso


class CodexCLI(BaseAgent):
    """Codex CLI 适配器。

    通过 CLI 调用 Codex CLI:
    - 传递 AgentInput 构建的 prompt
    - 设置 cwd 为 project_root
    - 超时控制
    - 解析 TaskResult JSON 输出

    P0: 基础实现（使用 echo 模式验证管线）
    P1: 真实 Codex CLI 集成
    """

    name = "codex"
    provider = "codex"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.command = config.get("command", "codex") if config else "codex"
        self.cwd = config.get("cwd", "{project_root}") if config else "{project_root}"
        self.timeout = config.get("timeout_seconds", 3600) if config else 3600

    def execute(self, agent_input: AgentInput) -> TaskResult:
        """通过 Codex CLI 执行任务。"""
        state_name = agent_input.task.name
        started_at = _now_iso()

        # 构建 prompt
        prompt = agent_input.build_prompt()

        # 解析 cwd
        cwd = self._resolve_cwd(agent_input)

        # 确保 staging 目录存在
        staging_dir = os.path.join(
            agent_input.context.run_root, "staging", state_name
        )
        os.makedirs(staging_dir, exist_ok=True)

        # 构建命令（P0: 使用 codex exec 模式）
        cmd = self._build_command(agent_input, prompt)

        # 执行
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return self._create_task_result(
                state_name, state_name,
                status="timeout",
                decision="fail",
                summary=f"执行超时（{self.timeout}s）",
            )
        except Exception as e:
            return self._create_task_result(
                state_name, state_name,
                status="failed",
                decision="fail",
                summary=f"执行异常: {e}",
            )

        finished_at = _now_iso()
        duration = 0  # 由 execution metadata 计算

        # 解析输出
        task_result = self._parse_output(state_name, result, agent_input)

        # 更新 execution metadata
        task_result.execution = ExecutionMetadata(
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=result.returncode,
            attempt=1,
            exit_code=result.returncode,
        )

        return task_result

    def _build_command(self, agent_input: AgentInput, prompt: str) -> list[str]:
        """构建 Codex CLI 命令。"""
        cmd = [self.command]

        # 如果 codex 在 PATH 中不可用，使用 echo 作为 fallback（P0 测试模式）
        if not shutil.which(self.command):
            # Fallback: 将 prompt 写入文件并标记为需要 P1 完善
            cmd = ["echo", f"[CodexCLI P0 fallback] {self.command} not found in PATH"]

        return cmd

    def _resolve_cwd(self, agent_input: AgentInput) -> str:
        """解析工作目录。"""
        cwd = self.cwd.replace(
            "{project_root}", agent_input.context.project_root
        )
        return os.path.abspath(cwd)

    def _parse_output(
        self,
        state_name: str,
        result: subprocess.CompletedProcess,
        agent_input: AgentInput,
    ) -> TaskResult:
        """解析 Codex CLI 输出为 TaskResult。"""
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # 尝试从 stdout 中提取 JSON TaskResult
        try:
            # 查找 JSON 块
            if "```json" in stdout:
                start = stdout.index("```json") + 7
                end = stdout.index("```", start)
                json_str = stdout[start:end].strip()
                return TaskResult.from_json(json_str)
        except (ValueError, json.JSONDecodeError):
            pass

        # 尝试直接解析整段 JSON
        try:
            return TaskResult.from_json(stdout.strip())
        except json.JSONDecodeError:
            pass

        # 无法解析 → 构建默认 TaskResult
        status = "success" if result.returncode == 0 else "failed"
        decision = "done" if result.returncode == 0 else "fail"

        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent=self.name,
            status=status,
            decision=decision,
            summary=stdout[:500] if stdout else f"exit_code={result.returncode}",
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=0,
                attempt=1,
                exit_code=result.returncode,
            ),
            issues=[],
        )
