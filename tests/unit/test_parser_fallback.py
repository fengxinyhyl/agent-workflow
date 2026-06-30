"""Parser invalid_output 兜底测试（Runtime v2 第 1 步）。

覆盖：
- 无结构化输出时 claude/codex 的 _parse_stream_output 返回 invalid_output/decision=None（不再伪造 success/done）
- _extract_task_result_fallback 在截断 JSON 缺字段时返回 decision=None/invalid_output
- _parse_task_result_text 命中合法 TaskResult 时 decision 原样保留
- 显式分支（CLI not found / 安全拦截等）decision=None
"""

import json

from agent_workflow.agents.claude_cli import ClaudeCLI
from agent_workflow.agents.codex_cli import CodexCLI
from agent_workflow.agents._parse import (
    _parse_task_result_text,
    _extract_task_result_fallback,
)


class TestParseStreamOutputFallback:
    """无结构化输出时的最终 fallback：invalid_output / decision=None。"""

    def test_claude_no_structured_output(self):
        agent = ClaudeCLI()
        # 纯文本，无 type=result 事件、无合法 ```json``` 块
        stdout = "这是一段没有结构化 TaskResult 的自由文本输出。\n第二行。"
        result = agent._parse_stream_output("plan", stdout, "", None)
        assert result.status == "invalid_output"
        assert result.decision is None
        assert "无法解析" in result.summary

    def test_codex_no_structured_output(self):
        agent = CodexCLI()
        stdout = "codex 自由文本，没有 agent_message 也没有合法 JSON。"
        result = agent._parse_stream_output("plan", stdout, "", None)
        assert result.status == "invalid_output"
        assert result.decision is None
        assert "无法解析" in result.summary

    def test_claude_empty_output(self):
        agent = ClaudeCLI()
        result = agent._parse_stream_output("plan", "", "", None)
        assert result.status == "invalid_output"
        assert result.decision is None


class TestExtractFallback:
    """_extract_task_result_fallback：截断/损坏 JSON 的正则兜底。"""

    def test_truncated_json_missing_decision(self):
        # ```json``` 块中数组被 [...] 截断导致 json.loads 失败，且无 decision 字段
        text = (
            "前言\n```json\n{\n"
            '  "schema_version": 1,\n'
            '  "status": "success",\n'
            '  "summary": "做了一些事",\n'
            '  "artifacts": [...]\n'  # 占位符截断，json 非法
            "}\n```\n"
        )
        result = _parse_task_result_text(text)
        assert result is not None
        # 命中了 status/summary 但无 decision → decision 应为 None
        assert result.decision is None
        assert result.summary == "做了一些事"
        assert result.status == "success"  # status 真实命中则保留

    def test_truncated_json_no_recognizable_field(self):
        # 损坏 JSON 但连可辨识字段都提取不到 → 返回 None（交还最终 fallback）
        text = "```json\n{ \"foo\": [...] }\n```"
        result = _parse_task_result_text(text)
        assert result is None

    def test_extract_fallback_status_default_invalid_output(self):
        # 直接调用 fallback：缺 status 时默认 invalid_output、缺 decision 时为 None
        json_text = '{ "summary": "断了", "artifacts": [...] }'
        text = "```json\n" + json_text + "\n```"
        start = text.index("```json") + len("```json")
        end = text.index("```", start)
        result = _extract_task_result_fallback(text, start, end)
        assert result is not None
        assert result.status == "invalid_output"
        assert result.decision is None
        assert result.summary == "断了"


class TestParseHappyPath:
    """命中合法 TaskResult 时 decision 原样保留。"""

    def test_parse_valid_taskresult_preserves_decision(self):
        payload = {
            "schema_version": 1,
            "task_id": "review",
            "state": "review",
            "status": "success",
            "decision": "approve",
            "summary": "评审通过",
        }
        text = "一些前言\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n结尾"
        result = _parse_task_result_text(text)
        assert result is not None
        assert result.decision == "approve"
        assert result.status == "success"

    def test_parse_valid_taskresult_without_decision(self):
        # 合法 TaskResult 但省略 decision → 解析后 decision 为 None
        payload = {
            "schema_version": 1,
            "task_id": "plan",
            "state": "plan",
            "status": "success",
            "summary": "完成",
        }
        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        result = _parse_task_result_text(text)
        assert result is not None
        assert result.decision is None


class TestExplicitBranchDecisionNone:
    """显式构造分支：decision 置 None（靠 status 路由）。"""

    def test_claude_cli_not_found_decision_none(self):
        # 配置一个不存在的命令，触发 CLI not found 分支
        agent = ClaudeCLI({"command": "claude-nonexistent-xyz-cmd"})
        from types import SimpleNamespace
        ctx = SimpleNamespace(
            current_state="plan",
            staging_root=".pytest_tmp",
            project_root=".",
            run_root=".pytest_tmp/run",
        )
        task = SimpleNamespace(name="plan")
        agent_input = SimpleNamespace(
            state_name="plan",
            context=ctx,
            task=task,
            build_prompt=lambda: "prompt",
        )
        result = agent.execute(agent_input)
        assert result.status == "blocked"
        assert result.decision is None
