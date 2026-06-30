# Skill Adoption: plan_review

采纳时间: plan_review

## 已加载 Skills

- **agent-workflow-lifecycle**: Agent Workflow 生命周期规则
- **dev-review**: 开发计划审核节点规范

## 策略约束

- agent-workflow-lifecycle: forbidden_actions = ['直接写正式 artifact（必须写 staging）', '输出下一 state 名称', '绕过 TaskResult schema', '读取 .env 或密钥文件']
- dev-review: allowed_decisions = ['approve', 'revise', 'reject', 'fail', 'blocked']
- dev-review: forbidden_actions = ['在审核阶段修改代码', '把审核意见直接当成最终采纳决定']