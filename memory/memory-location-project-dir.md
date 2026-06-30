---
name: memory-location-project-dir
description: 本项目记忆写入项目根 memory/ 目录，而非 Claude Code 全局 memory 目录
metadata:
  type: feedback
---

本项目的记忆系统位于**项目根目录** `F:\code\agent-workflow\memory\`，由 AGENTS.md「记忆系统」章节定义，**不是** Claude Code 全局的 `C:\Users\<user>\.claude\projects\...\memory\`。

**Why:** 2026-06-27 用户明确纠正——曾误将记忆写到全局目录。AGENTS.md 是项目指令，优先级高于 harness 默认行为。

**How to apply:** 写记忆时一律用项目根 `memory/`，格式遵循 AGENTS.md：每文件一条事实，带 frontmatter（name/description/metadata.type），写完在 `memory/MEMORY.md` 加一行索引。type 取 user/feedback/project/reference。
