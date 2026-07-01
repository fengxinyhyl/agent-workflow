# Runtime v2 第 1 步执行结果审查

## 审查结论

**decision: revise**

本次实现基本覆盖了计划中的核心改造：删除 Runtime 全局 `VALID_DECISIONS`，将 `TaskResult.decision` 改为 Optional，Parser 不再在无结构化输出时伪造 `success/done`，并抽出了共享 parser 模块。新增的 parser 与 TaskResult 契约测试单独通过。

但仍有 2 个需要回到 refinement 的契约遗漏，都会影响“decision Optional / Runtime 不认识业务词”的第一步落地完整性。

## 阻塞问题

### 1. `decision=None` 与 TaskResult JSON Schema 不一致

- 文件：[src/agent_workflow/tasks/result_schema.py](G:/aw-wt/runtime-v2/src/agent_workflow/tasks/result_schema.py:49)
- 严重级别：blocking

`TaskResult.to_dict()` 和 parser fallback 现在会真实输出 `"decision": null`，但 `TASK_RESULT_SCHEMA["properties"]["decision"]` 仍是 `{"type": "string"}`。这意味着“字段可省略”成立，但“字段存在且为 null”不符合 schema。

本次目标明确要求 `decision` 默认值为 `None`、`to_dict/from_dict/get_decision` 同步适配，并且 parser fallback 返回 `decision=None`。因此 schema 也需要表达 `null` 可接受，例如 `type: ["string", "null"]`，并在 `build_task_result_schema(allowed_decisions)` 注入 enum 时同步允许 `None` 或避免对 null 误拒。否则 AgentInput 下发的 schema 与 Runtime 自己产出的 TaskResult 之间存在契约冲突。

建议补充测试：对 `TaskResult(..., decision=None).to_dict()` 使用 `TASK_RESULT_SCHEMA` 或等价断言验证 decision schema 接受 null；同时覆盖 `build_task_result_schema(None)` 与有 allowed_decisions 的形态。

### 2. Agent prompt 仍把 `decision` 作为必填，并在无 allowed_decisions 时示例 `done`

- 文件：[src/agent_workflow/context/agent_input.py](G:/aw-wt/runtime-v2/src/agent_workflow/context/agent_input.py:97)
- 严重级别：blocking

虽然 `result_schema.py` 已把 `decision` 移出 required，但 `AgentInput.build_prompt()` 生成的“TaskResult 的必需字段”仍列出 `decision`，并且示例在没有 `allowed_decisions` 时继续输出 `"decision": "done"`。

这会继续把线性任务和无分支任务诱导为输出 `done`，与本步目标“decision 字段默认 None / Optional；无 allowed_decisions 时 decision 为自由字符串但非必填；Parser 不再兜底 done”冲突。它也会削弱后续“Runtime 不认识业务词”的方向，因为 prompt 仍把旧业务词作为默认示例。

建议修订：只有存在 `allowed_decisions` 或 schema decision enum 时才在必需字段说明与示例中强调 decision；无 allowed_decisions 时说明 decision 可省略，示例不要默认 `done`。

## 已核对上一轮建议

上一轮 `plan_review_doc` 中的非阻塞建议已大多处理：

- `get_decision()` 注解已改为 `str | None`。
- `_extract_task_result_fallback` 的命中条件已按“正则实际命中字段”实现，避免 `status="invalid_output"` 默认值吞掉最终 fallback。
- 已新增至少 1 条显式错误分支 `decision is None` 测试，覆盖 Claude CLI not found 分支。

## 范围与正确性

未发现超出本步边界的核心代码改动。`machine.py`、`runner.py`、`loader.py`、`config/`、`_loop` 未被纳入本次实现 diff；`validators/task_result.py` 只做了删除全局 decision 白名单和移除 required decision 的最小连带清理，符合计划边界。

`VALID_DECISIONS` 在 `src` 与 `tests` 中已清零。`claude_cli.py` 与 `codex_cli.py` 的无结构化输出最终 fallback 都改为 `status="invalid_output"`、`decision=None`；Codex `_parse_output_fallback` 在 `returncode == 0` 且无结构化输出时也不再臆测 `done`。

## 测试验证

已运行：

```powershell
$env:PYTHONPATH='src;.'; python -m py_compile src\agent_workflow\agents\_parse.py tests\unit\test_parser_fallback.py src\agent_workflow\agents\claude_cli.py src\agent_workflow\agents\codex_cli.py src\agent_workflow\tasks\result.py src\agent_workflow\tasks\result_schema.py src\agent_workflow\validators\task_result.py
$env:PYTHONPATH='src;.'; pytest tests/unit/test_parser_fallback.py tests/unit/test_task_result_v4.py -q --basetemp=.pytest_tmp_review2 -p no:cacheprovider
$env:PYTHONPATH='src;.'; pytest tests -q --basetemp=.pytest_tmp_review_full -p no:cacheprovider
```

结果：

- `py_compile` 通过。
- `test_parser_fallback.py` + `test_task_result_v4.py`：`30 passed`。
- 全量：`33 failed, 294 passed, 22 skipped`。

全量失败项与 execution_report 声明一致，主要是既有 `schemas/*.schema.json` 缺失、workflow fixture/agents.yaml 不一致、`test_negative.py` 中 allowed_decisions warning/error 旧预期和 cancel 路径旧问题；未发现新增 parser/TaskResult 测试失败。

## diff artifact 状态

未发现单独的 diff artifact。已改用 `git diff`、`git diff --name-only`、`git ls-files --others --exclude-standard`，并逐项读取 execution_report 列出的变更文件审查。残余风险是：若执行系统另有未落盘或未纳入工作树的 diff artifact，本次审查无法覆盖；当前结论基于工作区真实源码、staging report 和 git 工作树。

## 需 refinement 处理

1. 让 `result_schema.py` 的 `decision` schema 与 `decision=None` 契约一致，并补测试。
2. 修订 `AgentInput.build_prompt()` 的必填字段说明与示例，避免无 allowed_decisions 时继续强制/诱导输出 `decision: "done"`，并补测试或调整现有 prompt 断言。
