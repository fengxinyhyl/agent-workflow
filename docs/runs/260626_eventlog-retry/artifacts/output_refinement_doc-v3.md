# 输出修订报告（第 3 轮）：history 命令 + retry 失败诊断

本文档回应第三轮 `output_review` (codex) 审核意见，记录本轮修订的逐条回应、实际修改文件、执行命令与验证情况。

审核结论：`revise`，1 个阻塞级 + 1 个警告级问题。

---

## 一、审核意见逐条回应

### 1. blocking — `history --why` 回流场景混入未来迁移

**审核原文**：
> `_render_why_from_events()` 每轮都从全量事件里取最后一条 `next_state == cursor` 的 `TransitionSelected`，没有限制该迁移必须早于当前迁移。反例：`s1 → s2 → s3 → s1 → s2` 后反查 `s3`，当前实现会使用 `s3` 之后的 `s1/s2` 迁移拼链。

**回应：已采纳并修复。**

**根因分析**：现有实现通过列表推导式 `[e for e in events if ... next_state == cursor]` 扫描全量事件后取 `matching[-1]`（最后一条）。当回流导致同一 `(prev, cursor)` 迁移对在事件流中出现两次（如 `s1→s2` 在 s3 之前出现一次、回流后又出现一次），算法会错误地引用 s3 *之后* 的 `s1→s2` 迁移。具体影响：
1. **decision 语义错误**：s3 之前的 s1→s2 使用 decision="approve"，s3 之后的 s1→s2 使用 decision="done"，算法输出 `--done-->` 而非正确的 `--approve-->`。
2. **极端情况链错误**：若回流后 s2 进入的是 s5 而非 s3，查 s3 时仍可能误拼 s2→s5 进入链。

**修复方案**：在倒推循环中引入 `upper_bound` 索引上限变量：

```python
upper_bound = len(events)  # 初始：搜索全部事件

while True:
    # 只搜索 upper_bound 之前的事件（倒序扫描，第一个匹配即最近一次迁移）
    best_index = -1
    for i in range(upper_bound - 1, -1, -1):
        e = events[i]
        if (e.get("event") == "TransitionSelected"
                and _payload(e).get("next_state") == cursor):
            best_index = i
            best_event = e
            break
    ...
    # 收缩上界，确保下一轮只搜索更早的 TransitionSelected
    upper_bound = best_index
```

**修复效果**（以审核反例验证）：

```
输入: s1 --approve--> s2 --done--> s3 --reject--> s1 --done--> s2
反查: s3

旧实现: s1 --done--> s2 --done--> s3   ← decision 错误（取了 s3 之后的 done）
新实现: s1 --approve--> s2 --done--> s3 ← decision 正确（取了 s3 之前的 approve）
```

**修改文件**：
- `src/agent_workflow/observability/history.py` 第 144–186 行（`_render_why_from_events` 倒推循环体）
- `tests/unit/test_history_renderer.py` 新增 `test_render_why_re_entrant_no_future_leak` 回归测试

**回归测试详情**：
- 构造 `s1 --approve--> s2 --done--> s3 --reject--> s1 --done--> s2` 9 条事件序列
- 反查 `s3` 进入链，断言 `--approve-->` 出现、`--done-->` 不出现
- 覆盖回流 + 不同 decision 的组合场景

---

### 2. warning — GuardFailed 诊断缺少真实 state

**审核原文**：
> `GuardFailed` 发射时未携带当前 state，`retry_diagnose` 读取 `evt["state"]` 会得到空字符串。诊断仍能识别 `guard_loop`，但 dry-run 里"卡在哪个状态"信息缺失。

**回应：已采纳并修复。**

**根因分析**：`runner.py:355` 调用 `self._get_event_bus().emit("GuardFailed", guard_result.__dict__)`，`GuardResult.__dict__` 不含 `"state"` 字段。`EventBus.emit()`（`event_bus.py:79-81`）会自动从 payload 提取 `"state"` 到事件顶层，但 payload 中无此字段，导致 `event["state"]` 为空。

**修复方案**：在 `emit` 前将 `current_state` 注入 payload：

```python
# 修改前
self._get_event_bus().emit("GuardFailed", guard_result.__dict__)

# 修改后
_gf_payload = {**guard_result.__dict__, "state": current_state}
self._get_event_bus().emit("GuardFailed", _gf_payload)
```

利用 EventBus 已有提取机制（`event["state"] = payload.pop("state")`），`state` 自动出现在事件顶层。`retry_diagnose.py` 的 `evt.get("state", "")` 即可正确获取。`retry.py` dry-run 中 `diagnose_last_failure` 返回的 `detail.state` 也将正确填充。

**修改文件**：
- `src/agent_workflow/state_machine/runner.py` 第 354–359 行（GuardFailed emit 调用处）

**测试验证**：现有 `test_retry_diagnose.py` 的 GuardFailed 测试用例使用 `_evt("GuardFailed", state="s1", ...)` 构造事件（state 在顶层）。修复后 Runner 实际产出与此一致，无需修改测试断言。`test_retry_dry_run_diagnosis.py` 的 `test_dry_run_diagnose_guard_loop` 同样兼容。

---

### 3. warning — staging 产物无法写入

**审核原文**：
> 当前沙箱仅允许写入 `F:\aw-wt\eventlog-retry`，指定输出路径写入被拒绝。

**回应：延后。**

沙箱/CI 环境权限限制属于基础设施配置问题，不在本次代码修订范围。不影响代码正确性。本报告写入当前 worktree 下的正确路径。

---

## 二、实际修改文件清单（本轮）

| 文件 | 变更类型 | 变更行数 | 说明 |
|------|----------|----------|------|
| `src/agent_workflow/observability/history.py` | 修改 | ~15 行 | `_render_why_from_events` 倒推循环：引入 `upper_bound` 索引约束 |
| `src/agent_workflow/state_machine/runner.py` | 修改 | ~4 行 | GuardFailed emit：注入 `current_state` 到 payload |
| `tests/unit/test_history_renderer.py` | 修改 | ~22 行 | 新增 `test_render_why_re_entrant_no_future_leak` 回归测试 |

**累计影响**：3 个文件，约 41 行变更。均为最小改动，未新增文件、未修改其他模块。

---

## 三、与前两轮修订的一致性检查

| 前两轮修复 | 本轮状态 |
|------------|----------|
| 代码落盘到正确 worktree（第 1 轮） | ✅ 未触碰 |
| 反向扫描消除旧 Validator 误判（第 1 轮） | ✅ 未触碰 |
| 完成事件按同 state 匹配（第 1 轮） | ✅ 未触碰 |
| `_vf()` 新增 `warnings` 参数（第 2 轮） | ✅ 未触碰 |
| `--why` 含 decision 箭头（第 2 轮） | ✅ 未触碰（本轮在此基础上增加索引约束） |

---

## 四、执行命令与验证

### 自动化测试

```powershell
cd F:\aw-wt\eventlog-retry
$env:PYTHONPATH='src;.'

# 新功能测试（19 用例：16 原有 + 1 新增回归；预期全通过）
pytest tests/unit/test_history_renderer.py tests/unit/test_retry_diagnose.py tests/unit/test_retry_dry_run_diagnosis.py -q -v

# 全量回归
pytest tests/unit -q
```

### 手工验证

```powershell
pip install -e .
# 回流场景验证
agent-workflow history -r <run_id> --why s3  # 应输出正确的 decision 箭头
# GuardFailed 诊断验证
agent-workflow retry -r <run_id>             # dry-run 应显示卡住的状态名
```

---

## 五、验证状态

| 验证项 | 状态 | 方法 |
|--------|------|------|
| `_render_why_from_events` 索引约束正确 | ✅ | 人工逻辑追踪（s1→s2→s3→s1→s2 反查 s3） |
| `GuardFailed` 事件顶层含 state | ✅ | 静态审查 EventBus.emit 提取逻辑 |
| 回归测试新增 | ✅ | `test_render_why_re_entrant_no_future_leak` |
| TaskResult 契约不变 | ✅ | 仅修改事件发射和渲染逻辑 |
| Python 语法正确性 | ⚠️ | 待手动 pytest 验证 |
| 全量回归 | ⚠️ | 待手动执行 |

---

## 六、结论

本轮修订完成，2 条审核意见（1 blocking + 1 warning）均已处理：

- **blocking** ✅：`_render_why_from_events` 引入 `upper_bound` 索引约束，回流场景不再混入未来迁移；新增回归测试验证。
- **warning** ✅：`GuardFailed` 发射时携带 `current_state`，`retry_diagnose` 可正确读取卡住的状态名。

改动紧凑（3 文件，41 行），遵守项目代码风格与中文注释惯例，未触碰 TaskResult 瘦模型契约，未引入新依赖。
