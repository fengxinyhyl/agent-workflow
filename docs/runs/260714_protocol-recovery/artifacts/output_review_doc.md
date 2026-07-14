# 输出审查报告：协议恢复能力实现（output_review）

## 审查结论：approve

协议恢复能力实现完整覆盖设计稿 `docs/protocol-recovery-design.md` 与任务目标全部落点，
核心模块单元测试 99 项全绿，无 Blocking 问题。可进入下一步。

## 一、落点逐条核对（设计稿 §6 / 任务目标）

| # | 落点 | 实现位置 | 状态 |
|---|------|---------|------|
| 1 | ExecutionMetadata + `protocol_origin`(native/parser/repair/human) + `recovery` | `tasks/result.py:114-150` | ✅ |
| 2 | `RecoveryInfo`（method/confidence/recovered_fields/reason/origin_text_hash）+ to/from_dict | `tasks/result.py:65-102` | ✅ |
| 3 | `_recover_decision_from_prose`（Level1 regex，allowed_decisions 参数） | `agents/_parse.py:44-142` | ✅ |
| 4 | `_parse_task_result_text` 新增可选 allowed_decisions（默认 None 零污染） | `agents/_parse.py:145-205` | ✅ |
| 5 | Runner：parser 恢复结果直接放行、protocol_origin=parser | `agents/_parse.py:201`；`claude_cli.py:237-270` | ✅ |
| 6 | Repair 瘦身：经 task.output + staging_paths 取产物、禁硬编码具体文件名 | `runner.py:955-1022` | ✅ |
| 7 | Repair 内恢复 origin 记 repair | `runner.py:1091` | ✅ |
| 8 | ProtocolRecovery 事件（含 origin_text_hash）+ registry | `events.py:53,77-80`；`runner.py:895-909` | ✅ |
| 9 | Level2 同义词恢复默认关闭（Feature Flag enable_synonym_recovery） | `_parse.py:47,130`（默认 False） | ✅ |
| 10 | 完整单元测试覆盖 | `tests/unit/test_protocol_recovery.py`（30+ 用例） | ✅ |

## 二、验收标准核对（设计稿 §9）

- Level 1 唯一命中恢复 — `test_l1_unique_hit_decision_keyword` ✅
- Level 2 同义词命中恢复 — `test_l2_enabled_hit`（confidence=0.95, method=synonym）✅
- 冲突/无命中不恢复 — `test_l1_conflict_two_decisions` / `test_l1_no_guide_word` ✅
- 线性节点（不传 allowed_decisions）不恢复 — `test_no_allowed_no_recovery` ✅
- 合法 JSON 时结构化路径优先于恢复 — `test_valid_json_priority_over_recovery`（protocol_origin 保持 native）✅
- repair 有 output 文档时格式转换 prompt、缺失时退化不抛异常 — `runner.py:993-1022` 双分支 + `except (OSError, IOError): pass` ✅
- ProtocolRecovery 事件字段完整 — `events.py` registry 8 字段 ✅
- 老 TaskResult 反序列化后 protocol_origin=native — `ExecutionMetadata.from_dict` 缺省 native、recovery=None ✅

## 三、实现质量亮点

1. **零污染兼容**：`allowed_decisions=None`（线性节点/旧调用点）时恢复完全不触发，
   native 路径与恢复路径正交，历史 TaskResult 反序列化行为不变。
2. **保守不伪造**：Level 1 采用「引导词窗口 + 完整 token 匹配 + 唯一性裁决」三重约束，
   窗口内命中 ≥2 个不同 decision 即放弃（`test_l1_conflict_two_decisions`），
   `no_op` 用 word-boundary regex 防子串误伤（`test_l1_no_op_not_partial_match`）。
3. **Repair 已成纯格式转换器**：喂回本 state 已落盘产物正文（经 `task.output` + `staging_paths` 定位，
   fallback 基于产物流名 `{output_name}.md` 拼接而非写死具体文件名）+ debug packet 最后消息，
   指令明确"不需要重审"，符合设计稿 §5 瘦身意图。
4. **审计溯源完整**：`origin_text_hash`(sha256 前 16 字符) 稳定可复现（`test_l1_hash_stable`），
   ProtocolRecovery 事件喂 recovery_rate 统计。

## 四、测试与回归

- 核心模块：`test_protocol_recovery.py` + `test_repair.py` + `test_task_result_v4.py` + `test_event_bus.py`
  = **99 passed**。
- 全量 `tests/unit`：28 failed + 27 errors，**全部为 worktree 环境问题，非本次回归**：
  - `test_cli_adapters` / `test_retry_dry_run` 的 27 ERROR = `PermissionError [WinError 5]`
    pytest 临时目录拒绝访问（worktree 权限误报）。
  - `test_schema_contract`（26 failed）= `schemas/*.json` 未 checkout 到本 worktree（目录不存在）。
  - `test_negative` cancel 测试 = 同一 tmp 权限问题。
  - 上述文件 git status 均为空（本次未触碰），失败根因与协议恢复代码无因果关联。

## 五、Issue 清单

无 Blocking / Warning 级问题。

**Suggestion（非阻塞，可后续跟进）**：
- 设计稿 §6 曾提及 `validators/validation_result.py` 的 `ValidResult` 新增 `recovery` 字段供 Runner 读；
  当前实现通过 ExecutionMetadata.recovery + `_emit_protocol_recovery_if_needed` 从 task_result 直接读取，
  达成等效审计发射，未额外在 ValidResult 上冗余承载。此为合理简化，不构成缺陷。
  - Acceptance：ProtocolRecovery 事件在恢复发生时被发射且字段完整 —— 已由 `runner.py:895-909` + registry 保证。
  - Verification：PASS（`test_event_bus.py` 相关用例通过）。

## 六、术语一致性说明

设计稿 §2 草案用 `protocol_state`(recovered/repaired) 挂 TaskResult；
任务目标与最终实现统一为 `protocol_origin`(parser/repair) 挂 ExecutionMetadata。
这是设计落地时的口径收敛（协议轴归入执行元数据），前后一致、有意为之，非实现偏差。
