"""Heartbeat — 心跳机制。

每 30 秒写入一次心跳事件。
5 分钟无心跳 → 标记 stale。
状态命令显示 stale 状态。
"""

from __future__ import annotations

import os
import json
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


HEARTBEAT_INTERVAL_SECONDS = 30
STALE_THRESHOLD_SECONDS = 300  # 5 分钟


class HeartbeatEmitter:
    """心跳发射器。

    在后台线程中每 30 秒发射一次 Heartbeat 事件。

    用法:
        thread = HeartbeatEmitter.start(run_id, context_getter, event_bus)
        ...
        HeartbeatEmitter.stop(thread)
    """

    @staticmethod
    def start(
        run_id: str,
        context_getter: callable,
        event_bus: Any,
        interval: int = HEARTBEAT_INTERVAL_SECONDS,
    ) -> threading.Thread:
        """启动心跳线程。"""
        stop_event = threading.Event()

        def _heartbeat_loop():
            start_time = time.time()
            while not stop_event.is_set():
                stop_event.wait(interval)  # 等待 interval 秒或 stop 信号
                if stop_event.is_set():
                    break

                context = context_getter()
                elapsed = time.time() - start_time

                payload = {
                    "run_id": run_id,
                    "state": context.current_state if context else "unknown",
                    "agent": getattr(context, "current_task", None) or "",
                    "elapsed_seconds": int(elapsed),
                    "timestamp": _now_iso(),
                }

                try:
                    event_bus.emit("Heartbeat", payload)
                except Exception:
                    pass

                # 也写入 heartbeat 文件
                try:
                    if context:
                        heartbeat_path = os.path.join(
                            context.run_root, "heartbeat.json"
                        )
                        with open(heartbeat_path, "w") as f:
                            json.dump(payload, f, ensure_ascii=False)
                except Exception:
                    pass

        thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"heartbeat-{run_id}",
            daemon=True,
        )
        thread._stop_event = stop_event
        thread.start()
        return thread

    @staticmethod
    def stop(thread: threading.Thread):
        """停止心跳线程。"""
        if thread and hasattr(thread, "_stop_event"):
            thread._stop_event.set()
            thread.join(timeout=5)


def check_stale(
    run_root: str,
    threshold_seconds: int = STALE_THRESHOLD_SECONDS,
) -> tuple[bool, str]:
    """检查某个运行是否 stale。

    返回 (is_stale, reason)。
    """
    heartbeat_path = os.path.join(run_root, "heartbeat.json")
    if not os.path.exists(heartbeat_path):
        return True, "无心跳文件"

    try:
        with open(heartbeat_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return True, "心跳文件损坏"

    last_ts = data.get("timestamp", "")
    if not last_ts:
        return True, "心跳缺少时间戳"

    try:
        tz = timezone(timedelta(hours=8))
        last_time = datetime.fromisoformat(last_ts)
        now = datetime.now(tz)
        diff = (now - last_time).total_seconds()
    except (ValueError, TypeError):
        return True, "心跳时间戳解析失败"

    if diff > threshold_seconds:
        return True, f"最后心跳 {diff:.0f}s 前 > {threshold_seconds}s 阈值"

    return False, f"心跳正常（{diff:.0f}s 前）"
