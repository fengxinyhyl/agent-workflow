# 方案 C：运行时 Agent 覆盖 Map 实现计划

## 目标

支持在工作流执行时通过 CLI 参数 `--agent-map` 动态指定不同节点（state/task）使用的 agent，无需修改 YAML 配置。YAML 中的 `task.agent` 保留作为默认值，CLI 传入的映射优先。

## 用例

```bash
# 第一个节点（plan）用 codex，第二个节点（review）用 claude
agent-workflow run -w workflow.yaml -g "实现登录功能" \
  --agent-map "plan=cc-opus,review=claude-haiku"

# 也可以只覆盖部分节点，未覆盖的走 YAML 默认值
agent-workflow run -w workflow.yaml -g "重构代码" \
  --agent-map "code_audit=claude-haiku"
```

## 改动范围

### 1. `src/agent_workflow/state_machine/runner.py`

#### 1.1 `Runner.__init__` — 新增 `agent_overrides` 参数

在 `__init__` 参数列表末尾新增：

```python
agent_overrides: dict[str, str] | None = None,
```

存储到 `self._agent_overrides = agent_overrides or {}`。

#### 1.2 `Runner._resolve_agent()` — 增加覆盖逻辑

当前实现（line 974-978）：

```python
def _resolve_agent(self, task_model: TaskModel | None) -> str:
    if task_model is None:
        return "mock"
    return task_model.agent or "mock"
```

改为：

```python
def _resolve_agent(self, task_model: TaskModel | None) -> str:
    """按优先级解析 agent：CLI 覆盖 > task.agent > mock fallback。"""
    if task_model is None:
        return "mock"

    task_name = task_model.name

    # 1. CLI --agent-map 覆盖（按 task name 匹配）
    if self._agent_overrides and task_name in self._agent_overrides:
        return self._agent_overrides[task_name]

    # 2. YAML 配置的 agent
    if task_model.agent:
        return task_model.agent

    # 3. mock fallback
    return "mock"
```

#### 1.3 `Runner._execute_state()` — 日志中标注覆盖

在 `_execute_state()` 中（line 776 附近），当 agent 被覆盖时发射信息事件或记录到 context，便于追溯：

```python
agent_name = self._resolve_agent(task_model) if task_model else "mock"

# 记录是否被 CLI 覆盖（用于 status/explain 展示）
if task_model and self._agent_overrides and task_model.name in self._agent_overrides:
    if self.context:
        self.context.workflow_variables["_agent_override_source"] = "cli"
```

### 2. `src/agent_workflow/cli.py`

#### 2.1 `cmd_run` 函数 — 解析 `--agent-map` 参数

在 `cmd_run` 中添加：

```python
# 解析 --agent-map
agent_overrides = {}
agent_map_str = getattr(args, 'agent_map', '') or ''
if agent_map_str:
    for pair in agent_map_str.split(','):
        pair = pair.strip()
        if '=' in pair:
            k, v = pair.split('=', 1)
            agent_overrides[k.strip()] = v.strip()
```

传给 `Runner`：

```python
runner = Runner(
    wf,
    goal=args.goal,
    ...
    agent_overrides=agent_overrides if agent_overrides else None,
)
```

#### 2.2 `build_parser()` — 注册 `--agent-map` 参数

在 `run` 子命令的参数组中新增：

```python
p.add_argument(
    "--agent-map",
    help="运行时 agent 覆盖映射，格式: task1=agent1,task2=agent2（按 task name 匹配）",
    default="",
)
```

### 3. 测试

#### 3.1 新增单元测试 `tests/unit/test_agent_override.py`

| 用例 | 描述 |
|------|------|
| `test_resolve_agent_no_override` | 无覆盖时走 `task.agent` |
| `test_resolve_agent_with_override` | CLI 覆盖优先于 `task.agent` |
| `test_resolve_agent_partial_override` | 部分 task 覆盖，其余走默认 |
| `test_resolve_agent_task_model_none` | `task_model=None` 返回 `"mock"` |
| `test_resolve_agent_fallback_mock` | `task.agent` 为空且无覆盖时返回 `"mock"` |
| `test_agent_map_parsing` | 测试 CLI 解析 `"plan=cc,review=claude"` 的正确性 |

#### 3.2 扩展集成测试

在现有 `tests/integration/test_software_dev_mock_flow.py` 中增加一个场景：传入 `agent_overrides` 验证覆盖生效。

### 4. 可观测性变更

`AgentStarted` 事件中已包含 `agent` 字段（runner.py:834-839），覆盖后的 agent 名会直接体现在事件中，无需额外改动。建议在 `RunContext.workflow_variables` 中增加标记：

```
_agent_overrides: {"plan": "cc-opus"}   # 记录本次运行的所有覆盖项
```

便于 `status` / `explain` 命令展示"此节点的 agent 被 CLI 覆盖"。

### 5. 文档更新

在 `CLAUDE.md` 核心命令一节补充 `--agent-map` 用法示例。

---

## 改动文件清单

| 文件 | 改动类型 | 行数估计 |
|------|---------|---------|
| `src/agent_workflow/state_machine/runner.py` | 修改 `__init__` + `_resolve_agent` + `_execute_state` | ~15 行 |
| `src/agent_workflow/cli.py` | 修改 `cmd_run` + `build_parser` | ~15 行 |
| `tests/unit/test_agent_override.py` | **新增** | ~80 行 |
| `tests/integration/test_software_dev_mock_flow.py` | 新增 1 个测试用例 | ~30 行 |
| `CLAUDE.md` | 补充文档 | ~5 行 |

## 不涉及的范围

- ❌ **YAML 变量展开**（方案 B）：本次不做，但 `--agent-map` 与 YAML 变量展开不冲突，可后续叠加
- ❌ **Agent 热切换**：已经启动的工作流不支持中途改 agent（需要断点续跑 + 重新指定 `--agent-map`）
- ❌ **按 state 名匹配**：`--agent-map` 按 **task name** 匹配而非 state name，因为同一个 task 可能被多个 state 引用

## 风险与边界

- agent 名前向兼容：若 `--agent-map` 指定的 agent 未在 `agents.yaml` 中注册，`AgentRegistry.resolve()` 会 fallback 到 `MockAgent`（符合现有行为）
- 覆盖仅在 task 级别生效：`_resolve_agent()` 的调用方在 `_execute_state()` 中只传 `task_model`，不会出现 state 级别差异
