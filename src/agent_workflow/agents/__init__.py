"""agents 模块 — Agent 适配器。

P0 提供:
- BaseAgent: 基类
- MockAgent: Mock Agent（用于测试和 dry-run）
- CodexCLI: Codex CLI 适配器
- ClaudeCLI: Claude CLI 适配器
- CommandAgent: 通用命令代理（默认禁用）
- AgentRegistry: Agent 注册表
"""

from .base import BaseAgent
from .registry import AgentRegistry
from .mock import MockAgent
from .codex_cli import CodexCLI
from .claude_cli import ClaudeCLI
from .command import CommandAgent

__all__ = [
    "BaseAgent",
    "AgentRegistry",
    "MockAgent",
    "CodexCLI",
    "ClaudeCLI",
    "CommandAgent",
]
