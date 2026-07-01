# Runtime v2 第 1 步 — output_refinement（第 1 轮）

针对 `output_review_doc-v1`（decision: revise）的 2 个阻塞问题逐条回应并修订。

## 一、逐条回应审核意见

### 阻塞 1：`decision=None` 与 TaskResult JSON Schema 不一致

**采纳。**

审核指出：`to_dict()` / parser fallback 现在真实输出 `"decision": null`，但 schema 里
`decision` 仍是 `{"type": "string"}`——"字段可省略"成立，但"字段存在且为 null"不符合 schema，
造成 AgentInput 下发的 schema 与 Runtime 自产 TaskResult 的契约冲突。意见准确，已修订。

修改 `src/agent_workflow/tasks/result_schema.py`：

- `TASK_RESULT_SCHEMA["properties"]["decision"]["type"]` 由 `"string"` 改为 `["string", "null"]`，
  使 `decision=null` 合法。
- `build_task_result_schema(allowed_decisions)`：注入 enum 时由 `enum = allowed_decisions`
  改为 `enum = list(allowed_decisions) + [None]`，并同步 `type=["string","null"]`。
  这样有 allowed_decisions 的分支节点既限定业务取值，又不与"decision 非必填 / 可为 null"冲突
  （省略或显式 null 均通过校验）。
- 无 allowed_decisions 时维持不注入 enum（自由字符串 + null），与既有
  `test_build_schema_without_allowed_decisions_no_enum` 一致。

### 阻塞 2：Agent prompt 仍把 `decision` 作为必填，并在无 allowed_decisions 时示例 `done`

**采纳。**

审核指出：`result_schema.py` 已把 decision 移出 required，但 `AgentInput.build_prompt()`
的"必需字段"清单仍无条件列出 decision，且示例在无 allowed_decisions 时硬编码
`"decision": "done"`，会继续把线性/无分支任务诱导为输出 `done`，与"decision Optional /
Parser 不再兜底 done / Runtime 不认识业务词"方向冲突。意见准确，已修订。

修改 `src/agent_workflow/context/agent_input.py` `build_prompt()` 第 5 段：

- 在生成段落前先计算 `effective_decisions`：优先取 `skill_policy.allowed_decisions`，
  否则回退到 schema 中 `decision` 字段注入的 enum（剔除 None）。
- 必需字段说明：`effective_decisions` 非空时保留"见下方允许的决策列表"；为空时改为
  "语义决策（可选；本任务无分支决策，可省略或置为 null）"。
- 示例 JSON：仅当 `effective_decisions` 非空时才输出 `"decision": "<第一个允许值>"` 行；
  为空时整行省略，不再硬编码 `done`。
- 删除原来 `example_decision = "done"` 的兜底逻辑。

**范围说明**：本步 goal 的"改造点"列表未显式点名 `agent_input.py`，但 prompt 是 Runtime
下发给 Agent 的契约文本，与 result_schema/Parser 同属"契约层"；此处诱导 `done` 与第 1 步
目标直接冲突，故纳入本轮修订。改动仅限 prompt 文案与示例分支，未触碰路由模型
（machine.py）、Validator 纯函数化、_loop 适配等后续步骤范围。

### 已核对项（无需改动）

审核"已核对上一轮建议"与"范围与正确性"两节确认：`get_decision()` 注解、
`_extract_task_result_fallback` 命中条件、显式错误分支 `decision=None` 测试、
`VALID_DECISIONS` 清零、Claude/Codex fallback → `invalid_output`/`None` 均已落地，
本轮不重复处理。

## 二、实际修改文件

| 文件 | 改动 |
| --- | --- |
| `src/agent_workflow/tasks/result_schema.py` | decision type 改 `["string","null"]`；build 时 enum 追加 None、type 同步 |
| `src/agent_workflow/context/agent_input.py` | build_prompt 按 effective_decisions 条件化 decision 必填说明与示例，去除 done 兜底 |
| `tests/unit/test_task_result_v4.py` | 更新 `test_build_schema_with_allowed_decisions`（enum 含 None、type 校验）；新增 `test_decision_schema_accepts_null` |
| `tests/unit/test_run_context.py` | 新增 `test_build_prompt_no_allowed_decisions_no_done_example`、`test_build_prompt_allowed_decisions_example_uses_first` |

## 三、执行命令

```powershell
# 语法编译
python -m py_compile src\agent_workflow\tasks\result_schema.py src\agent_workflow\context\agent_input.py

# 相关单元测试
PYTHONPATH='src;.' python -m pytest tests/unit/test_task_result_v4.py tests/unit/test_run_context.py tests/unit/test_parser_fallback.py -q -p no:cacheprovider

# 全量
PYTHONPATH='src;.' python -m pytest tests -q -p no:cacheprovider
```

## 四、验证情况

- `py_compile`：通过。
- 相关单元测试（schema / agent_input prompt / parser fallback）：**43 passed**。
- 全量：**33 failed, 297 passed, 22 skipped**。

对照基线（execution_report / output_review 记录的 `33 failed, 294 passed`）：
失败数保持 33 不变，passed 由 294 增至 297，增量恰为本轮新增的 3 个测试——
**未引入任何新增回归**。

残余 33 个失败项均为审核已确认的既有问题，不属本步范围：

- `test_schema_contract.py`（22+ 项）：`schemas/*.schema.json` 物理文件缺失。
- `test_spec_dev_flow` / `test_system_architecture_flow`：workflow fixture / agents.yaml 不一致。
- `test_negative.py::test_decision_not_in_allowed_decisions_warning`：契约收敛后
  Runtime 不再用全局白名单判 decision，该用例的旧 warning 预期需在后续路由层步骤随
  Validator 调整一并更新。
- `test_negative.py::test_cancel_run_writes_file`：既有 cancel 路径问题，与本步无关。

## 五、本轮结论

2 个阻塞问题均已采纳并修订完毕，契约（result.py / result_schema.py / agent_input.py prompt）
与 Parser 在"decision Optional、可为 null、无分支不诱导 done"上达成一致，无新增回归。
decision: **done**。
