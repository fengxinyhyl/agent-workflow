# plan → review → advise → execute 工作流模板

通用四阶段 Agent 编排示例：**编写计划 → 审查 → 给出修改建议（回流）→ 执行**。
这是把 agent-workflow 引擎能力迁移到新项目的最小可用模板——**引擎代码零改动，只需这套 YAML 配置**。

## 链路

```
plan → review
         approve → execute → done
         advise  → advise → plan   （带着修改建议重做计划，回流）
         reject  → failed
```

`review` 通过 `advise` 触发回流到 `plan`，由 `guards.max_visits`（默认 5）兜底防止无限循环。

## 文件清单（"五件套"）

| 文件 | 作用 | mock 跑 | 真跑 |
|------|------|:------:|:----:|
| `workflow.yaml` | 状态机 + task 定义（链路核心） | 必需 | 必需 |
| `roles.yaml` | role → agent 映射 | 必需 | 必需 |
| `mock_script.yaml` | mock 模式按 state 的 decision 脚本（演示回流） | 必需 | 忽略 |
| `skills/` | required_skills 内容 | 必需 | 必需 |
| `outputs.yaml` | 产物流声明（文档用途，引擎不强依赖） | 可选 | 可选 |
| `agents.real.yaml` | agent → 真实 CLI provider 配置 | **不放**\* | 用 `--agents` 指定 |

\* **关键**：CLI 会自动发现同目录的 `agents.yaml`。一旦发现，节点就走真实 CLI（如 Claude/Codex），mock 脚本失效。因此真实 agent 配置故意命名为 `agents.real.yaml` 避免被自动发现——mock 跑时 registry 为空，全部 fallback 到 MockAgent，脚本才生效。

## mock 跑（开箱即用，无需任何外部 CLI）

```bash
# 先安装引擎（一次性）
pip install -e <agent-workflow 仓库路径>

# 跑通完整回流
agent-workflow run \
  -w examples/plan-review-advise-execute/workflow.yaml \
  -g "你的目标描述" \
  -p <项目根目录>
```

预期输出链路：

```
plan → review(advise) → advise → plan → review(approve) → execute → done
```

产物流写入 `<项目根>/.agent-workflow/runs/<run_id>/artifacts/`：
`plan_doc.md` / `review_doc.md` / `advice_doc.md` / `execution_report.md`。

### mock_script.yaml 怎么改

按 state 名给一个 decision 列表，**按该 state 的访问次数（1-based）取值**，列表耗尽后固定取最后一个：

```yaml
decision_script:
  review:
    - advise    # 第 1 次访问 review：触发回流
    - approve   # 第 2 次访问：通过
```

想跑"无回流主链"，把 `review` 改成 `[approve]` 即可。

## 真跑（接真实 Claude / Codex CLI）

```bash
agent-workflow run \
  -w examples/plan-review-advise-execute/workflow.yaml \
  -g "你的目标描述" \
  -p <项目根目录> \
  --agents examples/plan-review-advise-execute/agents.real.yaml
```

真跑前需要：

1. 把 `agents.real.yaml` 里的 `{CODEX_COMMAND}` / `{CLAUDE_COMMAND}` 换成真实 CLI 可执行文件路径。
2. **校准 CLI 命令参数**：当前 `src/agent_workflow/agents/claude_cli.py` 的 `_build_command` 用的是 P0 占位参数（`--print --output-format json --prompt-file`），真实 Claude Code / Codex CLI 的调用方式需自行核对调整。这是真跑时引擎里唯一需要动代码的地方。

## 迁移到新项目

把整个 `plan-review-advise-execute/` 目录复制到新项目任意位置，改三处即可：

1. `workflow.yaml` 的 `tasks.*.instruction` —— 换成你的领域任务描述。
2. `roles.yaml` / `agents.real.yaml` —— 换成你要用的 agent。
3. `mock_script.yaml` —— 调整 mock 演示节奏。

状态机骨架（plan/review/advise/execute + 回流）可原样保留，也可按需增删 state。
