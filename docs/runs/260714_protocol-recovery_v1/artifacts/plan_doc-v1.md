# 协议恢复能力迭代 — 开发计划

> 依据：`docs/protocol-recovery-design.md`（设计草案）＋ 本次 goal 落点清单。仅计划，不含实现代码。

## 1. 需求理解

### 1.1 目标复述
分支节点（尤其大模型跑的审查/评审节点）常「语义判对、但没按协议输出结构化 TaskResult」——
结论在散文里写清楚了（如"决策 **revise**"），却没包 ```json``` 块。现引擎一律降级为
`invalid_output` → Repair 两次耗尽 → 终态 `failed`，一次格式偏差阻塞整条流水线。

本次把状态从「单维塌缩」升级为「语义 × 协议」双维度：
- 语义轴：沿用 `status`（成没成）。
- 协议轴：新增 `protocol_origin`（结论怎么拿到的：native/parser/repair/human）。

并引入分级恢复：常规 JSON 解析全部失败、且节点有 `allowed_decisions` 时，用纯 regex（Level 1）
从散文决策语境窗口内无歧义地恢复 decision，直接放行（记 `parser`），恢复留痕（RecoveryInfo +
ProtocolRecovery 事件），可度量、可归因。同时把 Repair 从"重新执行任务"瘦身为"纯格式转换器"。

### 1.2 验收标准（设计稿 §9 + goal）
1. Level 1 唯一命中 → 恢复（confidence=1.0）；冲突/无命中 → 维持 invalid_output（绝不猜）。
2. 线性节点（不传 allowed_decisions）→ 完全不恢复，解析行为与现状零差异。
3. 有合法 JSON 时结构化路径优先于恢复（native 路径不变）。
4. Repair 有 output 文档时生成格式转换 prompt（喂回落盘产物正文 + 最后一条 assistant 原话）；
   文档缺失退化为精简 prompt，不因 IO 异常崩；经 `task.output`+Resolver 取产物，禁硬编码文件名。
5. ProtocolRecovery 事件字段完整（含 `origin_text_hash`）。
6. 老 TaskResult 反序列化后 `protocol_origin=native`、`recovery=None`（向后兼容）。
7. Level 2 同义词恢复默认关闭（Feature Flag `enable_synonym_recovery`）。
8. 全量 `pytest -q` 无回归。

### 1.3 歧义点（按下述取舍推进，必要时确认）
- 术语差异：设计稿 §6 用 `protocol_state`(native/recovered/repaired/human) 挂 TaskResult 顶层；
  goal 用 `protocol_origin`(native/parser/repair/human) 挂 ExecutionMetadata。
  取舍：以 goal 为准（字段名 protocol_origin、值 native/parser/repair/human、位置在 ExecutionMetadata）。
  理由：goal 为当前迭代直接指令，且与既有 session_id/token_usage 等运行时元数据聚拢。
- Feature Flag 来源：goal 要 `enable_synonym_recovery` 但未指定入口。取舍：首版走解析函数参数（默认 False）
  + 经 `skill_policy` 通道从 adapter 透传，暂不改 WorkflowConfig（改动面小，L2 默认关闭即满足验收）。
- 恢复结果 status：设计稿明确 recovered 是成功。取舍：恢复成功的 TaskResult status=success，
  decision=恢复值，execution.protocol_origin=parser，走正常校验 + 产物 backfill。

## 2. 目标与非目标

目标：
- ExecutionMetadata 新增 `protocol_origin` + `recovery(RecoveryInfo)`，含 to_dict/from_dict 兼容。
- `_parse.py` 新增 `_recover_decision_from_prose`；`_parse_task_result_text` 增可选
  allowed_decisions/enable_synonym_recovery 参数（默认 None/False，行为不变）。
- claude_cli/codex_cli 从 skill_policy 透传 allowed_decisions；恢复命中写 RecoveryInfo。
- runner：parser 恢复结果直接放行 + protocol_origin 落 workflow_state；Repair 瘦身；
  repair 内恢复 origin 记 repair；恢复时发 ProtocolRecovery 事件。
- events.py 新增 ProtocolRecovery 事件类型 + registry。
- 完整单元测试覆盖 + Level 2 默认关闭。

非目标（本次不做）：
- Confidence 阈值路由（设计稿 §8.1 Phase 2）——首版 confidence 只审计。
- L4「模型只吐语义、程序拼协议」（§8.2，runtime-v3 立项）。
- 离线 recovery_rate 聚合报表工具。
- 改 WorkflowConfig/YAML schema 引入配置级开关（首版走函数参数通道）。

## 3. 涉及文件与模块边界

| 文件 | 改动 | 风险 |
|---|---|---|
| `tasks/result.py` | 新增 RecoveryInfo；ExecutionMetadata +protocol_origin(默认 native)+recovery；手写 to_dict/from_dict | 低 |
| `agents/_parse.py` | 新增 `_recover_decision_from_prose`(L1 regex+L2 同义词)；`_parse_task_result_text` 增参数 | 中 |
| `agents/claude_cli.py` | `_parse_stream_output` 从 skill_policy 取 allowed_decisions/flag 透传 | 低 |
| `agents/codex_cli.py` | 同上（3 处 `_parse_task_result_text` 调用点） | 低 |
| `observability/events.py` | +EventType.ProtocolRecovery + registry 条目 | 低 |
| `state_machine/runner.py` | parser 放行发事件 + protocol_origin 落盘；Repair 瘦身为格式转换；repair 内恢复记 repair | 中高 |
| `tests/unit/` | 分级恢复/repair 格式转换/事件/向后兼容 | — |

模块边界：恢复算法收敛在 `_parse.py`（纯函数、无 IO、可独立测）；事件发射与编排在 runner.py；
协议轴数据模型在 result.py；adapter 只做透传参数 + 调用，不含恢复逻辑。

## 4. 分步骤实现方案（每步可独立验证）

### 步骤 1：数据模型 — RecoveryInfo + ExecutionMetadata 协议轴
- `tasks/result.py` 新增 RecoveryInfo dataclass：`method`(native|regex|synonym)、`confidence: float`、
  `recovered_fields: list[str]`、`reason: str`、`origin_text_hash: str`（散文原文 sha256 短哈希），含 to_dict()。
- ExecutionMetadata 增 `protocol_origin: str = "native"` 与 `recovery: RecoveryInfo | None = None`。
  recovery 是嵌套对象，放弃 asdict、手写 to_dict()（recovery=None 序列化为 None、非 None 递归 to_dict）。
  新增 `ExecutionMetadata.from_dict()`：缺省 protocol_origin=native、recovery=None（老数据兼容）。
- 调整 TaskResult.from_dict：execution 为 dict 时改用 `ExecutionMetadata.from_dict`（而非 `**exec_data`，
  否则多余键 TypeError）。
- 验证：老 dict（无 protocol_origin）→ origin=="native"、recovery is None；带 recovery round-trip 一致。

### 步骤 2：恢复算法 `_recover_decision_from_prose`（纯函数）
- `_parse.py` 实现，签名 `_recover_decision_from_prose(text, allowed_decisions, enable_synonym_recovery=False)
  -> tuple[str, RecoveryInfo] | None`。
- Level 1（regex，confidence=1.0）：引导词表（决策/决定/最终决定/结论/裁决/判定/decision/verdict/
  conclusion/final decision）；每个引导词后约 40 字符窗口内以完整 token（前后非字母边界）匹配
  allowed_decisions；窗口命中的不同 decision 恰好 1 个 → 恢复，0 或 ≥2 → 返回 None（不猜）。
- Level 2（synonym，confidence=0.95，默认关闭）：仅 enable_synonym_recovery=True 且 L1 未命中时启用；
  显式受控 `_SYNONYM_TABLE`（短语→decision），同受窗口+唯一性约束，映射目标须 ∈ allowed_decisions。
- `origin_text_hash = sha256(text)[:16]`。
- 验证：L1 唯一/窗口外/冲突/无引导词；L2 关闭 vs 开启；空 allowed 不恢复；hash 稳定。

### 步骤 3：`_parse_task_result_text` 接入恢复（向后兼容）
- 签名加 `allowed_decisions=None`、`enable_synonym_recovery=False`。仅在原有全部解析路径失败（现返回
  None 前）且 allowed_decisions 非空时调恢复算法。命中 → 构造 `TaskResult(status="success",
  decision=<恢复值>, execution=ExecutionMetadata(protocol_origin="parser", recovery=<info>))`，
  summary 注明 parser 恢复；未命中维持 None。递归调用点透传新参数。
- 验证：不传 allowed 散文返回 None（线性零污染）；传参含唯一决策返回 success/parser；有合法 json 块优先 native。

### 步骤 4：adapter 透传 allowed_decisions
- `claude_cli._parse_stream_output`：从 `agent_input.skill_policy.get("allowed_decisions", [])` 取值
  （agent_input 可能 None，空值防御），连同 flag 传入两处 `_parse_task_result_text`。
- codex_cli：3 处调用点同样处理。flag 来源 `skill_policy.get("enable_synonym_recovery", False)`
  （首版恒 False，预留通道）。
- 验证：带 skill_policy.allowed_decisions 的 AgentInput + 散文 → 返回 parser 恢复；agent_input=None 行为同现状。

### 步骤 5：events.py 新增 ProtocolRecovery
- `EventType.ProtocolRecovery`；registry `["state","agent","method","confidence","recovered_fields",
  "reason","origin_text_hash","timestamp"]`。
- 验证：validate_event 对缺字段的检测。

### 步骤 6：runner 放行 parser 恢复 + 落 workflow_state + 发事件
- 主循环 `validation.valid` 分支检测 `task_result.get_execution().protocol_origin == "parser"` 且
  recovery 非 None → 发 ProtocolRecovery 事件（字段取自 recovery + state/agent）。恢复结果因
  status=success/decision∈allowed 天然走 valid 直接放行，无需特判路由。
- protocol_origin 已随 to_dict() 落入 record_task_result → workflow_state.json，仅验证其出现。
- 验证：parser 恢复经 runner 后 events 有 ProtocolRecovery、workflow_state execution.protocol_origin=="parser"。

### 步骤 7：Repair 瘦身为格式转换器
- 改 `_build_repair_agent_input`：① 经 `original_agent_input.task.output` + ArtifactResolver（或读
  `staging/<state>/<output>.md`，与 backfill 命名一致）取已落盘产物正文并截断（约 8000 字符），禁硬编码
  文件名；② 从 debug packet（`task_result.packet_path`）读最后一条 assistant 原话；③ prompt 改为
  "你不需要重审，只把以下结论包装成合法 TaskResult JSON，最后一条消息只输出 json 块"，附产物正文+最后
  消息+当前 status/decision；④ 读文件失败/缺失 → try/except 退化为现状精简 prompt（不抛异常）。
- `_repair_task_result`：repair 成功置 `protocol_origin="repair"`；若 repair 输出仍靠恢复兜回，覆盖
  origin 为 repair（goal：repair 内恢复 origin 记 repair），并发 ProtocolRecovery。
- 验证：有 output 文档时 prompt 含产物正文片段且不含旧"只允许修改 status/decision"措辞；文件缺失不抛
  异常且回退精简 prompt；repair 成功后 origin=="repair"。

## 5. 测试策略

单元测试（`$env:PYTHONPATH='src;.'; pytest tests/unit -q`）：
- `test_protocol_recovery.py`（新）：`_recover_decision_from_prose` 全分支（L1 唯一/窗口外/冲突/无引导词；
  L2 关闭 vs 开启；空 allowed；hash 稳定）。
- `test_parser_fallback.py`（扩展）：不传 allowed 零污染；传参散文恢复 success/parser；native 优先。
- `test_task_result_v4.py`（扩展）：ExecutionMetadata 新字段缺省兼容 + RecoveryInfo round-trip。
- `test_event_bus.py`（扩展）：ProtocolRecovery registry 校验。
- `test_repair.py`（扩展）：格式转换 prompt 内容、IO 退化不崩、repair 成功 origin=repair。
- adapter 透传：含/不含 skill_policy 的 AgentInput + 散文 stdout 断言。

回归：全量 `pytest tests -q` 无新增失败（重点 unit + integration）。

端到端（人工，可选）：对 M17 run retry，观察 output_review 稳定路由、events 有 ProtocolRecovery、
workflow_state 有 protocol_origin。

验证方式：每步跑对应单测；步骤 6/7 后跑 integration；收尾跑全量。

## 6. 风险与停止规则

| 风险 | 缓解 | 停止规则 |
|---|---|---|
| runner.py 核心编排，改动易回归 | 恢复结果天然走 valid 分支，改动集中在事件发射+repair prompt | integration 非 flaky 回归且 2 次定向修复无效 → 停下报告根因 |
| asdict 对嵌套 recovery 序列化不正确 | 手写 to_dict/from_dict + round-trip 测试 | round-trip 不过即回退手写、不用 asdict |
| 恢复误伤正文（叙述里的 approve 当决策） | 决策语境窗口+完整 token+唯一性裁决三重约束 | 误恢复用例失败 → 收窄窗口/引导词，L2 保持关闭 |
| Repair 读产物路径与 worktree/staging 分离 | 复用 backfill 已验证的 `staging/<state>/<output>.md` 命名；IO 全包 try/except | worktree 下取不到 → 退化精简 prompt（可接受不崩） |
| ExecutionMetadata.from_dict 破坏旧调用点 | 保留兼容分支，多余键不再 `**` 展开 | 旧测试失败 → 优先保兼容分支，不改 schema |
| skill_policy 未携带 allowed_decisions | 空值防御，取不到即不恢复（等价线性节点） | — |

通用停止规则：同一错误 2 次定向修复无效 → 停止增量补丁，回本文档核对根因；若 goal 与设计稿字段差异
导致下游（status/explain/history）解读异常，暂停并向用户确认术语口径。

## 7. 预期产物

代码变更（`src/agent_workflow/`）：`tasks/result.py`（RecoveryInfo + protocol_origin/recovery +
to/from_dict）、`agents/_parse.py`（恢复算法 + 新参数）、`agents/claude_cli.py`/`codex_cli.py`（透传）、
`observability/events.py`（ProtocolRecovery）、`state_machine/runner.py`（parser 放行发事件 + Repair
瘦身 + repair 内恢复记 repair）。

测试：新增 `tests/unit/test_protocol_recovery.py`；扩展 `test_parser_fallback.py`/
`test_task_result_v4.py`/`test_event_bus.py`/`test_repair.py`。

验证结果：全量 `pytest -q` 通过。

（可选）记忆：实现中若发现非显而易见约定（asdict 嵌套陷阱、窗口边界取舍）写入 `memory/`。

本节点交付：本计划文档 `plan_doc.md`，供审核。decision=done。
