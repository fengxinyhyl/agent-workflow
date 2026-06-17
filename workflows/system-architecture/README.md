# system-architecture

根据已澄清需求形成系统架构的工作流。它承接 `requirement-understanding` 产出的 `final_requirement`，也可以在只有 `goal + project_context` 时单独运行，但会把未澄清需求标记为风险。

本工作流做系统架构，不写代码，不替代需求澄清，也不输出详细开发排期。

## 流程

```text
final_requirement + goal + project_context
  ↓
extract_drivers
  ↓
structure_constraints_objectives
  ↓
draft_architecture
  ↓
evaluation_gate
  ├─ approve → architecture_freeze → done
  ├─ revise  → conflict_revision → evaluation_gate
  └─ reject  → failed
```

## 六层定义

| Layer | State | Output | 工程化定义 |
|-------|-------|--------|------------|
| Drivers | `extract_drivers` | `architecture_drivers` | 输出业务目标、技术目标和必须显式化的隐含假设 |
| Constraints & Objectives | `structure_constraints_objectives` | `constraints_objectives` | 拆分 hard constraints、soft constraints 和 optimization function |
| Draft Architecture | `draft_architecture` | `architecture_draft` | 每个组件必须写 `Component → Responsibility → Driver Mapping → Constraint Coverage` |
| Evaluation Gate | `evaluation_gate` | `evaluation_report` | 输出评分矩阵、blocker list、violation list 和 trade-off conflict list |
| Conflict-driven Revision | `conflict_revision` | `conflict_revision_doc` | 只基于 violation / conflict 修订，并显式标记 constraint relaxation 审批 |
| Architecture Freeze + ADR | `architecture_freeze` | `final_architecture` | 冻结架构并输出 ADR，包含可逆成本 |

## 运行

```powershell
$env:PYTHONPATH='src;.'
python -m agent_workflow.cli run `
  -w workflows\system-architecture\workflow.yaml `
  -g "<已澄清需求或架构目标>"
```

真实运行时会自动发现同目录的 `agents.yaml`、`skills/` 和 `mock_script.yaml`。如果要强制指定：

```powershell
python -m agent_workflow.cli run `
  -w workflows\system-architecture\workflow.yaml `
  -g "<已澄清需求或架构目标>" `
  --agents workflows\system-architecture\agents.yaml `
  --skills-dir workflows\system-architecture\skills
```

## 与需求理解工作流衔接

推荐先运行 `requirement-understanding`，得到 `final_requirement` 后，再把它作为本工作流的输入上下文。当前 CLI 的 artifact 注入方式可通过项目上下文或目标文本传入，也可以在后续编排中把 `final_requirement` 显式挂到同一个 run context。

## 主要产物

| Artifact | 来源节点 | 作用 |
|----------|----------|------|
| `architecture_drivers` | `extract_drivers` | 业务目标、技术目标、关键用例、数据/集成驱动因素和隐含假设 |
| `constraints_objectives` | `structure_constraints_objectives` | hard constraints、soft constraints、optimization function 和验证方式 |
| `architecture_draft` | `draft_architecture` | 逐条对齐 driver 和 constraint 的系统架构草案 |
| `evaluation_report` | `evaluation_gate` | 评分矩阵、blocker、violation、trade-off conflict 和 residual risks |
| `conflict_revision_doc` | `conflict_revision` | conflict resolution strategy、完整修订后架构和 constraint relaxation 请求 |
| `final_architecture` | `architecture_freeze` | Architecture Freeze + ADR |

## 边界

- 可以输出架构策略、组件划分、数据流、接口契约、技术选型和 ADR。
- 不写代码，不创建开发任务，不替用户裁决未确认需求。
- Evaluation Gate 只评估，不修改架构产物。
- Conflict-driven Revision 只能基于 violation list 和 trade-off conflict list 修订。
- Constraint relaxation 必须显式标记为需要审批，不能由节点自行批准。

## 验证

```powershell
$env:PYTHONPATH='src;.'
pytest tests\integration\test_system_architecture_flow.py -q
python -m agent_workflow.cli validate-config -w workflows\system-architecture\workflow.yaml
python -m agent_workflow.cli validate-state-machine -w workflows\system-architecture\workflow.yaml
```
