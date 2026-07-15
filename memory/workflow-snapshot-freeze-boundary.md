# _workflow_snapshot 冻什么、不冻什么：instruction 冻 / allowed_tools 不冻

## 结论（retry 重建配置的边界）

`retry`（`state_machine/retry.py` L273-284）**无条件从 `_workflow_snapshot` 重建
WorkflowConfig，没有重载 `-w` workflow.yaml 的分支**。所以某项配置改源文件后能否对
"已存在的 run 的 retry"生效，取决于它到底在不在快照里：

| 配置项 | 是否进 `_workflow_snapshot` | 改源文件后 retry 是否生效 | 想对当前 run 生效怎么办 |
|--------|---------------------------|--------------------------|----------------------|
| **task.instruction** | ✅ 进（`_workflow_snapshot.tasks.<node>.instruction`） | ❌ 不生效（吃快照旧值） | 必须同步改 `workflow_state.json` 的快照字段 |
| **states / on / guards / 状态机结构** | ✅ 进 | ❌ 不生效 | 同上，改快照 |
| **task.skills 引用名** | ✅ 进（只存名字如 `["review-adoption"]`） | — | — |
| **skill 内容（skill.yaml content）** | ❌ 不进（运行时按名从磁盘加载） | ✅ 生效 | 改 skill.yaml 即可 |
| **agents.yaml allowed_tools / permission_mode** | ❌ 不进（`_discover_agents()` 重新读盘） | ✅ 生效 | 改 agents.yaml 即可 |

一句话：**快照冻的是"工作流定义结构"（instruction + 状态机 + guards），
不冻"运行时按名解析的资源"（skill 内容、agent 白名单）**。两者常被一起想当然，
实际一半冻一半不冻，混淆会导致"改了源文件以为 retry 会生效，结果当前 run 白改"。

## 触发场景（真实案例）

listing-management `spec-dev` 的 `plan_refinement` 节点 instruction 有一条约束
"禁止分批 Write、一次成型"，与单次工具调用输出上限（Claude ~8192 token）物理冲突：
大文档 Write 的 JSON 参数被截断 → `InputValidationError: could not be parsed as JSON` →
重生成整份再撞 → 空转 55min 零产出。改约束时改了源 workflow.yaml 才发现：instruction
在快照里，对卡住的 run retry 不生效，必须同改 `workflow_state.json` 里
`_workflow_snapshot.tasks.plan_refinement.instruction`。安全改法：从已改好的
workflow.yaml 读该字段赋值进快照（逐字一致），改前备份 `.bak`。

⚠️ **该约束改了两轮，每轮都得重新同步一次快照**：第一轮"允许分块"retry 后 agent 仍自选大块、
tool_use 吐一半 `agent_crash`；第二轮才改成"强制小块 ≤150 行/块"。含义：只要 instruction
再动一次，就要再跑一次"读源 yaml→写快照"，否则 retry 又吃回旧快照。这正是 instruction
进快照的直接后果——每次改 instruction 都是"源 + 快照"两处，别只改一处。

## 可考虑的引擎改进（未实施）

retry 目前没有"用 `-w` 重载 instruction、只沿用快照的执行进度"的选项。若常需事后修
instruction 续跑，可评估给 retry 加 `--reload-workflow`：重建 config 时用 `-w` 覆盖
instruction/states，但保留 task_results / artifacts 进度。当前无此路，只能手改快照。

参见项目记忆
`F:\listing-management\docs\memory\workflow-batch-write-constraint-vs-output-limit.md`。
相关：[[claude-permission-mode-semantics]]（allowed_tools 那条已记"改白名单 retry 重新读盘生效"，本条补齐 instruction 的相反行为）。
