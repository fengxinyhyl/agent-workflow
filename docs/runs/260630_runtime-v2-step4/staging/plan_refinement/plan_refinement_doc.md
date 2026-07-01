# Runtime v2 第 4 步：`_loop` 展开适配 + 新 YAML 范式 — 修订后开发计划

> 基于 plan_review_doc (decision: revise) 修订，逐条回应审核意见。

---

## 0. 审核意见逐条回应

### 0.1 Blocking 问题

#### B1：`advise_r2` 展开结果漂移 — **已采纳**

**审核意见**：移除 `on.pop("revise", None)` 后，`advise_r2.on` 会从 `{approve: execute, reject: failed}` 变为 `{approve: execute, reject: failed, revise: execute}`，违反"展开结果不变"验收标准。

**采纳方案**：保留最后一轮**删除**循环内 decision 的行为，但改为通用逻辑：

```python
# 旧逻辑（硬编码 revise 键名）：
on.pop("revise", None)

# 新逻辑（通用：删除所有指向循环内的 decision）：
for decision, target in list(on.items()):
    if target in loop_state_names or target == base_name:
        del on[decision]
```

**理由**：
1. 删除是更合理的语义——最后一轮没有"回跳到下一轮"的路由，指向循环内 state 的 decision 应被移除，落在 `default` 上
2. 改动是通用逻辑（不硬编码 `revise`），适用于任意 decision 词表
3. 对 `plan-review-advise-loop-example`，`advise_r2.on` 字面量完全不变：`{approve: execute, reject: failed}`
4. 保证了 step4-spec 第 3 条"展开结果不变"的字面验收标准

**额外保障**：如果删除后 `on` 变为空（所有 decision 都指向循环内），则自动添加一个退出出口指向 `on_break`，避免在最后一轮出现"无处可去"的死胡同。这替代了旧逻辑的 `if "approve" not in on: on["approve"] = on_break`，但不硬编码 `approve` 键名。

**计划修改**：
- Step 4c 伪代码更新：最后一轮 → 删除循环内 decision；非最后一轮 → 重定向到 `next_first`
- Step 4f 验证断言保持字面对比（因为展开结果现在确实不变）

---

#### B2：展开后 StateModel 构造缺失 `next`/`on_status` 字段 — **已采纳**

**审核意见**：当前代码构造 `StateModel` 时只传 `on` 和 `default`，未传 `next` 和 `on_status`。计划 Step 4b 只提了 `next`，未提 `on_status`；Step 4c 只讨论 `on`；Step 4d 只在外层修复中提 `on_status`。

**采纳方案**：完整写出 8 字段 StateModel 构造逻辑，并编写统一的引用修正函数：

```python
def _reroute_state_refs(
    state: StateModel,
    loop_state_names: set[str],
    reroute_map: dict[str, str],  # 原 target → 新 target
) -> StateModel:
    """根据 reroute_map 修正 state 的所有路由字段引用。

    处理字段：next, on, on_status, default。
    不修改 name, task, description, terminal, gate。
    """
    def _fix(target: str) -> str:
        return reroute_map.get(target, target)

    return StateModel(
        name=state.name,
        task=state.task,
        next=_fix(state.next) if state.next else "",
        on={d: _fix(t) for d, t in (state.on or {}).items()},
        on_status={s: _fix(t) for s, t in (state.on_status or {}).items()},
        default=_fix(state.default),
        description=state.description,
        terminal=state.terminal,
        gate=state.gate,
    )
```

**计划修改**：
- Step 4b/4c 增加完整的 StateModel 构造代码，显式列出 8 个字段
- Step 4d 改造为统一的 `_reroute_state_refs` 辅助函数
- 新增专门测试：`on_status` 中循环引用的修正

---

### 0.2 可简化点

#### S1：`_make_states_v2` 路径迂回 — **已采纳**

**审核意见**：通过 `_normalize_state` → `load_state` 构造测试 StateModel 引入不必要间接层。

**采纳方案**：直接在测试中构造归一化后的 StateModel：

```python
# 线性节点（归一化后，next 非空，on 为空）
StateModel(name="review", task="review", next="advise", default="failed")

# 分支节点（归一化后，on 包含业务词）
StateModel(name="advise", task="advise",
    on={"approve": "execute", "revise": "review", "reject": "failed"},
    default="failed")
```

**保留**：1 个集成测试走 `load_workflow` 验证完整归一化链路即可。

**计划修改**：Step 4a 简化，删除 `_make_states_v2` 概念，改为直接在 `_make_states` 中支持两种构造方式。

---

#### S2：Step 4a + 4b + 4c 可合并 — **已采纳**

**审核意见**：分三步的边界不清，4a 大概率直接通过。

**采纳方案**：合并为两阶段：

- **Phase 1（改造 + 测试）**：一次性改造 `_unroll_single_loop` 核心逻辑、修复 StateModel 构造、编写 `_reroute_state_refs` 辅助函数、新增全部单元测试
- **Phase 2（验证 + 回归）**：存量流程不变验证、全量回归测试

**计划修改**：重写 §4 分步骤方案，从 7 步缩减为 2 个 Phase + 最终回归。

---

#### S3：`_reroute_target` 改名为 `_reroute_state_refs` — **已采纳**

**审核意见**：建议统一为一次调用处理 `next`、`on`、`on_status`、`default` 四个字段。

**采纳方案**：如 B2 回应所述，提取 `_reroute_state_refs(state, loop_state_names, reroute_map)` 函数，同时用于 loop 内部 state 展开和外部 state 引用修正两处。

---

### 0.3 缺失测试 — **全部已采纳**

| # | 测试 | 优先级 | 采纳说明 |
|---|------|--------|----------|
| 1 | `test_loop_state_with_on_status_redirected` | 🔴 高 | 纳入 Step 4e 测试列表，增加 loop 内 state 有 `on_status` 的场景 |
| 2 | `test_external_next_preserved_when_not_pointing_to_loop` | 🟡 中 | 纳入 Step 4e，验证外部 state 的 `next` 指向循环外时保持不变 |
| 3 | `test_multi_loop_survival` | 🟡 中 | 纳入 Step 4e，简化为 `_loops` 数组含两个 1 轮循环的 smoke test |
| 4 | `test_normalized_on_empty_next_nonempty` | 🔴 高 | 纳入 Step 4e，这是实际生产中最常见场景——线性节点归一化后 `on={}` 且 `next` 非空 |

---

### 0.4 风险项补充 — **已采纳**

**审核意见**：在 `_unroll_single_loop` docstring 中声明归一化前置条件。

**采纳方案**：更新 docstring：

```python
def _unroll_single_loop(
    resolved: dict[str, Any],
    states: dict[str, StateModel],
    loop_block: dict[str, Any],
) -> dict[str, StateModel]:
    """展开单个 _loop 块为线性 state 序列。

    前置条件（调用者责任）：
    - states 中的每个 StateModel 必须已经过 _normalize_state 归一化，
      即 done→next、fail/blocked→on_status、业务词保留在 on
    - 此函数依赖 next/on/on_status 的三段式结构来区分节点角色

    展开规则：
    - 线性节点（state.next 非空）：next 指向同轮下一个 state
    - 分支节点（state.on 非空）：遍历 on 的每项 → 在循环内则回跳，在循环外则保留
    - 最后一轮：所有循环内 decision 删除（落 default），确保不产生无意义回跳
    """
```

---

### 0.5 覆盖缺失 — **已采纳**

**审核意见**：loop 内 state 有 `on_status` 时未讨论处理规则。

**采纳方案**：定义 `on_status` 中循环引用的处理规则：
- **非最后一轮**：`on_status` 中指向循环内的 target → 重定向到下一轮首 state（`next_first`）。例如 `on_status: {blocked: review}` 展开后变为 `{blocked: review_rN+1}`
- **最后一轮**：`on_status` 保留不变。`on_status` 中的 blocked/failed 是引擎级路由，即使指向循环内 state 也不删除——在最后一轮，该 state 已被展开为 `_rN` 版本，引用仍然有效
- 同时处理 `default` 字段中的循环引用（全部轮次统一修正）

---

## 1. 需求理解

### 1.1 目标

将 `_unroll_single_loop`（loader.py:185-304）的硬编码键名猜测逻辑改造为基于结构（`next` vs `on`）区分节点角色的新逻辑，与第 2 步已完成的 `_normalize_state` / 两段式路由对齐。

### 1.2 核心改造思路

用**结构存在性**替代**键名猜测**：

- **线性节点**（`base_state.next` 非空）：展开时将 `next` 指向回合内的下一个 state；不操作 `on`
- **分支节点**（`base_state.on` 非空）：展开时判断 `on` 中每个 decision 的目标是否在循环内——在则回跳（指回下一轮首 state），不在则保留为通过/退出出口
- **最后一轮分支节点**：删除所有指向循环内的 decision（通用逻辑，不硬编码键名）。若删除后 `on` 为空，自动添加一个出口指向 `on_break`
- `on_status` / `default` 中的循环引用：非最后一轮修正为 `_rN` 版本；最后一轮保留不变（因展开后的 state 名已更新）

### 1.3 验收标准（来自 step4-spec.txt）

| # | 标准 | 覆盖 |
|---|------|------|
| 1 | `_unroll_single_loop` 改按 `next`/`on` 区分节点角色 | Phase 1 |
| 2 | 新增混合场景测试（新旧混合、纯新、纯旧） | Phase 1 |
| 3 | `plan-review-advise-loop-example` 展开结果不变 | Phase 2 |
| 4 | 存量测试全通过 | Phase 2 |
| 5 | 不得改 StateModel 字段 | 非目标 |
| 6 | 不得改路由逻辑 | 非目标 |
| 7 | 保留旧 YAML 向后兼容 | Phase 1 |

### 1.4 歧义点（已澄清）

- **`_loop` 块语法**：不变（`states`/`repeat`/`on_break` 三个 key）
- **loop 内混合节点**：支持（线性+分支可共存于同一 loop）
- **`on_status` 循环引用**：非最后一轮→重定向，最后一轮→保留不变
- **最后一轮分支节点行为**：删除循环内 decision（通用逻辑），必要时补出口→`on_break`

---

## 2. 目标和非目标

### 2.1 目标（本次要做）

1. **改造 `_unroll_single_loop`**：用 `next`/`on` 结构区分线性/分支节点，消除硬编码 decision 键名
2. **补全展开后 StateModel 的完整 8 字段**：`next`、`on_status`、`on`、`default`、`name`、`task`、`gate`、`terminal`
3. **提取 `_reroute_state_refs` 统一辅助函数**：统一处理 loop 内部展开 + 外部引用修正
4. **修正外部 state 的所有路由字段循环引用**：`next`、`on`、`on_status`、`default`
5. **新增 12 个单元测试**：覆盖纯线性、纯分支、混合、`on_status`、外部引用、旧格式兼容、多循环
6. **存量流程不变验证**：`plan-review-advise-loop-example` 展开结果字面等价

### 2.2 非目标（本次不做）

- 不改 `StateModel` 字段定义
- 不改 `resolve_transition` 路由逻辑
- 不改 `_normalize_state` 归一化规则
- 不改 `_loop` 块 YAML 顶层语法
- 不改 `validate-state-machine` 护栏逻辑
- 不新增/修改 `_unroll_loops`（多循环）的顶层逻辑——仅限 `_unroll_single_loop` 内部
- 不改 Agent/Parser/Validator/Runner 等其他模块

---

## 3. 涉及文件和模块边界

| 文件 | 操作 | 理由 |
|------|------|------|
| `src/agent_workflow/config/loader.py` | **修改** — `_unroll_single_loop`（~120 行改造）+ 新增 `_reroute_state_refs`（~25 行） | 核心改造目标 |
| `tests/unit/test_loop_unroll.py` | **修改** — `_make_states` 辅助函数改造 + 新增 12 个测试 | 覆盖全场景 |
| `workflows/plan-review-advise-loop-example/workflow.yaml` | **不动** | 存量流程，展开结果必须保持不变 |
| `tests/fixtures/` | **可能新增** — 1 个新格式 YAML 夹具 | 集成测试用纯新写法 workflow |

**明确不动的文件：**
- `config/models.py`（StateModel 字段不变）
- `state_machine/machine.py`（路由逻辑不变）
- `state_machine/transition.py`（TransitionResult 不变）
- `validators/task_result.py`（校验逻辑不变）

---

## 4. 分步骤实现方案

### Phase 1：核心改造 + 全面测试（一步到位）

#### Step 1a：编写 `_reroute_state_refs` 统一辅助函数

**位置**：`loader.py` 新增函数，位于 `_unroll_single_loop` 之前

**代码骨架**：

```python
def _reroute_state_refs(
    state: StateModel,
    loop_state_names: set[str],
    reroute_map: dict[str, str],
) -> StateModel:
    """根据 reroute_map 修正 StateModel 的所有路由字段引用。

    处理字段：next, on, on_status, default。
    不修改：name, task, description, terminal, gate。
    """
    def _fix(target: str) -> str:
        if not target:
            return ""
        return reroute_map.get(target, target)

    return StateModel(
        name=state.name,
        task=state.task,
        next=_fix(state.next),
        on={d: _fix(t) for d, t in (state.on or {}).items()},
        on_status={s: _fix(t) for s, t in (state.on_status or {}).items()},
        default=_fix(state.default),
        description=state.description,
        terminal=state.terminal,
        gate=state.gate,
    )
```

**验证方式**：单元测试中直接调用此函数，验证各字段修正正确。

---

#### Step 1b：改造 `_unroll_single_loop` 核心逻辑

**改造范围**：loader.py:229-270（展开 state 的 on 修正 + StateModel 构造）

**新逻辑**（伪代码）：

```python
for r in range(1, repeat + 1):
    for i, base_name in enumerate(loop_state_names):
        base_state = states[base_name]
        round_name = f"{base_name}_r{r}"

        on = dict(base_state.on) if base_state.on else {}
        on_status = dict(base_state.on_status) if base_state.on_status else {}

        is_last_state_in_round = (i == len(loop_state_names) - 1)
        is_last_round = (r == repeat)
        next_state: str = ""  # 线性节点的 next 目标

        if is_last_state_in_round:
            # ── 轮次最后一个 state（通常是分支/决策节点）──
            if is_last_round:
                # 最后一轮：删除所有指向循环内的 decision
                for decision, target in list(on.items()):
                    if target in loop_state_names or target == base_name:
                        del on[decision]
                # 安全保障：如果删除后 on 为空，添加 on_break 出口
                if not on and on_break:
                    # 保留原始 on 中第一个 decision 的键名作为出口键
                    # 如果原始 on 为空，使用 on_break 自身
                    pass  # 具体实现时再确定策略
            else:
                # 前 N-1 轮：所有指向循环内的 decision → 下一轮首 state
                next_first = f"{loop_state_names[0]}_r{r + 1}"
                for decision, target in list(on.items()):
                    if target in loop_state_names or target == base_name:
                        on[decision] = next_first
            # 分支节点不需要设置 next

            # on_status 处理：非最后一轮修正循环内引用
            if on_status and not is_last_round:
                next_first = f"{loop_state_names[0]}_r{r + 1}"
                for status_key, target in list(on_status.items()):
                    if target in loop_state_names:
                        on_status[status_key] = next_first

        else:
            # ── 非轮次最后一个 state（线性节点或提前的分支节点）──
            next_in_round = f"{loop_state_names[i + 1]}_r{r}"
            if base_state.next:
                # 线性节点：next 指向同轮下一个 state
                next_state = next_in_round
            if on:
                # 分支节点（不常见但支持）：所有指向循环内的 → 同轮下一个
                for decision, target in list(on.items()):
                    if target in loop_state_names:
                        on[decision] = next_in_round

            # on_status 处理同上（非最后一轮修正循环内引用）
            if on_status:
                for status_key, target in list(on_status.items()):
                    if target in loop_state_names:
                        on_status[status_key] = next_in_round

        # 修正 default：如果指向循环内 → 修正为 _r 版本
        default = base_state.default
        if default in loop_state_names and default != base_name:
            # default 指向同循环内另一个 state → 修正为 _r 版本
            default = f"{default}_r{r}"

        # ── 完整 8 字段 StateModel 构造 ──
        expanded[round_name] = StateModel(
            name=round_name,
            task=base_state.task,
            on=on,
            next=next_state,
            on_status=on_status,
            default=default,
            description=f"{base_state.description or base_name} (第 {r} 轮)",
            terminal=False,  # 展开 state 永不为 terminal
            gate=base_state.gate,
        )
```

**关键变化 vs 旧逻辑**：

| 方面 | 旧逻辑 | 新逻辑 |
|------|--------|--------|
| 线性节点串接 | `on["done"] = next_in_round` | `next = next_in_round`（`on` 不动） |
| 分支节点回跳 | `on.pop("revise", None)` + 硬编码 `approve` | 通用：遍历 `on`，循环内 target→修正 |
| 最后一轮 | `on.pop("revise")` + redirect others to on_break | 通用：删除所有循环内 decision |
| StateModel 构造 | 只传 `on, default, name, task, description, terminal, gate`（缺 `next`, `on_status`） | 传全部 8 个字段 |
| `on_status` 处理 | 完全忽略 | 非最后一轮修正循环内引用 |

---

#### Step 1c：改造外部 state 的循环引用修正

**改造范围**：loader.py:272-297（`final_states` 构造）

**旧逻辑**：
- 只修正 `on` 和 `default` 中的循环引用
- 构造外部 state 时丢失 `next`、`on_status`、`gate`、`terminal` 字段

**新逻辑**（伪代码）：

```python
final_states: dict[str, StateModel] = {}
# 构建修正映射：循环内 state → _r1 版本
reroute_map = {name: f"{name}_r1" for name in loop_state_names}

for name, state in states.items():
    if name not in loop_state_names:
        # 使用统一辅助函数修正所有路由字段
        final_states[name] = _reroute_state_refs(
            state, set(loop_state_names), reroute_map
        )
    # 循环体内的原始 state 被展开版本替换
final_states.update(expanded)
```

---

#### Step 1d：更新 `_unroll_single_loop` docstring

声明归一化前置条件（见 §0.4 完整内容），确保未来维护者不会错误调整调用顺序。

---

#### Step 1e：改造测试辅助函数 + 新增 12 个单元测试

**改造 `_make_states`**：支持设置 `next`/`on`/`on_status`，直接构造归一化后的 StateModel：

```python
def _make_states(names: list[str], *, with_on: dict | None = None,
                 with_next: dict | None = None,
                 with_on_status: dict | None = None) -> dict[str, StateModel]:
    """构造归一化后的模拟 states。
    
    线性节点示例：_make_states(["review"], with_next={"review": "advise"})
    分支节点示例：_make_states(["advise"], with_on={"advise": {"approve": "execute", ...}})
    """
```

**新增 12 个测试**（在原 11 个基础上）：

| # | 测试方法 | 场景 | 验证点 |
|---|---------|------|--------|
| 1 | `test_linear_node_next_chains` | 纯线性节点 loop（`next` 非空，`on={}`） | `plan_r1.next == "review_r1"`，`review_r1.next == "execute_r1"` |
| 2 | `test_linear_node_on_empty_next_nonempty` | 归一化后 `on={}` `next="advise"` 的线性节点展开（最常见生产场景） | `next` 正确串接，`on` 保持为空 |
| 3 | `test_branch_node_on_generic` | 纯分支节点 loop（用自定义 decision 词如 `retry`/`skip`/`merge`） | 不依赖 `approve`/`revise` 键名，通用 decision 均正确处理 |
| 4 | `test_branch_last_round_deletes_loop_decisions` | 分支节点最后一轮：所有指向循环内的 decision 被删除 | `on` 中无循环内 target，保留外部 target |
| 5 | `test_mixed_linear_branch_loop` | 新旧节点混合（线性用 `next`，分支用 `on`） | 线性节点 `next` 正确，分支节点 `on` 正确 |
| 6 | `test_loop_state_with_on_status_redirected` | loop 内 state 有 `on_status: {blocked: review}` | `blocked` 目标在非最后一轮被修正为 `review_rN+1` |
| 7 | `test_external_next_redirected` | 外部 state 的 `next` 指向循环内 state | `next` 修正为 `_r1` |
| 8 | `test_external_next_preserved_when_not_pointing_to_loop` | 外部 state 的 `next` 指向循环外 | `next` 保持原样不被误修改 |
| 9 | `test_external_on_status_redirected` | 外部 state 的 `on_status.blocked` 指向循环内 | `on_status` 修正为 `_r1` |
| 10 | `test_branch_no_loop_back_decision` | 分支节点所有 decision 都指向循环外 | 所有出口保留，不强制添加/删除 |
| 11 | `test_pure_old_format_loop` | YAML 使用旧格式 `on={done, approve, ...}` + `_loop` | 通过 `_normalize_state` 归一化后展开结果与旧逻辑一致 |
| 12 | `test_multi_loop_survival` | `_loops` 数组含两个循环的 smoke test | 两个循环分别正确展开 |

---

### Phase 2：存量验证 + 回归测试

#### Step 2a：`plan-review-advise-loop-example` 展开结果等价验证

**验证步骤**：

1. 用 `load_workflow` 加载 `workflows/plan-review-advise-loop-example/workflow.yaml`
2. 检查展开后 state 数量 = 7（plan + review_r1 + advise_r1 + review_r2 + advise_r2 + execute + summary）+ 3 个 terminal（done/failed/cancelled）= 10
3. 关键字段断言：

| State | 字段 | 期望值 |
|-------|------|--------|
| `plan` | `next` (归一化后) | `review_r1` |
| `review_r1` | `next` | `advise_r1` |
| `advise_r1` | `on` | `{approve: execute, revise: review_r2, reject: failed}` |
| `review_r2` | `next` | `advise_r2` |
| `advise_r2` | `on` | `{approve: execute, reject: failed}`（revise 已删除） |
| `execute` | `next` | `summary` |

4. 确认 `wf.validate()` 返回无问题

**验证方式**：在现有集成测试 `test_load_workflow_with_loop` 中增加具体字段断言。

---

#### Step 2b：全量回归测试

```bash
# 1. 单元测试
pytest tests/unit/ -v

# 2. 集成测试
pytest tests/integration/ -v

# 3. 特定于 loop 的测试
pytest tests/unit/test_loop_unroll.py -v
```

**验收标准**：所有存量测试通过，"23 tests passed"（原 11 个存量 + 12 个新增）。

---

## 5. 测试策略

### 5.1 存量测试保护

- `test_loop_unroll.py` 中 11 个单元测试 + 2 个集成测试必须全通过
- 可能需要微调存量测试的 `_make_states` 辅助函数，但断言逻辑不变
- 存量测试的 base state 多为 `on={}`、`next=""`（旧构造方式）→ 归一化后同样为空，行为不变

### 5.2 新增测试覆盖点

| 覆盖维度 | 测试数量 | 关键断言 |
|----------|----------|----------|
| 线性节点 `next` 串接 | 2 | `next` 指向同轮下一个 state |
| 分支节点通用 `on` 回跳 | 2 | 回跳/通过按目标判定，不硬编码键名 |
| 分支节点最后一轮删除 | 1 | 循环内 decision 被删除，外部保留 |
| 混合节点 loop | 1 | 线性+分支同 loop 共存 |
| `on_status` 循环引用修正 | 1 | loop 内 `on_status` 引用被正确修正 |
| 外部引用修正（`next`/`on_status`） | 3 | 所有路由字段的循环引用均修正，非循环引用保留 |
| 旧格式兼容 | 1 | 旧 YAML 归一化后展开结果不变 |
| 多循环 smoke test | 1 | `_loops` 数组两个循环正确展开 |
| **合计** | **12** | |

### 5.3 测试基础设施

- 使用改造后的 `_make_states` 直接构造归一化 `StateModel`
- 1 个集成测试走完整 `load_workflow`（含 YAML→normalize→unroll 全链路）
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
| **存量测试大量失败**：存量 `_make_states` 改造后断言依赖旧 `on` 结构 | 低 | 需逐个分析 | 存量 base state 本就 `on={}`/`next=""`，归一化后一致；只需调整 `_make_states` 入参 |
| **`plan-review-advise-loop-example` 展开结果漂移**：B1 已通过"最后一轮删除"策略解决 | 低 | 必须修正 | 字面断言 `advise_r2.on` = `{approve: execute, reject: failed}` |
| **外部 state 的 `next` 引用未修正导致路由死循环** | 中 | 运行时跳回未展开的 state 名 | Step 1c 通过 `_reroute_state_refs` 统一处理，新增 3 个外部引用测试 |
| **`on_status` 循环引用处理遗漏** | 中 | `blocked` 跳转到不存在的 state | Step 1b 显式处理 `on_status`，新增测试 #6 |
| **`_normalize_state` 与 `_unroll_single_loop` 的隐式耦合** | 中 | 调用顺序变更导致静默错误 | Step 1d 在 docstring 声明前置条件 |

### 6.2 停止规则

1. **如果 `plan-review-advise-loop-example` 展开结果无法等价**：停止，重新审理删除逻辑的覆盖范围
2. **如果存量测试超过 3 个需要改动断言**：停止，说明改造范围超出预期，需重审方案
3. **如果改造代码（不含测试）超过 150 行**：停止，说明复杂度被低估
4. **如果 `_reroute_state_refs` 无法统一两处调用**：停止，分拆为两个独立函数

---

## 7. 预期产物

### 7.1 代码产物

| 产物 | 路径 | 说明 |
|------|------|------|
| `_reroute_state_refs` 新函数 | `src/agent_workflow/config/loader.py` | 统一修正 StateModel 的路由字段引用（~25 行） |
| 改造后的 `_unroll_single_loop` | 同上 | 消除硬编码键名，使用 `next`/`on` 结构分派（~100 行重写） |
| 改造后的外部 state 修正逻辑 | 同上 | 改为调用 `_reroute_state_refs`（~10 行） |
| 更新后的 docstring | 同上 | 声明归一化前置条件 |
| 更新后的测试 | `tests/unit/test_loop_unroll.py` | 改造 `_make_states` + 新增 12 个测试 |

### 7.2 验证产物

- 全量单元测试通过报告（23 tests passed）
- `plan-review-advise-loop-example` 展开字段断言通过
- 存量 workflow YAML 兼容性确认

### 7.3 文档产物

- 本修订后计划文档（即本文档）
- `_unroll_single_loop` 的更新后 docstring

---

## 8. 相对 plan_doc v1 的关键变更说明

| 变更 | 原计划 (v1) | 修订后计划 (v2) | 触发项 |
|------|------------|----------------|--------|
| 最后一轮分支节点处理 | 重定向循环内 decision 到 `on_break` | **删除**循环内 decision（落 default） | B1 |
| StateModel 构造 | 只提"要设置 next/on_status" | 完整 8 字段构造代码 | B2 |
| 统一辅助函数 | `_reroute_target`（只修正单个 target） | `_reroute_state_refs`（一次处理 4 个字段） | B2 + S3 |
| 步骤结构 | 7 个 Step（4a~4g） | 2 个 Phase（Phase 1: 改造+测试, Phase 2: 验证+回归） | S2 |
| `_make_states_v2` | 通过 normalize 链路构造 | 直接构造归一化 StateModel | S1 |
| `on_status` 处理 | 仅在 Step 4d 外部引用中提及 | Step 1b 核心逻辑中显式处理 | B2 + 覆盖缺失 |
| 测试数量 | 6~8 个新增 | 12 个新增（含 `on_status`、外部引用保留、多循环等） | 缺失测试 |
| docstring | 无要求 | 声明归一化前置条件 | 风险项 |
| 代码行数上限 | 200 行 | 150 行（不含测试） | 收紧停止规则 |
| 存量测试断言改动上限 | 5 个 | 3 个 | 收紧停止规则 |

---

## 附录 A：关键代码位置速查

| 位置 | 行号 | 内容 |
|------|------|------|
| `loader.py:_normalize_state` | 118-141 | 旧格式归一化 |
| `loader.py:load_state` | 144-157 | 构造 StateModel（调 normalize） |
| `loader.py:_unroll_single_loop` | 185-304 | **核心改造目标** |
| `loader.py:_unroll_loops` | 307-355 | 调用者（不变） |
| `loader.py:load_workflow` | 358+ | 加载流程 |
| `models.py:StateModel` | 57-99 | 8 字段定义 |
| `test_loop_unroll.py:_make_states` | 17-27 | 测试辅助函数（需改造） |
| `test_loop_unroll.py:TestUnrollLoopsUnit` | 112-323 | 11 个存量单元测试 |
| `test_loop_unroll.py:TestUnrollLoopsIntegration` | 328-387 | 2 个存量集成测试 |
| `plan-review-advise-loop-example/workflow.yaml` | 全文件 | 存量 loop 流程示例 |

## 附录 B：`_reroute_state_refs` 完整签名与行为表

```
_reroute_state_refs(state, loop_state_names, reroute_map) → StateModel
```

| 输入字段 | 处理方式 | 示例 |
|----------|----------|------|
| `next` | 若目标在 `loop_state_names` → 查 `reroute_map` 映射 | `next="review"` → `"review_r1"` |
| `on` | 每个 decision 的目标逐个修正 | `on={approve: execute, revise: review}` → `{approve: execute, revise: review_r1}` |
| `on_status` | 每个 status 的目标逐个修正 | `on_status={blocked: review}` → `{blocked: review_r1}` |
| `default` | 直接查映射 | `default="review"` → `"review_r1"` |
| `name/task/description/gate/terminal` | **不处理**（原样保留） | |
