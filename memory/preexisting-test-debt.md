---
name: preexisting-test-debt
description: main 分支上预先存在的测试失败（Windows 平台），不是新改动引入的回归
metadata:
  type: project
---

在干净的 main 分支上，`tests/unit/` 跑全量时有一批预先存在的失败，与任何新功能改动无关：

- `tests/unit/test_schema_contract.py` — 约 27 failed, 1 passed（valid/invalid fixture 校验，fixture 共 25 个）
- `tests/unit/test_negative.py::TestCancelFile::test_cancel_run_writes_file` — 1 failed，错误涉及 `ntpath` / `os.path`，是 Windows 平台相关

**Why:** 验证新改动是否引入回归时，若不知道这些本就红，会误判成自己改坏了，浪费排查时间。已于 2026-06-27 在 clean main 上逐一复现确认。

**How to apply:** 跑测试评估回归时，基线是「全量 ~262 passed」附近；遇到 schema_contract 或 test_cancel_run_writes_file 失败先排除这些已知项，用 `--ignore=tests/unit/test_schema_contract.py` 或单跑对比 clean main，再判断真实回归。相关：[[spec-dev-review-guard-loop]]
