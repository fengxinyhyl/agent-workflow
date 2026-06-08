"""QueueRunner — FIFO 执行器。

MVP 核心对象之一。负责将 ready 的 WorkItem 按 FIFO 顺序执行，
emit 事件，更新 workflow_state.json。

执行规则：
1. load workflow_state.json
2. compute ready_items
3. if paused → stop
4. pick ready_items[0]（FIFO）
5. emit ITEM_STARTED
6. execute item handler（handler 原地修改 item 的 status/artifact_path）
7. on success: emit ITEM_COMPLETED
8. on failure: emit ITEM_FAILED, mark failed, stop (fail-fast)
9. write workflow_state.json
10. repeat until no ready item

Handler 合约：
- 签名: Callable[[WorkItem], None]
- 成功: 设置 item.status = COMPLETED, item.artifact_path, 正常返回
- 失败: 抛出异常
- Runner 不负责自动设置 status/artifact_path；handler 必须原地修改 item。

MVP 不做 priority、不做并行、不做 retry、不做 cost-based ordering。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from workflow_run import WorkflowRun, RunStatus
from work_item import WorkItem, ItemStatus
from event_log import EventLog, WorkflowEvent, TZ_SHANGHAI
from state_store import StateStore, check_consistency
from dependency_graph import DependencyGraph


ItemHandler = Callable[[WorkItem], None]
"""Handler 类型签名。

合约：
- 接收 WorkItem 实例（可原地修改）
- 成功时设置 item.status = ItemStatus.COMPLETED 并设置 item.artifact_path，正常返回
- 失败时抛出异常（任何 Exception 子类）
"""


class QueueRunner:
    """FIFO 执行器 — MVP 的核心执行循环。

    用法:
        runner = QueueRunner(
            workflow_run=my_run,
            items=my_items,
            handler=my_handler,
            event_log=event_log,
            state_store=state_store,
        )
        runner.run()
    """

    def __init__(
        self,
        workflow_run: WorkflowRun,
        items: list[WorkItem],
        handler: ItemHandler,
        event_log: EventLog,
        state_store: StateStore,
    ):
        """初始化 QueueRunner。

        注意：参数名为 workflow_run 而非 run，避免与方法 run() 命名冲突。

        Args:
            workflow_run: 要执行的 WorkflowRun 实例
            items: 所有 WorkItem 列表（不要求顺序，DependencyGraph 负责就绪判定）
            handler: 每个 item 的执行回调，遵循 ItemHandler 合约
            event_log: 事件日志实例
            state_store: 状态存储实例
        """
        self.workflow_run = workflow_run
        self.items = items
        self._item_map = {item.id: item for item in items}
        self.handler = handler
        self.event_log = event_log
        self.state_store = state_store
        self._paused = False

    def run(self) -> None:
        """执行主循环。

        循环执行直到以下任一条件：
        - 无 ready item（全部完成或全部阻塞）
        - 某 item 执行失败（fail-fast）
        - workflow 被暂停

        Raises:
            ValueError: 若 state 与 event log 不一致（在启动时检测）
        """
        # 启动前状态一致性检查
        state = self.state_store.load()
        if state:
            events = self.event_log.read_by_workflow(self.workflow_run.id)
            inconsistencies = check_consistency(state, events)
            if inconsistencies:
                raise ValueError(
                    "workflow_state.json 与 Event Log 不一致，请人工检查后继续:\n"
                    + "\n".join(f"  - {e}" for e in inconsistencies)
                )

        # 恢复暂停状态
        if state.get("paused", False):
            self._paused = True

        # 首次运行：emit WORKFLOW_CREATED + WORK_ITEM_CREATED
        if self.workflow_run.status == RunStatus.PENDING:
            self._emit_workflow_created()
            self._emit_work_item_created()
            self.workflow_run.status = RunStatus.RUNNING
            self._save_state()

        # 主执行循环
        while True:
            # 1. 计算 ready items
            ready = DependencyGraph.ready_items(self.items)

            # 2. 无 ready item → 检查原因
            if not ready:
                # 全部完成？
                all_terminal = all(
                    item.status in (ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.SKIPPED)
                    for item in self.items
                )
                if all_terminal:
                    has_failure = any(
                        item.status == ItemStatus.FAILED for item in self.items
                    )
                    self.workflow_run.status = (
                        RunStatus.FAILED if has_failure else RunStatus.COMPLETED
                    )
                    self._save_state()
                break

            # 3. 检查暂停
            if self._paused:
                self.workflow_run.status = RunStatus.PAUSED
                self._save_state()
                break

            # 4. FIFO 选择第一个
            item = ready[0]

            # 5. emit ITEM_STARTED
            self._emit_item_started(item)
            item.status = ItemStatus.RUNNING
            self._save_state()

            # 6. 执行 handler
            try:
                self.handler(item)
                # 7. 成功：emit ITEM_COMPLETED
                # handler 应已设置 item.status 和 item.artifact_path
                if item.status == ItemStatus.RUNNING:
                    # handler 忘记设置状态 → 自动设为 COMPLETED
                    item.status = ItemStatus.COMPLETED
                self._emit_item_completed(item)
            except Exception as e:
                # 8. 失败：emit ITEM_FAILED, stop (fail-fast)
                item.status = ItemStatus.FAILED
                self._emit_item_failed(item, str(e))
                self.workflow_run.status = RunStatus.FAILED
                self._save_state()
                break

            # 9. 写 workflow_state.json
            self._save_state()

    def pause(self) -> None:
        """暂停 workflow。

        当前 item 完成后再停止。仅设置暂停标记，
        实际停在主循环的 ready item 获取之后。
        emit WORKFLOW_PAUSED 事件。
        """
        self._paused = True
        self._emit_workflow_paused()

    def resume(self) -> None:
        """恢复 workflow。

        清除暂停标记，emit WORKFLOW_RESUMED 事件。
        调用方需在 resume() 后重新调用 run() 以继续执行。
        """
        self._paused = False
        self.workflow_run.status = RunStatus.RUNNING
        self._emit_workflow_resumed()
        self._save_state()

    def _save_state(self) -> None:
        """保存当前状态到 workflow_state.json。"""
        self.state_store.save(self.workflow_run, self.items, paused=self._paused)

    def _now(self) -> datetime:
        """返回当前 Asia/Shanghai 时间。"""
        return datetime.now(tz=TZ_SHANGHAI)

    def _emit(self, event_type: str, item_id: str | None, payload: dict | None = None) -> None:
        """emit 一条事件。"""
        event = WorkflowEvent(
            event_type=event_type,
            workflow_id=self.workflow_run.id,
            item_id=item_id,
            payload=payload or {},
            created_at=self._now(),
        )
        self.event_log.append(event)

    def _emit_workflow_created(self) -> None:
        self._emit("WORKFLOW_CREATED", None, {"name": self.workflow_run.name})

    def _emit_work_item_created(self) -> None:
        for item in self.items:
            self._emit("WORK_ITEM_CREATED", item.id, {"title": item.title})

    def _emit_item_started(self, item: WorkItem) -> None:
        self._emit("ITEM_STARTED", item.id, {"title": item.title})

    def _emit_item_completed(self, item: WorkItem) -> None:
        self._emit("ITEM_COMPLETED", item.id, {
            "title": item.title,
            "artifact_path": item.artifact_path,
        })

    def _emit_item_failed(self, item: WorkItem, error: str) -> None:
        self._emit("ITEM_FAILED", item.id, {
            "title": item.title,
            "error": error,
        })

    def _emit_workflow_paused(self) -> None:
        self._emit("WORKFLOW_PAUSED", None, {"name": self.workflow_run.name})

    def _emit_workflow_resumed(self) -> None:
        self._emit("WORKFLOW_RESUMED", None, {"name": self.workflow_run.name})
