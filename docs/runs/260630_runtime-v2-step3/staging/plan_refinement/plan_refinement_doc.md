# 计划修订文档：Validator 纯函数化 + Runner Repair 闸口

## 审核意见逐条回应

### B1 — 新旧 `ValidationResult` 同名冲突（阻塞级）

**结论：已采纳**

**修改方案**：

- 新三态类型命名从 `ValidationResult` 改为 **`ValidResult`**，位于独立模块 `validators/validation_result.py`。
- 旧 `base.ValidationResult` 保留不动，供 artifact/repo/command validator 继续使用。
- `__init__.py` 同时导出两者：`from .validation_result import ValidResult, RouteShape` + 保留原有 `from .base import ValidationResult`。
- 导入方显式区分：Runner 用 `ValidResult`，artifact 校验仍用 `base.ValidationResult`。

**Artifact 校验结果合并契约**：

```python
# artifact validator 返回旧 base.ValidationResult
ar: base.ValidationResult = ArtifactValidator(...).validate(...)
# 合并到新 ValidResult：
if not ar.passed:
    vr.valid = False
    vr.repairable = False  # artifact 缺失不可修复
    vr.errors.extend(ar.errors)
    vr.reason = vr.reason or "Artifact 校验失败"
vr.warnings.extend(ar.warnings)
```

合并发生在 Runner 的 `_validate_task_result` 中，紧接纯函数校验之后。

---

### B2 — `_repair_task_result` 核心路径实现细节缺失（阻塞级）

**结论：已采纳**

**补充完整实现方案**（替换原 §4 Step 3b 的空白伪代码）：

#### AgentInput 构建方式

Repair 的 AgentInput **复用原始 `AgentInput`** 的大部份字段，仅替换 `instruction` 为 Repair prompt：

```python
def _build_repair_agent_input(
    self,
    state_name: str,
    original_task_result: TaskResult,
    validation_result: ValidResult,
    original_agent_input: AgentInput,
) -> AgentInput:
    """基于原始 AgentInput 构建 Repair 专用输入。"""
    repair_instruction = (
        f"你的上一次输出校验未通过。原因：{validation_result.reason}\n"
        f"错误明细：{'; '.join(validation_result.errors)}\n"
        f"请重新输出 TaskResult JSON，**只允许修改 status 和 decision 字段**。\n"
        f"禁止修改 summary、issues、artifacts、execution 等其他字段。\n"
        f"当前 decision 值：{original_task_result.decision}\n"
        f"合法 decision 值：{validation_result.allowed_decisions}\n"
    )
    return AgentInput(
        instruction=repair_instruction,
        task=original_agent_input.task,
        context=original_agent_input.context,
        skill_context=original_agent_input.skill_context,
        staging_paths=original_agent_input.staging_paths,
        schema=original_agent_input.schema,  # 仍然要求输出标准 TaskResult JSON
    )
```

#### Agent 调用路径

Repair 调用**直接走 Agent adapter**，绕过 `_execute_state()`（不触发 StateEntered 事件、不重新写入 staging 顶层目录、不受 Guard 限制）：

```python
def _call_agent_direct(self, agent_input: AgentInput, timeout: int) -> TaskResult:
    """直接调用 agent adapter，绕过 _execute_state 的完整流程。"""
    agent = self._resolve_agent(agent_input.task.agent)
    raw_output = agent.execute(agent_input, timeout=timeout)
    return self._parse_task_result_text(raw_output, agent_input)
```

此方法复用 `_parse_task_result_text`（含 Parser→fallback→Mock→`_create_error_result` 全链路），但 **不经过** `_execute_state` 的 event emit、skill adoption、Guard 检查。

#### 循环终止决策树

```
Repair 入口（validation.repairable == True）
  │
  ├─ attempt=1: _build_repair_agent_input → _call_agent_direct → _parse_task_result_text
  │   │
  │   ├─ Parser 成功 + Validator.valid=True → 返回 (repaired_result, True)  ← 成功退出
  │   ├─ Parser 成功 + Validator.repairable=True → attempt=2（继续循环）
  │   ├─ Parser 成功 + Validator.repairable=False → 不可修复 → 返回 (result, False)
  │   └─ Parser 失败 (_create_error_result, status=failed) → 返回 (result, False)  ← 不可修复
  │
  ├─ attempt=2: 同上流程
  │   ├─ Validator.valid=True → 返回 (repaired_result, True)
  │   └─ 否则 → 返回 (result, False)  ← 耗尽
  │
  └─ 耗尽后处理（在 _repair_task_result 返回 False 后）：
       task_result.status = "failed"
       task_result.decision = None
       task_result.issues.append({
           "severity": "blocking",
           "title": "Repair exhausted",
           "detail": f"originally=invalid_output, repair_exhausted after {max_attempts} attempts"
       })
```

关键保证：
- Repair 调 Agent 但**不经过** `_execute_state()` → 不触发 `record_state_visit()` → attempt 计数不增加。
- Repair 的 agent timeout 继承自 `task_model.timeout`（若配置）或 Runner 默认值（`DEFAULT_AGENT_TIMEOUT_SECONDS`）。
- 每次 Repair 都走完整 Parser + Validator，所以即使 Agent 第二轮又输出非法格式，Parser 会产出 `status=invalid_output`，Validator 会再次判 `repairable=True`，进入下一轮——直到次数耗尽。

---

### 覆盖遗漏 1 — `invalid_output` 与 `VALID_STATUSES` 的依赖关系

**结论：已采纳**

在 `validators/task_result.py` 的 `validate()` 纯函数中加上明确注释：

```python
# 注意：invalid_output 必须在 VALID_STATUSES 中。
# 原因：先做 status 有效性检查（不在 VALID_STATUSES → repairable=False），
# 再做 status=="invalid_output" 检查（→ repairable=True）。
# 如果将来把 invalid_output 移出 VALID_STATUSES，两个判断会矛盾：
# status 无效分支会先拦截并返回 repairable=False，导致 Repair 不可达。
# 维护规则：invalid_output 始终保留在 VALID_STATUSES 中。
```

---

### 覆盖遗漏 2 — `max_retries` 协调缺少具体实现锚点

**结论：已采纳**

在计划中明确写入以下保证：

> Repair 调 Agent 通过 `_call_agent_direct()` → `_parse_task_result_text()`，此路径**不经过** `_execute_state()`。当前 `record_state_visit()` 和 `get_attempt()` 的调用点都在 `_execute_state()` 入口处（`runner.py:322`），因此 Repair 天然不会触发 attempt 计数增加。这是**结构性隔离**，而非运行时判断。
>
> 同时，`guards.max_retries` 检查也只在 `_execute_state()` 入口处执行（`runner.py:318`），Repair 不经过该路径，不会触发 Guard。
>
> 未来若有人将 `record_state_visit` 移到 `_call_agent_direct` 内部，需在 code review 中标记此约束。

---

### R1 — Artifact 校验 + 路径 containment 校验的纯函数边界（高风险）

**结论：已采纳**

给出 `_validate_task_result` 的**完整 5 步伪代码**（替换原 §4 Step 3a 的简化版）：

```python
def _validate_task_result(
    self, task_result: TaskResult, state_name: str, agent_input: AgentInput
) -> ValidResult:
    """
    三态校验。5 个步骤，按顺序执行。
    步骤 1 为纯函数数据校验，步骤 2-5 涉及文件 I/O（非纯函数，留在 Runner 中）。
    """
    state = self.workflow.get_state(state_name)
    task_model = self.workflow.get_task(state.task) if state and state.task else None

    # 构建 RouteShape
    route_shape = RouteShape(
        has_on=bool(state.on) if state else False,
        has_next=bool(state.next) if state else False,
        allowed_decisions=task_model.allowed_decisions if task_model else [],
    )

    # ── 步骤 1：纯函数数据校验 ──
    vr: ValidResult = validate(task_result.to_dict(), route_shape)

    # ── 步骤 2：Artifact 文件存在性校验（使用旧 ValidationResult）──
    if task_result.artifacts:
        for art in task_result.artifacts:
            if art.staging_path:
                staging_full = self.run_root / art.staging_path
                if not staging_full.exists():
                    vr.valid = False
                    vr.repairable = False  # 文件缺失不可修复
                    vr.errors.append(f"staging 文件缺失: {art.staging_path}")
            # 路径 containment 检查
            resolved = (self.run_root / art.staging_path).resolve()
            allowed = (self.run_root / "staging").resolve()
            if not str(resolved).startswith(str(allowed)):
                vr.valid = False
                vr.repairable = False
                vr.errors.append(f"staging 路径逃逸: {art.staging_path}")

    # ── 步骤 3：staging 路径自动修正 ──
    # （仅修正 staging_path 前缀，不改动 ValidResult）
    self._normalize_staging_paths(task_result, state_name)

    # ── 步骤 4：worktree 文件复制 ──
    # （若当前在 worktree 中运行，将文件复制到主 run_root）
    self._copy_worktree_files_to_run_root(task_result)

    # ── 步骤 5：artifact 路径非逃逸复查 ──
    # （对修正/复制后的路径再次做 containment 检查）
    for art in task_result.artifacts:
        if art.artifact_path:
            resolved_art = (self.run_root / "artifacts" / art.artifact_path).resolve()
            allowed_art = (self.run_root / "artifacts").resolve()
            if not str(resolved_art).startswith(str(allowed_art)):
                vr.valid = False
                vr.repairable = False
                vr.errors.append(f"artifact 路径逃逸: {art.artifact_path}")

    # ── 汇总 ──
    if not vr.valid and not vr.reason:
        vr.reason = f"校验失败: {'; '.join(vr.errors[:3])}"
    self._last_validation_result = vr
    return vr
```

**说明**：
- 步骤 1 是纯函数（独立于 Runner，可单测）。
- 步骤 2-5 涉及文件系统操作，保留在 Runner 中，但统一使用 `ValidResult` 作为结果容器。
- `_normalize_staging_paths` 和 `_copy_worktree_files_to_run_root` 是现有方法，行为不变。
- 这 5 个步骤全部在 `_validate_task_result` 一个方法内完成，调用方只需检查 `vr.valid` / `vr.repairable`。

---

### R2 — Repair 中断主循环的风险（中风险）

**结论：已采纳**

在计划中明确：

> Repair 调用的 `_call_agent_direct()` 接受 `timeout` 参数，取值优先级：
> 1. `task_model.timeout`（若 YAML 中配置）
> 2. Runner 实例的 `DEFAULT_AGENT_TIMEOUT_SECONDS`（默认值，与正常 agent 调用一致）
>
> 每次 Repair 都受此超时限制。若 2 次 Repair 各超时，总耗时 = `2 × timeout`。用户可通过 YAML 配置 `task.timeout` 来控制最坏情况下的等待时间。

---

### R3 — `test_negative.py` 的现有测试依赖旧接口（中风险）

**结论：已采纳**

明确向后兼容策略：

> `TaskResultValidator` 类保留，构造函数签名不变：
>
> ```python
> class TaskResultValidator:
>     def __init__(self, allowed_decisions: list[str] | None = None):
>         self._allowed_decisions = allowed_decisions or []
>
>     def validate(self, data: dict):
>         """向后兼容接口，返回 base.ValidationResult（旧类型）。"""
>         route_shape = RouteShape(
>             has_on=bool(self._allowed_decisions),  # 推定为分支节点
>             has_next=False,
>             allowed_decisions=self._allowed_decisions,
>         )
>         new_vr = _validate(data, route_shape)  # 调纯函数 → 返回 ValidResult
>         # 字段映射：ValidResult → base.ValidationResult
>         return base.ValidationResult(
>             passed=new_vr.valid,
>             errors=new_vr.errors,
>             warnings=new_vr.warnings,
>         )
> ```
>
> 此映射确保 `test_negative.py` 中检查 `vr.passed` / `vr.errors` / `vr.warnings` 的代码无需修改。

---

### 缺失测试 1-6（全部已采纳）

以下 6 个测试用例补充到测试表中：

| # | 测试点 | 状态 |
|---|---|---|
| 1 | `repairable=False` 场景 → Runner 直接 failed，不走 Repair（如 `schema_version=0`） | **新增** |
| 2 | `status=invalid_output` + `has_on=True` + `decision=None` 复合错误 → 一次 Repair 同时修复两者 | **新增** |
| 3 | Repair 第 1 次失败（仍有 repairable 错误）→ 第 2 次成功 | **新增** |
| 4 | `has_next=True` 节点 + `status=invalid_output` → Repair → 成功 | **新增** |
| 5 | Repair 不触发 `record_state_visit` / 不增加 attempt | **新增** |
| 6 | `TaskResultValidator` 类向后兼容 — 返回旧 `ValidationResult(passed, errors, warnings)` | **新增** |

---

### S1 — MockAgent `status_script` 扩展可推迟

**结论：已采纳 — 删掉此项**

MockAgent 不做 `status_script` 扩展。Repair 测试使用现有 `decision_script` 已足够。此项从修改文件清单中移除。

---

### S2 — `RouteShape` 可作为 NamedTuple

**结论：已采纳**

```python
from typing import NamedTuple

class RouteShape(NamedTuple):
    """节点的路由形态（纯数据，Validator 只读）。"""
    has_on: bool = False
    has_next: bool = False
    allowed_decisions: tuple[str, ...] = ()
```

`NamedTuple` 天然 immutable，比 `@dataclass` 更轻量，符合"纯函数入参"语义。

---

### S3 — `has_next + decision 非空` 的 warning 可推迟

**结论：已采纳 — 标记为 nice-to-have**

在计划中标记：此 warning 不在首版必须实现。若时间允许再补，否则作为后续优化的 backlog 项。

---

## 修订后完整计划

### 1. 需求理解

将当前 Runner 中"校验失败 → 强制 fail"的一次否决逻辑（`runner.py:384-412`）替换为**三态校验 + 有界自愈**机制：

- **Validator 改为纯函数**：不调用 Agent，只做数据裁决。通过 `RouteShape`（NamedTuple）感知节点路由形态，返回三态 `ValidResult`。
- **Runner 编排 Repair**：当校验返回 `repairable=True` 时，Runner 带反馈直接调用 Agent（绕过 `_execute_state`，限定只重输出 `status` + `decision`），最多 2 次。修理成功则继续路由，耗尽则置 `status=failed` 并留取证痕迹。

核心原则（来自设计文档 §Validator 纯函数 + Runner 编排 Repair）：

> ```
> Validator(data, RouteShape) → ValidResult{valid, repairable, reason}   # 纯函数
> Runner: 读 ValidResult → repairable? → 编排 Repair（有界）→ 路由        # 编排
> ```

### 2. 目标和非目标

**本次要做**：

1. 新增 `ValidResult` 类型 + `RouteShape` NamedTuple（`validators/validation_result.py`）
2. `validators/task_result.py` 改为纯函数 `validate(data, route_shape) → ValidResult`
3. `state_machine/runner.py` Repair 编排（替换 `runner.py:384-412` 强制 fail，新增 `_call_agent_direct` / `_build_repair_agent_input` / `_repair_task_result`）
4. 测试：新增 `test_validation_result.py`、`test_repair.py`，适配 `test_task_result_v4.py`

**本次不做**：

- 不改路由模型（`next`/`on`/`on_status`/`default` 在第 2 步已完成）
- 不改 `_unroll_loops`（第 4 步）
- 不改 Agent Parser 的 fallback 逻辑（第 1 步已完成）
- 不改 YAML 配置格式
- 不新增 CLI 命令
- MockAgent 不做 `status_script` 扩展（已删除）

### 3. 涉及文件和模块边界

#### 3.1 新增文件

| 文件 | 理由 |
|---|---|
| `src/agent_workflow/validators/validation_result.py` | `ValidResult` 三态类型 + `RouteShape` NamedTuple |
| `tests/unit/test_validation_result.py` | 单元测试：三态构造、RouteShape、字段完整性 |
| `tests/unit/test_repair.py` | 单元测试：Repair 全流程（成功/耗尽/不触发 attempt） |

#### 3.2 修改文件

| 文件 | 修改内容 | 影响面 |
|---|---|---|
| `src/agent_workflow/validators/__init__.py` | 导出 `ValidResult`、`RouteShape`（与旧 `ValidationResult` 共存） | 低 |
| `src/agent_workflow/validators/task_result.py` | 新增纯函数 `validate(data, route_shape) → ValidResult`；`TaskResultValidator` 类保留向后兼容（内部委托纯函数 + 字段映射回 `base.ValidationResult`）；文档注释 `invalid_output` 与 `VALID_STATUSES` 依赖 | 中 |
| `src/agent_workflow/state_machine/runner.py` | 替换 `_validate_task_result`（完整 5 步）；新增 `_call_agent_direct`、`_build_repair_agent_input`、`_repair_task_result`；替换 `runner.py:384-412` 的强制 fail → Repair 闸口 | 高 |
| `tests/unit/test_task_result_v4.py` | 适配新 `validate()` 纯函数接口；新增 `TaskResultValidator` 向后兼容测试 | 低 |

#### 3.3 不修改的文件

（与原计划一致 — 路由模型、Parser、config 等）

### 4. 分步骤实现方案

#### 步骤 1：新增 `ValidResult` 三态类型 + `RouteShape`（估 15 分钟）

**文件**：`src/agent_workflow/validators/validation_result.py`

```python
from typing import NamedTuple

class RouteShape(NamedTuple):
    """节点的路由形态（纯数据，Validator 只读，天然 immutable）。"""
    has_on: bool = False
    has_next: bool = False
    allowed_decisions: tuple[str, ...] = ()

@dataclass
class ValidResult:
    """三态校验结果。命名与旧 base.ValidationResult 明确区分。"""
    valid: bool = True
    repairable: bool = False
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
```

**验证方式**：`python -c "from agent_workflow.validators.validation_result import ValidResult, RouteShape"`

#### 步骤 2：改造 `validators/task_result.py` 为纯函数（估 35 分钟）

**新增纯函数** `validate(data: dict, route_shape: RouteShape) -> ValidResult`：

- **Runtime 层**（不可修复 → `repairable=False`）：
  - `schema_version` < 1
  - 缺少必需字段（`task_id`, `state`, `status`, `summary`, `execution`）
  - `execution.started_at` / `finished_at` / `exit_code` 缺失
  - status 不在 `VALID_STATUSES` 中
- **Workflow 层**（可修复 → `repairable=True`）：
  - `has_on=True` 且 `decision` 为 `None`
  - `has_on=True` 且 `decision` 不在 `allowed_decisions` 中
  - `status == "invalid_output"`
- **非阻塞 warnings**：
  - artifacts 中 name/staging_path 缺失
  - （nice-to-have）`has_next=True` 但 `decision` 非空 — 首版不实现，标记 TODO

**关键依赖注释**：`invalid_output` 必须在 `VALID_STATUSES` 中，否则 status 无效分支会先拦截 → repairable=False → Repair 不可达。

**向后兼容**：`TaskResultValidator` 类保留，内部委托给 `validate()` 纯函数，返回 `base.ValidationResult`（字段映射：`valid → passed`）。

**校验判断表**：

| 校验项 | 错误级别 | repairable |
|---|---|---|
| schema_version < 1 | blocking | ❌ false |
| 缺少必需字段 | blocking | ❌ false |
| execution.started_at 缺失 | blocking | ❌ false |
| execution.finished_at 缺失 | blocking | ❌ false |
| execution.exit_code 缺失 | warning | — |
| status 无效（不在 VALID_STATUSES） | blocking | ❌ false |
| status == "invalid_output" | blocking | ✅ true |
| has_on + decision=None | blocking | ✅ true |
| has_on + decision ∉ allowed | blocking | ✅ true |

**验证**：`pytest tests/unit/test_task_result_v4.py -q`

#### 步骤 3：改造 Runner Repair 编排（估 60 分钟）

##### 3a. 新增 `_call_agent_direct` 方法

```python
def _call_agent_direct(
    self, agent_input: AgentInput, timeout: int
) -> TaskResult:
    """直接调用 agent adapter + Parser，绕过 _execute_state。
    不触发 StateEntered 事件、不触发 record_state_visit、不受 Guard 限制。
    """
    agent = self._resolve_agent(agent_input.task.agent)
    raw_output = agent.execute(agent_input, timeout=timeout)
    return self._parse_task_result_text(raw_output, agent_input)
```

##### 3b. 新增 `_build_repair_agent_input` 方法

（见上文 B2 回应的完整伪代码）

##### 3c. 新增 `_repair_task_result` 方法

（见上文 B2 回应的完整决策树伪代码）

##### 3d. 重写 `_validate_task_result`

（见上文 R1 回应的完整 5 步伪代码）

##### 3e. 替换主循环校验后处理逻辑（`runner.py:384-412`）

```python
# 旧逻辑（删除）：
# if has_blocking:
#     task_result.decision = "fail"
#     task_result.status = "invalid_output"

# 新逻辑：
validation = self._validate_task_result(task_result, current_state, agent_input)

if validation.valid:
    self._promote_artifacts(task_result)
elif validation.repairable:
    repaired_result, repaired_ok = self._repair_task_result(
        task_result, current_state, validation, agent_input
    )
    task_result = repaired_result
    if repaired_ok:
        self._promote_artifacts(task_result)
    # 否则：status=failed + decision=None，后续走 on_status 或 default → failed
else:
    # 不可修复 → 直接 failed
    task_result.status = "failed"
    task_result.decision = None
```

##### 3f. Repair 与 `guards.max_retries` 协调

- Repair 通过 `_call_agent_direct()` 调 Agent，**不经过** `_execute_state()`。
- `record_state_visit()` 调用点在 `_execute_state()` 入口处 → Repair 不会触发。
- `guards.max_retries` 检查也在 `_execute_state()` 入口 → Repair 不会触发。
- 结构性隔离，非运行时判断。未来修改需在 code review 中保持此约束。
- Repair timeout：优先级 `task_model.timeout` → `DEFAULT_AGENT_TIMEOUT_SECONDS`。

**验证**：`pytest tests/unit/test_repair.py tests/unit/test_state_machine.py -q`

#### 步骤 4：测试（估 35 分钟）

##### 4a. `tests/unit/test_validation_result.py`

覆盖：
- `ValidResult` 三态构造：`valid=True` / `valid=False+repairable=True` / `valid=False+repairable=False`
- `RouteShape` 构造和默认值、immutable 性
- `ValidResult` 字段完整性

##### 4b. `tests/unit/test_repair.py`

覆盖（含新增的 6 个用例）：

| # | 测试点 | 预期结果 |
|---|---|---|
| 1 | MockAgent 模拟 `invalid_output` → Repair 第 1 次成功 | status=success, 继续路由 |
| 2 | MockAgent 模拟 decision 非法 → Repair → 成功 | decision 合规 |
| 3 | Repair 耗尽（2 次都失败） | status=failed, issues 含 `originally=invalid_output, repair_exhausted` |
| 4 | Repair 后 summary/artifacts 不变 | 原字段保持 |
| 5 | 线性节点无 decision → 不触发 Repair | 直接路由 |
| 6 | `repairable=False` → Runner 直接 failed | 不走 Repair，decision=None |
| 7 | 复合错误（invalid_output + decision=None）一次修复 | 两个问题同时被修正 |
| 8 | Repair 第 1 次失败第 2 次成功 | 中间态正确处理 |
| 9 | 线性节点 + invalid_output → Repair → 成功 | 线性节点也享受 Repair |
| 10 | Repair 不触发 `record_state_visit` | attempt 计数不变 |

##### 4c. `tests/unit/test_task_result_v4.py` 适配

- 更新 `TaskResultValidator` 调用测试（若接口有变）
- 新增：`TaskResultValidator` 向后兼容 — 返回 `base.ValidationResult(passed, errors, warnings)`

##### 4d. 存量回归

```bash
$env:PYTHONPATH='src;.'; pytest tests/ -q
```

所有存量测试通过。特别关注：
- `test_state_machine.py` — 路由模型不变
- `test_loop_unroll.py` — `_unroll_loops` 不变
- `test_parser_fallback.py` — Parser 逻辑不变
- `test_negative.py` — TaskResultValidator 向后兼容

### 5. 风险与停止规则

（与原计划一致，补充一项）

| 风险 | 影响 | 缓解措施 | 停止条件 |
|---|---|---|---|
| Repair 无限循环 | Agent 连续返回非法格式 | 硬编码 max_repair_attempts=2 | 2 次仍失败 → 直接 failed |
| 与现有 retry 机制冲突 | 重试次数叠加 | Repair 不经过 `_execute_state`，结构性隔离 | 若发现意外触发 → 加隔离断言 |
| 新旧 ValidationResult 混淆 | 类型错误 | 新类型命名 `ValidResult`，旧 `base.ValidationResult` 保留 | 若混淆 → 用 mypy/pyright 类型检查 |
| Artifact 校验逻辑重组 | 工程量大 | 保持现有 helper 方法不动，仅统一结果容器 | 超过 2h 未完成 → 只改数据校验层 |
| Repair 超时卡主循环 | 用户等待过长 | Repair 继承 task_model.timeout | 同上 |
| `_create_error_result` decision 已为 None | 下游 NPE | grep 确认所有 `get_decision()` 调用方 None-safe | 若 NPE → 修复调用方 |

### 6. 预期产物

| 产物 | 路径 | 类型 |
|---|---|---|
| ValidResult 三态类型 + RouteShape | `src/agent_workflow/validators/validation_result.py` | 新增 |
| 纯函数 Validator | `src/agent_workflow/validators/task_result.py`（改造） | 修改 |
| Runner Repair 编排 | `src/agent_workflow/state_machine/runner.py`（改造） | 修改 |
| validators __init__.py 导出更新 | `src/agent_workflow/validators/__init__.py` | 修改 |
| test_validation_result.py | `tests/unit/test_validation_result.py` | 新增 |
| test_repair.py | `tests/unit/test_repair.py` | 新增 |
| test_task_result_v4.py 适配 | `tests/unit/test_task_result_v4.py` | 修改 |
| 存量测试全绿色 | `pytest tests/ -q` 全部通过 | 验收 |

---

## 相对上一版 plan_doc 的关键变更

| # | 变更项 | 旧版 | 新版 | 原因 |
|---|---|---|---|---|
| 1 | 新三态类型命名 | `ValidationResult` | **`ValidResult`** | 避免与 `base.ValidationResult` 同名冲突（B1） |
| 2 | RouteShape 类型 | `@dataclass` | **`NamedTuple`** | 更轻量、天然 immutable（S2） |
| 3 | `_validate_task_result` | 简化 2 步伪代码 | **完整 5 步伪代码**（数据校验 → artifact 文件校验 → staging 修正 → worktree 复制 → 路径 containment） | 补全 artifact 校验逻辑重组方案（R1） |
| 4 | `_repair_task_result` | 空白伪代码 | **完整实现方案**：`_build_repair_agent_input` + `_call_agent_direct` + 循环终止决策树 | 补全核心实现细节（B2） |
| 5 | `TaskResultValidator` 向后兼容 | 未说明映射 | **明确字段映射**：`valid → passed`，返回 `base.ValidationResult` | 确保 `test_negative.py` 不改动即通过（R3） |
| 6 | `invalid_output` 与 `VALID_STATUSES` | 未提及依赖 | **文档注释**两者的依赖关系 | 防止未来维护时引入矛盾（覆盖遗漏1） |
| 7 | Repair 与 max_retries 隔离 | "独立计数" | **结构性隔离**：Repair 不经过 `_execute_state()` → 不触发 `record_state_visit` | 补全实现锚点（覆盖遗漏2） |
| 8 | Repair timeout | 未提及 | **继承 `task_model.timeout`** | 避免 Repair 卡主循环（R2） |
| 9 | 测试用例数 | 11 个 | **17 个**（+6 个边界用例） | 补充缺失覆盖 |
| 10 | MockAgent `status_script` | 可选修改 | **删除** | 减少改动面（S1） |
| 11 | `has_next + decision 非空` warning | 必须实现 | **nice-to-have / 延后** | 设计文档未要求（S3） |
| 12 | Artifact 校验合并契约 | 未定义 | **明确**：`ar.passed → vr.valid`，`repairable=False`，`errors/warnings` 合并 | B1 子要求 |
