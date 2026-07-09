#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mapping_check.py 单测。零依赖，直接 subprocess 跑脚本，断言退出码与报告。

覆盖：
  - pass：所有 CR 被模块覆盖，无 data_model → 门2 跳过
  - fail：存在未被任何模块覆盖的 CR（门1）
  - 表覆盖 pass/fail：有 data_model coverage table 块时门2 生效
  - 悬空引用：模块引用不存在的 CR/表 → 阻断（门3）
  - exclude 豁免：裁决移出分母的 CR 不计入
  - 多 CR 单模块 / 单 CR 多模块：集合包含，均 pass
  - 缺机读块：拒绝放行
"""

import os
import sys
import subprocess
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "mapping_check.py")
PYTHON = sys.executable


def _run_case(final_req, draft, data_model=None):
    """临时 run_root 下写产物到 artifacts/，跑脚本，返回 (exit_code, stdout)。"""
    run_root = tempfile.mkdtemp(prefix="modcov_")
    try:
        art = os.path.join(run_root, "artifacts")
        os.makedirs(art)
        _write(os.path.join(art, "final_requirement.md"), final_req)
        _write(os.path.join(art, "module_breakdown_draft.md"), draft)
        if data_model is not None:
            _write(os.path.join(art, "data_model.md"), data_model)
        proc = subprocess.run(
            [PYTHON, SCRIPT, run_root],
            capture_output=True, text=True, encoding="utf-8",
        )
        return proc.returncode, proc.stdout
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _final(*items):
    return "# final\n\n```coverage canonical\n" + "\n".join(items) + "\n```\n"


def _draft(*items):
    return "# draft\n\n```module-coverage\n" + "\n".join(items) + "\n```\n"


def _draft_with_exclude(mod_items, exc_items):
    return ("# draft\n\n```module-coverage\n" + "\n".join(mod_items) + "\n```\n\n"
            "```coverage exclude\n" + "\n".join(exc_items) + "\n```\n")


def _data_model(*items):
    return "# dm\n\n```coverage table\n" + "\n".join(items) + "\n```\n"


def test_pass_all_cr_covered_no_dm():
    # 所有 CR 被模块覆盖，无 data_model → 门2 跳过 → pass
    code, out = _run_case(
        _final("- id: CR-01", "  sources: [R-1]", "- id: CR-02", "  sources: [R-2]"),
        _draft("- module: M15", "  covers_cr: [CR-01, CR-02]"),
    )
    assert code == 0, out
    assert "✅" in out
    assert "门2（表覆盖）跳过" in out


def test_fail_unmapped_cr():
    # CR-02 无模块覆盖 → 门1 fail
    code, out = _run_case(
        _final("- id: CR-01", "- id: CR-02"),
        _draft("- module: M15", "  covers_cr: [CR-01]"),
    )
    assert code == 1, out
    assert "CR-02" in out
    assert "未被任何模块覆盖" in out


def test_multi_cr_single_module():
    code, out = _run_case(
        _final("- id: CR-01", "- id: CR-02", "- id: CR-03"),
        _draft("- module: M15", "  covers_cr: [CR-01, CR-02, CR-03]"),
    )
    assert code == 0, out


def test_single_cr_multi_module():
    code, out = _run_case(
        _final("- id: CR-01"),
        _draft("- module: M15", "  covers_cr: [CR-01]",
               "- module: M16", "  covers_cr: [CR-01]"),
    )
    assert code == 0, out


def test_table_coverage_pass():
    code, out = _run_case(
        _final("- id: CR-01"),
        _draft("- module: M15", "  covers_cr: [CR-01]",
               "  covers_table: [master_community, mc_status_log]"),
        _data_model("- name: master_community", "- name: mc_status_log"),
    )
    assert code == 0, out
    assert "data_model 表总数：2" in out


def test_table_coverage_fail():
    # mc_status_log 无模块负责 → 门2 fail
    code, out = _run_case(
        _final("- id: CR-01"),
        _draft("- module: M15", "  covers_cr: [CR-01]", "  covers_table: [master_community]"),
        _data_model("- name: master_community", "- name: mc_status_log"),
    )
    assert code == 1, out
    assert "mc_status_log" in out
    assert "未被任何模块负责" in out


def test_dangling_cr_reference():
    # 模块引用了 final_requirement 中不存在的 CR-99 → 阻断
    code, out = _run_case(
        _final("- id: CR-01"),
        _draft("- module: M15", "  covers_cr: [CR-01, CR-99]"),
    )
    assert code == 1, out
    assert "CR-99" in out
    assert "悬空 CR 引用" in out


def test_dangling_table_reference():
    code, out = _run_case(
        _final("- id: CR-01"),
        _draft("- module: M15", "  covers_cr: [CR-01]", "  covers_table: [ghost_table]"),
        _data_model("- name: master_community"),
    )
    assert code == 1, out
    assert "ghost_table" in out
    assert "悬空表引用" in out
    # master_community 无模块负责也应同时报门2
    assert "master_community" in out


def test_exclude_removes_from_denominator():
    # CR-33 被裁决豁免 → 不计入分母 → pass
    code, out = _run_case(
        _final("- id: CR-01", "- id: CR-33"),
        _draft_with_exclude(
            ["- module: M15", "  covers_cr: [CR-01]"],
            ["- id: CR-33", "  reason: OQ-03 术语 rename 项"],
        ),
    )
    assert code == 0, out
    assert "裁决豁免 CR（移出分母）：1" in out


def test_missing_canonical_block_fails_hard():
    code, out = _run_case(
        "# final\n\n没有机读块\n",
        _draft("- module: M15", "  covers_cr: [CR-01]"),
    )
    assert code == 1, out
    assert "无法确定 CR 分母" in out


def test_missing_module_coverage_block_fails_hard():
    code, out = _run_case(
        _final("- id: CR-01"),
        "# draft\n\n没有 module-coverage 块\n",
    )
    assert code == 1, out
    assert "无法验证覆盖" in out


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

