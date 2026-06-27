# Skill Adoption: plan_refinement

采纳时间: plan_refinement

## 已加载 Skills

- **agent-workflow-lifecycle**: Agent Workflow 生命周期规则
- **review-adoption**: 审核意见采纳节点规范

## 策略约束

- agent-workflow-lifecycle: forbidden_actions = ['直接写正式 artifact（必须写 staging）', '输出下一 state 名称', '绕过 TaskResult schema', '读取 .env 或密钥文件']
- review-adoption: allowed_decisions = ['done', 'fail', 'blocked']
- review-adoption: forbidden_actions = ['在采纳阶段修改代码', '无记录地忽略 blocking review item']