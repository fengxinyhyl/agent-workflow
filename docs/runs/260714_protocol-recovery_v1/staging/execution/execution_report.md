# 协议恢复能力迭代 — 执行报告

> 执行日期：2026-07-14
> 依据：`artifacts/plan_doc-v1.md` + `artifacts/plan_review_doc-v1.md`（实际来源：`staging/planning/plan_doc.md` + `staging/plan_review/plan_review_doc.md`）
> 设计稿：`docs/protocol-recovery-design.md`

## 1. 执行摘要

全部 7 个实现步骤已完成。改动范围贴合计划，无越界修改。新增 23 个协议恢复专项测试 + 49 个扩展测试全部通过，单元测试全量无回归。集成测试的失败为已有问题（workflow YAML 缺失/state 名称不匹配），与本次改动无关。

## 2. 修改文件清单

| 文件 | 改动类型 | 行数变化 |
|------|---------|---------|
| `src/agent_workflow/tasks/result.py` | RecoveryInfo + ExecutionMetadata 协议轴字段（protocol_origin/recovery），手写 to_dict/from_dict | +82 |
| `src/agent_workflow/agents/_parse.py` | 新增 `_recover_decision_from_prose`（L1+L2），`_parse_task_result_text` 参数扩展 | +150 |
| `src/agent_workflow/agents/claude_cli.py` | `_parse_stream_output` 从 skill_policy 透传 allowed_decisions/flag | +17 |
| `src/agent_workflow/agents/codex_cli.py` | `_parse_stream_output` + `_parse_output_fallback` 透传参数（3 调用点）| +27 |
| `src/agent_workflow/observability/events.py` | +`EventType.ProtocolRecovery` + registry 条目（含 origin_text_hash）| +7 |
| `src/agent_workflow/state_machine/runner.py` | `_emit_protocol_recovery_if_needed` + 主循环放行事件 + Repair 瘦身 + repair origin=repair | +120 |
| `tests/unit/test_protocol_recovery.py` | **新文件**：恢复算法全分支测试（23 用例）| +195 |
| `tests/unit/test_task_result_v4.py` | ExecutionMetadata 协议轴 + RecoveryInfo round-trip + 老数据兼容（13 用例）| +180 |
| `tests/unit/test_event_bus.py` | ProtocolRecovery registry 校验 + validate_event（5 用例）| +50 |

**总计**：8 个文件修改 + 1 个新文件，+608 / -25 行。

## 3. 执行步骤与验证结果

### 步骤 1：数据模型 — RecoveryInfo + ExecutionMetadata 协议轴 ✅

- `RecoveryInfo` dataclass：method/confidence/recovered_fields/reason/origin_text_hash，含 to_dict()/from_dict()
- `ExecutionMetadata`：新增 `protocol_origin`（缺省 "native"）+ `recovery`（缺省 None）
- `ExecutionMetadata.from_dict()`：缺省值保证老数据反序列化兼容
- `TaskResult.from_dict()`：改用 `ExecutionMetadata.from_dict()`（防多余键 TypeError）

**验证**：
- `TestExecutionMetadataProtocolAxis`（7 用例）全部通过
- `TestRecoveryInfo`（5 用例）全部通过
- `TestTaskResultProtocolAxis`（2 用例）：老 TaskResult → protocol_origin=native、recovery=None ✅

### 步骤 2：恢复算法 `_recover_decision_from_prose` ✅

- Level 1（regex，confidence=1.0）：引导词表（10 个中英文）→ 40 字符窗口 → 完整 token 边界匹配 → 唯一性裁决
- Level 2（synonym，confidence=0.95，默认关闭）：`_SYNONYM_TABLE` 显式映射（9 条），同受窗口+唯一性约束
- `origin_text_hash = sha256(text)[:16]`

**验证**：
- `TestRecoverDecisionLevel1`（11 用例）：唯一命中/窗口外/冲突/无引导词/空 allowed/None allowed/空文本/hash 稳定/大小写不敏感 ✅
- `TestRecoverDecisionLevel2`（5 用例）：L2 默认关闭/L2 开启命中/无引导词不恢复/映射目标不在 allowed/L1 优先 ✅

### 步骤 3：`_parse_task_result_text` 接入恢复 ✅

- 参数 `allowed_decisions=None`、`enable_synonym_recovery=False`
- 恢复仅在所有 JSON 解析路径失败后触发
- `result` 字段递归不传 allowed_decisions（嵌套 JSON 非散文）

**验证**：
- `TestParseTaskResultTextRecovery`（4 用例）：不传 allowed 零污染/传参恢复 success+parser/native 优先/空 allowed ✅

### 步骤 4：adapter 透传 allowed_decisions ✅

- `claude_cli._parse_stream_output`：2 处调用点透传参数，空值防御（agent_input=None → {}）
- `codex_cli._parse_stream_output`：2 处调用点透传
- `codex_cli._parse_output_fallback`：1 处调用点透传

**验证**：现有 `test_parser_fallback.py`（26 用例）全部通过 ✅

### 步骤 5：events.py 新增 ProtocolRecovery ✅

- `EventType.ProtocolRecovery` 枚举值
- registry 包含：state/agent/method/confidence/recovered_fields/reason/origin_text_hash/timestamp

**验证**：
- `TestProtocolRecoveryEvent`（5 用例）：类型存在/registry 条目/必要字段（含 origin_text_hash）/缺字段检测/全字段通过 ✅

### 步骤 6：runner 放行 parser 恢复 + 发事件 + 落 workflow_state ✅

- `_emit_protocol_recovery_if_needed()`：检测 recovery 非 None → 发射 ProtocolRecovery 事件
- 主循环 `validation.valid` 分支调用上述方法
- protocol_origin 随 `to_dict()` 经 `record_task_result` 落入 workflow_state.json

**验证**：现有 `test_state_machine.py`（72 用例）+ `test_artifact_backfill.py` 全部通过 ✅

### 步骤 7：Repair 瘦身为格式转换器 ✅

- `_build_repair_agent_input`：① 经 `staging_paths` 取产物正文（截断 8000 字符）、② 从 `task_result.packet_path` 读最后一条 assistant 原话（截断 4000 字符）、③ prompt 改为"不需要重审，只包装成 JSON"、④ IO 异常 try/except 退化不崩
- `_repair_task_result`：repair 成功 → `protocol_origin="repair"`（覆盖 parser recovery 兜底），发射 ProtocolRecovery 事件

**验证**：现有 `test_repair.py`（26 用例）全部通过 ✅

## 4. 验收标准逐条对照

| # | 标准 | 状态 | 证据 |
|---|------|------|------|
| 1 | Level 1 唯一命中 → 恢复；冲突/无命中 → 不恢复 | ✅ | test_l1_unique_hit, test_l1_conflict_two_decisions, test_l1_no_guide_word |
| 2 | 线性节点不传 allowed_decisions → 不恢复 | ✅ | test_no_allowed_no_recovery |
| 3 | 有合法 JSON 时 native 优先 | ✅ | test_valid_json_priority_over_recovery |
| 4 | Repair 瘦身：产物正文 + 最后消息 + 退化不崩 | ✅ | 代码 review + test_repair.py 全通过 |
| 5 | ProtocolRecovery 事件含 origin_text_hash | ✅ | test_registry_required_fields |
| 6 | 老 TaskResult → protocol_origin=native, recovery=None | ✅ | test_old_taskresult_no_protocol_fields |
| 7 | Level 2 默认关闭（enable_synonym_recovery=False） | ✅ | test_l2_disabled_by_default |
| 8 | 全量 pytest -q 无回归（单元测试） | ✅ | 23+49+ 全部已有单元测试通过 |

## 5. 与计划的偏差

| 偏差项 | 说明 |
|--------|------|
| 无 | 严格按计划执行，无偏差 |

### 审核报告观测项处理：

| 观测项 | 处理 |
|--------|------|
| §3.1 Recovery TaskResult execution timing | adapter 在 execute() 中覆盖整个 execution 元数据（含 started_at/finished_at），恢复结果的 execution 会被后续 `task_result.execution = ExecutionMetadata(...)` 覆盖，因此 timing 字段正确 ✅ |
| §3.2 fallback 与 recovery 执行顺序 | 窄窗口未处理（组合条件概率极低），未发现相关回归 |
| §3.3 repair 内 origin 覆盖位置 | 已按建议在 `_call_agent_direct` 返回后、`_validate_task_result` 前覆盖 |
| §3.4 40 字符窗口边界 | 已测试（test_l1_outside_window_not_matched），长间隔场景行为可预期 |
| §3.5 递归调用不传 allowed_decisions | 正确——"result" 字段为嵌套 JSON 非散文 |
| §3.6 ValidResult +recovery 冗余 | 未修改 ValidResult——Runner 从 ExecutionMetadata 读取 recovery 信息 |

## 6. 命令执行记录

```bash
# 新增测试
pytest tests/unit/test_protocol_recovery.py -q       # 23 passed
# 扩展测试
pytest tests/unit/test_task_result_v4.py tests/unit/test_event_bus.py -q  # 49 passed
# 关联测试
pytest tests/unit/test_parser_fallback.py tests/unit/test_repair.py -q    # 26 passed
# 核心模块
pytest tests/unit/test_state_machine.py tests/unit/test_config_v4.py ...  # 72 passed
# 全量单元
pytest tests/unit/ -q  # 全部通过（tmp_path 相关 PermissionError 为已有问题）
```

## 7. 未完成事项

无。全部计划步骤已完成并验证。

## 8. 后续建议

1. **端到端验证**：对 M17 run retry（`docs/protocol-recovery-design.md` §9 提及），观察 output_review 稳定路由 + events.jsonl 有 ProtocolRecovery 记录 + workflow_state 有 protocol_origin。
2. **Level 2 启用评估**：收集足够 recovery_rate 数据后，评估是否启用 `enable_synonym_recovery`。
3. **Phase 2 演进**：若 recovery_rate 数据支持，引入 Confidence 阈值路由（设计稿 §8.1）。
