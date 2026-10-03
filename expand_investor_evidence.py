# -*- coding: utf-8 -*-
"""从公告全文确定性抽取普通投资者主题 Evidence，写入新的候选库。

脚本永不原地修改输入库；每条新增 Evidence 都是对应 PDF 页面的连续原文，最长
200 字符，并保留 document_id/source_page/source_url 供人工回溯。
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "xray-backend"))

from investor_topics import INVESTOR_TOPICS, InvestorTopic  # noqa: E402


PAGE_MARKER_RE = re.compile(r"(?m)^--- 第(\d+)页 ---\s*$")
BOUNDARY_CHARS = "。！？；\n"
MAX_QUOTE_CHARS = 200
MIN_QUOTE_CHARS = 20
METHOD = "investor_topic_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def split_pages(text: str) -> list[tuple[int, str]]:
    markers = list(PAGE_MARKER_RE.finditer(text or ""))
    pages: list[tuple[int, str]] = []
    for index, marker in enumerate(markers):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        pages.append((int(marker.group(1)), text[start:end].strip()))
    return pages


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def squeeze(text: str) -> str:
    return "".join(str(text or "").split())


def _boundary_before(text: str, position: int) -> int:
    found = max(text.rfind(char, 0, position) for char in BOUNDARY_CHARS)
    return found + 1 if found >= 0 else 0


def _boundary_after(text: str, position: int) -> int:
    found = [text.find(char, position) for char in BOUNDARY_CHARS]
    found = [value for value in found if value >= 0]
    return min(found) + 1 if found else len(text)


def exact_quote(page_text: str, keyword: str, start_at: int = 0) -> tuple[str, int] | None:
    """取包含关键词的最短句段；过长时围绕关键词截取，仍保持页面连续原文。"""
    position = page_text.find(keyword, start_at)
    if position < 0:
        return None
    start = _boundary_before(page_text, position)
    end = _boundary_after(page_text, position + len(keyword))
    # 表格标题常独占一行，例如“（四）劳务外包情况”；继续带上后续数据行，
    # 否则只有标题，既达不到定性引文长度，也支撑不了金额/人数结论。
    short_heading = len(squeeze(page_text[start:end])) < MIN_QUOTE_CHARS
    target_length = 70 if short_heading else MIN_QUOTE_CHARS
    while len(squeeze(page_text[start:end])) < target_length and end < len(page_text):
        next_end = _boundary_after(page_text, end + 1)
        if next_end <= end:
            break
        end = next_end
    if end - start > MAX_QUOTE_CHARS:
        start = max(start, position - 70)
        end = min(end, start + MAX_QUOTE_CHARS)
        if position + len(keyword) > end:
            end = position + len(keyword)
            start = max(0, end - MAX_QUOTE_CHARS)
    quote = page_text[start:end].strip()
    if len(quote) > MAX_QUOTE_CHARS:
        quote = quote[:MAX_QUOTE_CHARS].rstrip()
    if len(squeeze(quote)) < MIN_QUOTE_CHARS:
        return None
    return quote, position + len(keyword)


def iter_topic_quotes(text: str, topic: InvestorTopic) -> Iterable[tuple[int, str]]:
    seen: set[str] = set()
    for page_number, page_text in split_pages(text):
        for keyword in topic.evidence_keywords:
            cursor = 0
            while True:
                found = exact_quote(page_text, keyword, cursor)
                if found is None:
                    break
                quote, cursor = found
                normalized = tidy(quote)
                if keyword in normalized and normalized not in seen:
                    seen.add(normalized)
                    yield page_number, quote


def _period(row: sqlite3.Row) -> str:
    report_period = str(row["report_period"] or "").strip()
    if report_period:
        return report_period
    published = str(row["published_at"] or "").strip()
    if re.fullmatch(r"20\d{6}", published):
        return f"{published[:4]}-{published[4:6]}-{published[6:]}"
    return published or "公告披露期"


def _ensure_meta(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")


def expand_database(source: Path, destination: Path, *, force: bool = False) -> dict[str, object]:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("输入库与输出库不能相同；禁止原地覆盖")
    if not source.exists():
        raise FileNotFoundError(source)
    if destination.exists() and not force:
        raise FileExistsError(f"输出库已存在：{destination}；如需重建请显式传 --force")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    os.chmod(destination, os.stat(destination).st_mode | stat.S_IWRITE)

    inserted_by_metric: dict[str, int] = {}
    with sqlite3.connect(destination) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM evidence WHERE method=?", (METHOD,))
        docs = list(con.execute(
            "SELECT id, company_code, file_name, document_type, published_at, report_period, "
            "source_url, text_content FROM docs "
            "WHERE superseded=0 AND parse_status='ok' AND length(text_content)>0 "
            "ORDER BY company_code, coalesce(published_at,'') DESC, id DESC"
        ))
        companies = sorted({str(row["company_code"]) for row in docs})
        for topic in INVESTOR_TOPICS:
            count = 0
            for company_code in companies:
                company_count = 0
                matching_docs = [
                    row for row in docs
                    if str(row["company_code"]) == company_code
                    and (not topic.document_types or str(row["document_type"] or "") in topic.document_types)
                    and any(keyword in str(row["text_content"] or "") for keyword in topic.evidence_keywords)
                ]
                for row in matching_docs:
                    for page_number, quote in iter_topic_quotes(str(row["text_content"]), topic):
                        con.execute(
                            "INSERT INTO evidence "
                            "(company_code, document_id, category, metric, period, value, unit, content, "
                            "source_page, source_quote, method, review_status, excluded, quote_repaired, original_quote) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,NULL)",
                            (
                                company_code,
                                int(row["id"]),
                                topic.category,
                                topic.metric,
                                _period(row),
                                None,
                                None,
                                f"{topic.label}：{tidy(quote)}"[:2000],
                                page_number,
                                quote,
                                METHOD,
                                "auto",
                            ),
                        )
                        company_count += 1
                        count += 1
                        if company_count >= topic.max_per_company:
                            break
                    if company_count >= topic.max_per_company:
                        break
            inserted_by_metric[topic.metric] = count
        _ensure_meta(con)
        total = sum(inserted_by_metric.values())
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('investor_evidence_method',?)", (METHOD,))
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('investor_evidence_count',?)", (str(total),))
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        con.commit()

    return {
        "destination": str(destination),
        "sha256": sha256_file(destination),
        "inserted": sum(inserted_by_metric.values()),
        "by_metric": inserted_by_metric,
        "integrity": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=str(ROOT / "cninfo.multicompany.next.db"))
    parser.add_argument("--dst", default=str(ROOT / "cninfo.multicompany.expanded.db"))
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的输出候选库")
    args = parser.parse_args()
    try:
        result = expand_database(Path(args.src), Path(args.dst), force=args.force)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"[PASS] 新增 Evidence: {result['inserted']}")
    print(f"[PASS] integrity_check: {result['integrity']}")
    print(f"[PASS] 输出: {result['destination']}")
    print(f"[PASS] SHA-256: {result['sha256']}")
    for metric, count in sorted(result["by_metric"].items()):
        print(f"  {metric}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
