# Replacement Parity Contract

> **Baseline freeze date:** 2026-06-09  
> **Controlling plan:** `strategy/workflow/decisions/agent_workflow_long_task_migration/260609-agent_workflow_long_task_migration-plan-v7.md`  
> **Status:** baseline frozen for Phase 0  
> **Replacement target:** root `agent-workflow` must achieve full parity with legacy `strategy.research.agent_workflow` before cutover

本文档冻结当前 legacy 行为作为显式 parity contract。在 root `agent-workflow` 满足以下全部条件之前，不得切换 `.claude/commands/agent-workflow.md` 的默认 backend。

---

## 1. Command Matrix

### 1.1 默认启动 (quick-dispatch)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow <strategy> <topic> <goal...>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow quick-dispatch --strategy <strategy> --topic <topic> --goal <goal> --timeout-seconds 1800 --permission-mode default --stream summary` |
| **触发节点** | `lineage_preflight → scaffold → plan → review → revise → human_approval`（停在 approval） |
| **Artifact** | staged 写入 `output/workflows/<run_id>/staged/`；contract validation 通过后 promote 到 `strategy/<strategy>/decisions/<topic>/` |
| **State transition** | `initialized → preflight → planning → reviewing → revising → waiting_human_approval` |
| **Safety** | lock 在启动时获取，terminal/failed/cancelled 时释放；`approve-execution` 不自动触发执行 |

### 1.2 预览模式 (quick-plan / preview)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow preview <strategy> <topic> <goal...>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow quick-plan --strategy <strategy> --topic <topic> --goal <goal> --timeout-seconds 1800 --permission-mode default --stream summary` |
| **行为** | 同 quick-dispatch，停在 `waiting_human_approval` |

### 1.3 从已有 plan 接手 (quick-review)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow quick-review <strategy> <topic> <goal...>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow quick-review --strategy <strategy> --topic <topic> --goal <goal> --timeout-seconds 1800 --permission-mode default --stream summary` |
| **跳过节点** | `lineage_preflight` + `scaffold` + `plan` → 直接进入 `review` |
| **要求** | `strategy/<strategy>/decisions/<topic>/` 下已有符合命名规范的 plan 文件 |
| **Plan 验证** | 运行时验证 required sections、sha256 recording、`--existing-plan-path` containment |
| **State transition** | `initialized → reviewing → revising → waiting_human_approval` |

### 1.4 状态查询 (status)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow status <run_id>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow status --run-id <run_id>` |
| **输出字段** | `run_id`, `strategy`, `topic`, `status`, `current_node`, `lifecycle_phase`, `decisions_dir`, `lock_status`, `error_summary`, `nodes[]` |

### 1.5 日志查询 (log)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow log <run_id>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow log --run-id <run_id> --summary` |
| **输出** | JSONL 事件摘要：node → worker → status → exit code → tokens → error reason |

### 1.6 节点日志 (tail)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow tail <run_id> <node> [lines]` |
| **Backend 命令** | `python -m strategy.research.agent_workflow tail --run-id <run_id> --node <node> --lines <lines>` |
| **输出** | 指定 node 的 worker log 尾部行 |

### 1.7 批准执行 (approve)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow approve <run_id> [note...]` |
| **Backend 命令** | `python -m strategy.research.agent_workflow approve-execution --run-id <run_id> --note <note>` |
| **行为** | 仅更新 state → `approved_for_execution`；写入 approval record；**不自动触发 execute** |
| **State transition** | `waiting_human_approval → approved_for_execution` |

### 1.8 继续执行 (continue)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow continue <run_id>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow continue --run-id <run_id>` |
| **前置条件** | state 必须为 `approved_for_execution` |
| **触发节点** | `execute → code_audit → final_packet → summary` |
| **Safety** | 未 approval 时 fail-fast；stale lock 默认 fail-fast（需 `--force-unlock` 显式解锁） |

### 1.9 取消 (cancel)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow cancel <run_id>` |
| **Backend 命令** | `python -m strategy.research.agent_workflow cancel --run-id <run_id>` |
| **行为** | 写 state → `cancelled`；emit cancel event；释放 lock |

### 1.10 重试 (retry)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow retry <run_id> [node]` → 默认 dry-run |
| **真实执行** | `/agent-workflow retry <run_id> [node] dispatch` → 显式 `--dispatch` |
| **Dry-run** | `python -m strategy.research.agent_workflow retry --run-id <run_id> --dry-run --stream summary` |
| **Real dispatch** | `python -m strategy.research.agent_workflow retry --run-id <run_id> --dispatch --stream summary` |
| **Safety** | 默认 dry-run；`--dispatch` 必须显式声明 |

### 1.11 强制解锁 (force)

| 维度 | Legacy 行为 |
|------|------------|
| **用户输入** | `/agent-workflow force <strategy> <topic> <goal...>` |
| **Backend 命令** | `quick-dispatch --force-unlock` |
| **行为** | 清理旧 lock 后启动新 workflow |

---

## 2. Schema Mismatch Record

### 2.1 Legacy Lifecycle (9+1 nodes)

```
lineage_preflight → scaffold → plan → review → revise → human_approval → execute → code_audit → final_packet → summary
```

### 2.2 Root DEFAULT_STEPS (5 nodes)

```
plan → review_plan → code_execute → final_audit → summary
```

### 2.3 差异对比

| Legacy Node | Root Equivalent | 差异 |
|-------------|----------------|------|
| `lineage_preflight` | — | Root 无此节点。实验 lineage 检查 |
| `scaffold` | — | Root 无此节点。目录初始化 + sidecar 模板 |
| `plan` | `plan` | 名称一致，行为不同（contracts 驱动 vs generic） |
| `review` | `review_plan` | 名称不同。Adversarial review + 修正循环 |
| `revise` | — | Root 无此节点。review 后 plan 修订 |
| `human_approval` | — | Root 无此节点。人工确认 gate |
| `execute` | `code_execute` | 名称不同。Worker adapter dispatch |
| `code_audit` | `final_audit` | 名称和语义不同。Legacy 是代码审计，root 是最终审计 |
| `final_packet` | — | Root 无此节点。审计包构建 |
| `summary` | `summary` | 名称一致 |

### 2.4 缺失能力汇总

1. **lineage_preflight** — 实验 lineage 检查，防止重复/冲突实验
2. **scaffold** — decisions 目录初始化和 sidecar 模板
3. **revise** — review→plan 的修正循环
4. **human_approval** — 人工确认 gate（执行前阻断）
5. **final_packet** — 审计包构建（非仅审计）
6. **code_audit** — 独立于 final_audit 的代码级审计
7. **Lock 管理** — strategy×topic 级别的文件锁
8. **Contract 驱动** — output template / input deps / required sections validation
9. **Transient topic routing** — 测试 topic 不写入 formal decisions 目录
10. **--existing-plan-path containment** — 路径安全边界

---

## 3. Replacement Truth Source Declaration

**明确声明：**

- Cutover target 是 **legacy 9+1 lifecycle node parity**（`lineage_preflight → scaffold → plan → review → revise → human_approval → execute → code_audit → final_packet → summary`）。
- Root 当前 5-step `DEFAULT_STEPS`（`plan → review_plan → code_execute → final_audit → summary`）是 **placeholder draft**，不是 replacement truth source。
- Root `DEFAULT_STEPS` 在 replacement 实现中只能作为 **alias/view** 存在，不得作为 replacement 行为的源真值。
- 任何以 root 5-step 为基准的 parity 判断均无效。

---

## 4. Module Reuse Strategy (Phase 3)

Phase 3 策略：**先 reuse/import legacy 模块，后考虑 copy/migration**。

| 模块 | 策略 | 说明 |
|------|------|------|
| `strategy.research.agent_workflow.contracts` | Reuse (import in place) | Contract 定义：output_template、input_deps、extra_outputs |
| `strategy.research.agent_workflow.validators` | Reuse (import in place) | Required sections 验证、plan 文件验证 |
| `strategy.research.agent_workflow.prompts` | Reuse (import in place) | 各节点 prompt 模板 |
| `strategy.research.agent_workflow.skill_registry` | Reuse (import in place) | Skill 注册与映射 |
| `strategy.research.agent_workflow.adapters/` | Reuse (import in place) | Claude CLI / Codex CLI / DeepSeek CLI / Mock 适配器 |
| `strategy.research.agent_workflow.state` | Partial reuse | Run ID 生成、decisions_dir 计算；lock 管理需在 root 重新实现 |
| `strategy.research.agent_workflow.locks` | Reimplement in root | Lock 语义等价但实现方式可能不同 |
| `strategy.research.agent_workflow.cli` | Reimplement in root | CLI parser 需在 root 包内重新实现 |
| `strategy.research.agent_workflow.nodes` | Reuse (import in place) | Plan/review/revise 节点渲染 |
| `strategy.research.agent_workflow.execution_log` | Partial reuse | JSONL 格式保持一致，存储位置可能调整 |
| `strategy.research.agent_workflow.graph/` | Reimplement in root | LangGraph → root state_machine 的映射 |

---

## 5. Storage Profile Decision

**首版 replacement run storage：`output/workflows/<run_id>/`**（与 legacy 一致）。

- 不静默迁移用户到 `.agent-workflow/runs/<run_id>/`。
- 旧 legacy run 目录保持可检查。
- Cutover 前需在 README/sidecar 中记录 storage decision。

---

## 6. Blocking Parity Tests Checklist (Phase 3 前)

以下 7 项测试必须在 Phase 3 开始前全部通过（来自 plan-v7 §Blocking Parity Tests Before Phase 3）：

| # | 测试项 | 说明 |
|---|--------|------|
| 1 | `--existing-plan-path` containment | 拒绝 topic decisions 目录外路径 |
| 2 | `retry --dry-run` 不 promote formal artifacts | dry-run 不写入 formal sidecars |
| 3 | `continue` in `waiting_human_approval` fails fast | 未 approval 的 continue 失败 |
| 4 | `cancel` writes state and releases lock | cancel 完整释放资源 |
| 5 | transient route tests | 测试 topic 不写入 formal `strategy/workflow/decisions` |
| 6 | root command parser preserves legacy options | parser 输出兼容 legacy 用户可见选项 |
| 7 | replacement-parity.md 记录 schema 差异 | 本文档已记录 5-step vs 10-step 差异和 truth source 声明 |

---

## 7. Baseline Test Results (2026-06-09)

### 7.1 Legacy Tests

**Command:** `pytest strategy/research/tests -k agent_workflow -q --tb=short`

| 指标 | 值 |
|------|-----|
| Exit code | 1（1 error，非测试失败） |
| Collected (selected) | 373 (114) |
| Passed | 114 |
| Failed | 0 |
| Errors | 1 (`test_codex_raw_stream_prints_stdout_and_preserves_log` — PermissionError on temp dir，环境问题) |
| Deselected | 259 |

**评估：** Legacy 测试套件健康。唯一的 error 是 pytest tmp_path fixture 的 Windows 权限问题，与 agent_workflow 代码无关。

### 7.2 Root Tests

**Command:** `cd agent-workflow; $env:PYTHONPATH='src;.'; pytest tests -q --tb=short`

| 指标 | 值 |
|------|-----|
| Exit code | 0 |
| Collected | 211 |
| Passed | 211 |
| Failed | 0 |
| Errors | 0 |

**评估：** Root 测试套件全部通过（需 `PYTHONPATH='src;.'` 环境变量注入）。

---

## 8. Root-Level Loose Module Test Inventory

### 8.1 按 Import Type 统计

| Import Type | Count | Phase 1 操作 |
|-------------|-------|-------------|
| root_loose（`from work_item import ...` 等） | 9 | 改为 `from agent_workflow.long_task.xxx import ...` |
| sys_path_hack（`sys.path.insert`） | 1 | 改为 package import |
| package（`from agent_workflow.xxx import ...`） | 12 | 不变 |
| no_workflow_import | 1 | 不变 |
| **Total** | **23** | |

### 8.2 Root-Loose 测试文件清单

| Test File | 当前 Import | Phase 1 Target |
|-----------|------------|----------------|
| `test_work_item.py` | `from work_item import ...` | `from agent_workflow.long_task.work_item import ...` |
| `test_workflow_run.py` | `from workflow_run import ...` | `from agent_workflow.long_task.workflow_run import ...` |
| `test_dependency_graph.py` | `from dependency_graph import ...` | `from agent_workflow.long_task.dependency_graph import ...` |
| `test_event_log.py` | `from event_log import ...` | `from agent_workflow.long_task.event_log import ...` |
| `test_state_store.py` | `from state_store import ...` | `from agent_workflow.long_task.state_store import ...` |
| `test_queue_runner.py` | `from queue_runner import ...` | `from agent_workflow.long_task.queue_runner import ...` |
| `test_chain_workflow.py` | `from queue_runner import ...` | `from agent_workflow.long_task.queue_runner import ...` |
| `test_diamond_workflow.py` | `from queue_runner import ...` | `from agent_workflow.long_task.queue_runner import ...` |
| `test_failure_recovery.py` | `from queue_runner import ...` | `from agent_workflow.long_task.queue_runner import ...` |
| `test_cli_smoke.py` | `sys.path.insert(...)` → root-level loose | 改为 package import |
