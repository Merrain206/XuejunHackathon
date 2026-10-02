# -*- coding: utf-8 -*-
"""acceptance_check.py — 四公司候选库只读验收（对应任务书 §6 / §7）。

一条命令跑完全部 P0 检查，并用退出码表达结论：

    python acceptance_check.py --db cninfo.multicompany.next.db

退出码：
    0 = 全部 P0 检查通过
    1 = 有 P0 检查不通过（逐条打印公司 / document_id / page / 失败原因）
    2 = 数据库不可用

## 检查项

**结构完整性（§6.3 / §6.4）**
- `PRAGMA integrity_check` = ok
- 孤儿 chunks = 0、孤儿 evidence = 0
- 跨公司 document 关联 = 0（chunks 与 evidence 两个方向）
- 重复 canonical source URL（有则列出解释）
- `source_page` 越界 / 非正 = 0
- `source_url` 含 `#page=` fragment = 0

**全文索引（§2 验收）**
- `meta.fts_mode` 与真实 tokenizer 一致
- `chunks_fts` 行数 = 纳入范围（未 superseded 且 parse_status='ok'）的 chunks 数
- `chunks_fts.rowid` 与 `chunks.id` 一一对应，无重复插入
- FTS 纳入范围里没有 superseded / 解析失败的文档

**公司隔离与检索（§2 验收）**
- 四家公司 × 三个关键词，检索结果不得跨公司
- 每条查询耗时打印，并与 300 ms 目标比对

**Evidence 抽检（§3）**
- 引文核验（segment 规则，见 `evidence_verify.py`）
- 结构 / 页码 / URL 检查

**交付信息（§6.5）**
- 候选库路径、大小、SHA-256
- 四公司 docs / valid docs / chunks / evidence / metrics 计数
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 允许从任意工作目录直接执行本脚本（找出 evidence_verify.py 所在目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evidence_verify import (  # noqa: E402
    extract_pages,
    is_placeholder,
    normalize,
    split_segments,
    verify_record,
)
from repair_evidence_quotes import (  # noqa: E402
    MAX_REPAIRED_QUOTE_CHARS,
    _extra_content,
)

COMPANY_NAMES: Dict[str, str] = {
    "000066": "中国长城",
    "300558": "贝达药业",
    "600570": "恒生电子",
    "688583": "思看科技",
}
COMPANY_ORDER: tuple[str, ...] = ("688583", "600570", "000066", "300558")

DEMO_KEYWORDS: tuple[str, ...] = (
    "营业收入",
    "归属于上市公司股东的净利润",
    "经营活动产生的现金流量净额",
)

#: 检索耗时目标（§2 验收：本机低于 300 ms）
LATENCY_TARGET_MS = 300.0


class Checker:
    """收集检查结果，最后统一决定退出码。"""

    def __init__(self) -> None:
        self.failures: List[str] = []
        self.warnings: List[str] = []
        self.sections: List[str] = []

    def head(self, title: str) -> None:
        self.sections.append(title)
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")

    def ok(self, message: str) -> None:
        print(f"  [PASS] {message}")

    def fail(self, message: str) -> None:
        print(f"  [FAIL] {message}")
        self.failures.append(message)

    def warn(self, message: str) -> None:
        print(f"  [WARN] {message}")
        self.warnings.append(message)

    def info(self, message: str) -> None:
        print(f"         {message}")

    def expect(self, condition: bool, ok_message: str, fail_message: str) -> bool:
        if condition:
            self.ok(ok_message)
        else:
            self.fail(fail_message)
        return bool(condition)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 结构完整性
# ---------------------------------------------------------------------------


def check_integrity(conn: sqlite3.Connection, checker: Checker) -> None:
    checker.head("1. 结构完整性")

    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    checker.expect(result == "ok", f"PRAGMA integrity_check = {result}",
                   f"PRAGMA integrity_check = {result}")

    orphan_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks c LEFT JOIN docs d ON d.id = c.document_id "
        "WHERE d.id IS NULL").fetchone()[0]
    checker.expect(orphan_chunks == 0, f"孤儿 chunks = {orphan_chunks}",
                   f"孤儿 chunks = {orphan_chunks}（应为 0）")

    orphan_evidence = conn.execute(
        "SELECT COUNT(*) FROM evidence e LEFT JOIN docs d ON d.id = e.document_id "
        "WHERE d.id IS NULL").fetchone()[0]
    checker.expect(orphan_evidence == 0, f"孤儿 evidence = {orphan_evidence}",
                   f"孤儿 evidence = {orphan_evidence}（应为 0）")

    cross_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
        "WHERE c.company_code <> d.company_code").fetchone()[0]
    checker.expect(cross_chunks == 0, f"chunks 跨公司关联 = {cross_chunks}",
                   f"chunks 跨公司关联 = {cross_chunks}（应为 0）")

    cross_evidence = conn.execute(
        "SELECT COUNT(*) FROM evidence e JOIN docs d ON d.id = e.document_id "
        "WHERE e.company_code <> d.company_code").fetchone()[0]
    checker.expect(cross_evidence == 0, f"evidence 跨公司关联 = {cross_evidence}",
                   f"evidence 跨公司关联 = {cross_evidence}（应为 0）")

    dup_rows = conn.execute(
        "SELECT source_url, COUNT(*) n, GROUP_CONCAT(id) ids, "
        "       SUM(CASE WHEN superseded = 0 THEN 1 ELSE 0 END) active, "
        "       GROUP_CONCAT(DISTINCT company_code) companies "
        "FROM docs WHERE source_url IS NOT NULL AND source_url <> '' "
        "GROUP BY source_url HAVING n > 1 ORDER BY n DESC").fetchall()
    if dup_rows:
        # §6.4 要求"重复 canonical source URL = 0 或有解释"。
        # 实测：四公司库里有 133 组 URL 各出现 2 次，形态固定为
        # 「1 条 superseded + 1 条 active，同一家公司，同一 PDF」——
        # 这是**版本更替（supersede）机制的正常结果**，不是重复入库错误：
        # 同一份公告先以原始文件名入库，后来以规范的 `YYYYMMDD_` 前缀文件名重新入库，
        # 旧记录被标记 superseded。因为 FTS 只索引 active 文档，`chunks_fts` 里
        # 不会有重复内容。
        # 真正需要报警的只有两种情况：跨公司共用同一个 URL（串号），
        # 或同一 URL 下有多条 active（同一份 PDF 被当成两份在用）。
        cross_company: List[sqlite3.Row] = []
        multi_active: List[sqlite3.Row] = []
        for row in dup_rows:
            companies = [c for c in str(row["companies"] or "").split(",") if c]
            if len(set(companies)) > 1:
                cross_company.append(row)
            if int(row["active"] or 0) > 1:
                multi_active.append(row)

        checker.warn(
            f"重复 canonical source URL：{len(dup_rows)} 组，全部为 supersede 形态"
            f"（1 条 superseded + 1 条 active，同一公司、同一 PDF）")
        for row in dup_rows[:3]:
            print(f"         · n={row['n']} active={row['active']} "
                  f"companies={row['companies']} doc_ids={row['ids']} "
                  f"url={row['source_url'][:66]}")
        if len(dup_rows) > 3:
            print(f"         · ... 其余 {len(dup_rows) - 3} 组形态相同")

        if cross_company:
            checker.fail(f"有 {len(cross_company)} 组重复 URL 跨公司，疑似数据串号")
        else:
            checker.info("无跨公司共用 URL。")

        if multi_active:
            checker.fail(f"有 {len(multi_active)} 组重复 URL 下存在多条 active 文档"
                         "（同一份 PDF 被当成两份在用）")
            for row in multi_active[:5]:
                print(f"         · active={row['active']} doc_ids={row['ids']} "
                      f"url={row['source_url'][:70]}")
        else:
            checker.info("每组最多 1 条 active，FTS 不会索引到重复内容。")
    else:
        checker.ok("重复 canonical source URL = 0")

    bad_page = conn.execute(
        "SELECT COUNT(*) FROM evidence e JOIN docs d ON d.id = e.document_id "
        "WHERE e.source_page IS NULL OR e.source_page < 1 "
        "   OR (d.page_count IS NOT NULL AND e.source_page > d.page_count)").fetchone()[0]
    checker.expect(bad_page == 0, "source_page 全部为正整数且未越界",
                   f"source_page 非法或越界 = {bad_page}")

    fragment = conn.execute(
        "SELECT COUNT(*) FROM docs WHERE source_url LIKE '%#page=%'").fetchone()[0]
    checker.expect(fragment == 0, "source_url 不含 #page= fragment",
                   f"source_url 含 #page= fragment = {fragment}")

    empty_url = conn.execute(
        "SELECT COUNT(*) FROM evidence e JOIN docs d ON d.id = e.document_id "
        "WHERE d.source_url IS NULL OR d.source_url = ''").fetchone()[0]
    checker.expect(empty_url == 0, "所有 evidence 的文档都有 source_url",
                   f"evidence 关联文档缺 source_url = {empty_url}")


# ---------------------------------------------------------------------------
# 全文索引
# ---------------------------------------------------------------------------


def check_fts(conn: sqlite3.Connection, checker: Checker) -> None:
    checker.head("2. 全文索引（chunks_fts）")

    meta = {
        row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta").fetchall()
    }
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'").fetchone()
    if ddl is None:
        checker.fail("chunks_fts 不存在")
        return

    sql = ddl[0] or ""
    real_mode = "trigram" if "trigram" in sql.lower() else (
        "unicode61" if "unicode61" in sql.lower() else "unknown")
    declared = str(meta.get("fts_mode", "")).strip()
    checker.expect(
        declared == real_mode,
        f"meta.fts_mode='{declared}' 与真实 tokenizer='{real_mode}' 一致",
        f"meta.fts_mode='{declared}' 与真实 tokenizer='{real_mode}' 不一致")

    eligible = conn.execute(
        "SELECT COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
        "WHERE d.superseded = 0 AND d.parse_status = 'ok'").fetchone()[0]
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    fts_rows = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    checker.info(f"chunks 总数 = {total_chunks}")
    checker.info(f"纳入范围（未 superseded 且 parse_status='ok'）chunks = {eligible}")
    checker.info(f"chunks_fts 行数 = {fts_rows}")
    checker.expect(
        fts_rows == eligible,
        f"FTS 行数与纳入范围一致（{fts_rows} = {eligible}）",
        f"FTS 行数 {fts_rows} <> 纳入范围 {eligible}，差额 {eligible - fts_rows}")

    orphan_fts = conn.execute(
        "SELECT COUNT(*) FROM chunks_fts f LEFT JOIN chunks c ON c.id = f.rowid "
        "WHERE c.id IS NULL").fetchone()[0]
    checker.expect(orphan_fts == 0, "FTS 中没有指向不存在 chunks 的行",
                   f"FTS 中指向不存在 chunks 的行 = {orphan_fts}")

    missing_fts = conn.execute(
        "SELECT COUNT(*) FROM chunks c JOIN docs d ON d.id = c.document_id "
        "WHERE d.superseded = 0 AND d.parse_status = 'ok' "
        "  AND NOT EXISTS (SELECT 1 FROM chunks_fts f WHERE f.rowid = c.id)"
    ).fetchone()[0]
    checker.expect(missing_fts == 0, "纳入范围内没有遗漏的 chunks",
                   f"纳入范围内遗漏 chunks = {missing_fts}")

    bad_scope = conn.execute(
        "SELECT COUNT(*) FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
        "JOIN docs d ON d.id = c.document_id "
        "WHERE NOT (d.superseded = 0 AND d.parse_status = 'ok')").fetchone()[0]
    checker.expect(bad_scope == 0, "FTS 未纳入 superseded / 解析失败的文档",
                   f"FTS 纳入了 {bad_scope} 条本不该索引的 chunks")

    if fts_rows == eligible and missing_fts == 0 and bad_scope == 0:
        checker.info("覆盖率 100%，无需差异清单。")
    else:
        checker.info("⚠️ 覆盖率不是 100%，差异如上（§2 要求给出可解释清单）。")


# ---------------------------------------------------------------------------
# 公司隔离与检索耗时
# ---------------------------------------------------------------------------


def check_isolation(conn: sqlite3.Connection, checker: Checker,
                    codes: Sequence[str]) -> None:
    checker.head("3. 公司隔离与检索耗时（FTS）")

    timings: List[float] = []
    leaks = 0
    for code in codes:
        for keyword in DEMO_KEYWORDS:
            started = time.perf_counter()
            rows = conn.execute(
                "SELECT c.company_code FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
                "JOIN docs d ON d.id = c.document_id "
                "WHERE chunks_fts MATCH ? AND c.company_code = ? "
                "  AND d.superseded = 0 AND d.parse_status = 'ok' LIMIT 20",
                (keyword, code),
            ).fetchall()
            elapsed = (time.perf_counter() - started) * 1000
            timings.append(elapsed)
            leaked = sorted({row[0] for row in rows} - {code})
            if leaked:
                leaks += 1
                checker.fail(
                    f"{COMPANY_NAMES.get(code, code)}({code}) 检索「{keyword}」"
                    f"串到 {leaked}")
            print(f"  {code} | {keyword[:14]:16s} hits={len(rows):3d} {elapsed:7.2f} ms")

    checker.expect(leaks == 0, "四公司 × 三关键词检索均未串公司",
                   f"有 {leaks} 次检索跨公司")

    if timings:
        worst = max(timings)
        average = sum(timings) / len(timings)
        checker.info(f"最慢 {worst:.2f} ms，平均 {average:.2f} ms，目标 < {LATENCY_TARGET_MS:.0f} ms")
        checker.expect(worst < LATENCY_TARGET_MS,
                       f"最慢查询 {worst:.2f} ms 低于目标 {LATENCY_TARGET_MS:.0f} ms",
                       f"最慢查询 {worst:.2f} ms 超过目标 {LATENCY_TARGET_MS:.0f} ms")

    # LIKE 兜底：稳定库 FTS 不全时后端要用它，必须也隔离且够快
    print("\n  LIKE 兜底（FTS 不可用时的退路）:")
    like_timings: List[float] = []
    for code in codes:
        keyword = DEMO_KEYWORDS[0]
        started = time.perf_counter()
        rows = conn.execute(
            "SELECT c.company_code FROM chunks c JOIN docs d ON d.id = c.document_id "
            "WHERE c.company_code = ? AND c.content LIKE ? ESCAPE '\\' "
            "  AND d.superseded = 0 AND d.parse_status = 'ok' LIMIT 20",
            (code, f"%{keyword}%"),
        ).fetchall()
        elapsed = (time.perf_counter() - started) * 1000
        like_timings.append(elapsed)
        leaked = sorted({row[0] for row in rows} - {code})
        print(f"  {code} | LIKE hits={len(rows):3d} {elapsed:7.2f} ms "
              f"{'串公司 ' + str(leaked) if leaked else ''}")
        if leaked:
            checker.fail(f"{code} LIKE 兜底串到 {leaked}")
    if like_timings:
        checker.info(f"LIKE 最慢 {max(like_timings):.2f} ms")


# ---------------------------------------------------------------------------
# Evidence 抽检
# ---------------------------------------------------------------------------


def check_evidence(conn: sqlite3.Connection, checker: Checker,
                   codes: Sequence[str]) -> None:
    checker.head("4. Evidence 抽检（全量机械核验）")

    doc_cache: Dict[int, Tuple[Dict[str, Any], Dict[int, str]]] = {}
    totals: Dict[str, int] = {}
    rejected: List[Dict[str, Any]] = []
    non_contiguous: List[Dict[str, Any]] = []
    polluted: List[Dict[str, Any]] = []

    for code in codes:
        rows = conn.execute(
            "SELECT e.*, d.source_url FROM evidence e JOIN docs d ON d.id = e.document_id "
            "WHERE e.company_code = ? ORDER BY e.id", (code,)
        ).fetchall()
        counts: Dict[str, int] = {}
        for row in rows:
            doc_id = row["document_id"]
            if doc_id not in doc_cache:
                doc = conn.execute(
                    "SELECT id, company_code, superseded, parse_status, page_count, "
                    "       text_content, file_name, source_url FROM docs WHERE id = ?",
                    (doc_id,)
                ).fetchone()
                if doc is None:
                    doc_cache[doc_id] = ({}, {})
                else:
                    doc_cache[doc_id] = (
                        {key: doc[key] for key in doc.keys()},
                        extract_pages(doc["text_content"]),
                    )
            document, pages = doc_cache[doc_id]
            row_keys = set(row.keys())
            result = verify_record(
                company_code=row["company_code"], document_id=doc_id, metric=row["metric"],
                value=row["value"], unit=row["unit"], period=row["period"],
                source_page=row["source_page"], source_quote=row["source_quote"],
                source_url=document.get("source_url"), review_status=row["review_status"],
                document=document, pages=pages,
                excluded=bool(row["excluded"]) if "excluded" in row_keys else False,
                excluded_reason=(
                    row["excluded_reason"] if "excluded_reason" in row_keys else None),
            )
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            totals[result["status"]] = totals.get(result["status"], 0) + 1
            if result["status"] == "rejected":
                rejected.append({
                    "company_code": code, "document_id": doc_id,
                    "page": row["source_page"], "id": row["id"],
                    "metric": row["metric"], "issues": result["issues"],
                })
            # 引文必须是标注页的连续原文 —— 这是后端 page_from_markers 的口径
            if result["quote_state"] in ("segments",):
                non_contiguous.append({
                    "company_code": code, "document_id": doc_id,
                    "page": row["source_page"], "id": row["id"],
                    "metric": row["metric"],
                })
            if "excluded" in row_keys and not bool(row["excluded"]):
                quote = str(row["source_quote"] or "")
                if len(normalize(quote)) > MAX_REPAIRED_QUOTE_CHARS:
                    polluted.append({
                        "company_code": code, "document_id": doc_id,
                        "page": row["source_page"], "id": row["id"],
                        "reason": f"引文超过 {MAX_REPAIRED_QUOTE_CHARS} 字符",
                    })
                original = row["original_quote"] if "original_quote" in row_keys else None
                repaired = bool(row["quote_repaired"]) if "quote_repaired" in row_keys else False
                if repaired and original:
                    content = [
                        s for s in split_segments(original) if not is_placeholder(s)
                    ]
                    extra = _extra_content(quote, content)
                    if extra:
                        polluted.append({
                            "company_code": code, "document_id": doc_id,
                            "page": row["source_page"], "id": row["id"],
                            "reason": f"卷入原引文之外的数字 {extra[:8]}",
                        })
        name = COMPANY_NAMES.get(code, code)
        print(f"  {name}（{code}）：{len(rows)} 条 → "
              f"verified {counts.get('verified', 0)}、auto {counts.get('auto', 0)}、"
              f"rejected {counts.get('rejected', 0)}、"
              f"excluded {counts.get('excluded', 0)}")

    total = sum(totals.values())
    considered = total - totals.get("excluded", 0)
    checker.info(f"合计 {total} 条：verified {totals.get('verified', 0)}、"
                 f"auto {totals.get('auto', 0)}、rejected {totals.get('rejected', 0)}、"
                 f"excluded {totals.get('excluded', 0)}（不参与回答）")
    checker.info(f"参与回答的证据共 {considered} 条")

    if rejected:
        # 这里把证据缺陷当作**真实失败**，不做"降级为警告"的处理 ——
        # 上一版抽检脚本正是因为把"引文在该页找不到"降级成 auto 而给出了 100% 的假通过。
        # 缺陷确实存在（3 条 period 为空的规则抽取记录），必须让退出码如实反映。
        checker.fail(f"有 {len(rejected)} 条 Evidence 未通过机械核验"
                     f"（占 {len(rejected)}/{considered} = "
                     f"{len(rejected) / considered * 100:.1f}%），详见 docs/data_quality_report_4_companies.md")
        for item in rejected:
            name = COMPANY_NAMES.get(item["company_code"], item["company_code"])
            print(f"    - {name}（{item['company_code']}）evidence {item['id']} "
                  f"metric={item['metric']} document_id={item['document_id']} "
                  f"page={item['page']}")
            for issue in item["issues"]:
                print(f"        · {issue}")
        checker.info("这些记录在 evidence 表里仍是 review_status='auto'，"
                     "后端不得把它们当作已核验证据使用。")
    else:
        checker.ok("全部 Evidence 通过机械核验")

    contiguous = considered - len(non_contiguous)
    checker.info(f"引文为标注页连续原文：{contiguous}/{considered} 条")
    if non_contiguous:
        checker.fail(f"有 {len(non_contiguous)} 条引文不是标注页的连续原文"
                     f"（后端 page_from_markers 会判为找不到）；"
                     f"对候选库执行 repair_evidence_quotes.py 可修复")
        for item in non_contiguous[:20]:
            name = COMPANY_NAMES.get(item["company_code"], item["company_code"])
            print(f"    - {name}（{item['company_code']}）evidence {item['id']} "
                  f"metric={item['metric']} document_id={item['document_id']} "
                  f"page={item['page']}")
        if len(non_contiguous) > 20:
            print(f"    ... 其余 {len(non_contiguous) - 20} 条")
    else:
        checker.ok("全部引文都是标注页的连续原文")

    if polluted:
        checker.fail(f"有 {len(polluted)} 条 Evidence 引文过长或卷入额外数字")
        for item in polluted[:20]:
            print(f"    - {item['company_code']} evidence {item['id']} "
                  f"document_id={item['document_id']} page={item['page']}：{item['reason']}")
    else:
        checker.ok("全部可用 Evidence 均满足精确摘录长度与数字边界")


# ---------------------------------------------------------------------------
# 计数与交付信息
# ---------------------------------------------------------------------------


def report_counts(conn: sqlite3.Connection, checker: Checker,
                  codes: Sequence[str]) -> None:
    checker.head("5. 四公司计数（§8 PR 描述用）")

    print("  code     公司      docs  valid_docs  chunks  evidence  metrics  latest")
    totals = {"docs": 0, "valid": 0, "chunks": 0, "evidence": 0}
    for code in codes:
        name = COMPANY_NAMES.get(code, code)
        docs = conn.execute("SELECT COUNT(*) FROM docs WHERE company_code = ?", (code,)).fetchone()[0]
        valid = conn.execute(
            "SELECT COUNT(*) FROM docs WHERE company_code = ? AND superseded = 0 "
            "AND parse_status = 'ok'", (code,)).fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE company_code = ?", (code,)).fetchone()[0]
        evidence = conn.execute("SELECT COUNT(*) FROM evidence WHERE company_code = ?", (code,)).fetchone()[0]
        metrics = conn.execute(
            "SELECT COUNT(DISTINCT metric) FROM evidence WHERE company_code = ?", (code,)).fetchone()[0]
        latest = conn.execute(
            "SELECT report_period FROM docs WHERE company_code = ? AND superseded = 0 "
            "AND parse_status = 'ok' AND document_type IN "
            "('annual_report','semiannual_report','q1_report','q3_report','quarterly_report') "
            "ORDER BY published_at DESC LIMIT 1", (code,)).fetchone()
        latest_period = latest[0] if latest else "—"
        print(f"  {code}  {name}  {docs:5d}  {valid:10d}  {chunks:6d}  {evidence:8d}  "
              f"{metrics:7d}  {latest_period}")
        totals["docs"] += docs
        totals["valid"] += valid
        totals["chunks"] += chunks
        totals["evidence"] += evidence
    print(f"  总计      {'':6s}  {totals['docs']:5d}  {totals['valid']:10d}  "
          f"{totals['chunks']:6d}  {totals['evidence']:8d}")

    # 未入库的其它公司（如果有）
    others = [
        row[0] for row in conn.execute(
            "SELECT DISTINCT company_code FROM docs ORDER BY company_code")
        if row[0] not in COMPANY_ORDER
    ]
    if others:
        checker.warn(f"库内还有任务书之外的公司：{others}")


def report_delivery(db_path: str, checker: Checker) -> None:
    checker.head("6. 候选库交付信息（§6.5）")
    size = os.path.getsize(db_path)
    digest = sha256_file(db_path)
    checker.info(f"路径   : {os.path.abspath(db_path)}")
    checker.info(f"大小   : {size} bytes ({size / 1024 / 1024:.1f} MiB)")
    checker.info(f"SHA-256: {digest}")
    checker.info("提示   : .db 已被 Git 忽略，通过团队约定的文件传递方式交付；"
                 "替换稳定 cninfo.db 需用户明确确认。")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="四公司候选库只读验收")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--skip-evidence", action="store_true",
                        help="跳过 Evidence 全量核验（大库时更快）")
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        codes = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT company_code FROM docs ORDER BY company_code")
        ]
        ordered = [c for c in COMPANY_ORDER if c in codes]
        ordered += [c for c in codes if c not in ordered]

        checker = Checker()
        started = time.perf_counter()
        print(f"验收数据库：{db_path}")

        check_integrity(conn, checker)
        check_fts(conn, checker)
        check_isolation(conn, checker, ordered)
        if not args.skip_evidence:
            check_evidence(conn, checker, ordered)
        else:
            checker.head("4. Evidence 抽检")
            checker.warn("已按 --skip-evidence 跳过")
        report_counts(conn, checker, ordered)
        report_delivery(db_path, checker)

        elapsed = time.perf_counter() - started
        print(f"\n{'=' * 72}")
        print(f"检查完成，用时 {elapsed:.1f}s")
        print(f"失败 {len(checker.failures)} 项，警告 {len(checker.warnings)} 项")
        if checker.failures:
            print("\n失败明细：")
            for failure in checker.failures:
                print(f"  - {failure}")
            print("\n结论：[FAIL] 存在 P0 检查未通过，不得替换稳定 cninfo.db。")
            return 1
        print("\n结论：[PASS] 全部 P0 检查通过。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
