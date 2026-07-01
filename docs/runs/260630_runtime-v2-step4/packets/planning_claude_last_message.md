# planning claude debug packet

Session ID: 9a34ff90-d633-44a1-983a-56dbbdfac02e

## 最后一条 assistant message

---

## 开发计划概要

计划已完成，详细内容见 `staging/planning/plan_doc.md`。以下是核心要点：

### 需求理解

第 4 步要解决的核心问题是：`_unroll_single_loop`（loader.py:185-304）内部仍用**硬编码键名**（`done`/`revise`/`approve`）区分节点角色，但第 2 步已将 state 归一化为 `next`/`on`/`on_status` 结构。这导致展开后的 state 变成半旧半新的混合体——例如线性节点的 `done` 被重新塞回 `on` dict。

### 改造方案（7 个子步骤）

| 步骤 | 内容 | 可独立验证 |
|------|------|------------|
| **4a** | 重构测试辅助函数 `_make_states`，走 `_normalize_state` 路径 | 存量测试仍通过 |
| **4b** | 线性节点改用 `next` 串接，不再写 `on["done"]` | 线性展开测试 |
| **4c** | 分支节点通用化 `on` 分派，消除 `approve`/`revise` 硬编码 | 分支展开测试 |
| **4d** | 修正外部 state 的 `next`/`on_status` 循环引用 | 外部引用测试 |
| **4e** | 新增 6~8 个混合场景单元测试 | 全部新测试通过 |
| **4f** | `plan-review-advise-loop-example` 展开结果不变验证 | 存量流程等价 |
| **4g** | 全量回归测试 | 全绿 |

### 边界明确

- **不改** `StateModel` 字段、路由逻辑、`_normalize_state` 规则、`_loop` 块语法
- **只改** `_unroll_single_loop` 内部展开逻辑 + 测试
- **停止规则**：存量测试超 5 个需改断言、或代码改动超 200 行、或存量流程展开结果不等价时停止

```json
{
  "schema_version": 1,
  "task_id": "planning",
  "state": "planning",
  "status": "success",
  "decision": "done",
  "summary": "已完成 Runtime v2 第 4 步开发计划的制定。计划涵盖：_unroll_single_loop 核心逻辑改造（用 next/on 结构替代 done/revise/approve 硬编码键名）、外部 state 循环引用修正（覆盖 next/on_status 字段）、混合场景测试（6-8 个新测试）、存量兼容性验证。分 7 个可独立验证的子步骤（4a-4g），每步有明确验证方式。明确了 4 条停止规则和 4 个关键风险及缓解措施。",
  "artifacts": [
    {
      "name": "plan_doc",
      "staging_path": "G:\\aw-wt\\runtime-v2-step4\\staging\\planning\\plan_doc.md",
      "artifact_path": "artifacts/plan_doc.md",
      "type": "markdown"
    }
  ],
  "execution": {
    "started_at": "2026-06-30T00:00:00Z",
    "finished_at": "2026-06-30T00:00:00Z",
    "duration_seconds": 0,
    "attempt": 1,
    "exit_code": 0
  },
  "issues": []
}
```

