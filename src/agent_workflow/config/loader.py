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


def _check_forbidden_keys(
    data: dict[str, Any],
    forbidden: list[str],
    entity_type: str,
    entity_name: str,
):
    """检查字典中是否包含禁止的 key，命中则抛 ValueError。"""
    for key in forbidden:
        if key in data:
            raise ValueError(
                f"{entity_type} '{entity_name}' 包含禁止字段 '{key}'。"
                f" {entity_type} 不应定义 '{key}'，请从配置中移除。"
            )


def load_task(data: dict[str, Any]) -> TaskModel:
    """从字典加载 TaskModel。

    Task 禁止出现: transition、guard、retry、validator、provider、runtime。
    命中时抛 ValueError。
    """
    _check_forbidden_keys(
        data,
        [
            "transition", "transitions", "guard", "guards", "retry",
            "validator", "provider", "runtime", "transport",
        ],
        entity_type="Task",
        entity_name=data.get("name", "(unknown)"),
    )
    return TaskModel(
        name=data.get("name", ""),
        instruction=data.get("instruction", ""),
        role=data.get("role", ""),
        inputs=data.get("input", data.get("inputs", [])),
        output=data.get("output", ""),
        description=data.get("description", ""),
        timeout_seconds=data.get("timeout_seconds", 3600),
        allowed_decisions=data.get("allowed_decisions", []),
        skills=data.get("skills", []),
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
        gate=data.get("gate", False),
    )


def load_role(data: dict[str, Any]) -> RoleModel:
    """从字典加载 RoleModel。

    Role 禁止出现: capability、policy、validator、contract、guard。
    命中时抛 ValueError。
    """
    _check_forbidden_keys(
        data,
        [
            "capability", "capabilities", "policy", "validator",
            "contract", "guard", "guards",
        ],
        entity_type="Role",
        entity_name=data.get("name", "(unknown)"),
    )
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
        permission_mode=data.get("permission_mode", ""),
        allowed_tools=data.get("allowed_tools", ""),
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


def _unroll_loops(
    resolved: dict[str, Any],
    states: dict[str, StateModel],
) -> dict[str, StateModel]:
    """展开 _loop 块为线性 state 序列。

    语法:
        _loop:
          states: [plan, review, advise]   # 要重复的 state 序列（按已有 state 名引用）
          repeat: 3                         # 重复次数
          on_break: execute                 # 循环结束后进入的 state 名

    展开结果（以 repeat=3 为例）:
        plan_r1 → review_r1 → advise_r1 → plan_r2 → review_r2 → advise_r2
          → plan_r3 → review_r3 → advise_r3 → on_break

    每轮 advise 节点保留 approve/reject 决策:
      - 前 N-1 轮: revise → 下一轮 plan_rN（继续修改）
      - 最后轮:    不设 revise → 只能 approve 或 reject
      - 任意轮:    approve → on_break（提前通过，跳过剩余轮次）

    思路: 状态机保持 DAG，循环交给 YAML 层展开，Runner 无需感知。
    """
    loop_block = resolved.pop("_loop", None)
    if loop_block is None:
        return states

    loop_state_names: list[str] = loop_block.get("states", [])
    repeat: int = loop_block.get("repeat", 1)
    on_break: str = loop_block.get("on_break", "")

    if not loop_state_names or repeat < 1:
        return states

    # 校验：循环内的 state 名必须在 states 中存在
    for name in loop_state_names:
        if name not in states:
            raise ValueError(
                f"_loop.states 引用了未定义的 state '{name}'，"
                f"请先在 states 块中定义它"
            )

    # 校验：on_break 目标必须存在（或将在后续 states 中定义）
    if on_break and on_break not in states:
        raise ValueError(
            f"_loop.on_break '{on_break}' 未在 states 中定义，"
            f"请确保执行阶段 states 在 _loop 之前声明"
        )

    expanded: dict[str, StateModel] = {}

    for r in range(1, repeat + 1):
        for i, base_name in enumerate(loop_state_names):
            base_state = states[base_name]
            round_name = f"{base_name}_r{r}"

            # 复制原始 state 的 on 映射，后续会修正 transition 目标
            on = dict(base_state.on) if base_state.on else {}

            is_last_state_in_round = (i == len(loop_state_names) - 1)
            is_last_round = (r == repeat)

            if is_last_state_in_round:
                # ── 轮次最后一个 state（如 advise）──
                if is_last_round:
                    # 最后一轮：移除 revise（不再循环），approve → on_break
                    on.pop("revise", None)
                    for decision in on:
                        if on[decision] == base_name:  # 指向循环起始 state
                            on[decision] = on_break
                    # approve 指向 on_break（如果 approve 原本指向循环外的 execute，保持不变）
                    if "approve" not in on:
                        on["approve"] = on_break
                else:
                    # 前 N-1 轮：revise → 下一轮的 plan_r{N+1}
                    next_first = f"{loop_state_names[0]}_r{r + 1}"
                    if "revise" in on:
                        on["revise"] = next_first
                    else:
                        on["revise"] = next_first  # 自动添加 revise 到下一轮
                    # approve → on_break（允许提前通过）
                    if "approve" in on:
                        on["approve"] = on_break
                    else:
                        on["approve"] = on_break
            else:
                # ── 非轮次最后一个 state（如 plan、review）──
                # done → 同轮次下一个 state
                next_in_round = f"{loop_state_names[i + 1]}_r{r}"
                if "done" in on:
                    on["done"] = next_in_round
                else:
                    on["done"] = next_in_round

            expanded[round_name] = StateModel(
                name=round_name,
                task=base_state.task,
                on=on,
                default=base_state.default,
                description=f"{base_state.description or base_name} (第 {r} 轮)",
                terminal=False,
                gate=base_state.gate,
            )

    # 合并：原始 states 中循环体内的 state 名替换为展开后的 _r1/_r2/_r3
    # 循环外的 state 保持不变，但其 transition 中指向循环内 state 的需修正为 _r1
    final_states: dict[str, StateModel] = {}
    for name, state in states.items():
        if name not in loop_state_names:
            # 修正该 state 的 on 映射中指向循环内 state 的引用 → _r1
            fixed_on = {}
            for decision, target in (state.on or {}).items():
                if target in loop_state_names:
                    fixed_on[decision] = f"{target}_r1"
                else:
                    fixed_on[decision] = target
            final_states[name] = StateModel(
                name=state.name,
                task=state.task,
                on=fixed_on,
                default=(
                    f"{state.default}_r1"
                    if state.default in loop_state_names
                    else state.default
                ),
                description=state.description,
                terminal=state.terminal,
                gate=state.gate,
            )
        # 循环体内的原始 state 被展开版本替换，不保留
    final_states.update(expanded)

    # 修正 initial_state：如果 initial_state 是循环第一个 state，指向 _r1
    initial = resolved.get("initial_state", "")
    if initial == loop_state_names[0]:
        resolved["initial_state"] = f"{initial}_r1"

    return final_states


def load_workflow(path: str) -> WorkflowConfig:
    """从 YAML 文件加载 WorkflowConfig。

    用法:
        wf = load_workflow("workflows/software-dev/workflow.yaml")

    YAML 可选的 _loop 块用于声明重复 state 序列，加载时自动展开为线性 states。
    详见 _unroll_loops() 文档。
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

    # ── 展开 _loop 块（必须在加载 states 之后、计算 terminal states 之前）──
    states = _unroll_loops(resolved, states)

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
        agents = load_agents_config("workflows/software-dev/agents.yaml")
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
        roles = load_roles_config("workflows/software-dev/roles.yaml")
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
