# Skill Adoption: output_refinement

采纳时间: output_refinement

## 已加载 Skills

- **agent-workflow-lifecycle**: Agent Workflow 生命周期规则
- **code-implementation**: 编程执行节点规范

## 策略约束

- agent-workflow-lifecycle: forbidden_actions = ['直接写正式 artifact（必须写 staging）', '输出下一 state 名称', '绕过 TaskResult schema', '读取 .env 或密钥文件']
- code-implementation: allowed_decisions = ['done', 'fail', 'blocked']
- code-implementation: forbidden_actions = ['扩大到计划外重构', '删除用户未授权文件', '跳过记录实际修改']