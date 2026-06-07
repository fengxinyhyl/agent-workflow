"""Skill Policy — 解析和合并 skill 策略。"""

from __future__ import annotations

from typing import Any

from .model import SkillModel, SkillPolicy


def resolve_skill_policy(
    skills: dict[str, SkillModel],
    task_allowed_decisions: list[str] | None = None,
) -> dict[str, Any]:
    """解析合并后的 skill 策略。

    合并规则:
    - allowed_decisions: skills 策略的交集 ∩ task 限制
    - forbidden_actions: skills 策略的并集
    - required_inputs: skills 策略的并集
    """
    # 收集所有 skill 的 allowed_decisions
    skill_allowed = None
    for skill in skills.values():
        if skill.policy.allowed_decisions:
            if skill_allowed is None:
                skill_allowed = set(skill.policy.allowed_decisions)
            else:
                skill_allowed &= set(skill.policy.allowed_decisions)

    # 与 task 限制取交集
    if task_allowed_decisions and skill_allowed is not None:
        skill_allowed &= set(task_allowed_decisions)
    elif task_allowed_decisions and skill_allowed is None:
        skill_allowed = set(task_allowed_decisions)

    # 收集 forbidden_actions
    forbidden = set()
    for skill in skills.values():
        forbidden.update(skill.policy.forbidden_actions)

    # 收集 required_inputs
    required_inputs = set()
    for skill in skills.values():
        required_inputs.update(skill.policy.required_inputs)

    result = {}
    if skill_allowed is not None:
        result["allowed_decisions"] = sorted(skill_allowed)
    if forbidden:
        result["forbidden_actions"] = sorted(forbidden)
    if required_inputs:
        result["required_inputs"] = sorted(required_inputs)

    return result
