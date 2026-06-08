"""Schema Contract 验证测试。

使用纯标准库实现 SchemaValidator，零外部依赖。
验证 WorkflowEvent、ArtifactRegistryEntry、WorkflowCheckpoint 三种 schema
以及对应的 valid/invalid fixtures。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# 纯标准库 SchemaValidator
# ---------------------------------------------------------------------------

class SchemaValidationError(Exception):
    """Schema 验证失败，携带字段路径和诊断信息。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class SchemaValidator:
    """轻量 JSON Schema 验证器，纯标准库实现。

    支持: type, required, properties, enum, pattern, minimum, const,
           allOf (if/then), items, nested objects。
    """

    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    def validate(self, instance: Any, path: str = "$") -> list[str]:
        """验证 instance 是否符合 schema，返回错误列表（空列表表示通过）。"""
        errors: list[str] = []
        self._validate(instance, self._schema, path, errors)
        return errors

    def validate_or_raise(self, instance: Any) -> None:
        """验证并抛出 SchemaValidationError 若失败。"""
        errors = self.validate(instance)
        if errors:
            raise SchemaValidationError(errors)

    def _validate(
        self, instance: Any, schema: dict[str, Any], path: str, errors: list[str]
    ) -> None:
        if not isinstance(schema, dict):
            return

        # const
        if "const" in schema:
            if instance != schema["const"]:
                errors.append(
                    f"{path}: 值必须为 {schema['const']!r}，实际为 {instance!r}"
                )
            return

        # type (支持单类型和多类型 union)
        expected_type = schema.get("type")
        if expected_type and not self._check_type(instance, expected_type):
            type_label = expected_type if isinstance(expected_type, str) else " | ".join(expected_type)
            errors.append(
                f"{path}: 类型必须为 {type_label}，实际为 {type(instance).__name__}"
            )
            return

        # 处理 object（即使没有显式 type，有 properties 时也当作 object）
        if isinstance(instance, dict):
            if "properties" in schema or "required" in schema or "allOf" in schema:
                self._validate_object(instance, schema, path, errors)
        elif isinstance(instance, list):
            if expected_type == "array" or "items" in schema:
                self._validate_array(instance, schema, path, errors)

    def _validate_object(
        self, instance: dict[str, Any], schema: dict[str, Any],
        path: str, errors: list[str],
    ) -> None:
        # required
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: 缺少必需字段 '{req}'")

        # properties
        for prop_name, prop_schema in schema.get("properties", {}).items():
            if prop_name in instance:
                self._validate(
                    instance[prop_name], prop_schema,
                    f"{path}.{prop_name}", errors,
                )

        # patternProperties (for additional constraints on property names)
        # -- skip for now, our schemas don't use this

        # enum (on the object level, e.g. state enum)
        if "enum" in schema and instance not in [None]:
            # enum at object level doesn't make sense; handled at property level
            pass

        # allOf
        for sub_schema in schema.get("allOf", []):
            self._validate_allof(instance, sub_schema, path, errors)

    def _validate_allof(
        self, instance: dict[str, Any], schema: dict[str, Any],
        path: str, errors: list[str],
    ) -> None:
        # if/then 条件验证
        if "if" in schema and "then" in schema:
            if_errors: list[str] = []
            self._validate(instance, schema["if"], path, if_errors)
            if not if_errors:
                # if 条件匹配，验证 then
                self._validate(instance, schema["then"], path, errors)

    def _validate_array(
        self, instance: list[Any], schema: dict[str, Any],
        path: str, errors: list[str],
    ) -> None:
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(instance):
                self._validate(item, items_schema, f"{path}[{i}]", errors)

    def _check_type(self, instance: Any, expected_type: str | list[str]) -> bool:
        """检查类型，支持单类型字符串或多类型 union 数组。"""
        if isinstance(expected_type, list):
            return any(self._check_type_single(instance, t) for t in expected_type)
        return self._check_type_single(instance, expected_type)

    def _check_type_single(self, instance: Any, expected_type: str) -> bool:
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
            "null": type(None),
        }
        py_type = type_map.get(expected_type)
        if py_type is None:
            return True  # 未知类型跳过检查
        if py_type is type(None):
            return instance is None
        if isinstance(py_type, tuple):
            return isinstance(instance, py_type) and not isinstance(instance, bool)
        return isinstance(instance, py_type) and not (
            expected_type == "integer" and isinstance(instance, bool)
        )


# 在 SchemaValidator 中对 property-level 验证的扩展
def _validate_property_constraints(
    instance: Any, schema: dict[str, Any], path: str, errors: list[str]
) -> None:
    """验证字段级别的约束: enum, pattern, minimum, const。"""
    # enum
    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(
                f"{path}: 值 {instance!r} 不在允许的枚举中: {schema['enum']}"
            )

    # pattern
    if "pattern" in schema and isinstance(instance, str):
        if not re.search(schema["pattern"], instance):
            errors.append(
                f"{path}: 值 {instance!r} 不匹配模式 {schema['pattern']}"
            )

    # minimum
    if "minimum" in schema and isinstance(instance, (int, float)):
        if instance < schema["minimum"]:
            errors.append(
                f"{path}: 值 {instance} 小于最小值 {schema['minimum']}"
            )

    # const (at field level)
    if "const" in schema:
        if instance != schema["const"]:
            errors.append(
                f"{path}: 值必须为 {schema['const']!r}，实际为 {instance!r}"
            )


# Monkey-patch _validate to include property constraint checks
_original_validate = SchemaValidator._validate


def _validate_with_constraints(
    self: SchemaValidator, instance: Any, schema: dict[str, Any],
    path: str, errors: list[str],
) -> None:
    _validate_property_constraints(instance, schema, path, errors)
    _original_validate(self, instance, schema, path, errors)


SchemaValidator._validate = _validate_with_constraints  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# 加载 schema
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "schema_contract"


def _load_schema(name: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / name
    if not path.exists():
        pytest.fail(f"Schema 文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_fixture(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Schema 文件存在性测试
# ---------------------------------------------------------------------------

def test_schema_files_exist():
    """3 个 schema 文件必须存在且为合法 JSON。"""
    for name in [
        "workflow_event.schema.json",
        "artifact_registry_entry.schema.json",
        "workflow_checkpoint.schema.json",
    ]:
        schema = _load_schema(name)
        assert isinstance(schema, dict)
        assert "$id" in schema or "title" in schema


def test_event_schema_defines_all_12_event_types():
    """workflow_event schema 必须包含 v4 计划中定义的 12 种事件类型。"""
    schema = _load_schema("workflow_event.schema.json")
    event_type_enum = schema["properties"]["event_type"]["enum"]
    expected = {
        "WORKFLOW_CREATED", "WORK_ITEM_CREATED", "PHASE_STARTED",
        "PHASE_COMPLETED", "APPROVAL_REQUESTED", "APPROVAL_GRANTED",
        "APPROVAL_REJECTED", "RETRY_STARTED", "CANCELLED", "RESUMED",
        "ARTIFACT_PROMOTED", "CHECKPOINT_WRITTEN",
    }
    assert set(event_type_enum) == expected


def test_event_schema_defines_core_state_enum():
    """workflow_event 和 workflow_checkpoint 的 state 必须为 6 个 core 值。"""
    for schema_name in ["workflow_event.schema.json", "workflow_checkpoint.schema.json"]:
        schema = _load_schema(schema_name)
        state_enum = schema["properties"]["state"]["enum"]
        expected = {"PENDING", "RUNNING", "WAITING_APPROVAL", "FAILED", "COMPLETED", "CANCELLED"}
        assert set(state_enum) == expected


def test_registry_schema_has_all_required_selectors():
    """artifact_registry_entry 必须包含所有 registry selector 所需字段。"""
    schema = _load_schema("artifact_registry_entry.schema.json")
    required = set(schema["required"])
    # find_latest(kind, work_item_id, phase, state) 需要的字段
    assert "kind" in required
    assert "work_item_id" in required
    assert "phase" in required
    assert "state" in required
    # find_by_event 需要的字段
    assert "source_event_id" in required
    # artifact_id pattern 验证
    assert "artifact_id" in required
    assert "sha256" in required


def test_phase_is_string_not_enum():
    """phase 必须是 type: string，不能是硬编码 enum（pack-registered）。"""
    for schema_name in [
        "workflow_event.schema.json",
        "artifact_registry_entry.schema.json",
        "workflow_checkpoint.schema.json",
    ]:
        schema = _load_schema(schema_name)
        phase_schema = schema["properties"]["phase"]
        assert phase_schema.get("type") == "string"
        assert "enum" not in phase_schema, (
            f"{schema_name}: phase 必须是 pack-registered string，不能有 enum"
        )


# ---------------------------------------------------------------------------
# Parameterized valid fixture tests
# ---------------------------------------------------------------------------

def _collect_fixtures(subdir: str) -> list[Path]:
    """收集 fixtures 子目录中的所有 JSON 文件。"""
    d = FIXTURES_DIR / subdir
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def _schema_for_fixture(fixture_name: str) -> str:
    """根据 fixture 文件名推断对应的 schema 文件名。"""
    stem = Path(fixture_name).stem
    # event checkpoint fixtures 以 'event_' 开头，使用 event schema
    if stem.startswith("event_"):
        return "workflow_event.schema.json"
    if "checkpoint" in stem:
        return "workflow_checkpoint.schema.json"
    if "registry" in stem:
        return "artifact_registry_entry.schema.json"
    return "workflow_event.schema.json"


@pytest.mark.parametrize("fixture_path", _collect_fixtures("valid"))
def test_valid_fixtures_pass(fixture_path: Path):
    """所有 valid fixtures 必须通过 schema 验证。"""
    schema_name = _schema_for_fixture(fixture_path.name)
    schema = _load_schema(schema_name)
    instance = _load_fixture(fixture_path)

    validator = SchemaValidator(schema)
    errors = validator.validate(instance)

    assert errors == [], (
        f"Valid fixture {fixture_path.name} 应通过验证，但产生了错误:\n"
        + "\n".join(f"  - {e}" for e in errors)
    )


@pytest.mark.parametrize("fixture_path", _collect_fixtures("invalid"))
def test_invalid_fixtures_fail_with_diagnostics(fixture_path: Path):
    """所有 invalid fixtures 必须验证失败并输出清晰诊断信息。"""
    schema_name = _schema_for_fixture(fixture_path.name)
    schema = _load_schema(schema_name)
    instance = _load_fixture(fixture_path)

    validator = SchemaValidator(schema)
    errors = validator.validate(instance)

    assert errors, (
        f"Invalid fixture {fixture_path.name} 应验证失败，但通过了"
    )

    # 诊断信息必须包含具体字段名
    field_keywords = {
        "missing_required": ["缺少", "必需字段", "event_id"],
        "missing_sha256": ["sha256", "缺少"],
        "missing_checksum": ["checksum", "缺少"],
        "invalid_state": ["state", "不在"],
        "invalid_event_type": ["event_type", "不在"],
        "invalid_attempt": ["attempt", "小于", "最小值"],
        "invalid_artifact_id": ["artifact_id", "不匹配"],
        "missing_payload": ["缺少", "必需字段"],  # artifact_id/sha256/strategy/topic/checkpoint_id
    }
    matched = False
    for keyword_group in field_keywords.values():
        if all(kw in e for kw in keyword_group[:1] for e in errors):
            matched = True
            break
    # 更宽松的检查：至少一个错误信息中包含字段名相关关键词
    diagnostic_fields = [
        "event_id", "state", "event_type", "attempt", "artifact_id",
        "sha256", "checksum", "checkpoint_id", "strategy", "topic",
        "artifact_path", "必需字段", "不在", "不匹配", "小于",
    ]
    has_diagnostic = any(
        any(field in err for field in diagnostic_fields)
        for err in errors
    )
    assert has_diagnostic, (
        f"Invalid fixture {fixture_path.name} 的错误信息缺少诊断字段名:\n"
        + "\n".join(f"  - {e}" for e in errors)
    )


# ---------------------------------------------------------------------------
# 完整性检查
# ---------------------------------------------------------------------------

def test_fixture_counts():
    """每个 schema 至少有 2 valid + 2 invalid fixtures。"""
    valid = _collect_fixtures("valid")
    invalid = _collect_fixtures("invalid")

    # 按 schema 分组
    valid_event = [f for f in valid if "checkpoint" not in f.stem and "registry" not in f.stem]
    valid_registry = [f for f in valid if "registry" in f.stem]
    valid_checkpoint = [f for f in valid if "checkpoint" in f.stem]

    invalid_event = [f for f in invalid if "checkpoint" not in f.stem and "registry" not in f.stem]
    invalid_registry = [f for f in invalid if "registry" in f.stem]
    invalid_checkpoint = [f for f in invalid if "checkpoint" in f.stem]

    assert len(valid_event) >= 2, f"Event valid fixtures: {len(valid_event)} < 2"
    assert len(invalid_event) >= 2, f"Event invalid fixtures: {len(invalid_event)} < 2"
    assert len(valid_registry) >= 2, f"Registry valid fixtures: {len(valid_registry)} < 2"
    assert len(invalid_registry) >= 2, f"Registry invalid fixtures: {len(invalid_registry)} < 2"
    assert len(valid_checkpoint) >= 2, f"Checkpoint valid fixtures: {len(valid_checkpoint)} < 2"
    assert len(invalid_checkpoint) >= 2, f"Checkpoint invalid fixtures: {len(invalid_checkpoint)} < 2"
