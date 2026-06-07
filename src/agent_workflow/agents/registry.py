"""AgentRegistry — Agent 注册表。

管理所有可用的 Agent 适配器实例。
支持按名称、provider、role 查找 Agent。
"""

from __future__ import annotations

from typing import Any, Type

from ..config.models import AgentModel
from .base import BaseAgent
from .mock import MockAgent


class AgentRegistry:
    """Agent 注册表。

    用法:
        registry = AgentRegistry(agents_config)
        registry.register("codex_plan", CodexCLI(agent_config))
        agent = registry.resolve("codex_plan")
        result = agent.execute(agent_input)
    """

    def __init__(self, agents_config: dict[str, AgentModel] | None = None):
        self._agents: dict[str, BaseAgent] = {}
        self._agent_configs: dict[str, AgentModel] = {}
        self._agent_classes: dict[str, Type[BaseAgent]] = {}

        # 注册内置类型
        self._register_builtin_classes()

        # 从配置创建实例
        if agents_config:
            for name, config in agents_config.items():
                self._agent_configs[name] = config

    def _register_builtin_classes(self):
        """注册内置 Agent 类型。"""
        self._agent_classes["mock"] = MockAgent
        try:
            from .codex_cli import CodexCLI
            self._agent_classes["codex"] = CodexCLI
        except ImportError:
            pass
        try:
            from .claude_cli import ClaudeCLI
            self._agent_classes["claude"] = ClaudeCLI
        except ImportError:
            pass

    def register(self, name: str, agent: BaseAgent):
        """注册一个 Agent 实例。"""
        self._agents[name] = agent

    def register_class(self, provider: str, agent_class: Type[BaseAgent]):
        """注册一个 Agent 类型。"""
        self._agent_classes[provider] = agent_class

    def resolve(self, name: str) -> BaseAgent:
        """按名称解析 Agent。

        解析顺序:
        1. 已注册的实例
        2. 根据配置中的 provider 创建实例
        3. 返回 MockAgent 作为 fallback
        """
        # 1. 已注册的实例
        if name in self._agents:
            return self._agents[name]

        # 2. 根据配置创建
        if name in self._agent_configs:
            config = self._agent_configs[name]
            agent = self._create_from_config(name, config)
            if agent:
                self._agents[name] = agent
                return agent

        # 3. MockAgent fallback
        mock = MockAgent({"name": name})
        self._agents[name] = mock
        return mock

    def _create_from_config(self, name: str, config: AgentModel) -> BaseAgent | None:
        """根据配置创建 Agent 实例。"""
        provider = config.provider
        agent_class = self._agent_classes.get(provider)

        if agent_class is None:
            # 尝试延迟加载
            if provider == "codex":
                try:
                    from .codex_cli import CodexCLI
                    agent_class = CodexCLI
                    self._agent_classes["codex"] = CodexCLI
                except ImportError:
                    pass
            elif provider == "claude":
                try:
                    from .claude_cli import ClaudeCLI
                    agent_class = ClaudeCLI
                    self._agent_classes["claude"] = ClaudeCLI
                except ImportError:
                    pass

        if agent_class is None:
            return None

        return agent_class({
            "name": name,
            "provider": config.provider,
            "command": config.command,
            "cwd": config.cwd,
            "timeout_seconds": config.timeout_seconds,
        })

    def list_agents(self) -> list[str]:
        """列出所有已注册的 Agent 名称。"""
        names = set(self._agents.keys())
        names.update(self._agent_configs.keys())
        return sorted(names)

    def get_agent_info(self, name: str) -> dict[str, Any]:
        """获取 Agent 信息。"""
        agent = self._agents.get(name)
        config = self._agent_configs.get(name)

        info = {"name": name, "registered": agent is not None, "has_config": config is not None}

        if config:
            info["provider"] = config.provider
            info["sandbox"] = config.sandbox

        return info
