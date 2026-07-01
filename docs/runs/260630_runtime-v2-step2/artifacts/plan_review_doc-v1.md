# Runtime v2 Step 2 计划审核报告

## 审核结论

**Decision: revise** — 计划总体方向正确，核心路由逻辑设计基本对齐设计文档，但存在 **2 个阻塞级问题** 和若干重要遗漏需要在执行前修正。

---

## 一、阻塞级问题（Blocking Issues）

### B1. Loader `terminal_states` 自动推断未适配新模型

**位置**：`config/loader.py:384-389`

当前代码：
```python
terminal_states = [
    name for name, s in states.items()
    if s.terminal or not s.on
]
```

问题：新模型下，执行节点归一后的典型形态是 `next="audit", on={}`（空 `on`），按现有逻辑 `not s.on` 为 True，该节点会被**错误地自动识别为终止状态**，导致工作流在执行节点后直接终止而非继续路由。

这是 **运行时正确性阻塞问题**——会导致所有线性执行节点被误判为终止状态。

**修正方向**：改为 `if s.terminal or (not s.on and not s.next)`。

### B2. `_unroll_loops` 归一覆盖声明失实

**位置**：计划 §6.1 风险表第一行

计划声称：
> 归一在 `load_state` 中发生，`_loop` 展开后数据仍然经过 `load_state`，自然被归一覆盖。

**事实**：`_unroll_loops()` 在 `load_workflow()` 中的调用顺序为：
1. `load_state()` 处理原始 YAML states → 归一化（✓）
2. `_unroll_loops()` 创建**新的 StateModel 实例**，直接调用 `StateModel(...)` 构造函数，**绕过 `load_state()`** → 未归一化（✗）

`_unroll_loops()` 中直接写入 `on["done"] = next_in_round`（loader.py:231），产出的 StateModel 仍包含 `done`/`approve`/`revise` 等旧格式键。

**影响评估**：路由行为本身**不会出错**——因为两段式路由中 `decision=done` 能命中 `state.on["done"]`，走 `on` 匹配路径而非 `next` 路径。但计划的风险缓解声明是失实的，且若后续 validate 护栏严格检查"`on` 中有 `done`（生命周期词）→ 报错"，会导致 loop 展开的 workflow 校验失败。

**修正方向**（二选一）：
- **方案 A**：在 `_unroll_loops` 返回后增加一次后归一化遍历（不修改 `_unroll_loops` 本身，符合约束）。
- **方案 B**：在计划中明确记录 loop 展开后的 states 保持旧格式键，路由通过 `on` 匹配兜底，风险已知且接受，待 Step 4 统一处理。同时确保 validate 护栏不因 `on` 中含 `done` 而误报。

---

## 二、需求覆盖审查

| 需求点（来自 .goal.txt） | 计划覆盖 | 评价 |
|---|---|---|
| StateModel 新增 `next` + `on_status` | ✅ Step 2.1 | 完整 |
| `to_dict`/`from_dict` 同步 | ✅ Step 2.1 | `from_dict` 需显式列出读字段（计划未展开，属实现细节） |
| Loader 读 `next`/`on`/`on_status` | ✅ Step 2.2 | 完整 |
| 旧格式归一：`done→next` | ✅ Step 2.2 | 完整 |
| 旧格式归一：`fail`/`blocked→丢弃或 on_status` | ✅ Step 2.2 | 逻辑正确，`blocked≠default` 案例已识别 |
| 旧格式归一：业务词保留在 `on` | ✅ Step 2.2 | 完整 |
| `resolve_transition` 两段式 | ✅ Step 2.4a | 伪代码与设计 doc 一致 |
| 两条 validate 护栏 | ✅ Step 2.4b | 护栏语义正确（见 §四·1 微调建议） |
| TransitionResult 增 `status`+`route_by` | ✅ Step 2.3 | 完整 |
| Runner 主循环适配 | ✅ Step 2.5 | 完整 |
| `_create_error_result` 用 `decision=None` | ✅ Step 2.5 | 完整 |
| Observability 兼容 | ⚠️ Step 2.6 | 描述偏简略，见 §三·R3 |
| 存量测试全部通过 | ✅ Step 5.3 | 集成测试不改代码是最强回归防线 |
| 不实现 Repair | ✅ | 非目标明确 |
| 不改 `_unroll_loops` | ✅ | 非目标明确，但 B2 指出其后归一化仍需处理 |

**覆盖结论**：需求点全部触及，但实现细节层面有 3 处遗漏（见下节）。

---

## 三、主要风险

### R1. `get_state_names()` 遍历不完整（关联 B1）

**位置**：`state_machine/machine.py:163-187`

`get_state_names()` 的 DFS 只遍历 `state.on.values()` 和 `state.default`，不遍历 `state.next` 和 `state.on_status.values()`。新模型下仅通过 `next` 可达的 state 不会出现在有序列表中。

**影响**：`status` 命令的 State History 行可能缺少部分 state。

### R2. `WorkflowConfig.validate()` 目标存在性检查未扩展

**位置**：`config/models.py:201-241`

`validate()` 遍历 `state.on.items()` 检查目标 state 存在性（line 218），但未检查 `state.next` 和 `state.on_status` 的目标。若 YAML 中 `next: nonexistent`，当前校验不会报错。

计划 §2.4b 只提 StateMachine.validate()，未提 WorkflowConfig.validate()——但两个 validate 方法都需要更新。

### R3. Observability 兼容方案过于简略

**位置**：计划 §2.6（Step 6）

计划仅用 ~5 行描述 observability 改动，对 `explain.py` 中 Transition 段的输出格式没有具体说明。当前 `explain.py` 的 Transitions 段只展示 `on` 和 `default`；需要明确补充 `next` 和 `on_status` 的展示格式。

**建议**：在计划中补充具体输出样例，例如：
```
Transitions:
  next                 -> audit
  (on_status) blocked  -> audit
  default              -> failed
```

### R4. `continue_from_gate` 调用签名需更新

**位置**：`runner.py:1271`

当前：`transition = self.sm.resolve_transition(gate_state, decision)`

新模型：需要传入 `status` 参数。Gate 状态暂停前 task 已成功执行完毕，应传 `status="success"`。计划 §2.5 提到"Gate 状态：continue_from_gate() 调用也改为两段式"，但没有明确 status 取值来源。

### R5. `_find_reachable()` 遍历不完整

**位置**：`state_machine/machine.py:85-100`

与 `get_state_names()` 同构问题：DFS 只走 `state.on.values()` 和 `state.default`，缺少 `state.next` 和 `state.on_status.values()`。导致可达性分析不完整（目前只产生警告，不影响执行）。

### R6. `route_by` 语义偏差

**位置**：计划 §2.4a 伪代码第 48 行

```python
if state.next:
    return TransitionResult(
        ...,
        route_by="decision",  # ← 应为 "next" 或 "success"
    )
```

当走 `next` 路径时，实际上没有使用 `decision` 做匹配——路由依据是"这是一个线性节点，成功即走 `next`"。`route_by="decision"` 会误导下游 observability 展示。建议改为 `route_by="next"`。

---

## 四、缺失测试

### 4.1 未覆盖的功能点

| 缺失测试 | 严重度 | 说明 |
|---|---|---|
| `test_terminal_auto_detect_with_next_only` | **阻塞** | 验证 B1 修复：仅有 `next` 无 `on` 的 state 不被误判为 terminal |
| `test_find_reachable_via_next` | 中等 | `_find_reachable()` 通过 `next` 发现 state |
| `test_find_reachable_via_on_status` | 中等 | `_find_reachable()` 通过 `on_status` 发现 state |
| `test_get_state_names_includes_next_path` | 中等 | `get_state_names()` 遍历包含 `next` 路径 |
| `test_workflow_config_validate_checks_next_target` | 中等 | `WorkflowConfig.validate()` 检查 `next` 目标存在性 |
| `test_workflow_config_validate_checks_on_status_target` | 中等 | `WorkflowConfig.validate()` 检查 `on_status` 目标存在性 |
| `test_resolve_transition_decision_none` | 中等 | `decision=None`（Step 1 产物）在两段式路由中的行为 |
| `test_continue_from_gate_two_stage` | 中等 | Gate 继续时传入 `status="success"` 的正确性 |
| `test_loop_unrolled_state_normalization` | 中等 | loop 展开后的 state 在路由中的实际行为（B2 验证） |
| `test_blocked_to_non_default_on_status` | 中等 | software-dev `execute` 的 `blocked→audit` 案例完整性验证 |
| `test_explain_shows_next_and_on_status` | 低 | explain 输出格式手工验证 |

### 4.2 计划已覆盖但需微调的测试

| 测试 | 问题 |
|---|---|
| `test_validate_next_and_on_both_present` | 需确认：同时有 `next` 和 `on` 应该是**错误**（阻止），不是警告 |
| `test_validate_linear_no_allowed_decisions` | 计划定为"警告"，合理。但需确认警告不会导致集成测试失败（存量 YAML 大量存在此模式） |
| `test_old_format_normalize_fail_discarded` | 命名有歧义：并非"丢弃"，而是转入 `on_status`。建议改名为 `test_old_format_normalize_fail_to_on_status` |

---

## 五、可简化点

### S1. `_normalize_state` 可跳过与 `default` 相同的 `fail`/`blocked`

当前伪代码无条件将 `fail`/`blocked` 写入 `on_status`。若目标与 `default` 相同（如 `fail: failed` 且 `default: failed`），写入 `on_status` 是冗余的（路由结果相同，`on_status.get("failed") or default` → `"failed"`）。

建议优化：
```python
for key in ("fail", "blocked"):
    if key in on:
        target = on.pop(key)
        if target != data.get("default", "failed"):
            data.setdefault("on_status", {})
            data["on_status"][key] = target
```

这可以减少 `on_status` 的冗余条目，使 observability 输出更干净。

### S2. validate 护栏可合并到 `StateMachine.validate()` 一处

目前 `WorkflowConfig.validate()` 和 `StateMachine.validate()` 各自维护一套校验逻辑，有部分重叠（如 on 目标存在性）。新护栏如果只在 `StateMachine.validate()` 中实现而不同步到 `WorkflowConfig.validate()`，会导致 `validate-config` 和 `validate-state-machine` 两个命令输出不一致。建议在计划中明确：两条新护栏只在 `StateMachine.validate()` 实现，且 `WorkflowConfig.validate()` 中的 target 存在性检查同步扩展。

---

## 六、需求文档闭环检查

### 6.1 设计文档回溯

对照 `docs/runtime-v2-design.md` 中与 Step 2 相关的条款：

| 设计条款 | 计划匹配 |
|---|---|
| 路由伪代码（5 分支：status≠success → on_status\|default；success+on → on[decision]\|default；success+next → next；否则 → default） | ✅ Step 2.4a 伪代码完整覆盖 |
| `next`/`on` 二选一（一条成功路径） | ✅ validate 护栏 1 |
| `decision` 必填一致性（有 `on` → `allowed_decisions` 非空） | ✅ validate 护栏 2 |
| `on_status` 仅 `failed`/`blocked` | ✅ 归一逻辑限制只处理 `fail`/`blocked` |
| 不引入 `success` 键 | ✅ 非目标明确 |
| Runtime 全程不判断节点类型 | ✅ 路由只看 `on`/`next` 存在性 |
| `cancelled`/`timeout` 不进路由 | ✅ 非目标明确（cancel 单独处理） |

### 6.2 上一轮 plan_refinement 追踪

本次为 Step 2 首轮计划，无前序 plan_refinement_doc 需要追踪。`docs/runs/260626_eventlog-retry/` 中的 refinement 文档属于不同的 feature（history/retry 命令），与本步无关。

---

## 七、审核维度总结

| 维度 | 评估 | 说明 |
|---|---|---|
| **完整性** | ⚠️ 基本完整 | B1/B2 为遗漏，R1-R6 为细节不足 |
| **正确性** | ⚠️ 存在阻塞问题 | B1 导致执行节点被误判为终止 |
| **测试覆盖** | ⚠️ 缺 10 项 | 见 §四，最关键的 `terminal_states` 推断测试缺失 |
| **风险** | ⚠️ 可管理 | 计划 §6 风险表已经识别了主要风险，但 B2 的缓解声明失实 |
| **复杂度** | ✅ 合理 | 改动集中在 7 个文件，无新增模块，范围适当 |

---

## 八、总体评价

计划对 Runtime v2 核心路由改造的理解准确，两段式路由伪代码与设计文档高度一致，归一逻辑正确处理了 `blocked≠default` 的边缘案例（software-dev 的 `blocked→audit`）。测试策略分层合理（单元 + 集成回归）。

**必须在执行前修正**：
1. **B1**：loader 的 `terminal_states` 自动推断逻辑加 `not s.next` 条件
2. **B2**：纠正 `_unroll_loops` 归一覆盖声明，明确后归一化方案或接受已知差异

**强烈建议在执行中处理**：
3. `_find_reachable()` / `get_state_names()` 遍历补全 `next` + `on_status`
4. `WorkflowConfig.validate()` 扩展 target 存在性检查
5. `route_by` 在 `next` 路径使用 `"next"` 而非 `"decision"`
6. 补全 §四列出的 10 项缺失测试

计划整体为 **revise**——不需要重新设计，但需要针对上述阻塞问题做定向修正后重新提交审核。
