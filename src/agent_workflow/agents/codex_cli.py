from __future__ import annotations

import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from typing import Any

from .base import BaseAgent
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
        staging_dir = os.path.join(agent_input.context.run_root, "staging", state_name)
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
                decision="blocked",
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
                decision="blocked",
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
                decision="blocked",
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
                session_id="",
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
            task_result.log_path = log_path
            task_result.packet_path = packet_path
            return task_result

        # D1/D3: 解析 stream summary（thread_id + token_usage）
        stream_summary = self.parse_codex_stream_summary(stdout)

        # 解析 TaskResult（从 stdout 中的 agent_message）
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

        1. 扫描 agent_message 事件，从中提取 TaskResult
        2. Fallback: 全局搜索 TaskResult
        3. 最终 fallback: exit_code 摘要
        """
        # 第一步：在 JSONL 流中查找 agent_message（对齐 legacy parse_codex_stream）
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                nested = SimpleNamespace(
                    returncode=0,
                    stdout=item["text"],
                    stderr=stderr,
                )
                return self._parse_output_fallback(state_name, nested, agent_input)

        # 第二步：fallback 到全局搜索
        task_result = _parse_task_result_text(stdout)
        if task_result is not None:
            return task_result

        # 第三步：最终 fallback
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent=self.name,
            status="success",
            decision="done",
            summary=stdout[:500] if stdout else "codex 完成",
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
        parsed = _parse_task_result_text(stdout)
        if parsed is not None:
            return parsed

        status = "success" if result.returncode == 0 else "failed"
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent=self.name,
            status=status,
            decision="done" if result.returncode == 0 else "fail",
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
            # JSON 块解析失败（常见原因：模型用 [...] 截断长数组）
            # 回退到正则提取关键字段，构造最小可用 TaskResult
            return _extract_task_result_fallback(text, start, end)

    return None


def _extract_task_result_fallback(
    text: str, json_start: int, json_end: int
) -> TaskResult | None:
    """从截断/损坏的 JSON 块中用正则提取 decision/status/summary。

    当模型在 ```json``` 块中使用 [...] 等占位符截断长数组时，
    json.loads 会失败。此函数回退到逐字段正则提取。
    """
    import re

    json_text = text[json_start:json_end].strip()

    def _extract_str(key: str, default: str = "") -> str:
        m = re.search(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', json_text)
        if m:
            return m.group(1)
        return default

    decision = _extract_str("decision", "done")
    status = _extract_str("status", "success")
    summary = _extract_str("summary", "")
    task_id = _extract_str("task_id", "")
    state = _extract_str("state", "")

    # 即使只提取到 decision，也值得返回
    if decision:
        return TaskResult(
            schema_version=1,
            task_id=task_id,
            state=state,
            status=status,
            decision=decision,
            summary=summary,
        )

    return None
