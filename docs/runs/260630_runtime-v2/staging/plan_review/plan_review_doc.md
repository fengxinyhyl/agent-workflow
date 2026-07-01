# 计划评审：Runtime v2 第 1 步 — 契约收敛 + Parser 兜底

## 评审结论

**approve** — 计划可执行，可进入 execution。

计划质量高：行号引用与源码现状逐一吻合，范围纪律清晰（严守不碰 machine/runner/loader/config 的边界），风险章节与停止规则务实。下列改进点均为非阻塞性实现细节建议，可在既有分步验证中自然消化，不必单独回流修订。

## 1. 核对源码与行号准确性（已逐项验证）

| 计划声称 | 源码核实 | 结论 |
|---|---|---|
| `result.py:40` 有 `VALID_DECISIONS` | 确认 line 40 | ✓ |
| `result.py:138-139` decision 校验 | 确认 | ✓ |
| decision 默认 `"done"`@113、`from_dict`@251、`get_decision`@155-157、`to_dict`@193 | 全部确认 | ✓ |
| `validator:18` import、`:54-56` warning、`:59-62` blocking | 全部确认 | ✓ |
| schema `required` 含 `"decision"`@22；`build_task_result_schema`@164-181 | 确认 | ✓ |
| mock.py 不引用 `VALID_DECISIONS` | grep 确认无引用，裁定 #1 成立 | ✓ |
| Parser 兜底点（claude 264-265 success/done、codex 325-326、codex `_parse_output_fallback`@357） | 确认 | ✓ |
| `_extract_task_result_fallback` 用 `if decision:` 决定返回 | 确认在 line 379 | ✓ |

裁定 #1~#4 全部经源码验证成立。计划对"哪些是连带清理、哪些是后续步骤"的边界划分准确。

## 2. 需求覆盖

goal 四项改造点与验收标准全部覆盖，无遗漏、无超范围：

- 改造点 1（result.py 契约收敛）→ Step 1，逐字段覆盖。
- 改造点 2（result_schema.py）→ Step 2。
- 改造点 3（claude/codex Parser + 共享模块去重）→ Step 3/4/5，且正确处理了 goal 要求的"超时/取消显式分支 decision 置 None"。
- 改造点 4（连带清理 + 新增测试）→ Step 6。
- 验收（PYTHONPATH + pytest 全绿、三个新增测试点）→ Step 5 测试策略表 + Step 7。

边界自律到位：非目标章节明确排除 next/on_status、Validator 纯函数化、Repair 闸口、_loop、loader/config、status/explain，与设计文档"4 个独立步骤"的第 1 步范围一致。

## 3. 主要风险（计划已识别，补充确认）

1. **decision=None 的下游消费（计划风险 #1）**：runner.py:431 `get_decision()` 仅赋值给局部变量、runner.py:401 是 validator-blocking 分支显式写 `decision="fail"`（runner 自身逻辑，非 parser）。两处均不会因 None 在第 1 步崩溃。MockAgent 仍产出业务 decision，集成测试不受影响。风险判断准确，归后续步骤处理合理。

2. **test_cli_adapters 的 success/done 断言（计划未单列，已替评审核实）**：line 140-141、191-192、699-700、811 的 `status=="success"`/`decision=="done"` 均来自测试喂入的**合法结构化 JSON**（走 `_parse_task_result_text` happy path），不经兜底分支，**不会因兜底改造变红**。cancelled/timeout 相关测试（line 771/1035）只断言 status、不断言 decision，故 decision: blocked→None 的改动也不会破坏它们。此风险实际可控。

3. **共享模块循环 import（计划风险 #3）**：`_parse.py` 仅 import `tasks.result`，与现有 agents→tasks.result 依赖同向，无环。判断正确。

## 4. 改进建议（非阻塞，execution 时一并处理）

1. **`get_decision()` 类型注解**：当前签名 `-> str`，改为返回 None 后应同步改为 `-> str | None`，避免类型提示与实现不符。计划 Step 1 描述了行为但未提注解，补一句即可。

2. **`_extract_task_result_fallback` 返回条件的语义陷阱（建议在 Step 3 明确）**：status 默认值改为 `"invalid_output"`（非空）后，若新返回条件写成"提取到任意可辨识字段（含 status）即返回"，则因 status 恒有非空默认值，函数将**永远返回非 None**，再不会回退到 `_parse_stream_output` 的最终 fallback 分支。建议：返回条件只统计**正则实际命中**的字段（status/summary/task_id/decision 任一真实提取到），而非被默认值填充的字段；否则两层 fallback 的职责边界会被悄悄改变。这是本计划唯一一处可能引发行为偏差的实现细节，值得在 Step 3 写清。

3. **缺失的回归测试**：计划改动了 cancelled/timeout/CLI-not-found/安全拦截 4~5 个显式分支的 decision（blocked/fail→None），但 Step 6 的新增测试只覆盖 parser 兜底与契约层，未对这些显式分支的 `decision is None` 加断言。现有 test_cli_adapters 的 cancelled/timeout 用例不检查 decision，意味着这些改动**无任何测试护栏**。建议在 test_parser_fallback 或 test_cli_adapters 补 1~2 条断言，把"显式分支 decision=None"钉死，防止后续回退。

4. **validator 移除 `"decision"` 必填的范围确认（已认可）**：Step 6 在 goal 字面（"去掉 VALID_DECISIONS import 及 warning 校验"）之外，额外把 validator required 列表的 `"decision"` 也移除。这是**必要的连带改动**——否则 decision=None/空会触发 validator line 45 的"缺少必需字段"blocking error，与"decision Optional"自相矛盾。改动落在允许修改的 validator 文件内，方向正确，记录在案即可，无需扩大范围讨论。

## 5. 可简化点

无明显过度设计。抽取 `_parse.py` 共享模块是 goal 明确建议且能消除两份同构逻辑漂移，合理。分 7 步、每步独立验证的粒度适中，不冗余。

## 6. 与上一轮的核对

本轮为首轮评审（staging 下无 plan_review_doc / plan_refinement_doc），无上一轮修改方向需核对。
