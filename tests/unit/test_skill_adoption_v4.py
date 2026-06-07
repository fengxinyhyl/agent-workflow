"""测试 Skill Adoption 协议。"""

import os
import tempfile
import pytest

from agent_workflow.skills import (
    SkillModel,
    SkillPolicy,
    SkillLoader,
    load_skill,
    AdoptionProtocol,
    build_skill_context,
    resolve_skill_policy,
)
from agent_workflow.context import RunContext


class TestSkillModel:
    """Skill 模型测试。"""

    def test_create(self):
        skill = SkillModel(
            name="test-skill",
            description="测试技能",
            content="这是测试技能的内容",
            required=True,
        )
        assert skill.name == "test-skill"
        assert skill.required is True

    def test_get_summary(self):
        skill = SkillModel(
            name="test-skill",
            description="测试技能",
            content="## 规则\n\n1. 做 X\n2. 不要做 Y",
        )
        summary = skill.get_summary()
        assert "test-skill" in summary
        assert "做 X" in summary

    def test_content_truncation(self):
        skill = SkillModel(
            name="large-skill",
            description="大技能",
            content="A" * 5000,  # 超过 3000 字符限制
        )
        summary = skill.get_summary()
        assert "截断" in summary


class TestSkillLoader:
    """Skill 加载器测试。"""

    def test_load_yaml_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 YAML skill 文件
            skill_dir = os.path.join(tmpdir, "test-skill")
            os.makedirs(skill_dir)
            skill_path = os.path.join(skill_dir, "skill.yaml")
            with open(skill_path, "w", encoding="utf-8") as f:
                f.write("""skill:
  name: test-skill
  version: "1"
  description: 测试技能
  required: true
  policy:
    allowed_decisions:
      - approve
      - revise
    forbidden_actions:
      - delete_files
  content: |
    # 测试技能

    这是测试技能的内容。
""")

            skill = load_skill(skill_path, required=True)
            assert skill is not None
            assert skill.name == "test-skill"
            assert skill.policy.allowed_decisions == ["approve", "revise"]
            assert "delete_files" in skill.policy.forbidden_actions

    def test_load_markdown_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = os.path.join(tmpdir, "test-skill.md")
            with open(skill_path, "w", encoding="utf-8") as f:
                f.write("""---
name: test-md-skill
description: 来自 Markdown 的技能
---
# 技能内容

这些是技能的实际内容。
""")

            skill = load_skill(skill_path)
            assert skill is not None
            assert skill.name == "test-md-skill"
            assert "技能内容" in skill.content

    def test_load_nonexistent(self):
        skill = load_skill("/nonexistent/skill.yaml")
        assert skill is None

    def test_skill_loader_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建多个 skill
            skill1_dir = os.path.join(tmpdir, "skill1")
            os.makedirs(skill1_dir)
            with open(os.path.join(skill1_dir, "skill.yaml"), "w") as f:
                f.write("skill:\n  name: skill1\n  description: Skill 1\n")

            skill2_dir = os.path.join(tmpdir, "skill2")
            os.makedirs(skill2_dir)
            with open(os.path.join(skill2_dir, "skill.yaml"), "w") as f:
                f.write("skill:\n  name: skill2\n  description: Skill 2\n  dependencies:\n    - skill1\n")

            loader = SkillLoader(tmpdir)
            skills = loader.load_all(["skill1", "skill2"])
            assert "skill1" in skills
            assert "skill2" in skills

            missing = loader.get_missing()
            assert "skill1" not in missing

    def test_skill_loader_missing_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = SkillLoader(tmpdir)
            skills = loader.load_all(["nonexistent-skill"])
            missing = loader.get_missing()
            assert "nonexistent-skill" in missing


class TestSkillAdoption:
    """Skill Adoption 协议测试。"""

    def test_write_adoption_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            protocol = AdoptionProtocol(skills_dir="")

            skills = {
                "test-skill": SkillModel(
                    name="test-skill",
                    description="测试技能",
                    policy=SkillPolicy(
                        allowed_decisions=["approve", "revise"],
                        forbidden_actions=["delete_files"],
                    ),
                ),
            }

            path = protocol.write_adoption_artifact(run_root, "claude_review_plan", skills)
            assert os.path.exists(path)
            assert "skill_adoption" in path

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "test-skill" in content
            assert "approve" in content

    def test_build_summary(self):
        protocol = AdoptionProtocol(skills_dir="")
        skills = {
            "test-skill": SkillModel(
                name="test-skill",
                description="测试技能",
                content="这是测试内容",
            ),
        }
        summary = protocol.build_summary(skills)
        assert "test-skill" in summary
        assert "测试内容" in summary

    def test_required_skill_missing_fail_fast(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            protocol = AdoptionProtocol(
                skills_dir=tmpdir,
                required_skills=["critical-skill"],
            )
            with pytest.raises(RuntimeError) as exc:
                protocol.adopt(state_name="test")
            assert "缺失" in str(exc.value)


class TestSkillContext:
    """Skill 上下文构建测试。"""

    def test_build_context(self):
        skills = {
            "s1": SkillModel(
                name="s1",
                description="技能1",
                content="内容1",
                policy=SkillPolicy(allowed_decisions=["done"]),
            ),
            "s2": SkillModel(
                name="s2",
                description="技能2",
                content="内容2",
                policy=SkillPolicy(allowed_decisions=["done", "fail"], forbidden_actions=["rm"]),
            ),
        }

        context_text, policy = build_skill_context(skills)

        assert "s1" in context_text
        assert "s2" in context_text
        assert "done" in policy.get("allowed_decisions", [])
        assert "rm" in policy.get("forbidden_actions", [])

    def test_resolve_policy(self):
        skills = {
            "s1": SkillModel(
                name="s1",
                policy=SkillPolicy(allowed_decisions=["approve", "revise", "reject"]),
            ),
            "s2": SkillModel(
                name="s2",
                policy=SkillPolicy(allowed_decisions=["approve", "reject"]),
            ),
        }

        policy = resolve_skill_policy(skills, task_allowed_decisions=["approve", "revise", "reject"])
        # 交集: s1 ∩ s2 ∩ task = {approve, reject}
        assert "approve" in policy["allowed_decisions"]
        assert "reject" in policy["allowed_decisions"]
        # revise 不在 s2 的 allowed 中，所以不在交集中
        assert "revise" not in policy["allowed_decisions"]
