# -*- coding: utf-8 -*-
"""build_company_catalog.py — 生成四公司机器可读目录与问题覆盖矩阵。

对应 `docs/TONIGHT_DATABASE_TASKS.md` §5。

输出两个文件（默认写到 `docs/`）：

* `companies.json` —— 公司目录：code / name / exchange / board / industry /
  available metrics / latest report period / 数据计数。
* `coverage_matrix.md` —— 覆盖矩阵：哪些问题有足够 Evidence，
  并明确标出证据缺口。**指标缺失就是缺失，绝不补零或补空值。**

## 关于 exchange / board 的来源

数据库里没有交易所和板块字段。这里**不猜**行业，也不靠模型"常识"补公司事实：

* `exchange` / `board` **由证券代码结构推导**，这是公开的编码规则
  （`000xxx` 深交所主板、`300xxx` 深交所创业板、`600xxx` 上交所主板、
  `688xxx` 上交所科创板）；字段里同时给出 `exchange_source: "code_rule"`
  标明它是推导值，不是公告原文。
* `industry` 需要公告原文佐证，本脚本默认不填；要填必须给出处。

用法：

    python build_company_catalog.py --db cninfo.multicompany.next.db
    python build_company_catalog.py --db cninfo.multicompany.next.db \\
        --json docs/companies.json --matrix docs/coverage_matrix.md
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

#: 允许从任意工作目录直接执行本脚本（找出 cninfo_queries.py 所在目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cninfo_queries import period_rank  # noqa: E402

#: 公司名称固定（产品负责人确认，见 AGENTS.md）
COMPANY_NAMES: Dict[str, str] = {
    "688583": "思看科技",
    "600570": "恒生电子",
    "000066": "中国长城",
    "300558": "贝达药业",
}

#: 展示顺序：稳定基线在前
COMPANY_ORDER: tuple[str, ...] = ("688583", "600570", "000066", "300558")

#: 代码 → (交易所, 板块)。这是**编码规则推导**，不是公告原文。
CODE_RULES: tuple[tuple[str, str, str], ...] = (
    ("688", "上交所", "科创板"),
    ("600", "上交所", "主板"),
    ("601", "上交所", "主板"),
    ("603", "上交所", "主板"),
    ("605", "上交所", "主板"),
    ("000", "深交所", "主板"),
    ("001", "深交所", "主板"),
    ("002", "深交所", "主板"),
    ("300", "深交所", "创业板"),
    ("301", "深交所", "创业板"),
)

#: 指标中文名（用于覆盖矩阵可读性；缺名字的指标直接显示英文 key）
METRIC_LABELS: Dict[str, str] = {
    "revenue": "营业收入",
    "net_profit": "净利润",
    "net_profit_attr": "归属于上市公司股东的净利润",
    "net_profit_deducted": "扣除非经常性损益后的净利润",
    "operating_cash_flow": "经营活动产生的现金流量净额",
    "total_assets": "总资产",
    "equity_attr": "归属于上市公司股东的净资产",
    "eps": "每股收益",
    "debt_ratio": "资产负债率",
    "rd_expense": "研发费用",
    "rd_investment": "研发投入",
    "rd_ratio": "研发投入占营业收入比例",
    "accounts_receivable": "应收账款",
    "inventory": "存货",
    "gross_margin": "毛利率",
    "risk_product_mix": "风险：产品销售结构",
    "risk_tech_edge": "风险：技术优势",
    "risk_downstream_demand": "风险：下游需求",
}

#: 覆盖矩阵里逐项检查的核心指标（TONIGHT_DATABASE_TASKS.md §3）
CORE_METRICS: tuple[str, ...] = (
    "revenue",
    "net_profit_attr",
    "operating_cash_flow",
    "total_assets",
    "eps",
)

#: 扩展指标（有则展示）
EXTRA_METRICS: tuple[str, ...] = (
    "net_profit",
    "net_profit_deducted",
    "equity_attr",
    "debt_ratio",
    "rd_expense",
    "rd_investment",
    "rd_ratio",
    "accounts_receivable",
    "inventory",
    "gross_margin",
)

PERIOD_RE = period_rank


def derive_exchange_board(code: str) -> Dict[str, str]:
    """按证券代码规则推导交易所与板块。"""
    for prefix, exchange, board in CODE_RULES:
        if code.startswith(prefix):
            return {"exchange": exchange, "board": board, "exchange_source": "code_rule"}
    return {"exchange": "未知", "board": "未知", "exchange_source": "unresolved"}


def build_catalog(conn: sqlite3.Connection, codes: Sequence[str]) -> Dict[str, Any]:
    """组装公司目录（全部数字来自数据库实测）。"""
    companies: List[Dict[str, Any]] = []
    for code in codes:
        name = COMPANY_NAMES.get(code, code)
        derived = derive_exchange_board(code)

        doc_count = conn.execute(
            "SELECT COUNT(*) FROM docs WHERE company_code = ?", (code,)
        ).fetchone()[0]
        valid_docs = conn.execute(
            "SELECT COUNT(*) FROM docs WHERE company_code = ? "
            "AND superseded = 0 AND parse_status = 'ok'",
            (code,),
        ).fetchone()[0]
        chunk_count = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE company_code = ?", (code,)
        ).fetchone()[0]
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE company_code = ?", (code,)
        ).fetchone()[0]

        metrics = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT metric FROM evidence WHERE company_code = ? "
                "AND metric IS NOT NULL ORDER BY metric",
                (code,),
            )
        ]
        periods = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT period FROM evidence WHERE company_code = ? "
                "AND period IS NOT NULL AND period <> ''",
                (code,),
            )
        ]
        periods.sort(key=PERIOD_RE, reverse=True)

        # 最新定期报告：只看年报/半年报/季报，且未 superseded、解析成功
        latest_rows = conn.execute(
            "SELECT report_period, published_at, document_type, file_name, source_url, "
            "       page_count, id "
            "FROM docs WHERE company_code = ? AND superseded = 0 AND parse_status = 'ok' "
            "AND document_type IN ('annual_report','semiannual_report','quarterly_report',"
            "'q1_report','q3_report') "
            "ORDER BY published_at DESC LIMIT 1",
            (code,),
        ).fetchone()

        latest_period = periods[0] if periods else None
        latest_report = None
        if latest_rows:
            latest_report = {
                "report_period": latest_rows["report_period"],
                "published_at": latest_rows["published_at"],
                "document_type": latest_rows["document_type"],
                "document_id": latest_rows["id"],
                "document_title": latest_rows["file_name"],
                "page_count": latest_rows["page_count"],
                "source_url": latest_rows["source_url"],
            }
            # ⚠️ 以**最新定期报告**的报告期为准，而不是 Evidence 里 period 排序的第一名。
            #   招股书里会有 `2026`（预测期）这类 period，按字符串排序会排在 `2026H1`
            #   前面，把"最新报告期"说成预测期 —— 那是错的。
            if latest_rows["report_period"]:
                latest_period = latest_rows["report_period"]

        review = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT review_status, COUNT(*) FROM evidence WHERE company_code = ? "
                "GROUP BY review_status",
                (code,),
            )
        }

        companies.append({
            "code": code,
            "name": name,
            **derived,
            "industry": None,
            "industry_source": "not_asserted",
            "available_metrics": metrics,
            "metric_count": len(metrics),
            "latest_report_period": latest_period,
            "latest_report": latest_report,
            "counts": {
                "documents": doc_count,
                "valid_documents": valid_docs,
                "chunks": chunk_count,
                "evidence": evidence_count,
            },
            "evidence_review_status": review,
        })

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "schema": "ask-the-company/companies.json@1",
        "note": (
            "exchange/board 由证券代码规则推导（exchange_source=code_rule），"
            "不是公告原文；industry 未作断言。所有计数为数据库实测值。"
        ),
        "companies": companies,
    }


def available_questions(conn: sqlite3.Connection, code: str) -> List[Dict[str, Any]]:
    """该公司当前能安全回答的财务问题（依据 Evidence 覆盖，不生成新问题）。"""
    out: List[Dict[str, Any]] = []
    questions = [
        ("revenue", "你的收入结构和变化是什么？", ["revenue"]),
        ("profit", "你最近真的赚钱吗？", ["net_profit_attr", "net_profit_deducted",
                                        "operating_cash_flow", "revenue"]),
        ("assets", "你的资产规模和资产负债情况如何？", ["total_assets", "debt_ratio"]),
        ("eps", "你的每股收益表现如何？", ["eps"]),
        ("rd", "你的研发投入情况如何？", ["rd_expense", "rd_investment", "rd_ratio"]),
        ("working_capital", "你的应收账款和存货情况如何？",
         ["accounts_receivable", "inventory"]),
    ]
    for qid, text, metrics in questions:
        present = []
        missing = []
        for metric in metrics:
            n = conn.execute(
                "SELECT COUNT(*) FROM evidence WHERE company_code = ? AND metric = ?",
                (code, metric),
            ).fetchone()[0]
            (present if n else missing).append(metric)
        out.append({
            "id": qid,
            "question": text,
            "required_metrics": metrics,
            "covered_metrics": present,
            "missing_metrics": missing,
            "answerable": not missing,
            "coverage": f"{len(present)}/{len(metrics)}",
        })
    return out


def build_matrix(conn: sqlite3.Connection, codes: Sequence[str]) -> str:
    """生成 Markdown 覆盖矩阵。"""
    lines: List[str] = []
    lines.append("# 四公司 Evidence 覆盖矩阵")
    lines.append("")
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("> 生成脚本：`build_company_catalog.py`")
    lines.append("")
    lines.append("矩阵里的数字是**该指标在 evidence 表里的条数**。")
    lines.append("`—` 表示该指标在这家公司的库内**没有证据** —— 这是缺口，"
                 "不是零值，不允许用模型常识补全。")
    lines.append("")

    all_metrics = list(CORE_METRICS) + list(EXTRA_METRICS)
    # 只有至少一家公司有的指标才展示
    counts: Dict[str, Dict[str, int]] = {}
    for code in codes:
        counts[code] = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT metric, COUNT(*) FROM evidence WHERE company_code = ? GROUP BY metric",
                (code,),
            )
        }

    header = "| 指标 | " + " | ".join(
        f"{COMPANY_NAMES.get(c, c)}<br>`{c}`" for c in codes) + " |"
    lines.append(header)
    lines.append("|---|" + "---:|" * len(codes))

    for metric in all_metrics:
        if not any(counts[c].get(metric) for c in codes):
            continue
        label = METRIC_LABELS.get(metric, metric)
        cells = []
        for code in codes:
            n = counts[code].get(metric)
            cells.append(str(n) if n else "—")
        star = " ★" if metric in CORE_METRICS else ""
        lines.append(f"| {label} (`{metric}`){star} | " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("★ = 必查核心指标。")
    lines.append("")

    lines.append("## 逐公司可回答问题")
    lines.append("")
    for code in codes:
        name = COMPANY_NAMES.get(code, code)
        lines.append(f"### {name}（{code}）")
        lines.append("")
        lines.append("| 问题 | 需要的指标 | 覆盖 | 可回答 | 缺口 |")
        lines.append("|---|---|---|---|---|")
        for q in available_questions(conn, code):
            missing = "、".join(q["missing_metrics"]) or "—"
            lines.append(
                f"| {q['question']} | {'、'.join(q['required_metrics'])} | "
                f"{q['coverage']} | {'✅' if q['answerable'] else '⬜'} | {missing} |"
            )
        lines.append("")

    lines.append("## 数据缺口清单")
    lines.append("")
    any_gap = False
    for code in codes:
        name = COMPANY_NAMES.get(code, code)
        gaps = [m for m in CORE_METRICS if not counts[code].get(m)]
        if gaps:
            any_gap = True
            lines.append(f"- {name}（{code}）缺核心指标：{'、'.join(gaps)}")
    if not any_gap:
        lines.append("- 四家公司均已覆盖全部 5 个必查核心指标。")
    lines.append("")
    lines.append("## 非结构化风险证据")
    lines.append("")
    for code in codes:
        risks = {m: n for m, n in counts[code].items() if m.startswith("risk_")}
        name = COMPANY_NAMES.get(code, code)
        if risks:
            detail = "、".join(f"{METRIC_LABELS.get(m, m)}×{n}" for m, n in sorted(risks.items()))
            lines.append(f"- {name}（{code}）：{detail}")
        else:
            lines.append(f"- {name}（{code}）：**无**结构化风险 Evidence（风险类问题"
                         "只能靠 chunks 检索，不得虚构）")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成四公司目录与覆盖矩阵")
    parser.add_argument("--db", required=True)
    parser.add_argument("--json", default=None, help="目录 JSON 输出路径")
    parser.add_argument("--matrix", default=None, help="覆盖矩阵 Markdown 输出路径")
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    json_path = args.json or os.path.join(os.path.dirname(db_path), "docs", "companies.json")
    matrix_path = args.matrix or os.path.join(
        os.path.dirname(db_path), "docs", "coverage_matrix.md")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        codes = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT company_code FROM docs ORDER BY company_code")
        ]
        ordered = [c for c in COMPANY_ORDER if c in codes]
        ordered += [c for c in codes if c not in ordered]

        catalog = build_catalog(conn, ordered)
        for company in catalog["companies"]:
            company["answerable_questions"] = available_questions(conn, company["code"])

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(catalog, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

        matrix = build_matrix(conn, ordered)
        os.makedirs(os.path.dirname(matrix_path), exist_ok=True)
        with open(matrix_path, "w", encoding="utf-8") as fh:
            fh.write(matrix)

        print(f"公司数: {len(catalog['companies'])}")
        for company in catalog["companies"]:
            print(f"  {company['code']} {company['name']} "
                  f"{company['exchange']}/{company['board']} "
                  f"docs={company['counts']['documents']} "
                  f"evidence={company['counts']['evidence']} "
                  f"metrics={company['metric_count']} "
                  f"latest={company['latest_report_period']}")
        print(f"\n已写入: {json_path}")
        print(f"已写入: {matrix_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
