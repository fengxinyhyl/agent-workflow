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

        # 5. 检查可达性（从 initial_state 出发的 DFS）
        reachable = self._find_reachable()
        for name in self.states:
            if name not in reachable:
                # 只警告，不阻止
                pass

        # 6. 终止状态不应有 on 转换
        for name in self.terminal_states:
            if name in self.states:
                state = self.states[name]
                if state.on:
                    issues.append(f"终止状态 '{name}' 不应定义 on 转换")

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
        return reachable

    def resolve_transition(self, state_name: str, decision: str) -> TransitionResult:
        """根据当前状态和 decision 解析下一状态。

        规则（v4）：
        - 如果 decision 在 state.on 中 → 返回对应状态
        - 否则 → 返回 state.default
        - 未知 decision 必须写 observability event 和 execution log
        """
        state = self.states.get(state_name)
        if state is None:
            return TransitionResult(
                current_state=state_name,
                decision=decision,
                next_state="failed",
                matched=False,
                reason=f"状态 '{state_name}' 未定义",
            )

        if decision in state.on:
            return TransitionResult(
                current_state=state_name,
                decision=decision,
                next_state=state.on[decision],
                matched=True,
                reason=f"匹配到 on['{decision}']",
            )

        # 走 default
        next_state = state.default or "failed"
        return TransitionResult(
            current_state=state_name,
            decision=decision,
            next_state=next_state,
            matched=False,
            reason=f"未匹配 '{decision}'，走 default → '{next_state}'",
        )

    def is_terminal(self, state_name: str) -> bool:
        """判断是否为终止状态。"""
        return state_name in self.get_terminal_states()

    def get_terminal_states(self) -> set[str]:
        """获取所有终止状态。"""
        # 自动推断：没有 on 转换也没有 task 的 state 也是 terminal
        terminals = set(self.terminal_states)
        for name, state in self.states.items():
            if not state.on and not state.task:
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

        if self.initial_state:
            dfs(self.initial_state)

        # 添加未访问的状态
        for name in self.states:
            if name not in visited:
                ordered.append(name)

        return ordered


from .transition import TransitionResult
