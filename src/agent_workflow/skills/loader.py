"""SkillLoader — 从文件系统加载 Skill。"""

from __future__ import annotations

import os
import yaml
from typing import Any

from .model import SkillModel, SkillPolicy


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter（如果存在）。"""
    if content.startswith("---\n") or content.startswith("---\r\n"):
        # 查找第二个 ---
        end = content.find("\n---", 4)
        if end == -1:
            end = content.find("\r\n---", 4)
        if end > 0:
            try:
                meta = yaml.safe_load(content[4:end])
                body = content[end + 4:].strip()
                return meta or {}, body
            except yaml.YAMLError:
                pass
    return {}, content


def load_skill(path: str, required: bool = False) -> SkillModel | None:
    """从文件加载一个 Skill。

    支持:
    - .md 文件（纯 markdown 或含 YAML frontmatter）
    - .yaml/.yml 文件（结构化 skill 定义）

    用法:
        skill = load_skill("examples/software-dev/skills/agent-workflow-lifecycle/skill.yaml")
    """
    if not os.path.exists(path):
        return None

    name = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()

    if ext in (".yaml", ".yml"):
        return _load_yaml_skill(path, required=required)
    elif ext in (".md", ".markdown"):
        return _load_markdown_skill(path, name, required=required)
    else:
        # 尝试作为 markdown 读取
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return None

        meta, body = _parse_frontmatter(content)
        return SkillModel(
            name=meta.get("name", name),
            description=meta.get("description", ""),
            content=body,
            path=path,
            version=str(meta.get("version", "1")),
            required=required or meta.get("required", False),
            policy=SkillPolicy(**meta.get("policy", {})),
            dependencies=meta.get("dependencies", []),
        )


def _load_yaml_skill(path: str, required: bool = False) -> SkillModel | None:
    """加载 YAML 格式的 skill。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return None

    if data is None:
        return None

    skill_data = data.get("skill", data)

    policy_data = skill_data.get("policy", {})
    return SkillModel(
        name=skill_data.get("name", os.path.basename(path)),
        description=skill_data.get("description", ""),
        content=skill_data.get("content", skill_data.get("description", "")),
        path=path,
        version=str(skill_data.get("version", "1")),
        required=required or skill_data.get("required", False),
        policy=SkillPolicy(
            allowed_decisions=policy_data.get("allowed_decisions", []),
            required_inputs=policy_data.get("required_inputs", []),
            forbidden_actions=policy_data.get("forbidden_actions", []),
        ),
        dependencies=skill_data.get("dependencies", []),
    )


def _load_markdown_skill(
    path: str,
    name: str,
    required: bool = False,
) -> SkillModel | None:
    """加载 Markdown 格式的 skill。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    meta, body = _parse_frontmatter(content)
    return SkillModel(
        name=meta.get("name", name),
        description=meta.get("description", ""),
        content=body,
        path=path,
        version=str(meta.get("version", "1")),
        required=required or meta.get("required", False),
        policy=SkillPolicy(**meta.get("policy", {})),
        dependencies=meta.get("dependencies", []),
    )


def list_skills(skills_dir: str) -> list[str]:
    """列出目录下的所有 skill 文件。"""
    if not os.path.exists(skills_dir) or not os.path.isdir(skills_dir):
        return []

    skills = []
    for f in os.listdir(skills_dir):
        fpath = os.path.join(skills_dir, f)
        if os.path.isfile(fpath):
            ext = os.path.splitext(f)[1].lower()
            if ext in (".yaml", ".yml", ".md", ".markdown"):
                skills.append(fpath)
        elif os.path.isdir(fpath):
            # 检查目录下是否有 skill.yaml
            skill_yaml = os.path.join(fpath, "skill.yaml")
            if os.path.exists(skill_yaml):
                skills.append(skill_yaml)

    return skills


class SkillLoader:
    """批量 Skill 加载器。

    用法:
        loader = SkillLoader("examples/software-dev/skills/")
        skills = loader.load_all(["agent-workflow-lifecycle"])
        missing = loader.get_missing()
    """

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self._loaded: dict[str, SkillModel] = {}
        self._missing: list[str] = []

    def load_all(self, names: list[str]) -> dict[str, SkillModel]:
        """批量加载 skill。"""
        for name in names:
            if name in self._loaded:
                continue

            # 尝试多种路径
            candidates = [
                os.path.join(self.skills_dir, name, "skill.yaml"),
                os.path.join(self.skills_dir, name, "skill.yml"),
                os.path.join(self.skills_dir, f"{name}.yaml"),
                os.path.join(self.skills_dir, f"{name}.yml"),
                os.path.join(self.skills_dir, f"{name}.md"),
                os.path.join(self.skills_dir, name, "SKILL.md"),
            ]

            found = False
            for path in candidates:
                skill = load_skill(path, required=True)
                if skill is not None:
                    self._loaded[name] = skill
                    # 递归加载依赖
                    if skill.dependencies:
                        self.load_all(skill.dependencies)
                    found = True
                    break

            if not found:
                self._missing.append(name)

        return self._loaded

    def get_missing(self) -> list[str]:
        """获取加载失败的 skill 名称。"""
        return list(self._missing)

    def get_required_skills(self) -> list[SkillModel]:
        """获取所有 required=True 的 skill（缺失时 fail-fast）。"""
        return [s for s in self._loaded.values() if s.required]
