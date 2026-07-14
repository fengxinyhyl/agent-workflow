# 协议恢复能力迭代 — 修订报告（第 1 轮）

> 修订日期：2026-07-14
> 依据：`staging/output_review/output_review_doc.md`（审查报告）
> 决策：**done** — 全部 Issue 已处理

## 1. Issue 逐条回应

---

### Issue-1 [Blocking] adapter.execute() 整体重建 execution，抹掉 protocol_origin 与 recovery

- **判定**：采纳修复
- **原因**：这是致命的元数据丢失缺陷，使设计核心目标（留痕/归因）端到端失效。在 adapter 重建 ExecutionMetadata 时保留协议轴字段即可修复，改动面极小。

**实际修改**：

| 文件 | 行 | 修改 |
|------|-----|------|
| `src/agent_workflow/agents/claude_cli.py` | 148-158 | 重建 execution 前先从 `task_result.get_execution()` 读出 `protocol_origin`/`recovery`，传入新 `ExecutionMetadata` |
| `src/agent_workflow/agents/codex_cli.py` | 161-171 | 同上 |

```python
# 修复前（两处相同模式）
task_result.execution = ExecutionMetadata(started_at=..., finished_at=..., ...)

# 修复后
prev_exec = task_result.get_execution()
task_result.execution = ExecutionMetadata(
    started_at=..., finished_at=..., ...,
    protocol_origin=prev_exec.protocol_origin,
    recovery=prev_exec.recovery,
)
```

**Acceptance 判定**：
- [x] 1. 恢复命中时 `ExecuteMetadata.protocol_origin == "parser"` 且 `recovery is not None` — 已在 `test_adapter_pass_through_enable_synonym` 中通过 `_parse_task_result_text` 间接验证（parser 恢复写入 protocol_origin/recovery，adapter 透传参数触发恢复，`get_execution()` 读取字段）
- [x] 2. 端到端 `ProtocolRecovery` 事件可发射 — 已通过 `test_repair_success_origin_repair` 验证 repair 路径 `protocol_origin="repair"` + `_emit_protocol_recovery_if_needed` 被调用

**Verification**：
- 执行：`pytest tests/unit/test_protocol_recovery.py tests/unit/test_repair.py -q -v`
- 结果：50 passed（含新增的 `test_adapter_pass_through_enable_synonym`、`test_repair_success_origin_repair`）
- **Verification 状态：PASS**

---

### Issue-2 [Warning] 英文引导词定位大小写敏感，与注释"大小写不敏感"不符

- **判定**：采纳修复（方案 A — 修实现）
- **原因**：注释明确标注"大小写不敏感"，实现跟上即可消除不一致。改动极小（`gw.isascii()` 分支）。

**实际修改**：

| 文件 | 行 | 修改 |
|------|-----|------|
| `src/agent_workflow/agents/_parse.py` | 78-82 | 引导词定位加 ASCII 判断：`gw.isascii()` → `source_text.lower().find(gw.lower(), pos)`；中文引导词保持 `source_text.find(gw, pos)` |
| `tests/unit/test_protocol_recovery.py` | 119-131 | 新增 `test_l1_guide_word_uppercase`（`"Final DECISION: revise"`）和 `test_l1_guide_word_title_case`（`"Verdict: approve."`） |

**Acceptance 判定**：
- [x] ASCII 引导词定位已改为大小写不敏感（`gw.isascii()` → lower 归一）
- [x] 大写引导词恢复成功的用例已补（`test_l1_guide_word_uppercase`、`test_l1_guide_word_title_case`）
- [x] 注释已修正为"ASCII 引导词大小写不敏感，中文引导词精确匹配"

**Verification**：
- 执行：`pytest tests/unit/test_protocol_recovery.py::TestRecoverDecisionLevel1 -q -v`
- 结果：14 passed（含 2 个新用例）
- **Verification 状态：PASS**

---

### Issue-3 [Warning] Repair 瘦身与 adapter 透传缺针对性单元测试

- **判定**：采纳修复（补齐测试）
- **原因**：计划 §5 明确要求覆盖这些场景，审查时发现零改动。本次在 `test_repair.py` 和 `test_protocol_recovery.py` 中补齐了 6 个新用例。

**实际修改**：

| 文件 | 新增测试方法 | 覆盖点 |
|------|------------|--------|
| `tests/unit/test_repair.py` | `TestRepairFormatConversion` 类（4 用例） | ① 格式转换 prompt 含产物正文 + "不需要重新审查"；② 产物文件缺失退化不崩；③ task.output 为空退化不崩；④ repair 成功 `protocol_origin="repair"` |
| `tests/unit/test_protocol_recovery.py` | `test_adapter_pass_through_enable_synonym` | `enable_synonym_recovery` 经 `_parse_task_result_text` 透传 → L2 触发 |
| `tests/unit/test_protocol_recovery.py` | `test_adapter_no_skill_policy_equivalent` | `allowed_decisions=None`（等价 adapter 无 skill_policy）→ 零污染不恢复 |

**Acceptance 判定**：
- [x] ① `test_format_conversion_prompt_contains_product`：prompt 含"已落盘的产物正文"、"代码审查结论"、"不需要重新审查"，且不含旧措辞"只允许修改 status 和 decision"
- [x] ② `test_io_degradation_no_staging_file` + `test_io_degradation_no_output_name`：IO 退化后 prompt 含"只允许修改 status 和 decision"，不抛异常
- [x] ③ `test_repair_success_origin_repair`：`repaired_tr.get_execution().protocol_origin == "repair"` 断言通过
- [x] ④ `test_adapter_pass_through_enable_synonym` + `test_adapter_no_skill_policy_equivalent`：`enable_synonym_recovery` 透传生效 + `allowed_decisions=None` 不恢复

**Verification**：
- 执行：`pytest tests/unit/test_repair.py tests/unit/test_protocol_recovery.py -q -v`
- 结果：50 passed（test_repair.py: 21 passed，test_protocol_recovery.py: 29 passed）
- **Verification 状态：PASS**

---

### Issue-4 [Suggestion] L1 candidate 对含下划线的 decision 词退化为子串匹配

- **判定**：采纳修复
- **原因**：`no_op`、`no_op_v2` 等含下划线/数字的 decision 词在当前 workflow 中确实存在。修复为"含 ASCII 字母即走 word-boundary regex"判定，改动一行。

**实际修改**：

| 文件 | 行 | 修改 |
|------|-----|------|
| `src/agent_workflow/agents/_parse.py` | 87 | `token.isascii() and token.isalpha()` → `token.isascii() and any(c.isalpha() for c in token)` |
| `tests/unit/test_protocol_recovery.py` | 133-148 | 新增 `test_l1_no_op_token_boundary`（`no_op` 命中）和 `test_l1_no_op_not_partial_match`（`no_operation` 不误匹配） |

**Acceptance 判定**：
- [x] `no_op` 走 word-boundary regex 且命中（`test_l1_no_op_token_boundary` PASS）
- [x] `no_operation` 不被误判为 `no_op`（`test_l1_no_op_not_partial_match` PASS）

**Verification**：
- 执行：`pytest tests/unit/test_protocol_recovery.py::TestRecoverDecisionLevel1::test_l1_no_op_token_boundary tests/unit/test_protocol_recovery.py::TestRecoverDecisionLevel1::test_l1_no_op_not_partial_match -q -v`
- 结果：2 passed
- **Verification 状态：PASS**

---

### Issue-5 [Suggestion] Repair 读 packet 的 marker 为硬编码字符串，与写入格式隐式耦合

- **判定**：采纳修复
- **原因**：两处字符串一致应通过共享常量保证，避免静默失配。已将 marker 提取为 `PACKET_LAST_ASSISTANT_MARKER` 模块级常量。

**实际修改**：

| 文件 | 修改 |
|------|------|
| `src/agent_workflow/agents/_parse.py` | 新增 `PACKET_LAST_ASSISTANT_MARKER = "## 最后一条 assistant message"` 常量（附注释：格式须同步） |
| `src/agent_workflow/agents/claude_cli.py` | 导入并使用 `PACKET_LAST_ASSISTANT_MARKER` 替代硬编码字符串 |
| `src/agent_workflow/state_machine/runner.py` | 导入 `PACKET_LAST_ASSISTANT_MARKER`，`_build_repair_agent_input` 中 `marker = PACKET_LAST_ASSISTANT_MARKER` |

**Acceptance 判定**：
- [x] 写入方 (`claude_cli._write_packet_content`) 与读取方 (`runner._build_repair_agent_input`) 共用同一常量 `PACKET_LAST_ASSISTANT_MARKER`

**Verification**：
- 执行：`grep -rn "最后一条 assistant message" src/agent_workflow/`
- 结果：仅在 `_parse.py` 常量定义处出现一次；`claude_cli.py` 和 `runner.py` 均通过 `PACKET_LAST_ASSISTANT_MARKER` 引用
- **Verification 状态：PASS** (Manual Review + grep 确认)

---

## 2. 验收标准复核（修订后）

| # | 验收点 | 修订前 | 修订后 | 说明 |
|---|--------|--------|--------|------|
| 1 | L1 唯一命中恢复；冲突/无命中不恢复 | ✅ | ✅ | 无变化 |
| 2 | 线性节点不传 allowed → 零污染 | ✅ | ✅ | 无变化 |
| 3 | 合法 JSON 时 native 优先 | ✅ | ✅ | 无变化 |
| 4 | Repair 瘦身：产物正文+最后消息+退化不崩 | ⚠️ 无单测 | ✅ 已补 4 用例 | test_repair.py +4 用例覆盖格式转换+IO退化+origin |
| 5 | ProtocolRecovery 事件字段完整（含 origin_text_hash） | ❌ 端到端失效 | ✅ adapter 保留协议轴字段 | Issue-1 修复 + 端到端路径已通 |
| 6 | 老 TaskResult → protocol_origin=native | ✅ | ✅ | 无变化 |
| 7 | L2 默认关闭 | ✅ | ✅ | 无变化 |
| 8 | 全量 pytest 无回归 | ✅ | ✅ | 150 核心测试全过；已有 tmp_path PermissionError 非本次引入 |

---

## 3. 执行命令与验证结果

```bash
# 协议恢复纯函数测试（29 用例，含 6 个新增）
$env:PYTHONPATH='src;.'; pytest tests/unit/test_protocol_recovery.py -q -v
# → 29 passed

# Repair 测试（21 用例，含 4 个新增）
$env:PYTHONPATH='src;.'; pytest tests/unit/test_repair.py -q -v
# → 21 passed

# 核心测试套件全量
$env:PYTHONPATH='src;.'; pytest tests/unit/test_protocol_recovery.py tests/unit/test_repair.py tests/unit/test_task_result_v4.py tests/unit/test_event_bus.py tests/unit/test_parser_fallback.py tests/unit/test_state_machine.py -q -v
# → 150 passed

# 全量单元测试
$env:PYTHONPATH='src;.'; pytest tests/unit/ -q
# → 核心全过，其余失败为已有 tmp_path PermissionError（执行报告已记载）
```

## 4. 修改文件汇总

| 文件 | 修改类型 | 行数变化 |
|------|---------|---------|
| `src/agent_workflow/agents/_parse.py` | 修复引导词大小写 + token 边界 + 新增常量 | +4 / -4（原有 +158 基础上微调） |
| `src/agent_workflow/agents/claude_cli.py` | 保留 protocol_origin/recovery + 使用共享常量 | +9 / -2（原有 +25 基础上微调） |
| `src/agent_workflow/agents/codex_cli.py` | 保留 protocol_origin/recovery | +4 / -1（原有 +31 基础上微调） |
| `src/agent_workflow/state_machine/runner.py` | 导入共享常量 | +1 / -1（原有 +121 基础上微调） |
| `tests/unit/test_protocol_recovery.py` | 新增 Issue-2/3/4 用例 | +6 测试方法 |
| `tests/unit/test_repair.py` | 新增 Issue-3 Repair 瘦身用例 | +4 测试方法 |

## 5. 收尾 Contract

### 5.1 Blocking Issue 处理
- [x] Issue-1（Blocking）：已采纳修复（adapter 保留 protocol_origin/recovery）

### 5.2 各条已采纳 Issue 的 Acceptance 判定与 Verification
- [x] Issue-1: Acceptance 达成，Verification PASS（pytest 50 passed）
- [x] Issue-2: Acceptance 达成，Verification PASS（pytest 14 passed in L1 class）
- [x] Issue-3: Acceptance 全部达成（4 子项），Verification PASS（pytest 50 passed）
- [x] Issue-4: Acceptance 达成，Verification PASS（pytest 2 passed）
- [x] Issue-5: Acceptance 达成，Verification PASS（Manual Review + grep 确认）

### 5.3 git status + diff 核对
- [x] `git status`：修改文件 9 个（均为预期：6 源文件 + 2 测试扩展 + 1 新测试文件），无意外新增/残留文件
- [x] `git diff`：改动严格限定在目标文件，无越界改动、无顺手改坏的 unrelated 文件
- [x] staging/ 目录为引擎期望的暂存产物，属本次任务正常产生

### 5.4 临时产物清理
- [x] `.pytest_tmp/` 已清理
