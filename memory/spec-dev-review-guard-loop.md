---
name: spec-dev-review-guard-loop
description: spec-dev 工作流的审核节点（output_review）容易因 revise 回流撞上 max_visits guard 而 failed
metadata:
  type: project
---

spec-dev 工作流中，「审代码」类审核节点（如 `output_review`，由 codex 审、deepseek 改）容易陷入 revise 回流循环：审核 → revise → refinement → 再审 → 再 revise……三轮内常不收敛，最终撞上 `guards.max_visits=3` 触发 GuardFailed，整个 run 标记为 **failed**。

**关键反差：** 此时 `execution` 节点往往已 `decision=done`，**代码其实已经完整产出**，failed 只是卡在后续审核循环。不要因为 run 标记 failed 就认为没有产出——去 worktree 看 `git status` / 跑测试验证真实代码状态。

**Why:** 2026-06-27 用 spec-dev 开发 event log history + retry 诊断功能时实际撞到：run failed 于 output_review 第 4 次访问超 max_visits，但 worktree 里 history.py / retry_diagnose.py + 测试已全部产出且测试通过。讽刺的是，这个回流死循环正是当时要开发的 retry 诊断功能想识别的场景。

**How to apply:**
1. spec-dev run 报 failed 时，先查 `[GUARD] [FAIL] max_visits` 是不是死循环卡审核，而非真的没做出来。
2. 直接在 worktree 验证代码（`git status` + 跑相关测试），别盲信 failed 判定。
3. 若审核节点确实易反复，可考虑调高该 workflow 的 `max_visits` 或拆分审核粒度。
4. 评估测试回归时排除预存技术债，见 [[preexisting-test-debt]]。
