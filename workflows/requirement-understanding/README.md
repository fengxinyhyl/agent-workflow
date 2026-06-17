# requirement-understanding

纯需求理解工作流。它用于把原始产品/运营需求收敛成可追踪、可澄清、可交给 PRD 或 `spec-dev` 的 `final_requirement`。

本工作流不做 `advice`，不做方案设计，不推荐技术路线。

## 流程

```text
goal + project_context
  ↓
understand_deepseek
  ↓
understand_claude
  ↓
understand_codex
  ↓
review_by_claude
  ↓
review_by_codex
  ↓
review_by_deepseek
  ↓
combine_consensus
  ↓
generate_clarification_questions
  ↓
human_clarification_gate
  ↓ approve + human_clarification
final_requirement_synthesis
  ↓
done
```

## 运行

```powershell
$env:PYTHONPATH='src;.'
python -m agent_workflow.cli run `
  -w workflows\requirement-understanding\workflow.yaml `
  -g "<产品运营需求>"
```

真实运行时会自动发现同目录的 `agents.yaml`、`skills/` 和 `mock_script.yaml`。如果要强制指定：

```powershell
python -m agent_workflow.cli run `
  -w workflows\requirement-understanding\workflow.yaml `
  -g "<产品运营需求>" `
  --agents workflows\requirement-understanding\agents.yaml `
  --skills-dir workflows\requirement-understanding\skills
```

## 暂停与恢复

工作流执行到 `human_clarification_gate` 后会暂停，状态保存在 `workflow_state.json`。终端关闭后也可以恢复。

查看状态：

```powershell
python -m agent_workflow.cli status -r <run_id>
python -m agent_workflow.cli explain -r <run_id>
```

根据 `clarification_questions` 写一份人工澄清文件，例如 `human_clarification.md`：

```markdown
# Human Clarification

## Blocking Questions

1. 目标用户确认：运营人员。
2. 主要业务目标：提升活动转化率。

## Divergence Decisions

1. 数据看板先只覆盖活动级指标，不覆盖用户级权限。
```

批准并继续：

```powershell
python -m agent_workflow.cli continue `
  -r <run_id> `
  -w workflows\requirement-understanding\workflow.yaml `
  --approve `
  --input human_clarification.md
```

拒绝并结束到 `failed`：

```powershell
python -m agent_workflow.cli continue `
  -r <run_id> `
  -w workflows\requirement-understanding\workflow.yaml `
  --reject
```

## 主要产物

| Artifact | 来源节点 | 作用 |
|----------|----------|------|
| `understanding_deepseek` | `understand_deepseek` | DeepSeek 独立需求理解 |
| `understanding_claude` | `understand_claude` | Claude 独立需求理解 |
| `understanding_codex` | `understand_codex` | Codex 独立需求理解 |
| `review_claude` | `review_by_claude` | Claude 交叉审查 DeepSeek 的理解 |
| `review_codex` | `review_by_codex` | Codex 交叉审查 Claude 的理解 |
| `review_deepseek` | `review_by_deepseek` | DeepSeek 交叉审查 Codex 的理解 |
| `consensus_report` | `combine_consensus` | 共识需求、分歧需求、缺失信息和共识度 |
| `clarification_questions` | `generate_clarification_questions` | 面向用户的澄清问题 |
| `human_clarification_request` | `human_clarification_gate` | 暂停前的人工裁决请求 |
| `human_clarification` | `continue --input` | 用户回答的澄清信息 |
| `final_requirement` | `final_requirement_synthesis` | 最终需求理解产物 |

## 边界

- 只理解需求，不输出技术选型、架构建议或实现计划。
- 共识度只是中间指标，不代表需求自动通过。
- 分歧项必须由用户澄清或保留为未确认事项。
- `final_requirement` 可作为后续 PRD 或 `spec-dev` 输入。

## 验证

```powershell
$env:PYTHONPATH='src;.'
pytest tests\integration\test_requirement_understanding_flow.py -q
python -m agent_workflow.cli validate-config -w workflows\requirement-understanding\workflow.yaml
python -m agent_workflow.cli validate-state-machine -w workflows\requirement-understanding\workflow.yaml
```
