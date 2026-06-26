# spec-dev

需求驱动的开发工作流。条件回流链：列计划 → 审核计划 →（按需修订）→ 执行 → 审核结果 →（按需修订）→ 验证 → 总结。

## 流程

```text
goal + project_context
  ↓
planning ──done──► plan_review
                     ├─approve─► execution
                     └─revise──► plan_refinement ─► plan_review
execution ──done──► output_review
                     ├─approve─► validation
                     └─revise──► output_refinement ─► output_review
validation ─approve─► retrospective ─► done
           └─revise──► output_refinement
```

- review/test 类节点（plan_review / output_review / validation）输出 `approve` / `revise` / `reject` 驱动状态机。
- 执行/修订类节点（planning / plan_refinement / execution / output_refinement）输出 `done` / `fail` / `blocked`。
- `guards.max_visits=3` 限制同一 state 进入次数，防回流失控。

## 运行

```powershell
python -m agent_workflow.cli run `
  -w workflows\spec-dev\workflow.yaml `
  -g "<开发目标>"
```

真实运行会自动发现同目录的 `agents.yaml`、`skills/` 和 `mock_script.yaml`。

> **agents 配置两份**：`agents.yaml`（本地未跟踪，命令可用个人 wrapper 如 `cc-opus`，被 gitignore）与 `agents.tmp.yaml`（纳入版本库，命令写死 `claude`/`codex`，开箱即用）。不带 `--agents` 时默认发现 `agents.yaml`。

## worktree 隔离运行

并行跑多个 spec-dev run（各做一个模块）时，**多个 run 共享同一工作树没有文件隔离**——不同 run 的 execution 节点会同时往同一份代码写、互相覆盖。解法是给每个 run 一个独立 git worktree + 独立分支，开发期物理隔离，冲突推迟到合并阶段由 git 处理。

引擎约束（已验证）：`project_root` 在 run 启动时定死，**无法在工作流节点间中途切换**。所以隔离必须在 run 启动**之前**完成，不能做成工作流内的节点。项目封装了 `/spec-dev-wt` 命令自动完成（详见 `.claude/commands/spec-wt.md`），本节说明其底层机制。

### 路径约定

| 项 | 取值 |
|----|------|
| 主仓 | `F:\listing-management` |
| worktree 目录 | `F:\lm-wt\<module>`（与主仓同卷，便于合并） |
| 分支 | `feat/<module>`（默认） |
| run 产物 | `F:\listing-management\docs\runs\<run_id>\`（收口到主仓，不散落 worktree） |

### 启动（关键参数语义）

```powershell
git -C F:\listing-management worktree add 'F:\lm-wt\<module>' -b 'feat/<module>'

python -m agent_workflow.cli run `
  -w 'F:\listing-management\workflows\spec-dev\workflow.yaml' `
  -p 'F:\lm-wt\<module>' `
  --run-root 'F:\listing-management\docs\runs' `
  -t '<module>' -g '<goal>'
```

- **`-w` 用主仓路径**：agents/skills 配置从主仓单一来源发现，不受 worktree 内副本漂移影响。引擎读 agents 配置的锚点是 `-w` 同目录，与 worktree 无关——即使 `agents.yaml` 被 gitignore、worktree 里没有，也照常从主仓读到。
- **`-p` 指 worktree**：agent 执行目录（`cwd`）落在 worktree，代码改动隔离于此。`project_root` 也是引擎找 `.env` 的锚点。
- **`--run-root` 收口主仓**：产物统一存放、`run_index` 好查。

### .env 与命令占位符

worktree 是 gitignore 过滤后的副本，**不含 `.env`**。

- 若 `agents.yaml` 的 `command` 是**直接命令**（如 `cc-opus`）→ 引擎直接用，不查 `.env`/环境变量，无需任何额外处理。
- 若是 **`{OPUS_COMMAND}` 占位符** → 引擎从 `project_root/.env`（= worktree，没有）或 `os.environ` 解析。此时需把主仓 `.env` 的命令变量注入当前会话（`os.environ` 优先级高于 `.env`）。

### 重试

```powershell
python -m agent_workflow.cli retry -r <run_id> --dispatch `
  -w 'F:\listing-management\workflows\spec-dev\workflow.yaml'
```

- **`-p` 不用带**：retry 从快照恢复 `project_root`，原 run 跑在 worktree 就续在同一 worktree。（引擎已修正旧缺陷——以前不带 `-p` 会降级成当前目录 `"."` 而非快照值。）
- **`--run-root` 在主仓目录里敲可省**：找 run 靠 cwd 下的 `docs/runs` / `run_index.json`；在主仓之外敲才需补。
- **`-w` 仍需指主仓**：dispatch 用快照 `project_root`（= worktree）搜 `workflow.yaml`，会搜到 worktree 那份，而其同目录 `agents.yaml` 被 gitignore 不存在 → fallback 到 mock agent（产物空壳）。`-w` 指主仓才能让 agents 从主仓发现。这是「agents.yaml 只放主仓」设计的固有约束，非缺陷。

### 清理

清理只在合并确认后**手动**执行，工作流本身从不清理；失败/取消的 run 其 worktree 必须保留（重试依赖它）。

```powershell
cd F:\listing-management
git merge feat/<module>
git worktree remove F:\lm-wt\<module>
git branch -d feat/<module>
```

`git worktree remove` 失败的处理：

| 原因 | 现象 | 处理 |
|------|------|------|
| 有未提交改动/未跟踪文件 | `contains modified or untracked files` | 先确认去留：保留就先 commit；确认可丢弃再 `--force` |
| 进程占用目录 | `Permission denied` / 目录被锁 | 关掉占用进程（编辑器、终端、跑测试的 shell）后重试 |
| 目录已手删但登记残留 | `worktree list` 仍显示 | `git -C F:\listing-management worktree prune` |

### ⚠️ 工作流不自动 commit —— 接手与合并须知

**spec-dev 全程只改文件、写 report，不执行任何 git commit。** 即便工作流跑到 `done`，改动也只是 worktree 工作区的**未提交状态**，feat 分支上没有本次 run 的提交。因此：

1. **合并前必须先在 worktree 手动 commit**，否则 `git merge` 合不到东西：
   ```powershell
   git -C F:\lm-wt\<module> add -A
   git -C F:\lm-wt\<module> status        # 提交前核对范围
   git -C F:\lm-wt\<module> commit -m '<module>: <简述>（run <run_id>）'
   ```
2. **agent/人工接手**（工作流失败、卡死、产物不达标时把模块做完）：
   - 改动只留在 worktree 的 feat 分支，**不要跑到主仓直接改**——否则破坏隔离，且该 worktree 被 retry 续跑时会与主仓分叉、互相覆盖。
   - 接手完成后按上面方式手动 commit。
   - 在 `docs/runs/<run_id>/` 留一行说明该 run 由接手完成、非工作流产出，避免 run 记录与实际代码对不上。
   - 若原 run 已 failed 且被多次 retry/误操作污染、worktree 又无有效提交，优先**废弃重建**（删分支、删 worktree、重新启动），不要在脏基础上接手。

### 并行注意

- 每个模块单独一次启动，各自独立 worktree。
- 合并回主分支由用户**串行手动**执行，避免并行写 main 引发冲突。
- validation 节点在 worktree 运行：DB 集成测试因缺 `PGSQL_*` 环境变量自动 skip（`go test` 仍计为通过），纯单元测试正常执行。

## 主要产物

| Artifact | 来源节点 | 作用 |
|----------|----------|------|
| `plan_doc` | `planning` | 开发计划 |
| `plan_review_doc` | `plan_review` | 计划审核意见 |
| `plan_refinement_doc` | `plan_refinement` | 计划修订与逐条回应 |
| `execution_report` | `execution` | 实际改动文件、命令、与计划偏差 |
| `output_review_doc` | `output_review` | 执行结果审核意见 |
| `output_refinement_doc` | `output_refinement` | 结果修订与逐条回应 |
| `test_report` | `validation` | 单元测试结果与剩余风险 |
| `summary_report` | `retrospective` | 最终总结与复盘 |

## 边界

- 工作流产出代码改动与文档产物，**不 commit、不 merge、不清理 worktree**——这些均为用户手动确认后执行。
- worktree 隔离须在 run 启动前完成，工作流内无法切换 `project_root`。
- 失败/取消的 run 保留 worktree 供重试；`git merge`/`worktree remove`/`branch -d`/`retry --dispatch`/`--force` 均不自动执行。

## 验证

```powershell
python -m agent_workflow.cli validate-config -w workflows\spec-dev\workflow.yaml
python -m agent_workflow.cli validate-state-machine -w workflows\spec-dev\workflow.yaml
```
