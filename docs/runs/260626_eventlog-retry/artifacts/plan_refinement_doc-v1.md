# 修订计划：history 命令 + retry 失败诊断（v2）

本文档基于 `plan_review_doc-v1.md` 的审核意见，对 `plan_doc-v1.md` 做出修订。下游执行节点应将本文档与 `plan_doc-v1.md` 联合阅读，以本文档的差异为准。

---

## 一、审核意见处理总览

| # | 审核条目 | 类别 | 结论 |
|---|---|---|---|
| 1 | 遗漏 `max_duration_minutes` guard 分支 | 需求覆盖 | 已采纳（核心改动） |
| 2 | R1：`_build_dry_run_steps` 事件源缺空文件防御 | 风险 | 已采纳 |
| 3 | R2：`render_why` 缺过滤伪代码与防回环 | 风险 | 已采纳 |
| 4 | R3：测试夹具对 WorkflowConfig 内部结构的依赖 | 风险 | 已采纳 |
| 5 | 缺测试：`max_duration_minutes` guard 诊断 | 测试 | 已采纳 |
| 6 | 缺测试：events.jsonl 文件缺失 | 测试 | 已采纳 |
| 7 | 缺测试：多 state 多次 AgentStarted 的崩溃判定 | 测试 | 已采纳 |
| 8 | 缺测试：ValidatorFinished(passed=true) 非阻断场景诊断行为 | 测试 | 已采纳（澄清语义） |
| 9 | S1：`ValidatorStarted` 是否纳入主干 | 简化 | 已采纳 |
| 10 | S2：合并 `retry_diagnose.py` 到 `retry.py` 或 `history.py` | 简化 | 不采纳 |
| 11 | S3：Step 5（文档帮助文案）合并入 Step 4 | 简化 | 已采纳 |

---

## 二、逐条回应

### 1. 补全 `max_duration_minutes` 诊断分支（已采纳）

**事实核对**：`src/agent_workflow/state_machine/guard.py` 明确产生三种 `guard_type`：`max_visits`、`max_duration_minutes`、`max_retries`。原计划仅覆盖前者与后者，遗漏中间一项会落入 `KIND_UNKNOWN`，导致诊断结论"未知"，对用户无指导意义。

**修改**：
- 新增诊断子类 `KIND_GUARD_TIMEOUT`，独立于 `KIND_GUARD_LOOP`，**retry_recommended=True**（因为重新进入主循环会重置 GuardChecker 的累计运行时间起点；而 `max_visits`/`max_retries` 是按计数累积，重试不会自动重置，应判 False）。
- `diagnose_last_failure` 在扫到最后一条 `GuardFailed` 时按 `guard_type` 分派：
  - `max_visits` / `max_retries` → `KIND_GUARD_LOOP`，retry_recommended=False
  - `max_duration_minutes` → `KIND_GUARD_TIMEOUT`，retry_recommended=True
- `reason` 文案分别为"回流/重试次数已达上限，重试无意义"、"运行时长超限，重试将重置计时器"。

### 2. R1 — `_build_dry_run_steps` 空文件防御（已采纳）

**修改**：在 `_build_dry_run_steps()` 中先空列表早返一条 placeholder step：
```python
events = read_log(context.run_id, run_root=context.run_root)
if not isinstance(events, list) or not events:
    steps.append({
        "action": "diagnose_last_failure",
        "status": "ok",
        "detail": {"kind": "unknown", "reason": "无事件日志可供诊断", "retry_recommended": True},
    })
else:
    diagnosis = diagnose_last_failure(events)
    steps.append({...})
```
- 显式校验 `isinstance(events, list)` 以排除 `summary=True` 误用导致的字符串返回（属于编程错误的早暴露而非运行时容错）。
- `diagnose_last_failure([])` 内部仍然保留对空列表的容错（返回 `KIND_UNKNOWN`），双层保险。

### 3. R2 — `render_why` 过滤伪代码与防回环（已采纳）

**修改**：在计划 §4 Step 3 加入精确伪代码（也作为实现合同写入函数 docstring）：
```python
def render_why(events, target_state):
    chain = [target_state]
    cursor = target_state
    seen = {target_state}
    while True:
        # 取按时序最后一条进入 cursor 的 TransitionSelected
        ts = [e for e in events
              if e.get("event") == "TransitionSelected"
              and e.get("payload", {}).get("next_state") == cursor]
        if not ts:
            break  # 找不到进入路径 → 在链头打印"(start)"或友好提示
        prev = ts[-1].get("payload", {}).get("current_state")
        if not prev or prev in seen:
            break  # 防回环：gate→resume / 自循环 state
        chain.append(prev)
        seen.add(prev)
        cursor = prev
    return " → ".join(reversed(chain))
```
- **过滤条件**：`event == "TransitionSelected"` 且 `payload.next_state == cursor`。
- **取最后一条**：按文件顺序末位即时序最后一次进入。
- **防回环**：`seen` 集合记录已访问 state；若上游 state 已在 chain 中，停止。
- **链路断开**：若没有匹配的 TransitionSelected，结束并在输出层标注 `(进入源未知)` 或 `(start)`。

### 4. R3 — 测试夹具对 WorkflowConfig 的依赖（已采纳）

**事实核对**：`retry.py:262` 调用 `WorkflowConfig.from_dict(snapshot)`，snapshot 来自 `context.workflow_variables["_workflow_snapshot"]`。

**修改**：在测试中使用如下最简 snapshot 结构（在测试文件顶部定义常量复用，集中维护以应对未来 schema 变化）：
```python
MINIMAL_WORKFLOW_SNAPSHOT = {
    "name": "diag-test",
    "initial_state": "s1",
    "terminal_states": ["done", "failed"],
    "tasks": {
        "t1": {"instruction": "noop", "agent": "mock"},
    },
    "states": {
        "s1": {"task": "t1", "on": {"done": "done", "fail": "failed"}, "default": "failed"},
        "done": {},
        "failed": {},
    },
}
```
- 若 `WorkflowConfig.from_dict` 引入新必填字段导致测试失败 → 仅需修改该常量。
- 测试不依赖于 transition / retry policy 细节，因为诊断逻辑不消费 WorkflowConfig 的任何字段（它只读 events.jsonl），WorkflowConfig 仅是 `retry_run` 入口约束的形参。

### 5–7. 三个缺失测试用例（已采纳）

补入 `test_retry_diagnose.py`：

| 用例 | 构造 | 预期 |
|---|---|---|
| `test_max_duration_guard_diagnose` | 最后一条 `GuardFailed{guard_type=max_duration_minutes}` | `kind=KIND_GUARD_TIMEOUT, retry_recommended=True` |
| `test_diagnose_events_missing_file` | 直接传 `events=[]`（模拟 read_log 文件缺失返回） | `kind=KIND_UNKNOWN`，不抛异常 |
| `test_agent_crash_multi_state` | events = [AgentStarted(s1), TaskResultWritten(s1), TransitionSelected(s1→s2), AgentStarted(s2), Heartbeat] | `kind=KIND_AGENT_CRASH`，且 detail.state=s2（不是 s1） |

`test_agent_crash_multi_state` 强制 `diagnose_last_failure` 实现使用"最后一条 AgentStarted 之后是否有完成信号"而非"全局是否曾出现完成信号"——避免实现走捷径。

### 8. ValidatorFinished(passed=true) 非阻断场景诊断行为（已采纳·语义澄清）

**结论**：当最后一条 ValidatorFinished 的 `passed=true`（即使 `warnings` 非空），诊断**不应**将其视为失败原因。诊断按以下优先级匹配：

1. 最后一条 `ValidatorFinished{passed=false}` → validator_block
2. 否则最后一条 `GuardFailed` → guard_loop / guard_timeout
3. 否则最近的 `AgentStarted` 后是否有完成信号 → agent_crash
4. 否则 → unknown

`passed=true` 的 ValidatorFinished 不构成失败信号，会被跳到下一规则。补一条单元测试 `test_validator_passed_falls_through`：events 末尾是 `ValidatorFinished{passed=true, warnings=[...]}` + `AgentStarted` 无后续 → 预期 `kind=KIND_AGENT_CRASH` 而非 `KIND_VALIDATOR_BLOCK`。

### 9. S1 — `ValidatorStarted` 不纳入主干（已采纳）

`MAIN_EVENT_TYPES` 默认白名单去除 `ValidatorStarted`，仅保留 `ValidatorFinished`（带 passed/errors）。`--all` 可见。

### 10. S2 — 合并 retry_diagnose.py（不采纳）

**理由**：原计划的拆分理由（与 retry 流程解耦便于纯函数测试）是合理的。
- 放入 `retry.py` 会让 retry 模块同时承担"流程编排"与"诊断分析"两种关注点，模块体积接近 350 行后可读性下降。
- 放入 `history.py` 在概念上更难自洽：history 是渲染器，diagnose 是分类器；混在一起将来若新增"导出诊断为 JSON"的子命令会产生反向依赖。
- 拆分后 `test_retry_diagnose.py` 可纯函数式调用，无需 mock 任何 retry 内部状态，测试代价更低。

保留 `state_machine/retry_diagnose.py` 独立模块。

### 11. S3 — Step 5 合并入 Step 4（已采纳）

删除原 Step 5，argparse `help=` 文案在 Step 4 注册子命令时同步写好。Step 编号顺延：原 Step 6 → Step 5。

---

## 三、修订后的完整计划

### 1. 需求理解

（同 plan_doc-v1 §1，无变化。关键点重申：诊断的输入是同一份 `logs/events.jsonl`；不修改 TaskResult 契约；不引入数据库。）

### 2. 目标和非目标

（同 plan_doc-v1 §2，无变化。）

### 3. 涉及文件和模块边界

**新增**：
- `src/agent_workflow/observability/history.py`
  - `MAIN_EVENT_TYPES`（白名单常量，**不含 ValidatorStarted**）
  - `render_history(run_id, run_root, show_all=False) -> str`
  - `render_why(run_id, run_root, target_state) -> str`
  - 私有：`_filter_main_events`、`_format_event_line`
- `src/agent_workflow/state_machine/retry_diagnose.py`
  - 常量：`KIND_VALIDATOR_BLOCK`、`KIND_GUARD_LOOP`、`KIND_GUARD_TIMEOUT`（**新增**）、`KIND_AGENT_CRASH`、`KIND_UNKNOWN`
  - `diagnose_last_failure(events) -> dict`：返回 `{kind, reason, retry_recommended, detail}`

**修改**：
- `src/agent_workflow/cli.py`
  - `cmd_history(args)` + `history` 子命令注册（同步写 `help=` 文案）
  - `cmd_retry` 输出循环：为 `action=diagnose_last_failure` step 加一行格式化（`kind / retry_recommended / detail-key`）
- `src/agent_workflow/state_machine/retry.py`
  - `_build_dry_run_steps` 中调用 `read_log` + `diagnose_last_failure`，**先做空文件防御**

**测试新增**：
- `tests/unit/test_history_renderer.py`
- `tests/unit/test_retry_diagnose.py`
- `tests/unit/test_retry_dry_run_diagnosis.py`

**不动**：observability/events.py、jsonl_sink.py（只读）、runner.py、guard.py、TaskResult 模型、Config 模型。

### 4. 分步骤实现方案

**Step 1 — 建立诊断纯函数**
- 新建 `state_machine/retry_diagnose.py`，常量含 `KIND_GUARD_TIMEOUT`。
- `diagnose_last_failure(events)` 匹配优先级：
  1. 最后一条 `ValidatorFinished{passed=false}` → validator_block，提取 `payload.errors`、`payload.state`；retry_recommended=True（修产物后可重试）。
  2. 最后一条 `GuardFailed`：
     - `guard_type ∈ {max_visits, max_retries}` → guard_loop，retry_recommended=False。
     - `guard_type == max_duration_minutes` → guard_timeout，retry_recommended=True。
  3. 最后一条 `AgentStarted` 之后无 `TaskResultWritten`/`ValidatorFinished`/`TransitionSelected`/`TaskFinished`（忽略 Heartbeat/AgentOutput）→ agent_crash，retry_recommended=True，detail.state=该 AgentStarted 的 state。
  4. 否则 → unknown，retry_recommended=True（允许预览但提示无明确诊断）。
- 所有字段访问用 `.get()` 防御性默认值。
- 单元测试：见 §5。

**Step 2 — 接入 retry dry-run**
- `_build_dry_run_steps` 中：
  ```python
  events = read_log(context.run_id, run_root=context.run_root)
  if not isinstance(events, list) or not events:
      diagnosis = {"kind": "unknown", "reason": "无事件日志可供诊断", "retry_recommended": True, "detail": {}}
  else:
      diagnosis = diagnose_last_failure(events)
  steps.append({
      "action": "diagnose_last_failure",
      "status": "ok" if diagnosis["retry_recommended"] else "would_block",
      "detail": diagnosis,
  })
  ```
- `cli.cmd_retry` 输出 step 时，对 `action=diagnose_last_failure` 增加格式化行：`kind=<kind>  retry_recommended=<bool>  reason=<reason>`，errors/state 等附加字段以缩进列表展示。

**Step 3 — history 渲染器**
- `observability/history.py` 实现：
  - `MAIN_EVENT_TYPES = {"WorkflowStarted", "StateEntered", "AgentStarted", "TaskResultWritten", "ValidatorFinished", "ArtifactPromoted", "TransitionSelected", "GuardFailed", "TaskFinished", "WorkflowCompleted", "WorkflowFailed", "WorkflowCancelled", "SkillAdoptionWritten"}`（无 ValidatorStarted）。
  - `_format_event_line(event)` 输出 `[<ts>] <Event> state=<s> <key=value pairs>`，未识别 payload 字段降级为通用打印。
  - `render_history` 走过滤 + 行渲染拼接。
  - `render_why` 实现见前文伪代码，附带 chain 头部"(进入源未知)"提示。

**Step 4 — 接入 CLI（含 help 文案）**
- 在 `cli.py` 中：
  - `cmd_history(args)`：`_find_run_root` 解析 → 调用 history 渲染器 → `safe_print` 输出。
  - `build_parser` 中注册 `history` 子命令，参数：`--run-id/-r`（必填）、`--why <state>`、`--all`、`--project-root/-p`、`--run-root`。`help=` 文案一并写好（不再单列 Step）。
- 手工验证：在现有 `docs/runs/<run>` 上跑 `history -r <id>` 与 `--why <state>`。

**Step 5 — 全量回归**
- `$env:PYTHONPATH='src;.'; pytest tests/unit -q`，确认新增 3 个测试文件通过、未影响存量测试。

### 5. 测试策略

`test_retry_diagnose.py` 用例清单（共 8 项）：
1. `test_validator_block` — 末尾 ValidatorFinished(passed=false, errors=[...]) → validator_block，errors 透传。
2. `test_guard_loop_max_visits` — GuardFailed(max_visits) → guard_loop，retry_recommended=False。
3. `test_guard_loop_max_retries` — GuardFailed(max_retries) → guard_loop，retry_recommended=False。
4. `test_guard_timeout_max_duration_minutes` — **新增**，GuardFailed(max_duration_minutes) → guard_timeout，retry_recommended=True。
5. `test_agent_crash` — AgentStarted 后仅 Heartbeat → agent_crash，retry_recommended=True。
6. `test_agent_crash_multi_state` — **新增**，s1 完成 + s2 AgentStarted 无后续 → agent_crash，detail.state=s2。
7. `test_validator_passed_falls_through` — **新增**，ValidatorFinished(passed=true) + AgentStarted 无后续 → agent_crash（不被 passed=true 误判为 validator_block）。
8. `test_unknown_and_empty` — 空列表 + 仅 WorkflowStarted 两种情形 → unknown，无异常。

`test_history_renderer.py` 用例清单：
- render_history 主干模式：含 Heartbeat/AgentOutput/ValidatorStarted 的混合输入 → 输出只含 MAIN_EVENT_TYPES。
- render_history `show_all=True`：所有事件出现。
- render_why 链式：构造 a→b→c 的 TransitionSelected 链 → 输出 `a → b → c`。
- render_why 防回环：a→b→a→c 的事件序列、target=c → 不死循环且输出可读。
- render_why 未找到：target=z 但无 TransitionSelected → 输出包含 `(进入源未知)` 或等效友好提示。

`test_retry_dry_run_diagnosis.py` 用例：
- 用 `MINIMAL_WORKFLOW_SNAPSHOT`（§二·4）+ tmp_path 写 `workflow_state.json` 与 `logs/events.jsonl`（含一条 ValidatorFinished(passed=false)）。
- 调用 `retry_run(run_id, run_root=..., dry_run=True)`。
- 断言：返回 `ok=True`，`steps` 中存在 `action=diagnose_last_failure` 且 `detail.kind=validator_block`。
- 第二组：events.jsonl 不存在 → 仍含 diagnose step，`detail.kind=unknown`，`retry_recommended=True`。

**验证命令**：
```
$env:PYTHONPATH='src;.'; pytest tests/unit/test_retry_diagnose.py tests/unit/test_history_renderer.py tests/unit/test_retry_dry_run_diagnosis.py -q
$env:PYTHONPATH='src;.'; pytest tests/unit -q
```

### 6. 风险与停止规则

（沿用 plan_doc-v1 §6，无新增风险。R3 已通过 §二·4 的 `MINIMAL_WORKFLOW_SNAPSHOT` 常量缓解。）

**停止规则补充**：若 `WorkflowConfig.from_dict` 拒绝 `MINIMAL_WORKFLOW_SNAPSHOT` → 检查实际 snapshot 的字段补全，不擅自修改 Config 模型。

### 7. 预期产物

- `src/agent_workflow/observability/history.py`（新增）
- `src/agent_workflow/state_machine/retry_diagnose.py`（新增）
- `src/agent_workflow/cli.py`（修改）
- `src/agent_workflow/state_machine/retry.py`（修改）
- `tests/unit/test_history_renderer.py`（新增）
- `tests/unit/test_retry_diagnose.py`（新增，**8 用例**）
- `tests/unit/test_retry_dry_run_diagnosis.py`（新增，2 用例）

不交付：新文档、新依赖、对事件 schema/runner/guard 的改动。

---

## 四、相对 plan_doc-v1 的关键变更摘要

1. **新增诊断子类 `KIND_GUARD_TIMEOUT`**：将 `max_duration_minutes` 从"未知"分流到独立 kind，retry_recommended=True；区别于 `max_visits`/`max_retries` 的 False。
2. **`_build_dry_run_steps` 增加空事件/类型守卫**：`isinstance(events, list)` + 空列表 → 直接产出 `kind=unknown` 的 step，避免依赖诊断函数的内部容错。
3. **`render_why` 形式化**：补完过滤表达式、取最后一条、`seen` 防回环、链路断开时的友好输出。
4. **测试夹具常量 `MINIMAL_WORKFLOW_SNAPSHOT`**：集中维护，降低 Config 模型变更对测试的扩散影响。
5. **新增 3 个诊断测试用例**：`max_duration_minutes` / 多 state crash / `passed=true` 不误判为 block；共扩到 8 用例。
6. **`MAIN_EVENT_TYPES` 移除 `ValidatorStarted`**：默认主干降噪，`--all` 仍可见。
7. **Step 数从 6 降为 5**：原 Step 5（独立文档/帮助文案）合并入 Step 4 的 CLI 注册。
8. **保留 `retry_diagnose.py` 独立模块**：拒绝合并提议，理由见 §二·10。
