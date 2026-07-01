# 第 3 步实施计划：Validator 纯函数化 + Runner Repair 闸口

## 1. 需求理解

### 1.1 目标

将当前 Runner 中"校验失败 → 强制 fail"的一次否决逻辑（`runner.py:384-412`）替换为**三态校验 + 有界自愈**机制：

- **Validator 改为纯函数**：不调用 Agent，只做数据裁决。通过 `route_shape` 参数感知节点的路由形态（有无 `on`/`next`/`allowed_decisions`），返回三态 `ValidationResult`。
- **Runner 编排 Repair**：当校验返回 `repairable=True` 时，Runner 带反馈重新调用 Agent（限定只重输出 `status` + `decision`），最多 1-2 次。修理成功则继续路由，耗尽则置 `status=failed` 并留取证痕迹（`originally=invalid_output, repair_exhausted`）。

核心原则（来自设计文档 §Validator 纯函数 + Runner 编排 Repair）：

> ```
> Validator(data, 节点路由形态) → ValidationResult{valid, repairable, reason}   # 纯函数
> Runner: 读 ValidationResult → repairable? → 编排 Repair（有界）→ 路由           # 编排
> ```

### 1.2 三态模型

| 状态 | 含义 | Runner 动作 |
|---|---|---|
| `valid=True` | 全部通过 | 直接路由 |
| `valid=False, repairable=True` | decision ∉ allowed_decisions，或解析出 invalid_output | 进入 Repair（有界 1~2 次） |
| `valid=False, repairable=False` | 不可救（exit 127、二进制缺失、进程崩溃） | 直接 failed |

### 1.3 验收标准（来自 step3-spec.txt）

1. 存量测试全部通过（`PYTHONPATH=src;. pytest tests -q`）
2. 不修改路由模型（第 2 步已完成）
3. 不修改 `_unroll_loops`（第 4 步）

### 1.4 歧义点

| # | 问题 | 我的理解 |
|---|---|---|
| 1 | Repair 是否调用同一个 Agent，还是只用 MockAgent？ | 调用同一个 Agent adapter，但带专用 Repair prompt。MockAgent 模式下走模拟。 |
| 2 | Repair 的 1-2 次上限是硬编码还是可配置？ | 设计文档说"有界 1~2 次"，spec 说"注意与 guards.max_retries 协调"。建议硬编码为 2，不混入 max_retries 计数（max_retries 是 state 级重试，Repair 是 validation 级修复，粒度不同）。若未来需要可配置，从 guard 读 `max_repair_attempts`（默认 2）。 |
| 3 | Repair 后重新校验整个 TaskResult 还是只校验 status/decision？ | Repair prompt 限定 Agent 只重输出 status/decision，但重新校验仍需走完整 Validator（因为新输出仍可能格式错误）。 |
| 4 | `has_on` 和 `has_next` 从哪传入 Validator？ | Runner 在执行 state 时已知当前 state model（`self.workflow.get_state(state_name)`），直接提取 `bool(state.on)` / `bool(state.next)` 传入。 |
| 5 | `_create_error_result` 的 decision 是否已改？ | 设计文档说改为 `decision=None`+`status="failed"`。当前代码（runner.py:1335）已是 `decision=None`（第 1 步已完成），但需确认 runner.py:401 的旧逻辑会被替换。 |

---

## 2. 目标和非目标

### 2.1 本次要做

1. **新增/改造 `ValidationResult` 类型** — 支持三态 `valid`, `repairable`, `reason`（在 `validators/` 目录新增 `validation_result.py`）
2. **`validators/task_result.py` 改为纯函数** — 函数签名 `validate(data: dict, route_shape: RouteShape) → ValidationResult`，不调用 Agent
3. **`state_machine/runner.py` Repair 编排** — 替换 `_validate_task_result` 的强制作废逻辑（`runner.py:384-412`），加入 Repair 流程
4. **测试** — 新增 `test_validation_result.py`（三态覆盖）、`test_repair.py`（Repair 流程覆盖）、更新 `test_task_result_v4.py`

### 2.2 本次不做

- 不改路由模型（`next`/`on`/`on_status`/`default` 在第 2 步已完成）
- 不改 `_unroll_loops`（第 4 步）
- 不改 Agent Parser 的 fallback 逻辑（`_parse.py`、`claude_cli.py`、`codex_cli.py` 在第 1 步已完成）
- 不改 YAML 配置格式
- 不新增 CLI 命令

---

## 3. 涉及文件和模块边界

### 3.1 新增文件

| 文件 | 理由 |
|---|---|
| `src/agent_workflow/validators/validation_result.py` | 新三态 `ValidationResult` 类型 + `RouteShape` 数据类。与现有 `base.py` 的 `ValidationResult` 不兼容，故独立新建。 |
| `tests/unit/test_validation_result.py` | 单元测试：三态构造、merge、序列化 |
| `tests/unit/test_repair.py` | 单元测试：Repair 流程（MockAgent + decision_script 模拟 repairable → repair → 成功/耗尽） |

### 3.2 修改文件

| 文件 | 修改内容 | 影响面 |
|---|---|---|
| `src/agent_workflow/validators/__init__.py` | 导出新增的 `RouteShape`、新版 `ValidationResult` | 低 |
| `src/agent_workflow/validators/task_result.py` | 改为纯函数 `validate(data, route_shape) → ValidationResult`；保留 `validate_file()` 便捷方法；增加 Runtime 层（schema_version/必需字段/execution）与 Workflow 层（decision ∈ allowed_decisions）的分层判断；错误按 repairable/not_repairable 分类 | 中 |
| `src/agent_workflow/state_machine/runner.py` | 替换 `_validate_task_result`（`runner.py:669-831`）为三态版本；新增 `_repair_task_result` 方法编排 Repair；替换 `runner.py:384-412` 的强制作废 -> Repair 闸口；`_create_error_result` 确认 decision=None | 高 |
| `src/agent_workflow/agents/mock.py` | `decision_script` 扩展为支持 `status_script`（按 state 返回不同 status 值），演示 `invalid_output→repair` 回流。可选：若不扩展，现有 `decision_script` 已够用。 | 低 |
| `tests/unit/test_task_result_v4.py` | 适配新 Validator 接口（从 `TaskResultValidator(allowed_decisions).validate()` 改为 `validate(data, route_shape)`） | 低 |

### 3.3 不修改的文件

- `src/agent_workflow/validators/base.py` — 保留旧版 `ValidationResult`（artifact/repo/command validator 仍在使用）
- `src/agent_workflow/validators/artifact.py` — 仍用旧 `ValidationResult`
- `src/agent_workflow/tasks/result.py` — 第 1 步已完成改造
- `src/agent_workflow/tasks/result_schema.py` — 第 1 步已完成改造
- `src/agent_workflow/config/models.py` — 第 2 步已完成改造
- `src/agent_workflow/config/loader.py` — 第 2 步已完成 `_unroll_loops`
- `src/agent_workflow/state_machine/machine.py` — 第 2 步已完成两段式路由
- `src/agent_workflow/state_machine/transition.py` — 第 2 步已完成
- 所有 `agents/` 下的 Parser 文件 — 第 1 步已完成

---

## 4. 分步骤实现方案

### 步骤 1：新增 `ValidationResult` 三态类型（估 15 分钟）

**文件**：`src/agent_workflow/validators/validation_result.py`

**内容**：

```python
@dataclass
class RouteShape:
    """节点的路由形态（纯数据，Validator 只读）。"""
    has_on: bool = False          # 是否有 on 字典（分支节点）
    has_next: bool = False        # 是否有 next 字符串（线性节点）
    allowed_decisions: list[str] = field(default_factory=list)

@dataclass
class ValidationResult:
    """三态校验结果。"""
    valid: bool = True            # 是否整体通过
    repairable: bool = False      # 是否可修复（仅在 valid=False 时有意义）
    reason: str = ""              # 人类可读的裁决理由
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
```

**验证方式**：`python -c "from agent_workflow.validators.validation_result import *"` 不报错。

### 步骤 2：改造 `validators/task_result.py` 为纯函数（估 30 分钟）

**文件**：`src/agent_workflow/validators/task_result.py`

**改造内容**：

1. **新增纯函数** `validate(data: dict, route_shape: RouteShape) -> ValidationResult`：
   - **Runtime 层**（不可修复 → `repairable=False`）：
     - `schema_version` < 1
     - 缺少必需字段（`task_id`, `state`, `status`, `summary`, `execution`）
     - `execution.started_at` / `execution.finished_at` / `execution.exit_code` 缺失
     - status 不在 `VALID_STATUSES` 中
   - **Workflow 层**（可修复 → `repairable=True`）：
     - `has_on=True` 且 `decision` 为 `None` → "decision 必填但为空"
     - `has_on=True` 且 `decision` 不在 `allowed_decisions` 中 → "decision 非法"
     - `status == "invalid_output"` → "解析失败，需重新输出"
   - **非阻塞**（warnings）：
     - `has_next=True` 但 `decision` 非空（线性节点不该有 decision，但仅警告）
     - artifacts 中 name/staging_path 缺失

2. **保留** `TaskResultValidator` 类（向后兼容），内部委托给新纯函数：
   - 构造函数接受 `allowed_decisions` + 可选 `route_shape`
   - `validate()` 方法内部调用新纯函数
   - `validate_file()` 方法不变

**关键判断逻辑**：

| 校验项 | 错误级别 | repairable |
|---|---|---|
| schema_version < 1 | blocking | ❌ false |
| 缺少必需字段 | blocking | ❌ false |
| execution.started_at 缺失 | blocking | ❌ false |
| execution.finished_at 缺失 | blocking | ❌ false |
| execution.exit_code 缺失 | warning | — |
| status 无效 | blocking | ❌ false |
| status == "invalid_output" | blocking | ✅ true |
| has_on + decision=None | blocking | ✅ true |
| has_on + decision ∉ allowed | blocking | ✅ true |

**验证方式**：
```bash
$env:PYTHONPATH='src;.'; pytest tests/unit/test_task_result_v4.py -q -k "test_validate or test_decision"
```

### 步骤 3：改造 Runner Repair 编排（估 45 分钟）

**文件**：`src/agent_workflow/state_machine/runner.py`

**改造内容**：

#### 3a. 重写 `_validate_task_result`

```python
def _validate_task_result(self, task_result, state_name: str) -> ValidationResult:
    """三态校验：返回 ValidationResult{valid, repairable, reason}。"""
    state = self.workflow.get_state(state_name)
    task_model = self.workflow.get_task(state.task) if state and state.task else None
    
    route_shape = RouteShape(
        has_on=bool(state.on) if state else False,
        has_next=bool(state.next) if state else False,
        allowed_decisions=task_model.allowed_decisions if task_model else [],
    )
    
    result = validate(task_result.to_dict(), route_shape)
    
    # 仍做 Artifact 校验（不可修复项，合并到 errors）
    # 仍做路径 containment 检查（不可修复项）
    self._validate_artifacts(task_result, state_name, result)
    
    self._last_validation_result = result
    return result
```

#### 3b. 新增 `_repair_task_result`

```python
def _repair_task_result(self, task_result, state_name, validation_result, max_attempts=2):
    """Repair 编排：带反馈重新调用 Agent，限定只重输出 status + decision。
    
    Returns: (repaired_task_result, success: bool)
    """
    for attempt in range(1, max_attempts + 1):
        repair_prompt = (
            f"你的上一次输出校验未通过：{validation_result.reason}\n"
            f"请重新输出，只允许修改 status 和 decision 字段，"
            f"禁止修改 summary/issues/artifacts。"
        )
        # 构建带 repair 反馈的 AgentInput
        agent_input = self._build_repair_input(
            state_name, task_result, repair_prompt
        )
        repaired_result = self._run_agent(...)
        
        # 重新校验
        vr2 = self._validate_task_result(repaired_result, state_name)
        if vr2.valid:
            return repaired_result, True
        
        validation_result = vr2  # 传递新错误到下一轮
    
    # 耗尽 → 置 failed + 取证
    repaired_result.status = "failed"
    repaired_result.decision = None
    repaired_result.issues.append({
        "severity": "blocking",
        "title": "Repair exhausted",
        "detail": f"originally=invalid_output, repair_exhausted after {max_attempts} attempts",
    })
    return repaired_result, False
```

#### 3c. 修改主循环中校验后的处理逻辑（替换 `runner.py:384-412`）

```python
# 当前逻辑（需删除）:
# if has_blocking:
#     task_result.decision = "fail"
#     task_result.status = "invalid_output"
#     ...

# 新逻辑:
validation = self._validate_task_result(task_result, current_state)

if validation.valid:
    # 通过 → promotion + 继续
    self._promote_artifacts(task_result)
elif validation.repairable:
    # 可修复 → Repair 闸口
    repaired_result, repaired_ok = self._repair_task_result(
        task_result, current_state, validation
    )
    task_result = repaired_result
    if repaired_ok:
        self._promote_artifacts(task_result)
    # 否则 status 已被置为 failed，决策走 on_status 或 default
else:
    # 不可修复 → 直接 failed
    task_result.status = "failed"
    task_result.decision = None
```

#### 3d. Repair 与 `guards.max_retries` 协调

- Repair 次数与 state 重试（`max_retries`）独立计数，不共用
- Repair 发生在**单次 state 执行内部**，不触发 state 的 `record_state_visit`
- 若需从 Guard 读取，可新增 `max_repair_attempts` 字段（默认 2），但不作为本次必须

**验证方式**：
```bash
$env:PYTHONPATH='src;.'; pytest tests/unit/test_repair.py -q
$env:PYTHONPATH='src;.'; pytest tests/unit/test_state_machine.py -q
```

### 步骤 4：新增测试 + 存量回归（估 30 分钟）

#### 4a. `tests/unit/test_validation_result.py`

覆盖：
- 三态构造：`valid=True` / `valid=False, repairable=True` / `valid=False, repairable=False`
- `RouteShape` 构造和默认值
- `ValidationResult` 字段完整性

#### 4b. `tests/unit/test_repair.py`

覆盖：
- MockAgent 模拟 `invalid_output` → Repair 成功（第 1 次修复成功）
- MockAgent 模拟决策非法 → Repair → 成功
- MockAgent 模拟 Repair 耗尽（2 次都失败）→ status=failed + issues 取证
- Repair 不修改 summary/artifacts（原字段保持不变）
- 线性节点（has_next=True）无 decision 时不应该触发 Repair

#### 4c. `tests/unit/test_task_result_v4.py` 适配

- 更新 `TaskResultValidator` 调用方式（若接口变化）
- 确保现有决策测试通过

#### 4d. 存量回归

```bash
$env:PYTHONPATH='src;.'; pytest tests/ -q
```

---

## 5. 测试策略

### 5.1 单元测试覆盖点

| 测试类 | 测试点 | 预期结果 |
|---|---|---|
| `TestValidationResult` | `valid=True` 构造 | valid=True, repairable=False, reason 为空 |
| | `valid=False, repairable=True` 构造 | 对应字段正确 |
| | `valid=False, repairable=False` 构造 | 对应字段正确 |
| `TestValidateTaskResult` | 全部合法输入 | valid=True |
| | schema_version=0 | valid=False, repairable=False |
| | 缺少 task_id | valid=False, repairable=False |
| | 缺少 execution.started_at | valid=False, repairable=False |
| | status=invalid_output（解析失败） | valid=False, repairable=True |
| | has_on + decision=None（分支节点缺 decision） | valid=False, repairable=True |
| | has_on + decision 不在 allowed_decisions | valid=False, repairable=True |
| | has_next + decision 非空（线性节点多余 decision） | valid=True, warnings 包含提示 |
| | has_next + decision=None（线性节点正确） | valid=True |
| `TestRepair` | invalid_output → 第 1 次 repair 成功 | task_result.status=success, 继续路由 |
| | decision 非法 → repair 成功 | decision 合规 |
| | repair 耗尽（2 次都失败） | status=failed, issues 含 originally=invalid_output |
| | repair 后 summary/artifacts 不变 | 原字段保持 |
| | 线性节点无 decision → 不触发 repair | 直接路由 |

### 5.2 集成测试覆盖

- 完整 Runner 循环：`invalid_output` 从 Parser 产出 → Validator 判 repairable → Repair → 路由（用 MockAgent + decision_script 模拟）
- `_create_error_result` 返回 `decision=None` + `status="failed"`（已确认第 1 步完成）

### 5.3 回归验证

```bash
$env:PYTHONPATH='src;.'; pytest tests/ -q
```

所有存量测试必须通过，特别关注：
- `test_state_machine.py` — 路由模型不变
- `test_loop_unroll.py` — `_unroll_loops` 不变
- `test_parser_fallback.py` — Parser 逻辑不变

---

## 6. 风险与停止规则

| 风险 | 影响 | 缓解措施 | 停止条件 |
|---|---|---|---|
| Repair 无限循环 | Agent 连续返回非法格式，Repair 耗尽后仍失败 | 硬编码 max_repair_attempts=2，与 max_retries 独立 | 2 次仍失败 → 直接 failed（不阻塞整个 pipeline） |
| 与现有 retry 机制冲突 | 重试次数叠加导致任务超时 | Repair 不修改 state attempt 计数，不触发 Guard.retries | 若发现冲突，将 repair count 写入 `context.workflow_variables` 隔离 |
| 旧版 `ValidationResult`（`base.py`）与新三态混淆 | artifact/repo/command validator 仍用旧格式，两个同名类型并存 | 新三态放在 `validation_result.py` 独立模块，import 时显式指定来源 | 若混淆导致类型错误，立即回退到使用别名 |
| `_validate_artifacts` 逻辑在 Runner 中重复 | 纯函数化后 artifact 校验仍在 Runner 中做路径查找，逻辑分散 | 将 artifact 校验也收拢到纯函数 helpers（仅做数据校验，不做文件 I/O 的仍是纯函数） | 若 artifact 校验路径修正逻辑过复杂，保持现状不迁移 |
| `_create_error_result` decision 已为 None | 某些下游代码（如 status/explain）可能依赖 decision 为字符串 | grep 所有 `get_decision()` 调用方确认 None safe | 若发现 NPE，修复调用方而非回退 |

### 停止规则

1. **存量测试不过 → 停止**：任何修改导致存量测试失败且无法在 30 分钟内修复 → 回退，重新评估方案。
2. **Repair 无法收敛 → 停止**：若 MockAgent 模拟的 Repair 在 2 次内无法产出合法输出（证明 prompt 设计有问题）→ 调整 prompt 再试，超过 3 轮调整仍失败 → 降低 repair 边界（例如只修 decision，不修 status）。
3. **修改波及路由模型 → 停止**：若改动迫使修改 `resolve_transition` / `machine.py` → 立即回退，确认范围边界。

---

## 7. 预期产物

| 产物 | 路径 | 类型 |
|---|---|---|
| 三态 ValidationResult 类型 | `src/agent_workflow/validators/validation_result.py` | 新增 |
| 纯函数 Validator | `src/agent_workflow/validators/task_result.py`（改造） | 修改 |
| Runner Repair 编排 | `src/agent_workflow/state_machine/runner.py`（改造） | 修改 |
| validators __init__.py 导出更新 | `src/agent_workflow/validators/__init__.py` | 修改 |
| MockAgent status_script 扩展（可选） | `src/agent_workflow/agents/mock.py` | 修改 |
| test_validation_result.py | `tests/unit/test_validation_result.py` | 新增 |
| test_repair.py | `tests/unit/test_repair.py` | 新增 |
| test_task_result_v4.py 适配 | `tests/unit/test_task_result_v4.py` | 修改 |
| 存量测试全绿色 | `pytest tests/ -q` 全部通过 | 验收 |

---

## 附录 A：关键代码引用

- 设计文档：`G:\agent-workflow\docs\runtime-v2-design.md` §"Validator：纯函数 + Runner 编排 Repair"
- 改造需求：`step3-spec.txt`
- 当前 Validator：`src/agent_workflow/validators/task_result.py:34-82`
- 当前强制 fail 逻辑：`src/agent_workflow/state_machine/runner.py:384-412`
- 当前 _validate_task_result：`src/agent_workflow/state_machine/runner.py:669-831`
- 第 1 步产出：commit `c638cf2`（契约收敛 + Parser 兜底）
- 第 2 步产出：commit `6f1dd34`（路由模型两段式 + loader 旧格式归一）

## 附录 B：新旧 Validator 接口对比

### 旧接口
```python
validator = TaskResultValidator(allowed_decisions=["approve", "revise"])
result = validator.validate(data)  # → ValidationResult(passed, errors, warnings)
# Runner 侧：
# if result.errors → has_blocking=True → 强制 fail
```

### 新接口
```python
route_shape = RouteShape(has_on=True, has_next=False, allowed_decisions=["approve", "revise"])
result = validate(data, route_shape)  # → ValidationResult(valid, repairable, reason, errors, warnings)
# Runner 侧：
# if result.valid → promote + 路由
# elif result.repairable → Repair(1-2次) → 成功路由 / 耗尽 failed
# else → failed
```
