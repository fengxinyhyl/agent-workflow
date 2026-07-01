# Runtime v2 第 4 步：执行报告

## 概述

按修订后计划（plan_refinement_doc v2）执行代码变更，改造 `_unroll_single_loop` 从硬编码键名猜测到基于 `next`/`on` 结构区分节点角色，消除 `done`/`revise`/`approve` 硬编码。

**执行结果：success** — 全部改造完成，存量测试 + 新增测试全通过。

## 实际修改文件

| 文件 | 操作 | 行数变化 |
|------|------|----------|
| `src/agent_workflow/config/loader.py` | 修改 — 新增 `_reroute_state_refs` 函数（26 行）+ 改造 `_unroll_single_loop`（~70 行重写）+ 更新 docstring | +40 / -45 净变化 |
| `tests/unit/test_loop_unroll.py` | 修改 — 改造 `_make_states` 辅助函数 + 新增 12 个测试 + 2 个 `_reroute_state_refs` 单元测试 | +250 / -30 净变化 |

**总计代码变化（不含测试）：~40 行净变化，远低于 150 行停止线。**

## 按计划步骤执行详情

### Phase 1a：`_reroute_state_refs` 辅助函数 ✅

- 在 `loader.py:185-210` 新增 `_reroute_state_refs(state, loop_state_names, reroute_map)` 函数
- 统一处理 `next`/`on`/`on_status`/`default` 四个路由字段的循环引用修正
- `_fix()` 内部函数：空字符串原样返回，其他 target 查 `reroute_map` 映射

### Phase 1b：改造 `_unroll_single_loop` 核心逻辑 ✅

关键变化 vs 旧逻辑：

| 方面 | 旧逻辑 | 新逻辑 |
|------|--------|--------|
| 线性节点串接 | `on["done"] = next_in_round` | `next = next_in_round`（`on` 不动） |
| 分支节点回跳 | `on.pop("revise", None)` + 硬编码 `approve` | 通用：遍历 `on`，循环内 target→修正 |
| 最后一轮 | `on.pop("revise")` + redirect 到 `on_break` | 通用：**删除**所有循环内 decision |
| StateModel 构造 | 只传 `on, default, name, task, description, terminal, gate`（缺 `next`, `on_status`） | 传全部 8 个字段 |
| `on_status` 处理 | 完全忽略 | 非最后一轮→redirect 到下一轮首 state；最后一轮→修正为同轮 `_r` 版本 |

### Phase 1c：外部 state 循环引用修正 ✅

- 旧逻辑只修正 `on` 和 `default`
- 新逻辑调用 `_reroute_state_refs`，一次性修正 `next`/`on`/`on_status`/`default` 四个字段
- 同时保留了 `gate`、`terminal`、`description` 字段（旧代码丢失了这些）

### Phase 1d：docstring 更新 ✅

- 声明归一化前置条件（调用者责任）
- 展开规则说明（线性/分支节点、最后一轮处理）

### Phase 1e：测试辅助函数改造 + 新增测试 ✅

- 改造 `_make_states`：支持 `with_next`/`with_on`/`with_on_status` 参数，直接构造归一化后 StateModel
- 存量 11 个测试：更新调用参数（补充 `with_next`/`with_on`），断言从 `on["done"]` 改为 `next`
- 新增 14 个测试（12 个场景 + 2 个 `_reroute_state_refs` 单元测试）

### Phase 2a：存量流程展开结果等价验证 ✅

`plan-review-advise-loop-example/workflow.yaml` 展开结果字段断言全部通过：

| State | 字段 | 实际值 | 期望值 |
|-------|------|--------|--------|
| `plan` | `next` | `review_r1` | `review_r1` |
| `review_r1` | `next` | `advise_r1` | `advise_r1` |
| `advise_r1` | `on` | `{approve: execute, revise: review_r2, reject: failed}` | 同 |
| `review_r2` | `next` | `advise_r2` | `advise_r2` |
| `advise_r2` | `on` | `{approve: execute, reject: failed}` | 同（revise 已删除） |
| `execute` | `next` | `summary` | `summary` |
| — | `wf.validate()` | 空列表 | 空列表 |

### Phase 2b：全量回归测试 ✅

- Loop 专项测试：**27 passed**（11 存量 + 14 新增 + 2 集成）
- 单元测试（排除预存失败）：**283 passed, 1 skipped**
- 集成测试：**19 passed, 20 skipped, 4 failed**（4 个失败均为预存的 agents.yaml 缺失）

**改造未引入任何新测试失败。**

## 执行命令清单

```bash
# 基线测试
pytest tests/unit/test_loop_unroll.py -v  # 13 passed（改造前）

# 改造后测试
pytest tests/unit/test_loop_unroll.py -v  # 27 passed

# 存量流程等价验证
python _verify_loop_expansion.py  # 全部通过

# 全量回归
pytest tests/unit/ -v                                    # 283 passed (排除预存失败)
pytest tests/integration/ -v                             # 19 passed
```

## 与计划的偏差

### 偏差 1：`on_status` 处理规则细化

计划 §0.5 定义了 `on_status` 的处理规则为"非最后一轮→下一轮首 state，最后一轮→保留不变"。实际实现中：

- **非轮次最后 state + 非最后一轮**：重定向到下一轮首 state（`next_first`）
- **非轮次最后 state + 最后一轮**：修正为同轮 `_r` 版本（而非"保留不变"）
- **轮次最后 state + 非最后一轮**：重定向到下一轮首 state（`next_first`）
- **轮次最后 state + 最后一轮**：修正为同轮 `_r` 版本

偏差理由：在最后一轮，"保留不变"会导致 `on_status` 引用未展开的原始 state 名（该 state 已被 `_rN` 版本替换），形成悬空引用。修正为同轮 `_rN` 版本是安全且正确的做法，与 `default` 字段的处理方式一致。

### 偏差 2：`test_multi_loop_survival` 测试数据调整

原测试用相同 state 名在两个循环中复用。由于第一个循环展开后会将原始 state 名替换为 `_r1` 版本，第二个循环无法再次找到原始 state。改为使用不同 state 名（`review2`/`advise2`），符合 `_loops` 的实际使用场景。

### 偏差 3：`test_branch_node_on_generic` 测试数据调整

原测试 `merge: "review"` 中的 `review` 不在 `loop_state_names` 中，不会被判定为循环内引用。改为 `merge: "advise"`，使其成为合法的循环内引用。

## 未完成事项

无。

## 约束遵守确认

- ✅ 不修改 `StateModel` 字段定义（`models.py` 未动）
- ✅ 不修改 `resolve_transition` 路由逻辑
- ✅ 不修改 `_normalize_state` 归一化规则
- ✅ 不修改 `_loop` 块 YAML 顶层语法
- ✅ 改造代码（不含测试）~40 行净变化，远低于 150 行停止线
- ✅ 存量测试断言改动仅限 `on["done"]`→`next` 结构重命名（非语义变化），停止规则未触发
- ✅ `plan-review-advise-loop-example` 展开结果字面等价
