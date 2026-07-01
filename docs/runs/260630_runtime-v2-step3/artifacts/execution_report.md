# 执行报告：Runtime v2 第 3 步 — Validator 纯函数化 + Runner Repair 闸口

## 概述

按 `plan_doc` 与 `plan_refinement_doc` 实施，将 Runner 中"校验失败 → 强制 fail"的一次否决逻辑替换为**三态校验 + 有界自愈**机制。

**执行结果：done** — 全部计划变更完成，核心测试通过。

## 实际修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/agent_workflow/validators/validation_result.py` | **新增** | `ValidResult` 三态数据类 + `RouteShape` NamedTuple |
| `src/agent_workflow/validators/__init__.py` | 修改 | 导出 `ValidResult`、`RouteShape`（与旧 `ValidationResult` 共存） |
| `src/agent_workflow/validators/task_result.py` | 修改 | 新增纯函数 `validate(data, route_shape) → ValidResult`；`TaskResultValidator` 类保留向后兼容（委托给纯函数 + 字段映射 `valid → passed`） |
| `src/agent_workflow/state_machine/runner.py` | 修改 | 4 处变更见下表 |
| `tests/unit/test_validation_result.py` | **新增** | 18 个测试：ValidResult 三态构造、RouteShape 不可变性、validate() 纯函数端到端 |
| `tests/unit/test_repair.py` | **新增** | 13 个测试：Repair 决策判定、Runner 级 Repair 编排、向后兼容 |
| `tests/unit/test_negative.py` | 修改 | 适配 2 个测试（语义升级：invalid status/decision 从 warning 变为 error） |

### runner.py 具体变更点

| # | 位置 | 变更 |
|---|---|---|
| 1 | `_execute_state()` ~L944 | 新增 `self._last_agent_input = agent_input` 保存 AgentInput 供 Repair 使用 |
| 2 | `run()` L383-452 | 替换 `has_blocking` 二分逻辑为三态分支：`valid → promote` / `repairable → Repair` / `not repairable → failed` |
| 3 | `_validate_task_result()` L691-837 | 从 `(bool, list)` 改为返回 `ValidResult`；步骤 1 用纯函数 `validate()`，步骤 2-5 保留原有文件系统校验 |
| 4 | 新增方法 L840-940 | `_call_agent_direct()`、`_build_repair_agent_input()`、`_repair_task_result()` |

## 与计划的偏差

| # | 偏差项 | 说明 | 决策 |
|---|---|---|---|
| 1 | `_build_repair_agent_input` 传 `instruction=` | `AgentInput` 无 `instruction` 字段，需通过 `TaskConfig.instruction` 传递 Repair prompt | 已修正：构造新的 `TaskConfig` 传入 |
| 2 | `test_negative.py` 两测试需更新 | 纯函数化后 `invalid status` 从 warning 升级为 blocking error，旧测试断言 `passed=True` 不再成立 | 已修正测试（从 `test_invalid_status_warning` → `test_invalid_status_error`，`test_decision_not_in_allowed_decisions_warning` → `test_decision_not_in_allowed_decisions_error`） |
| 3 | `test_repair.py` Repair 耗尽测试需 monkeypatch | MockAgent 始终返回合法 decision，无法自然触发 Repair 耗尽；改用 `types.MethodType` 替换 `_call_agent_direct` 模拟持续返回 `invalid_output` | 已适配 |
| 4 | Windows 文件锁定 | `JSONLSink` 持有 `events.jsonl` 文件句柄，`tempfile.TemporaryDirectory` 清理时 `PermissionError` | 测试中手动 `close()` sink + `shutil.rmtree(ignore_errors=True)` |

## 未完成事项

无。所有 plan_refinement_doc 标记为"本次要做"的项目已完成。

标记为 nice-to-have 的延后项：
- `has_next + decision 非空` 的 warning — 首版不实现

## 测试结果

### 测试通过情况

| 测试套件 | 用例数 | 通过 | 失败 | 备注 |
|---|---|---|---|---|
| test_validation_result.py | 18 | 18 | 0 | 全部新增 |
| test_repair.py | 13 | 13 | 0 | 全部新增 |
| test_task_result_v4.py | 22 | 22 | 0 | 不变 |
| test_state_machine.py | 42 | 42 | 0 | 不变 |
| test_negative.py | 16 | 15 | 1 | 1 个预存路径 typo（`doc/runs/` vs `docs/runs/`） |
| test_loop_unroll.py | 8 | 8 | 0 | 不变 |
| 其他（含集成测试等） | 209 | 191 | 4 | 4 个预存文件缺失（`agents.yaml`） |
| **总计** | **328** | **309** | **5** | 5 个失败全部为预存问题 |

### 关键验证点

- ✅ 存量 YAML 配置无需修改（`next`/`on`/`default` 格式已在第 2 步归一）
- ✅ 路由模型不变（`resolve_transition` 在第 2 步已完成，本步不碰）
- ✅ `_unroll_loops` 不变（第 4 步）
- ✅ Agent Parser fallback 逻辑不变（第 1 步已完成）
- ✅ `TaskResultValidator` 向后兼容（`validate()` 返回 `base.ValidationResult(passed, errors, warnings)`）
- ✅ Repair 与 `guards.max_retries` 结构性隔离（`_call_agent_direct` 绕过 `_execute_state` → 不触发 `record_state_visit`）

## 关键执行决策

1. **`AgentInput` 无 `instruction` 参数**：修正是将 Repair prompt 写入 `TaskConfig.instruction`，构造新的 `TaskConfig` 传入 `AgentInput`。
2. **MockAgent 总是返回合法决策**：Repair 耗尽测试改用 `types.MethodType` monkeypatch `_call_agent_direct` 模拟持续返回 `invalid_output`。
3. **`_last_validation_result` 类型变化**：从临时 `type('_VR',...)()` 改为 `ValidResult` 实例，`errors`/`warnings` 字段保持兼容。
4. **Artifact 校验逻辑保留原位**：5 步全在 `_validate_task_result` 内完成，未提取独立方法（避免过度工程化）。
