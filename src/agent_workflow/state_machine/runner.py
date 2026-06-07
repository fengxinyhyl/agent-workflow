"""Runner — 工作流主循环。

Runner 负责:
- 创建工作流运行上下文
- 按状态机循环执行
- Guard 检查
- Agent 调度
- TaskResult 校验
- Artifact 管理
- Transition 选择
- 状态持久化

核心原则（v4）:
- 所有语义决策来自 TaskResult
- 所有状态迁移由 Runner 决定
- Agent 只写 staging
- 未知 decision 走 default
"""

from __future__ import annotations

import os
import time
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from ..config.models import WorkflowConfig, TaskModel, AgentModel, RoleModel
from ..context.run_context import RunContext
from ..context.agent_input import AgentInput, TaskConfig as AgentTaskConfig
from .machine import StateMachine, TransitionResult
from .guard import GuardChecker, GuardResult


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


def _generate_run_id() -> str:
    """生成唯一的 run_id。"""
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"run_{ts}_{short}"


class Runner:
    """工作流运行器。

    用法:
        wf = load_workflow("workflow.yaml")
        runner = Runner(wf, goal="实现登录功能", project_root=".")
        run_id = runner.start()
        runner.run()
    """

    def __init__(
        self,
        workflow: WorkflowConfig,
        goal: str = "",
        project_root: str = ".",
        run_root: str | None = None,
        agents: dict[str, AgentModel] | None = None,
        event_bus: Any = None,
    ):
        self.workflow = workflow
        self.goal = goal
        self.project_root = os.path.abspath(project_root)

        # 状态机
        self.sm = StateMachine(workflow)

        # Guard
        self.guard_checker = GuardChecker(workflow.guards)

        # RunContext（在 start() 中创建）
        self.context: RunContext | None = None

        # 运行根目录
        if run_root is None:
            run_root = os.path.join(
                self.project_root, ".agent-workflow", "runs"
            )
        self.base_run_root = run_root

        # Agent registry
        self._agent_registry = agents or {}

        # EventBus（延迟导入避免循环依赖）
        self._event_bus = event_bus

        # 心跳控制
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._running = False
        self._cancelled = False
        self._run_id: str = ""

    @property
    def run_id(self) -> str:
        return self._run_id

    def _get_event_bus(self):
        """延迟获取 EventBus。"""
        if self._event_bus is None:
            try:
                from ..observability.event_bus import EventBus
                self._event_bus = EventBus()
            except ImportError:
                self._event_bus = _NullEventBus()
        return self._event_bus

    def start(self) -> str:
        """初始化运行上下文并返回 run_id。"""
        self._run_id = _generate_run_id()
        run_root = os.path.join(self.base_run_root, self._run_id)

        self.context = RunContext.create(
            workflow_id=self.workflow.name,
            goal=self.goal,
            project_root=self.project_root,
            run_id=self._run_id,
            run_root=run_root,
        )

        # 创建目录结构
        os.makedirs(os.path.join(run_root, "staging"), exist_ok=True)
        os.makedirs(os.path.join(run_root, "artifacts"), exist_ok=True)
        os.makedirs(os.path.join(run_root, "logs"), exist_ok=True)

        # 初始化 current_state
        self.context.current_state = self.sm.initial_state

        # Guard 设置启动时间
        self.guard_checker.set_start_time(
            datetime.fromisoformat(self.context.started_at)
        )

        # 持久化初始状态
        self.context.save()

        # 发射 WorkflowStarted 事件
        self._get_event_bus().emit("WorkflowStarted", {
            "run_id": self._run_id,
            "workflow_id": self.workflow.name,
            "goal": self.goal,
            "initial_state": self.sm.initial_state,
            "timestamp": _now_iso(),
        })

        return self._run_id

    def run(self) -> str:
        """运行主循环直到终止状态。"""
        if self.context is None:
            raise RuntimeError("请先调用 start() 初始化运行")

        self._running = True
        self._start_heartbeat()

        try:
            current_state = self.context.current_state

            while not self.sm.is_terminal(current_state) and not self._cancelled:
                # 1. Guard 检查
                guard_result = self.guard_checker.check(current_state, self.context)
                if not guard_result.passed:
                    self._get_event_bus().emit("GuardFailed", guard_result.__dict__)
                    self._transition_to(guard_result.next_state_if_failed)
                    break

                # 2. 发射 StateEntered
                self.context.record_state_visit(current_state)
                self._get_event_bus().emit("StateEntered", {
                    "state": current_state,
                    "task": self.workflow.states[current_state].task if current_state in self.workflow.states else None,
                    "attempt": self.context.get_attempt(current_state),
                    "timestamp": _now_iso(),
                })

                # 3. 执行当前 state 的 task
                task_result = self._execute_state(current_state)

                # 4. 校验和记录 TaskResult
                if task_result:
                    self.context.record_task_result(current_state, task_result.to_dict())

                    # 校验 TaskResult
                    validation_issues = task_result.validate()
                    if validation_issues:
                        for issue in validation_issues:
                            self._get_event_bus().emit("ValidatorFinished", {
                                "state": current_state,
                                "passed": False,
                                "issues": validation_issues,
                            })

                    # Promote artifacts
                    self._promote_artifacts(task_result)

                # 5. 发射 TaskFinished
                decision = task_result.get_decision() if task_result else "fail"
                self._get_event_bus().emit("TaskFinished", {
                    "state": current_state,
                    "decision": decision,
                    "status": task_result.status if task_result else "failed",
                    "timestamp": _now_iso(),
                })

                # 6. Transition
                transition = self.sm.resolve_transition(current_state, decision)
                self._get_event_bus().emit("TransitionSelected", transition.to_event_dict())

                # 7. 更新状态
                next_state = transition.next_state
                self._transition_to(next_state)
                current_state = next_state

                # 持久化
                self.context.save()

            # 循环结束
            if self._cancelled:
                self._get_event_bus().emit("WorkflowCancelled", {
                    "run_id": self._run_id,
                    "final_state": current_state,
                    "timestamp": _now_iso(),
                })
            else:
                self._get_event_bus().emit("WorkflowCompleted", {
                    "run_id": self._run_id,
                    "final_state": current_state,
                    "total_states": len(self.context.state_history),
                    "timestamp": _now_iso(),
                })

        finally:
            self._stop_heartbeat()
            self._running = False
            if self.context:
                self.context.save()

        return current_state

    def _execute_state(self, state_name: str):
        """执行一个 state 的 task。"""
        state = self.workflow.get_state(state_name)
        if state is None:
            return self._create_error_result(
                state_name, f"状态 '{state_name}' 未定义"
            )

        task_model = self.workflow.get_task(state.task) if state.task else None
        if task_model is None and state.task:
            return self._create_error_result(
                state_name, f"Task '{state.task}' 未定义"
            )

        # 解决 Role → Agent
        agent_name = self._resolve_agent(task_model) if task_model else "mock"

        # 构建 AgentInput
        agent_input = self._build_agent_input(state_name, task_model, agent_name)

        # 发射 AgentStarted
        self._get_event_bus().emit("AgentStarted", {
            "state": state_name,
            "task": task_model.name if task_model else None,
            "agent": agent_name,
            "timestamp": _now_iso(),
        })

        # 执行 Agent
        start_time = time.time()
        try:
            result = self._run_agent(agent_name, agent_input, state_name)
            exec_time = time.time() - start_time

            # 填充 execution metadata（如果 Agent 没有提供）
            if result and not result.execution:
                result.execution = {
                    "started_at": _now_iso(),
                    "finished_at": _now_iso(),
                    "duration_seconds": exec_time,
                    "attempt": self.context.get_attempt(state_name),
                    "exit_code": 0,
                }

            return result

        except Exception as e:
            exec_time = time.time() - start_time
            return self._create_error_result(
                state_name,
                f"Agent 执行异常: {e}",
                exec_time,
            )

    def _build_agent_input(
        self,
        state_name: str,
        task_model: TaskModel | None,
        agent_name: str,
    ) -> AgentInput:
        """构建 AgentInput。"""
        # 转换 TaskModel → TaskConfig
        task_config = AgentTaskConfig(
            name=task_model.name if task_model else state_name,
            instruction=task_model.instruction if task_model else "",
            role=task_model.role if task_model else agent_name,
            inputs=task_model.inputs if task_model else [],
            output=task_model.output if task_model else "",
        )

        # 获取 Skill 上下文
        skill_context = ""
        try:
            from ..skills.adoption import get_adoption_summary
            skill_context = get_adoption_summary(self.context)
        except ImportError:
            pass

        # 构建 staging paths
        staging_dir = os.path.join(self.context.run_root, "staging", state_name)
        output_name = task_config.output or "output"
        staging_paths = {
            output_name: os.path.join(staging_dir, f"{output_name}.md"),
            "task_result": os.path.join(staging_dir, "task_result.json"),
        }

        # 获取 TaskResult schema
        allowed_decisions = task_model.allowed_decisions if task_model else []
        try:
            from ..tasks.result_schema import build_task_result_schema
            schema = build_task_result_schema(allowed_decisions)
        except ImportError:
            schema = {}

        return AgentInput(
            task=task_config,
            context=self.context,
            skill_context=skill_context,
            skill_policy={"allowed_decisions": allowed_decisions} if allowed_decisions else {},
            expected_task_result_schema=schema,
            staging_paths=staging_paths,
        )

    def _run_agent(
        self,
        agent_name: str,
        agent_input: AgentInput,
        state_name: str,
    ):
        """运行 Agent。

        优先使用注册的 agent adapter，fallback 到 mock agent。
        """
        # 尝试获取已注册的 agent adapter
        adapter = None
        if self._agent_registry and agent_name in self._agent_registry:
            adapter = self._get_agent_adapter(agent_name)
        else:
            # 使用 mock agent
            try:
                from ..agents.mock import MockAgent
                adapter = MockAgent()
            except ImportError:
                pass

        if adapter is None:
            return self._create_error_result(
                state_name,
                f"Agent '{agent_name}' 未注册且无 mock fallback",
            )

        # 确保 staging 目录存在
        staging_dir = os.path.join(self.context.run_root, "staging", state_name)
        os.makedirs(staging_dir, exist_ok=True)

        # 执行
        result = adapter.execute(agent_input)
        return result

    def _get_agent_adapter(self, agent_name: str):
        """获取 Agent adapter 实例。"""
        try:
            from ..agents.registry import AgentRegistry
            registry = AgentRegistry(self._agent_registry)
            return registry.resolve(agent_name)
        except ImportError:
            return None

    def _resolve_agent(self, task_model: TaskModel | None) -> str:
        """解析 Role → Agent 名称。"""
        if task_model is None:
            return "mock"

        role = self.workflow.get_role(task_model.role)
        if role:
            return role.agent

        return "mock"

    def _transition_to(self, next_state: str):
        """执行状态迁移。"""
        if self.context:
            self.context.current_state = next_state
            self.context.touch()

    def _promote_artifacts(self, task_result):
        """Promote artifacts（从 staging 到正式 artifacts）。"""
        if task_result is None:
            return

        artifacts = task_result.get_artifacts()
        for artifact in artifacts:
            try:
                from ..artifacts.promotion import promote_artifact
                promote_artifact(
                    staging_path=artifact.staging_path,
                    artifact_path=artifact.artifact_path,
                    run_root=self.context.run_root,
                    artifact_name=artifact.name,
                )
                self.context.promote_artifact(artifact.name, artifact.artifact_path)
                self._get_event_bus().emit("ArtifactPromoted", {
                    "name": artifact.name,
                    "artifact_path": artifact.artifact_path,
                })
            except ImportError:
                # 如果 promotion 模块不可用，直接记录
                self.context.promote_artifact(artifact.name, artifact.artifact_path)

    def cancel(self, reason: str = ""):
        """取消运行。"""
        self._cancelled = True

    def _start_heartbeat(self):
        """启动心跳线程。"""
        try:
            from ..observability.heartbeat import HeartbeatEmitter
            self._heartbeat_thread = HeartbeatEmitter.start(
                run_id=self._run_id,
                context_getter=lambda: self.context,
                event_bus=self._get_event_bus(),
            )
        except ImportError:
            pass

    def _stop_heartbeat(self):
        """停止心跳线程。"""
        if self._heartbeat_thread:
            try:
                from ..observability.heartbeat import HeartbeatEmitter
                HeartbeatEmitter.stop(self._heartbeat_thread)
            except ImportError:
                pass

    def _create_error_result(
        self,
        state_name: str,
        error: str,
        duration: float = 0.0,
    ):
        """创建错误 TaskResult。"""
        from ..tasks.result import TaskResult, ExecutionMetadata, Issue
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent="runner",
            status="failed",
            decision="fail",
            summary=error,
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=duration,
                attempt=1,
                exit_code=1,
            ),
            issues=[Issue(severity="blocking", title="执行失败", detail=error)],
        )


def cancel_run(run_id: str, reason: str = "") -> bool:
    """取消一个正在运行的 workflow。

    P0 实现：设置取消标记。
    P1 完善：信号机制。
    """
    # P0: 标记取消（需要运行中的 Runner 实例检查标记）
    cancel_path = os.path.join(
        ".agent-workflow", "runs", run_id, "cancelled"
    )
    try:
        os.makedirs(os.path.dirname(cancel_path), exist_ok=True)
        with open(cancel_path, "w") as f:
            f.write(reason or "cancelled by user")
        return True
    except Exception:
        return False


class _NullEventBus:
    """空 EventBus 实现，用于没有 observability 模块时的 fallback。"""

    def emit(self, event_type: str, payload: dict):
        pass
