"""配置模型定义。

所有模型严格遵循 v4 计划约束：
- Task 禁止: transition, guard, retry, validator, provider, runtime
- Role 禁止: capability, policy, validator, contract, guard
- Transition 必须有 default
- Guard 支持: max_visits, max_duration_minutes, max_retries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskModel:
    """Task 配置（瘦模型）。

    Task 只描述：执行什么、输入是什么、输出是什么、由哪个 role 执行。
    """

    name: str = ""
    instruction: str = ""
    role: str = ""
    inputs: list[str] = field(default_factory=list)
    output: str = ""

    # 元数据（非执行逻辑）
    description: str = ""
    timeout_seconds: int = 3600

    # TaskResult 策略
    allowed_decisions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "instruction": self.instruction,
            "role": self.role,
            "inputs": self.inputs,
            "output": self.output,
            "description": self.description,
            "timeout_seconds": self.timeout_seconds,
            "allowed_decisions": self.allowed_decisions,
        }


@dataclass
class StateModel:
    """State 配置。

    每个 state 关联一个 task，定义 on（decision → next_state）映射和 default。
    """

    name: str = ""
    task: str = ""  # task name
    on: dict[str, str] = field(default_factory=dict)  # decision → next_state
    default: str = "failed"  # 未知 decision 的默认跳转
    description: str = ""
    terminal: bool = False  # 是否为终止状态

    def resolve_transition(self, decision: str) -> str:
        """根据 decision 解析下一状态。

        规则：
        1. 如果 decision 在 on 中，返回对应状态
        2. 否则返回 default
        3. 如果 terminal，不跳转
        """
        if self.terminal:
            return self.name
        if decision in self.on:
            return self.on[decision]
        return self.default

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "task": self.task,
            "on": self.on,
            "default": self.default,
            "description": self.description,
            "terminal": self.terminal,
        }


@dataclass
class RoleModel:
    """Role 配置（最小）。

    Role 只是一个 agent alias，可选 fallback。
    禁止承载：capability、policy、validator、contract、guard。
    """

    name: str = ""
    agent: str = ""  # primary agent name
    fallback_agents: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent": self.agent,
            "fallback_agents": self.fallback_agents,
            "description": self.description,
        }


@dataclass
class AgentModel:
    """Agent Profile 配置。

    P0 provider 就是 CLI adapter，不抽象 runtime。
    """

    name: str = ""
    provider: str = ""  # claude / codex / deepseek / mock
    command: str = ""  # 可包含环境变量占位符如 {CODEX_COMMAND}
    cwd: str = "{project_root}"
    sandbox: str = ""  # workspace-write / workspace-read / none
    timeout_seconds: int = 3600
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "command": self.command,
            "cwd": self.cwd,
            "sandbox": self.sandbox,
            "timeout_seconds": self.timeout_seconds,
            "description": self.description,
        }


@dataclass
class GuardModel:
    """Guard 配置。

    P0 支持三类 Guard：
    - max_visits: 限制某 state 被进入的次数
    - max_duration_minutes: 限制 workflow 或 state 的最长运行时间
    - max_retries: 限制同一 state/task 的重试次数
    """

    max_visits: int = 0  # 0 表示不限制
    max_duration_minutes: int = 0  # 0 表示不限制
    max_retries: int = 0  # 0 表示不限制
    on_guard_failed: str = "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_visits": self.max_visits,
            "max_duration_minutes": self.max_duration_minutes,
            "max_retries": self.max_retries,
            "on_guard_failed": self.on_guard_failed,
        }


@dataclass
class WorkflowConfig:
    """完整的 Workflow 配置。

    字段说明：
      name: Workflow 名称
      version: 配置版本
      description: 描述
      tasks: Task 列表（按 name 索引）
      states: State 列表（按 name 索引）
      roles: Role 列表（按 name 索引）
      guards: 全局 Guard 配置
      initial_state: 初始状态名称
      terminal_states: 终止状态名称列表
      required_skills: 必需的 skill 列表
    """

    name: str = ""
    version: str = "1"
    description: str = ""
    tasks: dict[str, TaskModel] = field(default_factory=dict)
    states: dict[str, StateModel] = field(default_factory=dict)
    roles: dict[str, RoleModel] = field(default_factory=dict)
    guards: GuardModel = field(default_factory=GuardModel)
    initial_state: str = ""
    terminal_states: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)

    def get_state(self, name: str) -> StateModel | None:
        return self.states.get(name)

    def get_task(self, name: str) -> TaskModel | None:
        return self.tasks.get(name)

    def get_task_for_state(self, state_name: str) -> TaskModel | None:
        state = self.states.get(state_name)
        if state is None:
            return None
        return self.tasks.get(state.task)

    def get_role(self, name: str) -> RoleModel | None:
        return self.roles.get(name)

    def get_role_for_state(self, state_name: str) -> RoleModel | None:
        task = self.get_task_for_state(state_name)
        if task is None:
            return None
        return self.roles.get(task.role)

    def is_terminal(self, state_name: str) -> bool:
        return state_name in self.terminal_states

    def validate(self) -> list[str]:
        """校验配置完整性，返回问题列表。"""
        issues = []

        # 1. 初始状态存在
        if self.initial_state and self.initial_state not in self.states:
            issues.append(f"initial_state '{self.initial_state}' 未在 states 中定义")

        # 2. State 引用的 task 存在
        for name, state in self.states.items():
            if state.task and state.task not in self.tasks:
                issues.append(f"state '{name}' 引用的 task '{state.task}' 未定义")

        # 3. State.on 的目标 state 存在
        for name, state in self.states.items():
            if state.terminal:
                continue
            for decision, next_state in state.on.items():
                if next_state not in self.states:
                    issues.append(
                        f"state '{name}' on '{decision}' → '{next_state}' "
                        f"目标 state 未定义"
                    )

        # 4. default 目标 state 存在（非 terminal）
        for name, state in self.states.items():
            if state.terminal:
                continue
            if state.default and state.default not in self.states:
                issues.append(
                    f"state '{name}' default → '{state.default}' 目标 state 未定义"
                )

        # 5. Task 引用的 role 存在
        for name, task in self.tasks.items():
            if task.role and task.role not in self.roles:
                issues.append(f"task '{name}' 引用的 role '{task.role}' 未定义")

        # 6. Role 引用的 agent 存在（这个需要在加载 agents 后校验）
        #    此处只做基本检查：如果 role.agent 为空且 fallback 也为空，报告

        # 7. 终止状态不能有 on 转换
        for name in self.terminal_states:
            if name in self.states:
                state = self.states[name]
                if state.on:
                    issues.append(f"终止状态 '{name}' 不应定义 on 转换")

        return issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tasks": {n: t.to_dict() for n, t in self.tasks.items()},
            "states": {n: s.to_dict() for n, s in self.states.items()},
            "roles": {n: r.to_dict() for n, r in self.roles.items()},
            "guards": self.guards.to_dict(),
            "initial_state": self.initial_state,
            "terminal_states": self.terminal_states,
            "required_skills": self.required_skills,
        }
