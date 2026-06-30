"""ValidResult — 三态校验结果 + RouteShape 路由形态。

Validator 纯函数化：Validator 只做数据裁决，返回三态 ValidResult。
Runner 读 ValidResult → repairable? → 编排 Repair（有界）→ 路由。

命名与旧 base.ValidationResult 明确区分，避免同名冲突。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class RouteShape(NamedTuple):
    """节点的路由形态（纯数据，Validator 只读，天然 immutable）。

    Validator 通过此结构感知节点是否有分支，无需知道节点类型名称。
    """

    has_on: bool = False
    has_next: bool = False
    allowed_decisions: tuple[str, ...] = ()


@dataclass
class ValidResult:
    """三态校验结果。

    valid=True           → 全部通过，Runner 直接路由
    valid=False + repairable=True   → decision ∉ allowed 或 invalid_output → Runner 进入 Repair
    valid=False + repairable=False  → 不可救（缺少必需字段、进程崩溃等）→ Runner 直接 failed
    """

    valid: bool = True
    repairable: bool = False
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
