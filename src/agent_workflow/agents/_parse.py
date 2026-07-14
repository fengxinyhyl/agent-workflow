"""Agent 输出解析共享模块。

claude_cli 与 codex_cli 的 TaskResult 文本解析逻辑同构，统一抽取至此，避免两份漂移。

解析策略（_parse_task_result_text）：
1. 整段文本是合法 JSON 且含 schema_version → 直接 from_dict
2. 文本含 ```json``` 代码块 → 逐块尝试解析
3. 代码块 JSON 损坏/截断 → 正则提取（_extract_task_result_fallback）
4. 全部失败 + allowed_decisions 非空 → 分级恢复（Level1 regex / Level2 synonym）
5. 全部失败 → 返回 None，由调用方决定最终兜底（invalid_output）
"""

from __future__ import annotations

import hashlib
import json
import re

from ..tasks.result import ExecutionMetadata, RecoveryInfo, TaskResult

# ── packet 格式常量（claude_cli 写入 / runner 读取，格式须同步） ──
PACKET_LAST_ASSISTANT_MARKER = "## 最后一条 assistant message"

# ── 分级恢复：引导词表（中英文，Level 1 + Level 2 共用） ──
_GUIDE_WORDS = [
    "决策", "决定", "最终决定", "结论", "裁决", "判定",
    "decision", "verdict", "conclusion", "final decision",
]

# ── Level 2 同义词表（受控白名单，短语 → decision） ──
_SYNONYM_TABLE: dict[str, str] = {
    "建议修改后重新提交": "revise",
    "打回修订": "revise",
    "需返工": "revise",
    "通过": "approve",
    "同意进入下一步": "approve",
    "no blocking": "approve",
    "拒绝": "reject",
    "不予接受": "reject",
    "驳回": "reject",
}


def _recover_decision_from_prose(
    text: str,
    allowed_decisions: list[str] | None,
    enable_synonym_recovery: bool = False,
) -> tuple[str, RecoveryInfo] | None:
    """从散文文本中分级恢复 decision。

    Level 1（regex，confidence=1.0）：引导词后约 40 字符窗口内，
    以完整 token（前后非字母）匹配 allowed_decisions，唯一命中 → 恢复。

    Level 2（synonym，confidence=0.95，默认关闭）：L1 未命中时，
    在同样窗口内匹配受控同义词表短语，映射目标须 ∈ allowed_decisions。

    返回 (decision, RecoveryInfo) 或 None（无法无歧义恢复）。
    """
    if not allowed_decisions or not text:
        return None

    allowed_set = {d.lower().strip() for d in allowed_decisions}

    def _compute_hash(t: str) -> str:
        return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]

    def _match_decisions_in_windows(
        source_text: str,
        candidates: dict[str, str],  # token → decision
        confidence: float,
        method: str,
    ) -> tuple[str, RecoveryInfo] | None:
        """在引导词窗口中匹配候选词，唯一命中 → 恢复。"""
        matched_decisions: set[str] = set()

        for gw in _GUIDE_WORDS:
            # 在 source_text 中找引导词位置（ASCII 引导词大小写不敏感，中文引导词精确匹配）
            pos = 0
            while True:
                if gw.isascii():
                    # 英文引导词：大小写不敏感定位
                    idx = source_text.lower().find(gw.lower(), pos)
                else:
                    idx = source_text.find(gw, pos)
                if idx == -1:
                    break
                # 窗口：引导词之后约 40 字符
                window_start = idx + len(gw)
                window = source_text[window_start:window_start + 40]

                for token, decision in candidates.items():
                    # 完整 token 匹配：前后非字母（或窗口边界）
                    # ASCII 字母构成的 token（含下划线/数字，如 no_op）走 word-boundary regex；
                    # 纯中文短语走直接子串匹配
                    if token.isascii() and any(c.isalpha() for c in token):
                        # 含 ASCII 字母的 token：用 regex word boundary 匹配
                        pattern = r'(?<![a-zA-Z])' + re.escape(token) + r'(?![a-zA-Z])'
                        if re.search(pattern, window, re.IGNORECASE):
                            matched_decisions.add(decision)
                    else:
                        # 中文短语：直接子串匹配
                        if token in window:
                            matched_decisions.add(decision)

                pos = idx + len(gw)

        if len(matched_decisions) == 1:
            decision = next(iter(matched_decisions))
            return (
                decision,
                RecoveryInfo(
                    method=method,
                    confidence=confidence,
                    recovered_fields=["decision"],
                    reason=f"JSON missing; {method} decision recovery from prose",
                    origin_text_hash=_compute_hash(source_text),
                ),
            )

        return None

    # ── Level 1：英文 decision 词精确匹配 ──
    # allowed_decisions 中的词作为 token 直接匹配
    l1_candidates = {d: d for d in allowed_set if d}
    result = _match_decisions_in_windows(text, l1_candidates, 1.0, "regex")
    if result is not None:
        return result

    # ── Level 2：同义词匹配（默认关闭） ──
    if enable_synonym_recovery:
        # 只保留映射目标在 allowed_set 中的条目
        l2_candidates = {
            phrase: decision
            for phrase, decision in _SYNONYM_TABLE.items()
            if decision in allowed_set
        }
        if l2_candidates:
            result = _match_decisions_in_windows(text, l2_candidates, 0.95, "synonym")
            if result is not None:
                return result

    return None


def _parse_task_result_text(
    text: str,
    allowed_decisions: list[str] | None = None,
    enable_synonym_recovery: bool = False,
) -> TaskResult | None:
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        # "result" 字段是嵌套 JSON/TaskResult 字符串，不是散文 → 不传 allowed_decisions
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

    # ── 分级恢复：所有结构化解析均失败，且节点有 allowed_decisions —→ 尝试从散文恢复 ──
    if allowed_decisions:
        recovery = _recover_decision_from_prose(
            text, allowed_decisions, enable_synonym_recovery=enable_synonym_recovery
        )
        if recovery is not None:
            decision, ri = recovery
            return TaskResult(
                schema_version=1,
                task_id="",
                state="",
                status="success",
                decision=decision,
                summary=f"parser 恢复: decision={decision} (method={ri.method}, confidence={ri.confidence})",
                execution=ExecutionMetadata(
                    started_at="",
                    finished_at="",
                    protocol_origin="parser",
                    recovery=ri,
                ),
            )

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
