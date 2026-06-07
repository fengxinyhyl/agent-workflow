"""StateStore — 状态持久化。"""

from __future__ import annotations

import json
import os
from typing import Any


class StateStore:
    """状态持久化存储。

    管理:
    - workflow_state.json: 运行时状态（RunContext 序列化）
    - heartbeat.json: 心跳状态
    - cancelled: 取消标记
    """

    def __init__(self, run_root: str):
        self.run_root = run_root
        os.makedirs(run_root, exist_ok=True)

    @property
    def state_path(self) -> str:
        return os.path.join(self.run_root, "workflow_state.json")

    @property
    def heartbeat_path(self) -> str:
        return os.path.join(self.run_root, "heartbeat.json")

    @property
    def cancel_path(self) -> str:
        return os.path.join(self.run_root, "cancelled")

    def save_state(self, data: dict[str, Any]):
        """保存运行状态。"""
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_state(self) -> dict[str, Any]:
        """加载运行状态。"""
        if not os.path.exists(self.state_path):
            return {}
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_heartbeat(self, data: dict[str, Any]):
        """保存心跳。"""
        with open(self.heartbeat_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load_heartbeat(self) -> dict[str, Any]:
        """加载心跳。"""
        if not os.path.exists(self.heartbeat_path):
            return {}
        with open(self.heartbeat_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def mark_cancelled(self, reason: str = ""):
        """标记取消。"""
        with open(self.cancel_path, "w", encoding="utf-8") as f:
            f.write(reason or "cancelled by user")

    def is_cancelled(self) -> bool:
        """检查是否已取消。"""
        return os.path.exists(self.cancel_path)

    def get_cancel_reason(self) -> str:
        """获取取消原因。"""
        if not os.path.exists(self.cancel_path):
            return ""
        with open(self.cancel_path, "r", encoding="utf-8") as f:
            return f.read().strip()


def save_workflow_state(run_root: str, data: dict[str, Any]):
    """便捷函数：保存运行状态。"""
    StateStore(run_root).save_state(data)


def load_workflow_state(run_root: str) -> dict[str, Any]:
    """便捷函数：加载运行状态。"""
    return StateStore(run_root).load_state()
