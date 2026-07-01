# Runtime v2 第 4 步：`_loop` 展开适配 + 新 YAML 范式 — 开发计划

## 1. 需求理解

### 1.1 目标

将 `_unroll_single_loop`（loader.py:185-304）的硬编码键名猜测逻辑改造为基于结构（`next` vs `on`）区分节点角色的新逻辑，与第 2 步已完成的 `_normalize_state` / 两段式路由对齐。

### 1.2 当前问题（代码分析）

第 2 步已引入 `_normalize_state`，将旧格式 YAML 的 `on={done, fail, blocked, approve, ...}` 归一化为：
- `done` → `next`（线性节点成功出口）
- `fail/blocked` → `on_status`（仅目标≠default 时写入）
- `approve/revise/reject` 保留在 `on`（分支节点业务决策）

`load_workflow` 先调用 `load_state`（内部调 `_normalize_state`），再调用 `_unroll_loops`。因此 `_unroll_single_loop` 收到的 `StateModel` 已经是归一化后的——但内部逻辑仍按旧格式编写：

| 行号 | 旧逻辑 | 问题 |
|------|--------|------|
| 260 | `on["done"] = next_in_round` | 硬编码 `done`，归一化后 `on` 中已无此键；应改设 `next` 字段 |
| 233 | `on.pop("revise", None)` | 硬编码 `revise` 作为"回跳键"；应通用判断"哪些 decision 指向循环内 state" |
| 237-238 | `if "approve" not in on: on["approve"] = on_break` | 硬编码 `approve` 作为"通过键"；应通用判断"哪些 decision 指向循环外" |
| 248-252 | `if not has_loop_back: on["revise"] = next_first` | 同上，硬编码 `revise` |
| 262-270 | 构建 `StateModel(on=on)` 但未设置 `next`/`on_status` | 展开后的 state 丢失归一化结构，变成半旧半新的混合体 |
| 274-297 | 外部 state 的 `fixed_on` 只处理 `on` 和 `default` | 缺少对 `next` 和 `on_status` 中循环引用的修正 |

### 1.3 改造核心思路

用**结构存在性**替代**键名猜测**：

- **线性节点**（`base_state.next` 非空）：展开时只需将 `next` 指向回合内的下一个 state；不操作 `on`
- **分支节点**（`base_state.on` 非空）：展开时只需判断 `on` 中每个 decision 的目标是否在循环内——在则回跳（指回下一轮首 state），不在则保留为通过/退出出口
- 最后一轮：所有指向循环内的 decision → `on_break`；保留指向循环外的通过出口
- 不再引入任何硬编码 decision 键名

### 1.4 验收标准（来自 step4-spec.txt）

1. `_unroll_single_loop` 改按 `next` vs `on` 区分节点角色，不再猜 `done/revise/approve` 键名
2. 不引入新字段到 `StateModel`，纯展开逻辑改动
3. 保留对旧 YAML（仅有 `on`，走 `_normalize_state` 归一化路径）的向后兼容
4. 存量测试全通过
5. `plan-review-advise-loop-example/workflow.yaml` 展开结果不变
6. 不得改 `StateModel` 字段、不得改路由逻辑

### 1.5 歧义点

- **`_loop` 块语法是否也要改？** step4-spec 只提 `_unroll_single_loop` 内部逻辑改造，未提 `_loop` 块本身的 YAML 语法变化。`_loop: {states, repeat, on_break}` 保持不变。
- **`loop_state_names` 中的 state 是否必须是同质节点？** 当前没有约束——线性/分支节点可混合在同一个 loop 内。改造后应支持。
- **最后一轮分支节点的 `on_break` 行为：** 当前所有指向循环内的 decision 在最后一轮被重定向到 `on_break`。这保留了"只能通过"的语义，应该是正确的，只需把硬编码键名改为通用逻辑。

---

## 2. 目标和非目标

### 2.1 目标（本次要做）

1. **改造 `_unroll_single_loop`**：用 `next`/`on` 结构区分线性/分支节点，消除硬编码 decision 键名
2. **补全展开后 StateModel 的字段**：展开后的 state 必须正确携带 `next`、`on_status`、`on`
3. **修正外部 state 的循环引用**：覆盖 `next` 和 `on_status` 中指向循环内 state 的引用
4. **新增混合场景测试**：新旧节点混合、纯新写法、纯旧写法（兼容验证）
5. **存量流程不变验证**：`plan-review-advise-loop-example` 展开结果不变

### 2.2 非目标（本次不做）

- 不改 `StateModel` 字段定义
- 不改 `resolve_transition` 路由逻辑
- 不改 `_normalize_state` 归一化规则
- 不改 `_loop` 块 YAML 语法
- 不改 `validate-state-machine` 护栏逻辑
- 不新增/修改 `_loops`（多循环）的逻辑——仅限 `_unroll_single_loop` 内部
- 不改 Agent/Parser/Validator/Runner 等其他模块

---

## 3. 涉及文件和模块边界

| 文件 | 操作 | 理由 |
|------|------|------|
| `src/agent_workflow/config/loader.py` | **修改** — `_unroll_single_loop` 函数（~120 行改造） | 核心改造目标：消除硬编码键名，改用结构分派 |
| `tests/unit/test_loop_unroll.py` | **修改** — 新增 6~8 个测试，更新 `_make_states` 辅助函数 | 覆盖混合场景、纯新写法、旧写法兼容、外部引用修正 |
| `workflows/plan-review-advise-loop-example/workflow.yaml` | **不动** | 存量流程，展开结果必须保持不变 |
| `tests/fixtures/` | **可能新增** — 1~2 个新格式 YAML 夹具 | 集成测试用的纯新写法 workflow 示例 |

**明确不动的文件：**
- `config/models.py`（StateModel 字段不变）
- `state_machine/machine.py`（路由逻辑不变）
- `state_machine/transition.py`（TransitionResult 不变）
- `validators/task_result.py`（校验逻辑不变）
- 其他模块

---

## 4. 分步骤实现方案

### Step 4a：重构 `_make_states` 辅助函数 + 先让存量测试通过（验证基线）

**目标**：将测试辅助函数 `_make_states` 改造为通过 `_normalize_state`/`load_state` 路径构造 states，使测试能反映真实的归一化后数据结构。然后验证存量测试在"归一化后的世界"中是否仍然通过。

**具体工作**：
- 在 `test_loop_unroll.py` 中新增一个 `_make_states_v2` 辅助函数，通过手动构建 `data` dict 再调 `_normalize_state` → `load_state` 路径构造 `StateModel`
- 为存量测试增加 `_make_states_v2` 版本的对应测试，验证存量逻辑在归一化后是否一致
- 如果存量测试有失败，分析是否是 `_unroll_single_loop` 的 bug（而非测试写法问题）

**验证方式**：`pytest tests/unit/test_loop_unroll.py -v` 全部通过

---

### Step 4b：改造 `_unroll_single_loop` 核心逻辑（线性节点 `next` 分派）

**目标**：非轮次最后 state（如 plan、review）改用 `next` 字段串接，不再写 `on["done"]`。

**具体改动**（loader.py:253-260 → 重写）：
```python
# 旧逻辑：
# on["done"] = next_in_round

# 新逻辑：
# if base_state.next:  # 线性节点
#     expanded_state.next = next_in_round
# elif base_state.on:  # 分支节点（不太常见，但支持）
#     for d in on: redirect loop-internal targets → next_in_round
```

**注意**：展开后的 `StateModel` 必须同时设置 `next`、`on_status`、`on`（从 base 复制并修正），使展开后 state 也是规范的归一化格式。

**验证方式**：现有线性展开测试通过 + 新增线性节点 `next` 字段验证

---

### Step 4c：改造 `_unroll_single_loop` 核心逻辑（分支节点 `on` 分派）

**目标**：轮次最后 state（如 advise）改用通用逻辑——按 decision 目标是否在 `loop_state_names` 中区分回跳/通过，不再硬编码 `approve`/`revise`。

**具体改动**（loader.py:229-252 → 重写）：

核心算法：
```python
for decision, target in list(on.items()):
    if target in loop_state_names or target == base_name:
        # 此 decision 是循环内回跳
        if is_last_round:
            on[decision] = on_break  # 最后一轮 → 退出
        else:
            on[decision] = next_first  # 前 N-1 轮 → 下一轮首 state
    # else: 此 decision 指向循环外，保留不变（即"通过/退出"出口）
```

**移除的逻辑**：
- `on.pop("revise", None)` — 不再硬编码
- `if "approve" not in on: on["approve"] = on_break` — 不再自动添加
- `if not has_loop_back: on["revise"] = next_first` — 不再自动添加

**新增考虑**：
- 如果分支节点没有任何 decision 指向循环内（都是外部出口），且非最后一轮——应该保留所有出口不变，不强制添加回跳
- 如果分支节点没有任何 decision 指向循环外（全是循环内），且非最后一轮——这是合法场景（必须走完所有轮次），所有 decision 重定向到下一轮

**验证方式**：现有分支展开测试通过 + 新增通用 decision 展开测试

---

### Step 4d：修正外部 state 的 `next`/`on_status` 循环引用

**目标**：`_unroll_single_loop` 中修正外部 state 引用时，覆盖 `next` 和 `on_status` 字段（当前只处理 `on` 和 `default`）。

**具体改动**（loader.py:274-297 → 扩展 `fixed_on` → 通用 `_fix_loop_refs` 辅助函数）：

为展开后的 `StateModel` 提取一个小函数：
```python
def _reroute_target(target, loop_state_names, suffix="_r1"):
    if target in loop_state_names:
        return f"{target}{suffix}"
    return target
```

然后在修正外部 state 时统一处理所有路由字段：
- `on`: 逐个 decision 修正
- `next`: 如果指向循环内 → 修正为 `_r1`
- `default`: 如果指向循环内 → 修正为 `_r1`
- `on_status`: 逐个 status 修正

**验证方式**：新增测试——外部 state 的 `next` 指向循环内时修正为 `_r1`

---

### Step 4e：新增混合场景单元测试

**目标**：覆盖 step4-spec.txt 第 2 条的所有场景。

**新增测试**（添加到 `TestUnrollLoopsUnit` 或新建类）：

| 测试 | 场景 | 验证点 |
|------|------|--------|
| `test_linear_node_next_chains` | 纯线性节点 loop（plan→review→execute，均用 `next`） | `plan_r1.next == "review_r1"`, `review_r1.next == "execute_r1"` |
| `test_branch_node_on_generic` | 纯分支节点 loop（review→advise，用自定义 decision） | 不依赖 `approve`/`revise` 键名，通用 decision 均正确处理 |
| `test_mixed_linear_branch_loop` | 新旧节点混合（线性用 `next`，分支用 `on`） | 线性节点展开后 `next` 正确，分支节点展开后 `on` 正确 |
| `test_external_next_redirected` | 外部 state 的 `next` 指向循环内 state | `next` 修正为 `_r1` |
| `test_external_on_status_redirected` | 外部 state 的 `on_status.blocked` 指向循环内 | `on_status` 修正为 `_r1` |
| `test_branch_no_loop_back_decision` | 分支节点所有 decision 都指向循环外 | 所有出口保留，不强制添加回跳 |
| `test_pure_new_format_loop` | YAML 使用 `next`+`on` 新格式 + `_loop` | 展开后 state 全部是归一化格式 |
| `test_pure_old_format_loop` | YAML 使用旧格式 `on={done, approve, ...}` + `_loop` | 通过 `_normalize_state` 归一化后展开结果与旧逻辑一致 |

**验证方式**：`pytest tests/unit/test_loop_unroll.py -v` 全部通过

---

### Step 4f：存量流程不变验证

**目标**：确认 `plan-review-advise-loop-example/workflow.yaml` 展开结果不变。

**验证步骤**：
1. 用 `load_workflow` 加载该 YAML
2. 检查展开后的 state 数量、名称、`initial_state` 与改造前一致
3. 检查 `advise_r1`、`advise_r2` 的 `on` 映射（approve/revise/reject 的目标）与改造前一致
4. 检查 `plan_r1`、`review_r1` 的链式关系与改造前一致
5. 检查 `wf.validate()` 返回无问题

**验证方式**：在现有集成测试 `test_load_workflow_with_loop` 中增加具体字段断言

---

### Step 4g：全量回归测试

**目标**：确保改造不改动任何现有行为。

```bash
# 1. 单元测试
pytest tests/unit/ -v

# 2. 集成测试
pytest tests/integration/ -v

# 3. 特定于 loop 的测试
pytest tests/unit/test_loop_unroll.py -v
```

---

## 5. 测试策略

### 5.1 存量测试保护

- `test_loop_unroll.py` 中 11 个单元测试 + 2 个集成测试必须全通过
- 可能需要微调存量测试（如 `_make_states` 辅助函数改造），但断言逻辑不变

### 5.2 新增测试覆盖点

| 覆盖维度 | 测试数量 | 关键断言 |
|----------|----------|----------|
| 线性节点 `next` 串接 | 2 | `next` 指向同轮下一个 state |
| 分支节点通用 `on` 回跳 | 3 | 回跳/通过按目标判定，不硬编码键名 |
| 混合节点 loop | 1 | 线性+分支同 loop 共存 |
| 外部引用修正（`next`/`on_status`） | 2 | 所有路由字段的循环引用均修正 |
| 旧格式兼容 | 1 | 旧 YAML 归一化后展开结果不变 |
| 存量流程不变 | 2 | `plan-review-advise-loop-example` 展开等价 |

### 5.3 测试基础设施

- 使用 `_make_states` 辅助函数（改造后）构造规范化 `StateModel` 对象
- 使用 `tempfile` + `load_workflow` 做集成加载验证
- 不依赖外部服务或 CLI

### 5.4 不测试的内容

- 不测试 `resolve_transition`（已在 step 2 覆盖）
- 不测试 `_normalize_state`（已在 step 2 覆盖）
- 不测试 Runner/Validator/Agent（不在本次范围）

---

## 6. 风险与停止规则

### 6.1 关键风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| **存量测试大量失败**：`_make_states` 改归一化路径后，存量断言依赖旧 `on` 结构（含 `done` 键） | 中 | 需逐个分析失败原因 | 分步改造：先加 v2 测试，确认行为等价后再改核心逻辑 |
| **`plan-review-advise-loop-example` 展开结果漂移**：通用化逻辑可能导致 `approve`/`revise` 的目标与硬编码版本不同 | 低 | 必须修正 | 集成测试精准断言每个展开 state 的 `on`/`next` 值 |
| **外部 state 的 `next` 引用未修正导致路由死循环**：当前代码未处理 `next` 的循环引用修正 | 中 | 运行时跳回未展开的 state 名 | Step 4d 专门覆盖，新增测试防御 |
| **`_normalize_state` 与 `_unroll_single_loop` 的隐式耦合**：归一化假设 `done→next`，展开也假设 `done` 在 `on` 中 | 高（当前已存在） | 展开后 state 字段不一致 | 改造后展开逻辑直接读 `next`/`on`，不再假设 `done` 在 `on` 中 |

### 6.2 停止规则

1. **如果 step 4a 中存量测试无法在归一化路径下通过**：停止，先分析 `_unroll_single_loop` 对归一化后数据的错误假设，记录到 issue
2. **如果 `plan-review-advise-loop-example` 展开结果无法等价**：停止，通用化逻辑可能有语义偏差，需手动审理每个 decision 的归属
3. **如果存量测试超过 5 个需要改动断言**：停止，说明改造范围超出预期，需重审方案
4. **如果改造超过 200 行代码**：停止，说明复杂度被低估，需拆分为更小步骤

---

## 7. 预期产物

### 7.1 代码产物

| 产物 | 路径 | 说明 |
|------|------|------|
| 改造后的 `_unroll_single_loop` | `src/agent_workflow/config/loader.py` | 消除硬编码键名，使用 `next`/`on` 结构分派 |
| 改造后的 `_unroll_loops` | 同上 | 可能微调以适配新逻辑 |
| 新增辅助函数 | 同上 | `_reroute_target` 或内联逻辑，统一循环引用修正 |
| 更新后的测试 | `tests/unit/test_loop_unroll.py` | 新增 6~8 个测试 + 辅助函数改造 |

### 7.2 验证产物

- 全量单元测试通过报告
- `plan-review-advise-loop-example` 展开结果等价验证截图/输出
- 存量 workflow YAML 列表及兼容性确认

### 7.3 文档产物

- 本计划文档（即本文档）
- 如有需要，补充 loader.py 中 `_unroll_single_loop` 的 docstring（更新逻辑说明）

---

## 附录：关键代码位置速查

| 位置 | 行号 | 内容 |
|------|------|------|
| `loader.py:_normalize_state` | 118-141 | 旧格式归一化（done→next, fail/blocked→on_status） |
| `loader.py:load_state` | 144-157 | 调 `_normalize_state` 后构造 StateModel |
| `loader.py:_unroll_single_loop` | 185-304 | 本次核心改造目标 |
| `loader.py:_unroll_loops` | 307-355 | `_unroll_single_loop` 的调用者 |
| `loader.py:load_workflow` | 358-431 | 加载流程（先 normalize → 后 unroll） |
| `models.py:StateModel` | 57-99 | StateModel 定义（next/on/on_status/default） |
| `test_loop_unroll.py:_make_states` | 17-27 | 测试辅助函数（直接构造 StateModel，未走 normalize） |
| `test_loop_unroll.py:TestUnrollLoopsUnit` | 112-323 | 11 个单元测试 |
| `test_loop_unroll.py:TestUnrollLoopsIntegration` | 328-387 | 2 个集成测试 |
| `plan-review-advise-loop-example/workflow.yaml` | 全文件 | 存量 loop 流程示例（旧格式 `on={done, approve, ...}`） |
