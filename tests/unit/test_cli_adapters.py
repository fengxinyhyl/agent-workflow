from __future__ import annotations

import io
import json
import os

from agent_workflow.agents.claude_cli import ClaudeCLI
from agent_workflow.agents.codex_cli import CodexCLI
from agent_workflow.context import AgentInput, RunContext, TaskConfig


def _task_result_json(agent: str, state: str = "plan") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "task_id": state,
            "state": state,
            "agent": agent,
            "status": "success",
            "decision": "done",
            "summary": f"{agent} completed",
            "execution": {
                "started_at": "2026-06-11T00:00:00+08:00",
                "finished_at": "2026-06-11T00:00:01+08:00",
                "duration_seconds": 1,
                "attempt": 1,
                "exit_code": 0,
                "pid": None,
            },
            "issues": [],
            "session_id": "",
            "token_usage": {},
            "log_path": "",
            "packet_path": "",
        }
    )


def _agent_input(tmp_path, state: str = "plan") -> AgentInput:
    run_root = tmp_path / "run"
    return AgentInput(
        task=TaskConfig(
            name=state,
            instruction="执行当前任务并输出 TaskResult JSON",
            agent="planner",
            output="plan_doc",
        ),
        context=RunContext.create(
            workflow_id="test",
            goal="验证 CLI adapter",
            project_root=str(tmp_path),
            run_id="run_test",
            run_root=str(run_root),
        ),
        state_name=state,
        staging_paths={
            "plan_doc": str(run_root / "staging" / state / "plan_doc.md"),
            "task_result": str(run_root / "staging" / state / "task_result.json"),
        },
    )


class _FakeStdin:
    def __init__(self) -> None:
        self.text = ""
        self.closed = False

    def write(self, text: str) -> int:
        self.text += text
        return len(text)

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout_text: str, returncode: int = 0) -> None:
        self.stdin = _FakeStdin()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("")
        self.returncode = returncode
        self.pid = 1234

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout=None) -> int:
        return self.returncode

    def communicate(self, input=None, timeout=None):
        if input is not None:
            self.stdin.write(input)
            self.stdin.close()
        return self.stdout.getvalue(), self.stderr.getvalue()

    def kill(self) -> None:
        self.returncode = -9


def test_codex_cli_runs_exec_json_and_sends_prompt_to_stdin(monkeypatch, tmp_path):
    captured = {}
    stdout_text = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread-1"}',
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": f"```json\n{_task_result_json('codex')}\n```",
                    },
                }
            ),
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
        ]
    )

    def fake_popen(cmd, **kwargs):
        process = _FakeProcess(stdout_text)
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["stdin"] = process.stdin
        return process

    monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda command: command)
    monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

    result = CodexCLI({"command": "codex", "sandbox": "workspace-write"}).execute(
        _agent_input(tmp_path)
    )

    assert captured["cmd"][:2] == ["codex", "exec"]
    assert "--json" in captured["cmd"]
    assert "--ephemeral" in captured["cmd"]
    assert "-" in captured["cmd"]
    assert "-o" in captured["cmd"] or "--output-last-message" in captured["cmd"]
    assert "执行当前任务" in captured["stdin"].text
    assert captured["stdin"].closed is True
    assert captured["cwd"] == str(tmp_path)
    assert result.status == "success"
    assert result.decision == "done"
    # D1: session_id from thread_id
    assert result.session_id == "thread-1"
    # D2: log_path filled
    assert result.log_path != ""
    # D3: token_usage filled
    assert result.token_usage.get("input_tokens", 0) == 10
    # D4: packet_path filled
    assert result.packet_path != ""


def test_claude_cli_runs_stream_json_and_sends_prompt_to_stdin(monkeypatch, tmp_path):
    captured = {}
    task_result_json_str = _task_result_json("claude")
    # C1: stream-json 格式（多行 JSONL）
    stdout_text = (
        '{"type":"system","subtype":"init"}\n'
        + json.dumps({
            "type": "result",
            "subtype": "success",
            "result": f"```json\n{task_result_json_str}\n```",
            "usage": {"input_tokens": 50, "output_tokens": 30},
        })
        + "\n"
    )

    def fake_popen(cmd, **kwargs):
        process = _FakeProcess(stdout_text)
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["stdin"] = process.stdin
        return process

    monkeypatch.setattr("agent_workflow.agents.claude_cli.shutil.which", lambda command: command)
    monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

    result = ClaudeCLI({"command": "claude", "permission_mode": "default"}).execute(
        _agent_input(tmp_path)
    )

    assert captured["cmd"][:2] == ["claude", "-p"]
    assert "--output-format" in captured["cmd"]
    assert "stream-json" in captured["cmd"]
    assert "--verbose" in captured["cmd"]
    assert "--session-id" in captured["cmd"]
    assert "--input-format" in captured["cmd"]
    assert "--prompt-file" not in captured["cmd"]
    assert "执行当前任务" in captured["stdin"].text
    assert captured["stdin"].closed is True
    assert captured["cwd"] == str(tmp_path)
    assert result.status == "success"
    assert result.decision == "done"
    # C1: 验证 session_id 非空
    assert result.session_id != ""
    # C2: 验证 token_usage 非空（从 stream log 解析）
    assert result.token_usage.get("input_tokens", 0) == 50
    assert result.token_usage.get("output_tokens", 0) == 30
    # C3: 验证 log_path 已填充
    assert result.log_path != ""
    # C4: 验证 packet_path 已填充
    assert result.packet_path != ""


def test_codex_cli_resolves_command_from_project_env_when_placeholder_unset(
    monkeypatch, tmp_path
):
    captured = {}
    (tmp_path / ".env").write_text(
        "AGENT_WORKFLOW_CODEX_COMMAND=codex-from-env.cmd\n",
        encoding="utf-8",
    )

    def fake_popen(cmd, **kwargs):
        process = _FakeProcess(_task_result_json("codex"))
        captured["cmd"] = cmd
        return process

    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_WORKFLOW_CODEX_COMMAND", raising=False)
    monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda command: command)
    monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

    result = CodexCLI({"command": "{CODEX_COMMAND}"}).execute(_agent_input(tmp_path))

    # .cmd 命令被 _wrap_command_for_os 包裹为 ["cmd", "/c", "codex-from-env.cmd", ...]
    assert "codex-from-env.cmd" in captured["cmd"]
    assert result.status == "success"


# ═══════════════════════════════════════════════════════
# Phase B 新增测试: B2 (_wrap_command_for_os) + B3 (_assert_safe_permission)
# ═══════════════════════════════════════════════════════

class TestWrapCommandForOS:
    """B2: _wrap_command_for_os 命令包裹测试。"""

    @staticmethod
    def _wrap(cmd: list[str]) -> list[str]:
        from agent_workflow.agents.base import BaseAgent
        return BaseAgent._wrap_command_for_os(cmd)

    def test_windows_cmd_wrapped(self, monkeypatch):
        """Windows 下 .cmd 命令被包裹。"""
        monkeypatch.setattr("os.name", "nt")
        result = self._wrap(["cc-deepseek.cmd", "-p", "hello"])
        assert result == ["cmd", "/c", "cc-deepseek.cmd", "-p", "hello"]

    def test_windows_bat_wrapped(self, monkeypatch):
        """Windows 下 .bat 命令被包裹。"""
        monkeypatch.setattr("os.name", "nt")
        result = self._wrap(["run.bat", "arg"])
        assert result == ["cmd", "/c", "run.bat", "arg"]

    def test_windows_already_wrapped_no_double(self, monkeypatch):
        """已经是 cmd /c 包裹的不重复包裹。"""
        monkeypatch.setattr("os.name", "nt")
        result = self._wrap(["cmd", "/c", "run.cmd"])
        assert result == ["cmd", "/c", "run.cmd"]

    def test_windows_exe_not_wrapped(self, monkeypatch):
        """.exe 命令不包裹。"""
        monkeypatch.setattr("os.name", "nt")
        result = self._wrap(["python.exe", "script.py"])
        assert result == ["python.exe", "script.py"]

    def test_windows_no_extension_not_wrapped(self, monkeypatch):
        """无扩展名命令（python/claude/codex）不包裹。"""
        monkeypatch.setattr("os.name", "nt")
        result = self._wrap(["claude", "-p", "hello"])
        assert result == ["claude", "-p", "hello"]

    def test_non_windows_no_wrap(self, monkeypatch):
        """非 Windows 平台不包裹。"""
        monkeypatch.setattr("os.name", "posix")
        result = self._wrap(["run.cmd", "arg"])
        assert result == ["run.cmd", "arg"]

    def test_empty_command(self, monkeypatch):
        """空命令列表不报错。"""
        monkeypatch.setattr("os.name", "nt")
        result = self._wrap([])
        assert result == []


class TestAssertSafePermission:
    """B3: _assert_safe_permission 安全拦截测试。"""

    @staticmethod
    def _check(cmd: list[str]) -> str | None:
        from agent_workflow.agents.base import BaseAgent
        return BaseAgent._assert_safe_permission(cmd)

    def test_dangerously_flag_blocked(self):
        """--dangerouslyDisableSandbox flag 被拦截。"""
        result = self._check(["claude", "--dangerouslyDisableSandbox"])
        assert result is not None
        assert "dangerouslyDisableSandbox" in result

    def test_permission_mode_dangerously_blocked(self):
        """--permission-mode dangerously 被拦截。"""
        result = self._check(["claude", "--permission-mode", "dangerously"])
        assert result is not None
        assert "dangerously" in result

    def test_permission_mode_bypass_blocked(self):
        """--permission-mode bypassPermissions 被拦截。"""
        result = self._check(["claude", "--permission-mode", "bypassPermissions"])
        assert result is not None
        assert "bypassPermissions" in result

    def test_normal_command_not_blocked(self):
        """正常命令不拦截。"""
        result = self._check(["claude", "-p", "hello"])
        assert result is None

    def test_dangerously_in_prompt_text_not_blocked(self):
        """prompt 文本中的 'dangerously' 不误杀。"""
        result = self._check(["claude", "-p", "this is a dangerously good strategy"])
        assert result is None

    def test_empty_command_not_blocked(self):
        """空命令不报错。"""
        result = self._check([])
        assert result is None


# ═══════════════════════════════════════════════════════
# Phase C0: streaming foundation gate 测试
# ═══════════════════════════════════════════════════════

class TestRunWithCancelPollStreaming:
    """C0: _run_with_cancel_poll streaming 模式测试。"""

    def test_streaming_writes_log(self, monkeypatch, tmp_path):
        """C0-T1: streaming 模式下日志文件被创建并包含 JSONL 行。"""
        from agent_workflow.agents.base import BaseAgent

        stdout_lines = ["line1\n", "line2\n", '{"type":"result","usage":{"input_tokens":10}}\n']
        stderr_lines = ["warning\n"]

        class _StreamProcess:
            def __init__(self):
                self.stdin = _FakeStdin()
                self.stdout = io.StringIO("".join(stdout_lines))
                self.stderr = io.StringIO("".join(stderr_lines))
                self.returncode = 0
                self.pid = 5678

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def communicate(self, input=None, timeout=None):
                if input is not None:
                    self.stdin.write(input)
                    self.stdin.close()
                return self.stdout.getvalue(), self.stderr.getvalue()

            def kill(self):
                self.returncode = -9

        agent = BaseAgent()
        log_path = str(tmp_path / "logs" / "plan.stream.jsonl")
        agent_input = _agent_input(tmp_path)

        def fake_popen(cmd, **kwargs):
            return _StreamProcess()

        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        _process, status, exit_code, stdout, stderr = agent._run_with_cancel_poll(
            ["claude", "-p"],
            cwd=str(tmp_path),
            timeout=30,
            agent_input=agent_input,
            stdin_text="hello",
            stream_log_path=log_path,
        )

        assert os.path.exists(log_path)
        content = open(log_path, encoding="utf-8").read()
        assert len(content) > 0
        # 每行应为合法 JSONL
        for line in content.strip().split("\n"):
            data = json.loads(line)
            assert "ts" in data
            assert "source" in data
            assert "text" in data

        assert status == "success"
        assert "line1" in stdout
        assert "warning" in stderr

    def test_streaming_no_stream_log_path_backward_compat(self, monkeypatch, tmp_path):
        """C0-T4: stream_log_path 为 None 时行为不变（向后兼容）。"""
        from agent_workflow.agents.base import BaseAgent

        stdout_text = json.dumps({"result": "ok"})

        class _SimpleProcess:
            def __init__(self):
                self.stdin = _FakeStdin()
                self.stdout = io.StringIO(stdout_text)
                self.stderr = io.StringIO("")
                self.returncode = 0
                self.pid = 9999

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def communicate(self, input=None, timeout=None):
                if input is not None:
                    self.stdin.write(input)
                    self.stdin.close()
                return self.stdout.getvalue(), self.stderr.getvalue()

            def kill(self):
                self.returncode = -9

        agent = BaseAgent()
        agent_input = _agent_input(tmp_path)

        def fake_popen(cmd, **kwargs):
            return _SimpleProcess()

        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        _process, status, exit_code, stdout, stderr = agent._run_with_cancel_poll(
            ["claude", "-p"],
            cwd=str(tmp_path),
            timeout=30,
            agent_input=agent_input,
            stdin_text="hello",
        )

        assert status == "success"
        assert stdout == stdout_text
        assert exit_code == 0

    def test_streaming_timeout_preserves_log(self, monkeypatch, tmp_path):
        """C0-T5: streaming 模式下超时仍保留已写入日志。"""
        from agent_workflow.agents.base import BaseAgent

        stdout_lines = ["line1\n", "line2\n"]

        class _HangingProcess:
            def __init__(self):
                self.stdin = _FakeStdin()
                self.stdout = io.StringIO("".join(stdout_lines))
                self.stderr = io.StringIO("")
                self.returncode = None  # 模拟仍在运行
                self.pid = 5555
                self._poll_count = 0

            def poll(self):
                self._poll_count += 1
                # 前 2 次 poll 返回 None（运行中），之后返回 0（正常退出）
                if self._poll_count <= 2:
                    return None
                return 0

            def wait(self, timeout=None):
                return 0

            def communicate(self, input=None, timeout=None):
                if input is not None:
                    self.stdin.write(input)
                    self.stdin.close()
                return self.stdout.getvalue(), self.stderr.getvalue()

            def kill(self):
                self.returncode = -9

        agent = BaseAgent()
        log_path = str(tmp_path / "logs" / "plan.stream.jsonl")
        agent_input = _agent_input(tmp_path)

        def fake_popen(cmd, **kwargs):
            return _HangingProcess()

        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        # 因为 poll 前2次返回 None，超时会触发 terminate
        # 但我们用很短的时间，让 deadline 在第一次 poll 后就超时
        _process, status, exit_code, stdout, stderr = agent._run_with_cancel_poll(
            ["claude", "-p"],
            cwd=str(tmp_path),
            timeout=0,  # 立即超时
            agent_input=agent_input,
            stdin_text="hello",
            stream_log_path=log_path,
        )

        assert status == "timeout"
        assert os.path.exists(log_path)

    def test_parse_claude_usage_from_stream_log(self, tmp_path):
        """C2-T1: 从 stream-json 日志解析 token usage。"""
        from agent_workflow.agents.base import BaseAgent

        log_path = str(tmp_path / "test.stream.jsonl")
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write('{"type":"stream_event","message":"hello"}\n')
            f.write('{"type":"result","usage":{"input_tokens":100,"output_tokens":50,"cache_read_input_tokens":20}}\n')

        usage = BaseAgent.parse_claude_usage(log_path)
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        assert usage["cache_read_input_tokens"] == 20

    def test_parse_claude_usage_empty_file(self, tmp_path):
        """C2-T2: 空文件返回空 dict。"""
        from agent_workflow.agents.base import BaseAgent

        log_path = str(tmp_path / "empty.stream.jsonl")
        os.makedirs(str(tmp_path), exist_ok=True)
        open(log_path, "w", encoding="utf-8").close()

        usage = BaseAgent.parse_claude_usage(log_path)
        assert usage == {}

    def test_parse_claude_usage_no_result_event(self, tmp_path):
        """C2-T3: 无 type=result 事件返回空 dict。"""
        from agent_workflow.agents.base import BaseAgent

        log_path = str(tmp_path / "no_result.stream.jsonl")
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write('{"type":"stream_event","message":"hello"}\n')
            f.write('{"type":"assistant","text":"ok"}\n')

        usage = BaseAgent.parse_claude_usage(log_path)
        assert usage == {}

    def test_parse_claude_usage_missing_file(self):
        """C2-T4: 文件不存在返回空 dict。"""
        from agent_workflow.agents.base import BaseAgent

        usage = BaseAgent.parse_claude_usage("/nonexistent/path.jsonl")
        assert usage == {}


# ═══════════════════════════════════════════════════════
# Phase D1.5: Codex sandbox 安全拦截测试
# ═══════════════════════════════════════════════════════

class TestAssertSafeSandbox:
    """D1.5: _assert_safe_sandbox 测试。"""

    @staticmethod
    def _check(cmd: list[str]) -> str | None:
        from agent_workflow.agents.base import BaseAgent
        return BaseAgent._assert_safe_sandbox(cmd)

    def test_read_only_allowed(self):
        """read-only sandbox 允许通过。"""
        result = self._check(["codex", "exec", "--sandbox", "read-only"])
        assert result is None

    def test_workspace_write_allowed(self):
        """workspace-write sandbox 允许通过。"""
        result = self._check(["codex", "exec", "--sandbox", "workspace-write"])
        assert result is None

    def test_danger_full_access_denied(self):
        """danger-full-access sandbox 被拒绝（codex CLI 实测值）。"""
        result = self._check(["codex", "exec", "--sandbox", "danger-full-access"])
        assert result is not None
        assert "danger-full-access" in result

    def test_none_denied(self):
        """none sandbox 被拒绝。"""
        result = self._check(["codex", "exec", "--sandbox", "none"])
        assert result is not None

    def test_unknown_value_denied(self):
        """未知 sandbox 值保守拒绝。"""
        result = self._check(["codex", "exec", "--sandbox", "unknown-sandbox-mode"])
        assert result is not None
        assert "unknown-sandbox-mode" in result

    def test_no_sandbox_flag_not_blocked(self):
        """无 --sandbox 参数不误杀。"""
        result = self._check(["codex", "exec", "--json"])
        assert result is None


# ═══════════════════════════════════════════════════════
# Phase C: ClaudeCLI 能力补齐测试
# ═══════════════════════════════════════════════════════

class TestClaudeCLI:
    """C1-C5: ClaudeCLI 补齐测试。"""

    def test_build_command_session_id(self, tmp_path):
        """C1-T2: _build_command 含 --session-id 且为有效 uuid。"""
        import uuid as _uuid
        agent_input = _agent_input(tmp_path)
        claude = ClaudeCLI({"command": "claude", "permission_mode": "default"})
        # 手动设置 session_id 避免 execute 的完整流程
        claude._session_id = "test-session-uuid-1234"
        cmd = claude._build_command(agent_input, "hello", command="claude", cwd=str(tmp_path))
        assert "--session-id" in cmd
        sid_idx = cmd.index("--session-id")
        assert sid_idx + 1 < len(cmd)
        assert cmd[sid_idx + 1] == "test-session-uuid-1234"

    def test_build_command_model_effort_from_env(self, monkeypatch, tmp_path):
        """C1-T3: --model / --effort 从 env 注入。"""
        agent_input = _agent_input(tmp_path)
        monkeypatch.setenv("AGENT_WORKFLOW_CLAUDE_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("AGENT_WORKFLOW_CLAUDE_EFFORT", "high")
        claude = ClaudeCLI({"command": "claude", "permission_mode": "default"})
        claude._session_id = "s1"
        cmd = claude._build_command(agent_input, "hello", command="claude", cwd=str(tmp_path))
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd
        assert "--effort" in cmd
        assert "high" in cmd

    def test_dangerously_flag_blocked_at_execute(self, monkeypatch, tmp_path):
        """C5-T1: --dangerouslyDisableSandbox 被 execute() 拦截为 blocked。"""
        agent_input = _agent_input(tmp_path)
        monkeypatch.setattr("agent_workflow.agents.claude_cli.shutil.which", lambda c: c)
        # 构造含危险 flag 的命令（跳过 _build_command 通过 monkeypatch _assert_safe_permission 间接验证）
        # 更直接的测试: monkeypatch _build_command 返回危险命令
        claude = ClaudeCLI({"command": "claude", "permission_mode": "dangerously"})
        claude._session_id = "s1"

        def fake_build(*args, **kwargs):
            return ["claude", "-p", "--dangerouslyDisableSandbox"]

        monkeypatch.setattr(claude, "_build_command", fake_build)

        result = claude.execute(agent_input)
        assert result.status == "blocked"
        assert "dangerouslyDisableSandbox" in result.summary

    def test_permission_mode_bypass_blocked_at_execute(self, monkeypatch, tmp_path):
        """C5-T2: --permission-mode bypassPermissions 被 execute() 拦截。"""
        agent_input = _agent_input(tmp_path)
        monkeypatch.setattr("agent_workflow.agents.claude_cli.shutil.which", lambda c: c)
        claude = ClaudeCLI({"command": "claude", "permission_mode": "default"})
        claude._session_id = "s1"

        def fake_build(*args, **kwargs):
            return ["claude", "-p", "--permission-mode", "bypassPermissions"]

        monkeypatch.setattr(claude, "_build_command", fake_build)

        result = claude.execute(agent_input)
        assert result.status == "blocked"
        assert "bypassPermissions" in result.summary

    def test_stream_json_parsing(self, monkeypatch, tmp_path):
        """C3-T1: stream-json 多行事件被正确解析为 TaskResult。"""
        task_json = _task_result_json("claude", "plan")
        stdout_text = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"thinking..."}]}}\n'
            + json.dumps({
                "type": "result",
                "subtype": "success",
                "result": task_json,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            })
            + "\n"
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.claude_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = ClaudeCLI({"command": "claude", "permission_mode": "default"}).execute(
            _agent_input(tmp_path)
        )

        assert result.status == "success"
        assert result.decision == "done"
        assert result.agent == "claude"
        assert result.session_id != ""
        assert result.log_path != ""
        assert result.packet_path != ""

    def test_packet_path_in_taskresult_and_content(self, monkeypatch, tmp_path):
        """C4-T1/T2: packet 路径在 TaskResult 中，且文件包含实质内容。"""
        task_json = _task_result_json("claude")
        stdout_text = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Here is the plan."}]}}\n'
            + json.dumps({
                "type": "result",
                "subtype": "success",
                "result": task_json,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })
            + "\n"
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.claude_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = ClaudeCLI({"command": "claude", "permission_mode": "default"}).execute(
            _agent_input(tmp_path)
        )

        assert result.packet_path != ""
        assert os.path.exists(result.packet_path)
        packet_content = open(result.packet_path, encoding="utf-8").read()
        assert "claude debug packet" in packet_content
        assert "Here is the plan." in packet_content

    def test_cancelled_status_fills_metadata(self, monkeypatch, tmp_path):
        """C5-T3: cancelled 状态正确填充 session_id/log_path/packet_path。"""
        agent_input = _agent_input(tmp_path)

        class _CancelledProcess:
            def __init__(self):
                self.stdin = _FakeStdin()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.returncode = None
                self.pid = 8888

            def poll(self):
                return None

            def communicate(self, input=None, timeout=None):
                if input is not None:
                    self.stdin.write(input)
                    self.stdin.close()
                return "", ""

            def kill(self):
                self.returncode = -9

        # 创建取消文件
        cancel_path = os.path.join(agent_input.context.run_root, "cancelled")
        os.makedirs(agent_input.context.run_root, exist_ok=True)
        open(cancel_path, "w").close()

        monkeypatch.setattr("agent_workflow.agents.claude_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", lambda cmd, **kwargs: _CancelledProcess())

        result = ClaudeCLI({"command": "claude", "permission_mode": "default"}).execute(agent_input)

        assert result.status == "cancelled"
        assert result.session_id != ""
        assert result.log_path != ""
        assert result.packet_path != ""


# ═══════════════════════════════════════════════════════
# Phase D: CodexCLI 能力补齐测试
# ═══════════════════════════════════════════════════════

class TestCodexCLI:
    """D1-D4: CodexCLI 补齐测试。"""

    def test_thread_id_extracted_as_session_id(self, monkeypatch, tmp_path):
        """D1-T1: thread.started 事件的 thread_id 写入 session_id。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            '{"type":"thread.started","thread_id":"thread-abc-123"}\n'
            + json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"```json\n{task_json}\n```",
                },
            })
            + "\n"
            + '{"type":"turn.completed","usage":{"input_tokens":50,"output_tokens":20,"cached_input_tokens":10,"reasoning_output_tokens":5}}\n'
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert result.session_id == "thread-abc-123"
        assert result.status == "success"

    def test_session_id_empty_when_no_thread_event(self, monkeypatch, tmp_path):
        """D1-T2: 无 thread.started 事件时 session_id 为空。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"```json\n{task_json}\n```",
                },
            })
            + "\n"
            + '{"type":"turn.completed"}\n'
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert result.session_id == ""

    def test_token_usage_from_turn_completed(self, monkeypatch, tmp_path):
        """D3-T1: turn.completed 事件的 usage 写入 token_usage。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            + json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"```json\n{task_json}\n```",
                },
            })
            + "\n"
            + '{"type":"turn.completed","usage":{"input_tokens":200,"output_tokens":80,"cached_input_tokens":30,"reasoning_output_tokens":15}}\n'
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert result.token_usage.get("input_tokens") == 200
        assert result.token_usage.get("output_tokens") == 80
        assert result.token_usage.get("cached_input_tokens") == 30
        assert result.token_usage.get("reasoning_output_tokens") == 15

    def test_token_usage_empty_when_no_turn_completed(self, monkeypatch, tmp_path):
        """D3-T2: 无 turn.completed 时 token_usage 为空。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            + json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"```json\n{task_json}\n```",
                },
            })
            + "\n"
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert result.token_usage == {}

    def test_stream_log_written(self, monkeypatch, tmp_path):
        """D2-T1: streaming 日志文件被创建且包含 JSONL。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            + json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"```json\n{task_json}\n```",
                },
            })
            + "\n"
            + '{"type":"turn.completed"}\n'
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert result.log_path != ""
        assert os.path.exists(result.log_path)

    def test_log_path_in_taskresult(self, monkeypatch, tmp_path):
        """D2-T2: TaskResult.log_path 正确填充。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"```json\n{task_json}\n```",
                },
            })
            + "\n"
            + '{"type":"turn.completed"}\n'
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert ".codex.jsonl" in result.log_path or result.log_path.endswith(".jsonl")
        assert "logs" in result.log_path

    def test_packet_path_in_taskresult_and_content(self, monkeypatch, tmp_path):
        """D4-T1/T2: packet 路径和内容验证。"""
        task_json = _task_result_json("codex")
        stdout_text = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            + json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": f"Here is the codex output.\n```json\n{task_json}\n```",
                },
            })
            + "\n"
            + '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
        )

        def fake_popen(cmd, **kwargs):
            return _FakeProcess(stdout_text)

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", fake_popen)

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(
            _agent_input(tmp_path)
        )

        assert result.packet_path != ""
        assert os.path.exists(result.packet_path)
        packet_content = open(result.packet_path, encoding="utf-8").read()
        assert "codex debug packet" in packet_content
        assert "Here is the codex output." in packet_content

    def test_dangerously_sandbox_blocked(self, monkeypatch, tmp_path):
        """D1.5/D4-T4: 危险 sandbox 在 execute() 中被拦截。"""
        agent_input = _agent_input(tmp_path)
        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        codex = CodexCLI({"command": "codex", "sandbox": "read-only"})

        def fake_build(*args, **kwargs):
            return ["codex", "exec", "--sandbox", "danger-full-access"]

        monkeypatch.setattr(codex, "_build_command", fake_build)

        result = codex.execute(agent_input)
        assert result.status == "blocked"
        assert "danger-full-access" in result.summary

    def test_cancelled_status_fills_metadata(self, monkeypatch, tmp_path):
        """D4-T5: cancelled 状态正确填充 metadata。"""
        agent_input = _agent_input(tmp_path)

        class _CancelledProcess:
            def __init__(self):
                self.stdin = _FakeStdin()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.returncode = None
                self.pid = 7777

            def poll(self):
                return None

            def communicate(self, input=None, timeout=None):
                if input is not None:
                    self.stdin.write(input)
                    self.stdin.close()
                return "", ""

            def kill(self):
                self.returncode = -9

        cancel_path = os.path.join(agent_input.context.run_root, "cancelled")
        os.makedirs(agent_input.context.run_root, exist_ok=True)
        open(cancel_path, "w").close()

        monkeypatch.setattr("agent_workflow.agents.codex_cli.shutil.which", lambda c: c)
        monkeypatch.setattr("agent_workflow.agents.base.subprocess.Popen", lambda cmd, **kwargs: _CancelledProcess())

        result = CodexCLI({"command": "codex", "sandbox": "read-only"}).execute(agent_input)

        assert result.status == "cancelled"
        assert result.log_path != ""
        assert result.packet_path != ""
