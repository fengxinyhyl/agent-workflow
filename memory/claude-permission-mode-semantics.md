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

正确放开工具的机制是 **`--allowedTools`**（逗号分隔白名单），配合 `permission_mode`：
- 写文档节点：`default` + `Read,Grep,Glob,Write`
- 执行 python/命令节点：`auto` + `Read,Grep,Glob,Write,Edit,Bash`
- 审计节点（需写报告）：`default` + `Read,Grep,Glob,Write`

这套模型来自 `G:\stock\strategy\research\agent_workflow\graph\phases.py` 的 `get_default_node_configs`。相关引擎修复见 [[agent-workflow-windows-fixes]]。
