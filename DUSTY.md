# agent-workflow 持久化存储布局

> **版本**: v1 (2026-06-08)
> **状态**: design — 尚未实现 runtime store
> **参考**: Phase 1 schema contract (`schemas/`)

## 概述

agent_workflow 的运行时持久化状态位于项目根下的 `.agent-workflow/` 目录。存储分为两类：

- **`durable/`** — 持久化恢复数据，独立于单次 `run_root`，用于 workflow recovery
- **`runs/`** — 单次运行的工作目录，可被清理

## 目录结构

```text
.agent-workflow/
  durable/
    events/
      <workflow_run_id>.events.jsonl     # EventLog: append-only 事件流
    registry/
      <workflow_run_id>.artifacts.jsonl  # ArtifactRegistry: artifact 记录
    checkpoints/
      <workflow_run_id>.checkpoints.jsonl # WorkflowCheckpoint: 恢复锚点
    manifests/
      <workflow_run_id>.workflow_manifest.json # 可再生视图 (cache/view)
  runs/
    <run_id>/
      staging/         # worker 暂存输出（promote 前）
      logs/            # worker 执行日志
      packets/         # worker 调试副本
```

## 存储规则

### durable/ 规则

1. **`durable/` 独立于单次 `run_root`**：清理 `runs/<run_id>/` 不得破坏 checkpoint/event/registry recovery。
2. **Registry 与 Checkpoint 分开 store**：只共享 `durable/` 根目录。
3. **EventLog 每个 `workflow_run_id` 至少一个文件**。Workflow terminal（completed / cancelled）后 event log 标记 closed。
4. **第一版只要求文件组织和 closed policy**，不要求 compaction。后续可在 final-audited 后归档 closed event log。

### runs/ 规则

1. **`runs/<run_id>/` 可被清理**：不包含 recovery 所需数据。
2. `staging/` 存放 worker 在 promote 前的临时输出。
3. `packets/` 存放 worker 调试副本（非正式 artifact）。
4. `logs/` 存放 worker 的原始执行日志（JSONL 格式）。

## 文件格式

### events/ — EventLog

每个 `workflow_run_id` 对应一个 JSONL 文件，每行一个 `WorkflowEvent`。Schema 定义见 `schemas/workflow_event.schema.json`。

- **写入模式**: append-only
- **排序**: 按 `sequence` 升序
- **关闭标记**: workflow terminal 后在文件末尾追加 `{"type": "event_log_closed", "workflow_run_id": "...", "closed_at": "..."}`
- **读取**: 按 `sequence` 回放

### registry/ — ArtifactRegistry

每个 `workflow_run_id` 对应一个 JSONL 文件，每行一个 `ArtifactRegistryEntry`。Schema 定义见 `schemas/artifact_registry_entry.schema.json`。

- **写入模式**: append-only（新版本追加新行）
- **查询**: 通过 selectors（`find_latest`, `find_by_event`, `find_by_work_item`, `list_versions`, `render_manifest`）在内存中过滤
- **版本**: 同 `artifact_id` 的多个版本共存，最新版本在文件末尾

### checkpoints/ — WorkflowCheckpoint

每个 `workflow_run_id` 对应一个 JSONL 文件，每行一个 `WorkflowCheckpoint`。Schema 定义见 `schemas/workflow_checkpoint.schema.json`。

- **写入触发点**: workflow created, work item created, phase completed, approval granted/rejected, artifact promoted, retry started, cancelled, resumed, final completed
- **恢复**: 从最新 checkpoint 开始，验证 checksum，回放后续 events

### manifests/ — Workflow Manifest

可再生视图，从 EventLog + Registry + Checkpoint 重建。不包含 EventLog/Registry 中没有的信息。

## 清理策略

1. `runs/<run_id>/` — 在 workflow 完成后可安全删除。
2. `durable/events/` — closed event log 可在 final-audited 后归档。
3. `durable/registry/` 和 `durable/checkpoints/` — 保留至 workflow_run_id 不再需要恢复。
4. 所有清理操作不得影响其他 workflow_run_id 的数据。

## 与现有系统的关系

- **`output/workflows/<run_id>/`** (legacy): 当前 `strategy/research/agent_workflow` 的运行目录，包含 `workflow_state.json`。计划在 Phase 2 legacy bridge 中导入。
- **`agent-workflow/src/agent_workflow/`** (root core): Phase 3+ 实现 runtime store，写入 `.agent-workflow/durable/`。
- **`.agent-workflow/`** 是项目级目录，不提交到 git（应在 `.gitignore` 中）。

## 设计约束

详见 `strategy/workflow/decisions/agent_workflow_long_task_migration/260608-agent_workflow_long_task_migration-plan-v4.md` 中的 Durable Storage Layout 章节。
