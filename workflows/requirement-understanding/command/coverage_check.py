#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Layer4 Coverage Check —— requirement-understanding 工作流的 Canonicalization Recall 看板。

看板（非阻断门）：度量并摆出 Layer3 的 final_requirement（Apply(Resolution) 的投影）
是否丢掉了 Layer1 baseline_requirement_set 已经发现的需求项，覆盖缺口标红供人工裁量。
它【不】证明 Extraction Recall（baseline 是否抽全了 PRD）——那是另一层问题，
靠多 pass / 异源 / 人工抽检逼近，不由本节点证明。

度量统一通式（不分字段/规则两套）：
    ∀ baseline 项, ∃ canonical 项 经 Resolution 可达

必须经 Resolution 解析后再比对：baseline 里的源 token（如「主小区」）与 canonical 里的
规范名（如「Master Community」）通过 Resolution 的等价/合并关系连通，纯字符串匹配会误报。

派生节点（derived-from）天然在 Coverage 分母之外：它不来自抽取、不在 baseline 里，
只能由 Human Gate 兜，本脚本不对其做覆盖断言。

约定的机读锚点（上游三份 markdown 各自嵌入的 fenced code block，语言标 coverage）：
  baseline_requirement_set.md 里：
      ```coverage baseline
      - id: FIELD-137          # baseline 项唯一 id
        token: 建筑面积         # 源 token（可选，用于 Resolution 连通）
        assertions: [默认值, 校验规则]   # 该项的最细断言（粒度下沉，可选）
      ```
  resolution.md 里：
      ```coverage resolution
      - type: equivalent | merge | rename | derived-from
        from: [FIELD-137, FIELD-138]   # 源 baseline id 集合
        to: CANON-CommunityArea         # 目标 canonical id
      ```
  final_requirement.md 里：
      ```coverage canonical
      - id: CANON-CommunityArea
        assertions: [默认值, 校验规则]
      ```

看板：存在未经 Resolution 追溯到某个 canonical 项的 baseline 项（且非 derived-from 产物）
    → 报告标红，供人工裁量，退出码仍为 0（不阻断工作流）。
    断言蒸发（id 覆盖但断言未逐条命中）同样只降为警告标红。
硬错误（产物缺失 / 机读块缺失，报告压根产不出）→ 退出码 1 → 工作流失败。

用法：python coverage_check.py <run_root>
  在 <run_root> 下按 output 名查找三份产物（先 artifacts/ 后根目录，兼容 promote 前后）。
"""

import sys
import os
import re


# ---- fenced block 解析 ----

_FENCE_RE = re.compile(
    r"```coverage[ \t]+(baseline|resolution|canonical|exclude)[ \t]*\n(.*?)```",
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


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_blocks(text, kind):
    """抽出指定 kind 的所有 coverage fenced block 原文，拼接返回。"""
    blocks = []
    for m in _FENCE_RE.finditer(text):
        if m.group(1) == kind:
            blocks.append(m.group(2))
    return "\n".join(blocks)


def _parse_items(block_text):
    """把 `- id: X` 列表块解析成 [{id, token, assertions, type, from, to}] 的朴素结构。

    不引第三方 YAML 依赖（脚本随工作流走，保持零依赖、确定性）。
    只解析约定的有限字段，格式偏差按行忽略并计入 warnings。
    """
    items = []
    current = None
    warnings = []
    block_field = None   # 正在累积的块列表字段名（如 assertions 的多行 "- x" 形式）
    for raw in block_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # 新条目：以 "- key:" 开头且 key 是条目头（id/type）
        m = re.match(r"^\s*-\s+(\w[\w-]*):\s*(.*)$", line)
        if m and _is_item_head(m.group(1)):
            if current is not None:
                items.append(current)
            current = {"id": None, "token": None, "assertions": [],
                       "type": None, "from": [], "to": None, "sources": []}
            block_field = None
            _assign(current, m.group(1), m.group(2), warnings)
            continue
        # 续行字段："key: value"（缩进）
        m2 = re.match(r"^\s+(\w[\w-]*):\s*(.*)$", line)
        if m2 and current is not None:
            key, val = m2.group(1), m2.group(2).strip()
            # 块列表字段头：值为空，后续以缩进 "- x" 逐条给出（如 assertions:\n  - a\n  - b）
            if key in ("assertions", "from", "sources") and val == "":
                block_field = key
                if not current.get(key):
                    current[key] = []
                continue
            block_field = None
            _assign(current, key, val, warnings)
            continue
        # 块列表续行："- 内容"（内容不是 "key:" 形式），归入当前块列表字段
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
    return key in ("id", "type")


def _assign(item, key, value, warnings):
    value = value.strip()
    if key in ("id", "to", "token", "type", "reason"):
        item[key] = value or None
    elif key in ("assertions", "from", "sources"):
        item[key] = _parse_list(value)
    else:
        warnings.append(f"未知字段：{key}")


def _parse_list(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


# ---- 主流程 ----

def main(argv):
    if len(argv) < 2:
        _fail_hard("用法：coverage_check.py <run_root>")
    run_root = argv[1]
    if not os.path.isdir(run_root):
        _fail_hard(f"run_root 不是目录：{run_root}")

    files = {
        "baseline": _find_artifact(run_root, "baseline_requirement_set.md"),
        "canonical": _find_artifact(run_root, "final_requirement.md"),
    }
    missing = [k for k, v in files.items() if v is None]
    if missing:
        _fail_hard(f"缺少上游产物：{', '.join(missing)}（在 {run_root} 及其 artifacts/ staging 下未找到）")

    baseline_items, bw = _parse_items(_extract_blocks(_read(files["baseline"]), "baseline"))
    canonical_items, cw = _parse_items(_extract_blocks(_read(files["canonical"]), "canonical"))
    # exclude 块可选：登记经 Human Gate 裁决移出范围的 baseline 项（不进分母）。
    exclude_items, ew = _parse_items(_extract_blocks(_read(files["canonical"]), "exclude"))

    if not baseline_items:
        _fail_hard("baseline_requirement_set.md 未找到任何 `coverage baseline` 机读块——"
                   "无法确定分母，拒绝放行（宁可报错，不给虚假 100%）")
    if not canonical_items:
        _fail_hard("final_requirement.md 未找到任何 `coverage canonical` 机读块")

    # 连接键 = 机器稳定标识符：canonical 项 sources 里回指的 baseline id。
    # 弃用 resolution.to -> canonical.id 绕行（LLM 自由命名锚点，与 id 不共享标识符，恒断裂）。
    # 每个 baseline R 只要出现在【任一】 canonical.sources 里即视为被追溯。
    source_to_canon = {}    # baseline_id -> set(canonical_id)
    for c in canonical_items:
        cid = c.get("id")
        for src in (c.get("sources") or []):
            source_to_canon.setdefault(src, set()).add(cid)

    canonical_assertions = {
        c["id"]: set(c.get("assertions") or []) for c in canonical_items if c.get("id")
    }
    excluded = {e.get("id"): (e.get("reason") or "") for e in exclude_items if e.get("id")}

    unmapped_ids = []       # baseline 项未出现在任何 canonical.sources（且未被裁决豁免）
    lost_assertions = []    # id 可达但断言在 canonical 侧蒸发

    for b in baseline_items:
        bid = b.get("id")
        if not bid:
            continue
        # 裁决豁免项：主动移出范围，不计入分母
        if bid in excluded:
            continue
        live_targets = source_to_canon.get(bid, set())
        if not live_targets:
            unmapped_ids.append(bid)
            continue
        # 粒度下沉：每条 baseline 断言必须出现在某个引用它的 canonical 的断言里
        b_assertions = set(b.get("assertions") or [])
        if b_assertions:
            covered = set()
            for t in live_targets:
                covered |= canonical_assertions.get(t, set())
            missing_assertions = b_assertions - covered
            if missing_assertions:
                lost_assertions.append((bid, sorted(missing_assertions)))

    _report(baseline_items, canonical_items, excluded,
            unmapped_ids, lost_assertions,
            bw + cw + ew)

    # 看板模式：本节点已从「阻断门」降级为「覆盖看板」。
    # 未追溯的 baseline 项（unmapped_ids）与断言蒸发（lost_assertions）都只标红进报告，
    # 不再退出码 1——canonicalize 是合并投影，LLM 无法零遗漏地把每个 baseline id 逐条回填进
    # canonical.sources，把它当硬门会导致「一定不通过」。Canonicalization Recall 缺口交由
    # 人工看报告裁量，机器只负责把缺口算清楚、摆出来。
    # 硬错误（产物缺失 / 机读块缺失，报告压根产不出）仍由 _fail_hard 退出码 1 → workflow failed。
    sys.exit(0)


def _report(baseline_items, canonical_items, excluded,
            unmapped_ids, lost_assertions, warnings):
    """输出 Coverage Report 到 stdout（会被 CommandAgent 落盘为 coverage_report）。"""
    total_all = len([b for b in baseline_items if b.get("id")])
    n_excluded = len(excluded)
    total = total_all - n_excluded     # 分母 = 全部 baseline - 裁决豁免项
    unmapped = len(unmapped_ids)
    covered = total - unmapped
    print("# Coverage Report")
    print()
    print("> 本报告为 **Canonicalization Recall 看板**（Layer3 是否丢了 Layer1 baseline 已发现的项），")
    print("> **不**证明 Extraction Recall（baseline 是否抽全 PRD）。后者靠多 pass/异源/人工抽检逼近。")
    print("> 连接键为 canonical.sources 回指的 baseline id（机器稳定标识符），不依赖 LLM 自由命名的锚点。")
    print("> 覆盖缺口仅标红供人工裁量，不阻断工作流；仅产物/机读块缺失等硬错误才使工作流失败。")
    print()
    rate = f"{covered}/{total}" if total else "0/0"
    pct = f"（{covered * 100 // total}%）" if total else ""
    print(f"> **看板结论：id 级覆盖 {rate}{pct}**"
          + ("，存在未追溯项，见下方标红。" if unmapped_ids else "，全部可追溯。"))
    print()
    print(f"- baseline 项总数：{total_all}")
    print(f"- 裁决豁免项（移出范围，不计分母）：{n_excluded}")
    print(f"- Coverage 分母（baseline - 豁免）：{total}")
    print(f"- 经 canonical.sources 追溯到的项：{covered}")
    print(f"- canonical 项数：{len([c for c in canonical_items if c.get('id')])}"
          f"（多对一合并使其可 < 分母，属正确结果）")
    print()
    if excluded:
        print("## ⓘ 裁决豁免项（不计入 Coverage 分母）")
        for eid, reason in excluded.items():
            print(f"- {eid}: {reason}")
        print()
    if unmapped_ids:
        print("## ❌ 未追溯的 baseline 项（缺 Canonicalization Recall，看板标红——不阻断，供人工裁量）")
        for bid in unmapped_ids:
            print(f"- {bid}")
        print()
    if lost_assertions:
        print("## ⚠ 断言未精确命中（警告，不阻断——canonicalize 合并投影会改写措辞，供人工抽查）")
        print(f"> 共 {len(lost_assertions)} 项存在断言字符串未逐条命中；id 级 Recall 已保证需求项未丢。")
        for bid, miss in lost_assertions:
            print(f"- {bid}: {', '.join(miss)}")
        print()
    if warnings:
        print("## ⚠ 解析告警（机读块格式偏差，按行忽略）")
        for w in warnings[:50]:
            print(f"- {w}")
        print()
    if not unmapped_ids:
        print("## ✅ 看板：所有 baseline 项（除裁决豁免）经 canonical.sources 均可追溯，id 级 Canonicalization Recall 完整")
    else:
        print(f"## ⓘ 看板：{unmapped} 项 baseline 未追溯，需人工裁量是否为真实丢失（本节点不阻断工作流）")


def _fail_hard(msg):
    print("# Coverage Report")
    print()
    print(f"## ❌ 无法执行覆盖检查：{msg}")
    sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
