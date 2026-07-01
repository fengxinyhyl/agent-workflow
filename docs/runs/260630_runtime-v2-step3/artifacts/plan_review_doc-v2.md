# 审核报告（第二轮）：Validator 纯函数化 + Runner Repair 闸口

## 总体评估

**Decision: approve**

修订后的计划（`plan_refinement_doc-v1`）全面、扎实地回应了第一轮审核（`plan_review_doc-v1`）提出的全部 14 条意见（2 个阻塞级 + 3 个风险 + 2 个覆盖遗漏 + 6 个缺失测试 + 3 个可简化点 + 额外建议）。修订质量高，未引入新的阻塞级问题。计划可以进入 execution。

---

## 一、第一轮审核问题追踪

下表核对了第一轮审核的所有意见在修订计划中的处理情况。

| # | 第一轮意见 | 类型 | 修订计划处理 | 状态 |
|---|---|---|---|---|
| B1 | ValidationResult 命名冲突 | 阻塞 | 新类型命名 `ValidResult`，旧 `base.ValidationResult` 保留；给出 artifact 合并契约（`ar.passed → vr.valid`） | ✅ 已处理 |
| B2 | `_repair_task_result` 实现缺失 | 阻塞 | 补充 `_build_repair_agent_input` + `_call_agent_direct` + 完整决策树 | ✅ 已处理 |
| C1 | `invalid_output` 与 `VALID_STATUSES` 依赖未注释 | 覆盖 | 添加文档注释，明确"invalid_output 必须在 VALID_STATUSES 中" | ✅ 已处理 |
| C2 | `max_retries` 协调缺实现锚点 | 覆盖 | 明确结构性隔离：Repair 不经过 `_execute_state()` → 不触发 `record_state_visit` | ✅ 已处理 |
| R1 | Artifact 校验纯函数边界低估 | 风险 | 给出完整 5 步 `_validate_task_result` 伪代码 | ✅ 已处理 |
| R2 | Repair 超时风险 | 风险 | 明确 timeout 优先级 `task_model.timeout → DEFAULT_AGENT_TIMEOUT_SECONDS` | ✅ 已处理 |
| R3 | `test_negative.py` 旧接口依赖 | 风险 | 明确 `TaskResultValidator` 向后兼容：字段映射 `valid → passed`，返回旧 `ValidationResult` | ✅ 已处理 |
| M1 | `repairable=False → Runner 直接 failed` | 缺测 | 新增为测试用例 #6 | ✅ 已补 |
| M2 | 复合错误一次修复 | 缺测 | 新增为测试用例 #7 | ✅ 已补 |
| M3 | 第 1 次失败第 2 次成功 | 缺测 | 新增为测试用例 #8 | ✅ 已补 |
| M4 | 线性节点 + invalid_output Repair | 缺测 | 新增为测试用例 #9 | ✅ 已补 |
| M5 | Repair 不触发 attempt | 缺测 | 新增为测试用例 #10 | ✅ 已补 |
| M6 | TaskResultValidator 向后兼容 | 缺测 | 新增为 4c 节测试 + 回归项 | ✅ 已补 |
| S1 | MockAgent status_script 过度 | 简化 | 从修改文件清单删除 | ✅ 已采纳 |
| S2 | RouteShape 用 NamedTuple | 简化 | 改为 `NamedTuple`（天然 immutable） | ✅ 已采纳 |
| S3 | `has_next + decision 非空` warning 过度 | 简化 | 标记为 nice-to-have / 首版不实现 | ✅ 已采纳 |

**结论**：14/14 条全部处理，无遗漏。

---

## 二、修订后计划的增量审查

虽然第一轮问题已全部解决，但修订引入了新的伪代码和接口约定，需要做第二轮深度审查。

### 2.1 接口一致性检查

| 检查项 | 发现 | 严重度 |
|---|---|---|
| `ValidResult` 定义 vs Repair prompt 引用 | 修订 §4 步骤 1 `ValidResult` 无 `allowed_decisions` 字段，但 B2 回应的 `_build_repair_agent_input` 伪代码写 `{validation_result.allowed_decisions}`。`allowed_decisions` 属于 `RouteShape` 而非 `ValidResult`——实际实现应从 `route_shape` 取。 | ⚠️ 轻微不一致 |
| `_repair_task_result` 签名 | §3e 调用 `_repair_task_result(task_result, current_state, validation, agent_input)` 传 4 个参数，但 §3c 方法签名未给出（仅给了决策树）。B2 响应中 `_repair_task_result(task_result, state_name, validation_result, max_attempts=2)` 只传 3/4 个参数且无 `agent_input`。方法签名需统一。 | ⚠️ 轻微不一致 |
| `_call_agent_direct` 的 `agent.execute()` timeout | 伪代码调 `agent.execute(agent_input, timeout=timeout)`。实际 agent adapter 接口是否接受 `timeout` 参数需实现时验证。若 agent adapter 通过 `AgentInput` 或其他机制传递超时，此调用需调整。 | ℹ️ 实现细节 |
| `_parse_task_result_text` 签名 | 伪代码调 `self._parse_task_result_text(raw_output, agent_input)`。此方法可能在第 1 步被重命名或改签名。实现时需确认。 | ℹ️ 实现细节 |
| `_normalize_staging_paths` / `_copy_worktree_files_to_run_root` 存在性 | 5 步伪代码引用这两个现有方法。计划声明它们"行为不变"，但未验证它们在当前代码中的确切签名。 | ℹ️ 实现细节 |

以上 5 条均为**实现时即可发现并修正**的细节问题，不构成阻塞。

### 2.2 5 步 `_validate_task_result` 的 artifact 校验路径分析

修订后的 5 步伪代码将 Artifact 校验**内联**到 `_validate_task_result` 中（步骤 2 直接遍历 `task_result.artifacts` 检查文件存在性及路径 containment），而非调用 `ArtifactValidator`。

**评估**：
- ✅ 这比旧方案（调 `ArtifactValidator` 得旧 `ValidationResult` 再 merge）更简洁
- ✅ 路径 containment 检查出现两次（步骤 2 staging + 步骤 5 artifact），与现有行为一致
- ℹ️ `ArtifactValidator` 类可能失去部分调用方——不是本步问题，后续清理即可
- ⚠️ 步骤 2 和步骤 5 之间夹着 `_normalize_staging_paths`（步骤 3）和 `_copy_worktree_files_to_run_root`（步骤 4），这两个方法**修改 `task_result.artifacts[*].staging_path`**。步骤 5 用修正后的路径复查 containment，逻辑正确

### 2.3 Repair 调用链完备性

```
_execute_state() → Parser 产出 invalid_output
  → _validate_task_result() → ValidResult(valid=False, repairable=True)
    → _repair_task_result()
      → for attempt in (1,2):
          _build_repair_agent_input()  # 基于原始 AgentInput + repair prompt
          → _call_agent_direct()       # agent.execute() → _parse_task_result_text()
            → Parser 解析（可能再出 invalid_output 或合法输出）
          → _validate_task_result()    # 重新走完整 5 步校验
            → valid=True → 返回成功
            → repairable=True → 继续循环（若 attempt < 2）
            → repairable=False → 返回失败
      → 耗尽：置 status=failed, decision=None, issues 留取证
    → 回到主循环：status=failed → 走 on_status 或 default
```

此链中：
- ✅ 每轮 Repair 都走完整 Parser（即使 Agent 二次输出非法 JSON，Parser 仍会产出 `invalid_output`，Validator 再判 `repairable`）
- ✅ 终止条件覆盖所有分支：valid 成功 / repairable=False 不可救 / 次数耗尽
- ✅ `_create_error_result`（Agent 进程崩溃）产出 `status=failed`，它不是 `invalid_output`，不会被 Validator 判 `repairable`，正确
- ✅ 取证记录格式 `originally=invalid_output, repair_exhausted after N attempts` 符合设计文档要求

### 2.4 与 Guards 的隔离确认

修订计划明确了结构性隔离：

```
_execute_state() 入口:
  - guard.check_max_visits()   ← Repair 不经过此处
  - guard.check_max_duration() ← Repair 不经过此处
  - context.record_state_visit() ← Repair 不经过此处

Repair 走 _call_agent_direct():
  - 绕过 _execute_state()
  - 不触发任何 Guard 检查
  - 不增加 attempt 计数
```

✅ 隔离机制依赖代码结构而非运行时判断，可靠性高。
⚠️ 未来维护风险：若有人将 `record_state_visit` 下沉到 `_call_agent_direct` 级别，此隔离会破。计划已注明"需在 code review 中保持此约束"。

---

## 三、需求覆盖（最终核查）

对照 `step3-spec.txt` 逐条覆盖：

| spec 条目 | 要求 | 修订计划覆盖 |
|---|---|---|
| 1 | 新增 ValidationResult 三态类型 | ✅ `ValidResult`（`valid`, `repairable`, `reason`, `errors`, `warnings`） |
| 2 | `validate(data, route_shape) → ValidationResult` | ✅ 纯函数 `validate()`，`RouteShape` 含 `has_on`, `has_next`, `allowed_decisions` |
| 3 | 绝不调用 Agent | ✅ 纯函数无任何 agent 调用 |
| 4 | 替换 runner.py:384-412 强制 fail | ✅ 三态分支 + Repair 闸口替换 |
| 5 | Parser→invalid_output→Validator→Repair | ✅ 完整链路已建模 |
| 6 | Repair 有界 1-2 次 | ✅ `max_attempts=2`，硬编码 |
| 7 | Repair prompt 只允许重输出 status+decision | ✅ prompt 模板明确约束 |
| 8 | 耗尽→failed + issues 取证 | ✅ `originally=invalid_output, repair_exhausted` |
| 9 | 与 guards.max_retries 协调 | ✅ 结构性隔离 |
| 10 | test_validation_result.py | ✅ §4 步骤 4a |
| 11 | test_repair.py | ✅ §4 步骤 4b（10 个用例） |
| 12 | 存量测试全通过 | ✅ 停止规则 1 + 回归验证清单 |
| 13 | 不改路由模型 | ✅ 不动文件清单确认 |
| 14 | 不改 `_unroll_loops` | ✅ 不动文件清单确认 |

**覆盖度：14/14，无遗漏，无超范围。**

---

## 四、主要风险（残余）

第一轮识别的 3 个风险（R1/R2/R3）已在修订中缓解。本轮增量风险：

| 风险 | 严重度 | 说明 | 缓解 |
|---|---|---|---|
| `_call_agent_direct` 绕过 skill adoption | 低 | 原始 `_execute_state` 在执行 agent 前加载 required skills + task skills。Repair 绕过此步骤，Agent 在 repair 时没有 skill 上下文。但由于 repair prompt 只要求"修正 status/decision"，skill 缺失不太可能影响输出。 | 若 repair 测试中发现 Agent"忘记"输出格式，考虑在 `_build_repair_agent_input` 中附带 schema 约束 |
| 行号引用过时 | 低 | runner.py:384-412 和 :669-831 的引用基于 step 2 完成后的代码快照，但 plan 是文档层面引用，实现时需按内容定位。 | 实现时 grep `has_blocking` / `"fail"` / `"invalid_output"` 定位 |

---

## 五、缺失测试（第二轮）

第一轮 6 个缺失测试已全部补入。本轮未发现新的测试缺口。当前测试矩阵共 10 个 Repair 用例 + 4 个 ValidResult 用例 + 2 个向后兼容用例 = 16 个独立测试点，覆盖充分。

以下边缘场景**已有隐含覆盖**，无需新增：
- Agent 在 repair 中返回完全空 JSON → Parser fallback 产出 `invalid_output` → Validator 判 `repairable` → 继续循环（含在用例 #3 的"第 1 次失败"路径）
- Agent 进程崩溃（exit 127）→ `_create_error_result` 产 `status=failed` → Validator 判 `valid=True`（failed 是合法 status）→ 直接走 on_status/default 路由（非 repair 路径）

---

## 六、可简化点（第二轮）

修订计划已处理 S1/S2/S3。本轮无新增可简化点——计划当前处于合理的复杂度水平。

---

## 七、Do-not-change 约束

以下约束来自 design doc 和 step 1/2 产出，实施时必须遵守：

1. **不改 `resolve_transition`**（`machine.py`）——路由逻辑在第 2 步已完成
2. **不改 `_unroll_loops`**（`loader.py`）——第 4 步范畴
3. **不改 Agent Parser 的 fallback 逻辑**——第 1 步已完成
4. **不改 YAML 配置格式**
5. **`base.ValidationResult` 不删除、不修改字段**——artifact/repo/command validator 仍依赖
6. **`TaskResultValidator` 公开接口不变**——`validate(data)` 和 `validate_file(path)` 签名保持

---

## 八、审核结论

| 维度 | 第一轮评级 | 第二轮评级 | 变化 |
|---|---|---|---|
| 架构合理性 | ✅ 好 | ✅ 好 | — |
| 与现有代码库一致性 | ⚠️ 同名冲突 | ✅ 已解决 | ↑ |
| 需求覆盖 | ⚠️ 2 个边缘遗漏 | ✅ 全覆盖 | ↑ |
| 测试策略 | ⚠️ 缺 6 个边界用例 | ✅ 16 个独立测试点 | ↑ |
| 风险可控性 | ⚠️ 工程量低估 + 实现缺失 | ✅ 风险已缓解 | ↑ |
| 代码复杂度 | ✅ 合理 | ✅ 合理 | — |

**Decision: approve** — 修订计划已充分处理第一轮审核的全部意见，需求覆盖完整，风险可控，测试策略充分。3 个轻微接口不一致（见 §2.1）均为实现时即可发现并修正的细节，不构成阻塞。计划可进入 execution。

---

## 附录：修订计划变更追踪

相对第一版 plan_doc 的 12 项关键变更汇总：

| # | 变更 | 效果 |
|---|---|---|
| 1 | `ValidationResult` → `ValidResult` | 消除与 `base.ValidationResult` 的命名冲突 |
| 2 | `RouteShape`: `@dataclass` → `NamedTuple` | 更轻量，天然 immutable |
| 3 | `_validate_task_result`: 2 步 → 完整 5 步 | artifact 校验重组方案透明化 |
| 4 | `_repair_task_result`: 空白 → 完整决策树 | 核心实现无盲区 |
| 5 | `TaskResultValidator`: 无映射 → 字段映射 `valid→passed` | 向后兼容保证 |
| 6 | `invalid_output` 与 `VALID_STATUSES`：无注释 → 文档注释 | 防止未来维护错误 |
| 7 | Repair vs max_retries："独立计数" → "结构性隔离" | 隔离机制可验证 |
| 8 | Repair timeout：无 → 继承 `task_model.timeout` | 卡主循环风险缓解 |
| 9 | 测试用例：11 → 17（含回归） | 边界覆盖补齐 |
| 10 | MockAgent `status_script`：有 → 删除 | 减少改动面 |
| 11 | `has_next + decision` warning：必须 → nice-to-have | 简化首版交付 |
| 12 | Artifact 合并契约：未定义 → 明确 | B1 子要求闭合 |
