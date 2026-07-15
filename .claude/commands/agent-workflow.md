# agent-workflow 命令

用户短命令触发 agent-workflow 生命周期。不要手动拼长命令。

## 支持的短用法

### 启动工作流

```text
/agent-workflow <workflow> [-t <topic>] <goal...>
```

等价于：

```powershell
python -m agent_workflow.cli run -w workflows/<workflow>/workflow.yaml -t "<topic>" -g "<goal>"
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
python -m agent_workflow.cli validate-config -w workflows/<workflow>/workflow.yaml
python -m agent_workflow.cli validate-state-machine -w workflows/<workflow>/workflow.yaml
```

### 查看运行状态

```text
/agent-workflow status <run_id>
/agent-workflow explain <run_id>
```

等价于：

```powershell
python -m agent_workflow.cli status -r <run_id>
python -m agent_workflow.cli explain -r <run_id>
```

### 查看日志

```text
/agent-workflow log <run_id>
/agent-workflow tail <run_id> <state> [lines]
```

等价于：

```powershell
python -m agent_workflow.cli log -r <run_id> --summary
python -m agent_workflow.cli tail -r <run_id> -s <state> -n <lines>
```

### 取消运行

```text
/agent-workflow cancel <run_id> [reason]
```

等价于：

```powershell
python -m agent_workflow.cli cancel -r <run_id> --reason "<reason>"
```

### 重试（默认 dry-run，加 dispatch 真实执行）

```text
/agent-workflow retry <run_id>
/agent-workflow retry <run_id> dispatch
```

等价于：

```powershell
python -m agent_workflow.cli retry -r <run_id>
python -m agent_workflow.cli retry -r <run_id> --dispatch
```

## 参数解析

1. 第一个 token 识别为 `mode`：`validate` / `status` / `explain` / `log` / `tail` / `cancel` / `retry`。
2. 默认模式（第一个 token 不是以上关键字）→ `run` 模式，第一个 token = workflow 名称。
3. 若出现 `-t <值>`，提取为 `--topic`，不归入 goal。
4. `retry` 模式下检查是否有 `dispatch` token，有则映射为 `--dispatch`，否则默认 `--dry-run`。
5. 剩余 token 拼接为 goal。
6. 如果缺少 workflow 名称或 goal，向用户提问补齐。
7. 构造 PowerShell 命令时，所有用户提供的参数用单引号包裹。

## 执行规则

1. 使用 PowerShell 工具执行命令，工作目录为**项目根目录**（当前仓库根，即本 `.claude/` 所在目录）。
2. Python 用 `python`（需先激活项目所用的 conda base 环境，Python 3.11+）；命令中的 `python` 即解析到该解释器。若环境未激活或 `python` 未指向正确解释器，先激活再执行，不要在命令里写死绝对路径。
3. 默认不加任何危险权限（`--dangerouslyDisableSandbox`、`bypassPermissions` 等）。
4. 不要自动执行 `cancel` 或 `retry --dispatch`，除非用户显式确认。
5. 不要读取、打印或外传 `.env`、密钥、数据库凭证等敏感内容。

## 可用工作流

| 工作流名称 | 文件 | 说明 |
|-----------|------|------|
| listing-dev | workflows/listing-dev/workflow.yaml | 标准开发链：plan → review → implement → audit → summary |
| spec-dev | workflows/spec-dev/workflow.yaml | 需求驱动开发链：plan (2轮审核+修订) → execute → audit (2轮审核+修订) → test → summary |
| req-analysis | workflows/req-analysis/workflow.yaml | 需求分析链：understand → review → advice（单向，不执行代码） |
| requirement-understanding | workflows/requirement-understanding/workflow.yaml | 纯需求理解链：三模型独立理解 → 交叉审查 → 共识合并 → 澄清问题 → 人工裁决 → final_requirement |
| decision-collection | workflows/decision-collection/workflow.yaml | 裁决收集链：指定 Markdown → 飞书 Base → 人工裁决 → 回收生成裁决包 |

## 编排命令

| 命令 | 文件 | 说明 |
|------|------|------|
| req-dev | .claude/commands/req-dev.md | 串行 req-analysis → listing-dev，逐 Step 自动实现 |
