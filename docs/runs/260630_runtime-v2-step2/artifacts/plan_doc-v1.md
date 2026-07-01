# Runtime v2 Step 2 开发计划：路由模型 + Loader 旧格式归一

## 1. 需求理解

### 1.1 目标

本步实现 Runtime v2 最核心的架构变更——**路由从单看 decision 改为两段式（先 status 后 decision）**，并且 Runtime 全程不判断节点类型（如 is_review/is_gate/is_execute），只看 `on`/`next` 结构存在性。

核心变化：

| 旧模型 | 新模型 |
|---|---|
| 路由只看 `decision` | 先看 `status`（success/failed/blocked），success 时才看 `decision` |
| `on` 中混有生命周期词（done/fail/blocked）和业务词（approve/revise/reject） | `on` 只保留业务词（approve/revise/reject）；`done` 提升为 `next`；`fail`/`blocked` 从 `on` 移除，走 `on_status` 或 `default` |
| 终止状态判断：`terminal=true` 或 `on` 为空 | 终止状态判断：无 `next`、无 `on`、无 task |
| `_create_error_result` 用 `decision="fail"` | `_create_error_result` 用 `decision=None, status="failed"` |
| validate 只检查基础完备性 | 新增两条静态护栏：缺失成功出口 + decision 必填一致性 |

### 1.2 验收标准

1. **存量测试全部通过**（零改动或无功能回退）
2. 存量 10+ 个 `workflow.yaml` 零修改即可在新 Runtime 跑通（loader 自动归一）
3. 新 YAML 可用 `next`/`on`/`on_status`/`default` 新写法
4. Runtime 路由逻辑中不出现 `is_review`、`is_gate`、`done`/`fail`/`blocked` 等业务/生命周期词作为路由判断
5. `_create_error_result` 的 `decision` 为 `None`

### 1.3 歧义点（已澄清）

| 项 | 确认 |
|---|---|
| `on_status` 是否必填？ | 非必填，仅当 `blocked` 需去往不同于 `failed` 处时才需声明 |
| `next` 和 `on` 能同时存在吗？ | 不能，非终止节点必须**恰好**定义一个成功出口（`next` 或 `on`），validate 期拦截 |
| 归一后 `fail`/`blocked` 从 `on` 中移除后去哪？ | `on_status` 或 `default`。归一阶段自动转：`fail` → 丢弃（走 `default`），`blocked` → 若与 `default` 不同则写入 `on_status` |
| `_loop` 展开需要改吗？ | 不改——明确非目标 |
| Repair 需要实现吗？ | 不实现——明确非目标 |

## 2. 目标与非目标

### 目标（做）

1. `StateModel` 新增 `next` + `on_status` 字段，`to_dict`/`from_dict` 同步
2. `load_state()` 读新字段 + **旧 YAML 自动归一**（`done→next`，`fail/blocked→丢弃`，业务词→保留在 `on`）
3. `resolve_transition` 改为两段式路由：`status != success → on_status.get(status, default)`，`success → on.get(decision, default) 或 next`
4. `TransitionResult` 新增 `status` 和 `route_by` 字段
5. Runner 主循环调用两段式路由，`_create_error_result` 改 `decision=None`
6. `StateMachine.validate` 新增两条护栏
7. observability（status/explain）兼容 `next`/`on_status`
8. 更新存量测试 + 新增归一/护栏测试

### 非目标（不做）

- ❌ 不实现 Repair（Step 3）
- ❌ 不改 `_unroll_loops`（Step 4）
- ❌ 不改 `tasks/result.py`（Step 1 已完成）
- ❌ 不改 agent adapter（Step 1 已完成）
- ❌ 不修改 `_loop` 展开中的键名判断逻辑（Step 4）
- ❌ 不处理 `cancelled`/`timeout` 的路由归一（cancel 路径单独处理，不进路由）
- ❌ 不修改任何存量 `workflow.yaml` 文件
- ❌ 不新增 `success` 键到 `on_status`
- ❌ 不删除 `VALID_DECISIONS`（Step 1 已完成）

## 3. 涉及文件和模块边界

### 3.1 核心修改

| 文件 | 修改内容 | 理由 |
|---|---|---|
| `config/models.py` | `StateModel` +`next` +`on_status`；`to_dict`/`from_dict` 同步 | 路由模型数据载体 |
| `config/loader.py` | `load_state()` 读新字段 + 旧格式归一逻辑 | 存量 YAML 零改动跑通的关键 |
| `state_machine/machine.py` | `resolve_transition` 两段式；`validate` 新增两条护栏 | 路由核心逻辑 |
| `state_machine/transition.py` | `TransitionResult` +`status` +`route_by`；更新 `to_event_dict` | 事件 payload 携带路由依据 |
| `state_machine/runner.py` | 主循环调用两段式；`_create_error_result` `decision=None` | 路由执行层 |

### 3.2 兼容修改

| 文件 | 修改内容 | 理由 |
|---|---|---|
| `observability/status.py` | 展示 `next`/`on_status` 路由（如果 `on` 中无匹配键时展示 status 路由） | 状态查询兼容新模型 |
| `observability/explain.py` | 展示 `next`/`on_status` | 解释兼容新模型 |

### 3.3 测试文件

| 文件 | 修改内容 | 理由 |
|---|---|---|
| `tests/unit/test_state_machine.py` | 新增：两段式路由、归一、护栏测试 | 核心路由逻辑覆盖 |
| `tests/unit/test_config_v4.py` | 新增：StateModel `next`/`on_status` 序列化 | 序列化往返覆盖 |
| `tests/unit/test_loop_unroll.py` | 不改——但需确认现有测试通过 | 回归验证 |
| `tests/integration/*` | 不改——存量集成测试不变 | 回归验证 |

### 3.4 不碰的文件

- `tasks/result.py` — Step 1 已完成
- `tasks/result_schema.py` — Step 1 已完成
- `agents/claude_cli.py` / `agents/codex_cli.py` — Step 1 已完成
- `agents/mock.py` — Step 1 已完成
- `validators/task_result.py` — Step 3 改纯函数化

## 4. 分步骤实现方案

### Step 2.1：StateModel 新增字段 + 序列化同步（~20 行改动）

**文件**：`config/models.py`

1. `StateModel` 新增两个字段：
   ```python
   next: str = ""                    # 成功后的单出口
   on_status: dict[str, str] = field(default_factory=dict)  # status → successor（仅 failed/blocked）
   ```
2. `to_dict()` 追加 `"next": self.next` 和 `"on_status": self.on_status`
3. `WorkflowConfig.from_dict()` 中创建 StateModel 时读 `next` 和 `on_status`

**验证**：运行 `tests/unit/test_config_v4.py` 和 `tests/unit/test_state_machine.py`，确保序列化往返不丢字段。

### Step 2.2：Loader 旧格式归一（~40 行改动）

**文件**：`config/loader.py`

在 `load_state()` 或新增一个 `_normalize_state()` 助手函数中实现归一逻辑：

```python
def _normalize_state(data: dict[str, Any]) -> dict[str, Any]:
    """将旧格式 on={done, fail, blocked, approve, ...} 归一为新格式。"""
    on = dict(data.get("on", {}))
    
    # 生命周期词 → 转义
    if "done" in on:
        data.setdefault("next", on.pop("done"))
    
    # fail/blocked → on_status 或丢弃（通常 == default）
    for key in ("fail", "blocked"):
        if key in on:
            target = on.pop(key)
            data.setdefault("on_status", {})
            if data["on_status"].get(key) is None:
                data["on_status"][key] = target
    
    data["on"] = on  # 剩余的是业务词（approve, revise, reject 等）
    return data
```

- 归一发生在 `load_state()` 调用 `StateModel(...)` **之前**（数据归一后再传参）
- 同时新增对 `next`/`on_status` 的显式读取（新 YAML 可直接声明）

**验证**：
- 用现有 workflow YAML 调用 `load_workflow`，检查 `StateModel.next` 和 `StateModel.on` 符合归一预期
- 新增测试 `test_old_format_normalization`

### Step 2.3：TransitionResult 扩展（~10 行改动）

**文件**：`state_machine/transition.py`

1. `TransitionResult` 新增两个字段：
   ```python
   status: str = ""              # 触发本次路由的 status
   route_by: str = ""            # "status" 或 "decision"
   ```
2. `to_event_dict()` 追加 `"status": self.status, "route_by": self.route_by`
3. 模块级 `resolve_transition` 纯函数保持不变（外部只用 StateMachine 的方法）

**验证**：`TransitionResult.to_event_dict()` 返回完整字段。

### Step 2.4：StateMachine 两段式路由 + validate 护栏（~60 行改动）

**文件**：`state_machine/machine.py`

#### 2.4a：`resolve_transition` 两段式

```python
def resolve_transition(self, state_name: str, status: str, decision: str | None) -> TransitionResult:
    state = self.states.get(state_name)
    if state is None:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state="failed", matched=False, route_by="status",
            reason=f"状态 '{state_name}' 未定义",
        )
    
    # 第一段：status != success → on_status 或 default
    if status != "success":
        route_target = state.on_status.get(status) or state.default
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=route_target, matched=(status in state.on_status),
            route_by="status",
            reason=f"status={status}, route_by=status → '{route_target}'",
        )
    
    # 第二段：status = success → on（分支）或 next（线性）
    if state.on and decision in state.on:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=state.on[decision], matched=True, route_by="decision",
            reason=f"status=success, decision='{decision}' 匹配 on",
        )
    
    if state.on:
        # decision 不匹配 → default
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=state.default, matched=False, route_by="decision",
            reason=f"decision='{decision}' 未匹配 on，走 default → '{state.default}'",
        )
    
    if state.next:
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=state.next, matched=True, route_by="decision",
            reason=f"status=success, 线性节点 next → '{state.next}'",
        )
    
    # 无 on 也无 next → default（配置疏漏，validate 期拦截）
    return TransitionResult(
        current_state=state_name, status=status, decision=decision or "",
        next_state=state.default, matched=False, route_by="status",
        reason=f"status=success, 无 on/next, 走 default → '{state.default}'",
    )
```

#### 2.4b：validate 新增两条护栏

1. **缺失成功出口**：非终止节点必须恰好定义一个成功出口（`on` 非空 或 `next` 非空），但不能同时有。
2. **decision 必填一致性**：节点有 `on` ⇒ 对应 task 的 `allowed_decisions` 非空；节点有 `next`（无 `on`）⇒ task 不应声明 `allowed_decisions`。

另外需要更新以下逻辑：
- `_find_reachable()`：遍历 `state.next` 和 `state.on_status` 的值
- `get_terminal_states()`：更新判断逻辑——现在 terminal 不仅看 `terminal` 属性和 `on` 为空，还要看 `next` 为空

**验证**：
- 护栏测试：构造缺出口配置应报错
- 护栏测试：构造 decision 不一致配置应报错
- 路由测试：status=success+next → 走 next
- 路由测试：status=success+decision 匹配 on → 走 on
- 路由测试：status=failed → 走 on_status 或 default

### Step 2.5：Runner 主循环适配（~30 行改动）

**文件**：`state_machine/runner.py`

1. 主循环中的 transition 调用改为两段式：
   ```python
   # 旧: transition = self.sm.resolve_transition(current_state, decision)
   # 新:
   transition = self.sm.resolve_transition(current_state, status, decision)
   ```

2. `_create_error_result`：`decision` 从 `"fail"` 改为 `None`
   ```python
   # 旧: decision="fail"
   # 新: decision=None
   ```

3. Gate 状态：`continue_from_gate()` 调用也改为两段式。

4. 注意：`decision` 可能是 `None`（当 `get_decision()` 返回 None 时），旧的 `str(decision)` 等操作需改为 `decision or ""`。

**验证**：运行存量集成测试确认行为不变。

### Step 2.6：Observability 兼容（~20 行改动）

**文件**：`observability/status.py`、`observability/explain.py`

1. `status.py`：Status 行展示中加入 `next`/`on_status` 信息（如果有）
2. `explain.py`：Transitions 段兼容 `next` + `on_status`，例如：
   ```
   Transitions:
     next                 -> execute
     (on_status) failed   -> failed
     default              -> failed
   ```

**验证**：创建含 next/on_status 的 run 后，`status`/`explain` 输出正确。

## 5. 测试策略

### 5.1 新增单元测试

| 测试 | 文件 | 验证点 |
|---|---|---|
| `test_next_field_serialization` | `test_state_machine.py` | StateModel 的 `next`/`on_status` 序列化往返 |
| `test_old_format_normalize_done_to_next` | `test_state_machine.py` | `on:{done:xxx}` → `next=xxx` |
| `test_old_format_normalize_fail_discarded` | `test_state_machine.py` | `on:{fail:xxx}` → 从 `on` 移除，`xxx` 若 != default 则进 `on_status` |
| `test_old_format_keep_business_keys` | `test_state_machine.py` | `on:{approve, revise, reject}` 保持不变 |
| `test_resolve_transition_success_next` | `test_state_machine.py` | status=success, state.next → 走 next |
| `test_resolve_transition_success_on_matched` | `test_state_machine.py` | status=success, decision 匹配 on |
| `test_resolve_transition_failed_to_default` | `test_state_machine.py` | status=failed → 走 on_status 或 default |
| `test_resolve_transition_blocked_to_on_status` | `test_state_machine.py` | status=blocked + on_status 有 blocked → 走 on_status |
| `test_transition_result_has_status_and_route_by` | `test_state_machine.py` | TransitionResult 含 status/route_by |
| `test_validate_missing_success_exit` | `test_state_machine.py` | 非终止节点无 on 无 next → validate 报错 |
| `test_validate_next_and_on_both_present` | `test_state_machine.py` | 同时有 on 和 next → validate 报错 |
| `test_validate_decision_consistency` | `test_state_machine.py` | 有 on 但 allowed_decisions 为空 → 报错 |
| `test_validate_linear_no_allowed_decisions` | `test_state_machine.py` | 有 next 但 allowed_decisions 非空 → 警告 |
| `test_create_error_result_decision_none` | `test_state_machine.py` 或新增 runner 测试 | `_create_error_result.decision is None` |

### 5.2 更新现有测试

| 测试 | 需要调整 |
|---|---|
| `test_resolve_transition_matched` | 改为两段式调用 `(state, "success", "done")` |
| `test_resolve_transition_default` | 增加 status 参数 |
| `TestTransition.test_resolve_matched` | 纯函数现在不被 Runner 直接调用，由 StateMachine 方法调用；保留或调整 |
| `TestTransition.test_resolve_unknown` | 同上 |
| `test_gate_resolve_transition_still_works` | 增加 status 参数 |

### 5.3 集成测试

存量集成测试文件**不改代码**，全部应通过。这是最重要的回归防线：

```bash
$env:PYTHONPATH='src;.'; pytest tests/integration/ -q
$env:PYTHONPATH='src;.'; pytest tests/unit/ -q
```

### 5.4 YAML 归一集成验证

手动用所有存量 workflow YAML 文件逐个 `load_workflow`，验证归一后的 StateModel 结构正确。

## 6. 风险与停止规则

### 6.1 关键风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| **`_unroll_loops` 输出旧格式** | `_loop` 展开后 state 的 `on` 键用 `done`/`revise`/`approve`，归一后路由走新逻辑 | 归一在 `load_state` 中发生，`_loop` 展开后数据仍然经过 `load_state`，自然被归一覆盖。不改 `_unroll_loops`，但需确认其输出被归一链路覆盖 |
| **`terminal_states` 自动推断变化** | `get_terminal_states()` 的判断逻辑变了（加了 `next` 判断），可能影响 `load_workflow` 中 386-389 行的自动识别 | 需确认新逻辑向后兼容：无 `on` 且无 `next` 且无 `task` → terminal |
| **`resolve_transition` 签名变化** | 调用方（runner.py, continue_from_gate）都需更新，漏改会导致 TypeError | 全局 grep `resolve_transition` 确保所有调用点已更新 |
| **decision=None 传播** | 旧代码中多处用 `str(decision)` 或 `decision or "fail"`，None 可能导致意外行为 | grep `decision` 检查 runner.py 中所有使用点 |
| **存量 YAML 中 `on: {done: xxx, fail: yyy, blocked: yyy}` 模式** | 大部分 YAML 的 `fail`/`blocked` 都指向 `failed`（即 default），归一丢弃后行为一致。但 `blocked: audit`（见 software-dev）的 `blocked` 目标和 `default: failed` 不同！ | software-dev 的 `execute` state: `on: {done: audit, fail: failed, blocked: audit}`——`blocked → audit` 需进 `on_status`，否则行为变化 |

### 6.2 停止规则

1. 如果存量集成测试无法全部通过且修复耗时 > 2 小时 → 停止，汇报阻塞点
2. 如果归一逻辑导致任何存量 YAML 的行为回退（无法自动修正）→ 停止，需要用户确认方案
3. 如果 `_unroll_loops` 与归一交互出现不一致且需要改动 `_unroll_loops` → 停止，这是明确非目标，需另开 step

## 7. 预期产物

### 交付物清单

1. **修改文件**（5 个核心 + 2 个兼容）：
   - `config/models.py` — StateModel 新字段
   - `config/loader.py` — load_state 归一
   - `state_machine/machine.py` — 两段式路由 + 护栏
   - `state_machine/transition.py` — TransitionResult 扩展
   - `state_machine/runner.py` — 主循环适配
   - `observability/status.py` — 兼容
   - `observability/explain.py` — 兼容

2. **测试文件**（1 个新增 + 1 个更新）：
   - `tests/unit/test_state_machine.py` — 新增归一/护栏/两段式测试
   - `tests/unit/test_loop_unroll.py` — 无改动，确认通过（回归）

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
