# 实现计划：history 命令 + retry 失败诊断

## 1. 需求理解

本次交付两个互相独立、但都依赖 `logs/events.jsonl` 事件流的功能：

**功能一 — `history` 子命令**：把单次 run 的事件序列渲染成"带时间戳的因果时间线"，让用户一眼看清 `WorkflowStarted → StateEntered → AgentStarted → TaskResultWritten → ValidatorFinished → TransitionSelected → ...` 这条主因果链上每一步发生了什么、卡在哪里。同时支持 `--why <state>` 反查：找出该 state 是因为哪条 `TransitionSelected` 进入的（即"上一个 state 给出了什么 decision 才走到这里"），并向上追溯到 WorkflowStarted 形成因果链。

**功能二 — retry 失败诊断**：在现有 retry dry-run 预览中额外插入一项"上次失败诊断"。诊断逻辑读取同一份 events.jsonl，识别三类典型失败：

- **Validator 阻塞失败**：最后一条 `ValidatorFinished{passed=false}` 的 errors 字段非空 → 提示具体校验错误，重试有意义但需先修产物质量。
- **Guard 回流死循环**：最后一条 `GuardFailed{guard_type ∈ {max_visits, max_retries}}` → 提示重试无意义（再重试还会撞同一道墙），建议改流程或提高阈值。
- **Agent 进程崩溃**：最近一条 `AgentStarted` 之后没有对应的 `TaskResultWritten` / `ValidatorFinished` / `TransitionSelected` → 推断 Agent 进程异常终止，重试有意义。
- **其它/未知**：没有任何上述模式或事件文件缺失 → 诊断结论"未知"，仍允许重试预览。

诊断结果以一条 step（`action=diagnose_last_failure`）插入到现有 dry-run steps 列表前段，并在 CLI 输出中突出展示。

**验收标准**：
- `agent-workflow history -r <run_id>` 可输出因果时间线。
- `agent-workflow history -r <run_id> --why <state>` 输出该 state 的进入原因链。
- `agent-workflow retry -r <run_id>` dry-run 输出中包含 `diagnose_last_failure` step，结论字段（kind, reason, retry_recommended）准确。
- 单元测试覆盖三类诊断路径与 history 渲染。

**歧义点（已用合理默认决议，无需阻塞）**：
- "因果链"对 history 默认渲染范围：默认渲染全部"主干事件"（StateEntered/AgentStarted/TaskResultWritten/ValidatorFinished/TransitionSelected/GuardFailed/WorkflowStarted/Workflow{Completed,Failed,Cancelled}），跳过 Heartbeat/AgentOutput 这类高噪声事件，必要时通过 `--all` 显示全部。
- `--why <state>` 当目标 state 在该 run 中被多次进入时：默认显示**最近一次**的进入链；保留打印总进入次数提示，避免歧义。
- 诊断"AgentStarted 后无完成事件"的判定窗口：以 events.jsonl 末尾为准；如果 AgentStarted 是文件中的最后一条事件，或后续仅有 Heartbeat，就判定为崩溃；只要出现 ValidatorFinished / TransitionSelected / TaskFinished 即视为正常结束。

## 2. 目标和非目标

**目标**：
- 新增 `history` CLI 子命令与底层渲染函数。
- 在 `retry.py` 中新增 `diagnose_last_failure()` 并接入现有 dry-run 流。
- 为以上两个功能补单元测试。
- 复用 `observability/jsonl_sink.read_log()` 解析事件，不引入数据库或新依赖。

**非目标**：
- 不修改 TaskResult 瘦模型契约。
- 不新增事件类型，不改 EventBus / sink 行为。
- 不做"重试时滚回文件系统副作用"（明确归 git/快照范畴）。
- 不重写 explain / status 命令；history 是新增视角，不替代它们。
- 不引入 TUI/分页或彩色输出框架；保持 `safe_print` 朴素文本风格。

## 3. 涉及文件和模块边界

**新增**：
- `src/agent_workflow/observability/history.py` — 渲染因果时间线 + `--why` 反查的纯函数。
  - 入口 `render_history(run_id, run_root, show_all=False) -> str`
  - 入口 `render_why(run_id, run_root, target_state) -> str`
  - 私有助手 `_filter_main_events(events)`、`_format_event_line(event)`
- `src/agent_workflow/state_machine/retry_diagnose.py` — 故障诊断纯函数（与 retry 流程解耦便于测试）。
  - 入口 `diagnose_last_failure(events) -> dict`，返回 `{kind, reason, retry_recommended, detail}`
  - 内部常量 `KIND_VALIDATOR_BLOCK / KIND_GUARD_LOOP / KIND_AGENT_CRASH / KIND_UNKNOWN`

**修改**：
- `src/agent_workflow/cli.py`
  - 新增 `cmd_history(args)` 与子命令注册（`history`）。
  - `cmd_retry()` 输出诊断 step 时给出更友好的提示行（不改变现有 step 顺序，只为新插入的 step 增加格式分支）。
- `src/agent_workflow/state_machine/retry.py`
  - `_build_dry_run_steps()` 末尾或重试起点解析之后，调用 `diagnose_last_failure()` 并插入新 step（action=`diagnose_last_failure`，status=`info` / `would_block`）。
  - 加载事件用现有 `read_log(run_id, run_root=...)`，不接管文件 I/O。

**测试新增**：
- `tests/unit/test_history_renderer.py`
- `tests/unit/test_retry_diagnose.py`
- `tests/unit/test_retry_dry_run_diagnosis.py`（轻量集成：构造内存事件 jsonl → 调用 `retry_run(dry_run=True)` → 断言 steps 含诊断）

**不动**：observability/events.py, jsonl_sink.py (只读取), runner.py, guard.py, TaskResult 模型。

## 4. 分步骤实现方案

每一步都可独立验证（运行对应单元测试或手工跑命令）。

**Step 1 — 建立诊断纯函数**
- 新建 `state_machine/retry_diagnose.py`。
- 实现 `diagnose_last_failure(events)`：
  - 先扫描末段最后一条 `ValidatorFinished{passed=false}`，若存在 → `KIND_VALIDATOR_BLOCK`，提取 `payload.errors`、`state`。
  - 否则扫描最后一条 `GuardFailed`，若 `guard_type ∈ {max_visits, max_retries}` → `KIND_GUARD_LOOP`，retry_recommended=False。
  - 否则找最后一条 `AgentStarted`，检查其后是否存在 `TaskResultWritten`/`ValidatorFinished`/`TransitionSelected`/`TaskFinished`（忽略 Heartbeat/AgentOutput）；若无 → `KIND_AGENT_CRASH`，retry_recommended=True。
  - 否则 `KIND_UNKNOWN`。
- 单元测试 `test_retry_diagnose.py`：四类用例 + 空事件列表用例。

**Step 2 — 接入 retry dry-run**
- 在 `retry._build_dry_run_steps()` 中：
  - 调用 `read_log(context.run_id, run_root=context.run_root)` 读事件。
  - 调用 `diagnose_last_failure(events)`。
  - 在 `resolve_from_state` 之后插入新 step：`action=diagnose_last_failure`, `status` 根据 `retry_recommended` 取 `ok` 或 `would_block`, `detail` 装 `{kind, reason, retry_recommended, errors|guard_type}`。
- 在 `cli.cmd_retry` 的输出循环里为该 action 增加一行格式化（如 `      kind: validator_block; retry_recommended: True`）。
- 单元测试 `test_retry_dry_run_diagnosis.py`：用 tmp_path 造 `workflow_state.json`（含最小 `_workflow_snapshot`）+ `logs/events.jsonl`，跑 `retry_run(dry_run=True)`，断言 steps 中包含 diagnose 项且字段正确。

**Step 3 — history 渲染器**
- 新建 `observability/history.py`：
  - `MAIN_EVENT_TYPES` 白名单常量（含 WorkflowStarted/StateEntered/AgentStarted/TaskResultWritten/ValidatorStarted/ValidatorFinished/ArtifactPromoted/TransitionSelected/GuardFailed/TaskFinished/WorkflowCompleted/WorkflowFailed/WorkflowCancelled/SkillAdoptionWritten）。
  - `render_history(run_id, run_root, show_all=False)`：调用 `read_log`，按时间顺序过滤后用 `_format_event_line` 渲染；每行含 `[timestamp]`、event、state、关键 payload 字段（如 decision/passed/next_state/agent）。
  - `render_why(run_id, run_root, target_state)`：从最后一条进入 target_state 的 `TransitionSelected{next_state=target_state}` 倒推上一个 state；再找该上一个 state 自己的进入 TransitionSelected，循环直到追溯到 WorkflowStarted 或链路断开；输出"…→…"链。
- 单元测试 `test_history_renderer.py`：构造内存事件列表，验证主干渲染与 `--why` 链。

**Step 4 — 接入 CLI**
- `cli.py` 中：
  - 新增 `cmd_history(args)`：复用 `_find_run_root` 解析 run_root；调用 history 渲染器，`safe_print` 输出。
  - `build_parser` 中注册 `history` 子命令：`--run-id/-r`（必填）、`--why <state>`、`--all`、`--project-root/-p`、`--run-root`。
- 手工验证：在现有 `docs/runs/<某个 run>` 上跑 `agent-workflow history -r <id>` 与 `--why`。

**Step 5 — 文档/帮助文案**
- 仅在 argparse `help=` 文案里补一两行，保持现有风格，不新写 markdown 文档。

**Step 6 — 全量回归**
- `pytest tests/unit -q`，确认新测试通过且未影响存量测试。

## 5. 测试策略

**单元测试覆盖点**：

- `test_retry_diagnose.py`
  - validator_block：构造 `ValidatorFinished{passed=false, errors=[...]}` → 断言 kind / reason / errors 透传。
  - guard_loop：构造 `GuardFailed{guard_type='max_visits'}` → retry_recommended=False。
  - guard_loop with `max_retries` 同上。
  - agent_crash：构造 `AgentStarted` 后无完成事件（仅 Heartbeat 跟随） → kind=agent_crash，retry_recommended=True。
  - unknown：仅 WorkflowStarted/StateEntered，无失败信号 → kind=unknown。
  - empty events → kind=unknown，不抛异常。

- `test_history_renderer.py`
  - render_history 主干模式：传入混合事件（含 Heartbeat / AgentOutput），断言输出只含主干。
  - render_history `show_all=True`：所有事件都出现。
  - render_why：构造 a→b→c 的 TransitionSelected 链，`render_why(..., target='c')` 输出 `a → b → c`。
  - render_why 找不到 target state 的进入：输出友好提示而非抛异常。

- `test_retry_dry_run_diagnosis.py`
  - 用 tmp_path 构造最小可加载 RunContext（必填 `_workflow_snapshot`，含 1 个 state）+ `logs/events.jsonl`。
  - 跑 `retry_run(run_id, run_root=..., dry_run=True)`。
  - 断言返回 `steps` 中含 `action == 'diagnose_last_failure'`，detail.kind 与构造的失败类型匹配。

**验证方式**：
```
$env:PYTHONPATH='src;.'; pytest tests/unit/test_retry_diagnose.py tests/unit/test_history_renderer.py tests/unit/test_retry_dry_run_diagnosis.py -q
$env:PYTHONPATH='src;.'; pytest tests/unit -q
```

## 6. 风险与停止规则

**风险**：
- R1 — events.jsonl 格式漂移：上游若改动事件字段名（如 payload 嵌套），诊断/渲染会失配。对策：所有字段访问用 `.get()` 防御性默认值，并在 history 渲染未识别事件时优雅降级为 `[ts] EventName ...`。
- R2 — 大 run 文件性能：events.jsonl 可能很长。对策：history 默认主干过滤；不引入流式分页，但避免 O(n²) 操作（render_why 倒推时记录 `seen_state` 防回环）。
- R3 — Windows 终端编码：沿用 `safe_print`，输出文本中文混排不引入彩色/Unicode 边框。
- R4 — 诊断误判：AgentStarted 后存在事件但属于上一个 state 的尾巴（理论上 EventBus 顺序保证不会发生，但仍校验"AgentStarted 之后是否出现属于同一 state 的完成信号"以增强稳健性）。

**停止规则**：
- 单一 step 实现尝试两次仍失败 → 停下来检查事件 schema 假设是否成立，不再小步打补丁。
- 任何修改触发 retry 现有测试失败且根因不是新逻辑 → 立即回滚相应改动并复核对 `_build_dry_run_steps` 的接入方式。
- 如果发现 events.jsonl 缺少诊断所需关键字段（如 errors 不在 ValidatorFinished payload）→ 仅在诊断结果里降级为 "kind=validator_block, errors=unknown"，不擅自修改 emit 处。

## 7. 预期产物

- `src/agent_workflow/observability/history.py`（新增）
- `src/agent_workflow/state_machine/retry_diagnose.py`（新增）
- `src/agent_workflow/cli.py`（修改：注册 history、retry 输出格式微调）
- `src/agent_workflow/state_machine/retry.py`（修改：dry-run 接入诊断）
- `tests/unit/test_history_renderer.py`（新增）
- `tests/unit/test_retry_diagnose.py`（新增）
- `tests/unit/test_retry_dry_run_diagnosis.py`（新增）

不交付：新文档 .md、新依赖、对事件 schema/runner/guard 的改动。
