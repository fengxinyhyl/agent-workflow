# Agent Workflow Core

通用 Agent 编排引擎。调度多个 AI Agent（Claude、Codex、DeepSeek）按预定义工作流协作，支持长任务运行、可观测性、产物流管理。

## 设计原则

- **TaskResult 驱动**：所有语义决策来自 Agent 输出的 TaskResult，Runner 只负责状态迁移
- **Agent 只写 staging**：Agent 输出先进入 staging 区，Validator 通过后 promotion 到正式 artifacts
- **Transition 有 default**：未知 decision 走 default 分支，不会卡死
- **Guard 机制**：max_visits / max_duration_minutes / max_retries 防止失控
- **Observability 内置**：EventBus + ConsoleSink + JSONL Sink + Heartbeat

## 产物流规范

### 目录结构

每次运行后 `.agent-workflow/runs/<run_id>/` 下的三个核心目录：

| 目录 | 用途 | 生命周期 |
|------|------|----------|
| `staging/` | Agent 原始输出暂存区，按 state 分目录（如 `staging/plan/`） | 校验通过后保留（用于排查） |
| `artifacts/` | 校验通过后的正式产物流，**扁平结构**，所有产出物直接放在根下 | 下游节点消费 |
| `packets/` | Agent worker 的完整调试副本（stream-json、assistant message） | 排查用 |

```
.agent-workflow/runs/<run_id>/
  staging/
    plan/                        ← 按 state 分目录（Agent 只写这里）
      output.md
      task_result.json
    review/
      output.md
      task_result.json
  artifacts/
    plan_doc.md                  ← 扁平结构，无子目录
    review_doc.md
    skill_adoption_plan.md
    skill_adoption_review.md
  packets/
    plan_claude_last_message.md
    review_claude_last_message.md
```

### 命名规则

1. **artifacts 目录禁止子目录**：所有正式产物流直接放在 `artifacts/` 根下，不创建 `artifacts/plan/` 等节点子目录
2. **每个 task 的 `output` 字段取唯一名**：避免不同节点产出同名文件。如 plan task 用 `output: plan_doc`，review task 用 `output: review_doc`
3. **skill_adoption 文件命名**：`skill_adoption_<state>.md`，用下划线代替层级
4. **版本策略**：`version_strategy: increment` 在同一节点回流（loop/retry）时自动生成 `-v1`、`-v2` 后缀，解决同一节点多次产出的版本管理

### Agent 契约

Agent 在 TaskResult 中登记产物流时，`artifact_path` 必须遵循扁平规则：

```json
{
  "artifacts": [
    {
      "name": "plan_doc",
      "staging_path": "<run_root>/staging/plan/plan_doc.md",
      "artifact_path": "<run_root>/artifacts/plan_doc.md",
      "type": "markdown"
    }
  ]
}
```

禁止在 `artifact_path` 中包含子目录（如 ~~`artifacts/plan/plan_doc.md`~~）。

## 安装

```bash
pip install -e .
```

## CLI

```bash
agent-workflow validate-config
agent-workflow validate-state-machine
agent-workflow smoke --agent claude_review
agent-workflow run --workflow examples/software-dev/workflow.yaml --goal "..."
agent-workflow status --run-id run_001
agent-workflow explain --run-id run_001
agent-workflow log --run-id run_001 --summary
agent-workflow cancel --run-id run_001 --reason "..."
```

## 项目结构

```
src/agent_workflow/
  cli.py                  # CLI 入口
  context/                # RunContext + AgentInput
  config/                 # YAML 配置模型与加载器
  state_machine/          # 状态机、Runner、Transition、Guard、Retry
  tasks/                  # Task 模型、TaskResult
  roles/                  # Role → Agent 解析
  agents/                 # Agent 适配器（mock/claude/codex/command）
  artifacts/              # Staging / Promotion / Resolver
  skills/                 # Skill 模型、加载、Adoption
  validators/             # TaskResult / Artifact / Repo / Command 校验器
  observability/          # EventBus、ConsoleSink、JSONLSink、Heartbeat、status、explain
  state/                  # 状态持久化与锁
examples/software-dev/    # software-dev 示例 Pack
tests/                    # 测试
```
