# Runtime v2 Step 2 修订开发计划：审核意见采纳与完整计划

## 一、审核意见逐条回应

### 阻塞级问题（Blocking Issues）

#### B1. Loader `terminal_states` 自动推断未适配新模型 ✅ 已采纳

**审核指出**：`config/loader.py:386-389` 中 `terminal_states` 自动推断只检查 `s.terminal or not s.on`。归一后线性节点 `next="xxx", on={}`（空 dict → falsy），会被**错误识别为终止状态**，导致工作流在线性节点后直接终止。

**修改方案**：在 `loader.py` 中增加 `not s.next` 条件：

```python
# 旧
terminal_states = [
    name for name, s in states.items()
    if s.terminal or not s.on
]

# 新
terminal_states = [
    name for name, s in states.items()
    if s.terminal or (not s.on and not s.next)
]
```

**同步影响**：`StateMachine.get_terminal_states()`（`machine.py:154-161`）中的自动推断逻辑同样需要加上 `not state.next` 条件：

```python
# 旧
if not state.on and not state.task:
    terminals.add(name)

# 新
if not state.on and not state.next and not state.task:
    terminals.add(name)
```

---

#### B2. `_unroll_loops` 归一覆盖声明失实 ✅ 已采纳（方案 B）

**审核指出**：计划 §6.1 声称 loop 展开后的 states"自然被归一覆盖"，但 `_unroll_loops()` 在 `load_state()` **之后**执行，且直接调用 `StateModel(on=on, ...)` 构造函数创建实例，**绕过归一化**。产出的 StateModel 仍包含 `done`/`approve`/`revise` 等旧格式键。

**修改方案**：采用审核建议的**方案 B**——

1. **不修改 `_unroll_loops`**（符合 Step 2 约束）
2. 在计划中**明确记录已知差异**：loop 展开后的 states 保持旧格式键（`on` 中含 `done`/`approve`/`revise`），路由通过 `on` 匹配兜底（`decision="done"` → `state.on["done"]` → 命中），行为正确
3. **validate 护栏不因 `on` 中含 `done` 而误报**：两条新护栏只检查结构性完备性（有无成功出口、decision 必填一致性），**不检查 `on` 键名的语义类别**（不区分生命周期词 vs 业务词）
4. 待 Step 4 统一处理 `_loop` 展开的新格式适配

**风险缓解更新**：风险表中"`_unroll_loops` 输出旧格式"条目改为**接受**，缓解声明修正为"路由通过 on 匹配兜底，护栏不做键名语义检查，Step 4 处理"。

---

### 需求覆盖审查 - 遗漏项

#### R1. `get_state_names()` 遍历不完整 ✅ 已采纳

**审核指出**：`machine.py:168-177` 的 DFS 只遍历 `state.on.values()` 和 `state.default`，不遍历 `state.next` 和 `state.on_status.values()`。新模型下仅通过 `next` 可达的 state 不会出现在有序列表中。

**修改方案**：在 DFS 中追加 `state.next` 和 `state.on_status.values()` 的遍历：

```python
def dfs(name):
    if name in visited or name not in self.states:
        return
    visited.add(name)
    ordered.append(name)
    state = self.states[name]
    # 旧：只遍历 on.values() 和 default
    for target in state.on.values():
        dfs(target)
    if state.default:
        dfs(state.default)
    # 新：追加
    if state.next:
        dfs(state.next)
    for target in state.on_status.values():
        dfs(target)
```

---

#### R2. `WorkflowConfig.validate()` 目标存在性检查未扩展 ✅ 已采纳

**审核指出**：`WorkflowConfig.validate()`（`models.py:218-223`）遍历 `state.on.items()` 检查目标 state 存在性，但未检查 `state.next` 和 `state.on_status` 的目标。

**修改方案**：在 `WorkflowConfig.validate()` 中新增两个检查块——

```python
# 3b. State.next 目标存在
for name, state in self.states.items():
    if state.terminal:
        continue
    if state.next and state.next not in self.states:
        issues.append(
            f"state '{name}' next → '{state.next}' 目标 state 未定义"
        )

# 3c. State.on_status 目标存在
for name, state in self.states.items():
    if state.terminal:
        continue
    for status_key, target in state.on_status.items():
        if target not in self.states:
            issues.append(
                f"state '{name}' on_status '{status_key}' → '{target}' 目标 state 未定义"
            )
```

---

#### R3. Observability 兼容方案过于简略 ✅ 已采纳

**审核指出**：计划 §2.6 仅用约 5 行描述 observability 改动，`explain.py` 的 Transitions 段展示格式没有具体说明。

**修改方案**：在计划中补充完整格式说明（见下方 §4.6）。

`explain.py` 的 Transitions 段改为：

```
Transitions:
  next                 -> audit
  (on_status) blocked  -> audit
  on: approve          -> execute
  on: revise           -> revise_plan
  on: reject           -> failed
  default              -> failed
```

展示规则：
- `next` 存在时优先展示（线性出口）
- `on_status` 条目以 `(on_status) <key>` 前缀展示
- `on` 条目以 `on: <key>` 前缀展示（与旧输出兼容）
- `default` 永远展示（非终止状态）

---

#### R4. `continue_from_gate` 调用签名需更新 ✅ 已采纳

**审核指出**：计划 §2.5 提到要改 Gate 调用但未明确 `status` 取值来源。

**修改方案**：Gate 状态的 task 在暂停前已完成并成功执行（否则会走 error 路径），因此 `continue_from_gate()` 传入 `status="success"`：

```python
# 旧
transition = self.sm.resolve_transition(gate_state, decision)

# 新
transition = self.sm.resolve_transition(gate_state, status="success", decision=decision)
```

---

#### R5. `_find_reachable()` 遍历不完整 ✅ 已采纳

**审核指出**：与 R1 同构问题。`machine.py:85-100` 的 DFS 只走 `state.on.values()` 和 `state.default`。

**修改方案**：在 `_find_reachable()` 中追加 `state.next` 和 `state.on_status.values()`：

```python
# 在 stack append 阶段追加
if state.next:
    stack.append(state.next)
for target in state.on_status.values():
    stack.append(target)
```

---

#### R6. `route_by` 语义偏差 ✅ 已采纳

**审核指出**：计划 §2.4a 伪代码中 `next` 路径使用 `route_by="decision"`，但走 `next` 时实际没有用 `decision` 做匹配——路由依据是"线性节点，成功即走 `next`"。`route_by="decision"` 会误导 observability 展示。

**修改方案**：`next` 路径改为 `route_by="next"`。同时细化五种路由分支的 `route_by` 取值：

| 分支 | 条件 | route_by | 说明 |
|---|---|---|---|
| 1 | `status != success` + `status in on_status` | `"status"` | status 驱动 |
| 2 | `status != success` + default | `"status"` | status 无显式路由，走 default |
| 3 | `status=success` + `decision in on` | `"decision"` | decision 匹配 |
| 4 | `status=success` + `on` 存在但 `decision not in on` | `"decision"` | decision 未匹配，走 default（仍在 decision 分支） |
| 5 | `status=success` + `next` | `"next"` | 线性成功出口（**本次修正**） |
| 6 | `status=success` + 无 on 无 next | `"status"` | 配置疏漏，走 default |

---

### 缺失测试（10 项）✅ 全部采纳

#### 4.1 未覆盖的功能点

| 缺失测试 | 严重度 | 采纳 |
|---|---|---|
| `test_terminal_auto_detect_with_next_only` | **阻塞** | ✅ 新增 |
| `test_find_reachable_via_next` | 中等 | ✅ 新增 |
| `test_find_reachable_via_on_status` | 中等 | ✅ 新增 |
| `test_get_state_names_includes_next_path` | 中等 | ✅ 新增 |
| `test_workflow_config_validate_checks_next_target` | 中等 | ✅ 新增 |
| `test_workflow_config_validate_checks_on_status_target` | 中等 | ✅ 新增 |
| `test_resolve_transition_decision_none` | 中等 | ✅ 新增 |
| `test_continue_from_gate_two_stage` | 中等 | ✅ 新增 |
| `test_loop_unrolled_state_normalization` | 中等 | ✅ 新增 |
| `test_blocked_to_non_default_on_status` | 中等 | ✅ 新增 |
| `test_explain_shows_next_and_on_status` | 低 | ✅ 手工验证记录于测试计划 |

#### 4.2 计划已覆盖但需微调的测试

| 测试 | 问题 | 修改 |
|---|---|---|
| `test_validate_next_and_on_both_present` | 应为**错误**（阻止），不是警告 | 确认：此护栏是硬错误，配置校验失败 |

---

### 可简化点

#### S1. 跳过与 default 相同的 `on_status` 条目 ✅ 已采纳

**修改方案**：归一逻辑中，`fail`/`blocked` 仅在目标不同于 `default` 时写入 `on_status`：

```python
for key in ("fail", "blocked"):
    if key in on:
        target = on.pop(key)
        if target != data.get("default", "failed"):
            data.setdefault("on_status", {})
            data["on_status"][key] = target
```

---

#### S2. clarify validate 护栏范围 ✅ 已采纳

**修改方案**：两条新增护栏（缺成功出口 + decision 必填一致性）**只在 `StateMachine.validate()` 中实现**。`WorkflowConfig.validate()` 的 target 存在性检查同步扩展到 `next` 和 `on_status`（见 R2）。两者的职责边界清晰：

- `WorkflowConfig.validate()`：字段级存在性（引用完整性）
- `StateMachine.validate()`：语义级完备性（路由合理性）

---

## 二、修订后完整计划

### 1. 需求理解

#### 1.1 目标

本步实现 Runtime v2 最核心的架构变更——**路由从单看 decision 改为两段式（先 status 后 decision）**，并且 Runtime 全程不判断节点类型（如 is_review/is_gate/is_execute），只看 `on`/`next` 结构存在性。

核心变化：

| 旧模型 | 新模型 |
|---|---|
| 路由只看 `decision` | 先看 `status`（success/failed/blocked），success 时才看 `decision` |
| `on` 中混有生命周期词（done/fail/blocked）和业务词（approve/revise/reject） | `on` 只保留业务词（approve/revise/reject）；`done` 提升为 `next`；`fail`/`blocked` 从 `on` 移除，走 `on_status` 或 `default` |
| 终止状态判断：`terminal=true` 或 `on` 为空 | 终止状态判断：无 `next`、无 `on`、无 task |
| `_create_error_result` 用 `decision="fail"` | `_create_error_result` 用 `decision=None, status="failed"` |
| validate 只检查基础完备性 | 新增两条静态护栏：缺失成功出口 + decision 必填一致性 |
| `route_by` 不存在 | TransitionResult 携带 `status` + `route_by`（`"status"` / `"decision"` / `"next"`） |

#### 1.2 验收标准

1. **存量测试全部通过**（零改动或无功能回退）
2. 存量 10+ 个 `workflow.yaml` 零修改即可在新 Runtime 跑通（loader 自动归一）
3. 新 YAML 可用 `next`/`on`/`on_status`/`default` 新写法
4. Runtime 路由逻辑中不出现 `is_review`、`is_gate`、`done`/`fail`/`blocked` 等业务/生命周期词作为路由判断
5. `_create_error_result` 的 `decision` 为 `None`
6. `terminal_states` 自动推断不会误判线性节点（`not s.on and not s.next`）
7. loop 展开后的 states 路由行为正确（已知 `on` 中含旧键如 `done`，通过 on 匹配兜底）

#### 1.3 歧义点（已澄清）

| 项 | 确认 |
|---|---|
| `on_status` 是否必填？ | 非必填，仅当 `blocked` 需去往不同于 `failed` 处时才需声明 |
| `next` 和 `on` 能同时存在吗？ | 不能，非终止节点必须**恰好**定义一个成功出口（`next` 或 `on`），validate 期拦截 |
| 归一后 `fail`/`blocked` 从 `on` 中移除后去哪？ | 目标与 `default` 相同则丢弃（不写入 `on_status`）；与 `default` 不同则写入 `on_status` |
| `_loop` 展开需要改吗？ | 不改——明确非目标。展开后的 states 保持旧格式键（`on` 中含 `done`），通过 `on` 匹配兜底，Step 4 处理 |
| Repair 需要实现吗？ | 不实现——明确非目标 |
| `route_by` 在 `next` 路径用什么值？ | `"next"`（修订：原计划用 `"decision"`，审核指出语义不准确） |

---

### 2. 目标与非目标

#### 目标（做）

1. `StateModel` 新增 `next` + `on_status` 字段，`to_dict`/`from_dict` 同步
2. `load_state()` 读新字段 + **旧 YAML 自动归一**（`done→next`，`fail/blocked`→与 default 相同则丢弃否则进 `on_status`，业务词→保留在 `on`）
3. `resolve_transition` 改为两段式路由（6 分支），`route_by` 返回 `"status"`/`"decision"`/`"next"`
4. `TransitionResult` 新增 `status` 和 `route_by` 字段
5. Runner 主循环调用两段式路由，`_create_error_result` 改 `decision=None`
6. `StateMachine.validate` 新增两条护栏；`WorkflowConfig.validate` 扩展 target 存在性检查
7. `_find_reachable()` / `get_state_names()` / `get_terminal_states()` 遍历补全 `next` + `on_status`
8. `terminal_states` 自动推断逻辑适配新模型（加 `not s.next`）
9. observability（status/explain）兼容 `next`/`on_status`
10. 更新存量测试 + 新增归一/护栏/两段式/traversal 测试（共约 24+ 项）

#### 非目标（不做）

- ❌ 不实现 Repair（Step 3）
- ❌ 不改 `_unroll_loops`（Step 4，但需确保 loop 展开后路由正确且护栏不误报）
- ❌ 不改 `tasks/result.py`（Step 1 已完成）
- ❌ 不改 agent adapter（Step 1 已完成）
- ❌ 不修改 `_loop` 展开中的键名判断逻辑（Step 4）
- ❌ 不处理 `cancelled`/`timeout` 的路由归一（cancel 路径单独处理，不进路由）
- ❌ 不修改任何存量 `workflow.yaml` 文件
- ❌ 不新增 `success` 键到 `on_status`
- ❌ 不删除 `VALID_DECISIONS`（Step 1 已完成）
- ❌ 不在 validate 护栏中检查 `on` 键名是否为生命周期词（避免 loop 展开后误报）

---

### 3. 涉及文件和模块边界

#### 3.1 核心修改

| 文件 | 修改内容 | 理由 |
|---|---|---|
| `config/models.py` | `StateModel` +`next` +`on_status`；`to_dict`/`from_dict` 同步；`WorkflowConfig.validate()` 扩展 `next`/`on_status` target 检查 | 路由模型数据载体 + 引用完整性 |
| `config/loader.py` | `load_state()` 读新字段 + 旧格式归一逻辑；`terminal_states` 自动推断加 `not s.next`；优化 `on_status` 冗余跳过 | 存量 YAML 零改动跑通的关键 |
| `state_machine/machine.py` | `resolve_transition` 两段式；`validate` 新增两条护栏；`_find_reachable()`/`get_state_names()`/`get_terminal_states()` 遍历补全 | 路由核心逻辑 + 遍历完整性 |
| `state_machine/transition.py` | `TransitionResult` +`status` +`route_by`；更新 `to_event_dict` | 事件 payload 携带路由依据 |
| `state_machine/runner.py` | 主循环调用两段式；`_create_error_result` `decision=None`；`continue_from_gate` 传 `status="success"` | 路由执行层 |

#### 3.2 兼容修改

| 文件 | 修改内容 | 理由 |
|---|---|---|
| `observability/status.py` | 展示 `next`/`on_status` 路由信息 | 状态查询兼容新模型 |
| `observability/explain.py` | Transitions 段展示 `next`/`on: <key>`/`(on_status) <key>` 格式；`is_terminal` 判据加 `not next` | 解释兼容新模型 |

#### 3.3 测试文件

| 文件 | 修改内容 | 理由 |
|---|---|---|
| `tests/unit/test_state_machine.py` | 新增：两段式路由、归一、护栏、traversal、terminal 推断测试（~24 项） | 核心路由逻辑覆盖 |
| `tests/unit/test_config_v4.py` | 新增：StateModel `next`/`on_status` 序列化 | 序列化往返覆盖 |
| `tests/unit/test_loop_unroll.py` | 不改——但需确认现有测试通过 | 回归验证 |
| `tests/integration/*` | 不改——存量集成测试不变 | 回归验证 |

#### 3.4 不碰的文件

- `tasks/result.py` — Step 1 已完成
- `tasks/result_schema.py` — Step 1 已完成
- `agents/claude_cli.py` / `agents/codex_cli.py` — Step 1 已完成
- `agents/mock.py` — Step 1 已完成
- `validators/task_result.py` — Step 3 改纯函数化

---

### 4. 分步骤实现方案

#### Step 2.1：StateModel 新增字段 + 序列化同步（~25 行改动）

**文件**：`config/models.py`

1. `StateModel` 新增两个字段：
   ```python
   next: str = ""                    # 成功后的单出口（线性节点）
   on_status: dict[str, str] = field(default_factory=dict)  # status → successor（仅 failed/blocked）
   ```
2. `to_dict()` 追加 `"next": self.next` 和 `"on_status": self.on_status`
3. `WorkflowConfig.from_dict()` 中创建 StateModel 时读 `next` 和 `on_status`
4. `WorkflowConfig.validate()` 新增两个检查块（见 R2）：
   - 3b: `state.next` 目标存在性
   - 3c: `state.on_status` 目标存在性

**验证**：运行 `tests/unit/test_config_v4.py`，确保序列化往返不丢字段。

---

#### Step 2.2：Loader 旧格式归一 + terminal_states 适配（~50 行改动）

**文件**：`config/loader.py`

##### 2.2a：`_normalize_state()` 归一助手

```python
def _normalize_state(data: dict[str, Any]) -> dict[str, Any]:
    """将旧格式 on={done, fail, blocked, approve, ...} 归一为新格式。"""
    on = dict(data.get("on", {}))
    default_target = data.get("default", "failed")

    # 生命周期词 done → next
    if "done" in on:
        data.setdefault("next", on.pop("done"))

    # fail/blocked → on_status（仅当目标不同于 default 时写入，避免冗余）
    for key in ("fail", "blocked"):
        if key in on:
            target = on.pop(key)
            if target != default_target:
                data.setdefault("on_status", {})
                data["on_status"][key] = target

    data["on"] = on  # 剩余的是业务词（approve, revise, reject 等）
    return data
```

##### 2.2b：`terminal_states` 自动推断适配

```python
# 旧（loader.py:386-389）
terminal_states = [
    name for name, s in states.items()
    if s.terminal or not s.on
]

# 新
terminal_states = [
    name for name, s in states.items()
    if s.terminal or (not s.on and not s.next)
]
```

##### 2.2c：`load_state()` 调用归一

归一在 `load_state()` 调用 `StateModel(...)` 之前执行。归一既处理新 YAML 的 `next`/`on_status` 显式声明，也处理旧 YAML 的自动转换。

**验证**：
- 用现有 workflow YAML 调用 `load_workflow`，检查 StateModel 结构符合归一预期
- `test_terminal_auto_detect_with_next_only`：仅有 `next` 无 `on` 的 state 不被误判为 terminal
- `test_old_format_normalize_*` 系列测试

---

#### Step 2.3：TransitionResult 扩展（~12 行改动）

**文件**：`state_machine/transition.py`

1. `TransitionResult` 新增两个字段：
   ```python
   status: str = ""          # 触发本次路由的 status（success/failed/blocked）
   route_by: str = ""        # "status" | "decision" | "next" — 路由驱动因素
   ```
2. `to_event_dict()` 追加：
   ```python
   "status": self.status,
   "route_by": self.route_by,
   ```

**验证**：`TransitionResult.to_event_dict()` 返回完整字段。

---

#### Step 2.4：StateMachine 两段式路由 + validate 护栏 + traversal 补全（~90 行改动）

**文件**：`state_machine/machine.py`

##### 2.4a：`resolve_transition` 两段式（6 分支）

```python
def resolve_transition(self, state_name: str, status: str, decision: str | None) -> TransitionResult:
    state = self.states.get(state_name)
    if state is None:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state="failed", matched=False, route_by="status",
            reason=f"状态 '{state_name}' 未定义",
        )

    # ── 第一段：status != success → on_status 或 default ──
    if status != "success":
        if status in state.on_status:
            return TransitionResult(
                current_state=state_name, status=status, decision=decision or "",
                next_state=state.on_status[status], matched=True, route_by="status",
                reason=f"status={status}, on_status 匹配 → '{state.on_status[status]}'",
            )
        route_target = state.default or "failed"
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=route_target, matched=False, route_by="status",
            reason=f"status={status}, 无 on_status 映射, 走 default → '{route_target}'",
        )

    # ── 第二段：status = success ──
    # 分支 3: on 中有匹配的 decision
    if state.on and decision in state.on:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=state.on[decision], matched=True, route_by="decision",
            reason=f"status=success, decision='{decision}' 匹配 on",
        )

    # 分支 4: on 存在但 decision 未匹配 → default（仍在 decision 分支）
    if state.on:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=state.default or "failed", matched=False, route_by="decision",
            reason=f"decision='{decision}' 未匹配 on，走 default → '{state.default}'",
        )

    # 分支 5: 线性节点 → next
    if state.next:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=state.next, matched=True, route_by="next",
            reason=f"status=success, 线性节点 next → '{state.next}'",
        )

    # 分支 6: 无 on 也无 next → default（配置疏漏，validate 期拦截）
    route_target = state.default or "failed"
    return TransitionResult(
        current_state=state_name, status=status, decision=decision or "",
        next_state=route_target, matched=False, route_by="status",
        reason=f"status=success, 无 on/next, 走 default → '{route_target}'",
    )
```

##### 2.4b：validate 新增两条护栏

**护栏 1 — 缺失成功出口**（硬错误）：
- 非终止节点必须恰好定义一个成功出口：`on` 非空 **或** `next` 非空，但不能同时有
- 同时有 `next` 和 `on` → 报错（配置歧义）

**护栏 2 — decision 必填一致性**（硬错误）：
- 节点有 `on` ⇒ 对应 task 的 `allowed_decisions` 非空（否则 on 永远无法命中）
- 节点有 `next`（无 `on`）⇒ task 不应声明 `allowed_decisions`（两者语义冲突）
  - 注意：此处只做**警告**而非硬错误，因为存量 YAML 大量存在此模式，硬错误会导致集成测试挂

**注意**：护栏不检查 `on` 键名是否为生命周期词（如 `done`/`fail`/`blocked`），避免 loop 展开后的 states 被误报。

##### 2.4c：遍历补全

| 方法 | 补全内容 |
|---|---|
| `_find_reachable()` | DFS 追加 `state.next` + `state.on_status.values()` |
| `get_state_names()` | DFS 追加 `state.next` + `state.on_status.values()` |
| `get_terminal_states()` | 自动推断加 `not state.next` 条件 |

**验证**：
- 护栏测试：构造缺出口配置应报错
- 护栏测试：构造 decision 不一致配置应报错
- 路由测试：6 个分支各一个测试用例
- traversal 测试：`_find_reachable` / `get_state_names` 通过 `next` / `on_status` 发现 state

---

#### Step 2.5：Runner 主循环适配（~30 行改动）

**文件**：`state_machine/runner.py`

1. 主循环中的 transition 调用改为两段式（`runner.py:486`）：
   ```python
   # 旧
   transition = self.sm.resolve_transition(current_state, decision)
   # 新
   transition = self.sm.resolve_transition(current_state, status, decision)
   ```

2. `_create_error_result`（`runner.py:1321-1345`）：`decision` 从 `"fail"` 改为 `None`

3. `continue_from_gate()`（`runner.py:1271`）：
   ```python
   # 旧
   transition = self.sm.resolve_transition(gate_state, decision)
   # 新
   transition = self.sm.resolve_transition(gate_state, status="success", decision=decision)
   ```
   Gate 状态的 task 在暂停前已成功完成，因此 `status="success"`。

4. 注意：`decision` 可能是 `None`（当 `get_decision()` 返回 None 时），旧的 `str(decision)` 等操作需改为 `decision or ""`。

**验证**：运行存量集成测试确认行为不变。

---

#### Step 2.6：Observability 兼容（~25 行改动）

**文件**：`observability/status.py`、`observability/explain.py`

##### 2.6a：`explain.py` Transitions 段新格式

```
Transitions:
  next                 -> audit
  (on_status) blocked  -> audit
  on: approve          -> execute
  on: revise           -> revise_plan
  on: reject           -> failed
  default              -> failed
```

实现逻辑：
```python
# Transitions
lines.append("Transitions:")
# 1. next（如果存在）
if state_config.get("next"):
    lines.append(f"  {'next':20s} -> {state_config['next']}")
# 2. on_status（如果存在）
on_status = state_config.get("on_status", {})
for key, target in sorted(on_status.items()):
    lines.append(f"  (on_status) {key:12s} -> {target}")
# 3. on（如果存在）
on_map = state_config.get("on", {})
for decision, next_state in sorted(on_map.items()):
    lines.append(f"  on: {decision:15s} -> {next_state}")
# 4. default（非终止状态）
if not is_terminal:
    lines.append(f"  {'default':20s} -> {default}")
```

##### 2.6b：`explain.py` `is_terminal` 判断

```python
# 旧
is_terminal = state_config.get("terminal", False) or (not on_map and not task_name)

# 新
is_terminal = state_config.get("terminal", False) or (
    not on_map and not state_config.get("next") and not task_name
)
```

##### 2.6c：`status.py`

State History 行中加入 `next`/`on_status` 信息（如果有）。

**验证**：创建含 next/on_status 的 run 后，`status`/`explain` 输出正确。

---

### 5. 测试策略

#### 5.1 新增单元测试（共 25 项）

##### 序列化

| # | 测试 | 验证点 |
|---|---|---|
| 1 | `test_next_field_serialization` | StateModel `next`/`on_status` 序列化往返 |
| 2 | `test_next_field_from_dict` | `from_dict` 正确读取 `next`/`on_status` |

##### 归一

| # | 测试 | 验证点 |
|---|---|---|
| 3 | `test_old_format_normalize_done_to_next` | `on:{done:xxx}` → `next=xxx` |
| 4 | `test_old_format_normalize_fail_to_on_status` | `on:{fail:xxx}`，xxx!=default → 进 `on_status` |
| 5 | `test_old_format_normalize_fail_same_as_default_skipped` | `on:{fail:failed}`，default=failed → 不入 `on_status` |
| 6 | `test_old_format_normalize_blocked_to_on_status` | `on:{blocked:audit}`，audit!=default → 进 `on_status` |
| 7 | `test_old_format_keep_business_keys` | `on:{approve, revise, reject}` 保持不变 |

##### 两段式路由

| # | 测试 | 验证点 |
|---|---|---|
| 8 | `test_resolve_transition_success_next` | status=success, state.next → 走 next, route_by="next" |
| 9 | `test_resolve_transition_success_on_matched` | status=success, decision 匹配 on → route_by="decision" |
| 10 | `test_resolve_transition_success_on_unmatched` | status=success, decision 不匹配 on → default, route_by="decision" |
| 11 | `test_resolve_transition_failed_to_default` | status=failed, 无 on_status → default, route_by="status" |
| 12 | `test_resolve_transition_blocked_to_on_status` | status=blocked + on_status 有 blocked → 走 on_status |
| 13 | `test_resolve_transition_decision_none` | decision=None 在两段式路由中的行为（Step 1 产物兼容） |
| 14 | `test_transition_result_has_status_and_route_by` | TransitionResult 含 status/route_by |

##### Terminal 推断

| # | 测试 | 验证点 |
|---|---|---|
| 15 | `test_terminal_auto_detect_with_next_only` | 仅有 `next` 无 `on` 的 state 不被误判为 terminal |

##### Traversal 补全

| # | 测试 | 验证点 |
|---|---|---|
| 16 | `test_find_reachable_via_next` | `_find_reachable()` 通过 `next` 发现 state |
| 17 | `test_find_reachable_via_on_status` | `_find_reachable()` 通过 `on_status` 发现 state |
| 18 | `test_get_state_names_includes_next_path` | `get_state_names()` 有序列表包含 `next` 路径 state |

##### Validate

| # | 测试 | 验证点 |
|---|---|---|
| 19 | `test_validate_missing_success_exit` | 非终止节点无 on 无 next → 报错 |
| 20 | `test_validate_next_and_on_both_present` | 同时有 on 和 next → **报错**（硬错误） |
| 21 | `test_validate_decision_consistency` | 有 on 但 allowed_decisions 为空 → 报错 |
| 22 | `test_validate_linear_no_allowed_decisions` | 有 next 但 allowed_decisions 非空 → **警告**（非硬错误） |
| 23 | `test_workflow_config_validate_checks_next_target` | `WorkflowConfig.validate()` 检查 `next` 目标存在性 |
| 24 | `test_workflow_config_validate_checks_on_status_target` | `WorkflowConfig.validate()` 检查 `on_status` 目标存在性 |

##### 集成路径

| # | 测试 | 验证点 |
|---|---|---|
| 25 | `test_continue_from_gate_two_stage` | Gate 继续时传入 `status="success"` 的正确性 |
| 26 | `test_loop_unrolled_state_normalization` | loop 展开 state 的 `on` 中含 `done` 时路由行为正确（on 匹配兜底） |
| 27 | `test_blocked_to_non_default_on_status` | software-dev `execute` 的 `blocked→audit` 案例完整性验证 |
| 28 | `test_create_error_result_decision_none` | `_create_error_result.decision is None` |

#### 5.2 更新现有测试

| 测试 | 需要调整 |
|---|---|
| `test_resolve_transition_matched` | 改为两段式调用 `(state, "success", "done")` |
| `test_resolve_transition_default` | 增加 status 参数 |
| `TestTransition.test_resolve_matched` | 纯函数签名改变，更新调用方 |
| `TestTransition.test_resolve_unknown` | 同上 |
| `test_gate_resolve_transition_still_works` | 增加 status 参数 |

#### 5.3 集成测试

存量集成测试文件**不改代码**，全部应通过：

```bash
$env:PYTHONPATH='src;.'; pytest tests/integration/ -q
$env:PYTHONPATH='src;.'; pytest tests/unit/ -q
$env:PYTHONPATH='src;.'; pytest tests -q
```

#### 5.4 YAML 归一集成验证

手动用所有存量 workflow YAML 文件逐个 `load_workflow`，验证归一后的 StateModel 结构正确。

---

### 6. 风险与停止规则

#### 6.1 关键风险（修订后）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **`_unroll_loops` 输出旧格式** | `_loop` 展开后 state 的 `on` 键用 `done`/`revise`/`approve`，未归一化 | **方案 B（已采纳）**：已知 `on` 中含 `done` 等生命周期词，路由通过 `on` 匹配兜底（`decision="done"` → `state.on["done"]` → 命中），行为正确。validate 护栏不检查 `on` 键名语义，不会误报。Step 4 统一处理 |
| **`terminal_states` 自动推断变化** | `get_terminal_states()` 的判断逻辑变了（加了 `next` 判断），可能影响 `load_workflow` 中 386-389 行的自动识别 | **已修正 B1**：`not s.on and not s.next` 防止线性节点误判。同步更新 `StateMachine.get_terminal_states()` |
| **`resolve_transition` 签名变化** | 调用方（runner.py, continue_from_gate）都需更新，漏改会导致 TypeError | 全局 grep `resolve_transition` 确保所有调用点已更新 |
| **decision=None 传播** | 旧代码中多处用 `str(decision)` 或 `decision or "fail"`，None 可能导致意外行为 | grep `decision` 检查 runner.py 中所有使用点 |
| **存量 YAML 中 `on: {done: xxx, fail: yyy, blocked: yyy}` 模式** | 大部分 YAML 的 `fail`/`blocked` 都指向 `failed`（即 default），归一丢弃后行为一致。但 `blocked: audit`（software-dev 的 `execute` state）的 `blocked` 目标和 `default: failed` 不同 | `blocked≠default` → 写入 `on_status`。**S1 优化**：与 default 相同则跳过写入，减少冗余 |

#### 6.2 停止规则

1. 如果存量集成测试无法全部通过且修复耗时 > 2 小时 → 停止，汇报阻塞点
2. 如果归一逻辑导致任何存量 YAML 的行为回退（无法自动修正）→ 停止，需要用户确认方案
3. 如果 `_unroll_loops` 与归一交互出现不一致且需要改动 `_unroll_loops` → 停止，这是明确非目标，需另开 step
4. 如果 loop 展开后的 states 被 validate 护栏误报 → 停止（护栏不应检查 `on` 键名语义）

---

### 7. 预期产物

#### 交付物清单

1. **修改文件**（5 个核心 + 2 个兼容）：
   - `config/models.py` — StateModel 新字段 + WorkflowConfig.validate 扩展
   - `config/loader.py` — load_state 归一 + terminal_states 适配
   - `state_machine/machine.py` — 两段式路由 + 护栏 + traversal 补全
   - `state_machine/transition.py` — TransitionResult 扩展
   - `state_machine/runner.py` — 主循环适配 + error_result decision=None
   - `observability/status.py` — 兼容
   - `observability/explain.py` — 兼容（含 is_terminal 修正）

2. **测试文件**（2 个更新）：
   - `tests/unit/test_state_machine.py` — 新增归一/护栏/两段式/traversal/terminal 测试（~28 项）
   - `tests/unit/test_config_v4.py` — 新增 StateModel 序列化测试
   - `tests/unit/test_loop_unroll.py` — 无改动，确认通过（回归）
   - `tests/integration/*` — 无改动，确认通过（回归）

3. **不新增文件**：此步不创建新模块，所有改动在现有文件中完成

4. **存量 YAML**：10 个 `workflow.yaml` 文件**零改动**

5. **验证命令**：
   ```bash
   # 全部测试
   $env:PYTHONPATH='src;.'; pytest tests -q

   # 状态机测试（本步核心）
   $env:PYTHONPATH='src;.'; pytest tests/unit/test_state_machine.py -v

   # 集成回归
   $env:PYTHONPATH='src;.'; pytest tests/integration/ -v
   ```

---

## 三、相对上一版 plan_doc 的关键变更

| 变更 | 来源 | 说明 |
|---|---|---|
| `terminal_states` 自动推断逻辑修正 | **B1** | 从 `not s.on` 改为 `not s.on and not s.next`，防止线性节点被误判；同步修正 `StateMachine.get_terminal_states()` |
| `_unroll_loops` 归一覆盖声明修正 | **B2** | 从"归一自然覆盖"改为"已知差异，接受，Step 4 处理"；新增护栏不检查 `on` 键名语义的约束 |
| `_find_reachable()` 遍历补全 | **R1/R5** | 追加 `state.next` + `state.on_status.values()` 遍历 |
| `get_state_names()` 遍历补全 | **R1** | 同上 |
| `WorkflowConfig.validate()` 扩展 | **R2** | 新增 `next`/`on_status` target 存在性检查 |
| Observability 格式具体化 | **R3** | `explain.py` Transitions 段明确展示 `next`/`on: <key>`/`(on_status) <key>` 格式；`status.py` 补充展示 |
| `continue_from_gate` status 取值明确 | **R4** | 明确传 `status="success"`（Gate 前 task 已成功） |
| `route_by` 语义修正 | **R6** | `next` 路径从 `"decision"` 改为 `"next"` |
| 归一优化：跳过冗余 `on_status` | **S1** | `fail`/`blocked` 目标 == default 时不写入 `on_status` |
| validate 护栏范围澄清 | **S2** | 明确新护栏只在 `StateMachine.validate()`，`WorkflowConfig.validate()` 仅扩展引用完整性检查 |
| 测试计划扩展 | §4.1+§4.2 | 新增 10 项缺失测试 + 微调 2 项已有测试描述 |
| `route_by` 取值表 | §R6 | 新增 6 分支的 `route_by` 取值对照表 |
