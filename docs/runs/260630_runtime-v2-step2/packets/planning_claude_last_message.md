# planning claude debug packet

Session ID: e7daa3ce-f2a8-404d-8b58-f31602002b64

## 最后一条 assistant message

---

## 开发计划摘要

我已完成 Runtime v2 Step 2 的完整开发计划，核心要点如下：

### 改造范围（7 个文件）

| 优先级 | 文件 | 改动量 | 关键变化 |
|---|---|---|---|
| **核心** | `config/models.py` | ~20行 | StateModel +`next` +`on_status` |
| **核心** | `config/loader.py` | ~40行 | `load_state` 归一：`done→next`，`fail/blocked→`丢弃 |
| **核心** | `state_machine/machine.py` | ~60行 | 两段式路由 + 两条 validate 护栏 |
| **核心** | `state_machine/transition.py` | ~10行 | `TransitionResult` +`status` +`route_by` |
| **核心** | `state_machine/runner.py` | ~30行 | 主循环调用两段式；`_create_error_result` `decision=None` |
| 兼容 | `observability/status.py` | ~10行 | 展示 `next`/`on_status` |
| 兼容 | `observability/explain.py` | ~10行 | 解释 `next`/`on_status` |

### 关键设计决策

1. **归一在 `load_state` 阶段执行**：归一到 `_normalize_state()` 函数，影响所有 YAML 加载路径（含 `_loop` 展开后的 state 数据）
2. **不删 `state.on` 字段**：`on` 仍然保留，只是其中的生命周期键（`done`/`fail`/`blocked`）被归一移除，剩下纯业务键
3. **`terminal_states` 推断更新**：现在判断 `next` 为空而非仅 `on` 为空
4. **最重要的风险**：`software-dev` 中有 `blocked: audit`（不等于 `default: failed`），归一后必须进 `on_status`

### 测试计划

- **新增 14 个单元测试**覆盖归一逻辑、两段式路由、护栏
- **存量集成测试零改动通过**——最重要的回归防线
- **存量 10 个 workflow YAML 零修改**

完整计划已写入 `staging/planning/plan_doc.md`。

```json
{
  "schema_version": 1,
  "task_id": "planning",
  "state": "planning",
  "status": "success",
  "decision": "done",
  "summary": "完成 Runtime v2 Step 2 开发计划制定。计划覆盖 7 个文件（5 核心 + 2 兼容），分 6 个子步骤，每步可独立验证。明确了非目标（不改 _unroll_loops、不实现 Repair），评估了 5 个关键风险（阻塞目标映射、resolve_transition 签名变更等），新增 14 个测试点。",
  "artifacts": [
    {
      "name": "plan_doc",
      "staging_path": "G:\\aw-wt\\runtime-v2-step2\\staging\\planning\\plan_doc.md",
      "artifact_path": "artifacts/plan_doc.md",
      "type": "markdown"
    }
  ],
  "execution": {
    "started_at": "2026-06-30T00:00:00+08:00",
    "finished_at": "2026-06-30T00:00:00+08:00",
    "duration_seconds": 0,
    "attempt": 1,
    "exit_code": 0
  }
}
```

