"""artifacts 模块 — Staging / Promotion / Resolver。

规则（v4 计划 §9）:
- Agent 只能写 staging path
- Validator 只读取 staging path
- Validator 通过后由 core promote 到 artifacts
- Validator 失败时 staging 保留用于排查，但不污染正式 artifacts
"""

from .staging import ensure_staging_dir, get_staging_path
from .promotion import promote_artifact, validate_and_promote
from .resolver import ArtifactResolver

__all__ = [
    "ensure_staging_dir",
    "get_staging_path",
    "promote_artifact",
    "validate_and_promote",
    "ArtifactResolver",
]
