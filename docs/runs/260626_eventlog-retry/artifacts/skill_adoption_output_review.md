# Skill Adoption: output_review

采纳时间: output_review

## 已加载 Skills

- **agent-workflow-lifecycle**: Agent Workflow 生命周期规则
- **code-audit**: 代码检查节点规范

## 策略约束

- agent-workflow-lifecycle: forbidden_actions = ['直接写正式 artifact（必须写 staging）', '输出下一 state 名称', '绕过 TaskResult schema', '读取 .env 或密钥文件']
- code-audit: allowed_decisions = ['approve', 'revise', 'reject', 'fail', 'blocked']
- code-audit: forbidden_actions = ['在检查阶段直接修代码', '只给总结不列具体风险']