# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言偏好

所有回复、代码注释和 commit message 均使用简体中文。代码标识符、字符串字面量保持原样。

## 项目概述

通用 AI Agent 编排引擎。通过 YAML 配置驱动状态机，调度多个 AI Agent（Claude CLI、Codex CLI、Mock）按预定义工作流协作。支持长任务运行、可观测性、产物流管理、断点续跑。

## 核心命令

```bash
# 安装（开发模式）
pip install -e .

# 校验工作流配置
agent-workflow validate-config -w <workflow.yaml>

# 校验状态机完备性
agent-workflow validate-state-machine -w <workflow.yaml>

# Agent/Role 冒烟测试
agent-workflow smoke --agent claude_review

# 启动工作流
agent-workflow run -w <workflow.yaml> -g "<目标描述>"

# 查看运行状态 / 解释当前等待项
agent-workflow status -r <run_id>
agent-workflow explain -r <run_id>

# 查看日志 / 节点日志
agent-workflow log -r <run_id> --summary
agent-workflow tail -r <run_id> -s <state> -n 80

# 重试（默认 dry-run，加 --dispatch 真实执行）
agent-workflow retry -r <run_id> [--dispatch]

# 取消运行
agent-workflow cancel -r <run_id> --reason "..."
```

### 运行测试

```bash
# 运行全部测试
cd agent-workflow
$env:PYTHONPATH='src;.'; pytest tests -q

# 运行单个测试文件
$env:PYTHONPATH='src;.'; pytest tests/unit/test_state_machine.py -q

# 运行单个测试方法
$env:PYTHONPATH='src;.'; pytest tests/unit/test_task_result_v4.py::TestTaskResult::test_create_valid -q

# 只运行单元测试
$env:PYTHONPATH='src;.'; pytest tests/unit/ -q
```

`pyproject.toml` 已配置 `testpaths = ["tests"]` 和 `timeout = 300`。

## 架构

### 核心设计原则（v4）

1. **TaskResult 驱动**：所有语义决策来自 Agent 输出的 `TaskResult`，Runner 只负责状态迁移。Agent 禁止直接指定下一状态。
2. **Agent 只写 staging**：Agent 输出先进入 `staging/` 暂存区，Validator 通过后 promotion 到正式 `artifacts/`。
3. **Transition 必有 default**：未知 decision 走 default 分支，不会卡死。
4. **Guard 机制**：`max_visits` / `max_duration_minutes` / `max_retries` 防止失控循环。
5. **可观测性内置**：EventBus → ConsoleSink + JSONLSink + Heartbeat，所有关键事件统一分发。

### 模块架构

```
src/agent_workflow/
  cli.py                   # CLI 入口（argparse，11 个子命令）
  config/                  # YAML 配置模型 (TaskModel/StateModel/RoleModel/GuardModel/WorkflowConfig) 与加载器
  state_machine/           # StateMachine、Runner（主循环）、Transition、Guard、Retry
  tasks/                   # TaskResult（标准化 Agent 输出）、result_schema（JSON Schema 生成）
  agents/                  # Agent 适配器：BaseAgent → MockAgent / ClaudeCLI / CodexCLI / Command
  artifacts/               # Staging 暂存、Promotion（路径 containment 检查）、Resolver
  skills/                  # Skill 模型、YAML/Markdown 加载、Adoption 协议、Policy 解析
  validators/              # TaskResult / Artifact / Repo / Command 校验器
  observability/           # EventBus、ConsoleSink、JSONLSink、Heartbeat、status、explain
  context/                 # RunContext（可序列化到 workflow_state.json，支持断点续跑）+ AgentInput
  long_task/               # 长任务 MVP：WorkflowRun / WorkItem / DependencyGraph / EventLog / StateStore / QueueRunner
  state/                   # 状态持久化与锁
  roles/                   # Role → Agent 名称解析
```

### 数据流与关键路径

```
CLI (run) → load_workflow(YAML) → Runner.start() → Runner.run() 主循环:
  1. Guard 检查（max_visits / max_duration / max_retries）
  2. 发射 StateEntered 事件
  3. _execute_state:
     a. Role → Agent 解析
     b. Skill adoption（加载 required skills + task skills）
     c. 构建 AgentInput（task + context + skill_context + staging_paths + schema）
     d. Agent.execute() → 子进程运行 Claude/Codex CLI → 解析 stream-json 输出
     e. 返回 TaskResult
  4. TaskResult 校验 → 写入 staging/<state>/task_result.json
  5. 检查 blocking errors → 拒绝 promotion 或继续
  6. Artifact promotion（staging → artifacts，含路径 containment 检查）
  7. 若是 Gate 状态 → 暂停循环，等待 continue_from_gate()
  8. resolve_transition(decision) → 下一状态
  9. 持久化 RunContext → workflow_state.json
```

### TaskResult（Agent 输出契约）

Agent 必须输出标准 JSON，核心字段：
- `schema_version`（≥1）、`task_id`、`state`、`agent`
- `status`: success / failed / blocked / cancelled / timeout / invalid_output
- `decision`: approve / revise / reject / done / fail / blocked / no_op
- `execution`: started_at, finished_at, duration_seconds, attempt, exit_code, pid
- `artifacts`: [{name, staging_path, artifact_path, type}]
- `session_id`, `token_usage`, `log_path`, `packet_path`（Phase C/D 已填充）

### Workflow YAML 配置结构

```yaml
name: standard-dev
initial_state: plan
terminal_states: [done, failed, cancelled]
guards: {max_visits: 5, max_duration_minutes: 480, max_retries: 3}
required_skills: [agent-workflow-lifecycle]

tasks:
  <name>:
    instruction: "..."      # Agent prompt
    role: planner           # 引用 roles 中的 key
    inputs: [goal, ...]     # 输入产物流名称
    output: plan_doc        # 输出产物流名称
    skills: [dev-plan]      # 此 task 需要的 skill
    allowed_decisions: [done, fail, blocked]
    version_strategy: overwrite | increment

states:
  <name>:
    task: plan              # 引用 tasks 中的 key
    on: {done: review, fail: failed}
    default: failed
    gate: false             # true = 需外部 approve 才能继续

roles:
  <name>:
    agent: claude_plan      # 映射到 agents.yaml 中的 agent name
```

### 产物流版本策略

- `overwrite`（默认）：每次覆盖同名文件
- `increment`：自动递增版本号（如 `plan_doc-v1.md`, `plan_doc-v2.md`）
- `RunContext.artifact_versions` 保留完整版本链，`artifacts` 始终指向最新版

### 持久化存储

```
.agent-workflow/
  durable/                  # 持久化恢复数据（独立于单次 run）
    events/<id>.events.jsonl
    registry/<id>.artifacts.jsonl
    checkpoints/<id>.checkpoints.jsonl
  runs/<run_id>/
    staging/<state>/        # Agent 暂存输出
    artifacts/              # 正式产物流（promote 后）
    logs/events.jsonl       # 事件日志
    packets/                # worker 调试副本
    workflow_state.json     # RunContext 序列化
    cancelled               # 取消信号文件
```

### Agent 适配器

- **MockAgent**：不调用外部 CLI，生成 mock 输出。支持 `decision_script` 配置（按 state 访问次数返回不同 decision，演示状态机回流）
- **ClaudeCLI**：调用 Claude CLI (`claude`)，解析 stream-json 输出提取 token usage / session_id
- **CodexCLI**：调用 Codex CLI (`codex exec`)，解析 JSONL 输出提取 thread_id / usage
- **安全拦截**：`_assert_safe_permission()` 拒绝 `--dangerouslyDisableSandbox` / `--permission-mode bypass`；`_assert_safe_sandbox()` 白名单限制 Codex `--sandbox` 值

### Replacement Parity（与 legacy 对齐）

当前 root `agent-workflow` 正在替换 legacy `strategy.research.agent_workflow`。目标 parity 是 legacy 的 10 节点生命周期（`lineage_preflight → scaffold → plan → review → revise → human_approval → execute → code_audit → final_packet → summary`），而非当前 root 的 5 步 `DEFAULT_STEPS`。详细 parity contract 见 `docs/replacement-parity.md`。

## 测试约定

- 框架：pytest（`pyproject.toml` 中 `timeout = 300`）
- `tests/unit/` — 单元测试（每个模块一个文件）
- `tests/integration/` — 集成测试（完整 workflow 流程）
- `tests/soak/` — 长时间运行冒烟测试
- `tests/fixtures/schema_contract/` — schema 校验夹具（valid/invalid）
- 运行测试前需设置 `$env:PYTHONPATH='src;.'`
- 测试文件使用 `from agent_workflow.xxx import ...` 的 package import 形式（Phase 1 迁移中，部分 `tests/unit/` 测试仍使用 root-loose import，见 `docs/replacement-parity.md` §8）

## 关键约定

- TaskModel 禁止包含 transition / guard / retry / validator / provider / runtime 字段（v4 瘦模型）
- RoleModel 禁止包含 capability / policy / validator / contract / guard（Role 只是 agent alias）
- 所有 TaskResult 写入 staging 后必须经 `_validate_task_result()` 校验（含路径 containment 检查）
- promotion 前检查 staging 文件路径不逃逸 `run_root/staging/`，artifact 路径不逃逸 `run_root/artifacts/`
- `cancel` 通过写入 `cancelled` 标记文件实现，Runner 主循环每轮检查
- `retry` 默认 dry-run，必须显式 `--dispatch` 才真实执行
- 未知 decision → default transition → 通常到 `failed`（不会卡死）
