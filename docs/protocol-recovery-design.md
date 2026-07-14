# 协议层双维度状态 + 分级恢复设计

> 状态：设计草案（Draft，已纳入评审反馈 v2），待评审后进入实现。
> 关联：`docs/runtime-v2-design.md`（本设计在其 TaskResult/Validator/Repair 三态模型上做增强，不推翻）。
>
> **评审反馈 v2 采纳记录：**
> 1. `protocol_state` 更名 `protocol_origin`（`native/parser/repair/human`），统一"结果来源"维度（§2）。
> 2. `protocol_origin` + `RecoveryInfo` 从 TaskResult 移入 `ExecutionMetadata`，业务对象不承载运行时信息（§2.3 / §3 / §6）。
> 3. Level 2 同义词恢复默认关闭，作为 Feature Flag；首版只上 Level 1（§3.1）。
> 4. Repair 只通过 `task.output` + Artifact Resolver 取本节点产物，禁止硬编码任何文件名/workflow 语义词（§5）。
> 5. ProtocolRecovery 事件记 `origin_text_hash`（+offset）而非原文正文（§4.1）。
> 6. RecoveryRegistry 插件化列为演进项，首版不做（§8.3）。
> 触发案例：`listing-management` run `260714_M17-master-community-service` —— `output_review`（agent=claude-audit/cc-opus）
> 已完成审查、在 result 文本明写"决策 **revise**"并列全 Blocking，但用自然语言收尾、未附 ```json``` TaskResult
> 块，解析失败 → invalid_output → repair 两次耗尽 → failed。判断对了，只因没按协议包 JSON 就整流水线崩。

## 1. 问题陈述

### 1.1 现象
分支节点（尤其大模型跑的审查/评审节点）会"语义判对、但没按协议输出结构化 TaskResult"，
现引擎将其一律降为 `invalid_output`，经 repair 仍失败则终态 `failed`，**一次格式偏差阻塞整条流水线**。

### 1.2 根因：status 单字段塌缩了两个正交维度
现有 `TaskResult.status` 同时承载了两件本质不同的事：

- **任务语义**（业务）：这次审查/执行本身成没成（该 approve 还是 revise）。
- **结果来源**（运行时）：这个结论是"模型直接吐的合规 JSON"，还是"引擎从散文里恢复的"。

二者塌缩进一个字段，导致终态只有二元的「native success ↔ failed」，
中间地带（判对了但协议不合规）无处安放，只能算失败。
注意这两维分属不同层：任务语义是**业务结果**（属 TaskResult），
结果来源是**运行时事项**（属 ExecutionMetadata，见 §2.3）。

### 1.3 设计目标
1. 判对的结论不因协议瑕疵被丢弃 —— 从 **prompt robustness** 抬到 **protocol robustness**。
2. 协议违规不被掩盖 —— 恢复必须留痕、可度量、可归因到具体 agent/skill/workflow。
3. 保守不伪造 —— 只在能无歧义确定语义时恢复；否则维持 invalid_output，绝不猜。
4. 向后兼容、零污染线性节点。

## 2. 核心模型：状态正交拆分

把 status 拆成两个正交轴，且**分属不同对象**：

| 轴 | 所属对象 | 字段 | 取值 | 语义 |
|---|---|---|---|---|
| **语义轴**（现有） | `TaskResult` | `status` | success / failed / blocked / cancelled / timeout / invalid_output | 任务本身成没成（业务） |
| **来源轴**（新增） | `ExecutionMetadata` | `protocol_origin` | `native` / `parser` / `repair` / `human` | 这条 TaskResult 最终由哪个**环节**定型（运行时） |

> **命名说明（反馈①）**：不叫 `protocol_state`，因为 `native`/`human` 表来源、`repaired` 表处理过程，
> 混了两个维度。改用 `protocol_origin`，四值统一回答同一个问题——"结果最终来自哪里"：
> `native`（模型直接产出）/ `parser`（解析器从散文恢复）/ `repair`（repair 轮重新包装）/ `human`（人工裁决）。
> 未来扩展 `cache` / `checkpoint` / `resume` / `api` 也能自洽落在同一轴上。

### 2.1 成功不再是单点，而是质量分层

五种来源里，前四种都可以是「成功」，只是**成功质量（Quality of Success）**递减：

```
native  success  模型直接吐合规 JSON          —— 首选，协议零成本
parser  success  解析器从散文无歧义恢复        —— 引擎兜底，打审计
repair  success  repair 轮把结论重新包装成 JSON —— 中间层，职责单一
human   success  人工裁决推进                  —— 最后防线
failed           无法恢复且无人工介入          —— 真失败
```

关键区分：`parser/repair/human` 来源 **都是成功**（任务语义达成、流水线继续），
它们与 `failed` 的差别是「成功质量」，不是「成功与否」。

### 2.2 protocol_origin 与 status 的关系
- `protocol_origin` 只对 **status=success** 有区分意义（描述这个 success 由哪个环节定型）。
- status=failed/blocked 时 protocol_origin 无意义，固定 `native`（不额外语义）。
- 缺省值 `native` —— 保证所有历史 TaskResult 反序列化后行为不变（见 §7）。

**归属裁决规则（反馈①遗留歧义）**：因 §5 规定 Repair 轮仍走完整 Parser+恢复层，
若结果是"在 repair 轮里由 regex 恢复出来的"，`protocol_origin` 取**最后定型的责任环节 = `repair`**
（而非 `parser`）。即：只有在**首次** parse 阶段由恢复层产出的才记 `parser`；进入 repair 流程后产出的一律记 `repair`。

### 2.3 为什么挂在 ExecutionMetadata 而非 TaskResult（反馈②③）
TaskResult 是**业务契约**（decision / status / summary / artifacts / issues），回答"任务结果是什么"。
"这条结果是怎么被恢复/修复出来的"是**运行时事项**，与业务语义正交。若把 `protocol_origin`、`RecoveryInfo`
直接挂在 TaskResult 顶层，业务对象会随运行时关注点持续膨胀（下一步就是 repair_count、resume 标记……）。

`ExecutionMetadata` 本就承载 pid / exit_code / duration / attempt 这类运行时元数据，是这些字段的天然归宿：

```
WorkflowState
  └─ TaskResult              # 业务：decision / status / summary / artifacts / issues
       └─ ExecutionMetadata  # 运行时：started_at / finished_at / attempt / exit_code / pid
            ├─ protocol_origin   (新增)
            └─ recovery          (新增，RecoveryInfo，未恢复则 None)
```

Runner 读取路径相应变为 `task_result.get_execution().protocol_origin` / `.recovery`。

## 3. 分级恢复：Confidence + Method

新增 `RecoveryInfo`，恢复发生时挂在 **ExecutionMetadata** 上（未恢复则为 None，见 §2.3）：

```
RecoveryInfo:
  method:            "regex" | "synonym"   # 恢复手段（native 不产生 RecoveryInfo）
  confidence:        float  (0.0 ~ 1.0)     # 可信度
  recovered_fields:  ["decision", ...]      # 恢复了哪些字段
  reason:            str    # "JSON missing; regex decision recovery"
```

### 3.1 首版恢复分级（首版只上 Level 1；Level 2 默认关闭）

| 级别 | method | confidence | 触发条件 | 首版是否启用 |
|------|--------|-----------|---------|---------|
| **Level 1** | regex | **1.00** | 决策语境窗口内**唯一**命中一个 allowed_decision（如"决策 **revise**"） | ✅ 默认 ON |
| **Level 2** | synonym | **0.95** | 决策语境命中受控**同义词表**里的短语（如"建议修改后重新提交"→revise） | ⛔ 默认 OFF（Feature Flag） |
| （越界） | semantic | <0.95 | 需自由语义推断（如"存在一些问题"猜 revise） | ❌ 永不恢复，维持 invalid_output |

> **Level 2 默认关闭（反馈④）**：Level 2 的同义词表是一层受控推断，风险在于**长期维护会膨胀**——
> "建议修改/建议继续完善/建议补充论证……" 越加越多，最后退化成 prompt 式的 `if contains` 逻辑，
> 这是维护性灾难前兆。故首版策略：
> - **Level 1（确定性 regex，confidence=1.0）默认开启**，已覆盖触发案例（M17 的"决策 **revise**"即 L1 唯一命中）。
> - **Level 2 首版只留接口位、同义词表先不填**，由 `workflow.yaml` 的 `enable_synonym_recovery=true`（默认 false）开启。
>   等 §4 的 `recovery_rate` 数据证明确有 L2 需求（某类 agent 大量卡在"语义判对但非 L1 命中"）再填表启用，风险最低。

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
命中 Level 1（1.0，首版唯一启用）直接放行（protocol_origin=parser）；
未命中/冲突则维持 invalid_output → 走现有 repair。
confidence 照实记录并落审计事件（喂 §4 的 recovery_rate 统计），
但暂不引入"confidence≥阈值才放行"的分段逻辑（见 §8 演进路线）。

## 4. 审计与可观测

### 4.1 新增 ProtocolRecovery 事件
接入 `observability/events.py` 的 EventType + event_registry：

```
ProtocolRecovery {
  state, agent, method, confidence, recovered_fields, reason, timestamp,
  origin_text_hash,   # SHA256(assistant 原文)，不存正文
  origin_text_offset  # 命中窗口在原文中的字符偏移（可选，便于事后定位）
}
```

恢复发生时由 Runner（或 adapter）发射一条，写入 run 的 events.jsonl。

> **只记 hash 不记正文（反馈⑦）**：assistant 原文可能几十 KB，直接塞进事件会让 events.jsonl / audit 迅速膨胀。
> 只存 `SHA256(原文)` + 命中偏移量：既能在 debug packet 里反查是哪段原文触发了恢复，又不撑爆审计流。

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
- Repair 成功 → protocol_origin=repair。它现在是"自动恢复失败后、上升到人工前"的中间层。
- Repair 仍走完整 Parser+Validator（含分级恢复）→ 双保险：即便 repair 又用散文，恢复层也能兜
  （此时 origin 记 `repair`，见 §2.2 归属裁决）。

### 5.1 硬约束：Repair 不认识任何具体产物文件名（反馈⑤）
"喂回本 state 已落盘的 output 产物正文"**只能**通过 `task.output` 声明的产物流名 + Artifact Resolver 获取，
**禁止**在 `runner.py` / repair 逻辑里出现任何具体文件名（如 `output_review_doc.md`）或 workflow 语义词
（如 CodeAudit / Planning / Architecture）。否则 Runtime 就"认识"了具体 Workflow，构成职责反转：

```
Repair ──task.output──▶ Artifact Resolver ──▶ 返回本节点产物正文（Review / Plan / 任意 markdown）
         （只知道产物流名，不知道文件叫什么、属于哪种审查）
```

> 这条不是推翻设计稿——§5 原文本就是"本 state 的 output 产物"，现有 `_build_repair_agent_input`
> 也已通过 `original_agent_input.task.output` 传产物流名。此处只是把它**固化为实现纪律**，
> 防止实现阶段图省事退化成硬编码文件名。验收单（§9）据此加一条静态检查。

## 6. 落点清单（全部在引擎 `G:\agent-workflow`）

| 文件 | 改动 | 风险 |
|---|---|---|
| `tasks/result.py` | 在 **`ExecutionMetadata`** 上 +`protocol_origin`（默认 native）+`recovery`（RecoveryInfo，默认 None）+ to_dict/from_dict；处理旧数据反序列化未知字段兼容 | 低（向后兼容） |
| `agents/_parse.py` | 新增 `_recover_decision_from_prose`（首版仅 Level1 regex，返回 RecoveryInfo；Level2 留接口位、Flag 关闭时不触发）；`_parse_task_result_text` 增可选 `allowed_decisions` 参数（默认 None 行为不变） | 中 |
| `agents/claude_cli.py` / `codex_cli.py` | 从 skill_policy 透传 `allowed_decisions`；恢复命中时把 RecoveryInfo/origin 写入 `execution` + 发 ProtocolRecovery 事件（含 origin_text_hash） | 低 |
| `config/*` | +`enable_synonym_recovery`（workflow 级，默认 false）配置项 | 低 |
| `validators/validation_result.py` | `ValidResult` +`recovery` 字段（承载 method/confidence，供 Runner 读） | 低 |
| `state_machine/runner.py` | parser 恢复结果直接放行 + `protocol_origin` 经 execution 落 workflow_state；Repair 瘦身（经 `task.output`+Resolver 取产物，**禁硬编码文件名**，格式转换 prompt）；repair 内恢复 origin 记 `repair` | **中高**（核心编排，改动最谨慎） |
| `observability/events.py` | +`ProtocolRecovery` 事件类型（含 origin_text_hash/offset）+ registry 条目 | 低 |
| `tests/unit/` | parser L1 恢复/冲突/线性节点不恢复/JSON 优先、L2 Flag 关闭不触发、repair 格式转换、repair 无硬编码文件名静态检查、事件字段（含 hash）、向后兼容回归（旧 execution 反序列化 origin=native） | — |

## 7. 向后兼容

- `ExecutionMetadata.protocol_origin` 缺省 `native`、`recovery` 缺省 None → 历史 run 反序列化行为不变。
  （注意：`ExecutionMetadata` 当前用 `ExecutionMetadata(**exec_data)` 构造，新增字段须保证旧 dict 缺字段时取默认值，
  且对旧数据可能不含的键不抛 `TypeError`。）
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
恢复失败/耗尽                              → NEED-HUMAN（protocol_origin=human 待定）
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
- **收益重叠**：L4 想解决的"模型总漏 JSON"，本设计的 parser success + recovery_rate 已消化大部分——
  既不阻塞流水线，又能定位到具体 agent。L4 多出的边际收益（彻底免除 JSON 要求）有限。
- **建议**：用本设计（1.5）运行一段时间、收集 recovery_rate 数据后再评估 L4：
  - 若某类 agent recovery_rate 长期居高（如 opus 稳定 >30%）→ L4 有实证依据；
  - 若 recovery 多为 Level 1（confidence=1.0）且占比低 → 现协议够用，L4 无必要。
  **让数据决定，而非现在拍脑袋重构。**

> **命名（反馈⑧）**：L4 不宜叫"Runtime v3"——它真正改动的是 `build_prompt` 输出契约、各 adapter 解析主路径、
> 以及每个 skill 契约，Runner 编排几乎不动。**本质是 Agent 契约升级，建议立项名为 `Agent Contract v2`**。
> （纯命名归类，对本次首版开发零影响。）

### 8.3 RecoveryRegistry 插件化（演进项，首版不做）（反馈⑥）
反馈建议把恢复器做成 `RecoveryRegistry`（JSONRecovery / DecisionRecovery / YamlRecovery / MarkdownRecovery，
parser 遍历 registry 逐个尝试）。**方向正确，但首版不做**：

- 首版只有**一种**恢复器（regex 决策恢复），JSON/YAML/Markdown 恢复都还不存在。
  为想象中的扩展点先建 registry 框架是过早抽象，凭空加一层间接。
- 首版把恢复逻辑写成**签名清晰的纯函数**（`_recover_decision_from_prose`）即可。
- **触发条件**：当出现第二种恢复器时，再从该函数抽出 `RecoveryRegistry`——届时重构成本很低，且需求已明确。

## 9. 验收标准（实现阶段用）

- 单测：Level 1 唯一命中恢复（origin=parser）；冲突/无命中不恢复；
  **Level 2 在 `enable_synonym_recovery=false`（默认）时不触发**；
  线性节点（不传 allowed_decisions）不恢复；有合法 JSON 时结构化路径优先于恢复；
  repair 在有 output 产物时生成格式转换 prompt、产物缺失时退化不抛异常；
  **repair 逻辑无硬编码文件名/workflow 语义词（静态检查或 grep 断言）**；
  ProtocolRecovery 事件字段完整（含 `origin_text_hash`）；
  老数据反序列化后 `ExecutionMetadata.protocol_origin=native`、`recovery=None`。
- 全量 `pytest -q` 无回归。
- 端到端：对 M17 run retry，观察 output_review 是否稳定路由（无论 opus 是否附 JSON），
  且恢复时 events.jsonl 有 ProtocolRecovery 记录、workflow_state 中 execution 有 `protocol_origin`。
