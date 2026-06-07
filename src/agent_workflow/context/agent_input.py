"""AgentInput — 所有 Agent 的统一输入结构。

AgentInput = Task + RunContext + Skill

Agent adapter 不再从散落参数拼 prompt，只接收 AgentInput。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .run_context import RunContext


@dataclass
class TaskConfig:
    """Task 配置（瘦模型）。

    Task 只描述：执行什么、输入是什么、输出是什么、由哪个 role 执行。
    禁止出现：transition、guard、retry、validator、provider、runtime。
    """

    name: str = ""
    instruction: str = ""
    role: str = ""
    inputs: list[str] = field(default_factory=list)
    output: str = ""


@dataclass
class SkillContext:
    """Skill adoption 的上下文摘要。

    包含已加载的 skill 内容、adoption 结果和注入到 Agent prompt 的策略。
    """

    raw_skills: dict[str, str] = field(default_factory=dict)
    adoption_summary: str = ""
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentInput:
    """所有 Agent 的统一输入结构。

    字段说明：
      task: Task 配置（执行什么）
      context: RunContext 快照（当前运行时状态）
      skill_context: Skill 上下文（已加载的 skill 内容和策略）
      skill_policy: Skill 策略（rules、allowed_decisions 等）
      expected_task_result_schema: TaskResult 的 JSON Schema（Agent 必须按此输出）
      staging_paths: 各产物的 staging 路径映射
    """

    task: TaskConfig = field(default_factory=TaskConfig)
    context: RunContext = field(default_factory=RunContext)
    state_name: str = ""  # 显式 state 名（由 Runner 设置，用于 adapter 定位 staging/artifact 路径）
    skill_context: str = ""
    skill_policy: dict[str, Any] = field(default_factory=dict)
    expected_task_result_schema: dict[str, Any] = field(default_factory=dict)
    staging_paths: dict[str, str] = field(default_factory=dict)

    def build_prompt(self) -> str:
        """构建发给 Agent 的完整 prompt。"""
        parts = []

        # 1. 全局目标
        if self.context.goal:
            parts.append(f"## 目标\n\n{self.context.goal}\n")

        # 2. 当前任务
        parts.append(f"## 当前任务\n\n{self.task.instruction}\n")
        if self.task.inputs:
            parts.append(f"输入: {', '.join(self.task.inputs)}\n")
        if self.task.output:
            parts.append(f"期望输出: {self.task.output}\n")

        # 3. 上下文
        if self.context.artifacts:
            parts.append("## 已有产物流\n")
            for name, path in self.context.artifacts.items():
                parts.append(f"- {name}: {path}")
            parts.append("")

        if self.context.state_history:
            parts.append(f"状态历史: {' → '.join(self.context.state_history)}\n")

        # 4. Skill 上下文
        if self.skill_context:
            parts.append(f"## 技能指引\n\n{self.skill_context}\n")

        # 5. TaskResult Schema
        if self.expected_task_result_schema:
            parts.append("## 输出格式要求\n")
            parts.append("你必须输出一个 **TaskResult** JSON 对象，格式如下：\n")
            parts.append("```json")
            parts.append(self._format_schema(self.expected_task_result_schema))
            parts.append("```\n")

        # 6. Staging 路径
        if self.staging_paths:
            parts.append("## 输出路径\n")
            for name, path in self.staging_paths.items():
                parts.append(f"- {name}: {path}")
            parts.append("\n⚠️ 所有输出必须写入 staging 路径，禁止直接写 artifacts。\n")

        # 7. 技能策略
        if self.skill_policy:
            allowed = self.skill_policy.get("allowed_decisions", [])
            if allowed:
                parts.append(f"允许的决策: {', '.join(allowed)}\n")

        return "\n".join(parts)

    @staticmethod
    def _format_schema(schema: dict, indent: int = 0) -> str:
        """格式化 JSON Schema 为可读字符串。"""
        import json
        return json.dumps(schema, ensure_ascii=False, indent=2)
