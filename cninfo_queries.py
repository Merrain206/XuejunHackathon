# -*- coding: utf-8 -*-
"""cninfo_queries.py — 四公司 Evidence / Chunks 的**只读**参数化查询层。

对应 `docs/TONIGHT_DATABASE_TASKS.md` §4：给后端一组"可以照抄"的查询，
让动态问答先检索候选 Evidence，再交给模型组织答案。

## 硬约束（全部由本模块保证）

1. **只读**：一律 `file:...?mode=ro` 打开，误写直接报错，不会改坏演示库。
2. **参数化**：任何用户输入都走 `?` 占位符，绝不拼接进 SQL 字符串。
3. **公司隔离**：每条查询都必须带 `company_code = ?`；FTS 命中后再 JOIN
   `chunks`/`docs` 并**再次**限制 `company_code`，避免跨公司串数据。
4. **默认过滤** `docs.parse_status='ok' AND docs.superseded=0`。
5. **不生成** Answer / Claim / Signal / Chart，也不调用 LLM。

## 五种查询（§4 要求）

| 函数 | 用途 |
|---|---|
| `latest_evidence(company, metric)` | 按公司 + 指标取最新 Evidence |
| `recent_periods(company, metrics)` | 按公司 + 多指标取最近若干报告期 |
| `page_text(document_id, page)` | 按 document + 页码取整页原文 |
| `search_chunks(company, keyword)` | 按公司 + 关键词检索 chunks |
| `evidence_with_document(...)` | Evidence JOIN docs，返回标题/URL/页数/报告期 |

命令行自查：

    python cninfo_queries.py --db cninfo.multicompany.next.db
    python cninfo_queries.py --db cninfo.multicompany.next.db --company 600570 --keyword 营业收入
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: 支持的四家公司（固定顺序，避免前端展示顺序漂移）
COMPANY_CODES: tuple[str, ...] = ("000066", "300558", "600570", "688583")

#: 后端动态检索关心的指标（与 TONIGHT_BACKEND_TASKS.md §2.2 对齐）
FINANCIAL_METRICS: tuple[str, ...] = (
    "revenue",
    "net_profit",
    "net_profit_attr",
    "net_profit_deducted",
    "operating_cash_flow",
    "total_assets",
    "eps",
    "debt_ratio",
    "rd_expense",
    "rd_investment",
    "rd_ratio",
    "accounts_receivable",
    "inventory",
    "equity_attr",
    "gross_margin",
)

#: 有效文档过滤条件（字符串常量，永不拼接用户输入）
VALID_DOC = "d.parse_status = 'ok' AND d.superseded = 0"

#: 被隔离证据的过滤条件。
#:
#: `repair_evidence_quotes.py` 会把无法支撑回答的记录标成 `evidence.excluded = 1`
#: （当前是 3 条 `period` 为空的规则抽取记录）。默认查询一律排除它们，
#: 这样后端即使忘记加条件也不会把不可用证据喂给模型。
#: 老库没有 `excluded` 列时，`_excluded_filter()` 会自动退化，不会报错。
NOT_EXCLUDED = "COALESCE(e.excluded, 0) = 0"

#: 报告期排序权重：同一年内 FY/年度 < Q3 < H1 < Q1 之外的规则见 `period_rank`
_PERIOD_RE = re.compile(r"^(\d{4})\s*(H1|H2|Q1|Q2|Q3|Q4|FY)?$", re.IGNORECASE)
_PERIOD_WEIGHT = {"Q1": 0.1, "H1": 0.5, "Q2": 0.2, "Q3": 0.7, "Q4": 0.8, "FY": 0.9, "": 0.9}

#: 检索关键词的最小长度（防止单字把全库匹配出来）
MIN_KEYWORD_LENGTH = 2


class QueryError(ValueError):
    """参数不合法（空公司代码、负 limit、过短关键词等）。"""


def connect_ro(db_path: str) -> sqlite3.Connection:
    """以只读方式打开数据库。库不存在时抛 FileNotFoundError。"""
    path = os.path.abspath(db_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"数据库不存在：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _evidence_filters(conn: sqlite3.Connection) -> str:
    """Evidence 查询的固定过滤条件（含"排除已隔离"的能力探测）。"""
    if "excluded" in _evidence_columns(conn):
        return f"{VALID_DOC} AND {NOT_EXCLUDED}"
    return VALID_DOC


def _evidence_columns(conn: sqlite3.Connection) -> set:
    """evidence 表的真实列名（老库可能没有 excluded）。"""
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(evidence)")}
    except sqlite3.Error:
        return set()


def _require_company(company_code: Optional[str]) -> str:
    code = (company_code or "").strip()
    if not code:
        raise QueryError("company_code 不能为空（每次查询都必须限定公司）")
    return code


def _require_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise QueryError(f"limit 必须是正整数，收到 {limit!r}")
    return limit


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % 把全库匹配出来。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def period_rank(period: Optional[str]) -> float:
    """把报告期字符串折成可排序的数值。

    `2026H1` → 2026.5，`2025FY` / `2025` → 2025.9，`2024Q3` → 2024.7。
    解析不出来的排最前（0.0），这样它们不会冒充"最新报告期"。
    """
    text = (period or "").strip().upper()
    if not text:
        return 0.0
    match = _PERIOD_RE.match(text)
    if not match:
        # `2024H1` 之类以外还有 `2024年` 这种写法，退一步只取年份
        year_match = re.match(r"^(\d{4})", text)
        return float(year_match.group(1)) if year_match else 0.0
    year, suffix = int(match.group(1)), (match.group(2) or "")
    return year + _PERIOD_WEIGHT.get(suffix.upper(), 0.9)


# ---------------------------------------------------------------------------
# 查询 1：按 company_code + metric 取最新 Evidence
# ---------------------------------------------------------------------------


def latest_evidence(
    conn: sqlite3.Connection, company_code: str, metric: str, *, limit: int = 2
) -> List[Dict[str, Any]]:
    """某公司某指标的最新几条 Evidence（JOIN docs，含标题 / URL / 页数）。

    报告期排序用 `period_rank` 的 SQL 等价式（见 `_PERIOD_RANK_SQL`）。
    """
    code = _require_company(company_code)
    _require_limit(limit)
    if not metric or not str(metric).strip():
        raise QueryError("metric 不能为空")

    sql = f"""
        SELECT e.id, e.company_code, e.document_id, e.metric, e.period, e.value, e.unit,
               e.content, e.source_page, e.source_quote, e.method, e.review_status,
               d.file_name AS document_title, d.document_type, d.report_period,
               d.published_at, d.page_count, d.source_url
        FROM evidence e
        JOIN docs d ON d.id = e.document_id
        WHERE e.company_code = ? AND e.metric = ? AND {_evidence_filters(conn)}
        ORDER BY {_PERIOD_RANK_SQL} DESC, e.id DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (code, str(metric).strip(), limit)).fetchall()
    return [_evidence_row(r) for r in rows]


#: `period_rank()` 的 SQL 版本：`2026H1` → 2026.5，`2025FY` → 2025.9。
#: 只依赖 substr，不拼接任何外部输入。
_PERIOD_RANK_SQL = """
    (CASE
        WHEN e.period IS NULL OR e.period = '' THEN 0.0
        ELSE CAST(substr(e.period, 1, 4) AS REAL)
             + CASE upper(substr(e.period, 5))
                   WHEN 'Q1' THEN 0.1 WHEN 'Q2' THEN 0.2 WHEN 'Q3' THEN 0.7
                   WHEN 'Q4' THEN 0.8 WHEN 'H1' THEN 0.5 WHEN 'H2' THEN 0.6
                   WHEN 'FY' THEN 0.9 ELSE 0.9 END
     END)
"""


# ---------------------------------------------------------------------------
# 查询 2：按 company_code + 多个 metric 取最近若干报告期
# ---------------------------------------------------------------------------


def recent_periods(
    conn: sqlite3.Connection,
    company_code: str,
    metrics: Sequence[str],
    *,
    periods: int = 4,
) -> List[Dict[str, Any]]:
    """某公司多个指标、最近 `periods` 个报告期的 Evidence。

    用于"收入/利润/现金流放在一起看趋势"这类问题。返回按报告期倒序、
    同报告期内按指标分组，调用方自己决定怎么配对比期。
    """
    code = _require_company(company_code)
    _require_limit(periods)
    wanted = [str(m).strip() for m in (metrics or []) if str(m).strip()]
    if not wanted:
        raise QueryError("metrics 不能为空")

    placeholders = ", ".join("?" for _ in wanted)
    # 先取该公司这些指标出现过的报告期（按 rank 倒序），只保留最近 N 个
    period_rows = conn.execute(
        f"""
        SELECT e.period AS period, {_PERIOD_RANK_SQL} AS rank
        FROM evidence e
        JOIN docs d ON d.id = e.document_id
        WHERE e.company_code = ? AND e.metric IN ({placeholders}) AND {_evidence_filters(conn)}
          AND e.period IS NOT NULL AND e.period <> ''
        GROUP BY e.period
        ORDER BY rank DESC
        LIMIT ?
        """,
        (code, *wanted, periods),
    ).fetchall()
    chosen = [r["period"] for r in period_rows]
    if not chosen:
        return []

    period_placeholders = ", ".join("?" for _ in chosen)
    rows = conn.execute(
        f"""
        SELECT e.id, e.company_code, e.document_id, e.metric, e.period, e.value, e.unit,
               e.content, e.source_page, e.source_quote, e.method, e.review_status,
               d.file_name AS document_title, d.document_type, d.report_period,
               d.published_at, d.page_count, d.source_url
        FROM evidence e
        JOIN docs d ON d.id = e.document_id
        WHERE e.company_code = ? AND e.metric IN ({placeholders})
          AND e.period IN ({period_placeholders}) AND {_evidence_filters(conn)}
        ORDER BY {_PERIOD_RANK_SQL} DESC, e.metric, e.id DESC
        """,
        (code, *wanted, *chosen),
    ).fetchall()
    return [_evidence_row(r) for r in rows]


# ---------------------------------------------------------------------------
# 查询 3：按 document_id + source_page 取完整页文本
# ---------------------------------------------------------------------------


def page_text(
    conn: sqlite3.Connection, document_id: int, page_number: int, *, company_code: str
) -> Dict[str, Any]:
    """取某文档某页的完整原文。

    页码是 **PDF 查看器页码**。两种来源都试：
      1. `chunks` 表（年报/半年报按页切过 chunk）；
      2. `docs.text_content` 里的 `--- 第N页 ---` 页界标记（招股书只有这个）。

    `company_code` 是必填的：防止拿到别的公司的文档页码。
    """
    code = _require_company(company_code)
    if not isinstance(document_id, int) or isinstance(document_id, bool):
        raise QueryError("document_id 必须是整数")
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise QueryError("page_number 必须是 >= 1 的整数")

    doc = conn.execute(
        f"SELECT d.id, d.company_code, d.file_name, d.page_count, d.text_content, d.source_url "
        f"FROM docs d WHERE d.id = ? AND d.company_code = ? AND {VALID_DOC}",
        (document_id, code),
    ).fetchone()
    if doc is None:
        return {"found": False, "reason": "文档不存在、不属于该公司，或未通过有效文档过滤"}

    chunk_rows = conn.execute(
        "SELECT content FROM chunks WHERE document_id = ? AND company_code = ? "
        "AND page_number = ? ORDER BY chunk_index",
        (document_id, code, page_number),
    ).fetchall()
    text = "\n".join(str(r["content"] or "") for r in chunk_rows)
    source = "chunks"

    if not text:
        text = _page_from_markers(doc["text_content"], page_number)
        source = "page_markers" if text else "none"

    return {
        "found": bool(text),
        "company_code": code,
        "document_id": document_id,
        "document_title": doc["file_name"],
        "page_count": doc["page_count"],
        "source_url": doc["source_url"],
        "page_number": page_number,
        "text": text,
        "text_source": source,
        "text_length": len(text),
    }


def _page_from_markers(text_content: Optional[str], page_number: int) -> str:
    """从 `docs.text_content` 的页界标记里取某一页。"""
    if not text_content:
        return ""
    marker = re.compile(r"^---\s*第(\d+)页\s*---\s*$")
    current: Optional[int] = None
    buf: List[str] = []
    for line in text_content.split("\n"):
        match = marker.match(line)
        if match:
            if current == page_number:
                return "\n".join(buf)
            current = int(match.group(1))
            buf = []
        elif current is not None:
            buf.append(line)
    return "\n".join(buf) if current == page_number else ""


# ---------------------------------------------------------------------------
# 查询 4：按 company_code + 关键词检索 chunks
# ---------------------------------------------------------------------------


def search_chunks(
    conn: sqlite3.Connection,
    company_code: str,
    keyword: str,
    *,
    limit: int = 10,
    prefer_fts: bool = True,
) -> Dict[str, Any]:
    """按公司检索 chunks 原文，返回命中的分页片段。

    优先用 `chunks_fts`（trigram），**失败或为空时自动降级**为参数化 `LIKE`
    —— 稳定库里 `chunks_fts` 只覆盖 801/2845，不能假设它可用。

    公司隔离：FTS 命中后 JOIN `chunks` 并再次限制 `company_code`。

    :returns: {"mode": "fts"|"like", "elapsed_ms": float, "hits": [...]}
    """
    code = _require_company(company_code)
    _require_limit(limit)
    text = (keyword or "").strip()
    if len(text) < MIN_KEYWORD_LENGTH:
        raise QueryError(f"关键词至少 {MIN_KEYWORD_LENGTH} 个字符，收到 {keyword!r}")

    started = time.perf_counter()
    if prefer_fts and _fts_ready(conn):
        try:
            rows = conn.execute(
                f"""
                SELECT c.id, c.document_id, c.company_code, c.page_number, c.chunk_index,
                       c.content, d.file_name AS document_title, d.document_type,
                       d.report_period, d.published_at, d.page_count, d.source_url
                FROM chunks_fts f
                JOIN chunks c ON c.id = f.rowid
                JOIN docs d ON d.id = c.document_id
                WHERE chunks_fts MATCH ? AND c.company_code = ? AND {VALID_DOC}
                ORDER BY c.document_id DESC, c.page_number, c.chunk_index
                LIMIT ?
                """,
                (text, code, limit),
            ).fetchall()
            return {
                "mode": "fts",
                "keyword": text,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "hits": [_chunk_row(r) for r in rows],
            }
        except sqlite3.Error:
            # FTS 表结构异常 / trigram 不支持 → 落到 LIKE
            pass

    pattern = f"%{_escape_like(text)}%"
    rows = conn.execute(
        f"""
        SELECT c.id, c.document_id, c.company_code, c.page_number, c.chunk_index,
               c.content, d.file_name AS document_title, d.document_type,
               d.report_period, d.published_at, d.page_count, d.source_url
        FROM chunks c
        JOIN docs d ON d.id = c.document_id
        WHERE c.company_code = ? AND c.content LIKE ? ESCAPE '\\' AND {VALID_DOC}
        ORDER BY c.document_id DESC, c.page_number, c.chunk_index
        LIMIT ?
        """,
        (code, pattern, limit),
    ).fetchall()
    return {
        "mode": "like",
        "keyword": text,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "hits": [_chunk_row(r) for r in rows],
    }


def _fts_ready(conn: sqlite3.Connection) -> bool:
    """`chunks_fts` 是否存在且非空。"""
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "chunks_fts" not in tables:
            return False
        return conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] > 0
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------------------
# 查询 5：Evidence JOIN docs，返回标题 / URL / 页数 / 报告期 / review_status
# ---------------------------------------------------------------------------


def evidence_with_document(
    conn: sqlite3.Connection,
    company_code: str,
    *,
    metrics: Optional[Iterable[str]] = None,
    document_id: Optional[int] = None,
    review_status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Evidence 主查询：JOIN docs 补齐标题、URL、页数、报告期与核验状态。

    这是给后端"组装候选 Evidence"用的总入口：所有过滤条件都是可选参数，
    但 `company_code` 是必填。
    """
    code = _require_company(company_code)
    _require_limit(limit)

    where = ["e.company_code = ?", _evidence_filters(conn)]
    params: List[Any] = [code]

    if metrics:
        wanted = [str(m).strip() for m in metrics if str(m).strip()]
        if wanted:
            where.append(f"e.metric IN ({', '.join('?' for _ in wanted)})")
            params.extend(wanted)
    if document_id is not None:
        if not isinstance(document_id, int) or isinstance(document_id, bool):
            raise QueryError("document_id 必须是整数")
        where.append("e.document_id = ?")
        params.append(document_id)
    if review_status:
        where.append("e.review_status = ?")
        params.append(str(review_status).strip())

    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT e.id, e.company_code, e.document_id, e.metric, e.period, e.value, e.unit,
               e.content, e.source_page, e.source_quote, e.method, e.review_status,
               d.file_name AS document_title, d.document_type, d.report_period,
               d.published_at, d.page_count, d.source_url
        FROM evidence e
        JOIN docs d ON d.id = e.document_id
        WHERE {' AND '.join(where)}
        ORDER BY {_PERIOD_RANK_SQL} DESC, e.metric, e.id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_evidence_row(r) for r in rows]


# ---------------------------------------------------------------------------
# 行 → dict
# ---------------------------------------------------------------------------


def _evidence_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Evidence + docs 的一行转成 JSON 友好 dict。

    `source_page` 与 `source_url` **分开**返回：URL 不带 fragment，
    页码由前端拼 `#page=N`（见 AGENTS.md 的契约约定）。
    """
    item = {key: row[key] for key in row.keys()}
    item["source_url_has_fragment"] = "#page=" in (item.get("source_url") or "")
    item["source_url_is_https"] = str(item.get("source_url") or "").startswith("https://")
    return item


def _chunk_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


# ---------------------------------------------------------------------------
# 命令行自查
# ---------------------------------------------------------------------------


def _demo(db_path: str, company: Optional[str], keyword: str) -> int:
    conn = connect_ro(db_path)
    codes = [company] if company else list(COMPANY_CODES)
    try:
        print(f"数据库: {os.path.abspath(db_path)}")
        print("\n### 查询 1：按 company + metric 取最新 Evidence")
        for code in codes:
            rows = latest_evidence(conn, code, "revenue", limit=1)
            for r in rows:
                print(f"  {code} revenue {r['period']} value={r['value']} "
                      f"page={r['source_page']} title={str(r['document_title'])[:40]}")

        print("\n### 查询 2：按 company + 多指标取最近报告期")
        for code in codes:
            rows = recent_periods(
                conn, code, ["revenue", "net_profit_attr", "operating_cash_flow"], periods=2
            )
            periods = sorted({r["period"] for r in rows if r["period"]}, reverse=True)
            print(f"  {code}: 取到 {len(rows)} 条，报告期 {periods}")

        print("\n### 查询 3：按 document + page 取整页原文")
        for code in codes:
            rows = latest_evidence(conn, code, "revenue", limit=1)
            if not rows:
                continue
            r = rows[0]
            page = page_text(conn, r["document_id"], r["source_page"], company_code=code)
            print(f"  {code} doc={r['document_id']} page={r['source_page']} "
                  f"found={page['found']} source={page.get('text_source')} "
                  f"len={page.get('text_length')}")

        print(f"\n### 查询 4：按 company + 关键词检索 chunks（keyword={keyword!r}）")
        for code in codes:
            result = search_chunks(conn, code, keyword, limit=3)
            codeset = {h["company_code"] for h in result["hits"]}
            leak = codeset - {code}
            print(f"  {code}: mode={result['mode']} hits={len(result['hits'])} "
                  f"{result['elapsed_ms']}ms 跨公司={sorted(leak) or '无'}")

        print("\n### 查询 5：Evidence JOIN docs")
        for code in codes:
            rows = evidence_with_document(conn, code, limit=2)
            print(f"  {code}: {len(rows)} 条")
            for r in rows:
                print(f"     #{r['id']} {r['metric']} {r['period']} "
                      f"review={r['review_status']} url_fragment={r['source_url_has_fragment']}")
    finally:
        conn.close()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="cninfo 只读参数化查询自查")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--company", default=None, help="只查某一家公司")
    parser.add_argument("--keyword", default="营业收入", help="检索关键词")
    args = parser.parse_args(argv)
    return _demo(args.db, args.company, args.keyword)


if __name__ == "__main__":
    sys.exit(main())
