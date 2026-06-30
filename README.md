# Agent Workflow

通用 AI Agent 编排引擎。通过 **纯 YAML 配置** 驱动状态机，调度多个 AI Agent（Claude CLI、Codex CLI）按预定义工作流协作。支持可观测性、产物流管理、断点续跑。

> **说明**：Agent 的 provider 类型有 `claude`、`codex`、`mock`、`command` 四种。Claude CLI adapter 通过 `command` 配置可调用不同模型（如 Opus、DeepSeek、Haiku），它们在 provider 层面统一为 `claude`。

---

## 快速概览

| 维度 | 说明 |
|------|------|
| **做什么** | YAML 定义状态机 → 引擎调度 AI Agent 按工作流协作 → 产出自动物/日志/事件 |
| **核心价值** | 零代码编排、多 Agent 协作、产物可追溯、断点可续跑 |
| **适用场景** | 需求分析、方案设计、代码实现、代码审查、多模型交叉验证 |

### 命令速查

```bash
# 启动与运行
agent-workflow run        -w <workflow.yaml> -g "<目标>"    # 启动
agent-workflow continue   -r <run_id> -w <workflow.yaml>     # 从 Gate 恢复
agent-workflow cancel     -r <run_id>                        # 取消

# 状态查看
agent-workflow status     -r <run_id>       # 运行状态
agent-workflow explain    -r <run_id>       # 解释当前等待项
agent-workflow history    -r <run_id>       # 事件因果时间线
agent-workflow log        -r <run_id> --summary  # 汇总日志
agent-workflow tail       -r <run_id> -s <state>  # 节点日志

# 故障恢复
agent-workflow retry      -r <run_id> [--dispatch]   # 重试（默认 dry-run）

# 校验与测试
agent-workflow validate-config         -w <workflow.yaml>  # 校验配置
agent-workflow validate-state-machine  -w <workflow.yaml>  # 校验状态机
agent-workflow smoke      --agent <name>              # Agent 冒烟测试
```

### Claude Code 快捷命令速查

```text
/agent-workflow <workflow> [-t <topic>] <goal>    # 启动工作流
/agent-workflow status|explain|log|history <run_id>    # 查看运行
/agent-workflow tail <run_id> <state> [lines]     # 查看节点日志
/agent-workflow retry <run_id> [dispatch]          # 重试
/agent-workflow cancel <run_id> [reason]           # 取消
/agent-workflow continue <run_id> [approve]        # 从 Gate 恢复
/spec-wt -t <module> <goal>                        # Worktree 隔离并行开发
```

---

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 校验工作流
agent-workflow validate-state-machine -w workflows/plan-review-advise-execute-example/workflow.yaml

# 3. Mock 模式试跑（无需外部 CLI）
agent-workflow run \
  -w workflows/plan-review-advise-execute-example/workflow.yaml \
  -g "实现一个 hello world" \
  -p .

# 4. 查看结果
agent-workflow log -r <run_id> --summary
agent-workflow history -r <run_id>
```

---

## 设计原则

| 原则 | 说明 |
|------|------|
| **TaskResult 驱动** | 所有语义决策来自 Agent 输出的 `TaskResult`，Runner 只负责状态迁移。Agent 禁止直接指定下一状态 |
| **Agent 只写 staging** | Agent 输出先进入 `staging/` 暂存区，Validator 校验通过后 promotion 到正式 `artifacts/` |
| **Transition 必有 default** | 未知 decision 走 `default` 分支，不会卡死 |
| **Guard 机制** | `max_visits` / `max_duration_minutes` / `max_retries` 三重防护，防止失控循环 |
| **Observability 内置** | EventBus → ConsoleSink + JSONLSink + Heartbeat，所有关键事件统一分发 |

---

## CLI 命令参考

### 工作流生命周期

```bash
# 启动工作流
agent-workflow run -w <workflow.yaml> -g "<目标描述>" [-t <topic>] [-p <project_root>]

# 运行时覆盖 agent（无需修改 YAML）
# state: 优先级高于 task:，均高于 YAML 默认值
agent-workflow run -w <workflow.yaml> -g "<目标描述>" \
  --agent-map "task:review=cc-deepseek,state:review_r2=claude-haiku"

# 从 Gate 暂停状态恢复
agent-workflow continue -r <run_id> -w <workflow.yaml> --approve
agent-workflow continue -r <run_id> -w <workflow.yaml> --reject

# 注入人工澄清文件
agent-workflow continue -r <run_id> -w <workflow.yaml> --approve --input human_clarification.md

# 取消运行
agent-workflow cancel -r <run_id> --reason "..."
```

### 状态查看

```bash
# 查看运行状态
agent-workflow status -r <run_id>

# 解释当前等待项（当前在哪个 state、为什么等待、后续可能走向）
agent-workflow explain -r <run_id>

# 查看事件因果时间线（状态迁移 + TaskResult + promotion 链路）
agent-workflow history -r <run_id>

# 反查指定 state 的进入原因链
agent-workflow history -r <run_id> --why <state_name>

# 显示全部事件（包括心跳、输出行等细节）
agent-workflow history -r <run_id> --all

# 查看汇总日志
agent-workflow log -r <run_id> --summary

# 查看完整事件日志
agent-workflow log -r <run_id>

# 查看指定节点的输出（默认 80 行）
agent-workflow tail -r <run_id> -s <state> -n 80
```

### 运维与诊断

```bash
# 重试（默认 dry-run，仅预览不做实际执行）
agent-workflow retry -r <run_id>

# 从指定 state 开始重试
agent-workflow retry -r <run_id> --from-state <state>

# 真实执行重试
agent-workflow retry -r <run_id> --dispatch

# 重试时显式指定 workflow（用于自动发现 agents/skills）
agent-workflow retry -r <run_id> --dispatch -w <workflow.yaml>
```

**`retry` 诊断输出说明**：

dry-run 模式下会输出完整诊断信息和重试计划：
- `diagnose_last_failure` — 分析失败原因（`validator_block` / `guard_loop` / `guard_timeout` / `agent_crash`）
- `plan_rollback` — 规划回滚操作
- `plan_execution` — 规划重新执行的 state 序列
- `summary` — 汇总重试步骤

**retry 工作流程**：`retry`（默认 dry-run）→ 查看诊断 → 确认无误 → `retry --dispatch` 真实执行。dispatch 模式下会从 `workflow_state.json` 恢复 RunContext 和 `_workflow_snapshot` 快照，自动发现 workflow 文件并重建 Runner，从中断点继续执行。

```bash
# 1. 先预览（dry-run）
agent-workflow retry -r <run_id>

# 2. 查看诊断
agent-workflow history -r <run_id>
agent-workflow explain -r <run_id>

# 3. 确认后真实执行
agent-workflow retry -r <run_id> --dispatch
```

**`--agent-map` 工作原理**：

运行时覆盖 Agent 配置的字符串格式：`"state:状态名=agent名,task:任务名=agent名"`。

- **解析优先级**：`state:xxx`（每状态级别）> `task:xxx`（每任务级别）> YAML 默认值
- **格式校验（fail-fast）**：格式错误或引用不存在的 state/task/agent 时，直接拒绝启动
- **典型用法**：为不同 review 轮次指定不同模型

```bash
# 语法
agent-workflow run -w <workflow.yaml> -g "<目标>" \
  --agent-map "state:<state_name>=<agent>,task:<task_name>=<agent>"

# 示例：第一轮 review 用 DeepSeek，第二轮用 Haiku
agent-workflow run -w workflows/plan-review-advise-loop-example/workflow.yaml \
  -g "实现登录功能" \
  --agent-map "state:review_r1=cc-deepseek,state:review_r2=claude-haiku"
```

```bash
# 校验工作流配置
agent-workflow validate-config -w <workflow.yaml>

# 校验状态机完备性（检查 dead state、不可达路径等）
agent-workflow validate-state-machine -w <workflow.yaml>

# Agent 冒烟测试
agent-workflow smoke --agent <agent_name> [--agents <agents.yaml>]
```

---

## Claude Code 快捷命令

在 Claude Code 中通过 `/` 前缀触发，无需手动拼写完整 CLI 参数。

### `/agent-workflow` — 工作流生命周期

```text
# 启动工作流（最常用）
/agent-workflow <workflow> [-t <topic>] <goal...>

# 预览/校验工作流
/agent-workflow validate <workflow>

# 查看运行状态
/agent-workflow status <run_id>
/agent-workflow explain <run_id>

# 查看事件因果时间线
/agent-workflow history <run_id>
/agent-workflow history <run_id> <state>    # 反查 state 进入原因

# 查看日志
/agent-workflow log <run_id>
/agent-workflow tail <run_id> <state> [lines]

# 取消运行
/agent-workflow cancel <run_id> [reason]

# 重试（默认 dry-run，加 dispatch 真实执行）
/agent-workflow retry <run_id> [dispatch]

# 从 Gate 暂停恢复
/agent-workflow continue <run_id> [approve]
```

**示例**：

```text
/agent-workflow listing-dev 实现用户登录功能
/agent-workflow listing-dev -t add-login-page 实现用户登录功能
/agent-workflow spec-dev -t refactor-auth 重构权限验证模块
/agent-workflow validate spec-dev
/agent-workflow history 260626_listing-dev
/agent-workflow history 260626_listing-dev plan     # 反查 plan 状态为何被进入
/agent-workflow tail 260626_listing-dev plan 80
/agent-workflow retry 260626_listing-dev dispatch
```

### `/spec-wt` — Worktree 隔离并行开发

在**独立 git worktree** 中运行 `spec-dev` 工作流，实现多个并行开发的代码物理隔离——每个模块拥有独立工作目录和分支，互不覆盖。

```text
/spec-wt -t <module> [-b <branch>] <goal...>
```

| 参数 | 说明 |
|------|------|
| `-t <module>` | **必填**。模块名，用于 worktree 目录名、分支名、topic 命名 |
| `-b <branch>` | 可选。分支名，省略时默认 `feat/<module>` |
| `<goal>` | 要实现的目标描述 |

**示例**：

```text
/spec-wt -t alert-center 实现告警中心页面
/spec-wt -t audit-log -b feat/audit 实现审计日志查询
```

**机制**：在 `<repo>\..\aw-wt\<module>` 创建 worktree + `feat/<module>` 分支，`project_root` 指向 worktree、`run-root` 收口到主仓 `docs/runs/`。执行完成后展示 commit / merge / remove 指引（均需手动确认，不自动执行）。工作流失败时保留 worktree，支持从断点 `retry` 续跑。

**恢复**：模块名与对应 worktree/分支的映射持久化在 `docs/worktree_map.json`，会话丢失后凭 run 数据可找回。

---

## 核心功能详解

### 核心能力

- **YAML 驱动状态机**：无需写代码，一套 `workflow.yaml` 定义完整的 Agent 协作链路
- **独立工作目录与并行隔离**：`--project-root` 为每个 run 指定独立工作目录，Agent 的执行目录（`cwd`）随之解析；配合 git worktree 可让多个 run 在各自工作树中并行执行、代码改动互不覆盖，`--run-root` 可将产物统一收口到指定目录
- **多 Agent 编排**：支持 Claude CLI、Codex CLI 等多个 Agent 在同一工作流中分工协作。Claude CLI 可通过 `command` 配置切换不同模型（Opus、DeepSeek、Haiku 等）
- **TaskResult 契约**：标准化 JSON 输出，Agent 通过 `decision` 字段驱动状态迁移（如 `done`、`approve`、`revise`、`reject`）
- **Staging → Artifacts 两阶段**：Agent 输出先入暂存区，校验通过后才提升为正式产物流，保证产物可靠性
- **_loop 自动展开**：声明式循环块，引擎自动展开为 `_r1`/`_r2`/... 后缀的状态序列
- **版本管理**：`version_strategy: increment` 在同节点回流时自动生成 `-v1`/`-v2` 后缀，保留完整版本链
- **Guard 防护**：限制状态最大访问次数、最长运行时间、最大重试次数，防止死循环
- **Skill 系统**：每个 task 可挂载 skill（YAML/Markdown），Runner 自动加载并注入 Agent prompt
- **断点续跑**：`RunContext` 序列化到 `workflow_state.json`，中断后可从断点恢复

### 可观测性

**基础设施**：
- **EventBus** — 所有状态进入、TaskResult、promotion、错误等事件统一分发，支持多 sink 同时注册
- **ConsoleSink** — 终端实时输出，任务完成时展示耗时/token/agent 汇总表
- **JSONLSink** — 结构化事件日志写入 `events.jsonl`（每行一个 JSON 事件），每 50 条事件 flush 一次
- **Heartbeat** — 后台 daemon 线程每 30 秒发射心跳事件，支持 stale 检测（5 分钟阈值）

**查询接口**（具体用法见 [CLI 命令参考](#cli-命令参考)）：
- `status` — 当前 state、运行时长、心跳、产物列表
- `explain` — 为何停在此处、allowed_decisions、可能的后续走向、Guard 状态
- `history` — 事件因果时间线，支持 `--why` 反查指定 state 的进入原因链
- `log / tail` — 汇总日志 / 按节点查看最近 N 行输出

### 持久化存储

运行产物默认存放在 `{project_root}/docs/runs/<run_id>/`（可通过 `--run-root` 自定义）。`.agent-workflow/durable/` 存放跨 run 的持久化恢复数据。

```
{run_root}/                  # 默认: docs/runs/<run_id>/
  staging/<state>/           # Agent 暂存输出
  artifacts/                 # 正式产物流（promote 后）
  logs/events.jsonl          # 事件日志
  packets/                   # worker 调试副本
  workflow_state.json        # RunContext 序列化（含 _workflow_snapshot 快照）
  cancelled                  # 取消信号文件

.agent-workflow/
  durable/                   # 持久化恢复数据（独立于单次 run）
    events/<id>.events.jsonl
    registry/<id>.artifacts.jsonl
    checkpoints/<id>.checkpoints.jsonl
```

### Agent 适配器

- **MockAgent**：不调用外部 CLI，生成 mock 输出。支持 `decision_script` 配置（按 state 访问次数返回不同 decision，演示状态机回流）
- **ClaudeCLI**：调用 Claude CLI (`claude`)，解析 stream-json 输出提取 token usage / session_id。通过 `command` 配置可指定不同模型（如 Opus、DeepSeek、Haiku），provider 统一为 `claude`
- **CodexCLI**：调用 Codex CLI (`codex exec`)，解析 JSONL 输出提取 thread_id / usage
- **CommandAgent**：执行自定义 shell 命令。默认禁用（`enabled: false`），启用后命令需通过 `CommandValidator` 白名单安全检查，禁止 shell 元字符和危险操作
- **安全拦截**：`_assert_safe_permission()` 拒绝 `--dangerouslyDisableSandbox` / `--permission-mode bypass`；`_assert_safe_sandbox()` 白名单限制 Codex `--sandbox` 值

---

## Workflow 编排指南

> **本节是核心**：读完即可自主创建 Agent 工作流。一个完整的 workflow 包是纯 YAML 配置，引擎零改动。

### 快速理解

一个 workflow 本质上是 **一张有向图**：节点（state）执行任务（task），任务由 Agent 完成，Agent 输出的 `decision` 决定走哪条边到下一个节点。

```
plan ──done──▶ review ──approve──▶ execute ──done──▶ done
  ▲               │                    │
  │               │ revise             │ fail
  │               ▼                    ▼
  └── revise ── plan              failed
```

### Workflow 包目录结构

一个完整的 workflow 包包含以下文件：

```
workflows/<workflow-name>/
  workflow.yaml         # ★ 核心：状态机 + task 定义（必需）
  agents.yaml           # Agent 配置（真实运行时通过 --agents 指定）
  agents.real.yaml      # 真实 CLI agent 配置（避免被自动发现，mock 跑时用）
  mock_script.yaml      # Mock 模式的 decision 脚本（演示用）
  outputs.yaml          # 产物流声明（文档用途）
  skills/               # Skill 目录（workflow 引用的技能）
    <skill-name>/
      skill.yaml         # Skill 定义：规则 + 内容
```

---

### workflow.yaml 完整 Schema

#### 顶层字段

```yaml
name: <string>              # Workflow 名称（必需）
version: "1"                # 配置版本
description: |              # 描述
  ...

initial_state: <string>     # 初始状态名（必需，必须在 states 中定义）

terminal_states:            # 终止状态列表（至少一个）
  - done
  - failed
  - cancelled

guards:                     # 全局 Guard 配置（可选）
  max_visits: 5             #   每个状态最多被访问次数，0=不限制
  max_duration_minutes: 480 #   最长运行时间（分钟），0=不限制
  max_retries: 3            #   最大重试次数，0=不限制
  on_guard_failed: failed   #   Guard 触发后跳转的目标状态

required_skills:            # 全局必需的 skill 列表（可选）
  - agent-workflow-lifecycle

_loop:                      # 单循环块（可选，与 _loops 互斥）
  states: [review, advise]  #   循环体内的状态名列表
  repeat: 2                 #   重复次数
  on_break: execute         #   循环结束后的目标状态

_loops:                     # 多循环块（可选，与 _loop 互斥）
  - states: [plan_review, plan_refinement]
    repeat: 2
    on_break: execution
  - states: [output_review, output_refinement]
    repeat: 2
    on_break: validation

tasks:                      # Task 定义（必需）
  <task_name>: {...}

states:                     # State 定义（必需）
  <state_name>: {...}
```

#### tasks 字段详解

每个 task 定义一个"工作单元"——Agent 要做什么、输入什么、产出什么、允许什么决策。

```yaml
tasks:
  <task_name>:                  # Task 唯一标识（在 workflow 内唯一）
    instruction: |              # ★ Agent prompt（必需）
      对 Agent 的完整指令。应包含：
      - 任务目标和范围
      - 必须完成的步骤
      - 输出格式要求
      - 决策标准（什么情况输出什么 decision）

    agent: <agent_name>         # ★ Agent 名称（必需）
                                #   直接引用 agents.yaml 中的 agent name
                                #   不再通过 Role 间接寻址

    input:                      # 输入产物流列表（可选，单数）
                              #   YAML 中写 `input`，引擎加载后内部字段为 `inputs`
      - <artifact_name>         #   普通引用：当前最新版本
      - <artifact_name>:latest  #   显式引用最新版本
      - <artifact_name>:all     #   引用所有历史版本（用于回流节点）

    output: <string>            # 输出产物流名称（必需）
                                #   对应 artifacts/ 下的文件名前缀

    skills:                     # 此 task 需要的 skill（可选）
      - <skill_name>            #   引用的 skill 必须在 skills/ 目录存在

    allowed_decisions:          # 允许的 decision 值（必需）
      - done                    #   用于纯执行节点（只有成功/失败）
      - fail
      - blocked
      # 或用于决策节点：
      # - approve
      # - revise
      # - reject

    version_strategy: overwrite # 版本策略（可选，默认 overwrite）
                                #   overwrite: 每次覆盖同名文件
                                #   increment: 自动递增 -v1, -v2, ...

    timeout_seconds: 3600       # 超时秒数（可选，默认 3600）
    description: ""             # 描述（可选）
```

**instruction 编写要点**：

1. 明确告知 Agent 它的角色（计划者/审核者/执行者）
2. 列出必须包含的输出内容
3. 定义每种 `decision` 的使用场景
4. 若此节点可能回流（被 revise），应告知 Agent 关注上游产物的历史版本

**allowed_decisions 设计原则**：

- **纯执行节点**（plan、implement、summary）：用 `done / fail / blocked`
  - `done` → 任务完成，沿主链前进
  - `blocked` → 缺少信息或前置条件不满足
  - `fail` → 不可恢复的错误
- **审核/决策节点**（review、adoption、audit）：用 `approve / revise / reject`
  - `approve` → 通过，进入下游
  - `revise` → 需修改，回流到上游节点
  - `reject` → 不可接受，终止

**input 引用语法**：

| 写法 | 含义 | 场景 |
|------|------|------|
| `plan_doc` | 当前最新版本 | 正常前向引用 |
| `plan_doc:latest` | 等同于上者，显式语义 | 需要区分 latest/all 时 |
| `plan_doc:all` | 所有历史版本（逗号拼接） | 回流节点需要看到完整修改链 |

**version_strategy**：

- `overwrite`（默认）：每次执行覆盖 `artifacts/<output>.md`，适用于不回流的直线链路
- `increment`：每次执行生成新版本 `artifacts/<output>-v1.md`, `<output>-v2.md`...，`artifacts/<output>.md` 始终指向最新版。**回流节点必须用 increment**，否则历史版本会丢失

#### states 字段详解

每个 state 定义状态机的"节点"——执行哪个 task、每种 decision 跳转到哪。

```yaml
states:
  <state_name>:                 # State 唯一标识
    task: <task_name>           # ★ 关联的 task 名（必需，"" 表示无 task）
                                #   必须已在 tasks 中定义

    on:                         # ★ Decision → next_state 映射（必需）
      done: <next_state>        #   key 必须匹配 task 的 allowed_decisions
      fail: <next_state>
      blocked: <next_state>
      # 决策节点的典型映射：
      # approve: <next_state>
      # revise: <prev_state>    # 回流到上游
      # reject: failed

    default: <state_name>       # ★ 未知 decision 的默认跳转（必需）


    description: ""             # 描述（可选）
    gate: false                 # 是否为 Gate 状态（可选，默认 false）
                                #   true = 需外部 approve 才能继续
    terminal: true              # 仅终止状态使用（done/failed/cancelled）
```

**State 设计要求**：

1. **`on` 的 key 必须是 task 的 `allowed_decisions` 子集**（不必全部覆盖，未覆盖的走 `default`）
2. **每个非终止 state 必须定义 `default`**，防止未知 decision 卡死
3. **`task: ""` 只用于终止状态**（done/failed/cancelled），它们设 `terminal: true`
4. **回流边**：审核节点的 `revise` 应指回被审核节点的 state（如 `code_audit` 的 `revise → implement`）

#### 终止状态模板

```yaml
  done:
    task: ""
    terminal: true
    description: "工作流成功完成"

  failed:
    task: ""
    terminal: true
    description: "工作流失败终止"

  cancelled:
    task: ""
    terminal: true
    description: "工作流被取消"
```

---

### _loop / _loops：声明式循环块

> 引擎在加载时自动展开循环块并生成 `_r1`/`_r2`/... 后缀的状态名。你在 YAML 中只写 base 名，展开由引擎完成。

**单循环 `_loop`**：

```yaml
_loop:
  states: [review, advise]   # 循环体：两个 state 交替
  repeat: 2                  # 重复 2 轮
  on_break: execute          # 循环结束后的下一状态
```

展开结果（内部表示）：
```
review_r1 → advise_r1 → review_r2 → advise_r2 → execute
```

**多循环 `_loops`**（两段独立循环依次展开）：

```yaml
_loops:
  - states: [plan_review, plan_refinement]
    repeat: 2
    on_break: execution
  - states: [output_review, output_refinement]
    repeat: 2
    on_break: validation
```

展开结果：
```
plan_review_r1 → plan_refinement_r1 → plan_review_r2 → plan_refinement_r2
  → execution
  → output_review_r1 → output_refinement_r1 → output_review_r2 → output_refinement_r2
  → validation
```

**循环内的状态定义只写 base 名**，`on` 中的引用也用 base 名：

```yaml
states:
  review:                     # base 名，不是 review_r1
    task: review
    on:
      done: advise            # base 名，不是 advise_r1
      fail: failed
    default: failed

  advise:
    task: advise
    on:
      approve: execute        # 前 N-1 轮 approve → on_break（提前通过）
      revise: review          # revise → 下一轮 review（引擎自动处理）
      reject: failed
    default: failed
```

**循环机制要点**：
- 第 1~N-1 轮的 `done` 自动进入下一轮对应 state
- 任意轮的 `approve` 直接跳到 `on_break`（提前通过，跳过剩余轮次）
- 最后一轮不设 `revise` 分支（引擎自动移除），只能 approve 或 reject
- 需配合 `version_strategy: increment` 保留每轮产物

---

### agents.yaml：Agent 配置

```yaml
agents:
  <agent_name>:                     # Agent 唯一标识（被 task.agent 引用）
    provider: claude                # provider 类型: claude | codex | mock
    command: "{OPUS_COMMAND}"       # CLI 命令，{VAR} 从 .env 或环境变量读取
    cwd: "{project_root}"           # 工作目录占位符
    permission_mode: default        # Claude: default | acceptEdits | plan | auto
    allowed_tools: "Read,Grep,Glob,Write,Edit,Bash"  # 工具白名单（逗号分隔）
    sandbox: workspace-write        # Codex: workspace-write | workspace-read | none
    timeout_seconds: 3600           # 超时秒数
    description: "..."              # 描述
```

**provider 说明**：

| Provider | 适配的 CLI | sandbox 字段 | permission_mode 字段 |
|----------|-----------|-------------|---------------------|
| `claude` | Claude CLI (`claude`) | 不适用 | `default` / `acceptEdits` / `plan` / `auto` |
| `codex` | Codex CLI (`codex exec`) | `workspace-write` / `workspace-read` / `none` | 不适用 |
| `mock` | MockAgent（不调用外部 CLI） | 不适用 | 不适用 |
| `command` | CommandAgent（自定义 shell 命令） | 不适用 | 不适用 |

> **注意**：`command` provider 默认 `enabled: false`，需显式开启。执行的命令需通过 `CommandValidator` 白名单安全检查。

**Mock 模式**：当 agents.yaml 中找不到 task 引用的 agent 名时，自动 fallback 到 MockAgent。MockAgent 按 `mock_script.yaml` 中对应 state 的 decision 列表输出。

---

### skills/：Skill 系统

每个 skill 是一个独立的 `skills/<skill-name>/` 目录，包含 `skill.yaml`：

```yaml
# skills/<skill-name>/skill.yaml
skill:
  name: dev-plan                  # Skill 名称
  version: "1"
  description: "标准开发计划节点规范"
  required: false                 # true = 缺失时 fail-fast

  policy:                         # 策略约束
    allowed_decisions:            # 限制此节点可用的 decision
      - done
      - fail
      - blocked
    required_inputs:              # 必需的 artifact 输入
      - goal
      - project_context
    forbidden_actions:            # 禁止的操作（注入 Agent prompt）
      - "在计划阶段修改代码"
      - "跳过测试策略"

  content: |                      # ★ Skill 正文（Markdown，注入 Agent prompt）
    # Dev Plan Node

    计划节点只负责制定可执行开发计划，不修改代码。

    ## 必须包含
    - Goal / Non-goals
    - Scope and files
    - Implementation steps
    - Test strategy
    - Risks and stop rules
    - Expected artifacts

    ## 判断标准
    - `done`: 计划完整、可审核
    - `blocked`: 缺少关键上下文
    - `fail`: 目标不可行
```

**Skill 加载机制**：

1. `workflow.yaml` 的 `required_skills` 声明全局必需的 skill
2. `task.skills` 声明此 task 需要的 skill
3. Runner 启动时加载所有 skill，生成 `skill_adoption_<state>.md` 产物
4. Skill 正文注入 Agent prompt，作为行为约束

**Skill 文件格式**：支持 `.yaml`、`.yml`、`.md`（含 YAML frontmatter）三种格式。加载时按优先级搜索：`{name}/skill.yaml` → `{name}.yaml` → `{name}.md` → `{name}/SKILL.md`。

---

### mock_script.yaml：Mock 测试脚本

```yaml
decision_script:
  <state_name>:         # State 的 base 名（不含 _rN 后缀）
    - <decision_1>       # 第 1 次访问该 state 返回此 decision
    - <decision_2>       # 第 2 次访问返回此 decision
    # 列表耗尽后始终返回最后一个值
```

示例——演示回流：
```yaml
decision_script:
  plan:
    - done
  review:
    - advise         # 第 1 次：触发回流
    - approve        # 第 2 次：通过
  advise:
    - done
  execute:
    - done
```

预期链路：`plan → review(advise) → advise → plan → review(approve) → execute → done`

---

### outputs.yaml：产物流声明

```yaml
outputs:
  <artifact_name>:
    type: markdown            # 文件类型
    description: "..."        # 描述
    produced_by:              # 产生此产物的 task 列表
      - <task_name>
```

此文件为文档用途，引擎不强依赖。

---

### 完整编排教程

下面从头创建一个完整的 workflow。

#### Step 1：设计状态图

先在纸上画出状态跳转图。以"计划 → 审核 → 执行"为例：

```
plan ──done──▶ review ──approve──▶ execute ──done──▶ done
                 │                    │
                 │ revise             │ fail
                 ▼                    ▼
               plan                failed
```

#### Step 2：识别 task 和 decision

| State | Task | Agent | Decision |
|-------|------|-------|----------|
| plan | plan | cc-opus | done / fail / blocked |
| review | review | cc-deepseek | approve / revise / reject |
| execute | execute | codex | done / fail / blocked |

#### Step 3：编写 workflow.yaml

```yaml
name: my-first-workflow
version: "1"
description: |
  我的第一个 Agent 工作流：计划 → 审核 → 执行

initial_state: plan

guards:
  max_visits: 5
  max_duration_minutes: 120
  max_retries: 3
  on_guard_failed: failed

terminal_states:
  - done
  - failed
  - cancelled

required_skills:
  - agent-workflow-lifecycle

tasks:
  plan:
    instruction: |
      根据 goal 编写实现计划。计划必须包含:
      1. 目标和非目标
      2. 涉及文件和模块边界
      3. 分步骤实现方案
      4. 测试策略
      5. 风险和停止规则
      6. 预期产物
    agent: cc-opus
    input:
      - goal
      - project_context
    output: plan_doc
    skills:
      - dev-plan
    allowed_decisions:
      - done
      - fail
      - blocked

  review:
    instruction: |
      审核 plan_doc。只评价计划，不执行代码。
      必须识别 blocking 问题、主要风险、缺失测试和可简化点。

      决策:
      - approve: 计划可行，进入执行
      - revise: 计划需要回到 plan 重写
      - reject: 不可行，终止
    agent: cc-deepseek
    input:
      - plan_doc
      - project_context
    output: review_doc
    skills:
      - dev-review
    allowed_decisions:
      - approve
      - revise
      - reject

  execute:
    instruction: |
      按已批准的 plan_doc 执行代码变更。
      保持改动范围紧凑，遵守项目现有风格。
      记录实际修改文件、命令、偏差。
    agent: codex
    input:
      - plan_doc
      - project_context
    output: execution_report
    skills:
      - code-implementation
    allowed_decisions:
      - done
      - fail
      - blocked

states:
  plan:
    task: plan
    on:
      done: review
      fail: failed
      blocked: failed
    default: failed
    description: "编写实现计划"

  review:
    task: review
    on:
      approve: execute
      revise: plan        # 回流：回到 plan 重写
      reject: failed
    default: failed
    description: "审核实现计划"

  execute:
    task: execute
    on:
      done: done
      fail: failed
      blocked: failed
    default: failed
    description: "执行代码变更"

  done:
    task: ""
    terminal: true
    description: "工作流成功完成"

  failed:
    task: ""
    terminal: true
    description: "工作流失败终止"

  cancelled:
    task: ""
    terminal: true
    description: "工作流被取消"
```

#### Step 4：编写 agents.yaml

```yaml
agents:
  cc-opus:
    provider: claude
    command: "{OPUS_COMMAND}"
    cwd: "{project_root}"
    permission_mode: default
    allowed_tools: "Read,Grep,Glob,Write,Edit,Bash"
    timeout_seconds: 3600
    description: "Claude Opus — 计划"

  cc-deepseek:
    provider: claude
    command: "{DEEPSEEK_COMMAND}"
    cwd: "{project_root}"
    permission_mode: default
    allowed_tools: "Read,Grep,Glob,Write,Edit,Bash"
    timeout_seconds: 3600
    description: "Claude DeepSeek — 审核"

  codex:
    provider: codex
    command: "{CODEX_COMMAND}"
    cwd: "{project_root}"
    sandbox: workspace-write
    timeout_seconds: 7200
    description: "Codex — 执行"
```

#### Step 5：编写必要的 skills

至少需要 `agent-workflow-lifecycle`（全局必需）：

```yaml
# skills/agent-workflow-lifecycle/skill.yaml
skill:
  name: agent-workflow-lifecycle
  version: "1"
  required: true
  policy:
    required_inputs:
      - goal
      - project_context
    forbidden_actions:
      - "直接写正式 artifact（必须写 staging）"
      - "输出下一 state 名称"
      - "绕过 TaskResult schema"
  content: |
    # Agent Workflow Lifecycle
    你是 agent-workflow 编排中的一个节点。
    1. 所有产物只写入 prompt 提供的 staging 路径。
    2. 必须输出标准 TaskResult JSON。
    3. 只输出当前节点 decision，不输出下一 state 名称。
```

#### Step 6：编写 mock_script.yaml（可选）

```yaml
decision_script:
  plan:
    - done
  review:
    - approve
  execute:
    - done
```

#### Step 7：运行

```bash
# Mock 跑（无需外部 CLI）
agent-workflow run -w workflows/my-first-workflow/workflow.yaml -g "实现一个 hello world" -p .

# 真实跑
agent-workflow run \
  -w workflows/my-first-workflow/workflow.yaml \
  -g "实现一个 hello world" \
  -p . \
  --agents workflows/my-first-workflow/agents.yaml
```

---

### 常见编排模式

#### 模式 1：直线流水线（无回流）

```
A → B → C → D → done
```

适用：步骤确定、不需反复审核的简单流程。每个 task 用 `done / fail / blocked`，state 的 `on` 只有前向映射。

#### 模式 2：审核-回流

```
plan → review ──approve──▶ execute → done
  ▲       │
  └─revise─┘
```

核心：review 节点设 `approve / revise / reject`，`revise → plan` 形成回流环。plan 需设 `version_strategy: increment` 保留每版计划。

#### 模式 3：多轮审核（_loop 展开）

```yaml
_loop:
  states: [review, advise]
  repeat: 3
  on_break: execute
```

展开为 `review_r1 → advise_r1 → review_r2 → advise_r2 → review_r3 → advise_r3 → execute`。
- review 的 `done` → 对应 advise_rN
- advise 的 `approve` → 直接跳到 execute（提前通过）
- advise 的 `revise` → 下一轮 review_rN（继续修改）
- 最后一轮 advise 无 `revise`，只能 approve 或 reject

#### 模式 4：双循环（计划审核 + 结果审核）

```yaml
_loops:
  - states: [plan_review, plan_refinement]
    repeat: 2
    on_break: execution
  - states: [output_review, output_refinement]
    repeat: 2
    on_break: validation
```

先对计划进行 2 轮审核修订，执行后再对结果进行 2 轮审核修订，最后测试验证。参考 `workflows/plan-review-advise-loop-example/` 的 `_loop` 展开方式；需要双 `_loops` 时可按本模式扩展。

#### 模式 5：带 Gate 的审批流

```yaml
states:
  human_approval:
    task: ""
    gate: true              # 暂停循环，等待外部输入
    on:
      approve: execute
      reject: failed
    default: failed
```

Gate 状态暂停 Runner 主循环。CLI 使用 `agent-workflow continue` 恢复：

```bash
agent-workflow continue -r <run_id> -w <workflow.yaml> --approve
agent-workflow continue -r <run_id> -w <workflow.yaml> --reject
```

如需注入人工回答，可通过 `--input` 把 Markdown 文件登记为 `human_clarification` artifact：

```bash
agent-workflow continue -r <run_id> -w <workflow.yaml> --approve --input human_clarification.md
```

即使终端关闭，运行状态仍保存在 `workflow_state.json`，`continue` 会按 `run_index.json` / run 目录恢复。

#### 模式 6：纯需求理解 + 人工澄清

```yaml
states:
  generate_clarification_questions:
    on:
      done: human_clarification_gate

  human_clarification_gate:
    gate: true
    on:
      approve: final_requirement_synthesis
      reject: failed
    default: failed
```

适用于 `requirement-understanding` 这类需求理解工作流：先生成 `clarification_questions`，再暂停等待用户裁决；用户补充 `human_clarification.md` 后继续生成 `final_requirement`。

---

### 编排检查清单

编写完 workflow.yaml 后，确认以下各项：

- [ ] `initial_state` 在 `states` 中已定义
- [ ] 所有 `states.*.task` 引用的 task 在 `tasks` 中已定义
- [ ] 所有 `states.*.on` 的目标 state 在 `states` 中已定义
- [ ] 所有非终止 state 设置了 `default`
- [ ] 终止 state（done/failed/cancelled）设了 `terminal: true` 且 `task: ""`
- [ ] 每个 task 的 `allowed_decisions` 覆盖了对应 state 的 `on` key
- [ ] 回流节点的 task 用了 `version_strategy: increment`
- [ ] `_loop`/`_loops` 中引用的 state 以 base 名在 `states` 中定义
- [ ] `on_break` 目标 state 在 `states` 中已定义
- [ ] 所有 `task.agent` 在 `agents.yaml` 中有对应配置
- [ ] 所有 `task.skills` 和 `required_skills` 在 `skills/` 目录存在

可以用内置校验命令验证：
```bash
agent-workflow validate-state-machine -w <workflow.yaml>
```

---

## 产物流规范

### 目录结构

每次运行后 `{run_root}/`（默认 `docs/runs/<run_id>/`）下的核心目录：

```
{run_root}/                          # 默认 docs/runs/<run_id>/
  staging/                        ← Agent 原始输出暂存区（按 state 分目录）
    plan/
      output.md                   # Agent 的原始输出
      task_result.json            # TaskResult JSON
    review/
      output.md
      task_result.json
  artifacts/                      ← 正式产物流（扁平结构，无子目录）
    plan_doc.md
    review_doc.md
    adoption_doc.md
    skill_adoption_plan.md        # Skill 采纳记录
  packets/                        ← Agent worker 完整调试副本
    plan_claude_last_message.md
    review_claude_last_message.md
  logs/
    events.jsonl                  # 结构化事件日志
  workflow_state.json             # RunContext 序列化（断电续跑用）
  cancelled                       # 取消信号文件
```

### 命名规则

1. **artifacts 禁止子目录**：所有正式产物流直接放在 `artifacts/` 根下
2. **每个 task 的 `output` 取唯一名**：避免不同节点产出同名文件
3. **skill_adoption 文件命名**：`skill_adoption_<state>.md`，用下划线代替层级
4. **increment 版本后缀**：同节点回流时自动生成 `-v1`/`-v2` 后缀，`artifacts` 下保留完整链，引用的 `artifacts/<name>.md` 始终指向最新版

### TaskResult 契约

Agent 必须输出标准 JSON，核心字段：

```json
{
  "schema_version": 1,
  "task_id": "<uuid>",
  "state": "plan",
  "agent": "cc-opus",
  "status": "success",
  "decision": "done",
  "execution": {
    "started_at": "2026-01-01T00:00:00Z",
    "finished_at": "2026-01-01T00:05:00Z",
    "duration_seconds": 300,
    "attempt": 1,
    "exit_code": 0
  },
  "artifacts": [
    {
      "name": "plan_doc",
      "staging_path": "<run_root>/staging/plan/plan_doc.md",
      "artifact_path": "<run_root>/artifacts/plan_doc.md",
      "type": "markdown"
    }
  ],
  "session_id": "...",
  "token_usage": { "input": 1000, "output": 500 }
}
```

---

## 已有 Workflow 包

### 生产工作流（`/agent-workflow` 可直接使用）

| Workflow | 链路 | 说明 |
|----------|------|------|
| `listing-dev` | plan → review → implement → audit → summary | 标准开发链，覆盖完整 SDLC |
| `spec-dev` | planning → plan_review ⇄ plan_refinement → execution → output_review ⇄ output_refinement → validation → retrospective | 需求驱动开发，review/validation 节点用 approve/revise/reject 条件回流 |
| `req-analysis` | understand_requirements → review_breakdown → give_advice | 需求分析链（单向，不执行代码） |
| `requirement-understanding` | 三模型独立理解 → 三模型交叉审查 → 共识合并 → 澄清问题 → 人工裁决门 → 最终需求合成 | 纯需求理解，多模型独立解读、交叉审查、人工澄清恢复 |
| `system-architecture` | gather_context → extract_drivers → structure_constraints_objectives → draft_architecture → evaluation_gate → conflict_revision → architecture_freeze | 七层架构设计：上下文收集 → 驱动因素 → 约束目标 → 草案 → 评估门 → 冲突修订 → 冻结+ADR |
| `decision-collection` | collect_inputs → extract_decision_items → review_items → publish_to_lark_sheets → human_decision_gate → collect_sheets_results → synthesize_decision_package | 裁决收集链：收集 → 提取 → 审查 → 飞书发布 → 人工裁决 → 回收 → 合成裁决包 |

### 示例/学习工作流（Mock 模式可跑通）

| Workflow | 链路 | 说明 |
|----------|------|------|
| `standard-dev-example` | plan → review → adoption → implement → code_audit → unit_test → summary | `standard-dev` 的演示版，含双回流 |
| `plan-review-advise-loop-example` | plan → review(×2) → advise(×2) → execute → summary | `_loop` 展开两轮审核，含提前通过机制 |
| `plan-review-advise-execute-example` | plan → review → advise → execute | 通用四阶段链路，最小可用模板 |
| `software-dev-example` | plan → review_plan → revise_plan → execute → audit → revise_execute → summary | 独立 revise state 模式 |

### requirement-understanding 使用方式

`requirement-understanding` 只负责需求理解，不做 advice、不做方案设计、不推荐技术路线。它会产出共识需求、分歧需求、缺失信息、澄清问题，并在 `human_clarification_gate` 暂停等待用户回答。

启动：

```bash
agent-workflow run -w workflows/requirement-understanding/workflow.yaml -g "<产品运营需求>"
```

查看暂停状态：

```bash
agent-workflow status -r <run_id>
agent-workflow explain -r <run_id>
```

用户回答澄清问题后，保存为 `human_clarification.md`，再继续：

```bash
agent-workflow continue \
  -r <run_id> \
  -w workflows/requirement-understanding/workflow.yaml \
  --approve \
  --input human_clarification.md
```

如果用户裁决为不继续：

```bash
agent-workflow continue \
  -r <run_id> \
  -w workflows/requirement-understanding/workflow.yaml \
  --reject
```

恢复后会继续执行 `final_requirement_synthesis`，最终产物是 `final_requirement`，可作为后续 PRD 或 `spec-dev` 输入。

### 选择建议

```
需要改代码？
 ├── 是 → 需要多轮审核/条件回流？
 │        ├── 是 → spec-dev（plan/output 双审核循环 + Gate 人工确认）
 │        └── 否 → listing-dev（标准 SDLC：plan → review → implement → audit → summary）
 └── 否 → 需要执行代码变更？
          ├── 是 → req-analysis（理解需求、输出建议，不执行代码）
          └── 否 → 需要多模型共识？
                   ├── 是 → requirement-understanding（三模型理解 → 交叉审查 → Gate 澄清）
                   └── 否 → 需要架构设计？ → system-architecture
                         需要裁决收集？ → decision-collection
```

- **快速开始 / 学习编排**：从示例工作流 `plan-review-advise-execute-example` 开始，最简四阶段，Mock 模式零依赖
- **理解 _loop**：读 `plan-review-advise-loop-example`，单循环展开 + 提前通过
- **实际项目开发**：用 `listing-dev`，覆盖完整 SDLC（plan → review → implement → audit → summary）
- **需求驱动开发**：用 `spec-dev`，review/validation 节点条件回流 + Gate 人工确认
- **需求分析（不改代码）**：用 `req-analysis`，理解需求后输出建议，不执行变更
- **需求澄清（多模型共识）**：用 `requirement-understanding`，多模型独立理解后在 Gate 等待人工澄清
- **并行开发多模块**：用 `/spec-wt`，每个模块独立 worktree 隔离

---

## 安装与项目结构

### 安装

```bash
pip install -e .
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

### 项目结构

```
src/agent_workflow/
  cli.py                   # CLI 入口（12 个子命令）
  config/                  # YAML 配置模型 (TaskModel/StateModel/AgentModel/GuardModel/WorkflowConfig) 与加载器
  state_machine/           # StateMachine、Runner（主循环）、Transition、Guard、Retry
  tasks/                   # TaskResult（标准化 Agent 输出）、result_schema（JSON Schema 生成）
  agents/                  # Agent 适配器：BaseAgent → MockAgent / ClaudeCLI / CodexCLI
  artifacts/               # Staging 暂存、Promotion、Resolver
  skills/                  # Skill 模型、YAML/Markdown 加载、Adoption 协议、Policy 解析
  validators/              # TaskResult / Artifact / Repo / Command 校验器
  observability/           # EventBus、ConsoleSink、JSONLSink、Heartbeat、status、explain、history
  context/                 # RunContext（可序列化到 workflow_state.json，支持断点续跑）+ AgentInput
  state/                   # 状态持久化与锁
workflows/                 # Workflow 包（YAML 配置 + skills）
  listing-dev/              # 标准开发链
  spec-dev/                 # 需求驱动开发（条件回流）
  req-analysis/             # 需求分析链
  requirement-understanding/ # 纯需求理解（多模型→澄清→Gate）
  system-architecture/      # 系统架构设计
  decision-collection/      # 裁决收集
  standard-dev-example/     # 标准开发全链路示例
  plan-review-advise-loop-example/   # 两轮审核循环示例
  plan-review-advise-execute-example/ # 通用四阶段示例
  software-dev-example/     # 独立 revise state 示例
  .claude/commands/         # Claude Code 快捷命令定义
    agent-workflow.md       #   /agent-workflow 命令
    spec-wt.md              #   /spec-wt 命令
tests/                     # 测试
```

---

## 未来演进路线（2026–2028）

未来两年的演进围绕一个核心命题：**从"配置驱动的状态机"升级为"事件驱动的 Agent 操作系统"**。以下按优先级排列。

### 1. Event History（事件溯源）— 最高优先级

> 从"保存状态快照"升级到"保存事件流"，让整个 Runtime 可 Replay、可审计、可精准恢复。

当前 `workflow_state.json` 保存的是状态快照——断点续跑只能恢复到快照时刻，无法重现"中间发生了什么"。Event History 将每个状态迁移、TaskResult、promotion、guard 触发作为不可变事件追加写入，状态本身从事件流重建（Event Sourcing）。

**目标能力：**

- **完整审计追踪**：任意 run 的每一步决策、产物变更、异常都可追溯到具体事件
- **时间旅行调试**：从事件流重放到任意时刻，复现 Agent 行为和状态迁移
- **精准断点恢复**：不再依赖快照时间点，从事件流重建到中断前最后一刻
- **跨 run 分析**：基于事件流做 Agent 性能分析、决策质量统计、异常模式识别

**关键设计决策：**
- 事件不可变，只追加不修改
- `workflow_state.json` 从事件流派生（materialized view），不再作为 source of truth
- 事件 schema 向后兼容，支持版本演进

### 2. Artifact Registry（产物流注册中心）

> 让 Artifact 成为一等对象，而不是依赖固定文件名和目录。

当前 Artifact 通过约定路径（`artifacts/<name>.md`）引用，类型信息散落在 `outputs.yaml`、`task.output`、`TaskResult.artifacts` 中，缺乏统一的元数据模型和查询能力。

**目标能力：**

- **Artifact 元数据注册**：每个 artifact 有唯一 ID、类型、版本链、producer task、checksum、创建时间
- **版本语义**：支持 semver 式版本（major/minor/patch），不只是 `-v1`/`-v2` 后缀
- **依赖图**：artifact 之间的溯源关系（哪个 task 生产了它，哪些 task 消费了它）
- **内容寻址**：通过 checksum 去重和完整性校验
- **查询接口**：`registry.list(type="markdown", produced_by="plan")` 而非硬编码路径

**关键设计决策：**
- Artifact 元数据存储于 `durable/registry/`，独立于单次 run
- 文件路径从元数据派生，而非元数据从路径派生
- 与 Event History 打通：artifact promotion 作为事件记录

### 3. Policy Engine（可插拔策略引擎）

> 把 Validator 泛化为可插拔策略系统，覆盖质量、安全、成本、权限等维度。

当前 `validators/` 模块是硬编码的校验逻辑（TaskResult schema、artifact 路径 containment）。Policy Engine 将其抽象为声明式策略规则，按 scope（全局/workflow/task/state）组合，支持启用/禁用和自定义。

**目标能力：**

- **质量策略**：TaskResult schema 校验、artifact 完整性、输出格式合规
- **安全策略**：路径 containment、命令白名单、敏感信息泄露检测、sandbox 约束
- **成本策略**：token 预算上限、单 task 超时强制、run 级别花费告警
- **权限策略**：Agent 工具白名单、provider 约束、permission mode 强制
- **自定义策略**：用户通过 YAML 或 Python 插件注册自定义规则

**关键设计决策：**
- 策略定义沿用 YAML，与 workflow 配置一致的体验
- 策略评估结果分 `pass / warn / block` 三级，`warn` 不阻断但记录
- 支持 policy set 复用（多 workflow 共享同一套策略）

### 4. Goal → Workflow 动态生成

> 让 Workflow 本身可以由 Planner 动态生成，而不是完全静态 YAML。

当前 Workflow 完全由人工编写 YAML 定义，适合标准化流程但无法应对开放式的、需要动态规划的任务。Goal → Workflow 生成让一个 Planner Agent 根据目标描述自动生成 workflow 定义，再交由引擎执行。

**目标能力：**

- **意图解析**：输入自然语言目标，Planner 分解为 task 序列和决策分支
- **Workflow 合成**：动态生成合法的 workflow.yaml（含 state 图、task 定义、skill 匹配）
- **增量规划**：执行中遇到 blocked 时，Planner 动态插入新 state 或调整后续链路
- **模板实例化**：从预定义 workflow 模板库中选择并参数化
- **人工审批 Gate**：生成的 workflow 在关键分支设 Gate 暂停，由人工确认后继续

**关键设计决策：**
- 生成的 workflow 持久化为文件，可审计、可版本控制、可手动修正
- 动态生成不替代静态 YAML——标准化流程仍用静态配置，开放任务用动态生成
- Planner 本身也是 Agent（服从 TaskResult 契约），其输出是 workflow 定义

### 5. Knowledge Graph（知识图谱）

> 统一 Ledger、Evidence、Experiment、Memory，让 Agent 查询知识对象而不是文档。

当前 Skill 系统注入的是静态 Markdown 正文，Agent 缺乏对跨 run 积累的经验、实验结论、决策记录的查询能力。Knowledge Graph 将这些知识抽象为可查询的实体和关系。

**目标能力：**

- **Ledger（账本）**：跨 run 的决策记录——什么决策在什么上下文下做出了什么结果
- **Evidence（证据）**：产物流中的关键事实、度量数据、测试结果的语义索引
- **Experiment（实验）**：A/B 方案对比、技术选型分析的结构化记录
- **Memory（记忆）**：项目级和 org 级的经验积累，自动衰减和去重
- **语义查询**：Agent 在执行 task 时可以查询"类似场景下历史上做了什么决策、结果如何"

**关键设计决策：**
- 底层用图模型（实体 + 关系 + 属性），不依赖向量数据库
- 知识的写入由 Policy Engine 的策略触发（如"所有 `approve` 决策自动写入 Ledger"）
- 知识的查询通过 Agent 工具暴露（`query_knowledge("类似场景的决策记录")`），而不是注入 prompt
- 与 Event History、Artifact Registry 深度集成——事件和 artifact 是知识的主要来源

### 演进路线总览

```
2026 H2 ──── 2027 H1 ──── 2027 H2 ──── 2028 H1 ──── 2028 H2
  │            │            │            │            │
Event        Event       Artifact     Policy      Knowledge
History ◄──── History    Registry ◄─── Engine ◄──── Graph
  │          (完成)        │          (完成)        │
  │                       │                        │
  └─ Goal → Workflow ────┘                        │
         (与 Event History                       │
          并行启动)                               │
```

- **Event History** 是基础——所有后续能力依赖事件流作为数据来源
- **Artifact Registry** 和 **Goal → Workflow** 可并行推进
- **Policy Engine** 依赖 Registry 提供 artifact 元数据
- **Knowledge Graph** 是最终形态——消费 Event History、Artifact Registry 和 Policy Engine 的输出，构建跨 run 的语义层
