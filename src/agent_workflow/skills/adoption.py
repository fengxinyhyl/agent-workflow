"""Adoption Protocol — Skill 采用协议。

P0 必须:
- 加载 workflow required skills
- 加载 task skills
- 生成 skill-adoption artifact 到 staging
- adoption 通过 validator 后 promote
- adoption summary 注入 AgentInput
- required skill 缺失 fail-fast
"""

from __future__ import annotations

import os
from typing import Any

from ..context.run_context import RunContext
from .model import SkillModel
from .loader import SkillLoader


class AdoptionProtocol:
    """Skill 采用协议。

    在每次 state 执行前运行:
    1. 加载 required skills
    2. 加载 task-specific skills
    3. 生成 adoption summary
    4. 写入 staging/<state>/skill_adoption.md
    """

    def __init__(
        self,
        skills_dir: str,
        required_skills: list[str] | None = None,
    ):
        self.skills_dir = skills_dir
        self.required_skills = required_skills or []
        self.loader = SkillLoader(skills_dir)

    def adopt(
        self,
        state_name: str,
        task_skills: list[str] | None = None,
        context: RunContext | None = None,
    ) -> dict[str, SkillModel]:
        """为指定 state 加载所需 skills。"""
        all_skill_names = list(self.required_skills)
        if task_skills:
            for s in task_skills:
                if s not in all_skill_names:
                    all_skill_names.append(s)

        skills = self.loader.load_all(all_skill_names)
        missing = self.loader.get_missing()

        # required skill 缺失 → fail-fast
        required_missing = [
            s for s in missing
            if s in self.required_skills
        ]
        if required_missing:
            raise RuntimeError(
                f"必需的 Skill 缺失: {', '.join(required_missing)}"
            )

        return skills

    def write_adoption_artifact(
        self,
        run_root: str,
        state_name: str,
        skills: dict[str, SkillModel],
    ) -> str:
        """写入 skill adoption artifact 到 staging。

        返回 staging 路径。
        """
        staging_dir = os.path.join(run_root, "staging", state_name)
        os.makedirs(staging_dir, exist_ok=True)

        adoption_path = os.path.join(staging_dir, "skill_adoption.md")
        lines = [
            f"# Skill Adoption: {state_name}",
            "",
            f"采纳时间: {state_name}",
            "",
        ]

        if skills:
            lines.append("## 已加载 Skills")
            lines.append("")
            for name, skill in skills.items():
                lines.append(f"- **{name}**: {skill.description}")
                if skill.dependencies:
                    lines.append(f"  依赖: {', '.join(skill.dependencies)}")
            lines.append("")
        else:
            lines.append("*（无额外 skills）*")
            lines.append("")

        # 策略汇总
        lines.append("## 策略约束")
        lines.append("")
        for name, skill in skills.items():
            policy = skill.policy
            if policy.allowed_decisions:
                lines.append(f"- {name}: allowed_decisions = {policy.allowed_decisions}")
            if policy.forbidden_actions:
                lines.append(f"- {name}: forbidden_actions = {policy.forbidden_actions}")

        content = "\n".join(lines)
        with open(adoption_path, "w", encoding="utf-8") as f:
            f.write(content)

        return adoption_path

    def build_summary(self, skills: dict[str, SkillModel]) -> str:
        """构建 skill adoption summary 用于注入 AgentInput。"""
        if not skills:
            return ""

        parts = ["## 技能指引", ""]
        for name, skill in skills.items():
            parts.append(skill.get_summary())
            parts.append("")

        return "\n".join(parts)


def get_adoption_summary(context: RunContext) -> str:
    """从 RunContext 获取当前 state 的 skill adoption summary。

    尝试读取 staging 中的 skill_adoption.md。
    """
    if context is None:
        return ""

    adoption_path = os.path.join(
        context.run_root, "staging", context.current_state, "skill_adoption.md"
    )

    if os.path.exists(adoption_path):
        try:
            with open(adoption_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass

    return ""


def write_adoption_artifact(
    run_root: str,
    state_name: str,
    skills: dict[str, SkillModel],
) -> str:
    """便捷函数：写入 skill adoption artifact。"""
    protocol = AdoptionProtocol(skills_dir="")
    return protocol.write_adoption_artifact(run_root, state_name, skills)
