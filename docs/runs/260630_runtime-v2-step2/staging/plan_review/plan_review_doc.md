# Runtime v2 Step 2 计划审核报告（第二轮）

## 审核结论

**Decision: approve** — 修订后的计划完整、正确，上一轮指出的全部阻塞问题和遗漏均已充分处理。无新增阻塞问题，可以进入执行。

---

## 一、上一轮审核追踪（plan_review_doc-v1 → plan_refinement_doc）

| 上一轮指出 | 类型 | 处理状态 | 评价 |
|---|---|---|---|
| **B1**: terminal_states 自动推断未适配 | blocking | ✅ 已修正 | 从 `not s.on` 改为 `not s.on and not s.next`，同步修正 loader 和 machine 两处 |
| **B2**: _unroll_loops 归一覆盖失实 | blocking | ✅ 已修正 | 采用方案 B：明确记录已知差异，护栏不检查键名语义，Step 4 处理 |
| **R1**: get_state_names() 遍历不完整 | 风险 | ✅ 已采纳 | DFS 追加 next + on_status.values() |
| **R2**: WorkflowConfig.validate() 目标检查未扩展 | 风险 | ✅ 已采纳 | 新增 3b/3c 两个检查块 |
| **R3**: Observability 方案过于简略 | 风险 | ✅ 已采纳 | §4.6 补充了 explain.py 完整格式和实现逻辑 |
| **R4**: continue_from_gate 签名需更新 | 风险 | ✅ 已采纳 | 明确传 `status="success"` 并说明理由 |
| **R5**: _find_reachable() 遍历不完整 | 风险 | ✅ 已采纳 | 追加 next + on_status.values() |
| **R6**: route_by 语义偏差 | 风险 | ✅ 已采纳 | next 路径改为 `route_by="next"`，新增 6 分支取值表 |
| **S1**: 跳过冗余 on_status | 优化 | ✅ 已采纳 | fail/blocked == default 时不写入 on_status |
| **S2**: validate 护栏范围澄清 | 优化 | ✅ 已采纳 | 明确职责边界：WorkflowConfig → 引用完整性，StateMachine → 语义完备性 |
| 10 项缺失测试 | 覆盖 | ✅ 全部新增 | 测试从 ~14 项扩展到 ~28 项，覆盖全部功能点 |
| test_validate_next_and_on_both_present | 微调 | ✅ 确认 | 明确为硬错误 |
| test_validate_linear_no_allowed_decisions | 微调 | ✅ 确认 | 明确为警告（非硬错误） |

**追踪结论**：上一轮指出的 2 个阻塞问题 + 4 个风险 + 10 个缺失测试 + 2 个可简化点，在修订计划中**全部得到充分处理**。没有遗留未解决的评审意见。

---

## 二、需求覆盖审查（对照设计文档）

对照 `docs/runtime-v2-design.md` 中与 Step 2 相关的条款：

| 设计条款 | 修订计划 | 评价 |
|---|---|---|
| `StateModel` 增 `next` + `on_status` | ✅ §4.1 | 字段定义、to_dict、from_dict 全覆盖 |
| 路由伪代码（5 分支） | ✅ §4.4a | 展开为 6 分支，逻辑与设计文档一致 |
| `next`/`on` 二选一 | ✅ validate 护栏 1 | 硬错误拦截 |
| `decision` 必填一致性 | ✅ validate 护栏 2 | 硬错误 + 警告分层 |
| `on_status` 仅 `failed`/`blocked` | ✅ 归一逻辑限制只处理 `fail`/`blocked` | 正确 |
| 不引入 `success` 键 | ✅ 非目标明确 | — |
| Runtime 全程不判断节点类型 | ✅ 路由只看结构存在性 | — |
| `cancelled`/`timeout` 不进路由 | ✅ 非目标明确 | — |
| 旧 YAML 自动归一 | ✅ §4.2 | done→next，fail/blocked→on_status 或丢弃 |
| TransitionResult 增 status+route_by | ✅ §4.3 | 三种取值：status/decision/next |
| validate 两条护栏 | ✅ §4.4b | 静态检查，不检查键名语义 |
| observability 兼容 | ✅ §4.6 | explain.py 四种格式展示 |
| `_create_error_result` decision=None | ✅ §4.5 | 明确修改点为 runner.py:1321-1345 |
| `_loop` 展开 Step 2 不处理 | ✅ 方案 B | 已知差异，Step 4 处理 |

**覆盖结论**：14 项设计条款全部覆盖，无遗漏，无超范围。

---

## 三、存量 YAML 兼容性验证

通过 grep 扫描全部 10 个 `workflow.yaml` 文件：

| 模式 | 出现频率 | 归一后行为 | 结论 |
|---|---|---|---|
| `on: {done: xxx}` | 多个线性节点 | `next = xxx` | ✅ 正确 |
| `on: {fail: failed, blocked: failed}` | 绝大部分节点 | 与 default 相同 → 不写入 on_status | ✅ 正确（S1 优化） |
| `on: {blocked: audit}`（listing-dev:159） | **1 处** | audit ≠ failed(default) → 写入 `on_status.blocked = audit` | ✅ 正确（计划已识别此案例） |
| `on: {approve, revise, reject, fail, blocked}` | 分支节点 | 业务词保留在 on，fail/blocked 移除 | ✅ 正确 |

**关键发现**：`listing-dev/workflow.yaml` 的 `implement` 状态确实存在 `blocked: audit`（≠ default "failed"），归一后 `on_status = {"blocked": "audit"}`。这是存量 YAML 中唯一需要 on_status 的案例，计划的 S1 逻辑正确覆盖。其他所有 YAML 的 `fail`/`blocked` 均指向 `failed`（== default），归一后 on_status 为空，行为零回退。

---

## 四、主要风险评估

### 低风险（可执行中管理）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **`decision=None` 在 TaskFinished 事件中** | runner.py:431 `decision = task_result.get_decision() if task_result else "fail"` → None 会变成 "fail"（正确），但事件的 decision 字段语义需要与 transition 的 decision 区分 | 这是预期行为：事件记录的 decision 是 workflow 层提取的，transition 中的 decision 是传给路由的原始值。两者可以不同，设计合理 |
| **`_normalize_state` 的位置** | 需在 `load_state()` 调用 `StateModel(...)` 前执行。如果放置在 `load_workflow()` 的 states 加载循环中，需要确认不影响 `_unroll_loops` 的执行顺序 | 建议在 `load_state()` 内部执行归一，传 data 副本不修改原始字典。实现细节，非阻塞 |
| **`explain.py` is_terminal 判断** | 当前 `is_terminal = ... or (not on_map and not task_name)`。归一后 `on` 变空、`next` 非空时，需加 `not next` 防止误判 | 计划 §4.6b 已覆盖此修正，注意实现时变量名一致性 |
| **Guard 错误判定路径** | runner.py:401-402 的 blocking validation 路径仍使用 `decision="fail"`（而非 None），与 `_create_error_result` 的 `decision=None` 不一致 | 这是合理差异：validator blocking 是 workflow 层明确判决"fail"，error_result 是内部异常。Step 3 Repair 后将统一此路径 |
| **`BlockingError` → `decision="fail"` → resolve_transition** | 当 status 被设 "invalid_output" 但 transition 仍传入 `decision="fail"`，走到两段式路由时 `status != "success"` → on_status/default | 当前代码 runner.py:402 设 `status = "invalid_output"`，不在 {success, failed, blocked} 中。`resolve_transition` 会走 `status != "success"` 分支 → on_status 或 default。但如果 on_status 没有 "invalid_output" 键，走 default → "failed"。**行为正确** |

### 无风险（已验证）

- `_unroll_loops` 产出 states 保持旧键（`on` 中含 `done`），路由通过 `on` 匹配兜底 → 已确认正确
- 所有 YAML 的 `fail`/`blocked` → default 都是一致的（除了 listing-dev 的 blocked→audit，已正确处理）
- validate 护栏不检查 `on` 键名语义 → 不会误报 loop 展开的 states

---

## 五、测试覆盖评估

### 完整覆盖清单

修订计划列出了 **28 项测试**，覆盖 7 个维度：

| 维度 | 测试数 | 覆盖情况 |
|---|---|---|
| 序列化往返 | 2 | `next`/`on_status` 的 to_dict/from_dict |
| 旧格式归一 | 5 | done→next, fail→on_status, fail==default 跳过, blocked→on_status, 业务词保留 |
| 两段式路由（6 分支） | 7 | success+next, success+on(match), success+on(unmatch), failed→default, blocked→on_status, decision=None, TransitionResult 字段 |
| Terminal 推断 | 1 | next-only 不被误判 |
| Traversal 补全 | 3 | _find_reachable, get_state_names 通过 next/on_status 发现 |
| Validate 护栏 | 6 | 缺出口, next+on 冲突, decision 一致性(2), WorkflowConfig target 检查(2) |
| 集成路径 | 4 | continue_from_gate, loop unrolled state, blocked→non-default, error_result decision=None |

### 测试质量评价

- 每个测试对应一个可验证的具体行为，无冗余
- 错误路径和边界条件（decision=None, status=failed, 空 on/next）均有覆盖
- 集成回归防线：存量 `tests/integration/` 和 `tests/unit/` **不改代码**，全部应通过

---

## 六、可简化点（建议非强制性）

### S1. `_normalize_state` 可考虑处理空 `on` 清理

归一后如果 `on` 变为空 dict（如 `on: {done: xxx}` → `on: {}`），保持空 dict 而非 `None` 没有问题——`StateModel.on` 默认是 `field(default_factory=dict)`，空 dict 是合法值。不需要额外处理。

### S2. `from_dict` 中的字段读取顺序

`WorkflowConfig.from_dict()` 中创建 StateModel 时需增加 `next` 和 `on_status` 参数。由于 dataclass 有默认值，旧 snapshot 数据（不含这两个字段）恢复时自动回退到默认值，向后兼容。

### S3. 护栏 warning 的可见性

Guardrail 2 中"有 next 但 allowed_decisions 非空 → 警告"是合理的（存量 YAML 大量存在此模式），但建议在 warning message 中说明"allowed_decisions 将由引擎忽略（无 on 分支节点不需要 decision 约束）"，以便用户理解。

---

## 七、总体评价

| 维度 | 评估 | 说明 |
|---|---|---|
| **完整性** | ✅ | 14 项设计条款全覆盖，28 项测试覆盖 7 个维度 |
| **正确性** | ✅ | 两段式路由伪代码与设计文档一致，归一逻辑经存量 YAML 扫描验证无回退 |
| **测试覆盖** | ✅ | 单元测试覆盖全部行为分支，集成测试不改代码回归 |
| **风险** | ✅ | 已知风险均已缓解，无新增阻塞风险 |
| **复杂度** | ✅ | 改动集中在 7 个现有文件，无新增模块，范围适当 |
| **上一轮追踪** | ✅ | 2 个阻塞 + 10 个缺失 + 2 个优化，全部已处理 |

修订后的计划达到了可执行标准。所有从设计文档派生的需求都已映射到具体的代码修改步骤和测试用例。路由核心逻辑（6 分支两段式）与设计文档的伪代码高度一致。存量 YAML 兼容性通过 grep 扫描验证了零回退。

**建议在执行后重点验证**：
1. `listing-dev` workflow 的 `implement` 状态 blocked→audit 路由行为
2. loop 展开后的 states（如 spec-dev 的 plan_review_r1/_r2）路由行为
3. `explain` 输出格式中 next/on_status 的展示效果
