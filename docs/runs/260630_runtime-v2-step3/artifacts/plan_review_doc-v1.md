# 审核报告：Validator 纯函数化 + Runner Repair 闸口

## 总体评估

**Decision: revise**

计划整体方向正确，与设计文档的核心原则（纯函数 Validator + Runner 编排 Repair）完全对齐。模块边界清晰，步骤划分合理。但存在 **2 个阻塞级问题**和若干需在 refinement 中解决的遗漏。无根本性架构缺陷。

---

## 一、Blocking 问题

### B1 — 新旧 `ValidationResult` 同名冲突（阻塞级）

计划在 `validators/validation_result.py` 新建三态 `ValidationResult`（字段：`valid`, `repairable`, `reason`, `errors`, `warnings`），而 `validators/base.py` 已有同名类（字段：`passed`, `errors`, `warnings`, `metadata`）。两个类型同时存在于同一包内，且 `__init__.py` 当前导出旧版。

计划 §6 风险表承认此问题但缓解措施仅为"import 时显式指定来源"。这在实际开发中极易出错：

- `from agent_workflow.validators import ValidationResult` → 得到旧版（当前 `__init__.py` 行为）
- Runner 中的 `_validate_artifacts` 调用 ArtifactValidator → 返回 `base.ValidationResult(passed, errors, warnings)`
- 计划要求 Runner 将 artifact 校验结果"合并到"新 `ValidationResult` → 新旧字段无直接映射（`passed` vs `valid`, 无 `repairable` 字段）

**问题实例**：计划 §4 Step 3a 伪代码写 `self._validate_artifacts(task_result, state_name, result)` — 传入新 ValidationResult 对象，但 `_validate_artifacts` 内部用的是旧 `base.ValidationResult`（通过 ArtifactValidator），合并代码 `result.errors.extend(ar.errors)` 需要确保两边 errors 字段类型一致。

**要求**：在 refinement 中明确一个方案，选择其一：
- A) 新类型改名 `ValidResult` / `TaskValidationResult`（推荐）
- B) 旧类型保留不动，新的放在 `validation_result.py` 中以 `ValidResult` 命名导出，`__init__.py` 同时导出两个

同时必须明确 artifact 校验结果（旧 `ValidationResult`）如何合并到新类型的契约。

### B2 — `_repair_task_result` 核心路径实现细节缺失（阻塞级）

计划 §4 Step 3b 伪代码中有多处关键实现点未被解决：

1. **`_build_repair_input()` 方法体空白**：伪代码写 `agent_input = self._build_repair_input(state_name, task_result, repair_prompt)` 但未定义此方法。原始 `_execute_state` 流程中 AgentInput 由 `_build_agent_input` 构建，包含 task context、skill_context、staging_paths、schema 等。Repair 调用时这些源数据从哪里取？是复用原始 AgentInput 还是新构？

2. **`_run_agent(...)` 是私有方法还是公共接口**：当前 Runner 执行 Agent 通过 `_execute_state()` → Agent adapter 全流程。Repair 需要绕过正常流程（不触发 StateEntered 事件、不重新写入 staging、不受 Guard 限制），直接调 agent。该路径是否存在？

3. **Repair 结果该不该走完整 Parser**：如果 Repair 后 Agent 又输出格式错误的 JSON，Parser 会再次产出 `invalid_output`。此时应走 Validator → Repair 循环还是直接判定耗尽？当前伪代码只检查 `vr2.valid`，但若二次解析失败（`status=invalid_output`），Validator 应判 `repairable=True` 再进下一轮。这个循环终止条件需要明确。

**要求**：在 refinement 中补充 `_repair_task_result` 的完整实现伪代码，包括 AgentInput 构建方式、Agent 调用路径、循环终止条件的完整决策树。

---

## 二、需求覆盖分析

| 验收点 | 来源 | 覆盖 | 备注 |
|---|---|---|---|
| 新增 ValidationResult 三态类型 | spec §4.1 | ✅ | §4 Step 1 |
| `validate(data, route_shape)` 纯函数 | spec §4.2 | ✅ | §4 Step 2 |
| RouteShape 含 has_on/has_next/allowed_decisions | spec §4.2 | ✅ | §4 Step 1 |
| Runtime 层校验（status/必需字段/execution）| spec §4.2 | ✅ | §4 Step 2 表格 |
| Workflow 层校验（decision ∈ allowed_decisions）| spec §4.2 | ✅ | §4 Step 2 表格 |
| has_on 时 decision 为空 → repairable | spec §4.2 | ✅ | §5 测试表 |
| 绝不调用 Agent | spec §4.2 | ✅ | §1.1 核心原则 |
| 替换 runner.py:384-412 强制 fail | spec §4.3 | ✅ | §4 Step 3c |
| Parser→invalid_output→Validator→Repair | spec §4.3 | ✅ | §4 Step 3b |
| Repair prompt 只允许重输出 status+decision | spec §4.3 | ✅ | §4 Step 3b |
| Repair 耗尽 → failed + issues 取证 | spec §4.3 | ✅ | §4 Step 3b |
| 与 guards.max_retries 协调 | spec §4.3 | ⚠️ | §4 Step 3d 声明独立计数但未说明如何确保不相互干扰。当前 `get_attempt()` 按 state 名从 `workflow_variables` 取计数，Repair 若在同一 state 内多次调用 agent，可能误增 attempt 计数。需明确 Repair 期间 attempt 计数的隔离机制。 |
| 存量测试全通过 | spec §5 | ✅ | 停止规则1 |
| 不改路由模型 | spec §5 | ✅ | §4 Step 3c 只改校验后分支，不改 `resolve_transition` |
| 不改 `_unroll_loops` | spec §5 | ✅ | §3.3 不动文件清单 |
| test_validation_result.py | spec §4.4 | ✅ | §4 Step 4a |
| test_repair.py | spec §4.4 | ✅ | §4 Step 4b |
| 更新 test_task_result_v4.py | spec §4.4 | ✅ | §4 Step 4c |

**遗漏项**：

1. **`invalid_output` 的 status 归属**：根据设计文档，`invalid_output` 在 `VALID_STATUSES` 中。当前 `validators/task_result.py:50` 先做 `status not in VALID_STATUSES → warning`，再做 `status == "invalid_output" → repairable` 的逻辑。但计划 §4 Step 2 表格中 `status 无效 → blocking + repairable=false` 与 `status == "invalid_output" → blocking + repairable=true` 存在重叠判断。若 `invalid_output` 在 `VALID_STATUSES` 中，则它不会触发"status 无效"分支，这是正确的。但计划未明确说明这一依赖关系——若将来有人把 `invalid_output` 从 `VALID_STATUSES` 移除（因为它不应路由），则两个判断互相矛盾。**应在 Validator 文档中注释此依赖**。

2. **`max_retries` 协调机制**：计划 §4 Step 3d 声明"Repair 次数与 state 重试独立计数"，但缺少具体实现锚点。当前 Runner 通过 `context.record_state_visit()` 和 `context.get_attempt()` 追踪 state 访问。Repair 若走 `_run_agent()` 但不经过 `_execute_state()`，则不会触发 `record_state_visit`，attempt 自然不增加。这一假设应在 refinement 中明确写出，避免实现时误调。

---

## 三、主要风险

### R1 — Artifact 校验 + 路径 containment 校验的纯函数边界（高风险）

计划 §4 Step 3a 的伪代码在调用纯函数 `validate(data, route_shape)` 之后，仍调用 `_validate_artifacts` 和 `_check_path_containment` 做文件 I/O。这些步骤**不是纯函数**，产生新的 blocking error（文件不存在 → `repairable=False`），且使用旧 `ValidationResult` 类型。

当前的 `_validate_task_result`（runner.py:669-831）约 160 行，混杂了：
- 数据校验（调 TaskResultValidator）
- staging 路径自动修正（runner.py:719-736）
- worktree 文件复制（runner.py:752-778）
- artifact 文件存在性校验
- 路径 containment 检查

计划将数据校验抽成纯函数（步骤 2），但其余 4 项仍留在 Runner 中，并需要与新 `ValidationResult` 格式交互。这些遗留逻辑的量实际上比纯函数本身大得多。计划低估了这一步的工程量。

**缓解**：在 refinement 中给出 `_validate_task_result` 的**完整**伪代码，把新旧校验逻辑的拼接点写清楚。考虑将 artifact/路径校验也抽成接受新 `ValidationResult` 并原地修改的辅助函数。

### R2 — Repair 中断主循环的风险（中风险）

Repair 调 agent 发生在 `_execute_state` 返回后、`resolve_transition` 之前。如果 agent 子进程执行耗时过长（真实 Agent 可能 30 秒+），且 2 次 Repair 都失败，用户的 run 会在同一 state 上卡住 1 分钟+。`max_duration_minutes` Guard 在计时但不会在此阶段触发（Guard 检查只发生在 state 入口）。

**缓解**：Repair 应继承原有 agent 调用的 `timeout` 配置。在计划中明确 Repair agent 调用的超时参数来源。

### R3 — `test_negative.py` 的现有测试依赖旧接口（中风险）

`test_negative.py:70-128` 有 4 个测试直接构造 `TaskResultValidator()` 或 `TaskResultValidator(allowed_decisions=[...])` 并调用 `.validate(data)`。计划说要保留 `TaskResultValidator` 类向后兼容，但测试中检查的是 `vr.passed`（旧字段）、`vr.errors`、`vr.warnings`。若新纯函数 `validate()` 返回新 `ValidationResult`（`valid` 而非 `passed`），则测试代码需要同步修改。

计划 §4 Step 2 说"保留 `TaskResultValidator` 类（向后兼容），内部委托给新纯函数"。这意味着 `TaskResultValidator.validate()` 应继续返回 `base.ValidationResult`（旧类型）。但如果纯函数返回新类型，委托就需要做类型转换。计划未提及这个转换层。

**缓解**：明确 `TaskResultValidator.validate()` 的返回值类型保持为 `base.ValidationResult`，内部调纯函数后做字段映射（`valid → passed`）。

---

## 四、缺失测试

| 测试点 | 状态 | 说明 |
|---|---|---|
| `repairable=False` 场景 → Runner 直接 failed（不走 Repair） | ❌ 缺失 | §5 测试表无此场景。例如：`schema_version=0` → 应直接 failed + decision=None，不触发 Repair |
| `status=invalid_output` + `has_on=True` + `decision=None` 复合错误 → Repair 修两件事 | ❌ 缺失 | Repair prompt 同时要求修正 status 和 decision，验证一次 Repair 可同时修复两者 |
| Repair 第 1 次失败（仍有 repairable 错误）→ 第 2 次成功 | ❌ 缺失 | §5 测试表有"第 1 次 repair 成功"和"2 次都失败"，缺少"第 1 次失败第 2 次成功"的中间状态 |
| `has_next=True` 节点 + `status=invalid_output` → Repair → 成功 | ❌ 缺失 | 线性节点也应享受 Repair（invalid_output 修复通用） |
| Repair 不触发 `record_state_visit`/不增加 attempt | ❌ 缺失 | 与 max_retries 协调的核心验证 |
| `TaskResultValidator` 类向后兼容 — 返回旧 `ValidationResult(passed, errors, warnings)` | ❌ 缺失 | 确保 test_negative.py 不改动即通过 |

---

## 五、可简化点

### S1 — MockAgent `status_script` 扩展可推迟

计划 §3.2 标记为可选。Repair 测试用 `decision_script` 已够用：MockAgent 返回包含正确 decision 的 TaskResult，测试只需验证 Runner 的 Repair 编排逻辑，而非模拟 `invalid_output` 回流。**建议删掉此项**，减少改动面。

### S2 — `RouteShape` 可作为 NamedTuple

仅 3 个字段、无方法、纯数据载体。用 `NamedTuple` 比 `@dataclass` 更轻量且天然 immutable，符合"纯函数入参"语义。

### S3 — 计划 Step 2 中 `has_next + decision 非空` 的 warning 可推迟

设计文档未要求此项。在 refinement 中可标记为 `nice-to-have`，不在首版必须实现。

---

## 六、上一轮审核追踪

无历史相关 plan_review_doc / plan_refinement_doc（上一轮产出属于不同 run 的不同 feature）。

---

## 七、建议的 refinement 修改方向

1. **解决 `ValidationResult` 命名冲突**：新类型命名为 `ValidResult` 或 `TaskValidResult`，与旧 `ValidationResult` 明确区分。给出 artifact 校验结果合并契约。

2. **补全 `_repair_task_result` 完整伪代码**：包括 AgentInput 构建方式、Agent 调用路径（复用 `_execute_state` 的哪一部分）、循环终止决策树。

3. **扩展 `_validate_task_result` 完整伪代码**：把 5 个步骤（纯函数数据校验 → artifact 文件校验 → staging 修正 → worktree 复制 → 路径 containment）的接口和返回值串联起来。

4. **补充 6 个缺失测试用例**：repairable=False 直达 failed、复合错误一次修复、2 次修复中第 1 次失败的中间态、线性节点 invalid_output repair、Repair 不增加 attempt、TaskResultValidator 向后兼容。

5. **明确 `invalid_output` 与 `VALID_STATUSES` 的依赖关系**并在 Validator 文档中注释。

---

## 八、审核结论

| 维度 | 评级 |
|---|---|
| 架构合理性 | ✅ 好 — 纯函数 Validator + Runner 编排 Repair 正确实现设计意图 |
| 与现有代码库一致性 | ⚠️ ValidationResult 同名冲突 + 旧测试接口兼容未明确方案 |
| 需求覆盖 | ⚠️ max_retries 协调缺少具体锚点 + 2 个边缘场景遗漏 |
| 测试策略 | ⚠️ 缺 6 个边界用例，repairable=False 路径未覆盖 |
| 风险可控性 | ⚠️ artifact 校验逻辑重组的工程量被低估，Repair 实现缺失关键细节 |

**Decision: revise** — 计划可执行，但需在 refinement 中处理 2 个阻塞级问题（B1/B2）及上述测试缺口后再进入 execution。
