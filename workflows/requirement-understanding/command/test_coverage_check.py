#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""coverage_check.py 单测。零依赖，直接 subprocess 跑脚本，断言退出码与报告。

契约（v3 看板）：连接键为 canonical.sources 回指的 baseline id（机器稳定标识符），
不依赖 LLM 自由命名的锚点。覆盖缺口只标红进报告、不阻断（exit 0）；仅硬错误 exit 1。覆盖：
  - pass：每个 baseline 项都被某 canonical 的 sources 引用（exit 0，报告 ✅）
  - 未追溯：存在未被任何 canonical.sources 引用的 baseline 项 → 看板标红但 exit 0
  - 多对一：两条 baseline 合并到一个 canonical（其 sources 含两者），应 pass
  - 断言未命中：降为警告，不阻断（canonicalize 合并投影会改写措辞）
  - exclude 豁免块：裁决移出范围的 baseline 项不计入分母
  - 缺机读块：硬错误，拒绝放行 exit 1（不给虚假 100%）
"""

import os
import sys
import subprocess
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "coverage_check.py")
PYTHON = sys.executable


def _run_case(baseline, canonical):
    """在临时 run_root 下写 baseline 与 canonical 到 artifacts/，跑脚本，返回 (exit_code, stdout)。

    resolution.md 不再是必需产物（脚本已弃用 resolution.to→id 绕行），故不写入。
    """
    run_root = tempfile.mkdtemp(prefix="cov_")
    try:
        art = os.path.join(run_root, "artifacts")
        os.makedirs(art)
        _write(os.path.join(art, "baseline_requirement_set.md"), baseline)
        _write(os.path.join(art, "final_requirement.md"), canonical)
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


def _baseline(*items):
    return "# baseline\n\n```coverage baseline\n" + "\n".join(items) + "\n```\n"


def _canonical(*items):
    return "# final\n\n```coverage canonical\n" + "\n".join(items) + "\n```\n"


def _canonical_with_exclude(canonical_items, exclude_items):
    return ("# final\n\n```coverage canonical\n" + "\n".join(canonical_items) + "\n```\n\n"
            "```coverage exclude\n" + "\n".join(exclude_items) + "\n```\n")

def test_pass_sources_cover_all():
    # 每个 baseline 项都被某 canonical 的 sources 引用 → pass
    code, out = _run_case(
        _baseline("- id: R-1", "- id: R-2"),
        _canonical("- id: CR-01", "  sources: [R-1, R-2]"),
    )
    assert code == 0, out
    assert "✅" in out


def test_unmapped_is_dashboard_not_fail():
    # R-2 未被任何 canonical.sources 引用 → 看板标红但不阻断（exit 0）
    code, out = _run_case(
        _baseline("- id: R-1", "- id: R-2"),
        _canonical("- id: CR-01", "  sources: [R-1]"),
    )
    assert code == 0, out
    assert "R-2" in out
    assert "未追溯" in out


def test_many_to_one_ok():
    # 两条 baseline 合并到一个 canonical（sources 含两者）→ pass
    code, out = _run_case(
        _baseline("- id: BR-a", "- id: BR-b"),
        _canonical("- id: CR-17", "  sources: [BR-a, BR-b]"),
    )
    assert code == 0, out


def test_split_one_to_many_ok():
    # 一个 baseline 项被多个 canonical 引用（拆分）→ pass
    code, out = _run_case(
        _baseline("- id: R-1"),
        _canonical("- id: CR-01", "  sources: [R-1]", "- id: CR-02", "  sources: [R-1]"),
    )
    assert code == 0, out


def test_id_equality_still_needs_sources():
    # 旧契约的"baseline id == canonical id 直通"已废除：不写 sources 则不算覆盖 →
    # 看板标红，但看板化后不阻断（exit 0）
    code, out = _run_case(
        _baseline("- id: R-1"),
        _canonical("- id: R-1"),
    )
    assert code == 0, out
    assert "R-1" in out
    assert "未追溯" in out


def test_assertion_mismatch_is_warning_not_fail():
    # 断言未精确命中降为警告，不阻断（id 已被 sources 覆盖）
    code, out = _run_case(
        _baseline("- id: R-1", "  assertions: [默认值, 校验规则]"),
        _canonical("- id: CR-01", "  sources: [R-1]", "  assertions: [默认值]"),
    )
    assert code == 0, out
    assert "校验规则" in out
    assert "警告" in out


def test_block_list_assertions_parsed():
    # 多行块列表格式的 assertions 应被正确解析（回归 canonicalize 实际产物格式）
    code, out = _run_case(
        _baseline("- id: R-1", "  assertions: [甲, 乙]"),
        _canonical("- id: CR-01", "  sources: [R-1]", "  assertions:", "    - 甲", "    - 乙"),
    )
    assert code == 0, out
    # 断言全部命中，不应出现断言警告段
    assert "断言未精确命中" not in out


def test_exclude_removes_from_denominator():
    # exclude 块登记的 baseline 项被裁决移出范围，不计入分母；不写 sources 也 pass
    code, out = _run_case(
        _baseline("- id: R-1", "- id: R-100"),
        _canonical_with_exclude(
            ["- id: CR-01", "  sources: [R-1]"],
            ["- id: R-100", "  reason: BQ-06 裁决移出范围"],
        ),
    )
    assert code == 0, out
    assert "豁免" in out
    assert "R-100" in out


def test_missing_baseline_block_refuses():
    code, out = _run_case(
        "# baseline 无机读块\n",
        _canonical("- id: CR-01", "  sources: [R-1]"),
    )
    assert code == 1, out
    assert "无法确定分母" in out or "机读块" in out


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _main()

