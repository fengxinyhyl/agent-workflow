# Skill Adoption: planning

采纳时间: planning

## 已加载 Skills

- **agent-workflow-lifecycle**: Agent Workflow 生命周期规则
- **dev-plan**: 标准开发计划节点规范

## 策略约束

- agent-workflow-lifecycle: forbidden_actions = ['直接写正式 artifact（必须写 staging）', '输出下一 state 名称', '绕过 TaskResult schema', '读取 .env 或密钥文件']
- dev-plan: allowed_decisions = ['done', 'fail', 'blocked']
- dev-plan: forbidden_actions = ['在计划阶段修改代码', '跳过测试策略']