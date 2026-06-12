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

    Task 只描述：执行什么、输入是什么、输出是什么、由哪个 agent 执行。
    禁止出现：transition、guard、retry、validator、provider、runtime。
    """

    name: str = ""
    instruction: str = ""
    agent: str = ""
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
            parts.append("**重要：你必须在最后一条消息的末尾输出一个 ```json 代码块，内容为 TaskResult JSON 对象。**\n")
            parts.append("TaskResult 的必需字段（其他字段见下面 schema）：\n")
            parts.append("- `schema_version`: 固定为 1\n")
            parts.append("- `task_id`: 当前 task 名称\n")
            parts.append("- `state`: 当前 state 名称\n")
            parts.append("- `status`: 执行状态（success/failed/blocked/timeout）\n")
            parts.append("- `decision`: 语义决策（见下方允许的决策列表）\n")
            parts.append("- `summary`: 人类可读的执行摘要\n")
            parts.append("- `artifacts`: 产出物列表（每项包含 name/staging_path/type），可以为空数组\n")
            parts.append("- `execution`: 执行元数据（started_at/finished_at/exit_code 等，引擎会覆盖）\n")
            parts.append("\n示例输出（你的最后一条消息应以此格式结尾）：\n")
            parts.append("```json\n")
            parts.append("{\n")
            parts.append('  "schema_version": 1,\n')
            parts.append(f'  "task_id": "{self.task.name if self.task else "task"}",\n')
            parts.append(f'  "state": "{self.state_name or "state"}",\n')
            # 示例 decision 使用 allowed_decisions 的第一个值，而非硬编码 "done"
            example_decision = "done"
            allowed = self.skill_policy.get("allowed_decisions", [])
            if allowed:
                example_decision = allowed[0]
            elif self.expected_task_result_schema:
                decision_prop = (
                    self.expected_task_result_schema.get("properties", {})
                    .get("decision", {})
                )
                if "enum" in decision_prop and decision_prop["enum"]:
                    example_decision = decision_prop["enum"][0]
            parts.append('  "status": "success",\n')
            parts.append(f'  "decision": "{example_decision}",\n')
            parts.append('  "summary": "任务完成的简要描述",\n')
            parts.append('  "artifacts": [],\n')
            parts.append('  "execution": {"started_at": "", "finished_at": "", "exit_code": 0}\n')
            parts.append("}\n")
            parts.append("```\n")
            parts.append("\n完整 schema 参考（所有字段的详细说明）：\n")
            parts.append("<details>\n")
            parts.append("<summary>点击展开 JSON Schema</summary>\n\n")
            parts.append("```json\n")
            parts.append(self._format_schema(self.expected_task_result_schema))
            parts.append("\n```\n")
            parts.append("</details>\n")

        # 6. Staging 路径
        if self.staging_paths:
            parts.append("## 输出路径\n")
            for name, path in self.staging_paths.items():
                parts.append(f"- {name}: {path}")
            parts.append("\n⚠️ 所有输出必须写入 staging 路径，禁止直接写 artifacts。\n")
            parts.append(
                "⚠️ **产物登记契约（务必遵守，否则任务会校验失败）**：\n"
                "1. 你在 TaskResult 的 `artifacts` 列表里声明的每一个产物，"
                "都必须先用 Write 工具把对应文件真实写入它的 `staging_path`。\n"
                "2. 先写文件，再登记——不要声明一个尚未落盘的产物。\n"
                "3. 没有实际产出文件的产物，就不要写进 `artifacts` 列表（留空数组即可）。\n"
                "4. 上面列出的输出路径是引擎期望的产物，请按需逐个写入并登记。\n"
                "5. `artifact_path` 必须是扁平路径 \"artifacts/<输出名>.md\"，"
                "不要包含子目录（如禁止 artifacts/plan/output.md），"
                "输出名取 `staging_path` 的文件名即可（如 plan_doc.md → \"artifacts/plan_doc.md\"）。\n"
            )

        # 7. 技能策略
        if self.skill_policy:
            allowed = self.skill_policy.get("allowed_decisions", [])
            if allowed:
                parts.append(
                    f"\n⚠️ **本任务的 `decision` 字段必须从以下值中选择一个**："
                    f"{', '.join(allowed)}。"
                    f"不要使用列表之外的值（例如不要用 done 代替 approve）。\n"
                )

        return "\n".join(parts)

    @staticmethod
    def _format_schema(schema: dict, indent: int = 0) -> str:
        """格式化 JSON Schema 为可读字符串。"""
        import json
        return json.dumps(schema, ensure_ascii=False, indent=2)
