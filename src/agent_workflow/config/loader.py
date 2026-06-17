"""配置加载器 — 从 YAML 文件加载 Workflow/Agent 配置。"""

from __future__ import annotations

import os
import re
import yaml
from typing import Any

from .models import (
    WorkflowConfig,
    TaskModel,
    StateModel,
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


def _as_bool(value: Any) -> bool:
    """将 YAML 中的布尔字段归一化为 bool。

    本 loader 禁用了 YAML bool 自动转换以保护 `on` transition key，
    因此 `gate: true` / `terminal: true` 会先以字符串进入模型。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1")
    return bool(value)


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
        agent=data.get("agent", data.get("role", "")),  # 兼容旧 role 字段
        inputs=data.get("input", data.get("inputs", [])),
        output=data.get("output", ""),
        description=data.get("description", ""),
        timeout_seconds=data.get("timeout_seconds", 3600),
        allowed_decisions=data.get("allowed_decisions", []),
        skills=data.get("skills", []),
        version_strategy=data.get("version_strategy", "overwrite"),
    )


def load_state(data: dict[str, Any]) -> StateModel:
    """从字典加载 StateModel。"""
    return StateModel(
        name=data.get("name", ""),
        task=data.get("task", ""),
        on=data.get("on", {}),
        default=data.get("default", "failed"),
        description=data.get("description", ""),
        terminal=_as_bool(data.get("terminal", False)),
        gate=_as_bool(data.get("gate", False)),
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


def _unroll_single_loop(
    resolved: dict[str, Any],
    states: dict[str, StateModel],
    loop_block: dict[str, Any],
) -> dict[str, StateModel]:
    """展开单个 _loop 块为线性 state 序列。

    内部函数，由 _unroll_loops() 对每个 loop 块调用。
    """
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

    # 校验：on_break 目标必须存在
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
                # ── 轮次最后一个 state（如 advise、refinement）──
                if is_last_round:
                    # 最后一轮：移除 revise，所有指向循环内的决策 → on_break
                    on.pop("revise", None)
                    for decision in list(on.keys()):
                        if on[decision] in loop_state_names or on[decision] == base_name:
                            on[decision] = on_break
                    if "approve" not in on:
                        on["approve"] = on_break
                else:
                    # 前 N-1 轮：所有指向循环内的决策 → 下一轮首个 state
                    next_first = f"{loop_state_names[0]}_r{r + 1}"
                    has_loop_back = False
                    for decision in list(on.keys()):
                        if on[decision] in loop_state_names:
                            on[decision] = next_first
                            has_loop_back = True
                    # 若原始 state 未定义任何循环回跳决策，自动添加 revise（向后兼容）
                    if not has_loop_back:
                        on["revise"] = next_first
                    # 确保 approve → on_break（允许提前通过）
                    if "approve" not in on:
                        on["approve"] = on_break
            else:
                # ── 非轮次最后一个 state ──
                # done 及任意指向循环内的决策 → 同轮次下一个 state
                next_in_round = f"{loop_state_names[i + 1]}_r{r}"
                for decision in list(on.keys()):
                    if on[decision] in loop_state_names:
                        on[decision] = next_in_round
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

    # 合并：循环体内的原始 state 被展开版本替换
    # 循环外的 state 保持不变，但其 transition 中指向循环内 state 的需修正为 _r1
    final_states: dict[str, StateModel] = {}
    for name, state in states.items():
        if name not in loop_state_names:
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


def _unroll_loops(
    resolved: dict[str, Any],
    states: dict[str, StateModel],
) -> dict[str, StateModel]:
    """展开 _loops / _loop 块为线性 state 序列。

    支持两种 YAML 写法:

    1. 单循环（向后兼容）:
        _loop:
          states: [review, advise]
          repeat: 2
          on_break: execute

    2. 多循环（新增）:
        _loops:
          - states: [plan_review, plan_refinement]
            repeat: 2
            on_break: execution
          - states: [output_review, output_refinement]
            repeat: 2
            on_break: validation

    多循环按声明顺序依次展开，每个循环的 on_break 目标可以是后续循环
    的 states 中定义的 state 名（该引用会在后续循环展开时自动修正为 _r1）。

    思路: 状态机保持 DAG，循环交给 YAML 层展开，Runner 无需感知。
    """
    # 优先读取 _loops（数组），回退到 _loop（单对象）
    loop_config = resolved.pop("_loops", None)
    if loop_config is None:
        loop_config = resolved.pop("_loop", None)
        if loop_config is None:
            return states
        # 规范化为列表
        loops: list[dict[str, Any]] = [loop_config]
    elif isinstance(loop_config, list):
        loops = loop_config
    else:
        # 单个 dict 也规范化
        loops = [loop_config]

    if not loops:
        return states

    for loop_block in loops:
        states = _unroll_single_loop(resolved, states, loop_block)

    return states


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


