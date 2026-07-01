# Runtime v2 设计：status / decision / output 三层职责分离

## 背景与问题

当前工作流在执行任务时，Agent 返回的 `decision` 经常不符合预期，导致校验器拒绝、流程意外落入 `default → failed`。

根因不是偶发 bug，而是 `decision` 字段同时承载了两条正交语义轴：

- **生命周期轴**：`done / fail / blocked / no_op`（我这一步干完没干完）
- **评判轴**：`approve / revise / reject`（评审结论是什么）

一个 reviewer 跑完，从"我完成了"的角度天然倾向输出 `done`，但工作流要的是评判轴的 `approve/revise/reject`。叠加以下三处放大效应，错配频繁发生：

1. **路由寄生在 LLM 的自由发挥里**：本可确定性推导的"有 blocking issue 就该 revise/reject"，依赖模型自己想清楚再选对 token。
2. **解析端兜底默认 `done`**：`_parse_stream_output` 最终 fallback 与 `_extract_task_result_fallback` 在输出格式稍有偏差时，一律兜成 `done`/`success`，绕过 schema enum 约束，把假成功伪装成成功。
3. **校验是"一次否决"而非"约束 + 自愈"**：decision 不合法即 blocking，Runner 直接改写为 `fail` 走 default，模型没有纠正机会。

## 设计目标

本次改造的核心价值**不是修好 decision**，而是完成 Runtime 契约的三层职责分离。把 `decision` 承载的两条轴拆开，让每一层只认识自己的词。

| 层 | 唯一关心的字段 | 词表归属 | 对其他层 |
|---|---|---|---|
| **Runtime** | `status` | Runtime 固定枚举 | 不认识 decision，不认识 output |
| **Workflow** | `decision` | 每个 task 的 `allowed_decisions` | 不解析 output 语义 |
| **Business** | `artifacts` / `summary` | 完全自由 | 对上两层透明 |

- **`status`**：Agent 是否完成本次任务。全 Runtime 统一，每个节点都有。
- **`decision`**：仅"需要业务分支"的节点才有。表达下一步走哪条分支。
- **`output`**：业务产物，对 Runtime 与 Workflow 完全透明。

## 核心原则

### 原则一：Runtime 不认识业务词（删除 VALID_DECISIONS）

`decision` 一旦成为 workflow 词表（`approve/revise/reject`、`retry/skip/rollback`、`accept/discard/merge`……），Runtime 保留任何全局 decision 白名单都是层泄漏。

- **删除** Runtime 层的 `VALID_DECISIONS`。
- Validator 只做 `decision in task.allowed_decisions`，Runtime 对 `approve` 一无所知。
- `get_decision()` 可保留大小写/空白归一化（纯字符串处理），但删掉一切对全局集合的校验。
- 全局 schema 里 `decision` 在无 `allowed_decisions` 时退化为自由字符串；有 `allowed_decisions` 时才注入 enum。schema 自身表达"这是不是分支节点"。

### 原则二：路由只看结构，不看节点类型

Runtime 不应判断 `is_review` / `is_gate` / `is_execution`——那是把业务知识塞进 Runtime。改用**结构存在性**（有没有 `on`）替代**类型语义**。

以后任何节点写 `on: {yes, no, retry}` 都自动成立，不需要新增节点类型。

### 原则三：status 分两个层级

| 层级 | 取值 | 说明 |
|---|---|---|
| **Runtime 内部态**（Parser/Agent 可产出） | `success / failed / blocked / invalid_output` | Parser 解析失败产出 `invalid_output` |
| **可路由态**（到达 `resolve_transition` 的） | `success / failed / blocked` | `invalid_output` 永不进入路由 |

`invalid_output` 是 Runtime 内部瞬时态，**不暴露给 Workflow**。它的生命周期被夹在 **Parser → Repair** 之间，在 Runner 的 repair 闸口被消解为 `success`（修复成功）或 `failed`（修复耗尽），绝不出现在任何 YAML 的 `on_status` 里。

> 取证要求：repair 耗尽后置 `status=failed`，但需在 `issues` 里保留一条 `originally=invalid_output, repair_exhausted` 记录，否则排障时解析失败与真实任务失败会混淆。

### 原则四：成功路径用单出口 `next`，`on_status` 退化为可选覆盖

结合原则三（无 invalid_output）与"成功不走 on_status"：`on_status` 只剩 `failed`/`blocked`，而两者通常都等于 `default`，于是 `on_status` 退化为极少用的可选覆盖，绝大多数节点不写。

成功路径改用单出口 `next`。

## 路由模型

### 关键词表

| 关键字 | 用途 | 适用节点 |
|---|---|---|
| `next` | 成功后的唯一后继 | 线性/执行节点 |
| `on` | `decision → 后继` 映射 | 分支节点 |
| `on_status` | `status → 后继`，**可选**，仅当 `blocked` 想去到不同于 `failed` 处 | 任意 |
| `default` | 兜底 | 任意非终止节点 |

一个非终止节点必须恰好定义一条成功路径：`on`（分支）或 `next`（线性），二选一。

### 最小 YAML 示例

```yaml
# 执行节点：只有成功路径，失败全归 default
execute:
  task: execute
  next: summary
  default: failed

# 分支节点：success 进 decision，失败归 default
plan_review:
  task: plan_review
  on: { approve: execute, revise: plan_refine, reject: failed }
  default: failed
```

### 唯一的路由真相（resolver 伪代码）

```
resolve(status, decision, state):
    if status != "success":              # 只可能是 failed / blocked
        return state.on_status.get(status) or state.default
    elif state.on:                        # 分支节点（decision 已在上游校验/修复）
        return state.on.get(decision) or state.default
    elif state.next:                      # 线性节点
        return state.next
    else:
        return state.default              # 配置疏漏，由 validate 期拦截
```

Runtime 全程只看 `status != success` 与 `state.on / state.next` 的存在性，不碰任何业务词。

## Validator：纯函数 + Runner 编排 Repair

Validator 一旦能调 Agent，就会和 Claude/Codex/Shell/Python 全部 task 类型耦合死。正确分工：

```
Validator(data, 节点路由形态) → ValidationResult{ valid, repairable, reason }   # 纯函数
Runner: 读 ValidationResult → repairable? → 编排 Repair（有界）→ 路由           # 编排
```

`ValidationResult` 三态：

| 状态 | 含义 | Runner 动作 |
|---|---|---|
| `valid=True` | 通过 | 直接路由 |
| `valid=False, repairable=True` | decision ∉ allowed_decisions，或解析出 invalid_output | 进入 Repair（有界 1~2 次） |
| `valid=False, repairable=False` | 不可救（exit 127 二进制缺失、进程崩溃） | 直接 failed |

- Validator 判断"decision 是否必填"需要知道**该节点有没有 `on`**（success 且有 `on` 时 decision 才必填）。把节点路由形态作为入参传入，Validator 仍是纯函数（入参 → 裁决）。
- Repair prompt 模板明确：只允许重新输出 `status` 与 `decision`，禁止修改 `summary`/`issues`/`artifacts`。
- Repair 必须与 `guards.max_retries`、`record_state_visit`/`get_attempt` 协调，避免与现有重试计数冲突。

## validate 期护栏

成功路径拆成 `next`/`on` 后，多了两种"成功却被静默判死"的写法错误，必须在 `validate-state-machine` 阶段拦截，而非运行时暴露：

1. **缺失成功出口**：非终止节点必须恰好定义一条成功路径（`on` 或 `next`）。否则 success 落到 `default=failed`——成功任务被路由到失败且无报错。
2. **decision 必填一致性**：节点有 `on` ⇒ 其 `allowed_decisions` 非空且 `on` 的键覆盖 `allowed_decisions`；节点有 `next`（无 `on`）⇒ task 不应声明 `allowed_decisions`。

两条均为纯静态检查，不增加 Runtime 复杂度，把"这是不是分支节点"在配置期一次性钉死。

## 模块改造清单

| 模块 | 改造内容 |
|---|---|
| `tasks/result.py` | **删除** `VALID_DECISIONS`；`decision` 默认值由 `"done"` 改为 `None`/Optional；`VALID_STATUSES` 标注"可路由子集 = success/failed/blocked，invalid_output 仅内部"；`validate()` 中 decision 为空不报错 |
| `tasks/result_schema.py` | `decision` 移出 `required`；无 `allowed_decisions` 时 decision 为自由字符串，有时注入 enum 并在 description 标注"本任务无需 decision" |
| `config/models.py` `StateModel` | 增 `next`（成功单出口）+ 可选 `on_status`（仅 failed/blocked）；**不引入 success 键**；`to_dict` / `WorkflowConfig.from_dict` 同步序列化（影响 `_workflow_snapshot` 恢复、status/explain） |
| `config/loader.py` `load_state` | 读 `next`/`on`/`on_status`；**旧 YAML 归一**：`done→next`、`fail/blocked→默认丢弃或 on_status`，`approve/revise/reject` 保留在 `on` |
| `state_machine/machine.py` | `resolve_transition` 改两段式，**仅按 `on`/`next` 存在性**；新增两条 validate 护栏；非终止节点须覆盖可路由 status 的去向 |
| `state_machine/transition.py` | `TransitionResult` 增 `status` 字段与 `route_by: "status" \| "decision"`，`to_event_dict` 带上 |
| `validators/task_result.py` | 改**纯函数**，返回 `ValidationResult{valid, repairable, reason}`，入参带节点路由形态；不调用 Agent；分 Runtime 层（status、必需字段、execution）与 Workflow 层（decision ∈ allowed_decisions） |
| `state_machine/runner.py` | **Runner 编排 Repair**（替换现 `runner.py:384-412` 的强制 fail 逻辑）；`invalid_output` 在此闸口消解；耗尽→failed 且 issues 留取证；`_create_error_result` 的 `decision="fail"` 改 `decision=None`+`status="failed"`；主循环路由改两段式调用 |
| `agents/claude_cli.py` / `agents/codex_cli.py` | 两份同构的 `_parse_stream_output` / `_parse_task_result_text` / `_extract_task_result_fallback` 一起改（建议抽共享模块）：最终 fallback 不再返回 `success`/`done`，改为 `status=invalid_output, decision=None`；超时/取消分支 decision 置 None，靠 status 路由 |
| `_loop` 展开（`loader.py:156-326`） | 线性 vs 分支改靠 `next` vs `on` 结构区分，不再靠猜 `done/revise/approve` 键名；展开规则从"猜键名"变成"按字段类型分派" |
| `observability/status.py` / `explain.py` | 兼容 `next`/`on_status`，展示路由依据 |
| `agents/mock.py` | `decision_script` 机制扩展为能返回新模型的 `status`（演示 invalid_output→repair 回流） |
| `tests/` | 新增：invalid_output→repair、decision 非法→repair、新旧格式归一、缺失成功出口护栏、loop 新旧节点混合 等用例 |

## 兼容与迁移策略

现有 13+ 个 `workflow.yaml` 全部用 `on: {done, fail, blocked}` 旧格式。采用**渐进路线**（loader 自动归一）：

- 检测到 `on` 里含 `done/fail/blocked` 生命周期 key 时，自动映射：`done→next`、`fail→failed`、`blocked→blocked`（后两者通常即 `default`）；`approve/revise/reject` 保留在 `on`。
- 存量 YAML **零改动**即可在新 Runtime 跑通，新 YAML 用 `next`/`on`/`default` 新写法。

`cancelled`/`timeout` 两个旧 status：Runtime 内部仍可产生，但路由层归一到 `blocked`/`failed`（`cancelled` 由取消路径处理，不进路由），保持可路由枚举为 3 个。

> 注：本设计放弃先前讨论过的 `transitions: {status:{...}, decision:{...}}` 嵌套块写法——嵌套块里写 `status.success` 正是要消灭的重复。改用平铺 `next`/`on`/`default`，Runtime 仍只维护一种路由模型，YAML 更省。

## 实施顺序（4 个可独立验证的步骤）

1. **契约收敛 + Parser 兜底**：删 `VALID_DECISIONS`、`decision` Optional、status 双层级、Parser 不再造 `done`。最低风险，单独即缓解现状。
2. **路由模型 + loader 旧格式归一**：`next`/`on`/`default` + 两条 validate 护栏；存量 13 个 YAML 零改动跑通。
3. **Validator 纯函数化 + Runner Repair 闸口**：invalid_output 在此消解，取证留痕。
4. **`_loop` 适配 + 文档 + 新 YAML 范式**。

## 设计收口

五条修正的共同效果是让 **Runtime 彻底不认识业务**——它只判断 `status` 和"有没有出口"，decision 的词表、含义、合法性全部下沉到 workflow 配置，output 完全透明。这是本次改造区别于"只加一个 status 字段"的根本价值，够格作为 Runtime v2 的基线设计。
