# agent-workflow 命令

用户短命令触发 agent-workflow 生命周期。不要手动拼长命令。

> **前提**：已在项目根执行 `pip install -e .`，使 `agent-workflow` 入口命令可用。
> 若未安装，可用 `python -m agent_workflow.cli` 等价替代（需 `PYTHONPATH=src`）。

## 支持的短用法

### 启动工作流

```text
/agent-workflow <workflow> [-t <topic>] <goal...>
```

等价于：

```powershell
agent-workflow run -w workflows/<workflow>/workflow.yaml -t "<topic>" -g "<goal>"
```

`-t <topic>` 可选，省略时默认生成 `日期_工作流名称`。

示例：

```text
/agent-workflow listing-dev 实现用户登录功能
/agent-workflow listing-dev -t add-login-page 实现用户登录功能
/agent-workflow spec-dev -t refactor-auth 重构权限验证模块
```

### 预览/校验工作流

```text
/agent-workflow validate <workflow>
```

等价于：

```powershell
agent-workflow validate-config -w workflows/<workflow>/workflow.yaml
agent-workflow validate-state-machine -w workflows/<workflow>/workflow.yaml
```

### 查看运行状态

```text
/agent-workflow status <run_id>
/agent-workflow explain <run_id>
/agent-workflow history <run_id>
/agent-workflow history <run_id> <state>     # 反查 state 进入原因
```

等价于：

```powershell
agent-workflow status -r <run_id>
agent-workflow explain -r <run_id>
agent-workflow history -r <run_id>
agent-workflow history -r <run_id> --why <state>
```

### 查看日志

```text
/agent-workflow log <run_id>
/agent-workflow tail <run_id> <state> [lines]
```

等价于：

```powershell
agent-workflow log -r <run_id> --summary
agent-workflow tail -r <run_id> -s <state> -n <lines>
```

### 取消运行

```text
/agent-workflow cancel <run_id> [reason]
```

等价于：

```powershell
agent-workflow cancel -r <run_id> --reason "<reason>"
```

### 重试（默认 dry-run，加 dispatch 真实执行）

```text
/agent-workflow retry <run_id>
/agent-workflow retry <run_id> dispatch
```

等价于：

```powershell
agent-workflow retry -r <run_id>
agent-workflow retry -r <run_id> --dispatch
```

### 从 Gate 暂停恢复

```text
/agent-workflow continue <run_id> [approve]
```

等价于：

```powershell
agent-workflow continue -r <run_id> -w workflows/<workflow>/workflow.yaml [--approve]
```

带 gate 的工作流（如 spec-dev）会在 gate 状态暂停等待人工确认，用此命令恢复。`approve` token 映射为 `--approve` 表示通过；`continue` 需 `-w` 指定 workflow 以恢复状态机配置。

## 参数解析

1. 第一个 token 识别为 `mode`：`validate` / `status` / `explain` / `history` / `log` / `tail` / `cancel` / `retry` / `continue`。
2. 默认模式（第一个 token 不是以上关键字）→ `run` 模式，第一个 token = workflow 名称。
3. 若出现 `-t <值>`，提取为 `--topic`，不归入 goal。
4. `retry` 模式下检查是否有 `dispatch` token，有则映射为 `--dispatch`，否则默认 `--dry-run`。`continue` 模式下检查是否有 `approve` token，有则映射为 `--approve`。
5. 剩余 token 拼接为 goal。
6. 如果缺少 workflow 名称或 goal，向用户提问补齐。
7. 构造 PowerShell 命令时，所有用户提供的参数用单引号包裹。

## 执行规则

1. 工作目录为 `<repo>`（主仓根目录，取 `git rev-parse --show-toplevel`，不写死绝对路径）。
2. 命令优先用入口 `agent-workflow`（`pip install -e .` 后可用）；未安装时回退 `PYTHONPATH=src python -m agent_workflow.cli`。
3. 默认不加任何危险权限（`--dangerouslyDisableSandbox`、`bypassPermissions` 等）。
4. 不要自动执行 `cancel` 或 `retry --dispatch`，除非用户显式确认。
5. 不要读取、打印或外传 `.env`、密钥、数据库凭证等敏感内容。
6. `run` 默认自动发现 workflow 同目录的 `agents.yaml` / `skills/` / `mock_script.yaml`，无需显式 `--agents`。如需运行时覆盖 agent，用 `--agent-map "state:s1=agent1,task:t1=agent2"`。

## 可用工作流

| 工作流名称 | 文件 | 说明 |
|-----------|------|------|
| listing-dev | workflows/listing-dev/workflow.yaml | 标准开发链：plan → review → implement → audit → summary |
| spec-dev | workflows/spec-dev/workflow.yaml | 需求驱动开发链：plan (2轮审核+修订) → execute → audit (2轮审核+修订) → test → summary |
| req-analysis | workflows/req-analysis/workflow.yaml | 需求分析链：understand → review → advice（单向，不执行代码） |
| requirement-understanding | workflows/requirement-understanding/workflow.yaml | 纯需求理解链：三模型独立理解 → 交叉审查 → 共识合并 → 澄清问题 → 人工裁决 → final_requirement |
| system-architecture | workflows/system-architecture/workflow.yaml | 系统架构设计链 |
| decision-collection | workflows/decision-collection/workflow.yaml | 裁决收集链：指定 Markdown → 飞书 Base → 人工裁决 → 回收生成裁决包 |

> 另有 `*-example` 工作流（standard-dev-example / software-dev-example / plan-review-advise-*-example）为演示样例，可用 mock 模式跑通。

## 编排命令

| 命令 | 文件 | 说明 |
|------|------|------|
| spec-wt | .claude/commands/spec-wt.md | 在独立 git worktree 中运行 spec-dev，实现并行开发的代码物理隔离 |
