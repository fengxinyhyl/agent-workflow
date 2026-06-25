# decision-collection

把指定 Markdown 文件中的待裁决事项结构化到飞书电子表格，等待人工裁决后回收结果并生成最终裁决包。

本工作流用于需求、架构、数据字段、验收口径等需要人工定夺的事项。它不自动修改正式需求、架构、字段映射或模块拆解文档。

## 流程

```text
goal + project_context
  ↓
collect_inputs
  ↓
extract_decision_items
  ↓
review_items
  ↓ approve
publish_to_lark_sheets
  ↓
human_decision_gate
  ↓ approve + 人工已在电子表格中填写
collect_sheets_results
  ↓
synthesize_decision_package
  ↓
done
```

`human_decision_gate` 是人工暂停点。工作流执行到这里后停止，等待用户在飞书电子表格裁决明细子表中填写裁决结果。

## 明细子表字段

本批次裁决明细子表只允许 5 个字段：

| 裁决项ID | 待裁决问题 | 候选方案 | 最终结论 | 状态 |
|---|---|---|---|---|

不创建这些字段：`来源位置`、`类别`、`裁决人`、`裁决说明`、`截止时间`、`回收校验`。

`状态` 可选值为：`待裁决`、`已裁决`、`暂缓`、`作废`。

## 原始材料链接

原始 `.md` 文件会上传到用户指定的飞书云空间材料目录。链接不写入明细子表字段，而是写入：

- 电子表格中固定的 `裁决批次索引` 子表；
- `human_decision_request` 的底部"原始材料"区；
- `decision_package` 的底部"原始材料"区。

如果后续需要导出 Excel，导出文件应在工作表最后追加"原始材料链接"区，而不是把链接做成每条裁决项的字段。

## 输入要求

启动时的 goal 必须显式给出：

1. 本次要处理的 Markdown 文件路径，必须是当前项目内相对路径。
2. 飞书电子表格 URL 或 token（`/sheets/` 路径形态）。
3. 用于上传原始 Markdown 的飞书云空间文件夹 URL 或 token。

不要让工作流扫描整个 `docs/` 自动发现待裁决项。

示例：

```text
/agent-workflow decision-collection -t data-field-decision `
  处理以下文件的待裁决项：docs/data-model/field-mapping/00-overview.md、docs/runs/260618_system-architecture/artifacts/final_architecture.md。
  电子表格: https://xxx.feishu.cn/sheets/xxxx。
  原始材料上传目录: https://xxx.feishu.cn/drive/folder/xxxx。
```

## 继续与回收

工作流暂停后，人工在电子表格裁决明细子表中填写：

- `最终结论`
- `状态`

填写完成后继续：

```powershell
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli continue `
  -r <run_id> `
  -w workflows\decision-collection\workflow.yaml `
  --approve
```

恢复后，`collect_sheets_results` 会读取电子表格并校验：

- 子表仍然是 5 列；
- `已裁决` 的记录必须有 `最终结论`；
- `待裁决` 记录会阻断完成；
- `暂缓`、`作废` 进入裁决包，但不会伪装为已裁决。

## 主要产物

| Artifact | 来源节点 | 用途 |
|---|---|---|
| `input_inventory` | `collect_inputs` | 输入文件、电子表格、材料目录清单 |
| `decision_items` | `extract_decision_items` | 初稿裁决项表 |
| `reviewed_decision_items` | `review_items` | 审查后的可发布裁决项 |
| `lark_sheets_publish_packet` | `publish_to_lark_sheets` | 电子表格子表链接、批次索引、原始材料链接 |
| `human_decision_request` | `human_decision_gate` | 暂停前的人工填写说明 |
| `decision_results` | `collect_sheets_results` | 回收并校验后的裁决结果 |
| `decision_package` | `synthesize_decision_package` | 最终裁决包 |

## 验证

```powershell
C:/Users/12108/miniconda3/python.exe -m pytest tests/test_decision_collection_workflow.py -q
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli validate-config -w workflows\decision-collection\workflow.yaml
C:/Users/12108/miniconda3/python.exe -m agent_workflow.cli validate-state-machine -w workflows\decision-collection\workflow.yaml
```
