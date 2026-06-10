# standard-dev 工作流

标准开发链路：

```text
计划 -> 审核 -> 采纳 -> 编程 -> 检查代码 -> 单元测试 -> 总结
```

## 文件

| 文件 | 作用 |
|------|------|
| `workflow.yaml` | 状态机和 task 定义 |
| `roles.yaml` | role 到 agent 的映射 |
| `agents.real.yaml` | 真实 CLI agent 配置，需通过 `--agents` 显式指定 |
| `mock_script.yaml` | mock 跑通时的 decision 脚本 |
| `outputs.yaml` | 产物流说明 |
| `skills/` | 节点规范和 skill policy |

## Mock 运行

```powershell
cd agent-workflow
$env:PYTHONPATH='src'
python -m agent_workflow.cli run `
  -w examples/standard-dev/workflow.yaml `
  -g "实现一个 hello world CLI" `
  -p .
```

默认 mock 链路：

```text
plan -> review -> adoption -> implement -> code_audit -> unit_test -> summary -> done
```

产物写入：

```text
.agent-workflow/runs/<run_id>/staging/
.agent-workflow/runs/<run_id>/artifacts/
```

## 真实运行

真实运行前需要设置或替换 `agents.yaml` 中的命令占位：

```powershell
$env:CODEX_COMMAND='codex'
$env:CLAUDE_COMMAND='claude'
```

然后执行：

```powershell
agent-workflow run `
  -w examples/standard-dev/workflow.yaml `
  -g "你的开发目标" `
  -p <项目根目录> `
  --agents examples/standard-dev/agents.real.yaml
```

## 节点规范

每个 task 在 `workflow.yaml` 中通过 `skills:` 声明节点规范。Runner 会加载对应 skill，
写入 `skill_adoption` 产物，并注入到 Agent prompt。
