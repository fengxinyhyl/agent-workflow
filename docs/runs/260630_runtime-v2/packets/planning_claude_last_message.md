# planning claude debug packet

Session ID: b487f44d-2d28-4641-a328-04ccb2ef84bc

## 最后一条 assistant message

计划已完成并写入 staging。

我通读了主仓设计文档（`G:\agent-workflow\docs\runtime-v2-design.md`）和 runtime-v2 的全部相关源码，制定了第 1 步「契约收敛 + Parser 兜底」的开发计划。

核查中有两个关键发现，已在计划里作为歧义点裁定：

1. goal 第 4 条要求「清理 MockAgent 中引用 VALID_DECISIONS 的地方」，但 grep 全仓显示 `mock.py` 实际并未 import 或引用该符号——它只是默认产出业务 decision，属合法行为。裁定为本步不改 mock，仅列为验证点。

2. 删除 `VALID_DECISIONS` 会破坏 `validators/task_result.py` 的 import 和 warning 校验。裁定这属于「删除符号的连带清理」（仅移除 import 和那段 warning 校验），不等同于第 3 步的「Validator 纯函数化」，严格守住本步边界。

计划的核心结构：

- 7 个可独立验证的实现步骤，从 result.py 契约收敛 → schema → 抽取共享 Parser 模块 → claude/codex 兜底改造 → validator 连带清理 + 测试。
- 把两份同构的 `_parse_task_result_text`/`_extract_task_result_fallback` 抽到新建的 `agents/_parse.py` 去重，避免改造时两份逻辑漂移。
- 明确的停止规则：一旦发现必须改 `machine.py`/`runner.py`/`loader.py` 才能通过测试，就是触碰了后续步骤边界，停止并上报，不擅自越界。

最大风险点也已标注：decision 改 None 后，下游若有 `result.decision.lower()` 之类的隐性非空假设可能崩。计划界定本步只保证契约层（`get_decision` 安全返回 None）不崩，真正的路由消费属于第 3 步，不在此强行解决。

decision 为 `done`，计划完整可审核。

