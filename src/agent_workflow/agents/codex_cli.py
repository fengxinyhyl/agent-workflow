from __future__ import annotations

import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from typing import Any

from .base import BaseAgent
from ._parse import _parse_task_result_text, _extract_task_result_fallback
from ..context.agent_input import AgentInput
from ..tasks.result import ExecutionMetadata, TaskResult, _now_iso


class CodexCLI(BaseAgent):
    name = "codex"
    provider = "codex"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.command = config.get("command", "codex") if config else "codex"
        self.cwd = config.get("cwd", "{project_root}") if config else "{project_root}"
        self.timeout = config.get("timeout_seconds", 3600) if config else 3600
        self.sandbox = config.get("sandbox", "read-only") if config else "read-only"

    def execute(self, agent_input: AgentInput) -> TaskResult:
        state_name = agent_input.state_name or agent_input.context.current_state or agent_input.task.name
        started_at = _now_iso()
        command = self._resolve_command(agent_input)

        prompt = agent_input.build_prompt()
        staging_dir = os.path.join(agent_input.context.staging_root, "staging", state_name)
        os.makedirs(staging_dir, exist_ok=True)

        if not shutil.which(command):
            prompt_path = os.path.join(staging_dir, "prompt.md")
            try:
                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write(prompt)
            except OSError:
                pass
            return TaskResult(
                schema_version=1,
                task_id=agent_input.task.name,
                state=state_name,
                agent=self.name,
                status="blocked",
                decision=None,
                summary=f"Codex CLI command '{command}' 未在 PATH 中找到或未配置",
                execution=ExecutionMetadata(
                    started_at=started_at,
                    finished_at=_now_iso(),
                    duration_seconds=0,
                    attempt=1,
                    exit_code=127,
                ),
                issues=[],
            )

        cwd = self._resolve_cwd(agent_input)

        # D4: packet 路径（在 _build_command 外计算，供 -o 参数和 TaskResult 使用）
        packet_dir = os.path.join(agent_input.context.run_root, "packets")
        os.makedirs(packet_dir, exist_ok=True)
        packet_path = os.path.join(packet_dir, f"{state_name}_codex_last_message.md")

        cmd = self._build_command(agent_input, prompt, command=command, packet_path=packet_path)

        # D4: 安全拦截 —— 检测危险 permission mode
        safety_error = self._assert_safe_permission(cmd)
        if safety_error is not None:
            finished_at = _now_iso()
            return self._create_task_result(
                agent_input.task.name,
                state_name,
                status="blocked",
                decision=None,
                summary=f"安全拦截(permission): {safety_error}",
                started_at=started_at,
                finished_at=finished_at,
            )

        # D1.5: 安全拦截 —— 检测 Codex sandbox
        sandbox_error = self._assert_safe_sandbox(cmd)
        if sandbox_error is not None:
            finished_at = _now_iso()
            return self._create_task_result(
                agent_input.task.name,
                state_name,
                status="blocked",
                decision=None,
                summary=f"安全拦截(sandbox): {sandbox_error}",
                started_at=started_at,
                finished_at=finished_at,
            )

        # D2: stream log 路径
        log_path = os.path.join(
            agent_input.context.run_root, "logs", f"{state_name}.codex.jsonl"
        )

        process, status, exit_code, stdout, stderr = self._run_with_cancel_poll(
            cmd,
            cwd=cwd,
            timeout=self.timeout,
            agent_input=agent_input,
            stdin_text=prompt,
            stream_log_path=log_path,
        )

        finished_at = _now_iso()
        pid = process.pid if process else None

        # ── 5 状态 metadata 补齐 ──
        if status == "cancelled":
            return TaskResult(
                schema_version=1,
                task_id=agent_input.task.name,
                state=state_name,
                agent=self.name,
                status="cancelled",
                decision=None,
                summary="任务已被取消",
                execution=ExecutionMetadata(
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=0,
                    attempt=1,
                    exit_code=-1,
                    pid=pid,
                ),
                issues=[],
                session_id="",
                log_path=log_path,
                packet_path=packet_path,
            )

        if status == "timeout":
            task_result = self._create_task_result(
                agent_input.task.name,
                state_name,
                status="timeout",
                decision=None,
                summary=f"执行超时({self.timeout}s)",
                started_at=started_at,
                finished_at=finished_at,
                exit_code=-1,
                pid=pid,
            )
            task_result.log_path = log_path
            task_result.packet_path = packet_path
            return task_result

        # D1/D3: 解析 stream summary（thread_id + token_usage）
        stream_summary = self.parse_codex_stream_summary(stdout)

        # 解析 TaskResult（从 stdout 中的 agent_message）
        task_result = self._parse_stream_output(state_name, stdout, stderr, agent_input)

        # 填充 execution metadata（含真实 duration + pid）
        # 保留 _parse 恢复时写入的协议轴字段（protocol_origin / recovery）
        prev_exec = task_result.get_execution()
        task_result.execution = ExecutionMetadata(
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=0,
            attempt=1,
            exit_code=exit_code,
            pid=pid,
            protocol_origin=prev_exec.protocol_origin,
            recovery=prev_exec.recovery,
        )

        # D1: thread_id → session_id
        thread_id = stream_summary.get("thread_id")
        task_result.session_id = thread_id or ""

        # D3: token_usage
        task_result.token_usage = stream_summary.get("token_usage", {})

        # D2: log_path
        task_result.log_path = log_path

        # D4: packet_path
        task_result.packet_path = packet_path

        # D4: 确保 packet 文件存在（含可读内容）
        if not os.path.exists(packet_path):
            self._write_packet_content(packet_path, stdout, state_name)

        return task_result

    def _write_packet_content(self, packet_path: str, stdout: str, state_name: str) -> None:
        """D4: 写入 packet 文件，包含至少一项可读内容。

        优先级：
        1. 从 JSONL 提取 agent_message 文本
        2. 从 turn.completed 提取状态摘要
        3. 明确的 NO_AGENT_MESSAGE marker
        """
        import json as _json

        agent_messages: list[str] = []
        errors: list[str] = []
        status = "unknown"

        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped or not stripped.startswith("{"):
                continue
            try:
                event = _json.loads(stripped)
            except (ValueError, _json.JSONDecodeError):
                continue
            event_type = event.get("type", "")
            if event_type == "turn.completed":
                status = "completed"
            elif event_type == "turn.failed":
                status = "failed"
                errors.append(str(event.get("error", event.get("message", "turn.failed"))))
            elif event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", "")
                    if text:
                        agent_messages.append(text)

        with open(packet_path, "w", encoding="utf-8") as f:
            f.write(f"# {state_name} codex debug packet\n\n")
            f.write(f"Status: {status}\n\n")
            if agent_messages:
                f.write("## 最后一条 agent message\n\n")
                f.write(agent_messages[-1])
                f.write("\n\n")
            elif errors:
                f.write("## Errors\n\n")
                for err in errors:
                    f.write(f"- {err}\n")
                f.write("\n")
            else:
                f.write("<!-- NO_AGENT_MESSAGE -->\n")
                f.write("无 agent_message 事件。\n")

    def _build_command(
        self,
        agent_input: AgentInput,
        prompt: str,
        *,
        command: str | None = None,
        packet_path: str | None = None,
    ) -> list[str]:
        resolved_command = command or self._resolve_command(agent_input)
        cwd = self._resolve_cwd(agent_input)
        cmd = [
            resolved_command,
            "exec",
            "--json",
            "--ephemeral",
            "-C", cwd,
        ]
        if self.sandbox:
            cmd.extend(["--sandbox", self.sandbox])
        # D4: -o 参数指定 last message 输出文件 (packet)
        if packet_path:
            cmd.extend(["-o", packet_path])
        else:
            # 即使 packet_path 为空也添加占位符避免参数缺失
            cmd.extend(["-o", os.path.join(cwd, "codex_last_message.md")])
        cmd.append("-")
        # D4: Windows cmd /c 包裹
        cmd = self._wrap_command_for_os(cmd)
        return cmd

    def _resolve_command(self, agent_input: AgentInput) -> str:
        return self._resolve_command_value(
            self.command,
            agent_input=agent_input,
            env_key="AGENT_WORKFLOW_CODEX_COMMAND",
            default="codex",
        )

    def _resolve_cwd(self, agent_input: AgentInput) -> str:
        cwd = self.cwd.replace("{project_root}", agent_input.context.project_root)
        return os.path.abspath(cwd)

    def _parse_stream_output(
        self,
        state_name: str,
        stdout: str,
        stderr: str,
        agent_input: AgentInput,
    ) -> TaskResult:
        """解析 Codex JSONL 输出。

        1. 扫描全部 agent_message 事件，从最后一条（含完整 TaskResult）优先解析
        2. 若最后一条未命中，反向遍历所有 agent_message 尝试提取 TaskResult
        3. Fallback: 全局搜索 TaskResult
        4. 最终 fallback: exit_code 摘要
        """
        # 从 skill_policy 提取恢复参数（空值防御）
        skill_policy = getattr(agent_input, 'skill_policy', None) or {}
        allowed_decisions = skill_policy.get("allowed_decisions", []) or []
        enable_synonym_recovery = skill_policy.get("enable_synonym_recovery", False)

        # 第一步：收集全部 agent_message 文本，从最后一条开始尝试提取 TaskResult
        agent_messages: list[str] = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                agent_messages.append(item["text"])

        # 反向遍历：最后一条最可能包含完整 TaskResult
        for text in reversed(agent_messages):
            parsed = _parse_task_result_text(
                text,
                allowed_decisions=allowed_decisions,
                enable_synonym_recovery=enable_synonym_recovery,
            )
            if parsed is not None:
                return parsed

        # 第二步：fallback 到全局搜索（兼容非 agent_message 的输出）
        task_result = _parse_task_result_text(
            stdout,
            allowed_decisions=allowed_decisions,
            enable_synonym_recovery=enable_synonym_recovery,
        )
        if task_result is not None:
            return task_result

        # 第三步：最终 fallback —— 无法解析结构化 TaskResult，
        # 不再伪造 success/done，产出 Runtime 内部瞬时态 invalid_output/None，
        # 交由后续 Repair 闸口消解。
        last_text = agent_messages[-1] if agent_messages else (stdout[:500] if stdout else "")
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent=self.name,
            status="invalid_output",
            decision=None,
            summary=f"无法解析结构化 TaskResult 输出。原始输出摘要: {last_text[:500]}" if last_text else "无法解析结构化 TaskResult 输出（无可用输出）",
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=0,
                attempt=1,
                exit_code=0,
            ),
            issues=[],
        )

    def _parse_output_fallback(
        self,
        state_name: str,
        result: subprocess.CompletedProcess,
        agent_input: AgentInput,
    ) -> TaskResult:
        """从 agent_message 文本中解析 TaskResult（复用通用解析器）。"""
        stdout = result.stdout or ""
        # 从 skill_policy 提取恢复参数（空值防御）
        skill_policy = getattr(agent_input, 'skill_policy', None) or {}
        allowed_decisions = skill_policy.get("allowed_decisions", []) or []
        enable_synonym_recovery = skill_policy.get("enable_synonym_recovery", False)
        parsed = _parse_task_result_text(
            stdout,
            allowed_decisions=allowed_decisions,
            enable_synonym_recovery=enable_synonym_recovery,
        )
        if parsed is not None:
            return parsed

        # returncode==0 不再臆测 done/success：无结构化输出即 invalid_output；
        # returncode!=0 保留 failed，但 decision 一律置 None（靠 status 路由）。
        if result.returncode == 0:
            status = "invalid_output"
        else:
            status = "failed"
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent=self.name,
            status=status,
            decision=None,
            summary=stdout[:500] if stdout else f"无法解析结构化 TaskResult 输出，exit_code={result.returncode}",
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=0,
                attempt=1,
                exit_code=result.returncode,
            ),
            issues=[],
        )

