# 协议恢复能力迭代 — 代码审查报告（第 1 轮）

> 审查日期：2026-07-14
> 审查对象：`feat/protocol-recovery` 工作区未提交改动（8 文件修改 + 1 新文件）
> 依据：`docs/protocol-recovery-design.md`、`staging/planning/plan_doc.md`、`staging/execution/execution_report.md`
> 决策：**revise**（存在可在流程内修复的 Blocking）

## 0. diff artifact 缺失说明与残余风险

本 run 未产出 `diff` 产物流。审查改为**直接对工作区执行 `git diff` 逐文件通读**（result.py / _parse.py / claude_cli.py / codex_cli.py / events.py / runner.py 全量 diff + 新增 test_protocol_recovery.py 全文 + validator/context 关联代码）。覆盖等价于逐文件审查。

残余风险（因无 diff artifact 快照）：
- 无法核对"执行报告声称的行数变化"与实际 diff 是否逐行一致（已按语义审查，不影响结论）。
- `test_protocol_recovery.py` 为未跟踪新文件，`test_task_result_v4.py`/`test_event_bus.py` 的具体新增用例已在 diff 中确认；但**未实际执行 pytest**（本审查节点无执行环境，执行报告的测试通过声明属 agent 自述，见 Issue-1 的 Verification 状态）。

## 1. 总体评价

数据模型层（`result.py` 的 `RecoveryInfo` + `ExecutionMetadata` 协议轴 + 手写 to_dict/from_dict）与恢复纯函数层（`_parse.py` 的 `_recover_decision_from_prose`）实现质量高、向后兼容处理到位、保守不猜的三重约束（引导词窗口 + 完整 token + 唯一性裁决）落实正确，L2 默认关闭符合验收。

但**编排落地层存在一个致命的元数据丢失缺陷**：两个真实 adapter（Claude/Codex）在 `execute()` 中解析后整体重建 `ExecutionMetadata`，未保留 `_parse` 恢复时写入的 `protocol_origin` 与 `recovery`。结果是 —— 恢复的 **decision 能放行**（因重建恰好补上时间戳过了 validator），但**"留痕、可度量、可归因"这一设计核心目标端到端失效**：ProtocolRecovery 事件永不发射、workflow_state 的 `protocol_origin` 永远是 `native`。这直接使设计稿 §1.3 目标 2、验收点 5、以及 §9 端到端验收落空，且被"只测纯函数层"的单测缺口所掩盖。

---

## 2. Issue 清单

### Issue-1 [Blocking] adapter.execute() 整体重建 execution，抹掉 protocol_origin 与 recovery

- **问题**：`agents/claude_cli.py:149-156` 与 `agents/codex_cli.py:162-169` 在 `_parse_stream_output` 返回后，用一个全新 `ExecutionMetadata(started_at=..., finished_at=..., exit_code=..., pid=...)` **整体覆盖** `task_result.execution`。该新对象未传入 `protocol_origin`/`recovery`，二者回落默认值 `"native"`/`None`。因此 `_parse_task_result_text` 在恢复命中时精心写入的 `protocol_origin="parser"` 和 `recovery=RecoveryInfo(...)` 在 `execute()` 返回前即被清除。
- **连锁后果**：
  1. `runner._emit_protocol_recovery_if_needed` 读到 `exec_meta.recovery is None` → 直接 return → **ProtocalRecovery 事件永不发射**（验收点 5 端到端失效）。
  2. `record_task_result` 落盘的 `execution.protocol_origin` 恒为 `native` → **workflow_state 无法归因恢复**（设计稿 §9 端到端验收失效、§4 recovery_rate 统计数据源为空）。
  3. repair 成功路径虽在 `runner.py` 手动补 `protocol_origin="repair"`（在 execute 之后），origin 勉强成立；但"repair 输出本身靠散文恢复兜底"子场景的 `recovery` 同样被抹，`_emit_protocol_recovery_if_needed` 仍不发事件。
- **为何被测试漏过**：`test_protocol_recovery.py` 只对 `_parse_task_result_text` 纯函数断言 `protocol_origin=="parser"`，从未对 `adapter.execute()` 返回值做端到端断言。执行报告 §4 验收点 5「✅ test_registry_required_fields」只证明 registry 定义了字段，不证明事件被真实发射——属**验证手段与验收目标错配**。
- **Severity**：Blocking
- **Acceptance（必填）**：
  1. 恢复命中时，`ClaudeCLI.execute()` / `CodexCLI.execute()` 返回的 `TaskResult.get_execution().protocol_origin == "parser"` 且 `.recovery is not None`（保留 `method`/`confidence`/`recovered_fields`/`origin_text_hash`）。即重建 execution 时必须保留 `_parse` 设置的协议轴字段（例如：先从 `task_result.get_execution()` 读出 `protocol_origin`/`recovery` 再并入新对象，或改为只逐字段更新 timing/pid 而非整体替换）。
  2. 恢复结果经 Runner 主循环后，run 的 `events.jsonl` 含一条 `ProtocolRecovery` 记录（字段含 `origin_text_hash`），且 `workflow_state.json` 中该 state 的 `execution.protocol_origin == "parser"`。
- **Verification**：
  - 新增端到端单测：构造带 `skill_policy={"allowed_decisions":[...]}` 的 `AgentInput` + 仅含散文决策（无 json 块）的 stub stdout，调 `execute()`（或 `_parse_stream_output` 后模拟 execute 的 execution 重建路径），断言返回 `execution.protocol_origin=="parser"` 且 `recovery.method=="regex"`。
  - `$env:PYTHONPATH='src;.'; pytest tests/unit/test_protocol_recovery.py -q` 全过（含上述新用例）。
  - 状态：当前 **FAIL**（静态判定：现代码 execute 路径必然覆盖为 native，无用例覆盖此路径）。

### Issue-2 [Warning] 英文引导词定位大小写敏感，与注释"大小写不敏感"不符

- **问题**：`agents/_parse.py` 的 `_match_decisions_in_windows` 内以 `source_text.find(gw, pos)` 定位引导词，`str.find` **大小写敏感**；而该处注释写「在 source_text 中找引导词位置（大小写不敏感用于英文引导词）」。英文引导词（`decision`/`verdict`/`conclusion`/`final decision`）若在文本中首字母大写（如 `Decision: revise`、`DECISION`）将定位失败、不进入窗口，导致恢复漏命中。decision **token** 匹配用了 `re.IGNORECASE` 是对的，但**引导词定位本身**未做大小写归一。中文引导词不受影响。
- **测试误导**：`test_protocol_recovery.py::test_l1_case_insensitive` 用例名声称测"大小写不敏感"，但其文本 `"Final decision: APPROVE"` 中引导词 `decision` 本就是小写，实际只覆盖了 token 大小写，未覆盖引导词大小写。
- **Severity**：Warning（native 中文审查场景是主路径，不受影响；英文/混合场景漏恢复，退化为 invalid_output→repair，不产生错误路由）
- **Acceptance（必填）**：二选一并使注释、实现、测试三者一致：
  - (A) 修实现：ASCII 引导词定位改为大小写不敏感（如对 ASCII 引导词 `re.finditer(re.escape(gw), source_text, re.IGNORECASE)` 或 `source_text.lower().find(gw.lower())`），并补一个大写引导词恢复成功的用例；或
  - (B) 修文档：删除注释中"大小写不敏感用于英文引导词"表述、并将 `test_l1_case_insensitive` 更名为准确反映"token 大小写"的名字，明确英文引导词须小写。
- **Verification**：方案 A → 新增用例 `text="Final DECISION: approve"` 断言恢复成功；方案 B → `grep` 注释无"大小写不敏感"误导表述且用例名已修正（Manual Review）。

### Issue-3 [Warning] Repair 瘦身与 adapter 透传缺针对性单元测试（计划 §5 明确要求）

- **问题**：计划 `plan_doc.md` §5 明确要求「`test_repair.py`（扩展）：格式转换 prompt 内容、IO 退化不崩、repair 成功 origin=repair」及「adapter 透传：含/不含 skill_policy 的 AgentInput + 散文 stdout 断言」。但 `git diff --stat` 显示 `test_repair.py` 与 `test_parser_fallback.py` **本次零改动**。`runner.py` 的 Repair 瘦身是本次 +120 行核心改动（`_build_repair_agent_input` 读产物正文/packet、IO 退化、repair origin），却无任何针对性新单测。执行报告 §3 步骤 7「test_repair.py（26 用例）全部通过」仅为**既有用例回归**，未验证新行为。
- **Severity**：Warning（新行为未被测试覆盖，回归风险高；与计划承诺不符）
- **Acceptance（必填）**：新增单测覆盖以下每一项：
  1. `original_agent_input.task.output` 有值且对应 staging 产物存在时，生成的 repair prompt 含产物正文片段，且**不含**旧措辞"只允许修改 status 和 decision"（走格式转换分支）。
  2. `task.output` 为空 / staging 文件缺失 / packet 缺失时，退化为精简 prompt 且**不抛异常**（IO try/except 生效）。
  3. repair 成功后 `repaired_result.get_execution().protocol_origin == "repair"`。
  4. adapter 从 `skill_policy` 透传 `allowed_decisions`/`enable_synonym_recovery`，且 `agent_input=None`（或无 skill_policy）时行为等价现状（不恢复、不抛异常）。
- **Verification**：`$env:PYTHONPATH='src;.'; pytest tests/unit/test_repair.py tests/unit/test_parser_fallback.py -q` 新增用例通过。当前状态 **NOT_EXECUTED**（新用例尚未编写）。

### Issue-4 [Suggestion] L1 candidate 对含下划线的 decision 词退化为子串匹配

- **问题**：`_parse.py` 的 `_match_decisions_in_windows` 用 `if token.isascii() and token.isalpha()` 判定走 word-boundary regex 还是中文子串匹配。若某节点 `allowed_decisions` 含带下划线的值（如 `no_op`），`"no_op".isalpha()` 为 `False` → 落入中文子串匹配分支，**失去前后非字母边界保护**，可能被 `no_operation` 等更长词误伤。当前多数 workflow 的 allowed_decisions 为纯字母，属潜在边界隐患。
- **Severity**：Suggestion
- **Acceptance（必填）**：对含下划线/数字的 ASCII decision 词也走 word-boundary regex 分支（判定条件改为"含 ASCII 字母即走 regex 分支"，中文短语走子串），或在 `_recover_decision_from_prose` docstring 明确约束"allowed_decisions 仅支持纯字母 token"。
- **Verification**：补 `no_op` 场景用例，或 Manual Review 确认约束文档化。

### Issue-5 [Suggestion] Repair 读 packet 的 marker 为硬编码字符串，与写入格式隐式耦合

- **问题**：`runner.py` `_build_repair_agent_input` 用 `marker = "## 最后一条 assistant message"` 截取 packet 内容，该字符串须与 `claude_cli._write_packet_content` 实际写入的标题严格一致。若 packet 写入格式变动，此处静默失配（marker 不命中 → 不截断，退回读全文再按 4000 截断），不崩但语义漂移。
- **Severity**：Suggestion
- **Acceptance（必填）**：marker 与 packet 写入方共用同一常量（模块级常量引用），或在两处互加注释说明"格式须同步"。
- **Verification**：Manual Review。

---

## 3. 验收标准逐条复核（对照 plan_doc §1.2 / 设计稿 §9）

| # | 验收点 | 结论 | 说明 |
|---|--------|------|------|
| 1 | L1 唯一命中恢复；冲突/无命中不恢复 | ✅ | 纯函数层实现与用例充分（test_l1_*） |
| 2 | 线性节点不传 allowed → 零污染 | ✅ | test_no_allowed_no_recovery |
| 3 | 合法 JSON 时 native 优先 | ✅ | test_valid_json_priority_over_recovery |
| 4 | Repair 瘦身：产物正文+最后消息+退化不崩、禁硬编码文件名 | ⚠️ 部分 | 实现经 `task.output`+staging_paths 取产物（未硬编码文件名，符合），但**无针对性单测**（Issue-3） |
| 5 | ProtocolRecovery 事件字段完整（含 origin_text_hash） | ❌ | registry 定义完整，但**端到端永不发射**（Issue-1）；执行报告的 ✅ 系验证错配 |
| 6 | 老 TaskResult → protocol_origin=native、recovery=None | ✅ | from_dict 缺省兼容，test_old_taskresult_no_protocol_fields |
| 7 | L2 默认关闭 | ✅ | test_l2_disabled_by_default |
| 8 | 全量 pytest 无回归 | ❓ NOT_EXECUTED | 本节点未执行；且 Issue-1 端到端路径无用例，"无回归"不等于"新功能生效" |

**关键差距**：验收点 5 与设计稿 §9 端到端要求（"recovered 时 events.jsonl 有 ProtocolRecovery 记录、workflow_state 有 protocol_origin"）因 Issue-1 未达成；执行报告 §4 将其标为 ✅ 属高估。

---

## 4. 决策与依据

**decision = revise**

- 存在 1 个 Blocking（Issue-1）：可修复、无环境限制、可在 `output_refinement` 阶段闭环——修 adapter 保留协议轴字段 + 补端到端单测即可。不构成 NEED-HUMAN，故非 reject。
- 数据模型层与恢复纯函数层实现正确，非"变更不可接受"，故非 reject。
- 因 Blocking 使设计核心目标（留痕/归因）失效，故不能 approve。

回到 `output_refinement`：优先修复 Issue-1（Blocking），并按 Issue-3 补齐 Repair/透传/端到端单测；Issue-2/4/5 建议一并处理（其中 Issue-2 需使注释、实现、测试三者一致）。
