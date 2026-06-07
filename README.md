# Agent Workflow Core

通用 Agent 编排引擎。调度多个 AI Agent（Claude、Codex、DeepSeek）按预定义工作流协作，支持长任务运行、可观测性、产物流管理。

## 设计原则

- **TaskResult 驱动**：所有语义决策来自 Agent 输出的 TaskResult，Runner 只负责状态迁移
- **Agent 只写 staging**：Agent 输出先进入 staging 区，Validator 通过后 promotion 到正式 artifacts
- **Transition 有 default**：未知 decision 走 default 分支，不会卡死
- **Guard 机制**：max_visits / max_duration_minutes / max_retries 防止失控
- **Observability 内置**：EventBus + ConsoleSink + JSONL Sink + Heartbeat

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
