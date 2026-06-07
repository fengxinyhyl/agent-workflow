"""ArtifactResolver — 产物流路径解析。

根据 RunContext 中的 artifacts 映射和预期产物流配置，
解析每个 task 需要的输入 artifact 路径。
"""

from __future__ import annotations

import os
from typing import Any

from ..context.run_context import RunContext


class ArtifactResolver:
    """产物流路径解析器。

    将 task 的 inputs 列表（如 ["plan_doc", "review_doc"]）
    解析为实际的 artifact 文件路径。

    解析优先级:
    1. RunContext.artifacts 中已 promote 的路径
    2. run_root/artifacts/<name>.md
    3. 返回 None（表示产物不可用）
    """

    def __init__(self, context: RunContext):
        self.context = context
        self.artifacts_dir = os.path.join(context.run_root, "artifacts")

    def resolve(self, artifact_name: str) -> str | None:
        """解析单个 artifact 的路径。"""
        # 1. 检查 RunContext 中的映射
        if artifact_name in self.context.artifacts:
            path = self.context.artifacts[artifact_name]
            if os.path.exists(path):
                return path

        # 2. 检查 artifacts 目录
        candidates = [
            os.path.join(self.artifacts_dir, f"{artifact_name}.md"),
            os.path.join(self.artifacts_dir, f"{artifact_name}.json"),
            os.path.join(self.artifacts_dir, f"{artifact_name}.yaml"),
            os.path.join(self.artifacts_dir, artifact_name),
        ]

        for path in candidates:
            if os.path.exists(path):
                return path

        return None

    def resolve_all(self, artifact_names: list[str]) -> dict[str, str | None]:
        """批量解析 artifact 路径。"""
        return {name: self.resolve(name) for name in artifact_names}

    def build_input_context(self, artifact_names: list[str]) -> str:
        """为 Agent prompt 构建 artifact 内容上下文。

        读取已解析的 artifact 文件内容，构建上下文字符串。
        """
        parts = []
        resolved = self.resolve_all(artifact_names)

        for name, path in resolved.items():
            if path is None:
                parts.append(f"## {name}\n\n*（产物不可用）*\n")
                continue

            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 限制上下文大小（最多 8000 字符）
                if len(content) > 8000:
                    content = content[:8000] + "\n\n... (内容已截断)"
                parts.append(f"## {name}\n\n{content}\n")
            except Exception as e:
                parts.append(f"## {name}\n\n*（读取失败: {e}）*\n")

        return "\n".join(parts)
