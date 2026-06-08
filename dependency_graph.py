"""DependencyGraph — 依赖校验与就绪/阻塞计算。

支撑模块（非核心对象），全部为静态方法，无内部状态。
依赖关系完全由 WorkItem.depends_on 表达。

MVP 只支持 depends_on: list[str]。
"""

from __future__ import annotations

from work_item import WorkItem, ItemStatus


class DependencyGraph:
    """依赖图校验与就绪计算。

    全部静态方法，无内部状态。

    校验检查：
    - 依赖项必须存在
    - 禁止自依赖
    - 禁止环（DFS 拓扑排序检测）

    就绪规则：
        item.status == PENDING
        and all(dep.status == COMPLETED for dep in item.depends_on)
    """

    @staticmethod
    def validate(items: list[WorkItem]) -> list[str]:
        """校验依赖完整性，返回错误列表。

        检查项：
        1. 依赖的 item id 必须存在于 items 中
        2. 禁止自依赖（item.depends_on 包含自身 id）
        3. 禁止环（DFS 拓扑排序检测）

        Args:
            items: 所有 WorkItem 列表

        Returns:
            错误消息列表，空列表表示校验通过。
        """
        errors: list[str] = []
        item_ids = {item.id for item in items}

        for item in items:
            # 1. 检查依赖项存在
            for dep_id in item.depends_on:
                if dep_id not in item_ids:
                    errors.append(
                        f"Item '{item.id}' 依赖的 '{dep_id}' 不存在"
                    )

            # 2. 禁止自依赖
            if item.id in item.depends_on:
                errors.append(
                    f"Item '{item.id}' 不允许依赖自身"
                )

        # 3. 环检测（基于 DFS 的拓扑排序）
        #    如果存在环，将不可排序的节点全部报告
        if not errors:  # 只在前面检查通过后才做环检测
            cycle_errors = DependencyGraph._detect_cycles(items)
            errors.extend(cycle_errors)

        return errors

    @staticmethod
    def _detect_cycles(items: list[WorkItem]) -> list[str]:
        """DFS 环检测，返回环相关错误。

        Returns:
            错误消息列表
        """
        errors: list[str] = []
        item_map = {item.id: item for item in items}

        WHITE = 0  # 未访问
        GRAY = 1   # 正在访问（在当前 DFS 路径上）
        BLACK = 2  # 已完成

        color: dict[str, int] = {item.id: WHITE for item in items}

        def dfs(node_id: str, path: list[str]) -> None:
            color[node_id] = GRAY
            path.append(node_id)

            item = item_map.get(node_id)
            if item:
                for dep_id in item.depends_on:
                    dep_color = color.get(dep_id, BLACK)
                    if dep_color == GRAY:
                        # 找到环
                        cycle_start = path.index(dep_id)
                        cycle_nodes = path[cycle_start:] + [dep_id]
                        errors.append(
                            f"检测到依赖环: {' -> '.join(cycle_nodes)}"
                        )
                    elif dep_color == WHITE:
                        dfs(dep_id, path)

            path.pop()
            color[node_id] = BLACK

        for node_id in list(item_map.keys()):
            if color[node_id] == WHITE:
                dfs(node_id, [])

        # 去重（同一个环可能被多次检测到）
        seen = set()
        unique_errors = []
        for err in errors:
            if err not in seen:
                seen.add(err)
                unique_errors.append(err)

        return unique_errors

    @staticmethod
    def ready_items(items: list[WorkItem]) -> list[WorkItem]:
        """返回所有依赖已满足的 PENDING item。

        就绪规则：
            item.status == PENDING
            and all(dep 对于 id == dep_id 的 item, dep.status == COMPLETED)

        注意：FIFO 选择由 QueueRunner 负责（取 ready_items[0]），
        DependencyGraph 只负责就绪判定。

        Args:
            items: 所有 WorkItem 列表

        Returns:
            就绪的 WorkItem 列表（保持传入顺序）
        """
        item_map = {item.id: item for item in items}

        ready: list[WorkItem] = []
        for item in items:
            if item.status != ItemStatus.PENDING:
                continue
            # 检查所有依赖是否已完成
            all_deps_completed = True
            for dep_id in item.depends_on:
                dep = item_map.get(dep_id)
                if dep is None or dep.status != ItemStatus.COMPLETED:
                    all_deps_completed = False
                    break
            if all_deps_completed:
                ready.append(item)

        return ready

    @staticmethod
    def blocked_items(items: list[WorkItem]) -> list[WorkItem]:
        """返回所有被阻塞的 PENDING item（至少一个依赖未 COMPLETED）。

        注意：如果某个依赖 FAILED，该 item 仍然返回为 blocked（不会被 ready_items 选中），
        调度器需自行决定是否将下游标记为 SKIPPED。

        Args:
            items: 所有 WorkItem 列表

        Returns:
            被阻塞的 WorkItem 列表（保持传入顺序）
        """
        ready_set = {item.id for item in DependencyGraph.ready_items(items)}

        blocked: list[WorkItem] = []
        for item in items:
            if item.status == ItemStatus.PENDING and item.id not in ready_set:
                blocked.append(item)

        return blocked
