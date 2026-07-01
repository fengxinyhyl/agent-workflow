# Runtime v2 第 1 步执行结果复审

## 审查结论

**decision: approve**

本轮 refinement 已修复上一轮 `output_review_doc-v1` 指出的 2 个 blocking 问题：`decision=None` 与 JSON Schema 的不一致，以及 `AgentInput.build_prompt()` 在无 `allowed_decisions` 时继续把 `decision` 当必填并示例 `done` 的问题。当前变更符合“契约收敛 + Parser 兜底”的第 1 步范围，可以进入 validation。

## 上一轮问题核对

1. **`decision=None` 与 TaskResult JSON Schema 不一致：已修复。**
   - [src/agent_workflow/tasks/result_schema.py](G:/aw-wt/runtime-v2/src/agent_workflow/tasks/result_schema.py:49) 已将 `decision` type 调整为 `["string", "null"]`。
   - [src/agent_workflow/tasks/result_schema.py](G:/aw-wt/runtime-v2/src/agent_workflow/tasks/result_schema.py:176) 在 `allowed_decisions` 注入 enum 时同时保留 `None`。
   - [tests/unit/test_task_result_v4.py](G:/aw-wt/runtime-v2/tests/unit/test_task_result_v4.py:242) 增加了 `decision=None` schema 接受性测试。

2. **Agent prompt 仍诱导无分支任务输出 `decision: "done"`：已修复。**
   - [src/agent_workflow/context/agent_input.py](G:/aw-wt/runtime-v2/src/agent_workflow/context/agent_input.py:107) 通过 `effective_decisions` 区分是否存在分支决策。
   - [src/agent_workflow/context/agent_input.py](G:/aw-wt/runtime-v2/src/agent_workflow/context/agent_input.py:119) 无分支时说明 `decision` 可省略或置为 `null`。
   - [src/agent_workflow/context/agent_input.py](G:/aw-wt/runtime-v2/src/agent_workflow/context/agent_input.py:131) 仅有分支决策时才在示例 JSON 中输出 `decision`。
   - [tests/unit/test_run_context.py](G:/aw-wt/runtime-v2/tests/unit/test_run_context.py:179) 覆盖了无 `allowed_decisions` 时不再出现 `"decision": "done"`。

## 范围与正确性

- `tasks/result.py` 已删除 Runtime 全局 `VALID_DECISIONS`，`decision` 默认改为 `None`，`get_decision()` / `from_dict()` 均保留 Optional 语义；`VALID_STATUSES` 保留并标注 `invalid_output` 为 Runtime 内部瞬时态，见 [src/agent_workflow/tasks/result.py](G:/aw-wt/runtime-v2/src/agent_workflow/tasks/result.py:40) 和 [src/agent_workflow/tasks/result.py](G:/aw-wt/runtime-v2/src/agent_workflow/tasks/result.py:116)。
- `result_schema.py` 已将 `decision` 移出 required，并按 `allowed_decisions` 条件注入 enum；无 `allowed_decisions` 时不注入 enum。
- Claude/Codex parser 的同构逻辑已抽到 [src/agent_workflow/agents/_parse.py](G:/aw-wt/runtime-v2/src/agent_workflow/agents/_parse.py:20)，两端最终 fallback 均改为 `status="invalid_output"`、`decision=None`，见 [src/agent_workflow/agents/claude_cli.py](G:/aw-wt/runtime-v2/src/agent_workflow/agents/claude_cli.py:258) 与 [src/agent_workflow/agents/codex_cli.py](G:/aw-wt/runtime-v2/src/agent_workflow/agents/codex_cli.py:319)。
- Codex `_parse_output_fallback` 在 `returncode == 0` 且无结构化 TaskResult 时不再臆测 `done`，见 [src/agent_workflow/agents/codex_cli.py](G:/aw-wt/runtime-v2/src/agent_workflow/agents/codex_cli.py:353)。
- `validators/task_result.py` 只做了删除全局 decision 白名单和移除必填 decision 的最小清理，未改为纯函数化，符合本步边界。
- 未发现 `machine.py`、`runner.py`、`loader.py`、`config/`、`_loop` 路由模型被纳入本次 diff。

## 测试验证

已重新运行：

```powershell
$env:PYTHONPATH='src;.'; python -m py_compile src\agent_workflow\agents\_parse.py src\agent_workflow\agents\claude_cli.py src\agent_workflow\agents\codex_cli.py src\agent_workflow\tasks\result.py src\agent_workflow\tasks\result_schema.py src\agent_workflow\context\agent_input.py src\agent_workflow\validators\task_result.py
$env:PYTHONPATH='src;.'; python -m pytest tests/unit/test_task_result_v4.py tests/unit/test_run_context.py tests/unit/test_parser_fallback.py -q -p no:cacheprovider --basetemp=.pytest_tmp_review_recheck
$env:PYTHONPATH='src;.'; python -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp_review_full2
```

结果：
- `py_compile` 通过。
- 相关单测：`43 passed`。
- 全量：`33 failed, 297 passed, 22 skipped`。

全量失败数与 refinement 报告一致，仍为既有的 schema 文件缺失、workflow fixture/agents.yaml 不一致、`test_negative.py` 旧断言与 cancel 路径问题；未发现由本轮 refinement 新增的回归。

## 残余风险

- 未发现单独的 diff artifact；本轮审查基于 `git diff`、`git diff --name-only`、execution/refinement report 以及实际源代码逐项审查。残余风险是：若执行系统另有未落盘或未纳入工作树的 diff artifact，本审查无法覆盖。
- 现有 `TaskResultValidator(allowed_decisions=...)` 对显式 `decision=None` 仍会阻断，而 schema 已允许 `null`。这属于当前第 1 步与后续“Validator 纯函数化 + Runner Repair 闸口”之间的过渡行为：本步未要求修改 Validator 为纯函数或改 Runner 消费路径，且 prompt 在有 `allowed_decisions` 时仍要求输出具体决策。因此记录为后续步骤风险，不阻断本次契约与 Parser 落地。
- `BaseAgent._create_task_result()` 默认参数仍是 `decision="done"`，但当前 Claude/Codex 的显式失败、取消、超时和安全拦截分支均已传入 `decision=None`；未发现本次目标中的 parser fallback 仍通过该默认值伪造 `done`。建议后续路由消费改造时一并收敛该 helper 默认值。

## 结论

上一轮阻塞问题已处理，新增测试覆盖了 Parser invalid_output 兜底、decision Optional、schema decision 非必填和 prompt 不再默认诱导 `done`。本次执行结果可进入 validation。
