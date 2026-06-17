# Agent Workflow

通用 AI Agent 编排引擎。通过 **纯 YAML 配置** 驱动状态机，调度多个 AI Agent（Claude、Codex、DeepSeek）按预定义工作流协作。支持长任务运行、可观测性、产物流管理、断点续跑。

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

## 主要功能

### 核心能力

- **YAML 驱动状态机**：无需写代码，一套 `workflow.yaml` 定义完整的 Agent 协作链路
- **多 Agent 编排**：支持 Claude CLI、Codex CLI、DeepSeek 等多个 Agent 在同一工作流中分工协作
- **TaskResult 契约**：标准化 JSON 输出，Agent 通过 `decision` 字段驱动状态迁移（如 `done`、`approve`、`revise`、`reject`）
- **Staging → Artifacts 两阶段**：Agent 输出先入暂存区，校验通过后才提升为正式产物流，保证产物可靠性
- **_loop 自动展开**：声明式循环块，引擎自动展开为 `_r1`/`_r2`/... 后缀的状态序列
- **版本管理**：`version_strategy: increment` 在同节点回流时自动生成 `-v1`/`-v2` 后缀，保留完整版本链
- **Guard 防护**：限制状态最大访问次数、最长运行时间、最大重试次数，防止死循环
- **Skill 系统**：每个 task 可挂载 skill（YAML/Markdown），Runner 自动加载并注入 Agent prompt
- **断点续跑**：`RunContext` 序列化到 `workflow_state.json`，中断后可从断点恢复

### 可观测性

- **EventBus** — 所有状态进入、TaskResult、promotion、错误等事件统一分发
- **ConsoleSink** — 终端实时输出
- **JSONLSink** — 结构化事件日志（`logs/events.jsonl`）
- **Heartbeat** — 长时间运行心跳
- **status / explain** — 查看运行状态、解释当前等待项
- **log / tail** — 查看运行日志、按节点查看输出

### CLI 命令

```bash
agent-workflow validate-config        # 校验工作流配置
agent-workflow validate-state-machine # 校验状态机完备性
agent-workflow smoke --agent <name>   # Agent/Role 冒烟测试
agent-workflow run -w <workflow.yaml> -g "<目标>"  # 启动工作流
agent-workflow run -w <workflow.yaml> -g "<目标>" \
  --agent-map "task:review=cc-deepseek,state:review_r2=claude-haiku"  # 运行时覆盖 agent
agent-workflow status -r <run_id>     # 查看运行状态
agent-workflow explain -r <run_id>    # 解释当前等待项
agent-workflow log -r <run_id> --summary  # 查看汇总日志
agent-workflow tail -r <run_id> -s <state> # 查看节点日志
agent-workflow cancel -r <run_id>     # 取消运行
agent-workflow retry -r <run_id> [--dispatch]  # 重试
```

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

    input:                      # 输入产物流列表（可选）
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

Gate 状态暂停 Runner 主循环，由外部（如人工操作者）调用 `continue_from_gate()` 注入 decision 后继续。

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

每次运行后 `.agent-workflow/runs/<run_id>/` 下的三个核心目录：

```
.agent-workflow/runs/<run_id>/
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

| Workflow | 状态数 | 链路 | 特点 |
|----------|--------|------|------|
| `standard-dev` | 10 | plan → review → adoption → implement → code_audit → unit_test → summary | 标准开发全链路，含双回流（计划审核+代码审核） |
| `spec-dev` | 11 | planning → plan_review ⇄ plan_refinement → execution → output_review ⇄ output_refinement → validation → retrospective | 需求驱动，review/test 节点用 approve/revise/reject 条件回流 |
| `plan-review-advise-loop` | 7 | plan → review(×2) → advise(×2) → execute → summary | `_loop` 展开两轮审核，含提前通过机制 |
| `plan-review-advise-execute` | 6 | plan → review → advise → execute | 通用四阶段链路，最小可用模板 |
| `software-dev` | 10 | plan → review_plan → revise_plan → execute → audit → revise_execute → summary | P0 示例，独立 revise state 模式 |

### 选择建议

- **快速开始 / 学习编排**：从 `plan-review-advise-execute` 开始，最简四阶段
- **理解 _loop**：读 `plan-review-advise-loop`，单循环展开
- **理解条件回流开发流**：读 `spec-dev`，review/test 节点用 approve/revise/reject 驱动修订
- **实际项目使用**：用 `standard-dev`，覆盖完整 SDLC

---

## 安装

```bash
pip install -e .
```

## 项目结构

```
src/agent_workflow/
  cli.py                   # CLI 入口（11 个子命令）
  config/                  # YAML 配置模型 (TaskModel/StateModel/AgentModel/GuardModel/WorkflowConfig) 与加载器
  state_machine/           # StateMachine、Runner（主循环）、Transition、Guard、Retry
  tasks/                   # TaskResult（标准化 Agent 输出）、result_schema（JSON Schema 生成）
  agents/                  # Agent 适配器：BaseAgent → MockAgent / ClaudeCLI / CodexCLI
  artifacts/               # Staging 暂存、Promotion、Resolver
  skills/                  # Skill 模型、YAML/Markdown 加载、Adoption 协议、Policy 解析
  validators/              # TaskResult / Artifact / Repo / Command 校验器
  observability/           # EventBus、ConsoleSink、JSONLSink、Heartbeat、status、explain
  context/                 # RunContext（可序列化到 workflow_state.json，支持断点续跑）+ AgentInput
  state/                   # 状态持久化与锁
  long_task/               # 长任务：WorkflowRun / WorkItem / DependencyGraph / EventLog / StateStore
workflows/                 # Workflow 包（YAML 配置 + skills）
  standard-dev/            # 标准开发全链路
  spec-dev/                # 需求驱动开发（条件回流）
  plan-review-advise-loop/ # 两轮审核循环
  plan-review-advise-execute/ # 通用四阶段
  software-dev/            # Plan → Review → Revise → Execute → Audit → Summary
tests/                     # 测试
```
