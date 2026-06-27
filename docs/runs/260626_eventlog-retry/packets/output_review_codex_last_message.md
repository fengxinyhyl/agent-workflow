# 输出审查报告：history 命令 + retry 失败诊断

**结论：revise**

上一轮指出的 `_vf(..., warnings=...)` 单测问题已修复，`history --why` 也已补充 decision 箭头。但本轮发现一个阻塞级正确性问题：`history --why` 在状态回流/重复进入时会把目标 state 进入之后的未来迁移拼进因果链，导致反查结果不可靠。

**blocking**：`src/agent_workflow/observability/history.py:151`  
`_render_why_from_events()` 每轮都从全量事件里取最后一条 `next_state == cursor` 的 `TransitionSelected`，没有限制该迁移必须早于当前迁移。反例：`s1 -> s2 -> s3 -> s1 -> s2` 后反查 `s3`，当前实现会使用 `s3` 之后的 `s1/s2` 迁移拼链。应在倒推时携带当前事件索引或时间戳，只匹配更早的 `TransitionSelected`，并补回归测试。

**warning**：`src/agent_workflow/state_machine/runner.py:355`  
`GuardFailed` 发射时未携带当前 state，`retry_diagnose` 读取 `evt["state"]` 会得到空字符串。诊断仍能识别 `guard_loop`，但 dry-run 里“卡在哪个状态”信息缺失。建议发射事件时附带 `state=current_state`，或诊断时从前序 `StateEntered` 推断。

验证：新增定向测试通过：`30 passed`。全量 `pytest tests/unit -q` 当前为 `261 passed, 28 failed, 2 skipped`，失败主要来自既有 schema 文件缺失和旧 cancel 路径断言，未显示为本次新增功能导致。

我尝试写入指定 staging 路径 `F:\code\agent-workflow\docs\runs\260626_eventlog-retry\staging\output_review\...`，但当前沙箱仅允许写入 `F:\aw-wt\eventlog-retry`，PowerShell 返回访问被拒绝。因此未登记 artifact；以下 TaskResult 如实保留 `artifacts: []`。

```json
{
  "schema_version": 1,
  "task_id": "output_review",
  "state": "output_review",
  "agent": "codex",
  "status": "success",
  "decision": "revise",
  "summary": "审查完成：上一轮问题已基本修复，但 history --why 在回流场景会混入未来迁移，因果链不可靠，需要修订后重审；指定 staging 路径因沙箱权限无法写入。",
  "artifacts": [],
  "execution": {
    "started_at": "",
    "finished_at": "",
    "exit_code": 0
  },
  "issues": [
    {
      "severity": "blocking",
      "title": "history --why 可能混入未来迁移",
      "detail": "_render_why_from_events 每轮从全量 events 选择最后一条 next_state==cursor 的 TransitionSelected，未限制必须早于当前迁移；回流场景会输出错误因果链。"
    },
    {
      "severity": "warning",
      "title": "GuardFailed 诊断缺少真实 state",
      "detail": "Runner 发射 GuardFailed 时未携带 state，retry_diagnose 从事件顶层读取 state 会得到空字符串，dry-run 难以说明卡在哪个状态。"
    },
    {
      "severity": "warning",
      "title": "staging 产物无法写入",
      "detail": "当前沙箱仅允许写入 F:\\aw-wt\\eventlog-retry，指定输出路径 F:\\code\\agent-workflow\\docs\\runs\\260626_eventlog-retry\\staging\\output_review\\output_review_doc.md 写入被拒绝。"
    }
  ]
}
```