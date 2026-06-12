---
name: agent-workflow-windows-fixes
description: agent-workflow 引擎在 Windows 上调用 Claude CLI 的三类根因修复（编码/命令包裹/权限）
metadata:
  type: project
---

agent-workflow 引擎（`G:\agent-workflow`，pip install -e 安装）在 Windows 上驱动 Claude/Codex CLI 时，2026-06-12 修复了三个独立根因。引擎源码改动在 `G:\agent-workflow\src\agent_workflow\`：

1. **Popen 编码（最关键）**：`agents/base.py` 的 `_run_with_cancel_poll` 中 `subprocess.Popen(text=True)` 缺 `encoding`，Windows 默认用 GBK 解码 Claude 的 UTF-8 stream-json 输出 → reader 线程抛 `UnicodeDecodeError` → stdout 丢失 → `_parse_stream_output` 走 fallback 返回 `decision="done"`。修复：加 `encoding="utf-8", errors="replace"`。

2. **命令包裹**：`_wrap_command_for_os` 原只对带 `.cmd`/`.bat` 扩展名的字符串包裹 `cmd /c`。无扩展名命令（`codex`/`claude`/`cc-deepseek`）经 PATHEXT 解析真实文件是 `.cmd`，但 Popen 直接执行会失败。修复：无扩展名时用 `shutil.which` 解析真实路径再判断扩展名。

3. **权限链路**：`AgentModel`(config/models.py)、loader、registry 原本不传 `permission_mode`/`allowed_tools` 给 adapter。打通后，权限模型移植自 `G:\stock\strategy\research\agent_workflow`（已验证实现）——靠 `--allowedTools` 工具白名单授权，execute 节点用 `permission_mode=auto` + 含 `Bash` 的工具列表才能执行 python。详见 [[claude-permission-mode-semantics]]。

排查结论：之前怀疑的"缺环境变量/回退 MockAgent/POSIX 脚本不可执行"都不是真因——Runner 正确解析到 ClaudeCLI，真因是上述编码 bug。
