"""ArtifactResolver — 产物流路径解析。

根据 RunContext 中的 artifacts 映射和预期产物流配置，
解析每个 task 需要的输入 artifact 路径。

支持版本引用语法：
  plan_doc          → 最新版（向后兼容）
  plan_doc:latest   → 最新版
  plan_doc:v2       → 第 2 版
  plan_doc:all      → 所有版本合并摘要
"""

from __future__ import annotations

import os
from typing import Any

from ..context.run_context import RunContext


class ArtifactResolver:
    """产物流路径解析器。

    将 task 的 inputs 列表（如 ["plan_doc:latest", "review_doc:v2"]）
    解析为实际的 artifact 文件路径。

    解析优先级:
    1. 版本引用语法（:latest / :vN / :all）
    2. RunContext.artifact_versions 版本链
    3. RunContext.artifacts 中已 promote 的路径
    4. run_root/artifacts/<name>.md / .json / .yaml
    5. 返回 None（表示产物不可用）
    """

    def __init__(self, context: RunContext):
        self.context = context
        self.artifacts_dir = os.path.join(context.run_root, "artifacts")

    def resolve(self, artifact_ref: str) -> str | None:
        """解析 artifact 引用。

        支持语法：
          plan_doc          → 最新版（向后兼容）
          plan_doc:latest   → 最新版
          plan_doc:v2       → 第 2 版（1-indexed）
          plan_doc:all      → 所有版本合并摘要文件路径
        """
        # 解析引用语法
        if ":" in artifact_ref:
            name, qualifier = artifact_ref.split(":", 1)
        else:
            name, qualifier = artifact_ref, "latest"

        if qualifier == "latest":
            return self._resolve_latest(name)
        elif qualifier.startswith("v") and qualifier[1:].isdigit():
            v = int(qualifier[1:])
            return self._resolve_version(name, v)
        elif qualifier == "all":
            return self._build_versions_summary(name)
        else:
            # 未知 qualifier，fallback 到 latest
            return self._resolve_latest(name)

    def _resolve_latest(self, name: str) -> str | None:
        """解析最新版本。"""
        # 1. 检查版本链
        versions = self.context.artifact_versions.get(name, [])
        if versions:
            path = versions[-1]
            if os.path.exists(path):
                return path

        # 2. 检查 artifacts 映射
        if name in self.context.artifacts:
            path = self.context.artifacts[name]
            if os.path.exists(path):
                return path

        # 3. 检查 artifacts 目录
        return self._find_in_artifacts_dir(name)

    def _resolve_version(self, name: str, v: int) -> str | None:
        """解析指定版本（1-indexed）。"""
        path = self.context.get_version(name, v)
        if path and os.path.exists(path):
            return path
        # fallback: 直接在 artifacts 目录查找 plan_doc-v2.md
        return self._find_in_artifacts_dir(f"{name}-v{v}")

    def _find_in_artifacts_dir(self, name: str) -> str | None:
        """在 artifacts 目录中按名称查找文件。"""
        candidates = [
            os.path.join(self.artifacts_dir, f"{name}.md"),
            os.path.join(self.artifacts_dir, f"{name}.json"),
            os.path.join(self.artifacts_dir, f"{name}.yaml"),
            os.path.join(self.artifacts_dir, name),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _build_versions_summary(self, name: str) -> str | None:
        """将所有版本合并为一个摘要文件，返回其路径。

        生成的摘要文件放在 artifacts/ 目录下，文件名为 {name}_versions_summary.md。
        每次调用会重新生成（确保内容是最新的）。
        """
        versions = self.context.artifact_versions.get(name, [])
        if not versions:
            # 没有版本链，检查单个 artifact
            single = self._resolve_latest(name)
            if single is None:
                return None
            versions = [single]

        summary_path = os.path.join(
            self.artifacts_dir, f"{name}_versions_summary.md"
        )

        lines = [f"# {name} 版本历史", ""]
        for i, path in enumerate(versions, 1):
            lines.append(f"## v{i}")
            lines.append("")
            try:
                with open(path, "r", encoding="utf-8") as vf:
                    content = vf.read()
                # 限制每个版本最多 10000 字符
                if len(content) > 10000:
                    content = content[:10000] + "\n\n... (内容已截断)"
                lines.append(content)
            except Exception:
                lines.append(f"*(无法读取 {path})*")
            lines.append("")
            lines.append("---")
            lines.append("")

        os.makedirs(self.artifacts_dir, exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return summary_path

    def resolve_all(self, artifact_names: list[str]) -> dict[str, str | None]:
        """批量解析 artifact 引用。"""
        return {name: self.resolve(name) for name in artifact_names}

    def build_input_context(self, artifact_names: list[str]) -> str:
        """为 Agent prompt 构建 artifact 内容上下文。

        读取已解析的 artifact 文件内容，构建上下文字符串。
        对 :all 引用，直接使用 _build_versions_summary 生成的摘要文件。
        """
        parts = []
        resolved = self.resolve_all(artifact_names)

        for ref, path in resolved.items():
            # 提取纯名称用于显示
            display_name = ref.split(":")[0] if ":" in ref else ref

            if path is None:
                parts.append(f"## {display_name}\n\n*（产物不可用）*\n")
                continue

            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 限制上下文大小（最多 8000 字符）
                if len(content) > 8000:
                    content = content[:8000] + "\n\n... (内容已截断)"
                parts.append(f"## {display_name}\n\n{content}\n")
            except Exception as e:
                parts.append(f"## {display_name}\n\n*（读取失败: {e}）*\n")

        return "\n".join(parts)
