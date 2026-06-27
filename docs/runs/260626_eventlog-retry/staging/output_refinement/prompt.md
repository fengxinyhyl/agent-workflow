## 目标

为 agent-workflow 引擎实现两个功能，改动限定在 src/agent_workflow/ 与 tests/。

功能一：history 命令——新增 CLI 子命令 `history`，读取 run 的 logs/events.jsonl，把已有事件渲染成带时间戳的因果时间线（WorkflowStarted→StateEntered→AgentStarted→TaskResultWritten→ValidatorFinished→TransitionSelected→...），让用户一眼看出每一步发生了什么、卡在哪。支持 `--why <state>` 反查：顺着 TransitionSelected 事件倒推某个状态是如何进入的。复用现有 observability/jsonl_sink.py 的 read_log 能力，不要引入数据库。

功能二：retry 失败诊断——增强现有 retry 逻辑（state_machine/retry.py），在重试前读取 event log 诊断上次失败的类型：若是 ValidatorFinished(passed=false) 导致，把 validator 的 errors 一并提示；若是 GuardFailed(max_visits/max_retries) 导致，判定为回流死循环、提示重试无意义而非盲目重跑；若是 AgentStarted 后无完成事件，判定为 Agent 进程崩溃、重试有意义。诊断结果体现在 dry-run 预览里。

约束：保持改动范围紧凑，遵守项目现有代码风格与中文注释惯例；不修改 TaskResult 瘦模型契约；为两个功能补充单元测试；不要触碰文件系统副作用回滚（那是 git/快照范畴，本次不做）。

## 当前任务

根据 output_review_doc（或 validation 返回的 test_report）修订代码。

必须逐条回应审核/测试意见，说明采纳/延后/不采纳及原因，
然后据此修改代码，保持改动紧凑。

输出 output_refinement_doc，记录本轮回应、实际修改文件、
执行命令和验证情况。使用 done 表示本轮修订完成。


输入: plan_doc:latest, execution_report:latest, output_review_doc:latest, test_report

期望输出: output_refinement_doc

## 已有产物流

- skill_adoption:planning: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\artifacts\skill_adoption_planning.md
- plan_doc: artifacts/plan_doc-v1.md
- skill_adoption:plan_review: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\artifacts\skill_adoption_plan_review.md
- plan_review_doc: artifacts/plan_review_doc-v2.md
- skill_adoption:plan_refinement: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\artifacts\skill_adoption_plan_refinement.md
- plan_refinement_doc: artifacts/plan_refinement_doc-v1.md
- skill_adoption:execution: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\artifacts\skill_adoption_execution.md
- execution_report: artifacts/execution_report.md
- skill_adoption:output_review: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\artifacts\skill_adoption_output_review.md
- skill_adoption:output_refinement: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\artifacts\skill_adoption_output_refinement.md
- output_refinement_doc: artifacts/output_refinement_doc-v2.md

状态历史: planning → plan_review → plan_refinement → plan_review → execution → output_review → output_refinement → output_review → output_refinement → output_review → output_refinement

## 技能指引

## 技能指引

### agent-workflow-lifecycle
> Agent Workflow 生命周期规则
# Agent Workflow Lifecycle

你是 agent-workflow 编排中的一个节点。

## 通用规则

1. 所有产物只写入 prompt 提供的 staging 路径。
2. 必须输出标准 TaskResult JSON。
3. 只输出当前节点 decision，不输出下一 state 名称。
4. 不读取 `.env`、密钥、凭证或生产敏感数据。
5. 不绕过 guard、validator、artifact promotion。
6. 如果缺少必要输入，输出 `blocked` 并说明缺口。


### code-implementation
> 编程执行节点规范
# Code Implementation Node

编程节点按已采纳计划执行代码变更。

## 规则

- 改动范围必须贴合 plan_doc 和 plan_refinement_doc。
- 发现计划不可执行时输出 `blocked`，不要擅自扩大范围。
- 记录修改文件、运行命令、偏差、未完成事项。
- 不覆盖用户未要求修改的工作。



## 输出格式要求

**重要：你必须在最后一条消息的末尾输出一个 ```json 代码块，内容为 TaskResult JSON 对象。**

TaskResult 的必需字段（其他字段见下面 schema）：

- `schema_version`: 固定为 1

- `task_id`: 当前 task 名称

- `state`: 当前 state 名称

- `status`: 执行状态（success/failed/blocked/timeout）

- `decision`: 语义决策（见下方允许的决策列表）

- `summary`: 人类可读的执行摘要

- `artifacts`: 产出物列表（每项包含 name/staging_path/type），可以为空数组

- `execution`: 执行元数据（started_at/finished_at/exit_code 等，引擎会覆盖）


示例输出（你的最后一条消息应以此格式结尾）：

```json

{

  "schema_version": 1,

  "task_id": "output_refinement",

  "state": "output_refinement",

  "status": "success",

  "decision": "blocked",

  "summary": "任务完成的简要描述",

  "artifacts": [],

  "execution": {"started_at": "", "finished_at": "", "exit_code": 0}

}

```


完整 schema 参考（所有字段的详细说明）：

<details>

<summary>点击展开 JSON Schema</summary>


```json

{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "TaskResult",
  "description": "Agent 任务执行结果的标准格式。所有 Agent 必须按此格式输出。",
  "type": "object",
  "required": [
    "schema_version",
    "task_id",
    "state",
    "status",
    "decision",
    "summary",
    "execution"
  ],
  "properties": {
    "schema_version": {
      "type": "integer",
      "description": "TaskResult schema 版本号，当前为 1",
      "const": 1
    },
    "task_id": {
      "type": "string",
      "description": "任务标识，与 workflow 中 task name 一致",
      "examples": [
        "review_plan",
        "execute",
        "audit"
      ]
    },
    "state": {
      "type": "string",
      "description": "执行此 task 时的 state 名称"
    },
    "agent": {
      "type": "string",
      "description": "执行 Agent 名称"
    },
    "status": {
      "type": "string",
      "enum": [
        "success",
        "failed",
        "blocked",
        "cancelled",
        "timeout",
        "invalid_output"
      ],
      "description": "执行状态"
    },
    "decision": {
      "type": "string",
      "description": "语义决策（允许值: done, fail, blocked）",
      "enum": [
        "done",
        "fail",
        "blocked"
      ]
    },
    "summary": {
      "type": "string",
      "description": "人类可读的执行摘要"
    },
    "artifacts": {
      "type": "array",
      "description": "产出物列表",
      "items": {
        "type": "object",
        "required": [
          "name",
          "staging_path",
          "type"
        ],
        "properties": {
          "name": {
            "type": "string",
            "description": "产物名称（与 workflow outputs 对应）"
          },
          "staging_path": {
            "type": "string",
            "description": "staging 区路径（Agent 只能写此路径）"
          },
          "artifact_path": {
            "type": "string",
            "description": "预期的正式 artifact 路径（扁平结构，如 artifacts/plan_doc.md，禁止包含子目录）"
          },
          "type": {
            "type": "string",
            "enum": [
              "markdown",
              "json",
              "yaml",
              "code",
              "other"
            ],
            "description": "产物类型"
          }
        }
      }
    },
    "execution": {
      "type": "object",
      "description": "执行元数据（必填）",
      "required": [
        "started_at",
        "finished_at",
        "exit_code"
      ],
      "properties": {
        "started_at": {
          "type": "string",
          "description": "任务开始时间（ISO 8601）"
        },
        "finished_at": {
          "type": "string",
          "description": "任务完成时间（ISO 8601）"
        },
        "duration_seconds": {
          "type": "number",
          "description": "执行耗时（秒）"
        },
        "attempt": {
          "type": "integer",
          "description": "当前尝试次数",
          "default": 1
        },
        "exit_code": {
          "type": "integer",
          "description": "进程退出码"
        },
        "pid": {
          "type": "integer",
          "description": "子进程 PID"
        }
      }
    },
    "issues": {
      "type": "array",
      "description": "发现的问题列表",
      "items": {
        "type": "object",
        "required": [
          "severity",
          "title"
        ],
        "properties": {
          "severity": {
            "type": "string",
            "enum": [
              "blocking",
              "warning",
              "info"
            ]
          },
          "title": {
            "type": "string",
            "description": "问题简述"
          },
          "detail": {
            "type": "string",
            "description": "问题详情"
          }
        }
      }
    },
    "next_inputs": {
      "type": "object",
      "description": "传递给下一状态的输入数据（可选）"
    },
    "session_id": {
      "type": "string",
      "description": "CLI session/thread ID（对齐 legacy WorkerResult）"
    },
    "token_usage": {
      "type": "object",
      "description": "token 使用统计（Claude: cache_read_input_tokens; Codex: cached_input_tokens, reasoning_output_tokens）",
      "additionalProperties": true
    },
    "log_path": {
      "type": "string",
      "description": "stream 日志落盘绝对路径"
    },
    "packet_path": {
      "type": "string",
      "description": "debug packet 绝对路径"
    }
  }
}

```

</details>

## 输出路径

- output_refinement_doc: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\staging\output_refinement\output_refinement_doc.md
- task_result: F:\code\agent-workflow\docs\runs\260626_eventlog-retry\staging\output_refinement\task_result.json

⚠️ 所有输出必须写入 staging 路径，禁止直接写 artifacts。

⚠️ **产物登记契约（务必遵守，否则任务会校验失败）**：
1. 你在 TaskResult 的 `artifacts` 列表里声明的每一个产物，都必须先用 Write 工具把对应文件真实写入它的 `staging_path`。
2. 先写文件，再登记——不要声明一个尚未落盘的产物。
3. 没有实际产出文件的产物，就不要写进 `artifacts` 列表（留空数组即可）。
4. 上面列出的输出路径是引擎期望的产物，请按需逐个写入并登记。
5. `artifact_path` 必须是扁平路径 "artifacts/<输出名>.md"，不要包含子目录（如禁止 artifacts/plan/output.md），输出名取 `staging_path` 的文件名即可（如 plan_doc.md → "artifacts/plan_doc.md"）。


⚠️ **本任务的 `decision` 字段必须从以下值中选择一个**：blocked, done, fail。不要使用列表之外的值（例如不要用 done 代替 approve）。
