"""RunContext — 所有执行步骤的稳定输入上下文。

RunContext 将散落在各处（runner、task、agent、validator、artifact resolver）
的运行时状态集中管理，避免参数传递地狱。

可序列化到 workflow_state.json，支持断点续跑。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any


def _now_iso() -> str:
    """返回带时区的 ISO 时间戳。"""
    tz = timezone(timedelta(hours=8))  # Asia/Shanghai
    return datetime.now(tz).isoformat()


@dataclass
class RunContext:
    """工作流运行上下文，所有执行步骤的稳定输入。

    字段说明：
      run_id: 本次运行唯一标识
      workflow_id: 工作流标识（如 software-dev）
      goal: 工作流目标描述
      project_root: 项目根目录绝对路径
      run_root: 本次运行产物目录（.agent-workflow/runs/<run_id>/）
      current_state: 当前状态名称
      current_task: 当前任务名称（可为 None）
      workflow_variables: 工作流级别变量
      artifacts: 已确认的产物流映射 {name: artifact_path}
      state_history: 状态访问历史列表
      task_results: 各 task 的 TaskResult 映射 {state_name: result_dict}
      attempts: 各 state 的尝试次数 {state_name: count}
      started_at: 运行开始时间
      updated_at: 最后更新时间
    """

    run_id: str = ""
    workflow_id: str = ""
    goal: str = ""
    project_root: str = ""
    run_root: str = ""
    current_state: str = ""
    current_task: str | None = None
    workflow_variables: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    state_history: list[str] = field(default_factory=list)
    task_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    started_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def touch(self):
        """更新 updated_at 时间戳。"""
        self.updated_at = _now_iso()

    def record_state_visit(self, state_name: str):
        """记录一次状态访问。"""
        self.state_history.append(state_name)
        self.attempts[state_name] = self.attempts.get(state_name, 0) + 1
        self.current_state = state_name
        self.touch()

    def get_attempt(self, state_name: str) -> int:
        """获取某状态的访问次数。"""
        return self.attempts.get(state_name, 0)

    def record_task_result(self, state_name: str, result: dict[str, Any]):
        """记录一个 task 的执行结果。"""
        self.task_results[state_name] = result
        self.touch()

    def promote_artifact(self, name: str, artifact_path: str):
        """记录正式产物流。"""
        self.artifacts[name] = artifact_path
        self.touch()

    def set_variable(self, key: str, value: Any):
        """设置工作流变量。"""
        self.workflow_variables[key] = value
        self.touch()

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取工作流变量。"""
        return self.workflow_variables.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return asdict(self)

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunContext":
        """从字典反序列化。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, json_str: str) -> "RunContext":
        """从 JSON 字符串反序列化。"""
        return cls.from_dict(json.loads(json_str))

    def save(self):
        """保存到 workflow_state.json。"""
        path = os.path.join(self.run_root, "workflow_state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, run_root: str) -> "RunContext":
        """从 workflow_state.json 加载。"""
        path = os.path.join(run_root, "workflow_state.json")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    @classmethod
    def create(
        cls,
        workflow_id: str,
        goal: str,
        project_root: str,
        run_id: str,
        run_root: str,
        **kwargs,
    ) -> "RunContext":
        """创建新的 RunContext 实例。"""
        return cls(
            run_id=run_id,
            workflow_id=workflow_id,
            goal=goal,
            project_root=os.path.abspath(project_root),
            run_root=os.path.abspath(run_root),
            **kwargs,
        )
