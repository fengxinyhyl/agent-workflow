"""Agent 输出解析共享模块。

claude_cli 与 codex_cli 的 TaskResult 文本解析逻辑同构，统一抽取至此，避免两份漂移。

解析策略（_parse_task_result_text）：
1. 整段文本是合法 JSON 且含 schema_version → 直接 from_dict
2. 文本含 ```json``` 代码块 → 逐块尝试解析
3. 代码块 JSON 损坏/截断 → 正则提取（_extract_task_result_fallback）
4. 全部失败 → 返回 None，由调用方决定最终兜底（invalid_output）
"""

from __future__ import annotations

import json
import re

from ..tasks.result import TaskResult


def _parse_task_result_text(text: str) -> TaskResult | None:
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        if "result" in data and isinstance(data["result"], str):
            nested = _parse_task_result_text(data["result"])
            if nested is not None:
                return nested
        if "schema_version" in data:
            return TaskResult.from_dict(data)

    marker = "```json"
    search_from = 0
    while marker in text[search_from:]:
        try:
            start = text.index(marker, search_from) + len(marker)
            end = text.index("```", start)
            json_text = text[start:end].strip()
            data = json.loads(json_text)
            if isinstance(data, dict) and data.get("schema_version", 0) >= 1:
                return TaskResult.from_dict(data)
            search_from = end + 3
        except json.JSONDecodeError:
            # JSON 块损坏/截断 → 正则兜底（须排在 ValueError 之前，
            # 否则因 JSONDecodeError 是 ValueError 子类会被先捕获而跳过兜底）。
            return _extract_task_result_fallback(text, start, end)
        except ValueError:
            # text.index 未找到闭合 ``` 等 → 终止扫描
            break

    return None


def _extract_task_result_fallback(
    text: str, json_start: int, json_end: int
) -> TaskResult | None:
    """从截断/损坏的 JSON 块中用正则提取 decision/status/summary。

    当模型在 ```json``` 块中使用 [...] 等占位符截断长数组时，
    json.loads 会失败。此函数回退到逐字段正则提取。

    decision 默认 None、status 默认 invalid_output——即使提取失败，
    也产出 Runtime 内部瞬时态而非伪造成功。返回条件只统计正则**实际命中**
    的字段（避免 status 的非空默认值让函数永远返回非 None，吞掉最终 fallback）。
    """
    json_text = text[json_start:json_end].strip()

    def _extract_str(key: str) -> str | None:
        m = re.search(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', json_text)
        if m:
            return m.group(1)
        return None

    decision = _extract_str("decision")
    status = _extract_str("status")
    summary = _extract_str("summary")
    task_id = _extract_str("task_id")
    state = _extract_str("state")

    # 仅当正则真正命中任一可辨识字段时才返回；否则交还给最终 fallback。
    if any(v is not None for v in (decision, status, summary, task_id)):
        return TaskResult(
            schema_version=1,
            task_id=task_id or "",
            state=state or "",
            status=status or "invalid_output",
            decision=decision,
            summary=summary or "",
        )

    return None
