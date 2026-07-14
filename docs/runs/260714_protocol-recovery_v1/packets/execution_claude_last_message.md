# execution claude debug packet

Session ID: 8e93dece-2550-4134-b841-5692188dcec1

## 最后一条 assistant message

## 执行完成

协议恢复能力迭代全部 **7 个步骤**已完成。总结如下：

### 修改清单

| 文件 | 改动 |
|------|------|
| `tasks/result.py` | +RecoveryInfo, ExecutionMetadata +protocol_origin/recovery，手写 to_dict/from_dict |
| `agents/_parse.py` | +`_recover_decision_from_prose`(L1 regex + L2 synonym)，`_parse_task_result_text` 参数扩展 |
| `agents/claude_cli.py` | `_parse_stream_output` 从 skill_policy 透传 allowed_decisions |
| `agents/codex_cli.py` | 3 处调用点透传参数 |
| `observability/events.py` | +EventType.ProtocolRecovery + registry（含 origin_text_hash） |
| `state_machine/runner.py` | +`_emit_protocol_recovery_if_needed`，主循环发事件，Repair 瘦身为格式转换器，repair origin=repair |
| `tests/unit/test_protocol_recovery.py` | **新文件**：23 个恢复算法全分支测试 |
| `tests/unit/test_task_result_v4.py` | +13 个协议轴 round-trip/兼容测试 |
| `tests/unit/test_event_bus.py` | +5 个 ProtocolRecovery registry 校验测试 |

### 测试结果

- **新增测试**：23 个（`test_protocol_recovery.py`）✅
- **扩展测试**：49 个（`test_task_result_v4.py` + `test_event_bus.py`）✅
- **关联回归**：26 个（`test_parser_fallback.py` + `test_repair.py`）✅
- **核心回归**：72 个（`test_state_machine.py` + `test_config_v4.py` + …）✅
- **集成测试**：已有关联失败（workflow YAML 缺失/state 名称不匹配），与本次改动无关

### 验收标准 8 条全部满足

1. ✅ L1 唯一命中恢复，冲突/无命中不恢复
2. ✅ 线性节点不传 allowed_decisions 零污染
3. ✅ 合法 JSON 优先于恢复
4. ✅ Repair 瘦身：产物正文 + 最后消息 + IO 退化不崩
5. ✅ ProtocolRecovery 事件含 origin_text_hash
6. ✅ 老数据 protocol_origin=native, recovery=None
7. ✅ Level 2 默认关闭（Feature Flag）
8. ✅ 单元测试全量无回归

```json
{
  "schema_version": 1,
  "task_id": "execution",
  "state": "execution",
  "status": "success",
  "decision": "done",
  "summary": "协议恢复能力迭代全部 7 个步骤已完成。修改 8 个源文件 + 新增 1 个测试文件，共 +608/-25 行。新增 23 个恢复算法专项测试 + 49 个扩展测试全部通过。单元测试全量无回归。集成测试失败为已有问题，与本次改动无关。验收标准 8 条全部满足。",
  "artifacts": [
    {
      "name": "execution_report",
      "staging_path": "G:\\agent-workflow-wt\\protocol-recovery\\staging\\execution\\execution_report.md",
      "artifact_path": "artifacts/execution_report.md",
      "type": "markdown"
    }
  ],
  "execution": {"started_at": "", "finished_at": "", "exit_code": 0}
}
```

