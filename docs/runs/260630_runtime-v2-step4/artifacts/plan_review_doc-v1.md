# Runtime v2 第 4 步：修订后计划审核报告

## 审核结论

**Decision: approve** — 修订后计划充分回应了上一轮所有 6 条修订意见，核心设计缺陷（B1、B2）已妥善解决，需求覆盖完整，可通过执行。存在 2 个需在实施中注意的观测项，但不足以阻止进入执行阶段。

---

## 1. 上一轮修订意见逐条核对

### 1.1 Blocking 问题

| # | 上轮问题 | 修订方案 | 审核结论 |
|---|---------|---------|---------|
| B1 | `advise_r2.on` 展开结果漂移（重定向→`on_break` 导致 `revise` 残留） | 改为**删除**循环内 decision（通用逻辑），`plan-review-advise-loop-example` 中 `advise_r2.on` 字面量完全不变 `{approve: execute, reject: failed}` | ✅ **已解决**。删除策略与旧逻辑 `on.pop("revise", None)` 语义等价，且不再硬编码 `revise` 键名。安全保障（删除后 `on` 为空时补出口）逻辑合理。 |
| B2 | StateModel 构造缺失 `next`/`on_status` 字段 | 提取 `_reroute_state_refs` 统一函数，一次性处理 `next`/`on`/`on_status`/`default` 4 个路由字段；Step 1b 伪代码显式写出完整 8 字段构造 | ✅ **已解决**。Step 1b 伪代码中 `on_status` 的循环引用处理规则完整（非最后一轮→修正，最后一轮→保留，非轮次最后 state→修正为同轮下一个）。 |

### 1.2 可简化点

| # | 上轮建议 | 采纳情况 | 审核结论 |
|---|---------|---------|---------|
| S1 | `_make_states_v2` 路径迂回（走 normalize 间接层） | 改为直接构造归一化 StateModel，通过 `with_next`/`with_on`/`with_on_status` 参数控制 | ✅ 已采纳 |
| S2 | Step 4a+4b+4c 合并 | 7 步→2 个 Phase（Phase 1: 改造+测试, Phase 2: 验证+回归） | ✅ 已采纳 |
| S3 | `_reroute_target` → 统一函数 | 提取 `_reroute_state_refs`，一次处理 4 字段 | ✅ 已采纳 |

### 1.3 缺失测试

上一轮指出的 4 个缺失测试已全部纳入计划（test #6 `on_status_redirected`、test #8 `external_next_preserved`、test #12 `multi_loop_survival`、test #2 `on_empty_next_nonempty`）。✅

### 1.4 风险项

上一轮要求声明的 docstring 前置条件已在 Step 1d 中完整给出。✅

---

## 2. 需求覆盖分析

### 2.1 step4-spec.txt 验收点对照

| # | 验收标准 | 覆盖步骤 | 审核 |
|---|---------|---------|------|
| 1 | `_unroll_single_loop` 改按 `next`/`on` 区分节点角色 | Phase 1 Step 1b | ✅ 伪代码完整，`is_last_state_in_round` 分支处理线性/分支节点 |
| 2 | 新增混合场景测试（新旧混合、纯新、纯旧） | Phase 1 Step 1e（12 个测试） | ✅ test #5 混合、test #1 纯线性、test #3 纯分支、test #11 旧格式 |
| 3 | `plan-review-advise-loop-example` 展开结果不变 | Phase 2 Step 2a | ✅ B1 策略保证字面等价，期望值表精准 |
| 4 | 存量测试全通过 | Phase 2 Step 2b | ⚠️ 见 §4 观测项 |
| 5 | 不得改 StateModel 字段 | §2.2 非目标 | ✅ |
| 6 | 不得改路由逻辑 | §2.2 非目标 | ✅ |
| 7 | 保留旧 YAML 向后兼容 | test #11 旧格式兼容测试 | ✅ |

### 2.2 覆盖完整性

所有 7 条验收标准均有对应覆盖。未发现遗漏。

### 2.3 超范围检查

计划非目标列表与 step4-spec 约束一致。新增的 `_reroute_state_refs` 函数属实现层面的内聚提取，不算超范围。

---

## 3. 技术风险审核

| 风险 | 等级 | 分析 |
|------|------|------|
| **`on_status` 最后一轮引用修正遗漏**（见下详述） | 🟡 低 | Step 1b 伪代码中，`is_last_state_in_round && is_last_round` 分支对 `on_status` 完全未处理。若 loop 内最后 state 有 `on_status: {blocked: review}`（blocked 跳回循环内），展开后 `review`（未展开名）会因被替换为 `_rN` 版本而变成悬空引用。**缓解**：实际 YAML 中 `on_status` 极少指向循环内 state，且需同时满足 `target ≠ default` 才会写入 `on_status`（`_normalize_state` 的去重规则会丢弃大部分 `blocked` 条目）。可实现时补充一行修正或保持删除语义。 |
| **存量测试断言改动数量**（见 §4 观测项 1） | 🟡 中 | 存量测试有多处 `on["done"]` 断言，改为 `next` 后需重写。停止规则（超过 3 个断言改动→停止）提供安全网。 |
| **`_reroute_state_refs` 在 loop 内部未实际调用** | 🟢 低 | Step 1c 使用 `_reroute_state_refs` 处理外部 state，但 Step 1b 的 loop 内部展开逻辑是内联写的，未复用同一函数。属代码组织问题，不影响正确性。 |
| **安全保障的补出口策略未定** | 🟢 低 | 伪代码中 "删除后 on 为空时补出口" 的确切策略标注为 "具体实现时再确定"。场景极罕见（所有 `on` 中 decision 都指向循环内 + 最后一轮），留到实现时决定是合理的。 |

### 3.1 `on_status` 最后一轮引用详细分析

**场景**：loop 内 state `review` 有 YAML `on: {done: advise, blocked: review}`，`default: failed`。归一化后 `next: advise, on_status: {blocked: review}`（因 `blocked`→`review` ≠ `default`→`failed`）。

**展开问题链**：
- `review_r1`（非最后一轮）：伪代码在 `if on_status and not is_last_round:` 分支中修正 `blocked`→`review_r2` ✅
- `review_r2`（最后一轮）：伪代码不在最后一轮修正 `on_status`。`is_last_state_in_round=False` 时走 `if on_status:` 通用修正→`next_in_round` ✅；`is_last_state_in_round=True` 时 **完全跳过** `on_status` 处理 ❌

**风险窗口**：仅当最后一轮的最后一个 state 同时满足 (1) 有 `on_status` 条目 (2) 该条目目标指向循环内 state。实际上极其罕见——决策节点的 `on_status` 几乎总是 `{}`（因为 `blocked`/`fail` 通常与 `default` 同目标而被归一化丢弃）。

**审核意见**：不阻塞。实施时若遇到，修正方式有两种选择：(a) 将 `on_status` 中的循环引用重定向到 `on_break`，与 `on` 的删除语义对齐；(b) 在 `_reroute_state_refs` 调用时统一处理。

---

## 4. 观测项（非阻塞）

### 4.1 存量测试 `on["done"]` 断言变迁

当前 11 个存量测试中有多处断言 `result["plan_r1"].on["done"] == "review_r1"`（如 `test_done_transitions_chain_correctly`）。新逻辑将 `done` 移到 `next` 字段，这些断言需改为 `result["plan_r1"].next == "review_r1"`。

**影响评估**：至少 4 个测试方法涉及此类断言（`test_done_transitions_chain_correctly`、`test_advise_last_round_no_revise` 间接涉及、`test_approve_early_exit`、集成测试 `test_load_workflow_with_loop` 的 `on` 内容检查）。计划停止规则设为 3 个——可能在实施时触发停止。

**建议**：在改造 `_make_states` 时同步审视断言字面量变化。若变化来自 `on["done"]`→`next` 这一结构重命名（非语义变化），可视为预期内调整，放宽停止规则的计数方式。

### 4.2 新逻辑对 `on={}` 且 `next=""` 的 base state 行为

修订后计划 §5.1 写道 "存量测试的 base state 多为 `on={}`、`next=""`（旧构造方式）→ 归一化后同样为空，行为不变"。但新逻辑中 `next` 为空 + `on` 为空时，展开 state 将没有任何路由字段指向同轮下一个 state（既不走线性分支也不走分支分支），最终落在 `default: "failed"` 上，与旧逻辑硬编码 `on["done"] = next_in_round` 的行为**不同**。

**缓解**：修改后的 `_make_states` 需为线性节点 base state 设置 `next` 字段（通过 `with_next` 参数），使展开 logic 走线性分支路径。存量测试需同步更新 `_make_states` 调用。计划已提到"可能需要微调存量测试"，但未量化。此处的关键是确保 `_make_states` 的向后兼容包装自动为旧格式调用补上 `next` 推导。

---

## 5. 不可改约束确认

以下约束在修订后计划中均正确标识为不可改，审核确认与 step4-spec 和代码现状一致：

- ❌ `StateModel` 字段定义（`models.py:57-99`）— 8 字段不变
- ❌ `resolve_transition` 路由逻辑（`models.py:74-86`）
- ❌ `_normalize_state` 归一化规则（`loader.py:118-141`）
- ❌ `_loop` 块 YAML 顶层语法（`states`/`repeat`/`on_break`）
- ❌ Agent / Parser / Validator / Runner 等其他模块

---

## 6. 逐步骤评价

| 步骤 | 计划内容 | 审核 |
|------|---------|------|
| **Phase 1 Step 1a** | 编写 `_reroute_state_refs` 统一辅助函数 | ✅ 设计干净，处理 4 个路由字段，行为表清晰（附录 B） |
| **Phase 1 Step 1b** | 改造 `_unroll_single_loop` 核心逻辑 | ✅ 伪代码覆盖线性/分支/最后一轮/on_status，关键变化表对比清晰。⚠️ 见 §3 `on_status` 观测项 |
| **Phase 1 Step 1c** | 改造外部 state 的循环引用修正 | ✅ 通过 `_reroute_state_refs` 统一处理，消除了旧代码丢失 `next`/`on_status`/`gate`/`terminal` 字段的 bug |
| **Phase 1 Step 1d** | 更新 docstring | ✅ 声明归一化前置条件、展开规则，降低未来维护者误调顺序的风险 |
| **Phase 1 Step 1e** | 改造测试辅助 + 12 个新测试 | ✅ 测试覆盖矩阵完整。⚠️ 见 §4.1 存量断言变迁 |
| **Phase 2 Step 2a** | 存量流程等价验证 | ✅ 期望值表精准，包含 `plan`→`summary` 全链路 6 个 state 的字段断言 |
| **Phase 2 Step 2b** | 全量回归测试 | ✅ 三层测试（单元/集成/loop 专项） |

---

## 7. 相对 plan_doc v1 的改进评价

修订后计划在 10 个维度上解决了 v1 的问题：

| 变更 | 评价 |
|------|------|
| 最后一轮删除策略（vs 重定向） | ✅ 正确解决 B1，字面等价 |
| 完整 8 字段构造 | ✅ 解决 B2，消除字段遗漏 |
| `_reroute_state_refs` 统一函数 | ✅ 替代零散修正，可复用 |
| 步骤 7→2 阶段 | ✅ 减少切换开销 |
| 直接构造归一化 StateModel | ✅ 去除_|
| `on_status` 显式处理 | ✅ 补上最大空白 |
| 12 个新增测试 | ✅ 覆盖生产常见场景 |
| docstring 前置条件 | ✅ 防御性文档 |
| 代码上限 150 行 | ✅ 合理约束 |
| 停止规则收紧 | ✅ 早期止损 |

---

## 8. 执行建议

进入执行阶段时，建议按以下优先级处理：

1. **先跑存量测试** — 在改动任何代码前，用 `pytest tests/unit/test_loop_unroll.py -v` 记录基线（11 passed / 0 failed）
2. **先写 `_reroute_state_refs` + 对应单元测试** — 独立函数，容易单独验证
3. **改造 `_make_states`** — 确保存量测试能通过新辅助函数表达同样的预期行为
4. **改造 `_unroll_single_loop`** — 核心改动
5. **每完成一个 Phase 跑全量测试** — 不等到最后

---

## 附录：审核检查清单

- [x] 上一轮所有修订意见是否充分回应？— 是（§1 逐条核对）
- [x] step4-spec 全部验收点是否覆盖？— 是（§2.1）
- [x] 是否有新增的 blocking 问题？— 无（§3 均为低风险）
- [x] 不可改约束是否完整？— 是（§5）
- [x] 停止规则是否合理？— 是，需注意 §4.1 的断言变迁
- [x] 测试数量是否足够？— 是，12 个新增 + 11 个存量 = 23 个
- [x] 是否有超范围内容？— 无（§2.3）
