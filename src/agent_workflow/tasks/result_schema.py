"""TaskResult JSON Schema 定义。

Agent 需要根据此 schema 输出结构化的 TaskResult JSON。
"""

from __future__ import annotations

from typing import Any


# 标准 TaskResult JSON Schema
TASK_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TaskResult",
    "description": "Agent 任务执行结果的标准格式。所有 Agent 必须按此格式输出。",
    "type": "object",
    "required": [
        "schema_version",
        "task_id",
        "state",
        "status",
        "decision",
        "summary",
        "execution",
    ],
    "properties": {
        "schema_version": {
            "type": "integer",
            "description": "TaskResult schema 版本号，当前为 1",
            "const": 1,
        },
        "task_id": {
            "type": "string",
            "description": "任务标识，与 workflow 中 task name 一致",
            "examples": ["review_plan", "execute", "audit"],
        },
        "state": {
            "type": "string",
            "description": "执行此 task 时的 state 名称",
        },
        "agent": {
            "type": "string",
            "description": "执行 Agent 名称",
        },
        "status": {
            "type": "string",
            "enum": ["success", "failed", "blocked", "cancelled", "timeout", "invalid_output"],
            "description": "执行状态",
        },
        "decision": {
            "type": "string",
            "description": "语义决策，Runner 据此选择 state transition",
        },
        "summary": {
            "type": "string",
            "description": "人类可读的执行摘要",
        },
        "artifacts": {
            "type": "array",
            "description": "产出物列表",
            "items": {
                "type": "object",
                "required": ["name", "staging_path", "type"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "产物名称（与 workflow outputs 对应）",
                    },
                    "staging_path": {
                        "type": "string",
                        "description": "staging 区路径（Agent 只能写此路径）",
                    },
                    "artifact_path": {
                        "type": "string",
                        "description": "预期的正式 artifact 路径（扁平结构，如 artifacts/plan_doc.md，禁止包含子目录）",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["markdown", "json", "yaml", "code", "other"],
                        "description": "产物类型",
                    },
                },
            },
        },
        "execution": {
            "type": "object",
            "description": "执行元数据（必填）",
            "required": ["started_at", "finished_at", "exit_code"],
            "properties": {
                "started_at": {
                    "type": "string",
                    "description": "任务开始时间（ISO 8601）",
                },
                "finished_at": {
                    "type": "string",
                    "description": "任务完成时间（ISO 8601）",
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "执行耗时（秒）",
                },
                "attempt": {
                    "type": "integer",
                    "description": "当前尝试次数",
                    "default": 1,
                },
                "exit_code": {
                    "type": "integer",
                    "description": "进程退出码",
                },
                "pid": {
                    "type": "integer",
                    "description": "子进程 PID",
                },
            },
        },
        "issues": {
            "type": "array",
            "description": "发现的问题列表",
            "items": {
                "type": "object",
                "required": ["severity", "title"],
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["blocking", "warning", "info"],
                    },
                    "title": {
                        "type": "string",
                        "description": "问题简述",
                    },
                    "detail": {
                        "type": "string",
                        "description": "问题详情",
                    },
                },
            },
        },
        "next_inputs": {
            "type": "object",
            "description": "传递给下一状态的输入数据（可选）",
        },
        "session_id": {
            "type": "string",
            "description": "CLI session/thread ID（对齐 legacy WorkerResult）",
        },
        "token_usage": {
            "type": "object",
            "description": "token 使用统计（Claude: cache_read_input_tokens; Codex: cached_input_tokens, reasoning_output_tokens）",
            "additionalProperties": True,
        },
        "log_path": {
            "type": "string",
            "description": "stream 日志落盘绝对路径",
        },
        "packet_path": {
            "type": "string",
            "description": "debug packet 绝对路径",
        },
    },
}


def build_task_result_schema(allowed_decisions: list[str] | None = None) -> dict[str, Any]:
    """构建针对特定 task 的 TaskResult schema。

    如果指定 allowed_decisions，会限制 decision 字段的 enum。
    """
    schema = dict(TASK_RESULT_SCHEMA)  # shallow copy
    props = dict(schema["properties"])

    if allowed_decisions:
        decision_prop = dict(props["decision"])
        decision_prop["enum"] = allowed_decisions
        decision_prop["description"] = (
            f"语义决策（允许值: {', '.join(allowed_decisions)}）"
        )
        props["decision"] = decision_prop

    schema["properties"] = props
    return schema
