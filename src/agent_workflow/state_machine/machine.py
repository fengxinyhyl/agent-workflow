"""StateMachine — 工作流状态机核心。

负责：
- 状态机配置校验
- Transition 解析（ decision → next_state ）
- 终止状态判断
- 未知 decision → default 规则
"""

from __future__ import annotations

from ..config.models import WorkflowConfig, StateModel


class StateMachine:
    """工作流状态机。

    核心规则（v4）：
    1. Runner 根据 TaskResult.decision 选择 transition
    2. 未知 decision 走 default
    3. default 未配置 → failed
    4. 终止状态不跳转
    """

    def __init__(self, workflow: WorkflowConfig):
        self.workflow = workflow
        self.states = workflow.states
        self.initial_state = workflow.initial_state
        self.terminal_states = set(workflow.terminal_states)

    def validate(self) -> list[str]:
        """校验状态机完备性。"""
        issues = []

        # 1. 必须有初始状态
        if not self.initial_state:
            issues.append("缺少 initial_state")

        # 2. 初始状态必须存在
        if self.initial_state and self.initial_state not in self.states:
            issues.append(f"initial_state '{self.initial_state}' 未定义")

        # 3. 必须有终止状态
        if not self.terminal_states:
            issues.append("缺少 terminal_states（可通过 on 为空推断）")

        # 4. 检查每个非终止状态的转换完备性
        for name, state in self.states.items():
            if name in self.terminal_states:
                continue

            # 必须有 default
            if not state.default:
                issues.append(f"state '{name}' 未设置 default（非终止状态必须设置 default）")

            # default 目标必须存在
            if state.default and state.default not in self.states:
                issues.append(
                    f"state '{name}' default '{state.default}' 目标不存在"
                )

            # on 中的目标必须存在
            for decision, target in state.on.items():
                if target not in self.states:
                    issues.append(
                        f"state '{name}' on '{decision}' → '{target}' 目标不存在"
                    )

            # next 目标必须存在
            if state.next and state.next not in self.states:
                issues.append(
                    f"state '{name}' next → '{state.next}' 目标不存在"
                )

            # on_status 中的目标必须存在
            for status_key, target in state.on_status.items():
                if target not in self.states:
                    issues.append(
                        f"state '{name}' on_status '{status_key}' → '{target}' 目标不存在"
                    )

        # ── Runtime v2 护栏 1：缺失成功出口 ──
        for name, state in self.states.items():
            if name in self.terminal_states:
                continue
            has_on = bool(state.on)
            has_next = bool(state.next)
            if has_on and has_next:
                issues.append(
                    f"state '{name}' 同时定义了 on 和 next，非终止节点必须恰好定义一个成功出口"
                )
            elif not has_on and not has_next:
                issues.append(
                    f"state '{name}' 未定义成功出口（on 或 next），非终止节点必须恰好定义一个成功出口"
                )

        # ── Runtime v2 护栏 2：decision 必填一致性 ──
        for name, state in self.states.items():
            if name in self.terminal_states:
                continue
            task_model = self.workflow.tasks.get(state.task) if state.task else None
            if state.on:
                # 有 on 分支 → allowed_decisions 应非空
                if task_model and not task_model.allowed_decisions:
                    issues.append(
                        f"state '{name}' 定义了 on 分支但 task '{state.task}' "
                        f"未声明 allowed_decisions，on 键可能永远无法命中"
                    )
            elif state.next:
                # 有 next（无 on）→ allowed_decisions 不应声明（语义冲突）
                # 此处仅警告，存量 YAML 大量存在此模式
                if task_model and task_model.allowed_decisions:
                    pass  # 警告但不报错，存量兼容

        # 5. 检查可达性（从 initial_state 出发的 DFS）
        reachable = self._find_reachable()
        for name in self.states:
            if name not in reachable:
                # 只警告，不阻止
                pass

        # 6. 终止状态不应有 on/next 转换
        for name in self.terminal_states:
            if name in self.states:
                state = self.states[name]
                if state.on or state.next:
                    issues.append(f"终止状态 '{name}' 不应定义 on 或 next 转换")

        return issues

    def _find_reachable(self) -> set[str]:
        """DFS 查找从初始状态出发可达的所有状态。"""
        reachable = set()
        stack = [self.initial_state]
        while stack:
            name = stack.pop()
            if name in reachable:
                continue
            reachable.add(name)
            if name in self.states:
                state = self.states[name]
                if state.default:
                    stack.append(state.default)
                for target in state.on.values():
                    stack.append(target)
                if state.next:
                    stack.append(state.next)
                for target in state.on_status.values():
                    stack.append(target)
        return reachable

    def resolve_transition(self, state_name: str, status: str, decision: str | None = None) -> TransitionResult:
        """根据当前状态、status 和 decision 解析下一状态（Runtime v2 两段式路由）。

        规则：
        - 第一段：status != success → on_status 或 default
        - 第二段：status = success → on（分支）或 next（线性）
        - 未知 state → "failed"（配置缺失，validate 期拦截）
        """
        state = self.states.get(state_name)
        if state is None:
            return TransitionResult(
                current_state=state_name, status=status, decision=decision or "",
                next_state="failed", matched=False, route_by="status",
                reason=f"状态 '{state_name}' 未定义",
            )

        # ── 第一段：status != success → on_status 或 default ──
        if status != "success":
            if status in state.on_status:
                return TransitionResult(
                    current_state=state_name, status=status, decision=decision or "",
                    next_state=state.on_status[status], matched=True, route_by="status",
                    reason=f"status={status}, on_status 匹配 → '{state.on_status[status]}'",
                )
            route_target = state.default or "failed"
            return TransitionResult(
                current_state=state_name, status=status, decision=decision or "",
                next_state=route_target, matched=False, route_by="status",
                reason=f"status={status}, 无 on_status 映射, 走 default → '{route_target}'",
            )

        # ── 第二段：status = success ──
        # 分支 3: on 中有匹配的 decision
        if state.on and decision in state.on:
            return TransitionResult(
                current_state=state_name, status=status, decision=decision or "",
                next_state=state.on[decision], matched=True, route_by="decision",
                reason=f"status=success, decision='{decision}' 匹配 on",
            )

        # 分支 4: on 存在但 decision 未匹配 → default（仍在 decision 分支）
        if state.on:
            return TransitionResult(
                current_state=state_name, status=status, decision=decision or "",
                next_state=state.default or "failed", matched=False, route_by="decision",
                reason=f"decision='{decision}' 未匹配 on，走 default → '{state.default}'",
            )

        # 分支 5: 线性节点 → next
        if state.next:
            return TransitionResult(
                current_state=state_name, status=status, decision=decision or "",
                next_state=state.next, matched=True, route_by="next",
                reason=f"status=success, 线性节点 next → '{state.next}'",
            )

        # 分支 6: 无 on 也无 next → default（配置疏漏，validate 期拦截）
        route_target = state.default or "failed"
        return TransitionResult(
            current_state=state_name, status=status, decision=decision or "",
            next_state=route_target, matched=False, route_by="status",
            reason=f"status=success, 无 on/next, 走 default → '{route_target}'",
        )

    def is_terminal(self, state_name: str) -> bool:
        """判断是否为终止状态。"""
        return state_name in self.get_terminal_states()

    def is_gate_state(self, state_name: str) -> bool:
        """判断是否为 Gate 状态（需外部输入才能继续）。

        Gate 状态在执行完 task 后，Runner 会自动暂停主循环，
        等待外部调用 continue_from_gate() 才能 transition 到下一状态。
        """
        state = self.states.get(state_name)
        if state is None:
            return False
        return state.gate

    def get_terminal_states(self) -> set[str]:
        """获取所有终止状态。"""
        # 自动推断：没有 on 且没有 next 且没有 task 的 state 也是 terminal
        terminals = set(self.terminal_states)
        for name, state in self.states.items():
            if not state.on and not state.next and not state.task:
                terminals.add(name)
        return terminals

    def get_state_names(self) -> list[str]:
        """获取所有状态名称列表（按初始状态→终止状态顺序）。"""
        ordered = []
        visited = set()

        def dfs(name):
            if name in visited or name not in self.states:
                return
            visited.add(name)
            ordered.append(name)
            state = self.states[name]
            for target in state.on.values():
                dfs(target)
            if state.default:
                dfs(state.default)
            if state.next:
                dfs(state.next)
            for target in state.on_status.values():
                dfs(target)

        if self.initial_state:
            dfs(self.initial_state)

        # 添加未访问的状态
        for name in self.states:
            if name not in visited:
                ordered.append(name)

        return ordered


from .transition import TransitionResult
