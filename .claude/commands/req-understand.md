# req-understand 命令

四层需求规范化工作流 `requirement-understanding` 的专用命令。Build Baseline → 多模型 Semantic Resolution 提议 → 异源校验 → 合并 → 澄清 → Single Semantic Authority 人工裁决 → Apply(Resolution) 投影 Canonical Requirement → Coverage Check 脚本门。

本工作流不做方案设计，不推荐技术路线。Requirement 是 View 不是终点：SoT 为 `baseline_requirement_set + resolution`，`final_requirement` 是其确定性投影。产物 `final_requirement` 可作为后续 `system-architecture` 工作流的 Seed Artifact。

## 语法

```text
/req-understand [-t <topic>] <goal...>
/req-understand continue <run_id> [-f <clarification_file>]
/req-understand reject <run_id>
/req-understand validate
/req-understand status <run_id>
/req-understand explain <run_id>
```

| 子命令 | 说明 |
|--------|------|
| `<goal>` | 启动需求理解运行（默认） |
| `continue <run_id> [-f <file>]` | 人工澄清后批准并继续，可指定澄清文件路径 |
| `reject <run_id>` | 拒绝并结束到 failed |
| `validate` | 校验工作流配置 |
| `status <run_id>` | 查看运行状态 |
| `explain <run_id>` | 查看当前状态详情 |

| 参数 | 说明 |
|------|------|
| `-t <topic>` | 可选 topic，用于产物目录命名。**不要带日期前缀**，引擎会自动追加（如 `-t data-access-v1`，产物落 `docs/runs/260617_data-access-v1/`） |
| `-f <file>` | `continue` 时指定人工澄清文件路径。省略时默认读取 `docs/runs/<run_id>/human_clarification.md` |
| `<goal>` | 产品/运营需求描述 |

示例：

```text
/req-understand 运营人员需要一个活动数据看板
/req-understand -t data-access-v1 请根据 docs/requirement/v1.0/ 完成需求分析
/req-understand continue 260617_data-access-v1
/req-understand continue 260617_data-access-v1 -f docs/my_clarification.md
/req-understand reject 260617_data-access-v1
/req-understand validate
/req-understand status 260617_data-access-v1
```

## 参数解析规则

1. 第一个非选项 token 识别为 `mode`：`continue` / `reject` / `validate` / `status` / `explain`。
2. 默认模式（不匹配以上关键字）→ `run` 模式。
3. 若出现 `-t <值>`，提取为 `--topic`，不归入 goal。**值不要带日期前缀**（引擎会在前面自动追加 `YYMMDD_`）。
4. `continue` 模式下若出现 `-f <值>`，提取为澄清文件路径（绝对路径或相对 `F:\listing-management` 的路径）。
5. `run` 模式下，剩余 token 拼接为 goal。
6. 缺少 goal 或 run_id 时向用户提问补齐。

## 运行（run）

### 启动

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli run `
  -w workflows/requirement-understanding/workflow.yaml `
  -t '<topic>' `
  -g '<goal>'
```

`-t <topic>` 约定：
- 引擎自动在 topic 前追加 `YYMMDD_` 前缀，产物目录为 `docs/runs/YYMMDD_<topic>/`
- **不要在 `-t` 值中手动加日期**，否则会出现 `260617_260617_data-access-v1` 这样的双重日期
- 省略时引擎自动生成，但与 goal 关联性弱，建议始终指定有意义的 topic

### 流程概览

```text
build_baseline（Layer1）→ resolve_deepseek → resolve_claude → resolve_codex
  → review_by_claude → review_by_codex → review_by_deepseek
  → combine_resolution → generate_clarification_questions
  → human_semantic_gate ⏸ 暂停，等待人工裁决（Single Semantic Authority）
  → canonicalize（Layer3 Apply(Resolution)）→ coverage_check（Layer4 脚本门）
  → done
```

工作流在 `human_semantic_gate` 自动暂停，不会自动通过。用户必须审阅 Resolution 提议（尤其 Divergent/Derived Relations）和澄清问题后，通过 `continue` 或 `reject` 决定下一步。

**Coverage Check 是确定性脚本节点（非 LLM agent）**：`coverage_check.py` 经 Resolution 解析后比对 baseline 与 canonical，存在未追溯项则退出码 1 → 工作流失败。只证 Canonicalization Recall（没丢 baseline 已发现的），不证 PRD 抽全。

### goal 编写建议

goal 需包含足够信息供三个模型独立理解。建议包含：
- 需求文档的引用路径
- 项目背景和建设目标摘要
- 目标用户和核心范围

如果 goal 文本较长（超过数百字），优先使用 **Bash** 工具（而非 PowerShell）执行 CLI 命令，避免 PowerShell 对中文长文本的编码问题。或者将 goal 写入临时 UTF-8 文件，通过 `Get-Content -Raw` 读取后传递。

## 人工语义裁决门（human_semantic_gate）

本门是 **Single Semantic Authority**——工作流唯一的人类语义裁决点。它做两件事：(a) 呈现澄清问题等待回答；(b) 呈现 `resolution` 提议（尤其 Divergent / Derived Relations）+ Merge Evidence，请用户审批"被提议合并/归一/派生的条目对不对"。**派生关系（derived-from）Coverage 兜不住，必须逐条人工签字。**

### 门暂停后的操作步骤

1. **确认状态**：

```text
/req-understand status <run_id>
/req-understand explain <run_id>
```

2. **查看产物**：产物位于 `docs/runs/<run_id>/artifacts/` 下，主要关注：

| 产物 | 文件 | 作用 |
|------|------|------|
| `baseline_requirement_set` | `artifacts/baseline_requirement_set.md` | Layer1 best-effort 抽取的需求条目全集（Recall 分母，非 Ground Truth） |
| `resolution` | `artifacts/resolution.md` | 待批准的语义等价集（核心资产）：Agreed / Divergent / Derived Relations + Merge Evidence |
| `clarification_questions` | `artifacts/clarification_questions.md` | 面向用户的澄清问题 |
| `human_clarification_request` | `artifacts/human_clarification_request.md` | 裁决请求汇总（澄清问题 + 待批准 Resolution 提议 + 建议回答/裁决格式） |

3. **准备人工裁决文件**：参照 `human_clarification_request.md` 末尾的格式，创建裁决文件。它需同时包含**澄清回答**与**对 Resolution 提议的批准/否决**。**默认位置**：

```
docs/runs/<run_id>/human_clarification.md
```

若需放在其他位置，创建后通过 `-f` 参数指定：

```markdown
# Human Clarification

## Blocking Questions

BQ-1：
BQ-2：
...

## Resolution Decisions

# 对每条 Divergent / Derived Relation 批准或否决
DR-1（derived-from，必须签字）：approve / reject —— 理由
DR-2：approve / reject
...

## Optional Questions

OQ-1：
...
```

4. **批准并继续**：

```text
/req-understand continue <run_id>
```

或指定自定义文件路径：

```text
/req-understand continue <run_id> -f docs/my_clarification.md
```

命令会：
- 按以下优先级查找 `human_clarification.md`：
  1. `-f` 参数指定的路径（若提供）
  2. `docs/runs/<run_id>/human_clarification.md`（默认）
- 若文件不存在，提示路径和格式，并展示 `clarification_questions` 与 `resolution` 的位置
- 执行 `continue --approve --input <file>`
- 工作流从 `canonicalize`（Layer3 Apply(Resolution)）继续，再经 `coverage_check`（Layer4 脚本门）到 `done`

5. **或拒绝并结束**：

```text
/req-understand reject <run_id>
```

执行 `continue --reject`，工作流直接进入 `failed` 终止。

### continue 命令详情

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli continue `
  -r <run_id> `
  -w workflows/requirement-understanding/workflow.yaml `
  --approve `
  --input <clarification_file>
```

执行前检查：
- `docs/runs/<run_id>/` 目录是否存在（run 是否有效）
- 澄清文件是否存在（按上述优先级查找）
- 若文件不存在，不自动创建，提示用户后询问是否继续

`--input` 参数接受相对于 `F:\listing-management` 的路径。

### reject 命令

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli continue `
  -r <run_id> `
  -w workflows/requirement-understanding/workflow.yaml `
  --reject
```

执行前确认："将拒绝并结束运行 `<run_id>`，工作流将进入 failed 状态。确认？输入 y/yes 继续，n/no 取消。"

## 校验（validate）

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli validate-config -w workflows/requirement-understanding/workflow.yaml
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli validate-state-machine -w workflows/requirement-understanding/workflow.yaml
```

## 状态与诊断

### 查看状态

```text
/req-understand status <run_id>
```

等价于：

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli status -r <run_id>
```

### 查看详情

```text
/req-understand explain <run_id>
```

等价于：

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli explain -r <run_id>
```

适用于 gate 暂停后了解当前状态和下一步操作。

## 目录结构

一次完整运行的产物布局：

```
docs/runs/<YYMMDD_<topic>>/
├── artifacts/
│   ├── baseline_requirement_set.md        ← Layer1 抽取的需求条目全集（Recall 分母）
│   ├── resolution_proposal_deepseek.md    ← Layer2 三模型独立 Resolution 提议
│   ├── resolution_proposal_claude.md
│   ├── resolution_proposal_codex.md
│   ├── review_claude.md                    ← 异源盲区校验
│   ├── review_codex.md
│   ├── review_deepseek.md
│   ├── resolution.md                       ← Layer2 合并后的待批准 Resolution（核心资产）
│   ├── clarification_questions.md
│   ├── human_clarification_request.md
│   ├── final_requirement.md               ← Layer3 Canonical Requirement（最终产物）
│   └── coverage_report.md                  ← Layer4 覆盖报告
├── human_clarification.md                 ← 用户编写的裁决文件（默认位置）
└── workflow_state.json                    ← 内部状态
```

## 完成后的产物

| 产物 | 说明 |
|------|------|
| `final_requirement` | Layer3 Apply(Resolution) 投影出的 Canonical Requirement（最终产物）：用户目标、角色、场景流程规则、功能清单（含 ID）、范围边界、验收标准、已确认关系、未确认分歧、Evidence Index。**不含实现进度标记。** |
| `resolution` | Layer2 合并后经人审批准的语义等价集（核心资产，SoT 之一） |
| `baseline_requirement_set` | Layer1 抽取的需求条目全集（Canonicalization Recall 分母，SoT 之一） |
| `coverage_report` | Layer4 覆盖报告，证明 Canonicalization Recall（未追溯即工作流失败） |

**Requirement 是 View 不是终点**：SoT 为 `baseline_requirement_set + resolution`，`final_requirement` 是其确定性投影，可被重放与审计。

`final_requirement` 可直接作为：
- 后续 `system-architecture` 工作流的 Seed Artifact 输入
- `spec-dev` 工作流的需求输入

### produce：盖 Seed Artifact 的 artifact_id

工作流到 `done`（`coverage_check` 通过）后，需求跳的收尾动作是 **produce**——给 `final_requirement.md` 盖上 Seed Artifact 的 `artifact_id`（**仅 `artifact_id`，不盖 `lineage_id`**——lineage 从架构跳才开始，见 R13）：

```powershell
C:/Users/12108/miniconda3/python.exe scripts/attach.py `
  --file 'docs/runs/<run_id>/artifacts/final_requirement.md' `
  --artifact '<run_id>:final_requirement'
```

`attach.py` 幂等：重复盖同一 `artifact_id` 不产生重复条目；不传 `--lineage` 时自动识别为 Seed Artifact。这一步是 Runtime Protocol `collect → workflow → attach` 中需求首跳的 produce 变体（末尾是 `produce` 而非 `attach`，因为 lineage 决策尚未发生）。产出的 Seed Artifact 之后被 Architecture Command 的 collect 按 `artifact_id` 定位并注入。

## 边界约束

命令层面遵循工作流的边界：
- 只做需求理解，不输出技术选型、架构建议或实现计划
- 共识度只是中间指标，不代表需求自动通过
- 分歧项必须由用户澄清或保留为未确认事项
- 如果 `continue` 时未提供 `human_clarification.md`，`final_requirement` 中的未确认事项会保留为待确认问题

## 与其他命令的关系

```text
PRD → /req-understand → final_requirement（Seed Artifact，仅 artifact_id，无 lineage_id）
                              ↓
        Architecture Command（lineage 从架构跳开始）→ system-architecture
                              ↓
        Module Breakdown Command → module-breakdown
                              ↓
        spec-dev（以模块定义为 goal，需求驱动开发含测试）

/agent-workflow requirement-understanding <goal>  → 等效于 /req-understand 的 run 模式（但缺少 continue/reject 的引导）
```

> lineage 从架构跳开始，需求跳产出的是 **Seed Artifact**（仅 `artifact_id`，无 `lineage_id`）。详见 `docs/superpowers/plans/2026-07-06-lineage-command-runtime.md`。

## 执行规则

1. 使用 **PowerShell** 工具执行命令，工作目录固定为 `F:\listing-management`。
2. Python 固定路径：`C:/Users/12108/miniconda3/python.exe`。
3. 所有 goal / topic / run_id / file 参数用单引号包裹，防止特殊字符被 PowerShell 解析。
4. `--dangerouslyDisableSandbox`、`bypassPermissions` 等危险权限默认不加。
5. 不要自动执行 `cancel` 或 `retry --dispatch`，除非用户显式确认。
6. 不要读取、打印或外传 `.env`、密钥、数据库凭证等敏感内容。
7. `continue` 前必须检查澄清文件是否存在；不存在时提示用户，不自动创建。
8. `reject` 前必须向用户确认。
9. 运行结束时，报告最终状态（done / failed）并提示 `final_requirement` 的产物路径。
10. goal 文本较长时优先使用 **Bash** 工具而非 PowerShell，避免中文编码问题。
11. 人工澄清文件默认写入 `docs/runs/<run_id>/human_clarification.md`，不使用项目根目录。
