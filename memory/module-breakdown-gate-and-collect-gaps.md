---
name: module-breakdown-gate-and-collect-gaps
description: module-breakdown 工作流三处设计偏差：human gate 配了引擎不支持的 revise 分支、中间产物自带 lineage frontmatter、coverage_gate 脚本节点需上游文件物理就位
metadata:
  type: project
---

module-breakdown workflow（F:\listing-management\workflows\module-breakdown）实测发现三处与引擎能力/规范不一致的设计偏差，2026-07-09 手动跑 260709_master-community-governance-mb 时暴露：

**1. human gate 配了 revise 分支，但引擎只支持二元 approve/reject。**
`mapping_check` gate 的 `on:` 配了 `approve/revise/reject` 三分支（`revise: refine`），但引擎 `runner.continue_from_gate(approved: bool)` 硬编码 `decision = "approve" if approved else "reject"`（runner.py:1422），无法产出 revise。gate 节点在 resolve_transition **之前**无条件暂停（runner.py:496-499），agent 产出的 `decision=revise` 被丢弃。结果：`revise→refine` 回流是死代码，human gate 无法触发 refine。对照 requirement-understanding 的 `human_semantic_gate` 是正确范式——只配 `approve/reject`，人工修订意见靠 `continue --input` 注入 human_clarification artifact 由下游节点消费。
**Why:** 期望 revise→refine 的人会发现"continue 无法恢复"——`--reject` 走 failed 而非 refine。
**How to apply:** 要么改 workflow 去掉 revise 分支+走 --input 注入模式，要么扩展引擎 continue 支持三元决策。见 [[claude-permission-mode-semantics]] 同属"设计与引擎实际能力不匹配"类。

**2. 中间产物自带 lineage frontmatter，污染 collect。**
decompose/mapping_check 节点 instruction 让 agent 在 module_breakdown_draft.md / mapping_review.md 里写了 `artifact_id + lineage_id` frontmatter。但这些是中间产物（命令文档标注不进 lineage）。collect 的 scan_artifacts 只看 frontmatter 有无 lineage_id，于是把中间产物也聚合进 lineage。对照 req-understand：所有中间产物（baseline/resolution/review）frontmatter 都不带 lineage 标识，只有正式产物由 attach.py 事后盖章。
**How to apply:** 修 decompose/mapping_check/refine instruction，禁止 agent 给中间产物写 lineage frontmatter；lineage 标识只由 attach 对最终 module_breakdown 盖。

**3. coverage_gate（provider: command 脚本节点）需上游文件物理就位。**
mapping_check.py 的 `_find_artifact` 只在当前 run 目录（artifacts/根/staging）找 final_requirement.md / data_model.md。手动 CLI run 时 collect 只把上游产物路径+摘要注入 goal 文本（LLM 节点能按路径自读，脚本节点不行），导致 coverage_gate 因缺文件 fail。architecture 全 LLM 节点所以没暴露。另：data_model 缺 `coverage table` 机读块时门2（表覆盖）静默 skip，只跑门1（CR）。
**How to apply:** collect 命令层应把 lineage 的 Runtime Artifact 物理落地到 run 目录（而非仅注入路径），或脚本支持跨 run 路径解析。data_model 产出时应带 `coverage table` 块。见 [[architecture-command-collect-attach-scripts]]。

---

## 已修复（2026-07-09，全部在命令行/workflow 层，引擎一行未改）

关键认知：三处**都不是引擎 bug**。引擎的二元 gate + `continue --input` 注入本来就够用（requirement-understanding 是已验证的正确范式）。是 module-breakdown 自己配错/缺约束。照抄 req-understand 范式修复：

1. **gate 三元→二元**：workflow.yaml 的 mapping_check 改 `approve→finalize / reject→failed`，**删除 refine 节点与 revise 回流**。人工修订意见写进裁决文件（human_clarification），经 `continue --input` 注入，finalize 一次性 Apply（对齐 canonicalize）。skills/mapping-check、finalize 同步改。
2. **中间产物 frontmatter**：非引擎/skill 强制，是 agent 读到注入的上游产物（带 attach 盖的 frontmatter）后**照猫画虎自造**。修复：decompose/mapping-check/finalize 的 skill + workflow instruction 显式禁止 agent 输出 YAML frontmatter/lineage 标识；命令文档写明"frontmatter 由 attach 维护、只盖正式产物，中间产物不带"。
3. **coverage_gate 上游文件**：不复制、不 manifest。改 `mapping_check.py`：`_resolve_upstream` 先本地找，找不到按 artifact_id（--seed）/lineage_id（--lineage）/纯 artifact_name 三级全局扫 `docs/runs/*/artifacts/` 定位**原文件**（脚本自解析，手动裸跑也能过）。引擎 command 只有 {project_root}/{run_root}/{goal} 三占位符、无法传 seed/lineage，故命令层传参为增强、脚本自解析为主。

验证：validate-config/state-machine 通过（7 states/4 tasks/3 terminal）；mapping_check.py 三种定位路径均 EXIT=0。改动文件：workflow.yaml、skills/{decompose,mapping-check,finalize}/skill.yaml（删 skills/refine）、command/mapping_check.py、.claude/commands/module-breakdown.md。