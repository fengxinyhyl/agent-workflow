#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DDL Conflict Check —— system-architecture 工作流 data_model 节点后的确定性门。

证明的是 data_model 产物的 DDL 与项目【真实数据库基线】不冲突：
  1. 新建表名不与既有物理表重名（CREATE TABLE 撞库）；
  2. 声明占用的 migration 编号未被既有 migration 占用；
  3. 若产物自称"零 ALTER 既有表"（HC-13 类约束），却对既有表出现 ALTER，则暴露矛盾。

它【不】判断 DDL 设计是否合理、字段类型是否恰当——那是 LLM 评估/人工评审的事。
本门只做纯字符串/正则可判定的确定性冲突检测，与 evaluation_gate 的 LLM 判断正交，
用于补上"LLM 假 pass 放过撞库/撞编号"的确定性防线。

真实基线来源（运行时事实，非静态文档井）：
  项目实际 DDL/migration 目录，默认探测 docs/data-model/ 下的 ddl/ 与 migrations/。
  可用环境变量 DDL_BASELINE_DIR 覆盖（冒号或分号分隔多个目录）。

门（任一触发即退出码 1 → 工作流失败）：
  - 表名冲突：data_model 新建表名 ∈ 既有表名集合；
  - 编号冲突：data_model 占用的 migration 编号 ∈ 既有编号集合。
警告（不阻断，输出到报告供人工抽查）：
  - 声称零 ALTER 既有表却检出 ALTER 既有表；
  - 无法定位真实基线目录（降级为"仅自洽检查"）。

用法：python ddl_conflict_check.py <run_root> [<project_root>]
  <run_root>：本次 architecture run 目录，按 output 名找 data_model.md（先 artifacts/ 后根目录）。
  <project_root>：项目根，用于定位真实 DDL 基线；缺省时从 run_root 向上回溯探测。
"""

import sys
import os
import re


# ---- 产物定位（对齐 coverage_check 的 promote 前后兼容策略）----

def _find_artifact(run_root, name):
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

# ---- DDL / migration 解析（零依赖，纯正则）----

# CREATE TABLE [IF NOT EXISTS] [schema.]name —— 捕获裸表名（去 schema 前缀、去引号）
_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:[\"`]?(\w+)[\"`]?\s*\.\s*)?[\"`]?(\w+)[\"`]?",
    re.IGNORECASE,
)

# ALTER TABLE [schema.]name —— 捕获被 ALTER 的表名
_ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:(?:IF\s+EXISTS\s+)?)"
    r"(?:[\"`]?(\w+)[\"`]?\s*\.\s*)?[\"`]?(\w+)[\"`]?",
    re.IGNORECASE,
)

# migration 文件名/引用里的 4 位编号前缀，如 0010_xxx.sql / `0011`
_MIGRATION_NUM_RE = re.compile(r"(?<!\d)(\d{4})(?=[_\.\s`'\"]|$)")


def _table_names(sql_text):
    """从一段 SQL/markdown 文本抽出所有 CREATE TABLE 的裸表名（小写）。"""
    names = set()
    for m in _CREATE_RE.finditer(sql_text):
        names.add(m.group(2).lower())
    return names


def _altered_table_names(sql_text):
    names = set()
    for m in _ALTER_RE.finditer(sql_text):
        names.add(m.group(2).lower())
    return names


def _scan_baseline_dirs(project_root):
    """探测真实 DDL 基线目录，返回存在的目录列表。"""
    env = os.environ.get("DDL_BASELINE_DIR")
    if env:
        sep = ";" if ";" in env else ":"
        dirs = [d for d in env.split(sep) if d.strip()]
        return [d for d in dirs if os.path.isdir(d)]
    if not project_root:
        return []
    guesses = [
        os.path.join(project_root, "docs", "data-model", "ddl"),
        os.path.join(project_root, "docs", "data-model", "migrations"),
    ]
    return [d for d in guesses if os.path.isdir(d)]


def _collect_baseline(dirs):
    """扫描基线目录下所有 .sql，返回 (既有表名集合, 既有 migration 编号集合)。"""
    existing_tables = set()
    existing_migrations = set()
    for d in dirs:
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".sql"):
                continue
            path = os.path.join(d, fn)
            # 文件名前缀编号（migrations/ 下才是真正的编号占用）
            if os.path.basename(d).lower() == "migrations":
                m = _MIGRATION_NUM_RE.match(fn)
                if m:
                    existing_migrations.add(m.group(1))
            try:
                existing_tables |= _table_names(_read(path))
            except OSError:
                continue
    return existing_tables, existing_migrations


def _guess_project_root(run_root):
    """从 run_root 向上回溯，找到含 docs/data-model 的项目根。"""
    cur = os.path.abspath(run_root)
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, "docs", "data-model")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


# ---- 主流程 ----

def main(argv):
    if len(argv) < 2:
        _fail_hard("用法：ddl_conflict_check.py <run_root> [<project_root>]")
    run_root = argv[1]
    if not os.path.isdir(run_root):
        _fail_hard(f"run_root 不是目录：{run_root}")
    project_root = argv[2] if len(argv) > 2 else _guess_project_root(run_root)

    dm_path = _find_artifact(run_root, "data_model.md")
    if dm_path is None:
        _fail_hard(f"缺少上游产物 data_model.md（在 {run_root} 及其 artifacts/ staging 下未找到）")
    dm_text = _read(dm_path)

    new_tables = _table_names(dm_text)
    altered_tables = _altered_table_names(dm_text)
    declared_migrations = set(_MIGRATION_NUM_RE.findall(dm_text))

    baseline_dirs = _scan_baseline_dirs(project_root)
    existing_tables, existing_migrations = _collect_baseline(baseline_dirs)

    warnings = []
    if not baseline_dirs:
        warnings.append(
            "未定位到真实 DDL 基线目录（docs/data-model/ddl|migrations 或 DDL_BASELINE_DIR）——"
            "降级为仅自洽检查，撞库/撞编号无法确定性排除，请人工核对。")

    # 门 1：表名冲突（新建表名 ∈ 既有表名）
    table_conflicts = sorted(new_tables & existing_tables)

    # 门 2：migration 编号冲突（data_model 声明占用 ∈ 既有编号）
    #   仅当能读到既有编号时才判定；声明编号取"新建 migration"语境，这里用交集近似，
    #   既有编号本就该被 data_model 引用（如"沿用 0009"），故只对 >= 既有最大编号+1 的声明才算"占用意图"。
    migration_conflicts = []
    if existing_migrations:
        max_existing = max(int(n) for n in existing_migrations)
        for n in sorted(declared_migrations):
            # data_model 打算【新建】的编号（严格大于既有最大编号才是新建意图）落进既有集合 = 冲突
            if n in existing_migrations and int(n) <= max_existing:
                # 引用既有编号（如依赖 0009）是正常的，不算冲突；
                # 真正冲突 = 产物把某既有编号当作"新建"用。用启发式：产物文本里该编号旁出现 create/新建/建表。
                if _looks_like_new_use(dm_text, n):
                    migration_conflicts.append(n)

    # 警告：声称零 ALTER 既有表却 ALTER 了既有表
    altered_existing = sorted(altered_tables & existing_tables)
    # 检测"零 ALTER 既有表"类断言，排除针对治理表/外表的断言（非业务既有表）
    # 只在明确声称"零/不 ALTER 既有/现有/业务表"时触发，单纯引用 HC-13 编号不算
    claims_zero_alter = bool(re.search(
        r"(零|不|无)\s*ALTER\s*(既有|现有|业务)\s*(表|主表|对象表)",
        dm_text, re.IGNORECASE))
    if claims_zero_alter and altered_existing:
        warnings.append(
            f"产物声称零 ALTER 既有表（HC-13 类约束），但检出对既有表的 ALTER：{', '.join(altered_existing)}。"
            "若属循环 FK 后置声明等治理新表自身的 ALTER 可忽略，否则与约束矛盾，请核对。")

    _report(dm_path, baseline_dirs, new_tables, existing_tables,
            declared_migrations, existing_migrations,
            table_conflicts, migration_conflicts, warnings)

    if table_conflicts or migration_conflicts:
        sys.exit(1)
    sys.exit(0)


def _looks_like_new_use(text, num):
    """启发式：编号 num 附近是否出现"新建/建表/create"语境，用于区分"引用既有编号"与"当新编号用"。

    两类误报排除（避免把正当的"依赖既有 migration"当成撞号）：
      1. 引用上下文：窗口内出现"依赖/引用/基于/沿用/depends/see/ref"等引用词，
         说明该编号是被引用的既有 migration，非本次新建。
      2. 文件名前缀：编号后紧跟 `_`（如 `0002_create_object_tables.sql`）时，其后的
         `create` 属既有 migration 文件名的一部分，不是"在本编号里建表"的信号——
         判断新建语境时须剔除该文件名 token 再看。
    """
    _REF_WORDS = ("依赖", "引用", "基于", "沿用", "见 ", "参见", "depend", "see ", "ref", "requires")
    for m in re.finditer(re.escape(num), text):
        seg = text[max(0, m.start() - 40): m.end() + 40].lower()
        # 排除 1：引用上下文
        if any(w in seg for w in _REF_WORDS):
            continue
        # 排除 2：剔除"<num>_xxx.sql"文件名 token（含其中的 create 等词）后再判新建语境
        seg_wo_filename = re.sub(re.escape(num) + r"_[\w./-]*\.sql", " ", seg)
        if any(k in seg_wo_filename for k in ("新建", "建表", "create table", "create_", "一次性建")):
            return True
    return False


def _report(dm_path, baseline_dirs, new_tables, existing_tables,
            declared_migrations, existing_migrations,
            table_conflicts, migration_conflicts, warnings):
    print("# DDL Conflict Report")
    print()
    print("> 本门做 **确定性冲突检测**（表名撞库 / migration 编号撞号），与 evaluation_gate 的 LLM 判断正交。")
    print("> 不判断 DDL 设计合理性——那是评估/人工评审的事。")
    print()
    print(f"- data_model 产物：`{dm_path}`")
    print(f"- 真实基线目录：{', '.join(baseline_dirs) if baseline_dirs else '（未定位，降级自洽检查）'}")
    print(f"- data_model 新建表数：{len(new_tables)}")
    print(f"- 既有物理表数：{len(existing_tables)}")
    print(f"- data_model 涉及的 migration 编号：{', '.join(sorted(declared_migrations)) or '（无）'}")
    print(f"- 既有 migration 编号：{', '.join(sorted(existing_migrations)) or '（未读到）'}")
    print()
    if table_conflicts:
        print("## ❌ 表名冲突（新建表名与既有物理表重名，阻断）")
        for t in table_conflicts:
            print(f"- `{t}`")
        print()
    if migration_conflicts:
        print("## ❌ migration 编号冲突（把既有编号当新建用，阻断）")
        for n in migration_conflicts:
            print(f"- `{n}`")
        print()
    if warnings:
        print("## ⚠ 警告（不阻断，供人工核对）")
        for w in warnings:
            print(f"- {w}")
        print()
    if not table_conflicts and not migration_conflicts:
        print("## ✅ 通过：无表名撞库、无 migration 编号撞号")


def _fail_hard(msg):
    print("# DDL Conflict Report")
    print()
    print(f"## ❌ 无法执行 DDL 冲突检查：{msg}")
    sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)


