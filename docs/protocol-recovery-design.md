# 协议层双维度状态 + 分级恢复设计

> 状态：设计草案（Draft），待评审后进入实现。
> 关联：`docs/runtime-v2-design.md`（本设计在其 TaskResult/Validator/Repair 三态模型上做增强，不推翻）。
> 触发案例：`listing-management` run `260714_M17-master-community-service` —— `output_review`（agent=claude-audit/cc-opus）
> 已完成审查、在 result 文本明写"决策 **revise**"并列全 Blocking，但用自然语言收尾、未附 ```json``` TaskResult
> 块，解析失败 → invalid_output → repair 两次耗尽 → failed。判断对了，只因没按协议包 JSON 就整流水线崩。

## 1. 问题陈述

### 1.1 现象
分支节点（尤其大模型跑的审查/评审节点）会"语义判对、但没按协议输出结构化 TaskResult"，
现引擎将其一律降为 `invalid_output`，经 repair 仍失败则终态 `failed`，**一次格式偏差阻塞整条流水线**。

### 1.2 根因：status 单字段塌缩了两个正交维度
现有 `TaskResult.status` 同时承载了两件本质不同的事：

- **任务语义**：这次审查/执行本身成没成（该 approve 还是 revise）。
- **协议形态**：这个结论是"模型直接吐的合规 JSON"，还是"引擎从散文里恢复的"。

二者塌缩进一个字段，导致终态只有二元的「native success ↔ failed」，
中间地带（判对了但协议不合规）无处安放，只能算失败。

### 1.3 设计目标
1. 判对的结论不因协议瑕疵被丢弃 —— 从 **prompt robustness** 抬到 **protocol robustness**。
2. 协议违规不被掩盖 —— 恢复必须留痕、可度量、可归因到具体 agent/skill/workflow。
3. 保守不伪造 —— 只在能无歧义确定语义时恢复；否则维持 invalid_output，绝不猜。
4. 向后兼容、零污染线性节点。

## 2. 核心模型：状态正交拆分

把 status 拆成两个正交轴：

| 轴 | 字段 | 取值 | 语义 |
|---|---|---|---|
| **语义轴**（现有） | `status` | success / failed / blocked / cancelled / timeout / invalid_output | 任务本身成没成 |
| **协议轴**（新增） | `protocol_state` | `native` / `recovered` / `repaired` / `human` | 结构化结果**怎么拿到的** |

### 2.1 成功不再是单点，而是质量分层

五种终态里，前四种协议态都可以是「成功」，只是**成功质量（Quality of Success）**递减：

```
native success    模型直接吐合规 JSON          —— 首选，协议零成本
recovered success 解析器从散文无歧义恢复        —— 引擎兜底，打审计
repaired success  repair 轮把结论重新包装成 JSON —— 中间层，职责单一
human success     人工裁决推进                  —— 最后防线
failed            无法恢复且无人工介入          —— 真失败
```

关键区分：`recovered/repaired/human` **都是成功**（任务语义达成、流水线继续），
它们与 `failed` 的差别是「成功质量」，不是「成功与否」。

### 2.2 protocol_state 与 status 的关系
- `protocol_state` 只对 **status=success** 有区分意义（描述这个 success 怎么来的）。
- status=failed/blocked 时 protocol_state 无意义，固定 `native`（不额外语义）。
- 缺省值 `native` —— 保证所有历史 TaskResult 反序列化后行为不变（见 §7）。

## 3. 分级恢复：Confidence + Method

新增 `RecoveryInfo`，恢复发生时挂在 TaskResult 上（未恢复则为 None）：

```
RecoveryInfo:
  method:            "native" | "regex" | "synonym"   # 恢复手段
  confidence:        float  (0.0 ~ 1.0)               # 可信度
  recovered_fields:  ["decision", ...]                # 恢复了哪些字段
  reason:            str    # "JSON missing; regex decision recovery"
```

### 3.1 首版恢复分级（Level 1 + Level 2）

| 级别 | method | confidence | 触发条件 | 是否恢复 |
|------|--------|-----------|---------|---------|
| **Level 1** | regex | **1.00** | 决策语境窗口内**唯一**命中一个 allowed_decision（如"决策 **revise**"） | ✅ 恢复 |
| **Level 2** | synonym | **0.95** | 决策语境命中受控**同义词表**里的短语（如"建议修改后重新提交"→revise） | ✅ 恢复 |
| （越界） | semantic | <0.95 | 需自由语义推断（如"存在一些问题"猜 revise） | ❌ 不恢复，维持 invalid_output |

### 3.2 Level 1 算法（纯 regex，零推断）
输入 result 文本 + allowed_decisions（如 `[approve, revise, reject, blocked]`）：

1. **决策语境约束**：只在引导词邻近窗口内匹配，避免正文误伤。
   引导词：`决策 / 决定 / 最终决定 / 结论 / 裁决 / 判定 / decision / verdict / conclusion / final decision`。
   窗口：引导词之后约 40 字符（容纳 "：**revise**"、": revise"）。
2. **token 匹配**：allowed_decision 词需作为完整 token 命中（前后非字母，防子串误伤）。
3. **唯一性裁决**：窗口内命中的**不同** decision 恰好为 1 个 → 恢复（confidence=1.0）；
   命中 0 个或 ≥2 个不同 → 不恢复（不猜）。

### 3.3 Level 2 算法（受控同义词表，confidence=0.95）
维护一张**显式、受控**的同义短语 → decision 映射表（禁止自由推断），例如：

```
"建议修改后重新提交" / "打回修订" / "需返工"        → revise
"通过" / "同意进入下一步" / "no blocking"           → approve
"拒绝" / "不予接受" / "驳回"                        → reject
```

同样受 §3.2 的决策语境窗口 + 唯一性裁决约束。命中即 confidence=0.95、method=synonym。

> **灰色地带说明**：Level 2 引入了"短语→决策"的映射，本质是一层受控推断。
> 通过「显式白名单 + 决策语境窗口 + 唯一性裁决」三重约束把推断收窄到可控范围；
> 表由人工维护、每条可审查，不做模型式自由推断。若上线后误恢复率偏高，可退回纯 Level 1。

### 3.4 首版 confidence 的用途：只审计，不路由
**首版 confidence 仅用于审计与统计，不作路由门槛。**
命中 Level 1（1.0）或 Level 2（0.95）都直接放行（protocol_state=recovered）；
未命中/冲突则维持 invalid_output → 走现有 repair。
confidence 照实记录并落审计事件（喂 §4 的 recovery_rate 统计），
但暂不引入"confidence≥阈值才放行"的分段逻辑（见 §8 演进路线）。

## 4. 审计与可观测

### 4.1 新增 ProtocolRecovery 事件
接入 `observability/events.py` 的 EventType + event_registry：

```
ProtocolRecovery {
  state, agent, method, confidence, recovered_fields, reason, timestamp
}
```

恢复发生时由 Runner（或 adapter）发射一条，写入 run 的 events.jsonl。

### 4.2 可导出的统计（长期价值）
基于 ProtocolRecovery 事件流离线聚合出「协议遵循度」画像：

```
Agent=claude-opus    recovery_rate=12%
Agent=deepseek       recovery_rate=1%
Workflow=spec-dev    recovery_rate=8%
Skill=code-audit     recovery_rate=15%
```

**这是本设计的长期价值**：recovery_rate 直接指向"谁的协议遵循差"——
高的 agent/skill 就是该优化 prompt/repair/模型配置的地方，
不再靠人肉翻日志猜。native/recovered/repaired/human 的分布本身就是流水线健康度指标。

## 5. Repair 瘦身为纯格式转换

现状 `_build_repair_agent_input` 开新 session、prompt 只说"改 status/decision"、
不喂已落盘 output 文档 → 新 session 无审查上下文，退化成"重新执行任务"，既贵又不稳。

改法：Repair 变**协议转换器**，不重新审查：
- 喂回本 state 已落盘的 output 产物正文（从 staging 读，截断到合理长度）；
- 喂回最后一条 assistant 原话（从 debug packet 读）；
- 指令明确"你不需要重审，只把上述结论包装成合法 TaskResult JSON，最后一条消息只输出 ```json``` 块"。
- 读文件失败/缺失时退化为现状精简 prompt（不因 IO 异常崩）。
- Repair 成功 → protocol_state=repaired。它现在是"自动恢复失败后、上升到人工前"的中间层。
- Repair 仍走完整 Parser+Validator（含分级恢复）→ 双保险：即便 repair 又用散文，恢复层也能兜。

## 6. 落点清单（全部在引擎 `G:\agent-workflow`）

| 文件 | 改动 | 风险 |
|---|---|---|
| `tasks/result.py` | +`protocol_state`（默认 native）+`RecoveryInfo` 字段 + to_dict/from_dict | 低（向后兼容） |
| `agents/_parse.py` | 新增 `_recover_decision_from_prose`（Level1 regex + Level2 同义词表，返回 RecoveryInfo）；`_parse_task_result_text` 增可选 `allowed_decisions` 参数（默认 None 行为不变） | 中 |
| `agents/claude_cli.py` / `codex_cli.py` | 从 skill_policy 透传 `allowed_decisions`；恢复命中时并入 RecoveryInfo + 发 ProtocolRecovery 事件 | 低 |
| `validators/validation_result.py` | `ValidResult` +`recovery` 字段（承载 method/confidence，供 Runner 读） | 低 |
| `state_machine/runner.py` | recovered 结果直接放行 + protocol_state 落 workflow_state；Repair 瘦身（读文档+最后消息，格式转换 prompt） | **中高**（核心编排，改动最谨慎） |
| `observability/events.py` | +`ProtocolRecovery` 事件类型 + registry 条目 | 低 |
| `tests/unit/` | parser 分级恢复（L1/L2/冲突/线性节点不恢复/JSON 优先）、repair 格式转换、事件发射、向后兼容回归 | — |

## 7. 向后兼容

- `protocol_state` 缺省 `native`、`recovery` 缺省 None → 历史 run 反序列化行为不变。
- 分级恢复**只对有 allowed_decisions 的分支节点启用**；线性节点（planning/execution 等）传 None，
  解析行为与现状完全一致，零污染。
- 恢复只在"常规 JSON 解析已全部失败"后才介入，native 路径完全不变。
- `_parse_task_result_text` 新增参数带默认值，所有旧调用点无需改即兼容。

## 8. 演进路线（明确的第二阶段，本次不做）

### 8.1 Confidence 阈值路由（Phase 2）
首版 confidence 只审计。Phase 2 引入阈值分段路由：
```
confidence >= AUTO_THRESHOLD(默认 0.95)  → 自动放行
0 < confidence < AUTO_THRESHOLD           → 进入 Repair（带上下文）
恢复失败/耗尽                              → NEED-HUMAN（protocol_state=human 待定）
```
阈值设为可配置常量，不写死。届时 runner.py 引入分段编排（改动比首版大，故拆到 Phase 2）。

### 8.2 L4「模型只吐语义、程序拼协议」评估
**方向合理，但不应并入本次改造，建议作为 runtime-v3 单独立项。**

- **本质**：L4 是把本设计推到极端——不再要求模型吐 JSON，agent 只输出 `Decision: revise` + issues 清单，
  TaskResult 完全由程序构造。本设计（1.5）是渐进增强（native 仍首选、恢复是兜底），
  L4 是协议翻转（散文成为主协议）。
- **成本**：L4 要重写 `build_prompt` 的输出契约、所有 adapter 的解析主路径、mock agent、
  以及**每个 workflow 的每一个 skill 契约**，是 runtime-v3 级别的协议版本升级，
  牵动 `runtime-v2-design.md` 定义的整个 TaskResult 契约，粗估工作量为本设计的 5~8 倍，回归面大。
- **收益重叠**：L4 想解决的"模型总漏 JSON"，本设计的 recovered success + recovery_rate 已消化大部分——
  既不阻塞流水线，又能定位到具体 agent。L4 多出的边际收益（彻底免除 JSON 要求）有限。
- **建议**：用本设计（1.5）运行一段时间、收集 recovery_rate 数据后再评估 L4：
  - 若某类 agent recovery_rate 长期居高（如 opus 稳定 >30%）→ L4 有实证依据；
  - 若 recovery 多为 Level 1（confidence=1.0）且占比低 → 现协议够用，L4 无必要。
  **让数据决定，而非现在拍脑袋重构。**

## 9. 验收标准（实现阶段用）

- 单测：Level 1 唯一命中恢复；Level 2 同义词命中恢复；冲突/无命中不恢复；
  线性节点（不传 allowed_decisions）不恢复；有合法 JSON 时结构化路径优先于恢复；
  repair 在有 output 文档时生成格式转换 prompt、文档缺失时退化不抛异常；
  ProtocolRecovery 事件字段完整；老 TaskResult 反序列化后 protocol_state=native。
- 全量 `pytest -q` 无回归。
- 端到端：对 M17 run retry，观察 output_review 是否稳定路由（无论 opus 是否附 JSON），
  且 recovered 时 events.jsonl 有 ProtocolRecovery 记录、workflow_state 有 protocol_state。
