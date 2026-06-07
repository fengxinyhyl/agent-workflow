"""Skill Context — 构建注入 AgentInput 的 skill 上下文。"""

from __future__ import annotations

from typing import Any

from .model import SkillModel


def build_skill_context(
    skills: dict[str, SkillModel],
) -> tuple[str, dict[str, Any]]:
    """从已加载的 skills 构建上下文。

    返回 (skill_context_text, skill_policy_dict)。

    skill_context_text: 注入 Agent prompt 的技能指引
    skill_policy_dict: 合并后的策略约束
    """
    if not skills:
        return "", {}

    # 构建技能指引文本
    context_parts = ["## 技能指引", ""]
    for name, skill in skills.items():
        context_parts.append(f"### {name}")
        if skill.description:
            context_parts.append(f"> {skill.description}")
        if skill.content:
            content = skill.content[:3000]
            context_parts.append(content)
        context_parts.append("")

    # 合并策略
    merged_policy = _merge_policies(skills)

    return "\n".join(context_parts), merged_policy


def _merge_policies(skills: dict[str, SkillModel]) -> dict[str, Any]:
    """合并多个 skill 的策略约束。

    - allowed_decisions: 取交集（所有 skill 都允许的）
    - forbidden_actions: 取并集（任一 skill 禁止的）
    - required_inputs: 取并集（所有 skill 要求的）
    """
    all_allowed = None
    all_forbidden = set()
    all_required_inputs = set()

    for skill in skills.values():
        policy = skill.policy

        if policy.allowed_decisions:
            if all_allowed is None:
                all_allowed = set(policy.allowed_decisions)
            else:
                all_allowed &= set(policy.allowed_decisions)

        all_forbidden.update(policy.forbidden_actions)
        all_required_inputs.update(policy.required_inputs)

    result = {}
    if all_allowed is not None:
        result["allowed_decisions"] = sorted(all_allowed)
    if all_forbidden:
        result["forbidden_actions"] = sorted(all_forbidden)
    if all_required_inputs:
        result["required_inputs"] = sorted(all_required_inputs)

    return result
