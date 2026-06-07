"""Skill 模型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillPolicy:
    """Skill 策略配置。

    allowed_decisions: 此 skill 限制的 allowed decisions
    required_inputs: 必需的 artifact 输入
    forbidden_actions: 禁止的操作
    """

    allowed_decisions: list[str] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_decisions": self.allowed_decisions,
            "required_inputs": self.required_inputs,
            "forbidden_actions": self.forbidden_actions,
        }


@dataclass
class SkillModel:
    """Skill 定义模型。

    字段说明:
      name: skill 名称
      description: 描述
      content: skill 内容（markdown 文本）
      path: skill 文件路径
      version: 版本号
      required: 是否必需（缺失时 fail-fast）
      policy: skill 策略
      dependencies: 依赖的其他 skill 名称
    """

    name: str = ""
    description: str = ""
    content: str = ""
    path: str = ""
    version: str = "1"
    required: bool = False
    policy: SkillPolicy = field(default_factory=SkillPolicy)
    dependencies: list[str] = field(default_factory=list)

    def get_summary(self) -> str:
        """生成 skill 摘要（用于注入 Agent prompt）。"""
        parts = [f"### {self.name}"]
        if self.description:
            parts.append(f"> {self.description}")
        if self.content:
            # 限制注入内容长度（最多 3000 字符）
            content = self.content[:3000]
            if len(self.content) > 3000:
                content += "\n\n...(内容已截断)"
            parts.append(content)
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "required": self.required,
            "policy": self.policy.to_dict(),
            "dependencies": self.dependencies,
        }
