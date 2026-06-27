# 审核报告：history 命令 + retry 失败诊断（第二轮）

本文档基于 `plan_doc-v1.md` 与 `plan_refinement_doc-v1.md` 联合审核，并对第一轮 `plan_review_doc-v1.md` 提出的全部修改方向做追踪复核。

---

## 总体评估

**Decision: approve**

经过 refinement 后，计划已完整覆盖需求、充分响应了第一轮审核的所有修改方向。计划中对事件 payload 结构的假设经代码库实际验证均正确。未发现阻塞性问题。以下列出验证过程、逐条追踪和可选的增强建议。

---

## 一、第一轮审核追踪

第一轮 `plan_review_doc-v1.md` 提出 4 个修改方向 + 3 个缺失测试 + 3 个可简化点。逐项追踪：

| # | 第一轮条目 | Refinement 响应 | 追踪结论 |
|---|---|---|---|
| 1 | 遗漏 `max_duration_minutes` guard 分支 | 新增 `KIND_GUARD_TIMEOUT`，retry_recommended=True | ✅ 充分处理。代码验证：`guard.py` 实际产生 `max_duration_minutes` guard_type（第 84 行），事件 payload 通过 `guard_result.__dict__` 发射，`guard_type` 字段可直达。计时器在重试时因新 Runner 实例重新 `set_start_time` 而重置，retry_recommended=True 正确。 |
| 2 | R1：空文件防御 | `isinstance(events, list)` + 空列表早返 placeholder step | ✅ 充分处理。`read_log` 在文件不存在时返回 `[]`（第 76 行），`isinstance` 守卫可防御 `summary=True` 返回字符串的误用路径。 |
| 3 | R2：`render_why` 缺过滤伪代码 | 加入精确过滤表达式 + `seen` 防回环 + 链路断开友好输出 | ✅ 充分处理。过滤条件 `event=="TransitionSelected" && payload.next_state==cursor` 与 `Transition.to_event_dict()` 返回的 `{current_state, decision, next_state, ...}` 完全对齐。`seen` 集防回环正确。 |
| 4 | R3：测试夹具对 WorkflowConfig 的依赖 | `MINIMAL_WORKFLOW_SNAPSHOT` 常量集中维护 | ✅ 充分处理。常量含 1 个 state + tasks + states 映射，足以通过 `WorkflowConfig.from_dict` 的最简校验。 |
| 5 | 缺测试：max_duration_minutes | `test_guard_timeout_max_duration_minutes` | ✅ 已补入 §5 用例清单。 |
| 6 | 缺测试：events 文件缺失 | `test_diagnose_events_missing_file` → 在 `test_unknown_and_empty` 中合并覆盖 | ✅ 已纳入。 |
| 7 | 缺测试：多 state 崩溃判定 | `test_agent_crash_multi_state` | ✅ 已补入，强制验证"按最后一条 AgentStarted"而非"按全局"。 |
| 8 | 缺测试：ValidatorFinished(passed=true) | `test_validator_passed_falls_through` | ✅ 已补入，语义澄清为"passed=true 不阻塞 → fall through 到后续规则"。 |
| 9 | S1：ValidatorStarted 去噪 | 从 `MAIN_EVENT_TYPES` 移除 | ✅ 已执行。 |
| 10 | S2：合并文件 | 不采纳，理由合理 | ✅ 可接受。`retry_diagnose.py` 独立可纯函数测试，与 retry 流程解耦。 |
| 11 | S3：Step 5 合并 | 合并入 Step 4 | ✅ 已执行，Step 数 6→5。 |

**结论：11/11 条全部充分处理，无遗漏。**

---

## 二、Blocking 问题

**无。**

代码库实地验证确认计划中所有关键假设均成立：

### 2.1 事件 payload 结构验证

通过阅读 `runner.py`、`events.py`、`jsonl_sink.py`、`transition.py` 确认：

| 事件类型 | payload 关键字段 | 计划中的访问方式 | 验证结果 |
|---|---|---|---|
| `TransitionSelected` | `{current_state, decision, next_state, matched, reason}` | `e["payload"]["next_state"]`、`e["payload"]["current_state"]` | ✅ 正确（`transition.py:28-36`） |
| `GuardFailed` | `{passed, reason, guard_type, current_value, threshold, next_state_if_failed}` | `e["payload"]["guard_type"]` | ✅ 正确（`guard.py:68-101`，`guard_result.__dict__` 发射） |
| `ValidatorFinished` | `{state, passed, status_text, blocking, errors, ...}` (passed=false) 或 `{state, passed, status_text, warnings, ...}` (passed=true) | `e["payload"]["passed"]`、`e["payload"]["errors"]` | ✅ 正确（`runner.py:385-418`） |
| `AgentStarted` | event 顶层 `state` 字段 + payload 含 `agent`, `task` | `e["state"]` | ✅ 正确（`events.py:64`） |

### 2.2 JSONL 记录结构验证

`jsonl_sink.py:31-38` 写入的 record 结构：
```json
{"event": "TransitionSelected", "timestamp": "...", "run_id": "...", "state": "...", "task": "...", "payload": {...}}
```
- `event` 和 `payload` 均位于顶层，与计划中所有 `.get("event")` / `.get("payload", {})` 访问方式完全一致。✅

### 2.3 `read_log` 返回类型验证

`jsonl_sink.py:60`：`read_log(run_id, summary=False, run_root=None) -> list[dict] | str`。在非 `summary` 模式下返回 `list[dict]`，文件不存在时返回 `[]`。计划中的 `isinstance(events, list)` 守卫可正确防御 `summary=True` 时返回字符串的路径。✅

---

## 三、需求覆盖

| 验收点 | 覆盖 | 详细说明 |
|---|---|---|
| `history -r` 因果时间线 | ✅ | `render_history()` + `MAIN_EVENT_TYPES` 白名单过滤 + `_format_event_line()` 格式化 |
| `--why <state>` 反查 | ✅ | `render_why()` + TransitionSelected 回溯 + seen 防回环 + "(进入源未知)" 友好退出 |
| retry dry-run 含 diagnose step | ✅ | `_build_dry_run_steps()` 调用 `diagnose_last_failure()` + 空文件防御 |
| 三类诊断 + Guard 细分 | ✅ | validator_block / guard_loop / guard_timeout / agent_crash / unknown，共 5 个 kind |
| 单元测试全覆盖 | ✅ | 3 文件 × 8+5+2=15 用例，覆盖所有 kind + 边界（空事件、passed=true fallthrough、多 state crash） |
| 不修改 TaskResult 契约 | ✅ | 所有新代码只读 events.jsonl，不触碰 TaskResult 模型 |
| 不引入数据库/新依赖 | ✅ | 复用 `read_log()`，纯函数，无第三方库 |

**无遗漏，无超范围。**

---

## 四、主要风险

### R1 — `cmd_retry` 输出循环的隐式吞没（低风险）

**发现**：`cli.py:471-480` 的 step 输出循环对 `detail` dict 的 key 只处理 `"operations"` 和 `"next_states"`，其余 key 静默跳过。新增的 `diagnose_last_failure` step 的 `detail` 是一个诊断 dict（`kind`, `reason`, `retry_recommended`, `detail` 等），其 key 不在现有白名单中。

**计划响应**：计划 §4 Step 2 末尾明确说"对 `action=diagnose_last_failure` 增加格式化行"。这需要在循环内新增 `if step['action'] == 'diagnose_last_failure':` 分支。

**缓解**：实现时在输出循环开头加 action 分支判断即可，改动量 ~8 行，不影响其他 step 的输出。无需改动计划。

### R2 — `render_history` 函数签名的测试友好性（低风险）

**发现**：计划中 `render_history(run_id, run_root, show_all=False) -> str` 签名接受 `run_id`/`run_root` 并在内部调用 `read_log`。但单元测试需要构造内存事件列表直接传入。

**缓解**：实现时提供内部纯函数 `_render_events(events, show_all) -> str` 接受事件列表，公开的 `render_history` 作为薄封装调用 `read_log` + `_render_events`。计划中 `_filter_main_events` / `_format_event_line` 私有函数已为此打下基础。单元测试直接调 `_filter_main_events` + `_format_event_line`，或调 `_render_events`（建议加到模块约定暴露）。无需修改计划。

### R3 — `GuardFailed` 事件 payload 通过 `__dict__` 发射（低风险）

**发现**：`runner.py:355` 使用 `guard_result.__dict__` 作为 payload。这导致 JSONL 中 `GuardFailed` 的 payload 包含 `passed`、`reason`、`guard_type`、`current_value`、`threshold`、`next_state_if_failed`，比 `events.py:72` 的 `event_registry` 列出的必填字段多。计划中只用 `payload.guard_type`，不受影响，`.get()` 防御性访问不会因多余字段失败。

**缓解**：已通过 `.get()` 防御性访问。无需修改计划。

### R4 — `render_why` gate→resume 链路截断（低风险，已认知）

**发现**：当 workflow 包含 `s1 → gate → s1 → s2 → done` 这样的回流路径，`render_why(s2)` 会因 `seen` 集在第二次遇到 `s1` 时停止，输出 `s1 → s2` 而非 `s1 → gate → s1 → s2`。

**评价**：这不是 bug，而是"最多一次进入"约束下的必然行为。计划在 §1 歧义点中已声明"默认显示最近一次的进入链"，在伪代码注释中标注"防回环：gate→resume / 自循环 state"。设计合理。✅

---

## 五、缺失测试

对第一轮提出的缺失测试做最终盘点：

| 测试点 | 状态 | 位置 |
|---|---|---|
| `max_duration_minutes` guard 诊断 | ✅ 已补 | `test_retry_diagnose.py` #4 |
| events.jsonl 文件缺失 | ✅ 已补（合并在 unknown/empty） | `test_retry_diagnose.py` #8 |
| 多 state 多次 AgentStarted 崩溃判定 | ✅ 已补 | `test_retry_diagnose.py` #6 |
| ValidatorFinished(passed=true) fallthrough | ✅ 已补 | `test_retry_diagnose.py` #7 |
| `render_why` 防回环 | ✅ 已有 | `test_history_renderer.py` |
| `render_why` 未找到目标 state | ✅ 已有 | `test_history_renderer.py` |
| `show_all=True` vs 主干过滤 | ✅ 已有 | `test_history_renderer.py` |
| dry-run integration（含诊断 step） | ✅ 已有 | `test_retry_dry_run_diagnosis.py` |

**建议新增（非阻塞）**：`render_why` 目标 state 在该 run 中从未被进入 → 应输出"(进入源未知)"，当前在测试清单中列为 render_why 测试的第 5 项，但文字表述为"render_why 未找到"。建议测试用例名称中明确包含 `target_never_entered` 字样以便 grep。

**建议新增（非阻塞）**：对 `cmd_history` / `cmd_retry` 增设一个轻量 CLI 级集成测试（用 `tmp_path` 造 run 目录、调用 `cmd_history(args)`，断言行输出非空）。当前 plan 仅在 Step 4 做手工验证，缺少自动化回归。考虑到 CLI 层本身非常薄（参数解析 → 调函数 → safe_print），低优先级。

**结论：无缺失的阻塞级测试。**

---

## 六、可简化点

### S1 — `retry_diagnose.py` 常量可精简

当前 5 个常量：`KIND_VALIDATOR_BLOCK`、`KIND_GUARD_LOOP`、`KIND_GUARD_TIMEOUT`、`KIND_AGENT_CRASH`、`KIND_UNKNOWN`。可考虑用 `Enum` 替代裸字符串常量，提升类型安全性。

**建议**（非阻塞，不改计划）：使用 `str, Enum` 双继承定义 `FailureKind`：

```python
class FailureKind(str, Enum):
    VALIDATOR_BLOCK = "validator_block"
    GUARD_LOOP = "guard_loop"
    GUARD_TIMEOUT = "guard_timeout"
    AGENT_CRASH = "agent_crash"
    UNKNOWN = "unknown"
```

但这属于实现细节，不阻塞计划审批。

### S2 — Step 1→2 依赖链

Step 2（接入 retry dry-run）依赖 Step 1（诊断纯函数）的 `diagnose_last_failure` 函数签名。Step 3（history 渲染器）与 Step 1/2 完全独立，可并行实现。但在 5-step 线性计划中按序排列，实际执行时可按需并行。无需修改计划。

---

## 七、与现有代码库一致性

| 维度 | 评价 |
|---|---|
| 模块命名 | `observability/history.py` — 与 `observability/jsonl_sink.py`、`observability/events.py` 同级，一致 ✅ |
| 函数风格 | 纯函数（输入 events → 输出 str/dict），与 `jsonl_sink.read_log` 无副作用风格一致 ✅ |
| 错误处理 | `.get()` 防御性默认值，与现有 `console_sink.py`、`status.py` 风格一致 ✅ |
| 测试风格 | pytest + `tmp_path` 夹具，与现有 `tests/unit/` 风格一致 ✅ |
| 中文注释 | 遵循项目惯例 ✅ |
| CLI 注册 | `build_parser()` 中 `sub.add_parser` + `set_defaults(func=...)`，与现有 11 个子命令一致 ✅ |
| 输出方式 | `safe_print`，与现有 CLI 一致 ✅ |

---

## 八、审核结论

| 维度 | 评级 |
|---|---|
| 架构合理性 | ✅ 好 — 模块边界清晰，依赖方向正确（retry→diagnose, CLI→history） |
| 与现有代码库一致性 | ✅ 好 — 完全遵循现有模式 |
| 需求覆盖 | ✅ 完整 — 两个功能 + 所有诊断分支 + 反查 |
| 第一轮审核追踪 | ✅ 11/11 条全部充分处理 |
| 测试策略 | ✅ 充分 — 15 用例覆盖 5 个 kind + 全部边界 |
| 风险可控性 | ✅ 低风险 — 4 个已识别风险均有缓解措施 |

**Decision: approve** — 计划可执行，可进入 execution 阶段。

**建议执行时注意**（非阻塞，可在实现中自行决议）：
1. `cmd_retry` 输出循环需为 `action=diagnose_last_failure` 新增分支，注意现有 `detail` 迭代逻辑不会自动打印诊断字段。
2. `render_history` 建议暴露 `_render_events(events, show_all)` 内部纯函数供单元测试直接传入内存事件列表。
3. 可选：`FailureKind` 用 `StrEnum` 替代裸字符串常量。
