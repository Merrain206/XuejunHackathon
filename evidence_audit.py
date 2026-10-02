# -*- coding: utf-8 -*-
"""evidence_audit.py — 四公司 Evidence 质量抽检（只读）。

对应 `docs/TONIGHT_DATABASE_TASKS.md` §3：对四家公司按指标抽取最新两条 Evidence，
逐条做 7 项机械校验，输出 `docs/data_quality_report_4_companies.md`。

## 判定口径（三档，互不混淆）

| 判定 | 含义 |
|---|---|
| `verified` | **人工复核过**（`evidence.review_status='verified'`）且机械校验通过 |
| `auto` | 规则抽取，**本次机械校验通过**（引文归位 + 结构 + URL 全部合格） |
| `rejected` | 至少一项校验不通过，**不能支撑回答** |

⚠️ 本脚本**不会**把 `auto` 提升成 `verified`。机械校验再干净也只是机械校验。

## 引文核验为什么按 segment

`source_quote` 的真实形态是表格行拼接（`营业收入 | 185,101,612.27 | ...`），
PDF 文本层会把同一行的单元格抽成多行。因此整串子串匹配对绝大多数真实证据都会
失败 —— 那不是造假，是表格抽取的表达差异。核验规则见 `evidence_verify.py`：
每个片段（文字标签 + 每个数字）都要能在该页找到，且**整段能被后端严格核到**
（verbatim）与"只能按片段核"（segments）分开统计，不混为一谈。

## 用法

    python evidence_audit.py --db cninfo.multicompany.next.db
    python evidence_audit.py --db cninfo.multicompany.next.db --no-network   # 跳过 URL 可达性
    python evidence_audit.py --db cninfo.multicompany.next.db --full         # 全量而非抽检

退出码：
    0 = 没有 rejected 记录
    1 = 存在 rejected 记录（报告中逐条列出公司 / document_id / page / 原因）
    2 = 数据库不可用
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 允许从任意工作目录直接执行本脚本（找出 evidence_verify.py 所在目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evidence_verify import extract_pages, summarize, verify_record  # noqa: E402

#: 固定公司名（产品负责人确认）
COMPANY_NAMES: Dict[str, str] = {
    "000066": "中国长城",
    "300558": "贝达药业",
    "600570": "恒生电子",
    "688583": "思看科技",
}
COMPANY_ORDER: tuple[str, ...] = ("688583", "600570", "000066", "300558")

#: 必查指标（§3）
REQUIRED_METRICS: tuple[str, ...] = (
    "revenue", "net_profit_attr", "operating_cash_flow", "total_assets", "eps",
)
#: 有则查的指标（§3）
OPTIONAL_METRICS: tuple[str, ...] = (
    "debt_ratio", "rd_expense", "rd_investment", "accounts_receivable", "inventory",
)

METRIC_LABELS: Dict[str, str] = {
    "revenue": "营业收入",
    "net_profit_attr": "归属于上市公司股东的净利润",
    "operating_cash_flow": "经营活动产生的现金流量净额",
    "total_assets": "总资产",
    "eps": "每股收益",
    "debt_ratio": "资产负债率",
    "rd_expense": "研发费用",
    "rd_investment": "研发投入",
    "accounts_receivable": "应收账款",
    "inventory": "存货",
}

#: 每指标抽检条数
SAMPLE_PER_METRIC = 2


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 数据读取
# ---------------------------------------------------------------------------


def load_records(
    conn: sqlite3.Connection, codes: Sequence[str], *, full: bool
) -> List[Dict[str, Any]]:
    """读取要抽检的 Evidence，并附上 docs 信息与页面切分。

    :param full: True 时核验**该公司全部 metric**（不是只核 §3 列出的那些），
        否则只对必查 + 有则查的指标各取最新 `SAMPLE_PER_METRIC` 条。
        之所以要在 full 模式下扩到全部 metric：只核白名单会漏掉
        `net_profit_deducted`、`equity_attr`、`risk_*` 这些同样会被后端引用的证据，
        也会漏掉它们身上的缺陷（例如 period 为空）。
    """
    records: List[Dict[str, Any]] = []
    doc_cache: Dict[int, Tuple[Dict[str, Any], Dict[int, str]]] = {}

    for code in codes:
        present = sorted({
            r[0] for r in conn.execute(
                "SELECT DISTINCT metric FROM evidence WHERE company_code = ? "
                "AND metric IS NOT NULL", (code,))
        })
        if full:
            metrics = present
        else:
            metrics = list(REQUIRED_METRICS)
            metrics += [m for m in OPTIONAL_METRICS if m in present]
            metrics += [m for m in present if m not in metrics]

        for metric in metrics:
            # §3 的"最新两条"始终单独取一份，作为报告的明细行；
            # full 模式下余下的证据只进汇总与 rejected 清单，避免报告膨胀到几百行。
            sample_rows = conn.execute(
                "SELECT e.*, d.source_url FROM evidence e JOIN docs d ON d.id = e.document_id "
                "WHERE e.company_code = ? AND e.metric = ? "
                "ORDER BY e.period DESC, e.id DESC LIMIT ?",
                (code, metric, SAMPLE_PER_METRIC),
            ).fetchall()
            sample_ids = {r["id"] for r in sample_rows}

            if full:
                rows = conn.execute(
                    "SELECT e.*, d.source_url FROM evidence e JOIN docs d ON d.id = e.document_id "
                    "WHERE e.company_code = ? AND e.metric = ? "
                    "ORDER BY e.period DESC, e.id DESC",
                    (code, metric),
                ).fetchall()
            else:
                rows = sample_rows

            if not rows:
                records.append({
                    "id": None, "company_code": code, "metric": metric,
                    "missing": True, "status": "missing", "quote_state": "missing",
                    "issues": ["库内没有该指标的 Evidence"], "findings": {},
                })
                continue

            for row in rows:
                doc_id = row["document_id"]
                if doc_id not in doc_cache:
                    doc = conn.execute(
                        "SELECT id, company_code, superseded, parse_status, page_count, "
                        "       text_content, file_name, source_url, document_type, "
                        "       published_at, report_period "
                        "FROM docs WHERE id = ?", (doc_id,),
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
                    company_code=row["company_code"],
                    document_id=doc_id,
                    metric=row["metric"],
                    value=row["value"],
                    unit=row["unit"],
                    period=row["period"],
                    source_page=row["source_page"],
                    source_quote=row["source_quote"],
                    source_url=document.get("source_url"),
                    review_status=row["review_status"],
                    document=document,
                    pages=pages,
                    excluded=bool(row["excluded"]) if "excluded" in row_keys else False,
                    excluded_reason=(
                        row["excluded_reason"] if "excluded_reason" in row_keys else None),
                )
                result.update({
                    "id": row["id"],
                    "company_code": row["company_code"],
                    "metric": metric,
                    "period": row["period"],
                    "value": row["value"],
                    "unit": row["unit"],
                    "source_page": row["source_page"],
                    "source_quote": row["source_quote"],
                    "document_id": doc_id,
                    "document_title": document.get("file_name"),
                    "document_type": document.get("document_type"),
                    "review_status": row["review_status"],
                    "method": row["method"],
                    "latest_two": row["id"] in sample_ids,
                })
                records.append(result)
    return records


# ---------------------------------------------------------------------------
# URL 可达性（§3.7：HTTPS 必须真实可访问，不能只做字符串替换）
# ---------------------------------------------------------------------------


def probe_url(url: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    """真实发一个 HEAD 请求看 URL 是否可达且返回 PDF。"""
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (evidence-audit)"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            return {
                "url": url,
                "ok": response.status == 200,
                "status": response.status,
                "content_type": content_type,
                "is_pdf": "pdf" in content_type.lower(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": False, "status": exc.code,
                "error": f"HTTP {exc.code}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - 网络异常种类多，统一记录
        return {"url": url, "ok": False, "status": None,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


def probe_urls(urls: Sequence[str], *, workers: int = 8) -> Dict[str, Dict[str, Any]]:
    """并发探测一组 URL（去重后）。"""
    unique = sorted({u for u in urls if u})
    out: Dict[str, Dict[str, Any]] = {}
    if not unique:
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(probe_url, unique):
            out[result["url"]] = result
    return out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def render_report(
    records: List[Dict[str, Any]],
    *,
    db_name: str,
    full: bool,
    network: bool,
    probes: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """生成 Markdown 报告与汇总数据。"""
    totals = Counter()
    by_company: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    quote_states = Counter()
    contiguous_count = 0
    for record in records:
        totals[record["status"]] += 1
        if record.get("quote_state"):
            quote_states[record["quote_state"]] += 1
        if record.get("contiguous"):
            contiguous_count += 1
        by_company[record["company_code"]].append(record)

    checked = [r for r in records if r["status"] not in ("missing", "excluded")]
    rejected = [r for r in records if r["status"] == "rejected"]
    missing = [r for r in records if r["status"] == "missing"]
    excluded = [r for r in records if r["status"] == "excluded"]

    lines: List[str] = []
    lines.append("# 四公司 Evidence 数据质量抽检报告")
    lines.append("")
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据库：`{db_name}`")
    lines.append(f"> 抽检脚本：`evidence_audit.py`；引文核验规则：`evidence_verify.py`")
    lines.append(f"> 抽检范围：{'全量' if full else f'每指标最新 {SAMPLE_PER_METRIC} 条'}"
                 f"；URL 可达性实测：{'是' if network else '否（--no-network）'}")
    lines.append("")
    lines.append("## 1. 汇总")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 抽检总条数 | {len(checked) + len(excluded)} |")
    lines.append(f"| `verified`（人工复核） | {totals['verified']} |")
    lines.append(f"| `auto`（机械校验通过） | {totals['auto']} |")
    lines.append(f"| `rejected`（不能支撑回答） | {totals['rejected']} |")
    lines.append(f"| `excluded`（已隔离，不参与回答） | {len(excluded)} |")
    lines.append(f"| 缺该指标（无 Evidence） | {len(missing)} |")
    if checked:
        lines.append(f"| 通过率（不含隔离） | {totals['verified'] + totals['auto']}/{len(checked)} "
                     f"({(totals['verified'] + totals['auto']) / len(checked) * 100:.1f}%) |")
    lines.append("")
    lines.append("### 引文可核验性")
    lines.append("")
    lines.append(f"| 项目 | 条数 |")
    lines.append("|---|---:|")
    lines.append(f"| 参与回答的证据 | {len(checked)} |")
    lines.append(f"| 引文是标注页的**连续原文**（后端严格核验可通过） | {contiguous_count} |")
    lines.append(f"| 引文不是连续原文（需按 segment 核验） | "
                 f"{len(checked) - contiguous_count} |")
    lines.append(f"| 已隔离（不参与回答） | {len(excluded)} |")
    lines.append("")
    lines.append("> 「连续原文」= 忽略空白后，引文是标注页原文的一段**连续子串** —— "
                 "后端 `db.page_from_markers()` 用的就是这条口径。")
    lines.append("")
    lines.append("### 引文归位情况")
    lines.append("")
    lines.append("| 归位形态 | 条数 | 含义 |")
    lines.append("|---|---:|---|")
    lines.append(f"| `verbatim` | {quote_states['verbatim']} | 整段引文（忽略空白）是该页"
                 "**连续原文** |")
    lines.append(f"| `segments` | {quote_states['segments']} | 每个片段都在该页，"
                 "但引文不是连续原文 |")
    lines.append(f"| `failed` | {quote_states['failed']} | 有片段在该页找不到 |")
    lines.append(f"| `excluded` | {quote_states['excluded']} | 已隔离，不参与回答 |")
    lines.append("")
    lines.append("> 引文已在候选库中重写为标注页的**连续原文**（见 "
                 "`repair_evidence_quotes.py`），因此后端 `db.page_from_markers()` 的"
                 "严格口径可以直接通过。若引文仍是 `segments` 形态（例如直接跑在稳定库上），"
                 "后端应改为按 segment 核验，或先执行该修复脚本。")
    lines.append("")

    lines.append("## 2. 逐公司逐指标明细（每指标最新 2 条，对应 §3 抽检要求）")
    lines.append("")
    _AUDITED_METRICS = set(REQUIRED_METRICS) | set(OPTIONAL_METRICS)
    for code in COMPANY_ORDER:
        rows = [r for r in by_company.get(code, [])
                if r["metric"] in _AUDITED_METRICS and r.get("latest_two")]
        if not rows:
            continue
        name = COMPANY_NAMES.get(code, code)
        counts = Counter(r["status"] for r in rows)
        lines.append(f"### {name}（{code}）")
        lines.append("")
        lines.append(f"明细 {len([r for r in rows if r['status'] != 'missing'])} 条："
                     f"verified {counts['verified']}、auto {counts['auto']}、"
                     f"rejected {counts['rejected']}；缺指标 {counts['missing']}。"
                     f"（该公司全量核验结果见第 1 节汇总与第 3 节）")
        lines.append("")
        lines.append("| 指标 | Evidence ID | 报告期 | 页码 | 判定 | 归位 | 问题 |")
        lines.append("|---|---:|---|---:|---|---|---|")
        for record in rows:
            if record["status"] == "missing":
                lines.append(f"| {METRIC_LABELS.get(record['metric'], record['metric'])} "
                             f"| — | — | — | ⬜ missing | — | 库内无该指标 Evidence |")
                continue
            badge = {"verified": "✅ verified", "auto": "🟡 auto",
                     "rejected": "❌ rejected",
                     "excluded": "⛔ excluded"}.get(record["status"], record["status"])
            issues = "；".join(record["issues"]) or "—"
            quote = record.get("quote_state", "—")
            lines.append(
                f"| {METRIC_LABELS.get(record['metric'], record['metric'])} "
                f"| {record['id']} | {record.get('period') or '—'} "
                f"| {record.get('source_page')} | {badge} | {quote} | {issues} |")
        lines.append("")

    if full:
        lines.append("## 3. 全量核验：§3 白名单以外的证据")
        lines.append("")
        other = [r for r in records
                 if r["metric"] not in _AUDITED_METRICS and r["status"] != "missing"]
        other_rejected = [r for r in other if r["status"] == "rejected"]
        lines.append(f"本节覆盖 `{'`、`'.join(sorted({r['metric'] for r in other}))}` 等 "
                     f"{len(other)} 条证据（`--full` 才有的范围）。"
                     f"其中 rejected {len(other_rejected)} 条。")
        lines.append("")
        if other_rejected:
            for record in other_rejected:
                lines.append(f"- **{COMPANY_NAMES.get(record['company_code'], record['company_code'])}**"
                             f"（{record['company_code']}） evidence `{record['id']}`，"
                             f"metric `{record['metric']}`，page {record.get('source_page')}")
                for issue in record["issues"]:
                    lines.append(f"  - {issue}")
        else:
            lines.append("全部通过机械校验。")
        lines.append("")
        rejected_section = "## 4. rejected 明细（需要处理）"
        excluded_section = "## 5. 已隔离证据（不参与回答）"
        url_section_no = 6
        conclusion_no = 7
    else:
        rejected_section = "## 3. rejected 明细（需要处理）"
        excluded_section = "## 4. 已隔离证据（不参与回答）"
        url_section_no = 5
        conclusion_no = 6

    lines.append(rejected_section)
    lines.append("")
    if not rejected:
        lines.append("无。本次抽检没有不能支撑回答的 Evidence。")
    else:
        for record in rejected:
            lines.append(f"- **{COMPANY_NAMES.get(record['company_code'], record['company_code'])}**"
                         f"（{record['company_code']}） evidence `{record['id']}`，"
                         f"metric `{record['metric']}`，document `{record.get('document_id')}`，"
                         f"page {record.get('source_page')}")
            for issue in record["issues"]:
                lines.append(f"  - {issue}")
    lines.append("")

    lines.append(excluded_section)
    lines.append("")
    if not excluded:
        lines.append("无。")
    else:
        lines.append("这些记录在候选库中被显式标记为 `evidence.excluded = 1`，"
                     "**不参与任何回答**，保留在库中留档。"
                     "它们的问题无法从原文推断（如 `period` 为空），因此不臆造字段值。")
        lines.append("")
        for record in excluded:
            lines.append(f"- **{COMPANY_NAMES.get(record['company_code'], record['company_code'])}**"
                         f"（{record['company_code']}） evidence `{record['id']}`，"
                         f"metric `{record['metric']}`，document `{record.get('document_id')}`，"
                         f"page {record.get('source_page')}")
            for issue in record["issues"]:
                lines.append(f"  - {issue}")
    lines.append("")

    if network and probes:
        failed = [p for p in probes.values() if not p.get("ok")]
        lines.append(f"## {url_section_no}. source_url 可达性实测")
        lines.append("")
        lines.append(f"抽检涉及 {len(probes)} 个去重 URL，实测可达 "
                     f"{len(probes) - len(failed)} 个，不可达 {len(failed)} 个。")
        lines.append("")
        if failed:
            lines.append("| URL | 状态 | 错误 |")
            lines.append("|---|---|---|")
            for probe in failed[:30]:
                lines.append(f"| {probe['url'][:110]} | {probe.get('status')} "
                             f"| {probe.get('error', '—')} |")
        else:
            lines.append("全部返回 HTTP 200。")
        lines.append("")

    lines.append(f"## {conclusion_no}. 结论与限制")
    lines.append("")
    lines.append("- 本报告的 `auto` **只代表机械校验通过**，不代表人工复核；"
                 "不要把 auto 说成 verified。")
    lines.append("- 引文核验按 segment 做：文字标签必须在该页出现，每个数字必须是该页的"
                 "**完整数字**（去千分位后值相等）。")
    lines.append(f"- {contiguous_count} 条引文是标注页的**连续原文**，"
                 "后端严格核验（忽略空白后连续子串）可直接通过。")
    lines.append("- 引文形态由 `repair_evidence_quotes.py` 重写：从标注页原文里截取覆盖"
                 "全部内容片段的最小区间，**未改动任何数字、页码或 URL**；"
                 "原始引文保留在 `evidence.original_quote` 里可回溯。")
    lines.append("- `evidence.excluded = 1` 的记录被隔离，不参与回答；"
                 "它们的问题无法从原文推断，因此不臆造字段值。")
    lines.append("- 本报告只做机械核验；**未**把 auto 提升为 verified。")
    lines.append("")

    summary = {
        "checked": len(checked),
        "verified": totals["verified"],
        "auto": totals["auto"],
        "rejected": totals["rejected"],
        "excluded": len(excluded),
        "missing": len(missing),
        "contiguous": contiguous_count,
        "verbatim": quote_states["verbatim"],
        "segments": quote_states["segments"],
        "failed": quote_states["failed"],
        "urls_probed": len(probes),
        "urls_failed": len([p for p in probes.values() if not p.get("ok")]),
        "rejected_ids": [r["id"] for r in rejected],
    }
    return "\n".join(lines), summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="四公司 Evidence 质量抽检")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--output", default=None, help="报告输出路径")
    parser.add_argument("--full", action="store_true", help="全量核验而非抽检")
    parser.add_argument("--no-network", action="store_true", help="跳过 URL 可达性实测")
    parser.add_argument("--workers", type=int, default=8, help="URL 探测并发数")
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    output_path = args.output or os.path.join(
        os.path.dirname(db_path), "docs", "data_quality_report_4_companies.md")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        codes = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT company_code FROM docs ORDER BY company_code")
        ]
        ordered = [c for c in COMPANY_ORDER if c in codes]
        ordered += [c for c in codes if c not in ordered]

        log(f"读取 Evidence（{'全量' if args.full else '抽检'}）...")
        records = load_records(conn, ordered, full=args.full)
        log(f"共 {len(records)} 条")

        probes: Dict[str, Dict[str, Any]] = {}
        if not args.no_network:
            urls = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT d.source_url FROM evidence e "
                    "JOIN docs d ON d.id = e.document_id WHERE d.source_url LIKE 'http%'")
            ]
            log(f"实测 {len(urls)} 个去重 source_url 的可达性 ...")
            probes = probe_urls(urls, workers=args.workers)
            log(f"不可达 {len([p for p in probes.values() if not p.get('ok')])} 个")

        report, summary = render_report(
            records, db_name=os.path.basename(db_path), full=args.full,
            network=not args.no_network, probes=probes)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(report)

        log(f"抽检 {summary['checked']} 条：verified {summary['verified']}、"
            f"auto {summary['auto']}、rejected {summary['rejected']}、"
            f"excluded {summary['excluded']}、missing {summary['missing']}")
        log(f"引文：连续原文 {summary['contiguous']} 条；"
            f"verbatim {summary['verbatim']}、segments {summary['segments']}、"
            f"failed {summary['failed']}")
        log(f"报告已写入 {output_path}")

        if summary["rejected"]:
            log(f"存在 {summary['rejected']} 条 rejected：{summary['rejected_ids']}")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
