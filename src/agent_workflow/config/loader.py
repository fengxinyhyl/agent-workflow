"""配置加载器 — 从 YAML 文件加载 Workflow/Agent/Role 配置。"""

from __future__ import annotations

import os
import re
import yaml
from typing import Any

from .models import (
    WorkflowConfig,
    TaskModel,
    StateModel,
    RoleModel,
    AgentModel,
    GuardModel,
)
from .env import EnvResolver


# ── YAML 安全加载器（禁用布尔自动转换）─────────────────────────────────
# PyYAML 默认 YAML 1.1 模式下将 on/off/yes/no/ON/OFF 等解析为 Python bool。
# workflow YAML 的 key 名 `on`（表示 state transition mapping）会被错误转换为 True。
# 解决方法：从 SafeLoader 中移除布尔 implicit resolver，所有标量保留为字符串。

class _SafeStringLoader(yaml.SafeLoader):
    """禁用了布尔自动转换的 SafeLoader。"""


# 移除 SafeLoader 中匹配布尔值的隐式 resolver
# SafeLoader.yaml_implicit_resolvers 是一个 dict: {regex_pattern: [tag, match_list]}
# 布尔正则: ^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$
def _remove_bool_resolver():
    """从 _SafeStringLoader 中移除布尔 implicit resolver。"""
    resolvers = dict(_SafeStringLoader.yaml_implicit_resolvers)
    to_remove = []
    for pattern, resolvers_list in resolvers.items():
        new_list = []
        for tag, match_list in resolvers_list:
            if tag == "tag:yaml.org,2002:bool":
                continue
            new_list.append((tag, match_list))
        if new_list:
            resolvers[pattern] = new_list
        else:
            to_remove.append(pattern)
    for pattern in to_remove:
        del resolvers[pattern]
    _SafeStringLoader.yaml_implicit_resolvers = resolvers


_remove_bool_resolver()


def _expand_env(value: str, env: EnvResolver | None = None) -> str:
    """展开字符串中的环境变量占位符 {VAR_NAME}。"""
    if env is None:
        env = EnvResolver()
    return env.resolve(value)


def load_task(data: dict[str, Any]) -> TaskModel:
    """从字典加载 TaskModel。"""
    return TaskModel(
        name=data.get("name", ""),
        instruction=data.get("instruction", ""),
        role=data.get("role", ""),
        inputs=data.get("input", data.get("inputs", [])),
        output=data.get("output", ""),
        description=data.get("description", ""),
        timeout_seconds=data.get("timeout_seconds", 3600),
        allowed_decisions=data.get("allowed_decisions", []),
    )


def load_state(data: dict[str, Any]) -> StateModel:
    """从字典加载 StateModel。"""
    return StateModel(
        name=data.get("name", ""),
        task=data.get("task", ""),
        on=data.get("on", {}),
        default=data.get("default", "failed"),
        description=data.get("description", ""),
        terminal=data.get("terminal", False),
    )


def load_role(data: dict[str, Any]) -> RoleModel:
    """从字典加载 RoleModel。"""
    # 支持简化格式: planner: codex_plan → {name: planner, agent: codex_plan}
    return RoleModel(
        name=data.get("name", ""),
        agent=data.get("agent", ""),
        fallback_agents=data.get("fallback_agents", data.get("fallback", [])),
        description=data.get("description", ""),
    )


def load_agent(data: dict[str, Any]) -> AgentModel:
    """从字典加载 AgentModel。"""
    return AgentModel(
        name=data.get("name", ""),
        provider=data.get("provider", ""),
        command=data.get("command", ""),
        cwd=data.get("cwd", "{project_root}"),
        sandbox=data.get("sandbox", ""),
        timeout_seconds=data.get("timeout_seconds", 3600),
        description=data.get("description", ""),
    )


def load_guard(data: dict[str, Any]) -> GuardModel:
    """从字典加载 GuardModel。"""
    return GuardModel(
        max_visits=data.get("max_visits", 0),
        max_duration_minutes=data.get("max_duration_minutes", 0),
        max_retries=data.get("max_retries", 0),
        on_guard_failed=data.get("on_guard_failed", "failed"),
    )


def load_workflow(path: str) -> WorkflowConfig:
    """从 YAML 文件加载 WorkflowConfig。

    用法:
        wf = load_workflow("examples/software-dev/workflow.yaml")
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f, Loader=_SafeStringLoader)

    if data is None:
        raise ValueError(f"配置文件为空: {path}")

    # 展开环境变量
    env = EnvResolver()
    resolved = env.resolve_dict(data)

    # 加载 tasks
    tasks = {}
    tasks_raw = resolved.get("tasks", {})
    if isinstance(tasks_raw, list):
        for item in tasks_raw:
            task = load_task(item)
            tasks[task.name] = task
    elif isinstance(tasks_raw, dict):
        for name, item in tasks_raw.items():
            if isinstance(item, dict):
                item["name"] = name
                task = load_task(item)
                tasks[name] = task

    # 加载 states
    states = {}
    states_raw = resolved.get("states", {})
    if isinstance(states_raw, list):
        for item in states_raw:
            state = load_state(item)
            states[state.name] = state
    elif isinstance(states_raw, dict):
        for name, item in states_raw.items():
            if isinstance(item, dict):
                item["name"] = name
                state = load_state(item)
                states[name] = state

    # 加载 roles
    roles = {}
    roles_raw = resolved.get("roles", {})
    if isinstance(roles_raw, dict):
        for name, item in roles_raw.items():
            if isinstance(item, str):
                # 简化格式: planner: codex_plan
                roles[name] = RoleModel(name=name, agent=item)
            elif isinstance(item, dict):
                item["name"] = name
                role = load_role(item)
                roles[name] = role
    elif isinstance(roles_raw, list):
        for item in roles_raw:
            role = load_role(item)
            roles[role.name] = role

    # 加载 guards
    guards = load_guard(resolved.get("guards", {}))

    # 加载 terminal states
    terminal_states = resolved.get("terminal_states", [])
    if not terminal_states:
        # 自动识别 terminal states
        terminal_states = [
            name for name, s in states.items()
            if s.terminal or not s.on
        ]

    return WorkflowConfig(
        name=resolved.get("name", os.path.splitext(os.path.basename(path))[0]),
        version=str(resolved.get("version", "1")),
        description=resolved.get("description", ""),
        tasks=tasks,
        states=states,
        roles=roles,
        guards=guards,
        initial_state=resolved.get("initial_state", ""),
        terminal_states=terminal_states,
        required_skills=resolved.get("required_skills", []),
    )


def load_agents_config(path: str) -> dict[str, AgentModel]:
    """从 YAML 文件加载 Agent 配置。

    用法:
        agents = load_agents_config("examples/software-dev/agents.yaml")
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f, Loader=_SafeStringLoader)

    if data is None:
        return {}

    env = EnvResolver()
    resolved = env.resolve_dict(data)

    agents_raw = resolved.get("agents", resolved)
    agents = {}

    if isinstance(agents_raw, dict):
        for name, item in agents_raw.items():
            if isinstance(item, dict):
                item["name"] = name
                agent = load_agent(item)
                agents[name] = agent

    return agents


def load_roles_config(path: str) -> dict[str, RoleModel]:
    """从 YAML 文件加载 Role 配置。

    用法:
        roles = load_roles_config("examples/software-dev/roles.yaml")
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f, Loader=_SafeStringLoader)

    if data is None:
        return {}

    roles_raw = data.get("roles", data)
    roles = {}

    if isinstance(roles_raw, dict):
        for name, item in roles_raw.items():
            if isinstance(item, str):
                roles[name] = RoleModel(name=name, agent=item)
            elif isinstance(item, dict):
                item["name"] = name
                roles[name] = load_role(item)

    return roles
