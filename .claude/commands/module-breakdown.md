# module-breakdown 命令

模块拆分工作流 `module-breakdown` 的专用命令。承接 Architecture Command 产出的同 lineage Runtime Artifact（final_architecture + data_model），产出 module_breakdown（propagate 同 lineage_id）。

**本命令无 lineage 决策闸门**——lineage 在 Architecture Command 已决定，本命令只 propagate。

## 语法

```text
/module-breakdown [--lineage <lineage_id>] [--seed <run_id>] <goal...>
/module-breakdown continue <run_id> [-f <clarification_file>]
/module-breakdown reject <run_id>
/module-breakdown status <run_id>
/module-breakdown explain <run_id>
```

| 子命令 | 说明 |
|--------|------|
| `<goal>` | 启动模块拆分运行（默认） |
| `continue <run_id> [-f <file>]` | mapping_check 人工审查后批准并继续，可指定裁决文件路径 |
| `reject <run_id>` | 拒绝并结束到 failed |
| `status <run_id>` | 查看运行状态 |
| `explain <run_id>` | 查看当前状态详情 |

| 参数 | 说明 |
|------|------|
| `--lineage <lineage_id>` | 指定 lineage_id，collect 按此聚合同 lineage 的 Runtime Artifact（final_architecture + data_model + 历史 module_breakdown）。如省略，从 goal 文本中推断。 |
| `--seed <run_id>` | 指定上游 requirement-understanding 的 run_id，collect 据此按 artifact_id 定位 Seed Artifact（final_requirement）。用于四列追溯定位表中的需求映射。 |
| `<goal>` | 模块拆分目标描述。如含重点关注领域、分波策略偏好等。 |

## 运行（run）

### 启动前置：collect 生成上下文（不要手写 goal）

CLI `run` 无 `--seed`/`--lineage` 参数（那是命令层概念）。collect 的实际机制是把上游产物
路径+摘要嵌入 goal 文本，由 workflow 节点消费。**先跑 collect.py 生成规范上下文再拼进 goal**，
不要凭记忆手写（手写易漏路径、格式不一致，且 coverage_gate 脚本节点依赖确定性定位）：

```powershell
python scripts/collect.py `
  --lineage '<lineage_id>' --seed '<run_id>:final_requirement'
```

把 collect 输出（含 final_architecture + data_model + final_requirement 的 artifact_id/路径/摘要）
拼进 goal，再启动。

**启动前必查**（手动裸跑踩坑教训）：
- `workflows/module-breakdown/agents.yaml` 必须存在（非 `agents.example.yaml`），否则回退 mock
  空跑（产物是 instruction 原文、`agent=mock duration=0s tokens=0`）。
- agents.yaml 的 `allowed_tools` 必须含 **Edit**（模块拆解产物较长，agent 先 Write 再分块 Edit
  追加；缺 Edit 会因"权限尚未授予"导致节点 blocked）。

### 启动

```powershell
python -m agent_workflow.cli run `
  -w workflows/module-breakdown/workflow.yaml `
  -t '<topic>' `
  -g '<goal>'
```

### 流程概览

```text
collect（含 validate，双查找模式）
  ├─ 按 lineage_id 聚合 Runtime Artifact（final_architecture + data_model + 历史 module_breakdown）
  └─ 按 artifact_id 定位 Seed Artifact（final_requirement）
      ↓
workflow
  decompose → coverage_gate（确定性脚本门：CR/表覆盖+悬空引用）→ mapping_check（二元 Human Gate）⏸
    approve → finalize（应用人工裁决 + 锁定）→ done
    reject  → failed
      ↓
attach
  └─ module_breakdown  ← propagate 同 lineage_id
      ↓
Object Lifecycle Pipeline 收尾（与 attach 同级，纵向轴）
  ① split_and_register --propose  → 拆多文件（slug 自动继承，新模块打 warn）+ 差分 → ledger_proposal.md（type 待填）
  ①' 按 warn 提示给全新模块补 Mxx-<slug>.md 命名（人工，收尾必做）
  ② human gate（人）              → 在 proposal 里填 type/prev（唯一语义决策点）
  ③ split_and_register --apply    → append ledger + --check 阻断门
  ④ 回写 module-registry.md       → 编号 + 模块清单 + 依赖/波次一览（收尾必做，与 ledger 同级）
```

**对齐 requirement-understanding 二元 gate 范式**（2026-07-09 重构）：引擎 human gate 只支持
approve/reject，无 revise 回流。人工审查若需修订，把修订指令写进裁决文件经 `continue --input`
注入，由 finalize 一次性应用后锁定——不再有独立 refine 节点。

**module_breakdown 是 Runtime Artifact**（有 lineage_id），不是 Seed。它 propagate Architecture Command 确定的同 lineage_id。

### 产物

| 产物 | 说明 | Artifact 类型 |
|------|------|---------------|
| `module_breakdown` | 锁定版模块定义——模块清单、依赖DAG、并行波次、四列追溯定位表、关键路径、模块间契约草案、审计追踪 | Runtime |
| `module_breakdown_draft` | 模块拆解草稿（中间产物，不进 lineage） | — |
| `mapping_review` | 覆盖审查意见（中间产物） | — |

### attach：propagate 同 lineage_id

工作流到 `done` 后，给 module_breakdown 盖 frontmatter：

```powershell
python scripts/attach.py `
  --file 'docs/runs/<run_id>/artifacts/module_breakdown.md' `
  --lineage '<lineage_id>' `
  --artifact '<run_id>:module_breakdown'
```

`attach.py` 幂等。lineage_id 与同链的 final_architecture / data_model 一致（propagate，不是新建）。

### Object Lifecycle Pipeline 收尾（演化轴，与 attach 同级）

attach 完成的是**横向 Development Pipeline**（把这次需求做完、盖 lineage_id）。收尾还有**纵向 Object Lifecycle Pipeline**——把本版产出的版本对象串进演化账本 `docs/module-breakdown/ledger.jsonl`。二者正交，缺一条演化轴就断更（详见 `docs/superpowers/plans/2026-07-10-evolution-axis-lost-diagnosis.md`）。

**这一步不是可选的顺手记录，是命令层收尾的确定性环节**——靠 LLM 记得去跑就是 ledger 第一次成孤儿的死法。每次 module-breakdown 锁定后必跑，即便"零差分"也要跑一遍确认。

版本对象 = 模块拆分的自然产物；`module_breakdown` 的每个 `### Mxx` 段 = 一个版本对象。三步（机器①③确定性、人②语义，不塌成一步——撞禁止自动推断演进边的红线）：

```powershell
# ① 确定性拆分 + 差分 → 生成待分类提案（type 一律留 null）
python scripts/split_and_register.py --propose `
  --file 'docs/runs/<run_id>/artifacts/module_breakdown.md' `
  --version '<version>' `
  --lineage '<lineage_id>' [--force]
```

`--propose` 做三件确定性的事，绝不写 ledger、绝不推断边类型：
- 把单文件 `module_breakdown.md` 的每个 `### Mxx` 段拆成 `docs/module-breakdown/<version>/Mxx-<slug>.md`，头部生成机读的 `<!-- lineage: id/status/depends -->` 块（`depends` 从正文"上游依赖"行抽取）；缺省跳过已存在文件，`--force` 覆盖。
  - **命名（slug）自动继承**：修订模块（同 ID 在别的版本目录已存在，如 v1.1 的 `M01` 对应 v1.0 `M01-database-schema.md`）自动继承那个人工起的英文 slug 后缀，产出 `M01-database-schema.md` 而非裸 `M01.md`（中文标题无法自动转 kebab-case，故继承而非翻译）。
  - **全新模块（无处继承）** 用裸编号 `Mxx.md` 命名并打 `[warn]`，提示按既有约定手工重命名为 `Mxx-<english-kebab-slug>.md`（与 v1.0/v1.1 命名一致）——**这是收尾的必做人工步，不可跳过**（裸编号文件难区分，且破坏命名一致性）。重命名不损 lineage 关联（`lineage.py` 靠文件头 `id:` 块关联，非文件名）。
- 跨全部版本目录扫描（v1.0/v1.1/…）当前模块 ID 集合，与 ledger 中 accepted 集合做差分。
- 把"新增候选 / 消失候选"写进 `docs/runs/<run_id>/ledger_proposal.md`，每条 `type: null` 待人填。

> **同 ID 跨版本是正常的**（不是错误）：按 ID 冻结原则，既有模块的后续版本修订**保持原编号**（`M01†` 标记修订），仅全新组件取新号。`v1.0/M01` 与 `v1.1/M01` 是同一模块的两个版本快照，`lineage.py` 按「id + 版本目录」区分、校验不冲突。差分探测按 ID 集合比对，同 ID 跨版本修订不触发「新增候选」（身份延续、无新演进边），无需重登 ledger——只有全新 ID 才需人工分类。

```text
# ② human gate（唯一语义决策点）：在 ledger_proposal.md 的 yaml 块里逐条填 type/prev
#   - 全新对象（无前身）      → 保持 type: null、prev: []
#   - 迭代（旧版不退场、身份延续，可跨 lineage）→ type: iterates-from、prev: [旧ID]、status: accepted
#   - 拆分/合并/取代（源退场） → splits-into / merges-from / supersedes + prev，源另写退场记录
```

跨 lineage 的迭代边在这里判——如 v1.1 治理侧写授权 `M14` 是 v1.0 鉴权 `M02c` 的延续，填 `iterates-from M02c`（M02c 保持 accepted，新旧版并存）。差分探测扫全部版本目录、不按 lineage 切，正是为了让这类候选浮上来供人判定。

```powershell
# ③ 读人工填好的提案 → append ledger → 跑 --check 阻断门
python scripts/split_and_register.py --apply `
  --proposal 'docs/runs/<run_id>/ledger_proposal.md'
```

`--apply` 幂等（跳过同 `id+date+type` 已存在记录），append 后自动跑 `lineage.py:check`——账本与模块文档不一致（幽灵引用/源该退场未退场/迭代边误置源退场等）即退出码 1，这一步失败。

**校验命令**（CI/pre-commit 用，全仓校验账本）：

```powershell
python scripts/lineage.py --check
python scripts/lineage.py --diff        # 单独看差分候选
```

### ④ 回写模块注册表（收尾必做，与 ledger 同级）

`docs/module-breakdown/module-registry.md` 是**全部模块 ID 的单一事实源**（README §3 编号规则）。ledger 记演化边、registry 供人查阅——两者都得更新，缺一个后人查号/查依赖就落空。**每次 module-breakdown 锁定后必回写**，内容：

1. **头部编号**：若本次有新增模块，更新「当前最大编号 → 下一可用编号」；更新 Lineage 总览表对应行的模块范围。
2. **新增本次拆分小节**：一个 `## <version> — <lineage 说明>` 小节，含：
   - **模块清单表**（ID / 名称 / 变更性质「新增 or 修订†」/ 职责摘要 / 复杂度）
   - **职责摘要表**、**写入表归属表**
   - **依赖与并行波次一览表**（ID / 上游依赖 / 波次 / 备注 + 关键路径 + 无环说明）——从 `module_breakdown.md` §2 DAG / §3 波次 / §1 清单表提取，让人**不打开各 Mxx.md 即可看清跨文件依赖全景**（完整 mermaid DAG 留在 module_breakdown.md，registry 只放速查表并注明出处）。
3. **末尾更新时间行**：记本次 run_id 与要点。

> **命名与依赖不能只落在 run 产物里**：`module_breakdown.md`（run 目录）是本次快照，但跨版本查号/查依赖靠的是常驻的 `module-registry.md`。只更新 run 产物、漏回写 registry，等于依赖分析「治标」——下次拆分时无处对照既有模块与依赖，重蹈同 ID 混淆/依赖散落的覆辙。

### frontmatter 契约（重要）

**frontmatter（`artifact_id` / `lineage_id`）由命令层 attach 维护，只盖正式产物 `module_breakdown`。**

- 中间产物（`module_breakdown_draft` / `mapping_review`）**不 attach、不带 frontmatter、不进 lineage**。
- 节点 LLM **禁止**在产物正文输出 YAML frontmatter 或 lineage/artifact_id 标识（已在 workflow instruction 与 skill 约束）。若发现中间产物头部出现 `artifact_id`/`lineage_id` frontmatter（agent 模仿注入产物自造），会污染 collect 的 lineage 聚合——需移除后再 attach 正式产物。

### coverage_gate 的上游文件依赖

`coverage_gate`（`command/mapping_check.py` 确定性脚本节点）需读 `final_requirement` 与 `data_model`：

- **脚本自解析**：本 run 目录找不到时，脚本按 seed artifact_id / lineage_id 全局扫 `docs/runs/*/artifacts/` 定位原文件（不依赖文件被复制到 run 目录）。手动裸跑也能自行定位。
- **门2（表覆盖）依赖 data_model 的 `coverage table` 机读块**：data_model 产物须包含 ```` ```coverage table ```` 块（列 14 张表名，与模块 `covers_table` 写法一致，不带 schema 前缀），否则门2 静默 skip，只跑门1（CR 覆盖）。architecture 工作流产出 data_model 时应带此块。

## Human Gate（mapping_check，二元 approve/reject）

本工作流的 Human Gate 在 `mapping_check` 节点——**不是 lineage 决策**（那已由 Architecture Command 完成），而是**模块拆解语义审查**（架构落点/依赖/波次/边界）。CR/表机械覆盖已由上游 `coverage_gate` 脚本兜住。

工作流在 `mapping_check` 自动暂停（进程正常退出），等待人工通过 `continue`/`reject` 注入决策。

**对齐 requirement-understanding 范式**：引擎 human gate 只支持二元 approve/reject。人工审查若发现需修订的问题，**不走 revise 回流**（引擎不支持，已删 refine 节点），而是把修订指令写进裁决文件，由 `continue --input` 注入、finalize 应用。

### 门暂停后的操作步骤

1. **确认状态**：`/module-breakdown status <run_id>`、`/module-breakdown explain <run_id>`

2. **查看审查产物**：`docs/runs/<run_id>/artifacts/mapping_review.md`（agent 的审查结论 + 裁决文件模板）、`coverage_report.md`（脚本覆盖报告）。

3. **准备人工裁决文件**（默认位置 `docs/runs/<run_id>/human_clarification.md`）：

   ```markdown
   # Human Clarification（模块拆解裁决）

   ## 审查结论
   approve / reject

   ## 修订指令（若 approve 但需修订，逐条列出，finalize 应用）
   - <模块ID>: <具体修订指令>
   ```

4. **批准并继续**：`/module-breakdown continue <run_id>`（或 `-f <自定义路径>`）
   - 等价 `continue --approve --input <file>`；工作流从 finalize 应用裁决 + 锁定 → done。

5. **或拒绝**：`/module-breakdown reject <run_id>`（等价 `continue --reject` → failed）。

### continue / reject 命令详情

```powershell
# 批准（应用裁决文件中的修订指令后锁定）
python -m agent_workflow.cli continue `
  -r <run_id> -w workflows/module-breakdown/workflow.yaml `
  --approve --input <clarification_file>

# 拒绝
python -m agent_workflow.cli continue `
  -r <run_id> -w workflows/module-breakdown/workflow.yaml --reject
```

裁决文件查找优先级：`-f` 指定 → `docs/runs/<run_id>/human_clarification.md`（默认）。文件不存在不自动创建，提示后询问。`reject` 前须向用户确认。

## 状态与诊断

### 查看状态

```text
/module-breakdown status <run_id>
```

等价于：

```powershell
python -m agent_workflow.cli status -r <run_id>
```

### 查看详情

```text
/module-breakdown explain <run_id>
```

等价于：

```powershell
python -m agent_workflow.cli explain -r <run_id>
```

## 目录结构

```
docs/runs/<YYMMDD_<topic>>/
├── artifacts/
│   ├── module_breakdown_draft.md    ← 模块拆解草稿
│   ├── mapping_review.md            ← 覆盖审查意见
│   └── module_breakdown.md          ← 锁定版模块定义（Runtime Artifact，有 lineage_id）
└── workflow_state.json
```

## 与其他命令的关系

```text
/req-understand → final_requirement（Seed Artifact）
        ↓
/architecture → final_architecture + data_model（Runtime，lineage 诞生）
        ↓
/module-breakdown → module_breakdown（Runtime，propagate lineage）
        ↓
spec-dev（以 module_breakdown 的模块定义为 goal，逐波执行）
```

## 执行规则

1. 使用 **PowerShell** 工具执行命令，工作目录为**项目根目录**（当前仓库根，即本 `.claude/` 所在目录）。
2. Python 用 `python`（需先激活项目所用的 conda base 环境，Python 3.11+），不写死绝对路径。
3. 所有参数用单引号包裹，防止特殊字符被 PowerShell 解析。
4. goal 文本较长时优先使用 **Bash** 工具而非 PowerShell，避免中文编码问题。
5. **启动前跑 collect.py 生成上下文再拼 goal**，不手写；并确认 agents.yaml 存在且 `allowed_tools` 含 Edit（见"启动前置"）。
6. **`continue` 前必须检查裁决文件是否存在**；不存在时提示路径和格式，不自动创建。裁决文件默认写入 `docs/runs/<run_id>/human_clarification.md`，不使用项目根目录。
7. **`reject` 前必须向用户确认**（"将拒绝并结束运行，进入 failed 状态，确认？y/n"）。
8. 不要自动执行 `cancel` 或 `retry --dispatch`，除非用户显式确认。
9. 不要读取、打印或外传 `.env`、密钥、数据库凭证等敏感内容。
10. 运行到 `mapping_check` 会自动暂停（进程正常退出），此时向用户呈现审查产物并引导 continue/reject，不要误判为失败。
11. 工作流到 `done` 后执行 attach 给 module_breakdown 盖 lineage_id + artifact_id；**中间产物不 attach、不带 frontmatter**（见"frontmatter 契约"）。
12. 运行结束时，报告最终状态（done / failed）并提示产物路径。
