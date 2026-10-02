# -*- coding: utf-8 -*-
"""repair_evidence_quotes.py — 修复引文形态并隔离不可用证据（候选库专用）。

只对**候选库**执行，稳定 `cninfo.db` 不动。

## 它解决的两个问题

### 1. `source_quote` 不是页面上连续的原文

库里 `source_quote` 是表格行拼接（`营业收入 | 185,101,612.27 | ...`），
而 PDF 文本层把单元格抽成了多行。后端 `db.page_from_markers()` 用的是
「忽略空白后连续子串」这一严格口径，因此 503 条里 500 条会被判"找不到"。

修复方式：在标注页里取**覆盖全部内容片段的最小区间** —— 那一段就是页面原文
本身，必然能通过连续子串核验，而且仍然包含原来引用的每个标签和数字。
**不改任何数字、不改页码、不做任何推测**：新引文是从页面原文里原样截取的。

### 2. 无法形成精确短摘录的记录需要显式隔离

除 3 条已知字段缺陷外，如果覆盖全部目标片段的最短连续区间仍会卷入原引文
没有的数字，或超过 200 字符，也不能支撑精确回答。脚本不臆造、不截断，统一
写 `excluded = 1` + `excluded_reason`，让后端默认查询自然排除，并保留留档。

## 表结构变更（向后兼容）

* `evidence.excluded` INTEGER DEFAULT 0 —— 1 = 已隔离，不得用于回答
* `evidence.excluded_reason` TEXT —— 隔离原因
* `evidence.quote_repaired` INTEGER DEFAULT 0 —— 1 = 引文已按页原文重写
* `evidence.original_quote` TEXT —— 重写前的原始引文（留档 / 可回溯）

新增列都是可加的，旧代码 `SELECT *` 会多拿到几个字段但不受影响。

## 用法

    python repair_evidence_quotes.py --db cninfo.multicompany.next.db --dry-run
    python repair_evidence_quotes.py --db cninfo.multicompany.next.db

退出码：0 = 可修复记录已处理、不可安全修复记录已隔离且校验通过；1 = 校验失败。
"""

from __future__ import annotations

import argparse
from collections import Counter
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: Windows 控制台默认 GBK，打印 ⚠️ 之类字符会抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from evidence_verify import (  # noqa: E402
    _NUMBER_LITERAL as NUMBER_LITERAL,
    _canonical_number as canonical_number,
    extract_contiguous_span,
    extract_pages,
    is_contiguous_quote,
    is_placeholder,
    normalize,
    segment_hits,
    split_segments,
)

#: 已知的缺陷证据：period 为空的规则抽取记录。
#: 这里显式列出而不是靠条件猜 —— 隔离必须有明确依据，且要能在报告里逐条说明。
KNOWN_DEFECTIVE: Dict[int, str] = {
    3103: "规则抽取缺陷：period 为空且 value 抽错（写入 2024.0，引文实为净资产收益率/每股收益），无法定位报告期与数值",
    3104: "规则抽取缺陷：period 为空且 value 抽错（写入 2024.0，引文实为净资产收益率/每股收益），无法定位报告期与数值",
    3127: "规则抽取缺陷：period 为空，无法定位报告期",
}

COLUMNS = {
    "excluded": "ALTER TABLE evidence ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0",
    "excluded_reason": "ALTER TABLE evidence ADD COLUMN excluded_reason TEXT",
    "quote_repaired": "ALTER TABLE evidence ADD COLUMN quote_repaired INTEGER NOT NULL DEFAULT 0",
    "original_quote": "ALTER TABLE evidence ADD COLUMN original_quote TEXT",
}

# 与后端动态 Evidence 展示/核验使用同一跨度上限。超过这个长度的连续片段通常
# 已经跨过多行表格，虽然“来自同一页”，却不再是精确、相关的原文摘录。
MAX_REPAIRED_QUOTE_CHARS = 200


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def ensure_columns(conn: sqlite3.Connection, *, dry_run: bool) -> List[str]:
    """补齐修复所需的列，返回本次新增的列名。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(evidence)")}
    added: List[str] = []
    for name, ddl in COLUMNS.items():
        if name in existing:
            continue
        added.append(name)
        if dry_run:
            log(f"[dry-run] 将新增列 evidence.{name}")
        else:
            conn.execute(ddl)
    if added and not dry_run:
        conn.commit()
    return added


def load_records(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(evidence)")}
    excluded = "e.excluded" if "excluded" in columns else "0"
    return conn.execute(
        "SELECT e.id, e.company_code, e.document_id, e.metric, e.period, e.value, e.unit, "
        "       e.source_page, e.source_quote, e.document_id, d.text_content, "
        f"       d.page_count, {excluded} AS excluded "
        "FROM evidence e JOIN docs d ON d.id = e.document_id ORDER BY e.id"
    ).fetchall()


def _extra_content(span: str, original_content: Sequence[str]) -> List[str]:
    """span 里出现了、但原引文没有的内容片段（当作"卷进了邻行数据"的证据）。

    只比数值：文字片段因为会跨行拼接，做父子串比较会误报。
    """
    originals = Counter(
        canonical_number(literal)
        for segment in original_content
        for literal in NUMBER_LITERAL.findall(segment)
        if canonical_number(literal)
    )
    seen: Counter[str] = Counter()
    extra: List[str] = []
    for literal in NUMBER_LITERAL.findall(span):
        canonical = canonical_number(literal)
        if not canonical:
            continue
        seen[canonical] += 1
        if seen[canonical] > originals[canonical]:
            extra.append(literal)
    return extra


def plan_repairs(conn: sqlite3.Connection) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """算出要改什么，不写库。返回 (可修复列表, 无法修复列表)。"""
    planned: List[Dict[str, Any]] = []
    unfixable: List[Dict[str, Any]] = []

    for row in load_records(conn):
        if row["excluded"]:
            continue
        pages = extract_pages(row["text_content"])
        page_text = pages.get(row["source_page"], "")
        original = row["source_quote"] or ""

        if row["id"] in KNOWN_DEFECTIVE:
            # 已判定不能支撑回答的记录不修引文，直接进入隔离流程
            continue

        content = [s for s in split_segments(original) if not is_placeholder(s)]
        if not content:
            # 纯占位符引文（如只有 `-`）没有可截取的连续区间，保持原样
            continue

        if is_contiguous_quote(original, page_text):
            continue  # 已经是连续原文，不必改

        if not page_text:
            unfixable.append({"id": row["id"], "reason": f"第 {row['source_page']} 页无原文"})
            continue

        span = extract_contiguous_span(page_text, original)
        if span is None:
            unfixable.append({
                "id": row["id"],
                "reason": f"第 {row['source_page']} 页无法定位全部内容片段",
            })
            continue

        # 三条硬校验：新引文必须 (a) 是页上连续原文，(b) 仍覆盖原引文全部内容片段，
        # (c) 其内容**全部来自原引文**（防止把邻行数据卷进来）。
        if not is_contiguous_quote(span, page_text):
            unfixable.append({"id": row["id"], "reason": "截取结果不是连续原文"})
            continue
        # (b) 用与核验一致的宽松匹配（标签可能被表格拆行）。
        #     不能用 `normalize(seg) in normalize(span)`：那样会把拆行标签判成丢失。
        if not all(segment_hits(s, span) for s in content):
            unfixable.append({"id": row["id"], "reason": "截取结果丢失内容片段"})
            continue
        extra = _extra_content(span, content)
        if extra:
            unfixable.append({
                "id": row["id"],
                "reason": f"连续区间卷入原引文之外的数字：{extra[:8]}",
            })
            continue
        span_length = len(normalize(span))
        if span_length > MAX_REPAIRED_QUOTE_CHARS:
            unfixable.append({
                "id": row["id"],
                "reason": (
                    f"最短连续区间为 {span_length} 字符，超过 "
                    f"{MAX_REPAIRED_QUOTE_CHARS} 字符上限"
                ),
            })
            continue

        planned.append({
            "id": row["id"],
            "company_code": row["company_code"],
            "metric": row["metric"],
            "document_id": row["document_id"],
            "page": row["source_page"],
            "original": original,
            "repaired": span,
            "extra_segments": extra,
        })
    return planned, unfixable


def apply_repairs(
    conn: sqlite3.Connection, planned: Sequence[Dict[str, Any]], *, dry_run: bool
) -> int:
    """写入修复后的引文；原引文存进 original_quote 留档。"""
    if dry_run:
        return 0
    conn.executemany(
        "UPDATE evidence SET source_quote = ?, original_quote = ?, quote_repaired = 1 "
        "WHERE id = ?",
        [(item["repaired"], item["original"], item["id"]) for item in planned],
    )
    conn.commit()
    return len(planned)


def quarantine(
    conn: sqlite3.Connection,
    unfixable: Sequence[Dict[str, Any]],
    *,
    dry_run: bool,
) -> int:
    """隔离已知缺陷和无法安全修复的证据，不删行、不改事实字段。"""
    reasons = dict(KNOWN_DEFECTIVE)
    reasons.update({int(item["id"]): str(item["reason"]) for item in unfixable})
    columns = {row[1] for row in conn.execute("PRAGMA table_info(evidence)")}
    excluded = "excluded" if "excluded" in columns else "0 AS excluded"
    count = 0
    for ev_id, reason in reasons.items():
        row = conn.execute(
            f"SELECT id, company_code, metric, period, value, source_page, {excluded} "
            "FROM evidence WHERE id = ?", (ev_id,)).fetchone()
        if row is None:
            log(f"⚠️ 待隔离的 evidence {ev_id} 不存在（可能已被处理）")
            continue
        if row["excluded"]:
            continue
        count += 1
        if dry_run:
            log(f"[dry-run] 将隔离 evidence {ev_id} "
                f"({row['company_code']} {row['metric']} period={row['period']!r})")
            continue
        conn.execute(
            "UPDATE evidence SET excluded = 1, excluded_reason = ? WHERE id = ?",
            (reason, ev_id))
    if not dry_run:
        conn.commit()
    return count


def verify(conn: sqlite3.Connection) -> List[str]:
    """修复后校验：不该再有非连续引文，隔离记录必须齐全。"""
    failures: List[str] = []

    rows = conn.execute(
        "SELECT e.id, e.source_page, e.source_quote, e.original_quote, "
        "       e.quote_repaired, e.excluded, d.text_content "
        "FROM evidence e JOIN docs d ON d.id = e.document_id ORDER BY e.id"
    ).fetchall()
    for row in rows:
        if row["excluded"]:
            continue
        pages = extract_pages(row["text_content"])
        page_text = pages.get(row["source_page"], "")
        content = [s for s in split_segments(row["source_quote"]) if not is_placeholder(s)]
        if not content:
            continue
        if not is_contiguous_quote(row["source_quote"], page_text):
            failures.append(
                f"evidence {row['id']} 的引文仍不是第 {row['source_page']} 页的连续原文")
        if len(normalize(row["source_quote"])) > MAX_REPAIRED_QUOTE_CHARS:
            failures.append(
                f"evidence {row['id']} 的引文超过 {MAX_REPAIRED_QUOTE_CHARS} 字符上限")
        if row["quote_repaired"] and row["original_quote"]:
            original_content = [
                s for s in split_segments(row["original_quote"]) if not is_placeholder(s)
            ]
            extra = _extra_content(row["source_quote"], original_content)
            if extra:
                failures.append(
                    f"evidence {row['id']} 的修复引文卷入额外数字：{extra[:8]}")

    for ev_id in KNOWN_DEFECTIVE:
        row = conn.execute(
            "SELECT excluded, excluded_reason FROM evidence WHERE id = ?", (ev_id,)).fetchone()
        if row is None:
            failures.append(f"待隔离的 evidence {ev_id} 不存在")
        elif not row["excluded"]:
            failures.append(f"evidence {ev_id} 未被隔离")
        elif not row["excluded_reason"]:
            failures.append(f"evidence {ev_id} 缺少隔离原因")
    return failures


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="修复引文形态并隔离不可用证据")
    parser.add_argument("--db", required=True, help="候选库路径（会被写入）")
    parser.add_argument("--dry-run", action="store_true", help="只报告计划，不写库")
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2
    if os.path.basename(db_path) == "cninfo.db":
        print("拒绝修改稳定库 cninfo.db：请对候选库执行", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        log(f"候选库 {db_path}，evidence 共 {total} 条")

        added = ensure_columns(conn, dry_run=args.dry_run)
        if added:
            log(f"新增列：{added}")

        planned, unfixable = plan_repairs(conn)
        log(f"计划重写引文 {len(planned)} 条；无法修复 {len(unfixable)} 条")

        preview = planned[:5]
        for item in preview:
            print(f"    ev={item['id']} {item['metric']} page={item['page']}")
            print(f"      原: {item['original']!r}")
            print(f"      新: {item['repaired']!r}")
        if len(planned) > len(preview):
            print(f"    ... 其余 {len(planned) - len(preview)} 条同类")

        for item in unfixable:
            log(f"⚠️ 无法修复 evidence {item['id']}：{item['reason']}")

        written = apply_repairs(conn, planned, dry_run=args.dry_run)
        quarantined = quarantine(conn, unfixable, dry_run=args.dry_run)
        log(f"已重写引文 {written} 条，已隔离 {quarantined} 条")

        if args.dry_run:
            print("\n[dry-run] 未写入任何改动。")
            return 0

        failures = verify(conn)
        if failures:
            print("\n修复后校验未通过：")
            for failure in failures:
                print(f"  - {failure}")
            return 1

        remaining = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE excluded = 0").fetchone()[0]
        repaired = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE quote_repaired = 1").fetchone()[0]
        excluded = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE excluded = 1").fetchone()[0]
        print(f"\n{'=' * 72}")
        print(f"  可参与回答的 evidence : {remaining}")
        print(f"  引文已重写为连续原文  : {repaired}")
        print(f"  已隔离（不参与回答）  : {excluded}")
        print("  校验：全部引文均为标注页的连续原文，隔离记录齐全")
        print("\n结论：[PASS] 修复完成。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
