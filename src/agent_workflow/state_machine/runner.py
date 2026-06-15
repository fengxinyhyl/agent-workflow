"""Runner — 工作流主循环。

Runner 负责:
- 创建工作流运行上下文
- 按状态机循环执行
- Guard 检查
- Agent 调度
- TaskResult 校验
- Artifact 管理
- Transition 选择
- 状态持久化

核心原则（v4）:
- 所有语义决策来自 TaskResult
- 所有状态迁移由 Runner 决定
- Agent 只写 staging
- 未知 decision 走 default
"""

from __future__ import annotations

import os
import re
import time
import threading
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from ..config.models import WorkflowConfig, TaskModel, AgentModel
from ..context.run_context import RunContext
from ..context.agent_input import AgentInput, TaskConfig as AgentTaskConfig
from .machine import StateMachine, TransitionResult
from .guard import GuardChecker, GuardResult


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


def _generate_run_id(topic: str = "", workflow_name: str = "", goal: str = "") -> str:
    """生成 run_id：{YYMMDD}_{name}，name 按 topic > workflow_name > goal 优先级。"""
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%y%m%d")

    def _slug(s: str, max_len: int = 40) -> str:
        s = s.strip().replace(" ", "_").replace("\\", "_").replace("/", "_")
        s = re.sub(r'[^a-zA-Z0-9_一-鿿-]', '', s)
        return s[:max_len].strip("_-") or ""

    name = _slug(topic) or _slug(workflow_name) or _slug(goal)
    if not name:
        short = uuid.uuid4().hex[:6]
        return f"{ts}_{short}"

    return f"{ts}_{name}"


class Runner:
    """工作流运行器。

    用法:
        wf = load_workflow("workflow.yaml")
        runner = Runner(wf, goal="实现登录功能", project_root=".")
        run_id = runner.start()
        runner.run()
    """

    def __init__(
        self,
        workflow: WorkflowConfig,
        goal: str = "",
        topic: str = "",
        project_root: str = ".",
        run_root: str | None = None,
        agents: dict[str, AgentModel] | None = None,
        event_bus: Any = None,
        skills_dir: str | None = None,
        mock_script: dict | None = None,
    ):
        self.workflow = workflow
        self.goal = goal
        self.topic = topic
        self.project_root = os.path.abspath(project_root)

        # 状态机
        self.sm = StateMachine(workflow)

        # Guard
        self.guard_checker = GuardChecker(workflow.guards)

        # RunContext（在 start() 中创建）
        self.context: RunContext | None = None

        # 运行根目录
        if run_root is None:
            run_root = os.path.join(
                self.project_root, "doc", "runs"
            )
        elif not os.path.isabs(run_root):
            # 相对路径基于 project_root 解析
            run_root = os.path.join(self.project_root, run_root)
        self.base_run_root = os.path.abspath(run_root)

        # Agent registry
        self._agent_registry = agents or {}

        # EventBus
        self._event_bus = event_bus
        self._event_bus_external = event_bus is not None

        # P0a: JSONLSink 引用（用于 start/run 生命周期管理）
        self._jsonl_sink = None

        # P0d: Skill adoption
        self.skills_dir = skills_dir
        self._adoption = None

        # mock 模式的 decision 脚本（按 state 名 → decision 列表），
        # 仅在 mock fallback 时生效，用于演示状态机回流分支。
        self._mock_script = mock_script or {}

        # 心跳控制
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._running = False
        self._cancelled = False
        self._run_id: str = ""

    @classmethod
    def attach_existing(
        cls,
        run_root: str,
        workflow: WorkflowConfig,
        goal: str = "",
        project_root: str = ".",
        agents: dict[str, AgentModel] | None = None,
        event_bus: Any = None,
        skills_dir: str | None = None,
    ) -> "Runner":
        """从既有 run_root 加载 RunContext 并恢复 Runner。

        用于跨进程 continue 场景：新进程中从 workflow_state.json 恢复 Runner，
        然后调用 continue_from_gate() 继续执行。

        Args:
            run_root: 既有 run 的目录路径（如 doc/runs/run_xxx/）
            workflow: WorkflowConfig 实例
            goal: 工作流目标（空则从 context 读取）
            project_root: 项目根目录
            agents: Agent registry
            event_bus: EventBus 实例
            skills_dir: Skills 目录路径

        Returns:
            恢复后的 Runner 实例，context.current_state 停留在 Gate 状态
        """
        context = RunContext.load(run_root)

        # base_run_root 是实际 run 目录的父目录
        base_run_root = os.path.dirname(os.path.abspath(run_root))

        runner = cls(
            workflow=workflow,
            goal=goal or context.goal,
            project_root=project_root,
            run_root=base_run_root,
            agents=agents,
            event_bus=event_bus,
            skills_dir=skills_dir,
        )

        runner._run_id = context.run_id
        runner.context = context

        # 设置 guard 启动时间
        runner.guard_checker.set_start_time(
            datetime.fromisoformat(context.started_at)
        )

        return runner

    @property
    def run_id(self) -> str:
        return self._run_id

    def _get_event_bus(self):
        """延迟获取 EventBus。"""
        if self._event_bus is None:
            try:
                from ..observability.event_bus import EventBus
                self._event_bus = EventBus()
            except ImportError:
                self._event_bus = _NullEventBus()
        return self._event_bus

    def _mount_observability_sinks(self):
        """P0a: 为 EventBus 挂载默认 sink（ConsoleSink + JSONLSink）。

        仅在 EventBus 非外部注入时挂载（外部 EventBus 已有自己的 sink 配置）。
        """
        if self._event_bus_external:
            return

        bus = self._get_event_bus()
        if isinstance(bus, _NullEventBus):
            return

        try:
            from ..observability.console_sink import ConsoleSink
            bus.add_sink(ConsoleSink())
        except ImportError:
            pass

        try:
            from ..observability.jsonl_sink import JSONLSink
            jsonl_path = os.path.join(self.context.run_root, "logs", "events.jsonl")
            self._jsonl_sink = JSONLSink(jsonl_path)
            bus.add_sink(self._jsonl_sink)
        except ImportError:
            pass

    def start(self) -> str:
        """初始化运行上下文并返回 run_id。"""
        base_name = _generate_run_id(
            topic=self.topic,
            workflow_name=self.workflow.name,
            goal=self.goal,
        )

        # 同名目录自动 _v1/_v2 递增
        self._run_id = base_name
        run_root = os.path.join(self.base_run_root, self._run_id)
        if os.path.exists(run_root):
            v = 1
            while True:
                self._run_id = f"{base_name}_v{v}"
                run_root = os.path.join(self.base_run_root, self._run_id)
                if not os.path.exists(run_root):
                    break
                v += 1

        self.context = RunContext.create(
            workflow_id=self.workflow.name,
            goal=self.goal,
            project_root=self.project_root,
            run_id=self._run_id,
            run_root=run_root,
        )

        # 创建目录结构
        os.makedirs(os.path.join(run_root, "staging"), exist_ok=True)
        os.makedirs(os.path.join(run_root, "artifacts"), exist_ok=True)
        os.makedirs(os.path.join(run_root, "logs"), exist_ok=True)

        # P0a: 挂载默认 observability sink（ConsoleSink + JSONLSink）
        self._mount_observability_sinks()

        # P0b: 保存 workflow snapshot 到 context（供 status/explain 使用）
        self.context.workflow_variables["_workflow_snapshot"] = self.workflow.to_dict()

        # 初始化 current_state
        self.context.current_state = self.sm.initial_state

        # Guard 设置启动时间
        self.guard_checker.set_start_time(
            datetime.fromisoformat(self.context.started_at)
        )

        # P0f: 写入 run_index.json（必须在 skill 检查之前，确保失败的 run 也能被 cancel 发现）
        self._write_run_index()

        # P0d: 加载 required skills
        if self.workflow.required_skills:
            if self.skills_dir and os.path.isdir(self.skills_dir):
                try:
                    from ..skills.adoption import AdoptionProtocol
                    self._adoption = AdoptionProtocol(
                        self.skills_dir,
                        self.workflow.required_skills,
                    )
                    # 预加载 required skills（验证可用性，缺失则 fail-fast）
                    self._adoption.adopt(
                        "__init__",
                        task_skills=None,
                        context=self.context,
                    )
                except RuntimeError:
                    raise  # required skill 缺失，fail-fast
                except ImportError:
                    pass  # skills 模块不可用时静默跳过
            else:
                # skills_dir 未配置但有 required_skills → 创建 run 后立即 WorkflowFailed
                self._get_event_bus().emit("WorkflowFailed", {
                    "run_id": self._run_id,
                    "final_state": "failed",
                    "reason": "required_skills_missing",
                    "required_skills": list(self.workflow.required_skills),
                    "skills_dir": self.skills_dir or "(未配置)",
                    "timestamp": _now_iso(),
                })
                self.context.current_state = "failed"
                self.context.save()
                return self._run_id  # 不继续执行主循环

        # 持久化初始状态
        self.context.save()

        # 发射 WorkflowStarted 事件
        self._get_event_bus().emit("WorkflowStarted", {
            "run_id": self._run_id,
            "workflow_id": self.workflow.name,
            "goal": self.goal,
            "initial_state": self.sm.initial_state,
            "timestamp": _now_iso(),
        })

        return self._run_id

    def run(self) -> str:
        """运行主循环直到终止状态。"""
        if self.context is None:
            raise RuntimeError("请先调用 start() 初始化运行")

        self._running = True
        self._start_heartbeat()

        try:
            current_state = self.context.current_state

            while not self.sm.is_terminal(current_state) and not self._cancelled:
                # P0f: 每次循环迭代开始时检查取消文件
                self._check_cancel_file()

                if self._cancelled:
                    break

                # 1. Guard 检查
                guard_result = self.guard_checker.check(current_state, self.context)
                if not guard_result.passed:
                    self._get_event_bus().emit("GuardFailed", guard_result.__dict__)
                    self._transition_to(guard_result.next_state_if_failed)
                    break

                # 2. 发射 StateEntered
                self.context.record_state_visit(current_state)
                self._get_event_bus().emit("StateEntered", {
                    "state": current_state,
                    "task": self.workflow.states[current_state].task if current_state in self.workflow.states else None,
                    "attempt": self.context.get_attempt(current_state),
                    "timestamp": _now_iso(),
                })

                # 3. 执行当前 state 的 task
                task_result = self._execute_state(current_state)

                # 4. 校验和记录 TaskResult（P0c: 含 TaskResult/Artifact 双重校验 + 阻断链）
                if task_result:
                    self.context.record_task_result(current_state, task_result.to_dict())

                    # P0c: 写入 staging/<state>/task_result.json
                    self._write_task_result_json(current_state, task_result)

                    # P0c: 校验 TaskResult + Artifact
                    has_blocking, warnings_list = self._validate_task_result(
                        task_result, current_state
                    )

                    if has_blocking:
                        # Blocking error → 拒绝 promotion，强制 fail
                        self._get_event_bus().emit("ValidatorFinished", {
                            "state": current_state,
                            "passed": False,
                            "status_text": "FAIL",
                            "blocking": True,
                            "errors": [
                                e for e in self._last_validation_result.errors
                            ] if hasattr(self, '_last_validation_result') else [],
                            "timestamp": _now_iso(),
                        })
                        # 强制 decision 为 fail
                        task_result.decision = "fail"
                        task_result.status = "invalid_output"
                        if not task_result.issues:
                            from ..tasks.result import Issue
                            task_result.issues = []
                        task_result.issues.append(
                            Issue(
                                severity="blocking",
                                title="Validator blocking error",
                                detail="TaskResult/Artifact 校验发现阻塞性错误，promotion 已拒绝",
                            ).to_dict() if hasattr(Issue, 'to_dict') else {
                                "severity": "blocking",
                                "title": "Validator blocking error",
                                "detail": "TaskResult/Artifact 校验发现阻塞性错误，promotion 已拒绝",
                            }
                        )
                    else:
                        # 校验通过或仅有 warning → 允许 promotion
                        self._get_event_bus().emit("ValidatorFinished", {
                            "state": current_state,
                            "passed": True,
                            "status_text": "OK",
                            "warnings": warnings_list,
                            "timestamp": _now_iso(),
                        })

                        # P0c: Promote artifacts（检查返回值）
                        self._promote_artifacts(task_result)

                # 5. 发射 TaskFinished（含耗时、token、agent 信息）
                decision = task_result.get_decision() if task_result else "fail"
                status = task_result.status if task_result else "failed"
                task_agent = task_result.agent if task_result else ""
                if not task_agent:
                    task_agent = self.context.workflow_variables.get("_current_agent", "")

                # 计算耗时与 token（使用 to_dict 避免 ExecutionMetadata 对象访问问题）
                duration_seconds = 0.0
                input_tokens = 0
                output_tokens = 0
                if task_result:
                    tr_dict = task_result.to_dict()
                    exec_meta = tr_dict.get("execution", {})
                    if isinstance(exec_meta, dict):
                        started = exec_meta.get("started_at", "")
                        finished = exec_meta.get("finished_at", "")
                        if started and finished:
                            try:
                                s = datetime.fromisoformat(str(started))
                                f = datetime.fromisoformat(str(finished))
                                duration_seconds = (f - s).total_seconds()
                            except Exception:
                                pass
                    tu = tr_dict.get("token_usage", {})
                    if isinstance(tu, dict):
                        input_tokens = tu.get("input_tokens", 0)
                        output_tokens = tu.get("output_tokens", 0)

                self._get_event_bus().emit("TaskFinished", {
                    "state": current_state,
                    "decision": decision,
                    "status": status,
                    "agent": task_agent,
                    "duration_seconds": duration_seconds,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "timestamp": _now_iso(),
                })

                # 5b. Gate 状态检查：若当前 state 是 Gate 状态，暂停循环
                #     暂停位置在 task 执行+校验+promote 之后、resolve_transition 之前。
                #     此时 context.current_state 仍为 gate state，
                #     保证 continue_from_gate() 是唯一能从 gate transition 到下一状态的路径。
                if self.sm.is_gate_state(current_state):
                    self.context.workflow_variables["_paused_at_gate"] = current_state
                    self.context.workflow_variables["_run_status"] = "waiting_human_approval"
                    self.context.save()
                    self._get_event_bus().emit("WorkflowPausedAtGate", {
                        "run_id": self._run_id,
                        "gate_state": current_state,
                        "timestamp": _now_iso(),
                    })
                    break

                # 6. Transition
                transition = self.sm.resolve_transition(current_state, decision)
                self._get_event_bus().emit("TransitionSelected", transition.to_event_dict())

                # 7. 更新状态
                next_state = transition.next_state
                self._transition_to(next_state)
                current_state = next_state

                # 持久化
                self.context.save()

            # 循环结束 — 根据终态发不同事件
            _run_status = self.context.workflow_variables.get("_run_status", "")
            if _run_status == "waiting_human_approval":
                self._get_event_bus().emit("WorkflowAwaitingApproval", {
                    "run_id": self._run_id,
                    "gate_state": self.context.workflow_variables.get("_paused_at_gate", ""),
                    "current_state": current_state,
                    "timestamp": _now_iso(),
                })
            elif self._cancelled:
                self._get_event_bus().emit("WorkflowCancelled", {
                    "run_id": self._run_id,
                    "final_state": current_state,
                    "reason": "cancelled by user",
                    "timestamp": _now_iso(),
                })
            elif current_state == "failed":
                self._get_event_bus().emit("WorkflowFailed", {
                    "run_id": self._run_id,
                    "final_state": current_state,
                    "last_decision": self.context.task_results.get(current_state, {}).get("decision", ""),
                    "last_status": self.context.task_results.get(current_state, {}).get("status", ""),
                    "stage_summary": self._build_stage_summary(),
                    "timestamp": _now_iso(),
                })
            else:
                self._get_event_bus().emit("WorkflowCompleted", {
                    "run_id": self._run_id,
                    "final_state": current_state,
                    "total_states": len(self.context.state_history),
                    "stage_summary": self._build_stage_summary(),
                    "timestamp": _now_iso(),
                })

        finally:
            self._stop_heartbeat()
            self._running = False
            # P0a: flush + close JSONLSink
            if self._jsonl_sink:
                try:
                    self._jsonl_sink.flush()
                    self._jsonl_sink.close()
                except Exception:
                    pass
            # flush EventBus
            try:
                self._get_event_bus().flush()
            except Exception:
                pass
            if self.context:
                self.context.save()

        return current_state

    def _write_run_index(self):
        """P0f: 写入 run_index.json，记录 run_id → run_root 映射。

        供 cross-cwd cancel CLI 发现 run_root。
        """
        index_dir = os.path.join(self.project_root, "doc")
        os.makedirs(index_dir, exist_ok=True)
        index_path = os.path.join(index_dir, "run_index.json")

        # 读取现有映射
        existing = {}
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # 更新映射
        existing[self._run_id] = self.context.run_root

        # 原子写入
        tmp_path = index_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, index_path)
        except Exception:
            pass

    def _check_cancel_file(self):
        """P0f: 检查取消文件是否存在，存在则标记取消并删除文件。"""
        if self.context is None:
            return
        cancel_path = os.path.join(self.context.run_root, "cancelled")
        if os.path.exists(cancel_path):
            self._cancelled = True
            reason = "cancelled by user"
            try:
                with open(cancel_path, "r", encoding="utf-8") as f:
                    reason = f.read().strip() or reason
            except Exception:
                pass
            # 删除取消文件（避免残留）
            try:
                os.remove(cancel_path)
            except Exception:
                pass

    def _write_task_result_json(self, state_name: str, task_result):
        """P0c: 将 TaskResult 序列化写入 staging/<state>/task_result.json。"""
        try:
            staging_dir = os.path.join(self.context.run_root, "staging", state_name)
            os.makedirs(staging_dir, exist_ok=True)
            tr_path = os.path.join(staging_dir, "task_result.json")
            with open(tr_path, "w", encoding="utf-8") as f:
                f.write(task_result.to_json())
            self._get_event_bus().emit("TaskResultWritten", {
                "state": state_name,
                "path": tr_path,
                "timestamp": _now_iso(),
            })
        except Exception:
            # 写入失败不阻塞主流程（但记录为 issue）
            pass

    def _build_stage_summary(self) -> list[dict[str, Any]]:
        """构建阶段汇总：从 task_results 提取每个阶段的关键指标。"""
        summary = []
        # 按 state_history 顺序排列
        seen = set()
        for state_name in self.context.state_history:
            if state_name in seen:
                continue
            seen.add(state_name)
            tr = self.context.task_results.get(state_name, {})
            if not tr:
                continue

            # 提取 agent（优先 task_result.agent，其次 per-state 记录，最后 fallback）
            agent = tr.get("agent", "") or ""
            if not agent:
                agent = self.context.workflow_variables.get(f"_agent_{state_name}", "")
            if not agent:
                agent = self.context.workflow_variables.get("_current_agent", "")

            # 计算耗时
            duration_seconds = 0
            exec_meta = tr.get("execution", {})
            if isinstance(exec_meta, dict):
                started = exec_meta.get("started_at", "")
                finished = exec_meta.get("finished_at", "")
                if started and finished:
                    try:
                        s = datetime.fromisoformat(str(started))
                        f = datetime.fromisoformat(str(finished))
                        duration_seconds = (f - s).total_seconds()
                    except Exception:
                        pass

            # token
            tu = tr.get("token_usage", {})
            input_tokens = tu.get("input_tokens", 0) if isinstance(tu, dict) else 0
            output_tokens = tu.get("output_tokens", 0) if isinstance(tu, dict) else 0
            cache_tokens = tu.get("cache_read_input_tokens", 0) if isinstance(tu, dict) else 0

            summary.append({
                "state": state_name,
                "agent": agent,
                "status": tr.get("status", ""),
                "decision": tr.get("decision", ""),
                "duration_seconds": duration_seconds,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_tokens,
            })
        return summary

    def _validate_task_result(self, task_result, state_name: str) -> tuple[bool, list[str]]:
        """P0c: 校验 TaskResult 和 Artifact，返回 (has_blocking_errors, warnings)。

        Blocking errors: schema_version < 1, 缺少必需字段, execution metadata 缺失,
                         artifact staging 文件不存在, artifact 路径逃逸
        Warnings: 无效 status, 无效 decision, decision 不在 allowed_decisions
        """
        has_blocking = False
        all_warnings: list[str] = []

        # 获取 state 的 allowed_decisions
        task_model = None
        state = self.workflow.get_state(state_name)
        if state and state.task:
            task_model = self.workflow.get_task(state.task)

        allowed_decisions = task_model.allowed_decisions if task_model else []

        # 1. TaskResultValidator
        try:
            from ..validators.task_result import TaskResultValidator
            tr_validator = TaskResultValidator(allowed_decisions=allowed_decisions)
            validation_result = tr_validator.validate(task_result.to_dict())

            if validation_result.errors:
                has_blocking = True
            if validation_result.warnings:
                all_warnings.extend(validation_result.warnings)
        except ImportError:
            pass

        # 2. ArtifactValidator — 对每个 artifact staging path 做文件级校验
        try:
            from ..validators.artifact import ArtifactValidator
            av = ArtifactValidator()
            for artifact in task_result.get_artifacts():
                staging_path = artifact.staging_path
                # 确保路径解析正确
                if staging_path and not os.path.isabs(staging_path):
                    staging_path = os.path.join(self.context.run_root, staging_path)

                # 自动修正：如果 staging_path 文件不存在，尝试预期路径 staging/{state_name}/{filename}
                if staging_path and not os.path.exists(staging_path):
                    filename = os.path.basename(staging_path)
                    expected_path = os.path.join(
                        self.context.run_root, "staging", state_name, filename
                    )
                    if os.path.exists(expected_path):
                        all_warnings.append(
                            f"staging_path 自动修正: {artifact.staging_path} -> {expected_path}"
                        )
                        # 更新 ArtifactRef
                        artifact.staging_path = expected_path
                        staging_path = expected_path
                        # 同步更新原始 TaskResult.artifacts 中的 dict（确保 promotion 可见）
                        raw_artifacts = task_result.artifacts
                        for j, raw_a in enumerate(raw_artifacts):
                            if isinstance(raw_a, dict) and raw_a.get("name") == artifact.name:
                                raw_a["staging_path"] = expected_path
                                break

                ar = av.validate(staging_path)
                if ar.errors:
                    # artifact 文件缺失视为 blocking
                    has_blocking = True
                    all_warnings.extend(ar.errors)
                if ar.warnings:
                    all_warnings.extend(ar.warnings)
        except ImportError:
            pass

        # 3. 路径 containment 检查（P0g 在 promotion.py 中也做，此处做提前检测）
        try:
            from ..artifacts.promotion import _check_path_containment
            for artifact in task_result.get_artifacts():
                if artifact.staging_path and artifact.artifact_path:
                    staging_ok = _check_path_containment(
                        artifact.staging_path,
                        os.path.join(self.context.run_root, "staging"),
                    )
                    # artifact_path 可能是相对路径（如 "artifacts/plan_doc.md"），
                    # 需先相对于 run_root 解析，否则 abspath 会以 CWD 为基准导致逃逸误判
                    artifact_full = artifact.artifact_path
                    if not os.path.isabs(artifact_full):
                        artifact_full = os.path.join(
                            self.context.run_root, artifact.artifact_path
                        )
                    artifact_ok = _check_path_containment(
                        artifact_full,
                        os.path.join(self.context.run_root, "artifacts"),
                    )
                    if not staging_ok:
                        has_blocking = True
                        all_warnings.append(
                            f"staging 路径逃逸: {artifact.staging_path}"
                        )
                    if not artifact_ok:
                        has_blocking = True
                        all_warnings.append(
                            f"artifact 路径逃逸: {artifact.artifact_path}"
                        )
        except ImportError:
            pass

        return has_blocking, all_warnings

    def _execute_state(self, state_name: str):
        """执行一个 state 的 task。"""
        state = self.workflow.get_state(state_name)
        if state is None:
            return self._create_error_result(
                state_name, f"状态 '{state_name}' 未定义"
            )

        task_model = self.workflow.get_task(state.task) if state.task else None
        if task_model is None and state.task:
            return self._create_error_result(
                state_name, f"Task '{state.task}' 未定义"
            )

        # P0b: 维护 current_task
        if self.context:
            self.context.current_task = state.task if state.task else None

        # 解决 Role → Agent
        agent_name = self._resolve_agent(task_model) if task_model else "mock"

        # P0b: 维护 _current_agent
        if self.context:
            self.context.workflow_variables["_current_agent"] = agent_name
            # 记录每个 state 使用的 agent，供汇总表使用
            self.context.workflow_variables[f"_agent_{state_name}"] = agent_name

        # P0d: Skill adoption for this state
        adopted_skills: dict = {}
        skill_context_override = ""
        if self._adoption is not None and self.context:
            try:
                task_skills = []
                if task_model is not None:
                    task_skills = list(getattr(task_model, "skills", []) or [])
                adopted_skills = self._adoption.adopt(
                    state_name,
                    task_skills=task_skills,
                    context=self.context,
                )
                # 写入 staging/<state>/skill_adoption.md
                staging_adoption = self._adoption.write_adoption_artifact(
                    self.context.run_root,
                    state_name,
                    adopted_skills,
                )
                # 校验 + promote skill adoption artifact
                self._promote_skill_adoption(state_name, staging_adoption)
                # 发射 SkillAdoptionWritten 事件
                self._get_event_bus().emit("SkillAdoptionWritten", {
                    "state": state_name,
                    "skills": list(adopted_skills.keys()),
                    "timestamp": _now_iso(),
                })
                # 构建 adoption summary 用于注入 AgentInput
                if adopted_skills:
                    skill_context_override = self._adoption.build_summary(adopted_skills)
            except RuntimeError as e:
                # Skill 缺失 → 返回错误 TaskResult
                return self._create_error_result(
                    state_name,
                    f"Skill adoption 失败: {e}",
                )

        # 构建 AgentInput
        agent_input = self._build_agent_input(
            state_name,
            task_model,
            agent_name,
            adopted_skills=adopted_skills,
        )

        # 注入 skill adoption summary
        if skill_context_override:
            agent_input.skill_context = skill_context_override

        # 发射 AgentStarted
        self._get_event_bus().emit("AgentStarted", {
            "state": state_name,
            "task": task_model.name if task_model else None,
            "agent": agent_name,
            "timestamp": _now_iso(),
        })

        # 执行 Agent
        start_time = time.time()
        try:
            result = self._run_agent(agent_name, agent_input, state_name)
            exec_time = time.time() - start_time

            # 填充 execution metadata（如果 Agent 没有提供）
            if result and not result.execution:
                result.execution = {
                    "started_at": _now_iso(),
                    "finished_at": _now_iso(),
                    "duration_seconds": exec_time,
                    "attempt": self.context.get_attempt(state_name),
                    "exit_code": 0,
                }

            return result

        except Exception as e:
            exec_time = time.time() - start_time
            return self._create_error_result(
                state_name,
                f"Agent 执行异常: {e}",
                exec_time,
            )

    def _build_agent_input(
        self,
        state_name: str,
        task_model: TaskModel | None,
        agent_name: str,
        adopted_skills: dict | None = None,
    ) -> AgentInput:
        """构建 AgentInput。"""
        # 转换 TaskModel → TaskConfig
        task_config = AgentTaskConfig(
            name=task_model.name if task_model else state_name,
            instruction=task_model.instruction if task_model else "",
            agent=task_model.agent if task_model else agent_name,
            inputs=task_model.inputs if task_model else [],
            output=task_model.output if task_model else "",
        )

        # 获取 Skill 上下文
        skill_context = ""
        skill_policy = {"allowed_decisions": task_model.allowed_decisions} if task_model and task_model.allowed_decisions else {}
        try:
            from ..skills.adoption import get_adoption_summary
            skill_context = get_adoption_summary(self.context)
        except ImportError:
            pass

        if adopted_skills is not None and task_model is not None:
            try:
                from ..skills.policy import resolve_skill_policy
                skill_policy = resolve_skill_policy(
                    adopted_skills,
                    task_allowed_decisions=task_model.allowed_decisions,
                )
            except ImportError:
                pass

        # 构建 staging paths
        staging_dir = os.path.join(self.context.run_root, "staging", state_name)
        output_name = task_config.output or "output"
        staging_paths = {
            output_name: os.path.join(staging_dir, f"{output_name}.md"),
            "task_result": os.path.join(staging_dir, "task_result.json"),
        }

        # 获取 TaskResult schema
        allowed_decisions = task_model.allowed_decisions if task_model else []
        try:
            from ..tasks.result_schema import build_task_result_schema
            schema = build_task_result_schema(allowed_decisions)
        except ImportError:
            schema = {}

        return AgentInput(
            task=task_config,
            context=self.context,
            state_name=state_name,
            skill_context=skill_context,
            skill_policy=skill_policy,
            expected_task_result_schema=schema,
            staging_paths=staging_paths,
        )

    def _run_agent(
        self,
        agent_name: str,
        agent_input: AgentInput,
        state_name: str,
    ):
        """运行 Agent。

        优先使用注册的 agent adapter，fallback 到 mock agent。
        """
        # 尝试获取已注册的 agent adapter
        adapter = None
        if self._agent_registry and agent_name in self._agent_registry:
            adapter = self._get_agent_adapter(agent_name)
        else:
            # 使用 mock agent
            try:
                from ..agents.mock import MockAgent
                adapter = MockAgent({"decision_script": self._mock_script})
            except ImportError:
                pass

        if adapter is None:
            return self._create_error_result(
                state_name,
                f"Agent '{agent_name}' 未注册且无 mock fallback",
            )

        # 确保 staging 目录存在
        staging_dir = os.path.join(self.context.run_root, "staging", state_name)
        os.makedirs(staging_dir, exist_ok=True)

        # 执行
        result = adapter.execute(agent_input)
        return result

    def _get_agent_adapter(self, agent_name: str):
        """获取 Agent adapter 实例。"""
        try:
            from ..agents.registry import AgentRegistry
            registry = AgentRegistry(self._agent_registry)
            return registry.resolve(agent_name)
        except ImportError:
            return None

    def _resolve_agent(self, task_model: TaskModel | None) -> str:
        """解析 Task → Agent 名称。"""
        if task_model is None:
            return "mock"
        return task_model.agent or "mock"

    def _get_current_task_model(self) -> TaskModel | None:
        """获取当前 state 对应的 TaskModel。"""
        if self.context is None or not self.context.current_state:
            return None
        state = self.workflow.get_state(self.context.current_state)
        if state is None or not state.task:
            return None
        return self.workflow.get_task(state.task)

    def _transition_to(self, next_state: str):
        """执行状态迁移。"""
        if self.context:
            self.context.current_state = next_state
            self.context.touch()

    def _promote_artifacts(self, task_result):
        """P0c: Promote artifacts（从 staging 到正式 artifacts）。

        仅在 promote_artifact() 返回 ok=True 时才更新 RunContext.artifacts。
        失败时 artifact 留在 staging，不污染正式 artifacts。

        支持 version_strategy：
        - "overwrite"（默认）：每次覆盖同名文件，artifacts 始终指向最新版
        - "increment"：自动递增版本号，如 plan_doc-v1.md, plan_doc-v2.md, ...
        """
        if task_result is None:
            return

        # 获取当前 task 的 version_strategy
        task_model = self._get_current_task_model()
        version_strategy = (
            getattr(task_model, "version_strategy", "overwrite")
            if task_model else "overwrite"
        )

        artifacts = task_result.get_artifacts()
        for artifact in artifacts:
            # 根据 version_strategy 决定最终 artifact_path
            if version_strategy == "increment":
                # 版本号基于该产物流已有版本链长度递增，而非 state 的 attempt。
                # loop 展开后每轮是独立 state（如 plan_review_r1/_r2），attempt 恒为 1，
                # 用 attempt 会让各轮都生成 -v1 而相互覆盖；用版本链长度可跨 state 累积。
                existing = len(self.context.artifact_versions.get(artifact.name, []))
                version = existing + 1
                base, ext = os.path.splitext(artifact.artifact_path)
                versioned_path = f"{base}-v{version}{ext}"
            else:
                versioned_path = artifact.artifact_path

            try:
                from ..artifacts.promotion import promote_artifact
                result = promote_artifact(
                    staging_path=artifact.staging_path,
                    artifact_path=versioned_path,
                    run_root=self.context.run_root,
                    artifact_name=artifact.name,
                )
                if result.ok:
                    # 使用版本化 promote（保留完整版本链）
                    self.context.promote_artifact_versioned(artifact.name, versioned_path)
                    self._get_event_bus().emit("ArtifactPromoted", {
                        "name": artifact.name,
                        "artifact_path": versioned_path,
                        "version_strategy": version_strategy,
                    })
                else:
                    # Promotion 失败 → 记录事件，不更新 context
                    self._get_event_bus().emit("ArtifactPromotionFailed", {
                        "name": artifact.name,
                        "staging_path": artifact.staging_path,
                        "artifact_path": versioned_path,
                        "error": result.error,
                    })
            except ImportError:
                # promotion 模块不可用 → 直接记录（向后兼容）
                self.context.promote_artifact_versioned(artifact.name, versioned_path)

    def _promote_skill_adoption(self, state_name: str, staging_path: str):
        """P0d: 校验并 promote skill adoption artifact。

        将 staging/<state>/skill_adoption.md promote 到
        artifacts/skill_adoption_<state>.md（扁平结构，无子目录），登记到 RunContext，发 ArtifactPromoted。
        """
        if not staging_path or not os.path.exists(staging_path):
            return

        artifact_name = f"skill_adoption:{state_name}"
        artifact_path = os.path.join(
            self.context.run_root, "artifacts", f"skill_adoption_{state_name}.md"
        )

        # 校验 staging 文件
        try:
            from ..validators.artifact import ArtifactValidator
            av = ArtifactValidator()
            ar = av.validate(staging_path)
            if ar.errors:
                self._get_event_bus().emit("ArtifactPromotionFailed", {
                    "name": artifact_name,
                    "staging_path": staging_path,
                    "artifact_path": artifact_path,
                    "error": "; ".join(ar.errors),
                })
                return
        except ImportError:
            pass

        # Promote
        try:
            from ..artifacts.promotion import promote_artifact
            result = promote_artifact(
                staging_path=staging_path,
                artifact_path=artifact_path,
                run_root=self.context.run_root,
                artifact_name=artifact_name,
            )
            if result.ok:
                self.context.promote_artifact(artifact_name, artifact_path)
                self._get_event_bus().emit("ArtifactPromoted", {
                    "name": artifact_name,
                    "artifact_path": artifact_path,
                    "state": state_name,
                })
            else:
                self._get_event_bus().emit("ArtifactPromotionFailed", {
                    "name": artifact_name,
                    "staging_path": staging_path,
                    "artifact_path": artifact_path,
                    "error": result.error,
                })
        except ImportError:
            # promotion 模块不可用 → 确保目录存在并复制
            os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
            import shutil
            shutil.copy2(staging_path, artifact_path)
            self.context.promote_artifact(artifact_name, artifact_path)
            self._get_event_bus().emit("ArtifactPromoted", {
                "name": artifact_name,
                "artifact_path": artifact_path,
                "state": state_name,
            })

    def continue_from_gate(self, approved: bool = False) -> str:
        """从 Gate 状态继续执行。

        仅当 workflow 停在 Gate 状态时有效。
        若 approved=True，执行从 Gate 状态到下一状态的 transition 后继续循环。
        若 approved=False，transition 到 failed。

        Args:
            approved: 是否批准通过

        Returns:
            最终状态名称

        Raises:
            RuntimeError: 若 workflow 未停在 Gate 状态
        """
        if self.context is None:
            raise RuntimeError("Context 未初始化，请先调用 start() 或 attach_existing()")

        gate_state = self.context.workflow_variables.get("_paused_at_gate")
        if not gate_state:
            raise RuntimeError("Workflow 未停在 Gate 状态，无需 continue")

        # 清除 gate 标记
        del self.context.workflow_variables["_paused_at_gate"]
        if "_run_status" in self.context.workflow_variables:
            del self.context.workflow_variables["_run_status"]

        # 根据批准结果构造 decision 并 resolve transition
        decision = "approve" if approved else "reject"
        transition = self.sm.resolve_transition(gate_state, decision)
        next_state = transition.next_state

        # 发射 transition 事件
        self._get_event_bus().emit("TransitionSelected", transition.to_event_dict())

        # 执行 transition
        self._transition_to(next_state)

        # 发射恢复事件
        self._get_event_bus().emit("WorkflowResumedAfterGate", {
            "run_id": self._run_id,
            "gate_state": gate_state,
            "approved": approved,
            "next_state": next_state,
            "timestamp": _now_iso(),
        })

        self.context.save()

        # 从 next_state 继续主循环
        self._running = True
        self._start_heartbeat()
        return self.run()

    def cancel(self, reason: str = ""):
        """取消运行。"""
        self._cancelled = True

    def _start_heartbeat(self):
        """启动心跳线程。"""
        try:
            from ..observability.heartbeat import HeartbeatEmitter
            self._heartbeat_thread = HeartbeatEmitter.start(
                run_id=self._run_id,
                context_getter=lambda: self.context,
                event_bus=self._get_event_bus(),
            )
        except ImportError:
            pass

    def _stop_heartbeat(self):
        """停止心跳线程。"""
        if self._heartbeat_thread:
            try:
                from ..observability.heartbeat import HeartbeatEmitter
                HeartbeatEmitter.stop(self._heartbeat_thread)
            except ImportError:
                pass

    def _create_error_result(
        self,
        state_name: str,
        error: str,
        duration: float = 0.0,
    ):
        """创建错误 TaskResult。"""
        from ..tasks.result import TaskResult, ExecutionMetadata, Issue
        return TaskResult(
            schema_version=1,
            task_id=state_name,
            state=state_name,
            agent="runner",
            status="failed",
            decision="fail",
            summary=error,
            execution=ExecutionMetadata(
                started_at=_now_iso(),
                finished_at=_now_iso(),
                duration_seconds=duration,
                attempt=1,
                exit_code=1,
            ),
            issues=[Issue(severity="blocking", title="执行失败", detail=error)],
        )


def cancel_run(
    run_id: str,
    reason: str = "",
    project_root: str | None = None,
    run_root: str | None = None,
) -> bool:
    """取消一个正在运行的 workflow。

    run_root 发现优先级：
    1. --run-root 显式指定
    2. --project-root + run_index.json 查找
    3. cwd-relative doc/runs/<run_id>/

    P0 实现：设置取消标记文件。
    Runner 主循环每轮检查此文件，发现后进入 cancelled 状态。
    P1 完善：信号机制。
    """
    if run_root:
        cancel_path = os.path.join(run_root, "cancelled")
    elif project_root:
        # 尝试从 run_index.json 查找
        index_path = os.path.join(project_root, "doc", "run_index.json")
        found = False
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
                if run_id in index:
                    cancel_path = os.path.join(index[run_id], "cancelled")
                    found = True
            except (json.JSONDecodeError, IOError):
                pass
        if not found:
            cancel_path = os.path.join(project_root, "doc", "runs", run_id, "cancelled")
    else:
        # 默认：cwd-relative
        cancel_path = os.path.join("doc", "runs", run_id, "cancelled")

    try:
        os.makedirs(os.path.dirname(cancel_path), exist_ok=True)
        with open(cancel_path, "w", encoding="utf-8") as f:
            f.write(reason or "cancelled by user")
        return True
    except Exception:
        return False


class _NullEventBus:
    """空 EventBus 实现，用于没有 observability 模块时的 fallback。"""

    def emit(self, event_type: str, payload: dict):
        pass

    def flush(self):
        pass
