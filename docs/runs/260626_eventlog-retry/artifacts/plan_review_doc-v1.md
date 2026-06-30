# 审核报告：history 命令 + retry 失败诊断

## 总体评估

**Decision: revise**

计划整体方向正确，模块边界清晰，测试策略真实可行。但存在 3 个需在 refinement 中解决的非琐碎问题（1 个 Gap、1 个算法细节风险、1 个测试依赖风险），以及 5 个建议项。无根本性架构缺陷。

---

## 一、Blocking 问题

**无。** 未发现会导致计划不可行或与代码库现有契约冲突的根本缺陷。

---

## 二、需求覆盖分析

| 验收点 | 覆盖 | 备注 |
|---|---|---|
| `history -r` 因果时间线 | ✅ | §4 Step 3，render_history + _format_event_line |
| `--why <state>` 反查 | ✅ | §4 Step 3，render_why，回溯 TransitionSelected 链 |
| retry dry-run 含 diagnose step | ✅ | §4 Step 2，_build_dry_run_steps 接入 |
| 三类诊断路径 (validator/guard/crash) | ⚠️ | §4 Step 1 仅覆盖 max_visits/max_retries，max_duration_minutes 遗漏（见后） |
| 单元测试覆盖三者 + history | ✅ | §5，3 个新测试文件，18 个用例 |
| 不修改 TaskResult 契约 | ✅ | §2 非目标 |
| 不引入数据库/新依赖 | ✅ | 复用 read_log，纯函数 |

**遗漏项**：
- `max_duration_minutes` GuardFailed 未被任何诊断分支捕获，落入 `KIND_UNKNOWN`。需求文档未明确列出此 guard_type，但 GuardChecker 实际产出三种 guard_type（max_visits / max_duration_minutes / max_retries），event log 中必然存在。建议在 plan_refinement 中明确此 guard_type 的处理方式（纳入 KIND_GUARD_LOOP 或新增 KIND_GUARD_TIMEOUT）。

---

## 三、主要风险

### R1 — `_build_dry_run_steps` 签名不携带事件源路径（中风险）

计划要求在 `_build_dry_run_steps()` 内部调用 `read_log(context.run_id, run_root=context.run_root)`。当前签名：

```python
def _build_dry_run_steps(context: RunContext, retry_state: str, workflow: WorkflowConfig) -> list[dict[str, Any]]
```

RunContext 确实有 `run_id` 和 `run_root` 字段，路径可用。但：

1. `read_log` 返回 `list[dict] | str`（联合类型），作为 `list[dict]` 使用时需确保不传 `summary=True`。
2. 如果 `logs/events.jsonl` 不存在，`read_log` 返回 `[]`（空列表），`diagnose_last_failure([])` → `KIND_UNKNOWN`，不抛异常。✅ 已有防御。

**缓解**：实现时加上 `if not events: return default_step`，不依赖 diagnose 内部处理空列表。

### R2 — `render_why` 链式回溯的分支风险（中风险）

计划描述："从最后一条进入 target_state 的 TransitionSelected 倒推上一个 state"。

实际需在代码中明确：
- 过滤条件：`event["event"] == "TransitionSelected" and event["payload"]["next_state"] == target_state`
- 取该过滤集中**最后一条**（按文件顺序 === 时序顺序）
- 从该条获取 `event["payload"]["current_state"]` 作为上一状态
- 以此类推，需 `seen_state` 集合防止死循环（尤其 gate→resume 回路）

当前计划 §4 Step 3 的描述偏高层，缺少过滤条件的精确表达式。应在此次 refinement 中补充伪代码。

### R3 — `test_retry_dry_run_diagnosis.py` 强依赖 WorkflowConfig.from_dict 内构（中风险）

测试需要用 `tmp_path` 构造 `WorkflowConfig` 快照。当前：
- `WorkflowConfig.from_dict` 需要 `states` 字典，每个 state model 需要 `on`（transition map）。
- 若将来 Config 模型内部结构变化（如必填字段增加），测试会断。

**缓解**：在 refinement 中明确测试夹具的最简 WorkflowConfig 快照结构，减少对内部字段的隐含依赖。

### R4 — 事件字段漂移（低风险，已有防御）

ValidatorFinished 的 `errors` 位于 `event["payload"]["errors"]`（经验证，通过 EventBus → JSONLSink 路径确认）。计划采用 `.get()` 防御性访问，符合代码库现有风格。

### R5 — Windows 终端中文输出（低风险，已有防御）

延续 `safe_print` 复用，不引入 colorama/rich，一致性好。

---

## 四、缺失测试

| 测试点 | 状态 | 说明 |
|---|---|---|
| `max_duration_minutes` guard 诊断 | ❌ 缺失 | 计划 §5 的 guard_loop 用例仅测 max_visits + max_retries |
| ValidatorFinished(passed=true, warnings=[...]) 非阻断场景 | ❌ 缺失 | 当 passed=true 但之后 run 仍失败的场景，diagnose 应返回什么？ |
| 同 run 内多次 AgentStarted（不同 state）的崩溃判定 | ❌ 缺失 | 实现需确保"后无完成事件"是按同 event_type 的时序而非全局 |
| events.jsonl 缺失文件 | ❌ 缺失 | `read_log` 返回 `[]`，应确保不抛异常，`KIND_UNKNOWN` |
| `render_why` 目标 state 从未被进入 | ✅ | §5 已列 |
| Heartbeat 高噪声事件过滤 | ✅ | §5 已列（show_all vs 主干） |
| 空事件列表诊断 | ✅ | §5 已列 |

**建议新增的最小测试**：
1. `test_max_duration_guard_diagnose()` — GuardFailed(guard_type=max_duration_minutes) → 明确预期 kind
2. `test_diagnose_events_missing_file()` — read_log 返回 [] 或 None → KIND_UNKNOWN
3. `test_agent_crash_multi_state()` — 两个 state 的 AgentStarted，只有第一个有 TaskResultWritten → 正确判定最后一个为 crash

---

## 五、可简化点

### S1 — `ValidatorStarted` 是否纳入 MAIN_EVENT_TYPES

计划 §4 Step 3 将 `ValidatorStarted` 列入主干事件白名单。ValidatorStarted 的实际信息量较低（仅表示"开始校验"），ValidatorFinished 才携带有意义的 passed/errors 信息。建议默认主干移除 ValidatorStarted，通过 `--all` 可见。

### S2 — 两个新模块可合并为一个

`retry_diagnose.py` 仅包含一个入口函数 `diagnose_last_failure`（~30 行）和 4 个常量。可考虑放在 `retry.py` 同文件或合并到 `history.py`（因为两者都消费 events.jsonl）。但计划的分拆理由（与 retry 流程解耦便于测试）是合理的，保持现状可接受。

### S3 — Step 5（文档）可省略

"仅在 argparse help= 文案里补一两行" — 这个 step 体量极小，可合并到 Step 4（CLI 接入）。

---

## 六、上一轮审核追踪

无历史 plan_review_doc / plan_refinement_doc（本轮为首轮审核）。

---

## 七、建议的 refinement 修改方向

1. **补全 `max_duration_minutes` 诊断分支**：建议作为 `KIND_GUARD_LOOP` 的第三种 guard_type 或新增 `KIND_GUARD_TIMEOUT`（retry_recommended=True，因为重试会重置计时器）。
2. **明确 `render_why` 的 TransitionSelected 过滤伪代码**：`filter(lambda e: e["event"]=="TransitionSelected" and e["payload"]["next_state"]==target)`，并加入 `seen_states` 集合防回环。
3. **补充最小测试夹具结构**：在计划中给出 `workflow_state.json` 的最简快照示例（含一个 state + states 映射）。
4. **补充三个缺失测试用例**（max_duration_minutes、events 文件缺失、多 state 崩溃判定）。

---

## 八、审核结论

| 维度 | 评级 |
|---|---|
| 架构合理性 | ✅ 好 — 模块边界清晰，复用 read_log |
| 与现有代码库一致性 | ✅ 好 — 遵循安全打印、防御性 .get()、中文注释 |
| 需求覆盖 | ⚠️ 缺 max_duration_minutes 分支 |
| 测试策略 | ⚠️ 缺 3 个边界用例 |
| 风险可控性 | ✅ 低风险，均有缓解措施 |

**Decision: revise** — 计划可执行，但需在 refinement 中处理上述修改方向后再进入 execution。
