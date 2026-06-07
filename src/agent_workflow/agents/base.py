"""BaseAgent — Agent 适配器基类。

所有 Agent 适配器继承此类，实现 execute() 方法。
Agent 适配器只接收 AgentInput，不接收散落参数。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any

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
    ) -> tuple[subprocess.Popen | None, str, int, str, str]:
        """执行 subprocess.Popen，执行期间每 1 秒检查取消文件。

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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except Exception as e:
            return None, "failed", 1, "", str(e)

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

    def _create_task_result(
        self,
        task_id: str,
        state: str,
        status: str = "success",
        decision: str = "done",
        summary: str = "",
    ) -> TaskResult:
        """创建 TaskResult 的工具方法。"""
        from ..tasks.result import TaskResult, ExecutionMetadata, Issue, _now_iso

        return TaskResult(
            schema_version=1,
            task_id=task_id,
            state=state,
            agent=self.name,
            status=status,
            decision=decision,
            summary=summary,
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=0,
                attempt=1,
                exit_code=0 if status == "success" else 1,
            ),
            issues=[],
        )
