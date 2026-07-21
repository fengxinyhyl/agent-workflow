---
name: worktree-staging-resolves-against-project-root
description: worktree 模式下 staging_path 必须基于 agent 沙箱 project_root 解析，不是 run_root
metadata:
  type: project
---

worktree 模式下 `run_root`（主仓，如 `F:\code\stock\docs\runs\<id>`）与 agent 子进程 cwd `project_root`（worktree，如 `F:\code\aw-wt\sp1`）是两棵不同的文件树。

agent 被 `--add-dir cwd` 沙箱限制，只能写自己的 cwd（project_root），所以它声明的相对 `staging_path` 实际落在 worktree 下的 `project_root/docs/runs/<id>/staging/...`。早先引擎一律用 `run_root` 拼接解析相对 staging_path → 路径重复（`docs/runs/<id>` 出现两次）且跨树找不到文件，promotion 失败。

约束：run 目录必须留在主仓根目录下（产物 promote 到 `run_root/artifacts`），否则无法从根目录恢复工作流。所以不能把 run_root 挪进 worktree，只能修正解析基准。

修复（2026-06-27 排查，改动当时未提交）：
- `runner.py` 相对 staging_path 改用 `project_root` 解析，并在 project_root / run_root 两棵树下依次自动修正，**回写绝对路径**到 artifact 和原始 dict（关键：否则 promotion 拿相对路径二次拼 run_root 再次重复）。
- `promotion.py` 新增 `_check_staging_sandbox(staging_path, sandbox_roots)`：文件须在某个沙箱根内、且路径含 `staging` 段（防止 agent 把任意源码登记为产物）。`promote_artifact` 新增 `staging_root` 参数（默认 run_root，向后兼容），runner 调用时传 `staging_root=project_root`。artifact 仍严格限制在 `run_root/artifacts`。

**Why:** 普通模式下 project_root 与 run_root 同树，bug 被自动修正 fallback 掩盖；worktree 把两者拆到不同树才暴露。这是隐性跨 session 陷阱，源码里 staging 路径解析散落 runner + promotion 多处。

**How to apply:** 改动任何 staging_path 解析逻辑时，记住基准是 agent cwd（project_root），不是 run_root。涉及 worktree 时验证两棵树场景，参考 `tests/unit/test_artifact_promotion.py::TestWorktreeStaging`。关联 [[preexisting-test-debt]]（评估回归先排除 schema_contract + test_cancel 预存失败）、[[claude-permission-mode-semantics]]（--add-dir 沙箱）。

---

**治本修复（2026-06-27 第二轮，本次已落地+测试绿）**：上一轮只补了「解析/promotion」侧（治标），没堵住源头——`_build_agent_input`（runner.py）仍硬编码 `run_root/staging` 告知 agent 输出路径。worktree 下 agent 被沙箱锁在 project_root，**根本写不进主仓 run_root**，只能把路径尾部重新挂到自己 cwd → 落到 `project_root/docs/runs/<id>/staging/<state>/file`，auto-fix（只搜 `{root}/staging/<state>`，缺中间 `docs/runs/<id>` 段）找不到。

根因：引擎给了 agent 一个它物理上写不进去的路径。修复引入单一真值源 `RunContext.staging_root` 属性：判据「run_root 是否在 project_root 内」——在内=普通模式返回 run_root，否则=worktree 返回 project_root（agent 沙箱可写）。全链路统一使用 staging_root：runner.py 的 start 建目录 / `_write_task_result_json` / skill adoption 写入 / `_build_agent_input` / `_run_agent` 建目录 / auto-fix 候选搜索；adoption.py 的 `get_adoption_summary` 读取；retry.py 两处清理；claude_cli.py + codex_cli.py 的 prompt.md 写入。artifacts 仍 promote 到 run_root/artifacts（恢复能力不变）。

关键约束：staging 写/读/清理必须用**同一个**根，否则 worktree 下「output 落 project_root、retry 只清 run_root」会残留。所有现有测试 run_root 都在 project_root 内（normal 模式），heuristic 不误伤。回归测试见 `tests/unit/test_worktree_staging_root.py`。

---

**漏网补丁（2026-07-21，本次已落地+测试绿）**：上面两轮治本修复覆盖了 skill_adoption 的**写**路径（`adoption.py` / runner `_promote_skill_adoption` 建目录都用 `staging_root`），但漏了它的 **promote 调用**——`runner.py::_promote_skill_adoption` 里 `promote_artifact(...)` 没传 `staging_root`，回退默认 `run_root`，于是沙箱校验拿主仓 run_root 去比对落在 worktree 的 staging 文件 → worktree 下每个节点进入时刷一次 `ArtifactPromotionFailed: staging 路径逃逸沙箱`。

对比：主产物路径 `_promote_artifacts`（runner.py ~1448）早就传了 `staging_root=self.context.project_root`，所以 dev_plan 等主产物在 worktree 下正常 promote，**只有 skill_adoption 这条附属产物受影响**。

影响：**非致命**——丢的是「本节点采用了哪些技能」的审计记录，不影响主产物与代码产出，run 不会因此 failed。

修复：`runner.py::_promote_skill_adoption` 的 `promote_artifact(...)` 补 `staging_root=self.context.project_root`（与主产物路径一致）。一行参数、零签名改动，`promote_artifact` 早已支持该形参（`promotion.py:91` `staging_root: str | None = None`，`= staging_root or run_root` 回退）。

**How to apply（追加）**：凡新增「把 staging 文件 promote 到 run_root/artifacts」的调用，worktree 模式下都必须显式传 `staging_root=context.project_root`，否则沙箱校验用 run_root 必然拒。排查此类失败先看调用点有没有漏传该参数，而不是怀疑 `staging_root` 属性判据（判据对不同盘符/不同树已正确）。注意：runner 是长驻进程，改动对**正在跑的 run 不生效**，只惠及之后启动的 run。
