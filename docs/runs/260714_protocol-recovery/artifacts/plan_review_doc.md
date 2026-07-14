# 协议恢复能力迭代 — 开发计划审核报告

> 审核对象：`plan_doc-v1`（staging/planning/plan_doc.md）
> 依据：`docs/protocol-recovery-design.md`（设计草案 §1–§9） + goal 落点清单
> 审核结论：**approve** — 计划可执行，存在 5 个需在实施中注意的观测项，不阻塞进入执行阶段。

---

## 1. 审核结论总览

| 维度 | 评价 | 说明 |
|------|------|------|
| 需求覆盖 | ✅ 完整 | 8 条验收标准、6 条 goal 要求、设计稿 §6 全部落点均有对应步骤 |
| 架构合理性 | ✅ 良好 | 恢复算法收敛在 `_parse.py`（纯函数）、事件在 runner.py、模型在 result.py，模块边界清晰 |
| 向后兼容 | ✅ 充分 | 新参数默认值保证旧调用点零改动；protocol_origin 缺省 `native`、recovery 缺省 None 保证老数据反序列化 |
| 测试覆盖 | ✅ 充分 | 新文件 + 4 个已有文件扩展，覆盖 L1/L2/冲突/线性零污染/native 优先/repair/事件/兼容 |
| 可简化点 | 🟡 1 个 | §3 文件表对 `ValidResult` 的声称与步骤不一致（详见 §3.6） |
| Blocking 问题 | 🟢 无 | 未发现导致计划不可行的根本缺陷 |

---

## 2. 需求覆盖逐条核对

### 2.1 设计稿 §9 验收标准 8 条

| # | 验收标准 | 覆盖步骤 | 审核 |
|---|---------|---------|------|
| 1 | Level 1 唯一命中 → 恢复；冲突/无命中 → 不恢复 | Step 2 (§4 步骤2) | ✅ 引导词表、完整 token 边界、唯一性裁决三重约束完整 |
| 2 | 线性节点不传 allowed_decisions → 不恢复 | Step 3 (§4 步骤3) | ✅ `allowed_decisions=None` → 恢复分支不触发 |
| 3 | 有合法 JSON 时结构化路径优先 | Step 3 | ✅ 恢复仅在 `_parse_task_result_text` 最终 `return None` 前插入 |
| 4 | Repair 瘦身为格式转换 + IO 退化不崩 | Step 7 (§4 步骤7) | ✅ 经 `task.output`+Resolver 取产物、禁硬编码文件名、try/except 兜底 |
| 5 | ProtocolRecovery 事件含 `origin_text_hash` | Step 5 (§4 步骤5) | ✅ registry 明列 `origin_text_hash` 为必填字段 |
| 6 | 老 TaskResult 反序列化后 protocol_origin=native, recovery=None | Step 1 (§4 步骤1) | ✅ `ExecutionMetadata.from_dict()` 设缺省值 |
| 7 | Level 2 同义词恢复默认关闭 | Step 2/3/4 | ✅ `enable_synonym_recovery=False` 默认 + skill_policy 通道预留 |
| 8 | 全量 pytest 无回归 | §5 测试策略 | ✅ 回归步骤明确，含 unit + integration |

### 2.2 Goal 落点清单 6 条

| # | Goal 要求 | 覆盖步骤 | 审核 |
|---|----------|---------|------|
| 1 | ExecutionMetadata +protocol_origin +recovery | Step 1 | ✅ 手写 to_dict/from_dict，嵌套 RecoveryInfo 正确处理 |
| 2 | `_recover_decision_from_prose` (L1 regex, allowed_decisions 参数) | Step 2 | ✅ 签名、算法、confidence 完整 |
| 3 | runner: parser 恢复放行 + Repair 瘦身 + repair 内 origin=repair | Step 6 + 7 | ✅ 恢复走 valid 分支天然放行；见 §3.3 观测项 |
| 4 | events.py +ProtocolRecovery (含 origin_text_hash) | Step 5 | ✅ |
| 5 | 完整单元测试覆盖 | §5 | ✅ 5 个测试文件/维度 |
| 6 | Level 2 默认关闭 (Feature Flag) | Step 2/3/4 | ✅ |

### 2.3 超范围检查

- ❌ Confidence 阈值路由（Phase 2）— 计划明确列入非目标
- ❌ L4 模型只吐语义（runtime-v3）— 明确列入非目标
- ❌ 离线 recovery_rate 聚合工具 — 未涉及
- ❌ 改 WorkflowConfig/YAML schema — 明确列入非目标

无超范围内容。

---

## 3. 观测项（非阻塞，实施中注意即可）

### 3.1 🟡 恢复 TaskResult 的 execution 元数据填充

**问题**：`_parse_task_result_text` 构造恢复 TaskResult 时创建 `ExecutionMetadata(protocol_origin="parser", recovery=RecoveryInfo(...))`，其 `started_at`/`finished_at` 为空字符串。Runner 的 execution 填充逻辑（runner.py:1147）检查 `if result and not result.execution:`，由于 ExecutionMetadata 对象为 truthy，不会触发填充。

**影响**：TaskFinished 事件 duration_seconds=0，且 `execution.started_at` 为空可能在下游展示（status/explain/history）中显示异常。

**缓解**：恢复结果由 adapter（claude_cli/codex_cli）返回前填充 `started_at`/`finished_at`（adapter 自身已有 CLI 进程起止时间）。或在 runner 中放宽填充条件：检查 `execution.started_at` 为空而非 `not execution`。

**建议**：Step 4（adapter 透传）实施时同步处理——adapter 在调用 `_parse_task_result_text` 得到恢复结果后，从已有计时变量回填 execution 时间字段。

---

### 3.2 🟡 `_extract_task_result_fallback` 与 recovery 的执行顺序

**问题**：当前 `_parse_task_result_text` 中，```json 块损坏时先走 `_extract_task_result_fallback`（line 48），若它返回了含 `decision=None` 的部分结果，recovery 不会再介入。存在一个窄窗口：模型吐了损坏 JSON 块（如截断），fallback 提取了 status/summary 但没提取到 decision，此时散文中的明确决策（如"决策 **revise**"）无法被 recovery 兜底。

**实际风险**：极低。要同时满足 (a) 有 ```json 块、(b) 块损坏到 json.loads 失败、(c) fallback 提取到除 decision 外的字段、(d) 原文本中有清晰决策散文。这是一个组合条件，概率很低。

**建议**：实施时可让 `_extract_task_result_fallback` 在 `decision is None` 时也返回 None（或标记 allow_recovery=True），使 recovery 有机会介入。或在 Step 2 测试中覆盖此边界场景。

---

### 3.3 🟡 Repair 内 protocol_origin 覆盖的精确位置

**问题**：计划 Step 7 说"repair 成功置 `protocol_origin="repair"`；若 repair 输出仍靠恢复兜回，覆盖 origin 为 repair"。但未指明覆盖代码插入 `_repair_task_result` 的哪个位置。

**当前代码流程**：
```
_call_agent_direct → adapter → _parse_task_result_text → (可能 recovery, origin="parser")
→ 返回 repaired_result
→ _validate_task_result(repaired_result) → vr2
→ if vr2.valid: return repaired_result, True
```

**建议插入点**：在 `_call_agent_direct` 返回后、`_validate_task_result` 调用前：
```python
repaired_result = self._call_agent_direct(repair_input, state_name)
# Repair 内恢复统一记 repair
exec_meta = repaired_result.get_execution()
if exec_meta.protocol_origin in ("parser", "native"):
    exec_meta.protocol_origin = "repair"
```

**建议**：实施方案时在 Step 7 伪代码中明确此位置。

---

### 3.4 🟡 40 字符引导词窗口的边界场景

**问题**：引导词后约 40 字符窗口对中英混杂文本敏感。例如：

> "经过上述详细审查，我的最终决定是：建议修改后重新提交，具体包括补充安全分析和错误处理逻辑。决策为 **revise**。"

此例中 "revise" 不在 "最终决定" 的 40 字符窗口内，但在另一引导词 "决策为" 的窗口内 → 可恢复。但若原文只有 "最终决定" 而无 "决策为"，且散文过长，则可能遗漏。

**实际风险**：低。L1 只匹配英文 decision 词（"revise"、"approve" 等），这些词在中文散文中几乎只出现在决策声明处，40 字符足够覆盖引导词到 decision 的距离。且多重引导词表（10 个中英文引导词）增加了命中概率。

**建议**：测试中覆盖 "引导词 + 30+ 字符散文 + 英文 decision" 的长间隔场景，确认 40 字符窗口的实际覆盖率。

---

### 3.5 🟢 递归 `_parse_task_result_text` 调用不传 allowed_decisions

**问题**：`_parse.py:28` 递归调用 `_parse_task_result_text(data["result"])` 不传 `allowed_decisions`。但 "result" 字段期望的是嵌套 JSON/TaskResult 字符串，不是散文，因此不触发 recovery 是正确的。

**确认**：无需改动。`data["result"]` 本身是 JSON 结构中已解析出的字符串，设计上应包含完整 TaskResult JSON，不适用散文恢复。实施时在代码注释中标注此意图即可。

---

### 3.6 🟢 计划 §3 文件表中 `ValidResult +recovery` 与步骤不一致

**问题**：§3 涉及文件表列出 `validators/validation_result.py` → `ValidResult +recovery 字段`，但 7 个实现步骤中没有一步修改 ValidResult。恢复信息已承载在 `ExecutionMetadata.recovery` 上，Runner 从 `task_result.get_execution()` 读取即可，ValidResult 无需冗余字段。

**建议**：从 §3 表中移除 `validators/validation_result.py` 行，或在 Step 6 中明确"ValidResult 不新增 recovery 字段——Runner 从 ExecutionMetadata 读取"。

---

## 4. 逐步骤技术评价

| 步骤 | 计划内容 | 评价 |
|------|---------|------|
| **Step 1** | RecoveryInfo + ExecutionMetadata 协议轴 | ✅ 手写 to_dict/from_dict 避开 asdict 嵌套陷阱正确。Round-trip 测试覆盖充分。需注意：`TaskResult.from_dict` 中 `ExecutionMetadata(**exec_data)` 对老数据（无 protocol_origin）因字段有默认值而兼容，不会 TypeError。但若未来有人向 exec dict 塞了多余键，需 from_dict 兜底——建议预防性添加 `ExecutionMetadata.from_dict()`。 |
| **Step 2** | `_recover_decision_from_prose` 纯函数 | ✅ 引导词表（10 个中英文）+ 完整 token 边界（`\b`）+ 唯一性裁决，设计保守且可独立测试。`origin_text_hash = sha256(text)[:16]` 对于同 run 内匹配足够。注意：Level 2 的同义词表需支持中文引导词窗口内匹配——正则需处理 UTF-8。 |
| **Step 3** | `_parse_task_result_text` 接入恢复 | ✅ 参数默认值保证零污染。恢复在"所有解析失败且 allowed_decisions 非空"时才触发，符合设计稿"保守不伪造"。见 §3.2 关于 fallback 交互的观测项。 |
| **Step 4** | adapter 透传 allowed_decisions | ✅ 从 `agent_input.skill_policy` 取值路径正确（与 runner.py:1185 构建的 `skill_policy` 一致）。空值防御（agent_input=None、skill_policy={}）已提及。注意：claude_cli 2 处调用点、codex_cli 3 处调用点均需透传。 |
| **Step 5** | events.py 新增 ProtocolRecovery | ✅ 字段列表完整（含 origin_text_hash）。registry 校验覆盖。 |
| **Step 6** | runner 放行 + 落盘 + 发事件 | ✅ 恢复结果 status=success/decision∈allowed → 天然走 valid 分支放行，无需特判路由，设计干净。protocol_origin 随 to_dict() 落入 workflow_state.json。见 §3.1 关于 execution 元数据填充的观测项。 |
| **Step 7** | Repair 瘦身为格式转换器 | ✅ 经 `task.output`+Resolver 取产物（与 backfill 命名一致 `staging/<state>/<output>.md`），禁硬编码文件名。IO try/except 退化不崩。见 §3.3 关于 origin 覆盖位置的观测项。 |

---

## 5. 测试策略评价

### 5.1 新增测试覆盖矩阵

| 维度 | 测试文件 | 覆盖场景 | 评价 |
|------|---------|---------|------|
| 恢复算法 | `test_protocol_recovery.py`（新） | L1 唯一/窗口外/冲突/无引导词；L2 关闭 vs 开启；空 allowed；hash 稳定 | ✅ 全分支覆盖 |
| 解析接入 | `test_parser_fallback.py`（扩展） | 不传 allowed 零污染；传参恢复 success/parser；native 优先 | ✅ |
| 数据模型 | `test_task_result_v4.py`（扩展） | ExecutionMetadata 缺省兼容；RecoveryInfo round-trip | ✅ |
| 事件 | `test_event_bus.py`（扩展） | ProtocolRecovery registry 校验 | ✅ |
| Repair | `test_repair.py`（扩展） | 格式转换 prompt 内容；IO 退化不崩；origin=repair | ✅ |
| adapter 透传 | adapter 单元测试 | 含/不含 skill_policy 的 AgentInput | ✅ |

### 5.2 建议补充的测试（实施时酌情添加）

| # | 测试场景 | 优先级 |
|---|---------|--------|
| 1 | Recovery 后 execution 时间字段非空 | 🟡 中 |
| 2 | `_extract_task_result_fallback` 返回 decision=None 时 recovery 介入 | 🟢 低 |
| 3 | `agent_input=None` 时 adapter 不崩溃（防御） | 🟡 中 |
| 4 | repair 后 origin=repair（含 parser recovery 兜底子场景） | 🟡 中 |
| 5 | 引导词 + 长间隔（30+ 字符）decision 匹配 | 🟢 低 |

---

## 6. 风险矩阵

| 风险 | 等级 | 缓解 | 计划是否覆盖 |
|------|------|------|-------------|
| runner 核心编排改动回归 | 🟡 中 | 恢复走 valid 分支 + integration 回归 | ✅ §6 风险表 |
| asdict 嵌套序列化错误 | 🟡 中 | 手写 to_dict/from_dict + round-trip 测试 | ✅ §6 风险表 |
| 恢复误伤正文 | 🟢 低 | 三重约束（引导词窗口+完整 token+唯一性裁决） | ✅ §6 风险表 |
| Repair 读产物 worktree 路径分离 | 🟡 中 | 复用 backfill 命名 + IO try/except | ✅ §6 风险表 |
| ExecutionMetadata.from_dict 破坏旧调用 | 🟢 低 | 保留兼容分支 | ✅ §6 风险表 |
| skill_policy 未携带 allowed_decisions | 🟢 低 | 空值防御 | ✅ §6 风险表 |
| recovery TaskResult 的 execution timing 缺失 | 🟡 中 | adapter 回填时间字段 | ⚠️ 见 §3.1 |
| fallback 与 recovery 窄窗口冲突 | 🟢 低 | 组合条件极罕见 | ⚠️ 见 §3.2 |

---

## 7. 术语一致性确认

计划 §1.3 明确处理了设计稿与 goal 的术语差异：

| 维度 | 设计稿 | Goal | 计划采用 | 审核 |
|------|--------|------|---------|------|
| 字段名 | `protocol_state` | `protocol_origin` | `protocol_origin` | ✅ goal 优先，合理 |
| 取值 | native/recovered/repaired/human | native/parser/repair/human | native/parser/repair/human | ✅ goal 优先 |
| 位置 | TaskResult 顶层 | ExecutionMetadata | ExecutionMetadata | ✅ 与 session_id/token_usage 等运行时元数据聚拢 |
| level 1 method | regex（RecoveryInfo.method） | regex | regex | ✅ 一致 |
| level 2 method | synonym | synonym | synonym | ✅ 一致 |

术语选择内聚一致，不影响任何下游模块（status/explain/history 均通过 `get_execution()` 访问）。

---

## 8. 不可改约束确认

以下约束计划已正确标识为不可改（或通过默认参数保证兼容）：

- ❌ 线性节点解析行为 — 通过 `allowed_decisions=None` 默认值保证零差异
- ❌ native JSON 解析路径 — 恢复只在所有解析失败后触发
- ❌ `_parse_task_result_text` 旧调用点 — 新参数默认值保证无需修改
- ❌ 历史 TaskResult 反序列化 — protocol_origin 缺省 native、recovery 缺省 None
- ❌ WorkflowConfig YAML schema — 首版不改配置层
- ❌ `resolve_transition` 路由逻辑 — 不涉及

---

## 9. 执行建议

1. **Step 1 先行**：数据模型是所有后续步骤的依赖，先完成并验证 round-trip。
2. **Step 2 独立测试**：`_recover_decision_from_prose` 是纯函数，可完全独立于其他模块测试。
3. **Step 5 可并行**：events.py 改动独立于其他步骤，可与 Step 1-2 并行。
4. **Step 4 + Step 6 耦合**：adapter 透传和 runner 放行需要联调，建议连续实施。
5. **Step 7 最后**：Repair 瘦身依赖 Step 1-6 完成后的稳定基础。
6. **回归节奏**：每完成 2 个步骤跑一次全量 `pytest -q`，不等最后。

---

## 附录 A：审核检查清单

- [x] 需求覆盖完整性（goal 6 条 + 验收 8 条）
- [x] 设计稿 §6 落点清单 6 个文件改动是否全部覆盖
- [x] 向后兼容验证路径是否充分
- [x] 模块边界是否清晰（恢复算法/事件/编排/模型各在其位）
- [x] 是否有超范围或遗漏
- [x] 测试是否覆盖全分支（含负面用例）
- [x] 停止规则是否合理
- [x] 上一轮审核意见核对 — **不适用**（本项目无上一轮 plan_review/plan_refinement，docs/runs 下文件属于其他 workflow 运行）
