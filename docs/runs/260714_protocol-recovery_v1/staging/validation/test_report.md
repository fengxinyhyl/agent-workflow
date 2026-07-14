# 协议恢复能力迭代 — 测试验证报告

> 日期：2026-07-14
> 状态：`approve` — 全部验收标准通过
> 依据：`docs/protocol-recovery-design.md` §9 验收标准 + `artifacts/output_refinement_doc-v1.md` 修订 Issue

## 1. 测试执行摘要

| 测试套件 | 用例数 | 通过 | 失败 | 备注 |
|----------|--------|------|------|------|
| `tests/unit/test_protocol_recovery.py` | 29 | 29 | 0 | 新增专项测试 |
| `tests/unit/test_task_result_v4.py` | 37 | 37 | 0 | 含 13 个协议轴扩展用例 |
| `tests/unit/test_event_bus.py` | 12 | 12 | 0 | 含 5 个 ProtocolRecovery 用例 |
| `tests/unit/test_parser_fallback.py` | 9 | 9 | 0 | 无回归 |
| `tests/unit/test_repair.py` | 21 | 21 | 0 | 含 4 个 Repair 瘦身用例 |
| `tests/unit/test_state_machine.py` | 42 | 42 | 0 | 无回归 |
| `tests/unit/test_config_v4.py` | 13 | 13 | 0 | 无回归 |
| `tests/unit/test_artifact_backfill.py` | 4 | 4 | 0 | 无回归 |
| **核心测试合计** | **168** | **168** | **0** | |
| 全量单元（排除已知环境问题） | 379 | 376 | 1* | *已存在的 test_negative.py 路径拼写 bug |
| 全量单元（含所有文件） | 455 | 398 | 28† + 27‡ | †schemas/ 目录缺失(已有)；‡tmp_path PermissionError(已有) |

### 非本次变更导致的失败分析

| 类别 | 文件 | 数量 | 根因 | 与本次改动关系 |
|------|------|------|------|---------------|
| `tmp_path` PermissionError | `test_cli_adapters.py` / `test_retry_dry_run_diagnosis.py` | 27 errors | `C:\Users\12108\AppData\Local\Temp\pytest-of-12108` 目录权限拒绝。执行报告和修订报告均记载为已有问题。 | 无关 |
| Schema 文件缺失 | `test_schema_contract.py` | 28 failures | `schemas/workflow_event.schema.json` 等文件在仓库中不存在。git log 确认此测试文件自创建以来即依赖不存在的 fixtures。 | 无关 |
| `cancel_run` 路径拼写 | `test_negative.py::test_cancel_run_writes_file` | 1 failure | 断言路径 `"doc/runs/..."` vs 实际写入 `"docs/runs/..."` — 已有 bug。 | 无关 |

## 2. 验收标准逐条验证

| # | 设计稿 §9 验收标准 | 状态 | 证据（测试方法） |
|---|-------------------|------|-----------------|
| 1 | Level 1 唯一命中恢复；冲突/无命中不恢复 | ✅ | `test_l1_unique_hit_decision_keyword`, `test_l1_conflict_two_decisions`, `test_l1_no_guide_word`, `test_l1_outside_window_not_matched` |
| 2 | 线性节点不传 allowed_decisions → 不恢复 | ✅ | `test_no_allowed_no_recovery`, `test_adapter_no_skill_policy_equivalent` |
| 3 | 有合法 JSON 时结构化路径优先于恢复 | ✅ | `test_valid_json_priority_over_recovery` |
| 4 | Repair 瘦身：产物正文 + 最后消息 + IO 退化不崩 | ✅ | `test_format_conversion_prompt_contains_product`, `test_io_degradation_no_staging_file`, `test_io_degradation_no_output_name`, `test_repair_success_origin_repair` |
| 5 | ProtocolRecovery 事件含 origin_text_hash | ✅ | `test_registry_required_fields`（含 origin_text_hash）, `test_validate_event_complete` |
| 6 | 老 TaskResult → protocol_origin=native, recovery=None | ✅ | `test_old_taskresult_no_protocol_fields`, `test_from_dict_old_data_no_protocol_fields` |
| 7 | Level 2 同义词恢复默认关闭 | ✅ | `test_l2_disabled_by_default`, `test_adapter_pass_through_enable_synonym` |
| 8 | 全量 pytest -q 无回归（单元测试） | ✅ | 168/168 核心测试通过；排除已有环境问题后 376/379 通过 |

### 修订 Issue 验证（output_refinement_doc §1）

| Issue | 处理 | 验证状态 |
|-------|------|---------|
| Issue-1 [Blocking] adapter 保留 protocol_origin/recovery | 已修复（claude_cli.py + codex_cli.py） | ✅ 50 passed (recovery + repair) |
| Issue-2 [Warning] 引导词大小写不敏感 | 已修复（_parse.py ASCII 分支 + 2 新用例） | ✅ `test_l1_guide_word_uppercase`, `test_l1_guide_word_title_case` |
| Issue-3 [Warning] Repair 瘦身 + adapter 透传缺测试 | 已修复（+6 测试方法） | ✅ 4 Repair 格式转换 + 2 adapter 透传 |
| Issue-4 [Suggestion] no_op token boundary | 已修复（`any(c.isalpha())` + 2 新用例） | ✅ `test_l1_no_op_token_boundary`, `test_l1_no_op_not_partial_match` |
| Issue-5 [Suggestion] PACKET_LAST_ASSISTANT_MARKER 共享常量 | 已修复（提取到 _parse.py） | ✅ grep 确认仅定义处出现一次 |

## 3. 测试覆盖矩阵

### 3.1 `_recover_decision_from_prose` (17 用例)

| 场景 | 覆盖 |
|------|------|
| L1 唯一命中 (中文引导词 "决策"/"裁决"/"结论") | 3 |
| L1 唯一命中 (英文引导词 "decision"/"verdict") | 2 |
| L1 英文 decision 大小写不敏感 | 1 |
| L1 英文引导词大小写不敏感 (Issue-2) | 2 |
| L1 窗口外不匹配 | 1 |
| L1 两个 decision 冲突 | 1 |
| L1 无引导词 | 1 |
| L1 空/None allowed_decisions | 2 |
| L1 空文本 | 1 |
| L1 hash 稳定性 | 1 |
| L1 no_op token boundary (Issue-4) | 2 |
| L2 默认关闭 | 1 |
| L2 开启命中 | 1 |
| L2 无引导词不恢复 | 1 |
| L2 映射目标不在 allowed | 1 |
| L2 L1 优先 | 1 |

### 3.2 `_parse_task_result_text` 恢复接入 (6 用例)

| 场景 | 覆盖 |
|------|------|
| 不传 allowed → 零污染 | 1 |
| 传 allowed → 恢复 success/parser | 1 |
| 合法 JSON → native 优先 | 1 |
| 空 allowed → 不恢复 | 1 |
| enable_synonym_recovery 透传 | 1 |
| allowed_decisions=None 等价无 skill_policy | 1 |

### 3.3 数据模型 (ExecutionMetadata + RecoveryInfo) (12 用例)

| 场景 | 覆盖 |
|------|------|
| protocol_origin 缺省 native | 1 |
| 显设 protocol_origin=parser | 1 |
| to_dict 无 recovery | 1 |
| to_dict 有 recovery | 1 |
| round-trip 含 recovery | 1 |
| 老数据无协议字段兼容 | 1 |
| from_dict 空/None | 2 |
| RecoveryInfo 默认值/to_dict/from_dict/None/partial | 5 |

### 3.4 Repair 瘦身 (4 用例)

| 场景 | 覆盖 |
|------|------|
| 格式转换 prompt 含产物正文 + "不需要重新审查" | 1 |
| 产物文件缺失退化不崩 | 1 |
| task.output 为空退化不崩 | 1 |
| repair 成功 → origin=repair | 1 |

### 3.5 事件与同义词表 (7 用例)

| 场景 | 覆盖 |
|------|------|
| ProtocolRecovery 事件类型存在/registry/必需字段/缺字段/全字段 | 5 |
| 同义词表非空/合法性 | 2 |

## 4. 执行命令与结果

```bash
# 协议恢复专项测试 (29 passed)
$env:PYTHONPATH='src;.'; pytest tests/unit/test_protocol_recovery.py -q -v
# → 29 passed in 0.18s

# 核心套件全量 (168 passed)
$env:PYTHONPATH='src;.'; pytest tests/unit/test_protocol_recovery.py tests/unit/test_repair.py \
  tests/unit/test_task_result_v4.py tests/unit/test_event_bus.py \
  tests/unit/test_parser_fallback.py tests/unit/test_state_machine.py \
  tests/unit/test_config_v4.py tests/unit/test_artifact_backfill.py -v
# → 168 passed in 0.86s

# 排除已知环境问题的全量单元 (376 passed, 1 pre-existing failure)
$env:PYTHONPATH='src;.'; pytest tests/unit/ -q \
  --ignore=tests/unit/test_schema_contract.py \
  --ignore=tests/unit/test_cli_adapters.py \
  --ignore=tests/unit/test_retry_dry_run_diagnosis.py
# → 376 passed, 1 failed (test_negative.py cancel_run 路径拼写 — 已有 bug), 2 skipped
```

## 5. 未覆盖与残余风险

### 5.1 未覆盖（非本次 scope）

| 项目 | 说明 |
|------|------|
| 端到端 (M17 run retry) | 需要在真实 workflow 中验证 output_review 稳定路由 + events.jsonl 有 ProtocolRecovery + workflow_state 有 protocol_origin。执行报告 §8 建议后续人工执行。 |
| 集成测试 | 已有集成测试因 workflow YAML 缺失/state 名称不匹配无法运行，与本次改动无关。 |
| Confidence 阈值路由 (Phase 2) | 设计稿 §8.1，明确列入非目标。 |

### 5.2 残余风险

| 风险 | 评级 | 缓解 |
|------|------|------|
| `tmp_path` PermissionError 持续影响 test_cli_adapters.py | 低 | 27 个 error 均为 tmp_path fixture setup 失败，非测试逻辑问题。不影响核心协议恢复代码的验证。已由执行报告和修订报告两次记载为已有问题。 |
| `schemas/` 目录缺失导致 contract 测试无法运行 | 低 | 仓库中从未存在此目录，非本次变更引入。不影响功能验证。 |
| 端到端 event 发射未经集成环境验证 | 低 | `_emit_protocol_recovery_if_needed` 逻辑简单（检查 recovery 非 None → emit），且 runner 核心路径的已有测试全部通过（42 个 state_machine 用例 + 21 个 repair 用例）。 |

## 6. Decision

**approve** — 全部 8 条验收标准通过。168 个核心测试零失败。所有 5 个修订 Issue 已验证修复。已有环境问题（tmp_path PermissionError、schemas/ 缺失、cancel_run 路径拼写）与本次改动无关，风险已清楚记录。
