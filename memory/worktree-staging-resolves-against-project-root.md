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
