---
name: workflow-architecture
description: Use when creating or optimizing agent-workflow workflow packages, including requirement understanding, system architecture convergence, research/evidence, conditional development, review/gate, artifact contracts, decision semantics, agent assignment, permissions, and validation.
---

# Workflow Architecture

用于创建或优化 `agent-workflow` 工作流包。目标是让工作流能稳定流转、产物可追溯、节点职责清晰，而不是堆叠更多节点。

## 目录位置

- 自动触发入口和主维护位置是 `.claude/skills/workflow-architecture/SKILL.md`。
- `.agents/skills` 使用目录链接指向 `.claude/skills`，不要复制第二份 skill。
- 根目录 `skills/` 不是当前自动触发入口，不要把它作为唯一真相源。
- 若某个 `workflows/<name>/` 运行时节点也要使用这套规则，可以显式传入 `--skills-dir .claude/skills`，或把需要的内容落成该 workflow 包自己的 `skills/<skill-name>/skill.yaml`。

## Workflow Architect 八步协议

设计或优化工作流时，按以下顺序输出和落地。

### Step1 任务分类

先判断任务属于哪类：需求理解、信息收集、研究分析、方案设计、代码开发、内容创作、数据分析、决策支持、质量审核、其它。说明判断理由。

### Step2 失败风险分析

分析如果直接让单个模型执行，可能出现哪些失败模式。

输出格式：

```text
Failure Modes:
- 理解错误
- 信息遗漏
- 推理错误
- 幻觉
- 方案偏见
- 测试不足
- 验证不足
- 数据污染
- 过早收敛
```

只保留和当前任务真实相关的风险。

### Step3 工作流目标

明确工作流优先优化什么，并排序：提高理解准确率、提高研究深度、提高方案质量、降低幻觉、降低遗漏、提高可解释性、提高稳定性、提高执行成功率。

### Step3.5 Pattern Selection

不要默认套用通用 `review -> refinement -> review`。先根据任务类型、失败模式和收敛目标选择 workflow pattern，再设计节点。

选择规则：

- 核心风险是需求误解、信息遗漏、用户意图不清：使用 Requirement Understanding Pattern。
- 核心风险是方案空间过大、约束冲突、架构决策不可逆：使用 Architecture Convergence Pattern。
- 核心风险是实现偏离计划、代码质量、测试不足：使用 Conditional Development Pattern。
- 核心风险是事实错误、证据不足、来源污染：使用 Research / Evidence Pattern。
- 核心风险是评审标准不明确、主观偏好过强：使用 Rubric Evaluation Pattern。
- 需要人工裁决关键分歧：加入 Human Gate。
- 风险低且流程天然线性：使用 Simple Linear Pattern。

如果一个任务同时命中多个模式，保留最小必要组合。例如：需求到架构通常是 Requirement Understanding 的下游输入 + Architecture Convergence，而不是把澄清问题混进架构冻结节点。

### Step4 节点设计

为每个目标设计最小必要节点。每个节点必须写明：

```text
Node Name:
Purpose:
Input:
Output:
Success Criteria:
```

禁止增加没有明确价值的节点。

### Step5 Artifact 设计

分析哪些中间结果值得保存，例如 Requirement Artifact、Research Artifact、Evidence Artifact、Review Artifact、Decision Artifact、Final Output Artifact，并说明每个 artifact 的作用。

### Step6 角色设计

判断哪些节点应该使用不同角色，例如 Planner、Researcher、Reviewer、Critic、Architect、Tester、Judge，并说明原因。

### Step7 是否需要多模型

判断单模型是否足够。若需要多模型，必须说明为什么需要、放在哪个节点、如何避免模型互相污染、如何合并结果。

### Step8 工作流输出

最终输出必须包含：流程图、节点说明、Artifact 说明、风险控制机制、是否值得增加复杂度。若增加复杂度带来的收益不足 20%，优先采用更简单流程。

## Workflow Patterns

### Requirement Understanding Pattern

用于产品、用户或业务需求澄清。

默认节点：

```text
independent_understanding -> cross_review -> consensus_merge
-> clarification_questions -> human_clarification_gate
-> final_requirement_synthesis
```

规则：

- 只理解需求，不输出方案建议、技术路线、架构建议或实现计划。
- 分歧和缺失信息必须保留到澄清问题或最终待确认事项。
- blocking divergence 需要 Human Gate。

### Architecture Convergence Pattern

用于从需求收敛到系统架构、技术架构、平台架构或其它高影响设计决策。

默认节点：

```text
extract_drivers -> structure_constraints_objectives -> draft_architecture
-> evaluation_gate
  approve -> architecture_freeze
  revise  -> conflict_revision -> evaluation_gate
```

必需 artifacts：

- `architecture_drivers`
- `constraints_objectives`
- `architecture_draft`
- `evaluation_report`
- `conflict_revision_doc`
- `final_architecture`

关键规则：

- `structure_constraints_objectives` 必须拆分 hard constraints、soft constraints 和 optimization function。
- `draft_architecture` 中每个组件必须写 `Component -> Responsibility -> Driver Mapping -> Constraint Coverage`。
- `evaluation_gate` 必须输出 scoring matrix、blocker list、violation list 和 trade-off conflict list。
- `conflict_revision` 只能基于 violation / conflict 修订；resolution strategy 只能是 trade-off acceptance、architecture change 或 constraint relaxation。
- `architecture_freeze` 必须输出 ADR，格式包含 `Decision / Context / Options considered / Trade-offs / Final choice / Reversibility cost`。

### Conditional Development Pattern

用于产生代码或仓库变更。

默认节点：

```text
planning -> plan_review
  approve -> execution
  revise  -> plan_refinement -> plan_review
execution -> output_review
  approve -> validation
  revise  -> output_refinement -> output_review
validation -> retrospective
```

规则：

- planning / refinement / execution 使用 `done / fail / blocked`。
- review / validation 使用 `approve / revise / reject`，必要时加 `fail / blocked`。
- `review(revise) -> refinement -> review` 必须有 `guards.max_visits` 保护。

### Research / Evidence Pattern

用于事实准确性依赖证据、来源质量或外部信息的任务。

默认节点：

```text
question_decomposition -> source_collection -> evidence_extraction
-> contradiction_check -> synthesis -> evidence_review
```

必需 artifacts：

- `research_questions`
- `source_inventory`
- `evidence_table`
- `contradiction_report`
- `synthesis_report`

规则：

- 每个关键结论必须映射到 evidence。
- review 必须检查 source quality、contradiction handling 和 unsupported claims。
- 若信息会随时间变化，节点 instruction 必须要求确认来源日期和事件日期。

### Rubric Evaluation Pattern

用于评审标准必须显式化的质量审核、方案评估、候选项排序或决策支持。

默认节点：

```text
rubric_definition -> candidate_evaluation -> score_normalization
-> decision_review -> final_recommendation
```

规则：

- 先定义 rubric，再评估候选项。
- 输出必须包含 score table、must-fix issues、trade-off notes 和 residual risk。
- 如果 rubric 本身存在争议，加入 Human Gate。

### Simple Linear Pattern

用于风险低、产物单一、无明显回流收益的任务。

默认节点：

```text
prepare -> execute -> summarize
```

规则：

- 不要为了形式增加 review 或 gate。
- 若中途发现失败风险升高，升级到更合适的 pattern。

## 落地规则

1. 先读当前包的 `workflow.yaml`、`agents.yaml`、`mock_script.yaml`、`outputs.yaml`、`skills/*/skill.yaml` 和相关测试。
2. 只为明确风险增加节点。每个节点都要能映射到 `Purpose / Input / Output / Success Criteria`。
3. 纯执行节点使用 `done / fail / blocked`，例如 plan、implementation、summary。
4. 审核或 gate 节点使用 `approve / revise / reject`，必要时再加 `fail / blocked`。
5. `states.<state>.on` 必须覆盖该 task 的正常 decision，并始终保留 `default`。
6. `task.allowed_decisions`、节点 instruction、相关 skill policy 必须语义一致。
7. 对真实开发流，优先使用条件回流：`review(revise) -> refinement -> review`。
8. `_loops` 适合演示或固定轮次实验，不适合作为默认开发流；否则小任务会被迫重复审查。
9. 回流节点必须有 `guards.max_visits` 保护，防止 revise 循环失控。

## Artifact Contract

- 当前模型中 `task.output` 是单主产物。若节点需要“回应 review + 完整修订版计划”，写入同一个 `plan_refinement_doc`，不要假设多产物会自动提升。
- 对可能多轮产生的产物使用 `version_strategy: increment`，下游根据语义读取 `latest` 或 `all`。
- `outputs.yaml.produced_by` 必须只声明真实产出该 artifact 的节点。
- 审核代码时若没有 `diff` artifact，instruction 必须要求从 `execution_report` 列出的文件逐项审查，并记录残余风险。
- 总结节点读取关键产物的 `all`，用于复盘决策链和修订链路。

## Agent 与权限

- `cc-opus` 适合需求理解、计划、复杂修订和复盘。
- `cc-deepseek` 适合 review、audit、找遗漏和反向检查。
- `codex` 适合代码执行、修复、测试和需要本地工具验证的节点。
- 计划和审核节点通常只需要 `Read,Grep,Glob,Write`；`Write` 用于写 staging 产物。
- 不给纯 review 节点默认开放 `Edit` 或 `Bash`，除非该节点明确承担可验证的本地检查。
- Codex 执行或验证节点需要 `workspace-write`；`read-only` 会阻塞代码、测试或 staging 写入。

## 验证清单

- 增加或更新集成测试，覆盖状态列表、关键 transition、mock revise 路径、artifact promotion 和 agent 权限。
- mock 脚本至少覆盖 happy path；如果引入回流，覆盖一次 `revise -> approve`。
- 运行 `validate-state-machine` 和 `validate-config`。
- 若改名 workflow 包，测试路径、workflow name、README 和残留旧目录引用必须一起更新。

## 常见故障

- `allowed_decisions` 与 instruction 写法不一致，导致 Agent 输出的 decision 走 `default`。
- skill policy 过窄，和 task decision 集合没有有效交集。
- 用固定 `_loops` 模拟真实审查，造成无意义双轮或状态名膨胀。
- reviewer 权限过大，导致审核节点越权修改代码。
- refinement 只写“采纳说明”，没有写完整修订后内容，下游无法正确执行。
- 测试仍引用旧 workflow 目录或旧 workflow name，组合回归因此失败。
