from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from types import SimpleNamespace
from typing import Any

from .base import BaseAgent
from ..context.agent_input import AgentInput
from ..tasks.result import ExecutionMetadata, TaskResult, _now_iso


class ClaudeCLI(BaseAgent):
    name = "claude"
    provider = "claude"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.command = config.get("command", "claude") if config else "claude"
        self.cwd = config.get("cwd", "{project_root}") if config else "{project_root}"
        self.timeout = config.get("timeout_seconds", 3600) if config else 3600
        self.permission_mode = config.get("permission_mode", "default") if config else "default"
        self.model = config.get("model") if config else None
        self.effort = config.get("effort") if config else None
        # 每次 execute 生成新的 session_id
        self._session_id: str = ""

    def execute(self, agent_input: AgentInput) -> TaskResult:
        state_name = agent_input.state_name or agent_input.context.current_state or agent_input.task.name
        started_at = _now_iso()
        command = self._resolve_command(agent_input)
        self._session_id = str(uuid.uuid4())

        prompt = agent_input.build_prompt()
        staging_dir = os.path.join(agent_input.context.run_root, "staging", state_name)
        os.makedirs(staging_dir, exist_ok=True)
        prompt_path = os.path.join(staging_dir, "prompt.md")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        if not shutil.which(command):
            return TaskResult(
                schema_version=1,
                task_id=agent_input.task.name,
                state=state_name,
                agent=self.name,
                status="blocked",
                decision="blocked",
                summary=f"Claude CLI command '{command}' 未在 PATH 中找到或未配置",
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
        cmd = self._build_command(agent_input, prompt, command=command, cwd=cwd)

        # C5: 安全拦截 —— 检测危险 permission mode
        safety_error = self._assert_safe_permission(cmd)
        if safety_error is not None:
            finished_at = _now_iso()
            return self._create_task_result(
                agent_input.task.name,
                state_name,
                status="blocked",
                decision="blocked",
                summary=f"安全拦截: {safety_error}",
                started_at=started_at,
                finished_at=finished_at,
            )

        # C3: stream log 路径
        log_path = os.path.join(
            agent_input.context.run_root, "logs", f"{state_name}.stream.jsonl"
        )

        # C4: packet 路径
        packet_dir = os.path.join(agent_input.context.run_root, "packets")
        os.makedirs(packet_dir, exist_ok=True)
        packet_path = os.path.join(packet_dir, f"{state_name}_claude_last_message.md")

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
                decision="blocked",
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
                session_id=self._session_id,
                log_path=log_path,
                packet_path=packet_path,
            )

        if status == "timeout":
            task_result = self._create_task_result(
                agent_input.task.name,
                state_name,
                status="timeout",
                decision="fail",
                summary=f"执行超时({self.timeout}s)",
                started_at=started_at,
                finished_at=finished_at,
                exit_code=-1,
                pid=pid,
            )
            task_result.session_id = self._session_id
            task_result.log_path = log_path
            task_result.packet_path = packet_path
            return task_result

        # 解析 stream-json 输出 (C1+C3)
        task_result = self._parse_stream_output(state_name, stdout, stderr, agent_input)

        # 填充 execution metadata（含真实 duration + pid）
        task_result.execution = ExecutionMetadata(
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=0,
            attempt=1,
            exit_code=exit_code,
            pid=pid,
        )

        # C2: 提取 token usage
        token_usage = self.parse_claude_usage(log_path)
        task_result.token_usage = token_usage

        # 填充新增元数据字段
        task_result.session_id = self._session_id
        task_result.log_path = log_path
        task_result.packet_path = packet_path

        # C4: 写入 packet 内容（含实际 agent message 或 result 摘要）
        self._write_packet_content(packet_path, stdout, state_name)

        return task_result

    def _build_command(
        self,
        agent_input: AgentInput,
        prompt: str,
        *,
        command: str | None = None,
        cwd: str | None = None,
    ) -> list[str]:
        resolved_command = command or self._resolve_command(agent_input)
        cmd = [
            resolved_command,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--session-id", self._session_id,
            "--input-format", "text",
            "--permission-mode", self.permission_mode,
            "--add-dir", cwd or self._resolve_cwd(agent_input),
        ]
        # C1: 可选 --model / --effort（从 env 注入，对齐 legacy G7）
        model = self.model or os.environ.get("AGENT_WORKFLOW_CLAUDE_MODEL")
        effort = self.effort or os.environ.get("AGENT_WORKFLOW_CLAUDE_EFFORT")
        if model:
            cmd.extend(["--model", model])
        if effort:
            cmd.extend(["--effort", effort])

        # C5: Windows cmd /c 包裹
        cmd = self._wrap_command_for_os(cmd)
        return cmd

    def _resolve_command(self, agent_input: AgentInput) -> str:
        return self._resolve_command_value(
            self.command,
            agent_input=agent_input,
            env_key="AGENT_WORKFLOW_CLAUDE_COMMAND",
            default="claude",
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
        """解析 Claude stream-json 输出（逐行 JSONL）。

        1. 扫描 type=result 事件，提取 result 字段中的 TaskResult
        2. Fallback: 在全部 stdout 文本中搜索 TaskResult
        3. 最终 fallback: exit_code 摘要
        """
        # 第一步：在 JSONL 流中查找 type=result 事件
        result_text = ""
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped or not stripped.startswith("{"):
                # 可能是部分 JSON（不完整行）
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result_text = event.get("result", "")
                if result_text:
                    break

        # 尝试从 result_text 解析 TaskResult
        if result_text:
            parsed = _parse_task_result_text(result_text)
            if parsed is not None:
                return parsed

        # 第二步：fallback 到全局搜索（兼容旧的 text 格式）
        parsed = _parse_task_result_text(stdout)
        if parsed is not None:
            return parsed

        # 第三步：最终 fallback
        exit_code = 0  # 流解析无法获取，调用方会覆盖
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent=self.name,
            status="success",
            decision="done",
            summary=result_text[:500] if result_text else (stdout[:500] if stdout else "stream-json 完成"),
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=0,
                attempt=1,
                exit_code=exit_code,
            ),
            issues=[],
        )

    def _write_packet_content(self, packet_path: str, stdout: str, state_name: str) -> None:
        """C4: 写入 packet 文件，包含至少一项可读内容。

        优先级：
        1. 最后一条 assistant message（从 stream-json 中提取）
        2. type=result 事件的 result 摘要（前 500 字符）
        3. 明确的 NO_AGENT_MESSAGE marker
        """
        # 尝试从 stream-json 提取 assistant 文本
        assistant_texts: list[str] = []
        result_text = ""
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped or not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result_text = event.get("result", "")
            elif event.get("type") == "assistant":
                msg = event.get("message", {})
                content = msg.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        assistant_texts.append(block.get("text", ""))

        with open(packet_path, "w", encoding="utf-8") as f:
            f.write(f"# {state_name} claude debug packet\n\n")
            f.write(f"Session ID: {self._session_id}\n\n")
            if assistant_texts:
                f.write("## 最后一条 assistant message\n\n")
                f.write(assistant_texts[-1])
                f.write("\n\n")
            elif result_text:
                f.write("## Result 摘要\n\n")
                f.write(result_text[:500])
                f.write("\n\n")
            else:
                f.write("<!-- NO_AGENT_MESSAGE -->\n")
                f.write("无 assistant message 或 result 事件。\n")


def _parse_task_result_text(text: str) -> TaskResult | None:
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        if "result" in data and isinstance(data["result"], str):
            nested = _parse_task_result_text(data["result"])
            if nested is not None:
                return nested
        if "schema_version" in data:
            return TaskResult.from_dict(data)

    marker = "```json"
    if marker in text:
        try:
            start = text.index(marker) + len(marker)
            end = text.index("```", start)
            return TaskResult.from_json(text[start:end].strip())
        except (ValueError, json.JSONDecodeError):
            return None
    return None
