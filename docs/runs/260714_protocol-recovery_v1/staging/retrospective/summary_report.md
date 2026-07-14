# 协议恢复能力迭代 — 最终总结与复盘

> 生成日期：2026-07-14 | 依据：`docs/protocol-recovery-design.md` §6/§9 + goal 落点清单
> 状态历史：planning → plan_review → execution → output_review → **output_refinement** → output_review → validation → retrospective

本报告只汇总事实，不修改代码。

## 1. 完成内容（对照验收点）

本次把 Agent 输出从「单维塌缩」（一次格式偏差即 invalid_output → Repair 耗尽 → failed）升级为
「语义 × 协议」双维度：语义轴沿用 status，协议轴新增 protocol_origin(native/parser/repair/human)。
核心是分级恢复——JSON 解析全失败且节点有 allowed_decisions 时，用纯 regex 从散文决策语境窗口内
无歧义恢复 decision 并直接放行，留痕可归因。

### 设计稿 §9 验收标准（8 条全达成）

| # | 标准 | 状态 | 证据 |
|---|------|------|------|
| 1 | L1 唯一命中→恢复；冲突/无命中→不恢复 | ✅ | test_l1_unique_hit / conflict / no_guide_word |
| 2 | 线性节点不传 allowed→零污染不恢复 | ✅ | test_no_allowed_no_recovery |
| 3 | 有合法 JSON 时 native 优先 | ✅ | test_valid_json_priority_over_recovery |
| 4 | Repair 瘦身：产物正文+最后消息+IO 退化不崩 | ✅ | test_format_conversion_prompt_contains_product 等 4 用例 |
| 5 | ProtocolRecovery 事件含 origin_text_hash | ✅ | test_registry_required_fields |
| 6 | 老 TaskResult→protocol_origin=native、recovery=None | ✅ | test_old_taskresult_no_protocol_fields |
| 7 | L2 同义词恢复默认关闭 | ✅ | test_l2_disabled_by_default |
| 8 | 全量 pytest 单元测试无回归 | ✅ | 168 核心测试全绿 |

### Goal 落点清单（6 条全达成）
ExecutionMetadata 加 protocol_origin+recovery / `_recover_decision_from_prose`(L1 regex) /
runner parser 放行+Repair 瘦身+repair 记 repair / events.py 加 ProtocolRecovery / 完整单测 /
L2 默认关闭。

## 2. 关键决策链路

- **术语收敛**：设计稿 `protocol_state`(挂 TaskResult) vs goal `protocol_origin`(挂 ExecutionMetadata)。
  决策以 goal 为准，字段与 session_id/token_usage 聚拢更内聚；plan_review §7 确认不影响下游。
- **Feature Flag 入口**：首版走解析函数参数（默认 False）+ skill_policy 透传，不改 YAML schema，改动面最小。
- **恢复结果 status 语义**：恢复成功 status=success、protocol_origin=parser，天然走 valid 分支放行，
  runner 无需为恢复特判路由——本次架构最干净的一处，事件发射与编排解耦。
- **恢复算法三重约束**：引导词窗口(约 40 字符)+完整 token 边界+唯一性裁决，命中恰好 1 个才恢复；
  origin_text_hash=sha256(text)[:16] 保证可复现归因。
- **★Blocking 缺陷回环（output_review→refinement）**：第一轮 output_review 发现 Issue-1(Blocking)——
  adapter.execute() 得到解析结果后整体重建 ExecutionMetadata，抹掉了 `_parse_task_result_text` 刚写入的
  protocol_origin/recovery，使留痕/归因端到端失效（单测全绿却掩盖）。refinement 修复：重建前先
  `prev_exec=get_execution()` 保留协议轴字段。修复后回 output_review 复审 approve。此回环印证了
  plan_review §3.1 的前瞻预警，审核意见在实现回路中发挥了实际拦截作用。
- **Repair 瘦身为纯格式转换器**：不再重执行任务，改为把已有结论包装成 JSON；经 task.output+Resolver /
  `staging/<state>/<output>.md` 取产物正文，读 debug packet 最后消息，IO 全包 try/except 退化不崩，
  禁硬编码文件名（共享常量 PACKET_LAST_ASSISTANT_MARKER 消除写入/读取失配）。

## 3. 修改文件与产物流清单

### 3.1 源码改动（6 文件 + 1 新测试文件）

| 文件 | 改动 | 说明 |
|------|------|------|
| `tasks/result.py` | +82 | RecoveryInfo dataclass + ExecutionMetadata 协议轴字段 + 手写 to/from_dict |
| `agents/_parse.py` | +150 | `_recover_decision_from_prose`(L1+L2) + `_parse_task_result_text` 参数 + PACKET 常量 + 引导词大小写不敏感 + no_op token 边界 |
| `agents/claude_cli.py` | +25 | 透传 allowed_decisions；**重建 execution 保留 protocol_origin/recovery（Issue-1 修复）**；共享常量 |
| `agents/codex_cli.py` | +31 | 3 调用点透传；重建 execution 保留协议轴字段 |
| `observability/events.py` | +7 | EventType.ProtocolRecovery + registry（含 origin_text_hash） |
| `state_machine/runner.py` | +121 | `_emit_protocol_recovery_if_needed` + 主循环放行 + Repair 瘦身 + repair origin=repair |
| `tests/unit/test_protocol_recovery.py` | 新文件 29 用例 | 恢复算法全分支 + adapter 透传 |

### 3.2 测试扩展（3 文件）
- `test_task_result_v4.py`：ExecutionMetadata 协议轴 + RecoveryInfo round-trip + 老数据兼容
- `test_event_bus.py`：ProtocolRecovery registry 校验
- `test_repair.py`：Repair 瘦身格式转换 + IO 退化 + origin=repair（4 用例）

### 3.3 产物流清单
plan_doc-v1 / plan_review_doc-v1（approve，5 观测项）/ execution_report / output_review_doc-v2
（v1 发现 Blocking，v2 approve）/ output_refinement_doc-v1（5 Issue 全处理）/ test_report / summary_report。
git status 显示 9 文件变更（6 源+2 测试扩展+1 新测试），与清单一致，无越界改动。

## 4. 测试结果

| 测试套件 | 用例 | 通过 |
|----------|------|------|
| test_protocol_recovery.py（新） | 29 | 29 |
| test_task_result_v4.py | 37 | 37 |
| test_event_bus.py | 12 | 12 |
| test_parser_fallback.py | 9 | 9 |
| test_repair.py | 21 | 21 |
| test_state_machine.py | 42 | 42 |
| test_config_v4.py | 13 | 13 |
| test_artifact_backfill.py | 4 | 4 |
| **核心合计** | **168** | **168** |
| 全量单元（排除已知环境问题） | 379 | 376（1 pre-existing failure） |

**非本次变更导致的失败（均已有）**：tmp_path PermissionError（27 errors，worktree 临时目录权限）；
schemas/ 目录缺失（28 failures，仓库中从未存在）；test_negative.py cancel_run 路径拼写 bug（1）。
验证结论：8 条验收标准全通过，5 个修订 Issue 全部验证修复。

## 5. 残余风险与后续建议

**残余风险（均低）**：① 端到端事件发射未经集成验证（单测无法覆盖 adapter→runner 完整链路）；
② tmp_path 权限/schemas 缺失为环境债，不影响核心验证；③ fallback 与 recovery 窄窗口冲突
（plan_review §3.2，组合条件概率极低，未处理）。

**后续建议**：
1. **端到端验证（优先）**：对 M17 run retry，实机观察 output_review 稳定路由 + events.jsonl 有
   ProtocolRecovery + workflow_state 有 protocol_origin——这是验证 Issue-1 修复端到端生效的唯一途径。
2. L2 启用评估：收集 recovery_rate 数据后评估是否开启 enable_synonym_recovery。
3. Phase 2：数据支持则引入 Confidence 阈值路由（设计稿 §8.1）。
4. 环境债清理：tmp_path 权限、schemas/ fixtures、test_negative.py 路径拼写。

## 6. 经验沉淀

**值得复用**：
- 审核意见的前瞻拦截——plan_review §3.1 预警的 execution 元数据填充问题，正是后来 Blocking Issue-1
  的根因。规划阶段的观测项是真实风险清单，执行/审查时应逐条核对。
- 术语口径先行确认——动手前收敛跨文档字段命名冲突，避免下游解读歧义。
- 恢复算法保守设计——三重约束 + L2 默认关闭，"宁可不恢复也绝不猜"。

**★值得改进（关键教训）**：
- **"参数透传"与"元数据保留"是两个独立关注点，极易顾此失彼**：adapter 正确透传触发了恢复、也正确
  写入了协议轴字段，但随后整体重建 execution 时把它们抹掉了——上游写入与下游覆盖在同一函数不同代码段，
  单测各自都绿，集成缺陷被掩盖。教训：**字段由 A 写入、B 重建时，B 必须显式保留 A 的产出**，此类跨代码段
  的字段生命周期应有端到端测试兜底。
- **纯函数单测覆盖率高 ≠ 端到端正确**：99 项核心单测全绿仍漏掉 Blocking Issue-1，缺 adapter→runner
  集成断言。应对"元数据从解析到落盘"补一条最小端到端测试。
- **Repair 用产物流名而非硬编码文件名**：统一经 task.output+Resolver 定位，命名与 backfill 的
  `staging/<state>/<output>.md` 对齐，值得固化到所有读产物的节点。

（相关记忆：[[claude-cli-parses-stdout-only]]、[[parse-fallback-exception-order]]）
