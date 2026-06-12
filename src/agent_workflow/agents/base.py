"""BaseAgent — Agent 适配器基类。

所有 Agent 适配器继承此类，实现 execute() 方法。
Agent 适配器只接收 AgentInput，不接收散落参数。
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Callable

from ..context.agent_input import AgentInput
from ..tasks.result import TaskResult


class BaseAgent:
    """Agent 适配器基类。

    子类需实现:
    - execute(agent_input) → TaskResult
    - smoke_test() → bool

    可重写:
    - build_command(agent_input) → str: 构建 CLI 命令
    """

    name: str = "base"
    provider: str = "base"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def execute(self, agent_input: AgentInput) -> TaskResult:
        """执行 Agent 任务。

        参数:
          agent_input: 统一的 AgentInput（Task + RunContext + Skill）

        返回:
          TaskResult（包含 decision、artifacts、execution metadata）
        """
        raise NotImplementedError

    def smoke_test(self) -> bool:
        """冒烟测试：验证 Agent 是否可用。"""
        try:
            # 创建一个最小化的 AgentInput 进行测试
            from ..context.agent_input import AgentInput, TaskConfig
            test_input = AgentInput(
                task=TaskConfig(
                    name="smoke_test",
                    instruction="回复 'ok' 并确认 Agent 可用。",
                    role="test",
                ),
            )
            result = self.execute(test_input)
            return result is not None and result.status in ("success", "done")
        except Exception:
            return False

    def build_command(self, agent_input: AgentInput) -> str:
        """构建 CLI 命令（供子类重写）。"""
        return ""

    def _run_with_cancel_poll(
        self,
        cmd: list[str],
        *,
        cwd: str,
        timeout: int,
        agent_input: AgentInput,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        stream_log_path: str | None = None,
        on_stream_line: Callable[[str], None] | None = None,
    ) -> tuple[subprocess.Popen | None, str, int, str, str]:
        """执行 subprocess.Popen，执行期间每 1 秒检查取消文件。

        streaming 模式（stream_log_path 非空）：
        - 启动两个 daemon thread 分别逐行读取 stdout/stderr
        - 每行写入 stream_log_path（JSONL 格式）并可选回调 on_stream_line
        - 完成后返回收集的完整 stdout/stderr 文本

        非 streaming 模式（stream_log_path 为 None）：
        - 行为与之前完全一致，使用 communicate() 一次性收集

        Returns:
            (process, status, exit_code, stdout, stderr)
            status: "success" | "timeout" | "cancelled" | "failed"
        """
        run_root = agent_input.context.run_root
        cancel_path = os.path.join(run_root, "cancelled")

        process = None
        try:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.PIPE if stdin_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            if stdin_text is not None and process.stdin is not None:
                process.stdin.write(stdin_text)
                process.stdin.close()
                process.stdin = None
        except Exception as e:
            return None, "failed", 1, "", str(e)

        # ── streaming 模式 ──
        if stream_log_path is not None:
            return self._run_streaming(
                process, cmd, cwd, timeout, cancel_path, stream_log_path, on_stream_line
            )

        # ── 非 streaming 模式（原有行为）──
        # 轮询等待完成或取消
        deadline = time.time() + timeout
        cancelled = False

        while process.poll() is None:
            if time.time() > deadline:
                # 超时 → terminate
                self._terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    stdout, stderr = "", ""
                return process, "timeout", -1, stdout or "", stderr or ""

            # 检查取消文件
            if os.path.exists(cancel_path):
                cancelled = True
                self._terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    stdout, stderr = "", ""
                return process, "cancelled", -1, stdout or "", stderr or ""

            time.sleep(1)

        # 正常完成
        stdout, stderr = process.communicate()
        exit_code = process.returncode or 0
        status = "success" if exit_code == 0 else "failed"
        return process, status, exit_code, stdout or "", stderr or ""

    def _run_streaming(
        self,
        process: subprocess.Popen,
        cmd: list[str],
        cwd: str,
        timeout: int,
        cancel_path: str,
        stream_log_path: str,
        on_stream_line: Callable[[str], None] | None = None,
    ) -> tuple[subprocess.Popen | None, str, int, str, str]:
        """streaming 模式：双 daemon thread 逐行读取 stdout/stderr，落盘 JSONL。

        对齐 legacy claude_cli.py / codex_cli.py 的 reader() 模式。
        """
        import json as _json
        from datetime import datetime, timezone, timedelta

        tz = timezone(timedelta(hours=8))
        os.makedirs(os.path.dirname(stream_log_path) or ".", exist_ok=True)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        lock = threading.Lock()
        threads: list[threading.Thread] = []

        def _write_jsonl(source: str, text: str) -> None:
            ts = datetime.now(tz).isoformat()
            entry = {"ts": ts, "source": source, "text": text.rstrip("\n")}
            with lock:
                with open(stream_log_path, "a", encoding="utf-8") as fh:
                    fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")

        def _drain_stdout() -> None:
            if process.stdout is None:
                return
            try:
                for line in process.stdout:
                    stdout_lines.append(line)
                    _write_jsonl("stdout", line)
                    if on_stream_line is not None:
                        try:
                            on_stream_line(line)
                        except Exception:
                            pass
            except (ValueError, OSError):
                pass

        def _drain_stderr() -> None:
            if process.stderr is None:
                return
            try:
                for line in process.stderr:
                    stderr_lines.append(line)
                    _write_jsonl("stderr", line)
                    if on_stream_line is not None:
                        try:
                            on_stream_line(line)
                        except Exception:
                            pass
            except (ValueError, OSError):
                pass

        t_stdout = threading.Thread(target=_drain_stdout, daemon=True)
        t_stderr = threading.Thread(target=_drain_stderr, daemon=True)
        threads = [t_stdout, t_stderr]
        t_stdout.start()
        t_stderr.start()

        # 轮询等待完成或取消
        deadline = time.time() + timeout

        while process.poll() is None:
            if time.time() > deadline:
                self._terminate_process(process)
                break
            if os.path.exists(cancel_path):
                self._terminate_process(process)
                break
            time.sleep(1)

        # 等待 drain threads 收集剩余输出
        for t in threads:
            t.join(timeout=2)

        exit_code = process.returncode or 0

        # 确定状态
        if os.path.exists(cancel_path):
            status = "cancelled"
            exit_code = -1
        elif time.time() > deadline:
            status = "timeout"
            exit_code = -1
        else:
            status = "success" if exit_code == 0 else "failed"

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)
        return process, status, exit_code, stdout, stderr

    def _resolve_command_value(
        self,
        value: str,
        *,
        agent_input: AgentInput,
        env_key: str,
        default: str,
    ) -> str:
        """解析命令配置，支持项目 .env 和 {VAR} 占位符。"""
        project_env = self._load_project_env(agent_input.context.project_root)

        def _get_env(name: str) -> str:
            return os.environ.get(name) or project_env.get(name, "")

        raw = value or ""
        if not raw:
            return _get_env(env_key) or default

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            resolved = _get_env(name)
            if not resolved and name in {"CODEX_COMMAND", "CLAUDE_COMMAND"}:
                resolved = _get_env(f"AGENT_WORKFLOW_{name}")
            return resolved or match.group(0)

        resolved = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", _replace, raw)
        if resolved == raw and raw.startswith("{") and raw.endswith("}"):
            resolved = ""
        if "{" in resolved and "}" in resolved:
            return _get_env(env_key) or default
        return resolved or _get_env(env_key) or default

    @staticmethod
    def _load_project_env(project_root: str) -> dict[str, str]:
        env_path = os.path.join(project_root or ".", ".env")
        values: dict[str, str] = {}
        try:
            with open(env_path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        values[key] = value
        except OSError:
            pass
        return values

    @staticmethod
    def _terminate_process(process: subprocess.Popen):
        """终止子进程（Windows 兼容处理）。"""
        if process is None:
            return
        try:
            # Windows: 先 CTRL_BREAK_EVENT 再 kill
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    pass
                # 等待 2 秒让子进程清理
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            else:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    @staticmethod
    def _write_stream_log(stdout: str, log_path: str) -> None:
        """将 stdout 写入 stream 日志文件（最终落盘，非实时）。

        每行 stdout 作为一条 JSONL entry 写入，格式:
        {"ts": "<ISO8601>", "source": "stdout", "text": "<line>"}

        由 adapter 在 communicate() 完成后调用。
        实时逐行落盘 + emit 留给 Phase C/D 按需实现。
        """
        import json as _json
        from datetime import datetime, timezone, timedelta

        if not log_path or not stdout:
            return
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        tz = timezone(timedelta(hours=8))
        ts = datetime.now(tz).isoformat()
        with open(log_path, "w", encoding="utf-8") as f:
            for line in stdout.splitlines():
                entry = {"ts": ts, "source": "stdout", "text": line}
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _wrap_command_for_os(cmd: list[str]) -> list[str]:
        """在 Windows 下对 .cmd/.bat 命令包裹 cmd /c。

        纯 static helper，由 adapter（ClaudeCLI/CodexCLI）在 _build_command 中按需调用。
        不自动注入 _run_with_cancel_poll。

        判断扩展名时优先解析实际文件：无扩展名的命令（如 codex/claude）经
        PATHEXT 解析后真实文件可能是 .cmd/.bat，此类同样需要 cmd /c 包裹，
        否则 Popen 直接执行会失败。
        """
        if os.name != "nt":
            return cmd
        if not cmd:
            return cmd
        exe = cmd[0]
        # 已经是 cmd /c 包裹的不重复包裹
        if exe.lower() in ("cmd", "cmd.exe"):
            return cmd
        # 优先用传入字符串自身的扩展名；无扩展名时用 shutil.which 解析真实路径
        _, ext = os.path.splitext(exe)
        if not ext:
            resolved = shutil.which(exe)
            if resolved:
                _, ext = os.path.splitext(resolved)
        # .cmd / .bat 需要 shell shim
        if ext.lower() in (".cmd", ".bat"):
            return ["cmd", "/c"] + cmd
        return cmd

    @staticmethod
    def _assert_safe_permission(cmd: list[str]) -> str | None:
        """检测危险 permission mode。返回错误消息字符串或 None。

        检测规则（限定 CLI option/value 语义）：
        1. 命令参数中存在独立的 --permission-mode 后紧跟含 "dangerously" 或 "bypass" 的值
        2. 命令参数中存在独立的 --dangerouslyDisableSandbox flag
        3. 不作为命令的普通文本参数（如 prompt 内容）进行检测

        纯 static helper，由 adapter 在 build_command 后、Popen 前按需调用。

        返回: None 表示安全；非空字符串表示拦截原因。
        """
        # 先检查独立 flag（Claude + Codex 危险 flag）
        DANGEROUS_FLAGS = {
            "dangerouslydisablesandbox",
            "dangerously-bypass-approvals-and-sandbox",
        }
        for i, arg in enumerate(cmd):
            arg_lower = arg.lower().lstrip("-")
            if arg_lower in DANGEROUS_FLAGS:
                return f"禁止使用危险 permission mode: {arg}"
        # 再检查 --permission-mode <value> 模式
        for i, arg in enumerate(cmd):
            if arg == "--permission-mode" and i + 1 < len(cmd):
                val = cmd[i + 1].lower()
                if "dangerously" in val or "bypass" in val:
                    return f"禁止使用危险 permission mode: --permission-mode {cmd[i + 1]}"
        return None

    @staticmethod
    def parse_claude_usage(log_path: str) -> dict[str, int]:
        """从 Claude stream-json 日志提取 token usage。

        查找 type=result 事件中的 usage 字段。
        支持两种格式：
        1. 直接 JSONL（原始 stream-json）：每行即事件，type=result
        2. 包装 JSONL（streaming log）：每行为 {"ts":...,"source":"stdout","text":"<escaped event>"}

        移植自 legacy strategy/research/agent_workflow/adapters/claude_cli.py:17-47。
        """
        import json as _json

        if not log_path or not os.path.exists(log_path):
            return {}
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or not stripped.startswith("{"):
                        continue
                    try:
                        event = _json.loads(stripped)
                    except (ValueError, _json.JSONDecodeError):
                        continue

                    # 格式 1: 直接 JSONL（原始 stream-json）
                    if event.get("type") == "result":
                        usage = event.get("usage", {})
                        if usage:
                            return {
                                "input_tokens": usage.get("input_tokens", 0),
                                "output_tokens": usage.get("output_tokens", 0),
                                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                            }

                    # 格式 2: 包装 JSONL（streaming log）—— text 内嵌原始事件
                    if "source" in event and "text" in event:
                        try:
                            inner = _json.loads(event["text"])
                        except (ValueError, _json.JSONDecodeError):
                            continue
                        if inner.get("type") == "result":
                            usage = inner.get("usage", {})
                            if usage:
                                return {
                                    "input_tokens": usage.get("input_tokens", 0),
                                    "output_tokens": usage.get("output_tokens", 0),
                                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                                }
        except Exception:
            pass
        return {}

    @staticmethod
    def parse_codex_stream_summary(stdout: str) -> dict[str, Any]:
        """从 Codex JSONL stdout 提取结构化摘要。

        返回 dict 包含: thread_id, status, errors, token_usage。
        移植自 legacy strategy/research/agent_workflow/adapters/codex_cli.py:68-139。
        """
        import json as _json

        result: dict[str, Any] = {
            "thread_id": None,
            "status": "unknown",
            "errors": [],
            "token_usage": {},
        }

        if not stdout:
            result["status"] = "error"
            result["errors"].append("stdout 为空")
            return result

        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = _json.loads(stripped)
            except (ValueError, _json.JSONDecodeError):
                continue

            event_type = event.get("type", "")

            if event_type == "thread.started":
                result["thread_id"] = event.get("thread_id")

            elif event_type == "turn.completed":
                result["status"] = "completed"
                usage = event.get("usage", {})
                if usage:
                    result["token_usage"] = {
                        "input_tokens": usage.get("input_tokens", 0),
                        "cached_input_tokens": usage.get("cached_input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "reasoning_output_tokens": usage.get("reasoning_output_tokens", 0),
                    }

            elif event_type == "turn.failed":
                result["status"] = "failed"
                error_msg = event.get("error", event.get("message", "turn.failed"))
                result["errors"].append(str(error_msg))

            elif event_type == "error":
                result["errors"].append(str(event.get("message", event.get("error", "unknown error"))))

        if result["status"] == "unknown":
            if result["errors"]:
                result["status"] = "error"
            else:
                result["status"] = "incomplete"

        return result

    @staticmethod
    def _assert_safe_sandbox(cmd: list[str]) -> str | None:
        """检测 Codex --sandbox 参数是否安全。返回错误消息字符串或 None。

        Codex sandbox 白名单（允许，基于 codex exec --help 实测）:
          - read-only, workspace-write

        白名单外的任何值（包括 danger-full-access）一律拒绝。
        --sandbox 未出现时返回 None（不误杀）。

        纯 static helper，由 adapter 在 build_command 后、Popen 前按需调用。
        """
        SANDBOX_ALLOWLIST = {"read-only", "workspace-write"}

        for i, arg in enumerate(cmd):
            if arg == "--sandbox" and i + 1 < len(cmd):
                val = cmd[i + 1]
                if val in SANDBOX_ALLOWLIST:
                    return None
                return f"Codex sandbox 值 '{cmd[i + 1]}' 不在白名单中，拒绝"
        return None

    def _create_task_result(
        self,
        task_id: str,
        state: str,
        status: str = "success",
        decision: str = "done",
        summary: str = "",
        started_at: str = "",
        finished_at: str = "",
        exit_code: int = 0,
        pid: int | None = None,
    ) -> TaskResult:
        """创建 TaskResult 的工具方法。

        新增参数（全部带默认值，向后兼容现有调用者）:
          started_at: 任务开始时间（ISO 8601），为空时取当前时间
          finished_at: 任务完成时间（ISO 8601），为空时取当前时间
          exit_code: 进程退出码（覆盖自动推断）
          pid: 子进程 PID
        """
        from ..tasks.result import TaskResult as TR, ExecutionMetadata, Issue, _now_iso

        # 计算真实 duration
        duration = 0.0
        if started_at and finished_at:
            try:
                from datetime import datetime
                start_dt = datetime.fromisoformat(started_at)
                finish_dt = datetime.fromisoformat(finished_at)
                duration = (finish_dt - start_dt).total_seconds()
            except Exception:
                pass

        effective_exit_code = exit_code
        if exit_code == 0 and status not in ("success",):
            effective_exit_code = 1

        return TR(
            schema_version=1,
            task_id=task_id,
            state=state,
            agent=self.name,
            status=status,
            decision=decision,
            summary=summary,
            execution=ExecutionMetadata(
                started_at=started_at or _now_iso(),
                finished_at=finished_at or _now_iso(),
                duration_seconds=max(0.0, duration),
                attempt=1,
                exit_code=effective_exit_code,
                pid=pid,
            ),
            issues=[],
            # 新字段默认空值（Phase C/D 负责填充）
            session_id="",
            token_usage={},
            log_path="",
            packet_path="",
        )
