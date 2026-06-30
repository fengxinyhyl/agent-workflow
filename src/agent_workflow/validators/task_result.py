"""TaskResultValidator — 校验 Agent 输出的 TaskResult。

校验项:
- JSON 可解析性
- 必需字段存在
- status 在允许值范围内
- decision 在 allowed_decisions 范围内
- execution metadata 完整

Runtime v2: 新增纯函数 validate(data, route_shape) → ValidResult。
Validator 只做数据裁决，不调用 Agent。三态结果交由 Runner 编排 Repair。
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import BaseValidator, ValidationResult as BaseValidationResult
from .validation_result import RouteShape, ValidResult
from ..tasks.result import VALID_STATUSES, TaskResult


# ═══════════════════════════════════════════════════════════════════════════════
# 纯函数 validate: Validator 的唯一入口（Runtime v2）
# ═══════════════════════════════════════════════════════════════════════════════

def validate(data: dict[str, Any], route_shape: RouteShape) -> ValidResult:
    """纯函数: 对 TaskResult 数据做分层校验，返回三态 ValidResult。

    Runtime 层 (repairable=False):
      - schema_version < 1
      - 缺少必需字段 (task_id, state, status, summary, execution)
      - execution.started_at / finished_at 缺失
      - status 不在 VALID_STATUSES 中

    Workflow 层 (repairable=True):
      - status == "invalid_output"
      - has_on=True 且 decision 为 None
      - has_on=True 且 decision 不在 allowed_decisions 中

    Warnings（非阻塞）:
      - execution.exit_code 缺失
      - artifacts 中 name/staging_path 缺失
    """
    result = ValidResult()

    # ── 1. schema_version ──
    if data.get("schema_version", 0) < 1:
        result.valid = False
        result.repairable = False
        result.errors.append("schema_version 必须 >= 1")
        result.reason = "schema_version < 1，不可修复"

    # ── 2. 必需字段 ──
    required = ["task_id", "state", "status", "summary", "execution"]
    for field in required:
        if field not in data or not data[field]:
            result.valid = False
            result.repairable = False
            result.errors.append(f"缺少必需字段: {field}")
            if not result.reason:
                result.reason = f"缺少必需字段: {field}"

    # ── 3. status 有效性 ──
    # 注意：invalid_output 必须在 VALID_STATUSES 中。
    # 原因：先做 status 有效性检查（不在 VALID_STATUSES → repairable=False），
    # 再做 status=="invalid_output" 检查（→ repairable=True）。
    # 如果将来把 invalid_output 移出 VALID_STATUSES，两个判断会矛盾：
    # status 无效分支会先拦截并返回 repairable=False，导致 Repair 不可达。
    # 维护规则：invalid_output 始终保留在 VALID_STATUSES 中。
    status = data.get("status", "")
    if status and status not in VALID_STATUSES:
        result.valid = False
        result.repairable = False
        result.errors.append(f"无效 status: '{status}'，允许值: {VALID_STATUSES}")
        result.reason = result.reason or f"status '{status}' 不在允许范围，不可修复"

    # ── 4. execution metadata ──
    execution = data.get("execution", {})
    if isinstance(execution, dict):
        if not execution.get("started_at"):
            result.valid = False
            result.repairable = False
            result.errors.append("execution.started_at 必填")
            if not result.reason:
                result.reason = "execution.started_at 缺失，不可修复"
        if not execution.get("finished_at"):
            result.valid = False
            result.repairable = False
            result.errors.append("execution.finished_at 必填")
            if not result.reason:
                result.reason = "execution.finished_at 缺失，不可修复"
        if not execution.get("exit_code") and execution.get("exit_code") != 0:
            result.warnings.append("execution.exit_code 缺失")

    # ── 5. Workflow 层: status == "invalid_output" → repairable ──
    if status == "invalid_output":
        result.valid = False
        result.repairable = True
        result.errors.append("status=invalid_output，解析失败，需重新输出")
        result.reason = "Agent 输出解析失败 (invalid_output)，可尝试修复"

    # ── 6. Workflow 层: decision 合法性（仅在分支节点检查）──
    decision = data.get("decision")
    if route_shape.has_on:
        if decision is None:
            result.valid = False
            result.repairable = True
            result.errors.append("分支节点缺少 decision（decision 必填但为空）")
            result.reason = result.reason or "decision 必填但为空，可尝试修复"
        elif route_shape.allowed_decisions and decision not in route_shape.allowed_decisions:
            result.valid = False
            result.repairable = True
            result.errors.append(
                f"decision '{decision}' 不在 allowed_decisions "
                f"{list(route_shape.allowed_decisions)} 中"
            )
            result.reason = result.reason or f"decision '{decision}' 非法，可尝试修复"

    # ── 7. Warnings（非阻塞）──
    # Note: has_next + decision 非空的 warning 标记为 nice-to-have，首版不实现
    artifacts = data.get("artifacts", [])
    for i, artifact in enumerate(artifacts):
        if isinstance(artifact, dict):
            if not artifact.get("name"):
                result.warnings.append(f"artifact[{i}] 缺少 name")
            if not artifact.get("staging_path"):
                result.warnings.append(f"artifact[{i}] 缺少 staging_path")

    # ── 汇总 reason ──
    if not result.valid and not result.reason:
        result.reason = f"校验失败: {'; '.join(result.errors[:3])}"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TaskResultValidator 类（向后兼容旧接口）
# ═══════════════════════════════════════════════════════════════════════════════

class TaskResultValidator(BaseValidator):
    """TaskResult 校验器。

    向后兼容旧接口：内部委托给纯函数 validate()，返回旧 base.ValidationResult。

    用法:
        validator = TaskResultValidator(allowed_decisions=["approve", "revise", "reject"])
        result = validator.validate_file("path/to/task_result.json")
    """

    name = "task_result"

    def __init__(self, allowed_decisions: list[str] | None = None):
        self.allowed_decisions = allowed_decisions

    def validate(self, data: dict[str, Any]) -> BaseValidationResult:
        """校验 TaskResult 字典，返回旧 base.ValidationResult。

        向后兼容：内部委托给纯函数 validate()，再做字段映射。
        """
        # 推定为分支节点（有 allowed_decisions 即说明需要 decision）
        route_shape = RouteShape(
            has_on=bool(self.allowed_decisions),
            has_next=False,
            allowed_decisions=tuple(self.allowed_decisions or []),
        )
        new_vr = _validate_with_route_shape(data, route_shape)

        # 字段映射：ValidResult → base.ValidationResult
        return BaseValidationResult(
            passed=new_vr.valid,
            errors=new_vr.errors,
            warnings=new_vr.warnings,
        )

    def validate_file(self, path: str) -> BaseValidationResult:
        """从 JSON 文件加载并校验。"""
        if not os.path.exists(path):
            return BaseValidationResult(
                passed=False,
                errors=[f"TaskResult 文件不存在: {path}"],
            )

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return BaseValidationResult(
                passed=False,
                errors=[f"TaskResult JSON 解析失败: {e}"],
            )
        except IOError as e:
            return BaseValidationResult(
                passed=False,
                errors=[f"TaskResult 文件读取失败: {e}"],
            )

        return self.validate(data)


# ── 内部辅助 ──

def _validate_with_route_shape(
    data: dict[str, Any], route_shape: RouteShape
) -> ValidResult:
    """内部委托：复用纯函数但保持向后兼容类可访问。

    暴露为 module-level 函数以便 TaskResultValidator.validate() 调用，
    同时 Runner 可直接使用 validate() 顶层函数。
    """
    return validate(data, route_shape)
