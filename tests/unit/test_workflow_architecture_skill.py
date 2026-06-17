from pathlib import Path

import pytest

from agent_workflow.skills.loader import SkillLoader, load_skill


ROOT = Path(__file__).resolve().parents[2]


def test_workflow_architecture_skill_loads_from_project_skills():
    skill_path = ROOT / ".claude" / "skills" / "workflow-architecture" / "SKILL.md"

    skill = load_skill(str(skill_path))

    assert skill is not None
    assert skill.name == "workflow-architecture"
    assert "自动触发入口和主维护位置是 `.claude/skills/workflow-architecture/SKILL.md`" in skill.content
    assert "`.agents/skills` 使用目录链接指向 `.claude/skills`" in skill.content
    assert "Workflow Architect 八步协议" in skill.content
    assert "Step1 任务分类" in skill.content
    assert "Step8 工作流输出" in skill.content
    assert "Pattern Selection" in skill.content
    assert "Architecture Convergence Pattern" in skill.content
    assert "Research / Evidence Pattern" in skill.content
    assert "Rubric Evaluation Pattern" in skill.content
    assert "structure_constraints_objectives" in skill.content
    assert "Reversibility cost" in skill.content
    assert "条件回流" in skill.content
    assert "allowed_decisions" in skill.content
    assert skill.policy.allowed_decisions == []


def test_project_skill_loader_can_discover_workflow_architecture_skill():
    loader = SkillLoader(str(ROOT / ".claude" / "skills"))

    loaded = loader.load_all(["workflow-architecture"])

    assert "workflow-architecture" in loaded
    assert loader.get_missing() == []


def test_agents_skills_links_to_claude_skills():
    agents_skills = ROOT / ".agents" / "skills"
    claude_skills = ROOT / ".claude" / "skills"

    if not agents_skills.exists():
        pytest.skip(".agents/skills 是本机自动触发链接，干净检出后可按需创建")

    assert agents_skills.exists()
    assert agents_skills.resolve() == claude_skills.resolve()

    loader = SkillLoader(str(agents_skills))
    loaded = loader.load_all(["workflow-architecture"])

    assert "workflow-architecture" in loaded
