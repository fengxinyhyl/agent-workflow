"""RunLock — 运行锁管理。

防止同一 workflow 重复启动。
P0 使用文件锁实现（跨平台兼容）。
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


class RunLock:
    """文件锁，防止同一 workflow 重复启动。

    用法:
        lock = RunLock(".agent-workflow/locks")
        if lock.acquire("software-dev"):
            try:
                # 运行 workflow
                ...
            finally:
                lock.release("software-dev")
    """

    def __init__(self, lock_dir: str):
        self.lock_dir = lock_dir
        os.makedirs(lock_dir, exist_ok=True)

    def acquire(self, workflow_id: str, run_id: str = "", timeout_seconds: int = 0) -> bool:
        """获取锁。

        返回 True 表示获取成功。
        timeout_seconds: 0 表示如果已锁立即返回 False，>0 表示等待超时。
        """
        lock_path = os.path.join(self.lock_dir, f"{workflow_id}.lock")

        # 检查已有锁
        if os.path.exists(lock_path):
            # 检查是否是 stale 锁（超过 24h）
            try:
                with open(lock_path, "r") as f:
                    data = json.load(f)
                lock_time = datetime.fromisoformat(data.get("timestamp", ""))
                age = (datetime.now(timezone(timedelta(hours=8))) - lock_time).total_seconds()
                if age > 86400:  # 24h
                    # Stale 锁，覆盖
                    pass
                else:
                    return False
            except Exception:
                # 锁文件损坏，覆盖
                pass

        # 写入锁
        lock_data = {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "timestamp": _now_iso(),
        }
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, ensure_ascii=False)

        return True

    def release(self, workflow_id: str):
        """释放锁。"""
        lock_path = os.path.join(self.lock_dir, f"{workflow_id}.lock")
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass

    def is_locked(self, workflow_id: str) -> bool:
        """检查是否已锁定。"""
        lock_path = os.path.join(self.lock_dir, f"{workflow_id}.lock")
        if not os.path.exists(lock_path):
            return False

        try:
            with open(lock_path, "r") as f:
                data = json.load(f)
            lock_time = datetime.fromisoformat(data.get("timestamp", ""))
            age = (datetime.now(timezone(timedelta(hours=8))) - lock_time).total_seconds()
            if age > 86400:
                return False  # stale
            return True
        except Exception:
            return False


def acquire_lock(workflow_id: str, lock_dir: str = ".agent-workflow/locks") -> RunLock | None:
    """便捷函数：获取锁。"""
    lock = RunLock(lock_dir)
    if lock.acquire(workflow_id):
        return lock
    return None


def release_lock(lock: RunLock, workflow_id: str):
    """便捷函数：释放锁。"""
    if lock:
        lock.release(workflow_id)
