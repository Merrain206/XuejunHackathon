"""db.py 测试：三个查询函数 + 缺失场景 + created_at 容错。"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import db
from tests.helpers import CODES


# ---------------------------------------------------------------------------
# get_stocks
# ---------------------------------------------------------------------------


def test_get_stocks_returns_all_codes_sorted_and_unique():
    stocks = db.get_stocks()
    assert stocks == sorted(set(stocks)), f"未去重或未排序: {stocks}"
    assert set(CODES) <= set(stocks), f"缺少测试公司: {stocks}"


# ---------------------------------------------------------------------------
# get_announcements
# ---------------------------------------------------------------------------


def test_get_announcements_returns_expected_fields():
    items = db.get_announcements("688583", days=None)
    assert len(items) == 5, f"688583 应有 5 条公告，实际 {len(items)}"

    required = {
        "id",
        "company_code",
        "file_name",
        "rel_path",
        "created_at",
        "created_date",
        "announce_date",
        "date_source",
        "page_count",
        "text_content",
        "text_length",
        "has_tables",
    }
    assert required <= set(items[0].keys()), f"缺字段: {required - set(items[0].keys())}"
    assert all(i["company_code"] == "688583" for i in items), "混入了其它公司的公告"


def test_window_filters_by_parsed_announce_date_not_created_at():
    """时间窗口必须按**文件名解析出的公告日期**过滤，而不是 created_at。

    测试库里 created_at 全部同一时刻（模拟真实库），所以如果还用 created_at
    过滤，days=30 和 days=None 的结果会一模一样 —— 这个断言就是防这个回归。
    """
    all_items = db.get_announcements("688583", days=None)
    assert len(all_items) == 5

    # created_at 无区分度 → 所有条目入库时间相同
    assert len({i["created_date"] for i in all_items}) == 1, "测试数据应模拟入库时间相同"

    # 但公告日期不同 → 窗口必须真的筛掉东西
    dated = [i for i in all_items if i["announce_date"]]
    assert len({i["announce_date"] for i in dated}) >= 3, "测试数据应有多个不同公告日期"

    narrow = db.get_announcements("688583", days=30)
    assert 0 < len(narrow) < len(all_items), (
        f"days=30 应筛掉部分公告，实际 {len(narrow)}/{len(all_items)}"
    )
    # 距今 800 天那条必须被 365 天窗口排除
    year_365 = db.get_announcements("688583", days=365)
    assert len(year_365) < len(all_items), "365 天窗口应筛掉 800 天前那条"


def test_announce_date_parsing_variants():
    """方案 B：文件名里的多种日期写法都要能解析。"""
    items = db.get_announcements("688583", days=None)

    # 至少有一条解析出完整日期（距今 20 天那条）
    with_full = [i for i in items if i["announce_date"] and i["date_source"] == db.ANNOUNCE_DATE_FROM_NAME]
    assert with_full, "应至少有一条从文件名解析出日期"
    newest = max(with_full, key=lambda i: i["announce_date"])
    gap = (date.today() - date.fromisoformat(newest["announce_date"])).days
    assert gap <= 30, f"最新公告应在 30 天内，实际 {gap} 天"

    # 无日期的公告必须保留，并标记 date_source
    undated = [i for i in items if not i["announce_date"]]
    assert undated, "应有一条无日期的公告"
    assert all(i["date_source"] == db.ANNOUNCE_DATE_UNKNOWN for i in undated)
    assert undated[0]["file_name"] in [
        i["file_name"] for i in db.get_announcements("688583", days=7)
    ], "无日期的公告在窄窗口下也必须保留"

    # 中文数字年月（二〇二五年三月）
    zh = next(
        (i for i in db.get_announcements("000001", days=None) if "二〇二五年" in i["file_name"]),
        None,
    )
    if zh is not None:
        assert zh["announce_date"] == "2025-03-01", zh["announce_date"]


def test_sorted_by_announce_date_desc_with_undated_last():
    items = db.get_announcements("688583", days=None)
    dated = [i["announce_date"] for i in items if i["announce_date"]]
    assert dated == sorted(dated, reverse=True), f"应按公告日期倒序: {dated}"
    # 无日期的排在最后
    tail = [i for i in items if not i["announce_date"]]
    if tail:
        assert items[-1]["announce_date"] is None, "日期未知的应排在最后"


def test_parse_announce_date_unit():
    """parse_announce_date 的单元行为（含中文数字与越界保护）。"""
    p = db.parse_announce_date
    assert p("2024年4月19日公告.pdf") == "2024-04-19"
    assert p("2025-07-15公告.pdf") == "2025-07-15"
    assert p("2025年7月修订.pdf") == "2025-07-01"
    assert p("二〇二五年三月修订.pdf") == "2025-03-01"
    assert p("2024年年度报告.pdf") == "2024-01-01"
    assert p("没有日期的公告.pdf") is None
    assert p(None) is None
    assert p("1999年旧文件.pdf") is None, "超出合理年份范围应判为误匹配"
    assert p("2024年13月.pdf") == "2024-01-01", "非法月份应退回按年解析"


def test_parse_announce_date_compact_yyyymmdd():
    """紧凑 YYYYMMDD 前缀（真实库 cninfo.db 文件名的主格式）。

    回归保护：这里三个断言对应一个真实 bug —— 曾漏掉紧凑写法，
    导致文件名里的 YYYYMMDD 被忽略、退化成从标题里的「2026年」按年解析，
    全库 1905 条公告的日期一律变成 YYYY-01-01，排序与时间窗口全部失真。
    """
    p = db.parse_announce_date
    assert p("20260429_2026年一季度报告.pdf") == "2026-04-29"
    assert p("20260829_2026年半年度报告.pdf") == "2026-08-29"
    assert p("20230104_关于获得临床试验批准通知书的公告.pdf") == "2023-01-04"
    # 紧凑写法必须优先于标题里的年份
    assert (
        p("20260829_关于2026年1-6月募集资金存放、管理与实际使用情况的专项报告.pdf")
        == "2026-08-29"
    ), "紧凑前缀应胜过标题中的年份"


def test_parse_announce_date_not_fooled_by_month_range():
    """「1-6月」「1-9月」是月份区间，不是「1月6日」。

    回归保护：真实库里这类半年/季度表述极常见，曾被误解析成 1 月 6 日之类的假日期。
    """
    p = db.parse_announce_date
    # 区间不应被读成「某月某日」
    assert p("2026年1-6月经营情况公告.pdf") == "2026-01-01"
    assert p("关于2026年1-9月经营情况的公告.pdf") == "2026-01-01"
    # 但真正带「日」的写法仍要正常解析
    assert p("2023年年度报告（2026年9月12日）.pdf") == "2026-09-12"


def test_parse_announce_date_numeric_needs_separators():
    """纯数字日期必须有分隔符；无分隔的 8 位数按 YYYYMMDD 处理。"""
    p = db.parse_announce_date
    assert p("2024-04-19公告.pdf") == "2024-04-19"
    assert p("2024/04/19公告.pdf") == "2024-04-19"
    assert p("20240419公告.pdf") == "2024-04-19"
    # 非法紧凑日期不能被当成合法日期，也不能误取内部片段
    assert p("20260230坏日期.pdf") in (None, "2026-01-01")


def test_normalize_cn_digits():
    assert db.normalize_cn_digits("二〇二五年") == "2025年"
    assert db.normalize_cn_digits("２０２５") == "2025"
    assert db.normalize_cn_digits("abc") == "abc"


def test_limit_and_body_truncation():
    one = db.get_announcements("688583", days=None, limit=1)
    assert len(one) == 1

    short = db.get_announcements("688583", days=None, limit=1, body_chars=10)[0]
    assert len(short["text_content"]) == 10
    assert int(short["text_length"]) > 10, "text_length 应记录原文长度"

    full = db.get_announcements("688583", days=None, limit=1, body_chars=None)[0]
    assert len(full["text_content"]) == int(full["text_length"]), "body_chars=None 不应截断"


def test_unknown_company_returns_empty_not_error():
    assert db.get_announcements("999999") == []


def test_empty_stock_code_raises():
    with pytest.raises(ValueError):
        db.get_announcements("")


def test_undated_rows_are_kept():
    """无法解析日期的行必须保留（否则证据会凭空消失）。"""
    items = db.get_announcements("600036", days=None)
    names = [i["file_name"] for i in items]
    assert "坏日期公告.pdf" in names, f"坏日期公告被丢弃了: {names}"

    bad = next(i for i in items if i["file_name"] == "坏日期公告.pdf")
    # created_at 解析不出 → created_date 为 None
    assert bad["created_date"] is None
    # 文件名也解析不出 → announce_date 为 None，标记为日期未知
    assert bad["announce_date"] is None
    assert bad["date_source"] == db.ANNOUNCE_DATE_UNKNOWN

    # 即使加了窗口过滤，也不能把它误删
    recent = db.get_announcements("600036", days=7)
    assert "坏日期公告.pdf" in [i["file_name"] for i in recent]


# ---------------------------------------------------------------------------
# created_at 容错解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_prefix",
    [
        ("2024-04-19 09:30:00", "2024-04-19"),
        ("2024-04-19T14:05:00", "2024-04-19"),
        ("2024-04-19T14:05:00Z", "2024-04-19"),
        ("2024-04-19", "2024-04-19"),
        ("2024/04/19", "2024-04-19"),
        ("20220101", "2022-01-01"),
    ],
)
def test_parse_created_at_formats(raw, expected_prefix):
    parsed = db.parse_created_at(raw)
    assert parsed is not None, f"应能解析 {raw!r}"
    assert parsed.date().isoformat() == expected_prefix


def test_parse_created_at_yyyymmdd_not_epoch():
    """8 位数字必须按 YYYYMMDD 解析，而不是当成 Unix 秒（曾经的真 bug）。"""
    assert db.parse_created_at("20220101") == db.parse_created_at("2022-01-01")


def test_parse_created_at_unix_timestamps():
    assert db.parse_created_at(1713500000) is not None
    assert db.parse_created_at("1713500000") is not None


def test_parse_created_at_invalid_returns_none_without_raising():
    for raw in (None, "", "昨天", "not-a-date", "abc123"):
        assert db.parse_created_at(raw) is None, f"{raw!r} 应解析为 None"


# ---------------------------------------------------------------------------
# search_announcements
# ---------------------------------------------------------------------------


def test_search_finds_across_companies():
    hits = db.search_announcements("营业收入")
    codes = {h["company_code"] for h in hits}
    assert len(codes) >= 2, f"应跨公司命中，实际 {codes}"
    assert all("营业收入" in (h["text_content"] or "") or "营业收入" in (h["file_name"] or "")
               for h in hits)


def test_search_can_match_file_name():
    hits = db.search_announcements("诉讼")
    assert any("诉讼" in (h["file_name"] or "") for h in hits)


def test_search_no_match_returns_empty():
    assert db.search_announcements("量子计算机") == []


def test_search_escapes_like_wildcards():
    """'45%' 必须被当作字面量，而不是通配符（否则会匹配全库）。"""
    hits = db.search_announcements("45%")
    assert len(hits) == 1, f"应只命中 1 条，实际 {len(hits)}"
    assert hits[0]["file_name"].startswith("毛利率说明"), hits[0]["file_name"]


def test_search_rejects_short_keyword():
    for bad in ("", "营", " "):
        with pytest.raises(ValueError):
            db.search_announcements(bad)


def test_search_rejects_bad_limit():
    with pytest.raises(ValueError):
        db.search_announcements("营业收入", limit=0)


def test_search_respects_limit():
    assert len(db.search_announcements("营业收入", limit=1)) == 1


# ---------------------------------------------------------------------------
# 只读 + 缺失场景
# ---------------------------------------------------------------------------


def test_database_is_opened_readonly():
    con = db.connect()
    try:
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            con.execute("INSERT INTO docs (company_code) VALUES ('X')")
        assert "readonly" in str(excinfo.value).lower()
    finally:
        con.close()


def test_missing_file_raises_clear_error(monkeypatch, scratch_dir):
    monkeypatch.setattr(db.settings, "DB_PATH", str(scratch_dir / "nope.db"))
    monkeypatch.setattr(db.settings, "DB_PATH_STRICT", True, raising=False)
    with pytest.raises(db.DatabaseNotReadyError) as excinfo:
        db.get_stocks()
    assert "不存在" in str(excinfo.value)


def test_empty_file_raises_clear_error(monkeypatch, scratch_dir):
    empty = scratch_dir / "empty.db"
    monkeypatch.setattr(db.settings, "DB_PATH_STRICT", True, raising=False)
    empty.write_bytes(b"")
    monkeypatch.setattr(db.settings, "DB_PATH", str(empty))
    with pytest.raises(db.DatabaseNotReadyError) as excinfo:
        db.get_stocks()
    assert "为空" in str(excinfo.value)


def test_missing_docs_table_lists_actual_tables(monkeypatch, scratch_dir):
    other = scratch_dir / "other.db"
    monkeypatch.setattr(db.settings, "DB_PATH_STRICT", True, raising=False)
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE unrelated (id INTEGER)")
    con.commit()
    con.close()

    monkeypatch.setattr(db.settings, "DB_PATH", str(other))
    with pytest.raises(db.DatabaseNotReadyError) as excinfo:
        db.get_stocks()
    message = str(excinfo.value)
    assert "没有 docs 表" in message
    assert "unrelated" in message, "应列出实际存在的表名"


def test_missing_columns_reports_which_ones(monkeypatch, scratch_dir):
    partial = scratch_dir / "partial.db"
    monkeypatch.setattr(db.settings, "DB_PATH_STRICT", True, raising=False)
    con = sqlite3.connect(partial)
    con.execute("CREATE TABLE docs (id INTEGER, company_code TEXT)")
    con.commit()
    con.close()

    monkeypatch.setattr(db.settings, "DB_PATH", str(partial))
    with pytest.raises(db.DatabaseNotReadyError) as excinfo:
        db.get_stocks()
    message = str(excinfo.value)
    assert "缺少列" in message
    assert "text_content" in message


def test_db_status_never_raises(monkeypatch, scratch_dir):
    monkeypatch.setattr(db.settings, "DB_PATH", str(scratch_dir / "missing.db"))
    monkeypatch.setattr(db.settings, "DB_PATH_STRICT", True, raising=False)
    status = db.db_status()
    assert status["ready"] is False
    assert status["error"]


def test_count_and_summary():
    assert db.count_announcements() >= 10
    assert db.count_announcements("688583") == 5

    summary = db.get_company_summary("688583")
    assert summary is not None
    assert summary["announcement_count"] == 5
    assert summary["recent_titles"]
    # 日期区间基于解析出的公告日期；并报出有多少条日期未知
    assert summary["first_announcement_date"] and summary["last_announcement_date"]
    assert summary["undated_count"] == 1, summary
    assert db.get_company_summary("999999") is None
