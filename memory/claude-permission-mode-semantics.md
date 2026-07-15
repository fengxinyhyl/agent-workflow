---
name: claude-permission-mode-semantics
description: Claude Code CLI 各 permission-mode 与 allowedTools 的实测行为差异
metadata:
  type: reference
---

Claude Code CLI（`claude -p`，本机用 `cc-deepseek.cmd` 包装）的权限语义，2026-06-12 实测：

- `--permission-mode` 可选值：`default` / `acceptEdits` / `auto` / `dontAsk` / `plan` / `bypassPermissions`
- `acceptEdits`：自动批准 **Write/Edit 文件操作**，但**拒绝命令执行**（Bash/PowerShell 进 permission_denials）
- `dontAsk`：不询问且**直接拒绝**未预授权工具（写文件和执行全 deny），与字面直觉相反
- `bypassPermissions`：放开一切，但 agent-workflow 引擎的 `_assert_safe_permission` 会拦截（含 "bypass"/"dangerously" 的值被禁）

正确放开工具的机制是 **`--allowedTools`**（逗号分隔白名单），配合 `permission_mode`。两档模型：
- **纯文档节点**：`default` + `Read,Grep,Glob,Write,Edit`（**必含 Edit**，见下）
- **执行/命令节点**：`auto` + `Read,Grep,Glob,Write,Edit,Bash`

⚠️ **纯文档节点的白名单必须含 `Edit`，早期"只给 Write 不给 Edit"是错的**。Claude Code 内置规则：
单文件内容超约 50 行时，走「`Write` 建头 + `Edit` 分块追加」。缺 Edit 时长产物必然出问题，两种病症：
- **挂起**：`default` 下 Edit 走权限询问，批处理 `claude -p` 无人应答=拒绝，节点卡死无终态。
- **极慢**：模型改走整份反复 `Write` + 造 `*_tmp.md` 临时文件（试 Bash 追加也被白名单拒），
  同一文档写十几遍。实测 spec-dev `plan_refinement` 单轮 30min，换 Sonnet 提速无效（瓶颈是重复生成量）。
判别：节点慢/挂起先看 stream 日志同一产物被 Write 几次、有无 `*_tmp.md`，别误判"文档太大/模型慢"。

但 Edit ≠ Bash：纯文档节点给 Edit（编辑文件，必需）**不给 Bash/auto**（无人值守执行任意命令才是真风险，最小权限）。
消费方项目须同步改两份：`agents.yaml`（运行时加载）+ `agents.example.yaml`（入库模板），否则新克隆仍踩坑。

这套模型来自 `G:\stock\strategy\research\agent_workflow\graph\phases.py` 的 `get_default_node_configs`。相关引擎修复见 [[agent-workflow-windows-fixes]]。
