# Skill Adoption: codex_plan

采纳时间: codex_plan

## 已加载 Skills

- **agent-workflow-lifecycle**: Agent Workflow 生命周期规则 — 规范 Agent 在编排下的行为

## 策略约束

- agent-workflow-lifecycle: forbidden_actions = ['直接写正式 artifact（必须写 staging）', '输出下一 state 名称', '绕过 TaskResult schema']