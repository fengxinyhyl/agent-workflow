"""skills 模块 — Skill 模型、加载、Adoption Protocol。

P0 必须:
- 加载 workflow required skills
- 加载 task skills
- 生成 skill-adoption artifact 到 staging
- adoption 通过 validator 后 promote
- adoption summary 注入 AgentInput
- required skill 缺失 fail-fast
"""

from .model import SkillModel, SkillPolicy
from .loader import SkillLoader, load_skill, list_skills
from .adoption import (
    AdoptionProtocol,
    get_adoption_summary,
    write_adoption_artifact,
)
from .context import build_skill_context
from .policy import resolve_skill_policy

__all__ = [
    "SkillModel",
    "SkillPolicy",
    "SkillLoader",
    "load_skill",
    "list_skills",
    "AdoptionProtocol",
    "get_adoption_summary",
    "write_adoption_artifact",
    "build_skill_context",
    "resolve_skill_policy",
]
