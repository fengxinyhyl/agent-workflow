"""state_machine 模块 — 状态机、Runner、Transition、Guard、Retry。"""

from .machine import StateMachine
from .transition import resolve_transition, TransitionResult
from .guard import GuardChecker, GuardResult
from .retry import retry_run, RetryResult
from .runner import Runner

__all__ = [
    "StateMachine",
    "resolve_transition",
    "TransitionResult",
    "GuardChecker",
    "GuardResult",
    "retry_run",
    "RetryResult",
    "Runner",
]
