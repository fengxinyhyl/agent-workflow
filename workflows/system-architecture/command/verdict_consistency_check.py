#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verdict Consistency Check —— system-architecture 工作流 architecture_freeze 之后的确定性门。

证明的是节点间【无 verdict 漂移】：architecture_freeze 对 evaluation_report 已判定结论的转述，
没有翻转极性、没有漏掉。堵的是"后节点静默改写前节点正确判定"这一类稳定性缺陷
（本次实例：eval 判"10 项校验 CR-24 已定"，freeze 却转述成"降 OQ-01 待定"）。

它【不】判断 eval 或 freeze 谁对——两边措辞都对但都错的语义错误仍要靠评审。
本门只做纯字符串/极性可判定的确定性核对，与 evaluation_gate 的 LLM 判断正交。

机读锚点（两份 markdown 各自嵌入的 fenced code block）：
  evaluation_report.md 里：
      ```eval verdict
      - id: HC-5
        verdict: defined        # 极性槽，取值见下
        claim: 完整性校验 10 项 CR-24 已明确
      ```
  final_architecture.md 里：
      ```freeze restatement
      - ref: HC-5
        verdict: defined        # 必须与 eval 同 id 的 verdict 同极性
        restated: 10 项校验 CR-24 已明确；仅阈值降 OQ-01
      ```

极性槽（同组内视为同极性，跨组即极性翻转）：
  {pass}/{fail}、{defined}/{deferred}、{covered}/{uncovered}、{resolved}/{open}

门（任一触发即退出码 1 → 工作流失败）：
  - 漏回指：某条 eval verdict 的 id 未被任何 freeze restatement 的 ref 命中；
  - 极性翻转：freeze 回指的 verdict 与 eval 同 id 的 verdict 不同极性。
警告（不阻断）：
  - freeze 出现 eval 里不存在的 ref（悬空回指，可能笔误）；
  - verdict 取值不在已知极性槽内（无法判极性，降级为人工核对）。

用法：python verdict_consistency_check.py <run_root>
  在 <run_root> 下按 output 名找两份产物（先 artifacts/ 后根目录后 staging/）。
"""

import sys
import os
import re


# ---- fenced block 解析（对齐 coverage_check 风格）----

_FENCE_RE = re.compile(
    r"```(eval[ \t]+verdict|freeze[ \t]+restatement)[ \t]*\n(.*?)```",
    re.DOTALL,
)

# 极性分组：同组同极性；用于判断 eval 与 freeze 是否发生极性翻转
_POLARITY_GROUPS = [
    {"pass", "fail"},
    {"defined", "deferred"},
    {"covered", "uncovered"},
    {"resolved", "open"},
]

def _find_artifact(run_root, name):
    for path in (
        os.path.join(run_root, "artifacts", name),
        os.path.join(run_root, name),
        os.path.join(run_root, "staging", name),
    ):
        if os.path.isfile(path):
            return path
    # 兼容 version_strategy: increment 的带版本后缀命名（如 evaluation_report-v3.md）：取最高版本
    stem, ext = os.path.splitext(name)
    ver_re = re.compile(r"^" + re.escape(stem) + r"-v(\d+)" + re.escape(ext) + r"$")
    best = None  # (version, path)
    for d in (os.path.join(run_root, "artifacts"), run_root, os.path.join(run_root, "staging")):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            m = ver_re.match(fn)
            if m:
                v = int(m.group(1))
                if best is None or v > best[0]:
                    best = (v, os.path.join(d, fn))
        if best is not None:
            return best[1]
    return None


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_block(text, kind_re):
    """抽出首个匹配 kind 的 fenced block 原文（多个则拼接）。kind_re 为规范化后的组名匹配。"""
    blocks = []
    for m in _FENCE_RE.finditer(text):
        label = re.sub(r"[ \t]+", " ", m.group(1)).strip()
        if label == kind_re:
            blocks.append(m.group(2))
    return "\n".join(blocks)


def _parse_items(block_text):
    """解析 `- id:`/`- ref:` 列表为 [{key_id, verdict, text}]。零依赖，只认约定字段。"""
    items = []
    current = None
    warnings = []
    for raw in block_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        head = re.match(r"^\s*-\s+(id|ref):\s*(.*)$", line)
        if head:
            if current is not None:
                items.append(current)
            current = {"key_id": head.group(2).strip() or None,
                       "verdict": None, "text": ""}
            continue
        field = re.match(r"^\s+(\w+):\s*(.*)$", line)
        if field and current is not None:
            k, v = field.group(1), field.group(2).strip()
            if k == "verdict":
                current["verdict"] = v.lower() or None
            elif k in ("claim", "restated"):
                current["text"] = v
            continue
    if current is not None:
        items.append(current)
    return items, warnings


def _polarity_group(verdict):
    """返回 verdict 所属极性组的下标；未知取值返回 None。"""
    if not verdict:
        return None
    for i, grp in enumerate(_POLARITY_GROUPS):
        if verdict in grp:
            return i
    return None


def main(argv):
    if len(argv) < 2:
        _fail_hard("用法：verdict_consistency_check.py <run_root>")
    run_root = argv[1]
    if not os.path.isdir(run_root):
        _fail_hard(f"run_root 不是目录：{run_root}")

    eval_path = _find_artifact(run_root, "evaluation_report.md")
    freeze_path = _find_artifact(run_root, "final_architecture.md")
    missing = [n for n, p in (("evaluation_report.md", eval_path),
                              ("final_architecture.md", freeze_path)) if p is None]
    if missing:
        _fail_hard(f"缺少上游产物：{', '.join(missing)}（在 {run_root} 及 artifacts/ staging 下未找到）")

    eval_items, ew = _parse_items(_extract_block(_read(eval_path), "eval verdict"))
    freeze_items, fw = _parse_items(_extract_block(_read(freeze_path), "freeze restatement"))

    if not eval_items:
        _fail_hard("evaluation_report.md 未找到 `eval verdict` 机读块——"
                   "无法核对 freeze 转述保真度，拒绝放行（宁可报错，不给虚假一致）")
    if not freeze_items:
        _fail_hard("final_architecture.md 未找到 `freeze restatement` 机读块——"
                   "freeze 未按契约回指 eval 判定，视为漂移风险，阻断")

    # freeze 回指索引：ref -> verdict
    freeze_by_ref = {}
    for f in freeze_items:
        if f["key_id"]:
            freeze_by_ref.setdefault(f["key_id"], f["verdict"])
    eval_ids = {e["key_id"] for e in eval_items if e["key_id"]}

    missing_refs = []       # eval verdict 未被 freeze 回指（漏回指，阻断）
    polarity_flips = []     # 极性翻转（阻断）
    warnings = list(ew) + list(fw)

    for e in eval_items:
        eid, ev = e["key_id"], e["verdict"]
        if not eid:
            continue
        if eid not in freeze_by_ref:
            missing_refs.append(eid)
            continue
        fv = freeze_by_ref[eid]
        eg, fg = _polarity_group(ev), _polarity_group(fv)
        if eg is None or fg is None:
            warnings.append(f"{eid}: verdict 取值不在已知极性槽（eval='{ev}' freeze='{fv}'），无法判极性，请人工核对")
            continue
        # 极性一致 = 归一化取值完全相同。同一对立组内取不同值（defined vs deferred）即翻转；
        # 跨组（如 pass vs deferred）语义不可比，同样按不一致处理。
        if ev != fv:
            polarity_flips.append((eid, ev, fv))

    # 悬空回指：freeze 回指了 eval 里不存在的 id
    dangling = sorted(set(freeze_by_ref) - eval_ids)
    for d in dangling:
        warnings.append(f"freeze 回指了 eval 中不存在的 id：{d}（悬空回指，疑似笔误）")

    _report(eval_items, freeze_items, missing_refs, polarity_flips, warnings)

    if missing_refs or polarity_flips:
        sys.exit(1)
    sys.exit(0)


def _report(eval_items, freeze_items, missing_refs, polarity_flips, warnings):
    print("# Verdict Consistency Report")
    print()
    print("> 本门证明 **节点间无 verdict 漂移**（freeze 忠实转述 eval 判定，无极性翻转/无漏回指）。")
    print("> 不判断 eval/freeze 谁对——语义双错仍靠评审。与 evaluation_gate 的 LLM 判断正交。")
    print()
    print(f"- eval verdict 条目数：{len([e for e in eval_items if e['key_id']])}")
    print(f"- freeze restatement 条目数：{len([f for f in freeze_items if f['key_id']])}")
    print()
    if missing_refs:
        print("## ❌ 漏回指（eval 判定未被 freeze 承接，阻断）")
        for i in missing_refs:
            print(f"- `{i}`")
        print()
    if polarity_flips:
        print("## ❌ 极性翻转（freeze 改写了 eval 的判定极性，阻断）")
        for i, ev, fv in polarity_flips:
            print(f"- `{i}`：eval=`{ev}` → freeze=`{fv}`")
        print()
    if warnings:
        print("## ⚠ 警告（不阻断，供人工核对）")
        for w in warnings[:50]:
            print(f"- {w}")
        print()
    if not missing_refs and not polarity_flips:
        print("## ✅ 通过：每条 eval verdict 均被 freeze 忠实回指，无极性翻转")


def _fail_hard(msg):
    print("# Verdict Consistency Report")
    print()
    print(f"## ❌ 无法执行 verdict 一致性检查：{msg}")
    sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)


