#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Module Coverage Check —— module-breakdown 工作流的确定性门。

证明的是 Module Coverage：module_breakdown_draft 的模块集合承接了
final_requirement 的全部 CR，以及 data_model 的全部表——没有需求项/数据表悬空无模块负责。

与 requirement-understanding 的 coverage_check 同源同构（确定性 command 门，
退出码 0/非 0 → done/fail），但连接键更稳：CR 编号（CR-xx）与表名都是机器稳定标识符，
不存在 LLM 自由命名锚点断裂的问题，故直接用编号/表名做集合包含比对。

两道门：
  门1 CR 覆盖：∀ cr ∈ final_requirement 的 canonical CR 集合，∃ module 的 covers_cr 含 cr
  门2 表覆盖：∀ table ∈ data_model 的表集合，       ∃ module 的 covers_table 含 table

门是通用的、由输入自适应：
  - final_requirement 缺 canonical CR 块 → 无法确定分母，硬失败（宁可报错不给虚假通过）。
  - data_model 不存在或未声明任何表 → 门2 无分母，优雅跳过（只跑门1）。这是脚本按输入
    自适应的通用行为，不针对任何具体项目/版本。

悬空引用（门3）：module 引用了不在分母内的 CR/表 → 阻断（与 unmapped 同级，尽早暴露拼写错误）。

机读锚点约定（各产物内嵌 fenced code block）：
  final_requirement.md 里（已有，复用）：
      ```coverage canonical
      - id: CR-01
        sources: [R-1, R-12]
        assertions: [...]
      ```
  module_breakdown_draft.md 里（新增，由 decompose/refine 产出）：
      ```module-coverage
      - module: M15
        covers_cr: [CR-01, CR-02]
        covers_table: [master_community, mc_status_log]
      ```
  data_model.md 里（可选，用于门2 分母）：
      ```coverage table
      - name: master_community
      - name: mc_status_log
      ```
  排除项（可选，移出分母，登记裁决豁免）——写在 module_breakdown_draft.md 里：
      ```coverage exclude
      - id: CR-33
        reason: OQ-03 术语 rename 项，不进 Coverage 分母
      ```

用法：python mapping_check.py <run_root>
  在 <run_root> 下按产物名查找（先 artifacts/ 后根目录后 staging/，兼容 promote 前后）。
"""

import sys
import os
import re


# ---- fenced block 解析（与 coverage_check.py 同构，零第三方依赖） ----

_FENCE_RE = re.compile(
    r"```(coverage canonical|coverage table|coverage exclude|module-coverage)[ \t]*\n(.*?)```",
    re.DOTALL,
)


def _find_artifact(run_root, name):
    """在 run_root 下定位产物文件，兼容 promote 前（根目录/staging）与后（artifacts/）。"""
    candidates = [
        os.path.join(run_root, "artifacts", name),
        os.path.join(run_root, name),
        os.path.join(run_root, "staging", name),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _parse_frontmatter(filepath):
    """解析 Markdown 文件的 YAML frontmatter（零依赖，仅识别顶层 key: value）。

    与 scripts/collect.py 同款确定性解析，用于按 artifact_id / lineage_id 全局定位
    上游产物原文件——脚本自解析，不依赖文件被复制到 run 目录。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm


def _find_upstream_by_key(runs_dir, artifact_id=None, lineage_id=None, artifact_name=None):
    """全局扫 docs/runs/*/artifacts/*.md，按 artifact_id 或 lineage_id 定位原文件。

    确定性匹配（无 fuzzy）。优先级：
      1. artifact_id 精确命中（最可靠，命令层 --seed 传入）
      2. lineage_id + artifact_name 命中（--lineage 传入）
      3. 纯 artifact_name 兜底（未传键时的手动裸跑；同名多份取路径序末个≈最新 run）
    多命中时按路径排序（run_id 前缀近似时间序）取首个（键匹配）/末个（纯文件名兜底取最新）。
    """
    if not os.path.isdir(runs_dir):
        return None
    import glob as _glob
    id_matches, lineage_matches, name_matches = [], [], []
    for md in sorted(_glob.glob(os.path.join(runs_dir, "*", "artifacts", "*.md"))):
        base_ok = artifact_name is None or os.path.basename(md) == f"{artifact_name}.md"
        fm = _parse_frontmatter(md)
        if artifact_id and fm.get("artifact_id") == artifact_id:
            id_matches.append(md)
        if lineage_id and fm.get("lineage_id") == lineage_id and base_ok:
            lineage_matches.append(md)
        if base_ok:
            name_matches.append(md)
    if id_matches:
        return id_matches[0]
    if lineage_matches:
        return lineage_matches[0]
    if name_matches:
        return name_matches[-1]  # 纯文件名兜底：取最新 run
    return None


def _resolve_upstream(run_root, name, artifact_id=None, lineage_id=None):
    """定位上游产物：先 run_root 本地查找，找不到再按 artifact_id/lineage_id 全局定位原文件。

    保证手动裸跑（未经命令层把文件落到 run 目录）也能自行找到上游文件，不因缺文件 fail。
    """
    local = _find_artifact(run_root, name)
    if local:
        return local
    runs_dir = os.path.dirname(os.path.abspath(run_root))
    artifact_name = name[:-3] if name.endswith(".md") else name
    return _find_upstream_by_key(
        runs_dir, artifact_id=artifact_id, lineage_id=lineage_id, artifact_name=artifact_name
    )


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_blocks(text, kind):
    """抽出指定 kind 的所有 fenced block 原文，拼接返回。"""
    blocks = []
    for m in _FENCE_RE.finditer(text):
        if m.group(1) == kind:
            blocks.append(m.group(2))
    return "\n".join(blocks)


def _parse_items(block_text):
    """把 `- key:` 列表块解析成朴素 dict 结构。零依赖、确定性。

    支持字段：id / module / reason（标量），covers_cr / covers_table / name（列表，
    既支持行内 [a, b] 也支持多行缩进 - x）。条目头为 id / module / name。
    """
    items = []
    current = None
    warnings = []
    block_field = None
    for raw in block_text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^\s*-\s+(\w[\w-]*):\s*(.*)$", line)
        if m and _is_item_head(m.group(1)):
            if current is not None:
                items.append(current)
            current = {"id": None, "module": None, "name": None,
                       "reason": None, "covers_cr": [], "covers_table": []}
            block_field = None
            _assign(current, m.group(1), m.group(2), warnings)
            continue
        m2 = re.match(r"^\s+(\w[\w-]*):\s*(.*)$", line)
        if m2 and current is not None:
            key, val = m2.group(1), m2.group(2).strip()
            if key in ("covers_cr", "covers_table", "assertions") and val == "":
                block_field = key
                current.setdefault(key, [])
                continue
            block_field = None
            _assign(current, key, val, warnings)
            continue
        m3 = re.match(r"^\s*-\s+(.*)$", line)
        if m3 and current is not None and block_field is not None:
            val = m3.group(1).strip()
            if val:
                current[block_field].append(val)
            continue
        warnings.append(f"无法解析行：{line.strip()}")
    if current is not None:
        items.append(current)
    return items, warnings


def _is_item_head(key):
    return key in ("id", "module", "name")


def _assign(item, key, value, warnings):
    value = value.strip()
    if key in ("id", "module", "name", "reason"):
        item[key] = value or None
    elif key in ("covers_cr", "covers_table"):
        item[key] = _parse_list(value)
    elif key in ("sources", "anchor", "assertions"):
        # canonical 块里 requirement-understanding 的字段，本门只需 id，静默忽略
        pass
    else:
        warnings.append(f"未知字段：{key}")


def _parse_list(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def main(argv):
    if len(argv) < 2:
        _fail_hard("用法：mapping_check.py <run_root> [--seed <artifact_id>] [--lineage <lineage_id>]")
    run_root = argv[1]
    if not os.path.isdir(run_root):
        _fail_hard(f"run_root 不是目录：{run_root}")

    # 可选参数：命令层已知 seed artifact_id / lineage_id，传入以精确定位上游原文件。
    # 未传时脚本仍会按 artifact_name 全局扫描回退（见 _resolve_upstream）。
    seed_id = _arg_value(argv, "--seed")
    lineage_id = _arg_value(argv, "--lineage")

    # module_breakdown_draft 是本 run 的产物，只在本地找。
    draft_path = _find_artifact(run_root, "module_breakdown_draft.md")
    # final_requirement / data_model 是上游产物：本地找不到时按 artifact_id/lineage_id
    # 全局定位原文件（脚本自解析，不依赖文件被复制到 run 目录）。
    fr_path = _resolve_upstream(run_root, "final_requirement.md", artifact_id=seed_id)
    dm_path = _resolve_upstream(run_root, "data_model.md", lineage_id=lineage_id)

    if fr_path is None:
        _fail_hard("缺少 final_requirement.md（run 目录及全局按 artifact_id 均未定位到；"
                   "可传 --seed <artifact_id> 精确指定）")
    if draft_path is None:
        _fail_hard(f"缺少 module_breakdown_draft.md（在 {run_root} 及 artifacts/ staging 下未找到）")

    # 分母1：final_requirement 的 canonical CR 集合
    cr_items, cw = _parse_items(_extract_blocks(_read(fr_path), "coverage canonical"))
    cr_all = [c["id"] for c in cr_items if c.get("id")]
    if not cr_all:
        _fail_hard("final_requirement.md 未找到任何 `coverage canonical` 机读块——"
                   "无法确定 CR 分母，拒绝放行（宁可报错，不给虚假通过）")

    # 模块覆盖声明
    draft_text = _read(draft_path)
    mod_items, mw = _parse_items(_extract_blocks(draft_text, "module-coverage"))
    if not mod_items:
        _fail_hard("module_breakdown_draft.md 未找到任何 `module-coverage` 机读块——"
                   "无法验证覆盖，拒绝放行")
    # 排除项（可选）：移出分母
    exc_items, ew = _parse_items(_extract_blocks(draft_text, "coverage exclude"))
    excluded = {e["id"]: (e.get("reason") or "") for e in exc_items if e.get("id")}

    # 分母2：data_model 表集合（可选——不存在则门2 跳过，脚本按输入自适应）
    table_all, table_ready, tw = [], False, []
    if dm_path is not None:
        tbl_items, tw = _parse_items(_extract_blocks(_read(dm_path), "coverage table"))
        table_all = [t["name"] for t in tbl_items if t.get("name")]
        table_ready = bool(table_all)

    # 汇总模块声明的覆盖集合
    covered_cr, covered_table = set(), set()
    for mod in mod_items:
        covered_cr.update(mod.get("covers_cr") or [])
        covered_table.update(mod.get("covers_table") or [])

    cr_denom = [c for c in cr_all if c not in excluded]
    unmapped_cr = [c for c in cr_denom if c not in covered_cr]
    unmapped_table = [t for t in table_all if t not in covered_table] if table_ready else []

    # 门3 悬空引用：模块引用了不在分母内的 CR/表
    cr_set, table_set = set(cr_all), set(table_all)
    dangling_cr = sorted({c for c in covered_cr if c not in cr_set})
    dangling_table = sorted(
        {t for t in covered_table if t not in table_set}) if table_ready else []

    _report(cr_all, cr_denom, excluded, mod_items, table_all, table_ready,
            unmapped_cr, unmapped_table, dangling_cr, dangling_table,
            cw + mw + ew + tw)

    blocked = unmapped_cr or unmapped_table or dangling_cr or dangling_table
    sys.exit(1 if blocked else 0)


def _report(cr_all, cr_denom, excluded, mod_items, table_all, table_ready,
            unmapped_cr, unmapped_table, dangling_cr, dangling_table, warnings):
    """输出 Coverage Report 到 stdout（会被 CommandAgent 落盘为 coverage_report）。"""
    print("# Module Coverage Report")
    print()
    print("> 本门证明 **Module Coverage**：module_breakdown 的模块集合承接了 final_requirement")
    print("> 全部 CR 与 data_model 全部表。连接键为 CR 编号与表名（机器稳定标识符）。")
    print()
    print(f"- CR 总数：{len(cr_all)}")
    print(f"- 裁决豁免 CR（移出分母）：{len(excluded)}")
    print(f"- CR 分母（总数 - 豁免）：{len(cr_denom)}")
    print(f"- 被模块覆盖的 CR：{len(cr_denom) - len(unmapped_cr)}")
    print(f"- 模块数：{len(mod_items)}")
    if table_ready:
        print(f"- data_model 表总数：{len(table_all)}")
        print(f"- 被模块覆盖的表：{len(table_all) - len(unmapped_table)}")
    else:
        print("- data_model 表：未声明 `coverage table` 块 → 门2（表覆盖）跳过")
    print()
    if excluded:
        print("## ⓘ 裁决豁免 CR（不计入分母）")
        for eid, reason in excluded.items():
            print(f"- {eid}: {reason}")
        print()
    if unmapped_cr:
        print("## ❌ 未被任何模块覆盖的 CR（阻断）")
        for cid in unmapped_cr:
            print(f"- {cid}")
        print()
    if unmapped_table:
        print("## ❌ 未被任何模块负责的数据表（阻断）")
        for t in unmapped_table:
            print(f"- {t}")
        print()
    if dangling_cr:
        print("## ❌ 悬空 CR 引用——模块引用了 final_requirement 中不存在的 CR（阻断）")
        for c in dangling_cr:
            print(f"- {c}")
        print()
    if dangling_table:
        print("## ❌ 悬空表引用——模块引用了 data_model 中不存在的表（阻断）")
        for t in dangling_table:
            print(f"- {t}")
        print()
    if warnings:
        print("## ⚠ 解析告警（机读块格式偏差，按行忽略）")
        for w in warnings[:50]:
            print(f"- {w}")
        print()
    if not (unmapped_cr or unmapped_table or dangling_cr or dangling_table):
        scope = "CR + 数据表" if table_ready else "CR（data_model 未就绪，表覆盖跳过）"
        print(f"## ✅ 通过：{scope} 全部被模块覆盖，无悬空引用")


def _arg_value(argv, flag):
    """从 argv 提取 `--flag <value>` 的值，未提供返回 None。"""
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _fail_hard(msg):
    print("# Module Coverage Report")
    print()
    print(f"## ❌ 无法执行覆盖检查：{msg}")
    sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)



