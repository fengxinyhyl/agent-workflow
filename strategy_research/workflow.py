"""StrategyResearchWorkflow — 策略研究实验生命周期适配器。

将策略研究实验的标准 5 步骤映射为一组 WorkItem。
每个步骤完成后，artifact_path 指向对应的 markdown sidecar。

暂不抽象 WorkflowPack。抽象触发条件：
- 出现第二个真实 workflow → 评估公共接口
- 出现第三个真实 workflow → 正式提取 pack/framework
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_workflow.long_task.work_item import WorkItem, ItemStatus
from agent_workflow.long_task.workflow_run import WorkflowRun


@dataclass
class StrategyResearchStep:
    """策略研究 workflow 的一个步骤定义。

    Attributes:
        id: 步骤标识，如 "plan"
        title: 人类可读描述，如 "编写实现计划"
        depends_on: 依赖的步骤 id 列表
        handler_module: 处理模块名，如 "plan", "review", "execute", "audit"
    """

    id: str
    title: str
    depends_on: list[str] = field(default_factory=list)
    handler_module: str = ""


class StrategyResearchWorkflow:
    """策略研究实验生命周期适配器。

    默认 5 步骤（链式依赖）：
    plan → review_plan → code_execute → final_audit → summary
    """

    # 默认 5 步骤定义
    DEFAULT_STEPS: list[StrategyResearchStep] = [
        StrategyResearchStep(
            id="plan",
            title="编写实现计划",
            depends_on=[],
            handler_module="plan",
        ),
        StrategyResearchStep(
            id="review_plan",
            title="审查实现计划",
            depends_on=["plan"],
            handler_module="review",
        ),
        StrategyResearchStep(
            id="code_execute",
            title="执行代码变更",
            depends_on=["review_plan"],
            handler_module="execute",
        ),
        StrategyResearchStep(
            id="final_audit",
            title="最终审计",
            depends_on=["code_execute"],
            handler_module="audit",
        ),
        StrategyResearchStep(
            id="summary",
            title="生成总结报告",
            depends_on=["final_audit"],
            handler_module="summary",
        ),
    ]

    def __init__(
        self,
        experiment_id: str,
        topic: str,
        steps: list[StrategyResearchStep] | None = None,
    ):
        """初始化策略研究工作流。

        Args:
            experiment_id: 实验标识，如 "small_cap_limit_up_20260608"
            topic: 实验主题，如 "limit_up_止损参数优化"
            steps: 自定义步骤列表，None 则使用默认 5 步
        """
        self.experiment_id = experiment_id
        self.topic = topic
        self.steps = steps or self.default_steps()

    def build_workflow_run(self) -> WorkflowRun:
        """构建 WorkflowRun 实例。

        Returns:
            配置好的 WorkflowRun
        """
        return WorkflowRun(
            id=self.experiment_id,
            name=self.topic,
        )

    def build_items(self) -> list[WorkItem]:
        """根据 steps 生成 WorkItem 列表。

        Returns:
            WorkItem 列表，顺序与 steps 定义一致
        """
        return [
            WorkItem(
                id=step.id,
                title=step.title,
                depends_on=list(step.depends_on),
            )
            for step in self.steps
        ]

    @staticmethod
    def default_steps() -> list[StrategyResearchStep]:
        """返回标准的 5 步策略研究实验流程。

        plan (depends_on: [])
          → review_plan (depends_on: [plan])
            → code_execute (depends_on: [review_plan])
              → final_audit (depends_on: [code_execute])
                → summary (depends_on: [final_audit])

        Returns:
            默认步骤列表
        """
        return list(StrategyResearchWorkflow.DEFAULT_STEPS)
