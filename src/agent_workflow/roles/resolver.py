"""RoleResolver — Role → Agent 解析器。

Role 只是一个 agent alias，可选 fallback。
禁止承载：capability、policy、validator、contract、guard。
"""

from __future__ import annotations

from ..config.models import RoleModel, AgentModel


class RoleResolver:
    """Role → Agent 解析器。

    解析规则:
    1. 尝试 primary agent
    2. 如果 primary 不可用，尝试 fallback_agents
    3. 全部不可用 → 抛出异常

    用法:
        resolver = RoleResolver(roles_config, agents_config)
        agent_name = resolver.resolve("planner")
    """

    def __init__(
        self,
        roles: dict[str, RoleModel] | None = None,
        agents: dict[str, AgentModel] | None = None,
    ):
        self.roles = roles or {}
        self.agents = agents or {}

    def resolve(self, role_name: str) -> str:
        """解析 Role 到 Agent 名称。"""
        role = self.roles.get(role_name)
        if role is None:
            # 尝试直接作为 agent 名称
            if role_name in self.agents:
                return role_name
            raise ValueError(f"Role '{role_name}' 未定义且无对应 Agent")

        # 检查 primary agent 是否可用
        if role.agent and role.agent in self.agents:
            return role.agent

        # 尝试 fallback
        for fallback in role.fallback_agents:
            if fallback in self.agents:
                return fallback

        # 如果 primary agent 名称不在注册表中但也不为空
        if role.agent:
            return role.agent

        raise ValueError(
            f"Role '{role_name}': agent '{role.agent}' 和 fallback {role.fallback_agents} 均不可用"
        )

    def resolve_with_metadata(self, role_name: str) -> tuple[str, dict]:
        """解析 Role 并返回 agent 名称和元数据。"""
        agent_name = self.resolve(role_name)
        agent = self.agents.get(agent_name)

        metadata = {}
        if agent:
            metadata = {
                "agent_name": agent_name,
                "provider": agent.provider,
                "sandbox": agent.sandbox,
                "timeout_seconds": agent.timeout_seconds,
            }

        return agent_name, metadata

    def get_agent_config(self, agent_name: str) -> AgentModel | None:
        """获取 Agent 配置。"""
        return self.agents.get(agent_name)


def resolve_role(
    role: RoleModel,
    agents: dict[str, AgentModel],
) -> str:
    """纯函数：解析 Role → Agent 名称。

    参数:
      role: Role 配置
      agents: 可用 Agent 映射
    """
    if role.agent in agents:
        return role.agent
    for fallback in role.fallback_agents:
        if fallback in agents:
            return fallback
    return role.agent  # 即使不在注册表中也返回
