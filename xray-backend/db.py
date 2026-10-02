"""X-Ray 企业穿透分析 · 唯一的数据访问层。

数据源：`data/cninfo.db` 的 **docs** 表（从 PDF 提取的公告原文）。

真实 schema（严格按此编写，不猜测任何列名）：

    docs(
        id,                -- 自增主键
        company_code,      -- 公司代码（即股票代码，无需从文件名解析）
        file_name,         -- 文件名（当标题用）
        rel_path,          -- 相对路径
        page_count,        -- 页数
        text_content,      -- ★ 公告正文（分析主要依据）
        tables_json,       -- 表格（可能为空）
        created_at         -- 入库时间（用于时间窗口过滤 / 展示日期）
    )

设计原则：
  * **只读**：一律 `mode=ro` 打开，绝不写库；
  * **不写任何业务表**（结果落 `cache/` 与 `logs/`，由 analyzer / 跑批负责）；
  * 库或表不存在时抛 `DatabaseNotReadyError`，**绝不静默返回空**；
  * `created_at` 格式未知 → 用容错解析（ISO / 空格分隔 / 仅日期 / Unix 时间戳），
    解析不出来的行**保留而不是丢弃**，并在日志里说明。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from config import settings

logger = logging.getLogger(__name__)

#: docs 表的真实列名（供 SELECT 使用；与用户提供的 schema 完全一致）
DOCS_COLUMNS: tuple[str, ...] = (
    "id",
    "company_code",
    "file_name",
    "rel_path",
    "page_count",
    "text_content",
    "tables_json",
    "created_at",
)

#: 查询时实际取出的列（顺序即返回 dict 的键顺序）
_SELECT_COLUMNS = ", ".join(f'"{c}"' for c in DOCS_COLUMNS)

#: 检索关键词的最小长度（避免「的」「了」这种单字把全库都匹配出来）
MIN_KEYWORD_LENGTH = 2


class DatabaseNotReadyError(RuntimeError):
    """数据库文件缺失、不可读，或缺少 docs 表。

    单独定义一个异常类型，是为了让上层（API）能把它翻译成清晰的用户提示，
    而不是变成一个没有信息量的 500。
    """


# ---------------------------------------------------------------------------
# 连接与就绪性检查
# ---------------------------------------------------------------------------


def db_path() -> Path:
    """cninfo.db 的绝对路径（来自 config.settings.DB_PATH）。"""
    return settings.db_file


def _require_db() -> Path:
    """校验数据库文件存在且可读；否则抛 DatabaseNotReadyError。"""
    path = db_path()
    if not path.exists():
        raise DatabaseNotReadyError(
            f"数据库文件不存在：{path}\n"
            f"请把 cninfo.db 放到该位置，或在 .env 里设置 DB_PATH 指向实际文件。"
        )
    if not path.is_file():
        raise DatabaseNotReadyError(f"DB_PATH 不是文件：{path}")
    if path.stat().st_size == 0:
        raise DatabaseNotReadyError(f"数据库文件为空：{path}")
    return path


def connect() -> sqlite3.Connection:
    """以**只读**方式打开数据库。

    用 URI 的 `mode=ro` 而不是普通 connect()：任何误写会直接报错，
    而不是悄悄改坏演示用的数据库。
    """
    path = _require_db()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error as exc:
        raise DatabaseNotReadyError(f"无法打开数据库 {path}：{exc}") from exc
    con.row_factory = sqlite3.Row
    return con


def _ensure_docs_table(con: sqlite3.Connection) -> None:
    """确认 docs 表存在且列齐全；缺列时给出明确到列名的报错。"""
    try:
        rows = con.execute("PRAGMA table_info(docs)").fetchall()
    except sqlite3.Error as exc:
        raise DatabaseNotReadyError(f"读取 docs 表结构失败：{exc}") from exc

    if not rows:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        raise DatabaseNotReadyError(
            f"数据库里没有 docs 表。现有表：{tables or '（无）'}\n"
            f"可用 python scripts/inspect_db.py 查看实际结构。"
        )

    actual = {r[1] for r in rows}
    missing = [c for c in DOCS_COLUMNS if c not in actual]
    if missing:
        raise DatabaseNotReadyError(
            f"docs 表缺少列：{missing}\n"
            f"实际列：{sorted(actual)}\n"
            f"db.py 按约定的 8 列编写，请核对 schema（或告知我新的列名）。"
        )


#: docs 表里「公告链接」的可选列名（按优先级）。
#: 这列**不是必需的**：管道还没补时，db.py 会回退到可打开的公告列表页，
#: 而不是编造一个点开就 404 的公告深链。
OPTIONAL_URL_COLUMNS: tuple[str, ...] = ("url", "source_url", "doc_url")


def _optional_columns(con: sqlite3.Connection) -> set[str]:
    """docs 表里真实存在的列名（用于探测可选列）。"""
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(docs)")}
    except sqlite3.Error:
        return set()


def _url_column(con: sqlite3.Connection) -> str | None:
    """返回真正存在的 URL 列名；没有则 None。"""
    actual = _optional_columns(con)
    return next((c for c in OPTIONAL_URL_COLUMNS if c in actual), None)


def cninfo_list_url(stock_code: str) -> str:
    """巨潮资讯网该公司公告列表页（真实存在、可直接打开）。

    用在拿不到单篇公告直链时的回退 —— 这是**真实可达的页面**，
    不是伪造的深链。前端只要求 source_url 是合法 http(s) URL。
    """
    base = settings.CNINFO_LIST_URL.rstrip("/")
    return f"{base}?stock={stock_code}&tabName=fulltext"


def _rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Iterator[sqlite3.Row]:
    try:
        yield from con.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseNotReadyError(f"查询失败：{exc}\nSQL: {sql}") from exc


# ---------------------------------------------------------------------------
# created_at 容错解析
# ---------------------------------------------------------------------------

#: 常见的时间字符串格式（按出现概率排序）
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y%m%d",
)

#: ISO8601 带时区（+08:00 / Z）的通用匹配
_ISO_TZ = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_created_at(value: Any) -> datetime | None:
    """把 docs.created_at 解析成 datetime；解析不出返回 None。

    兼容：ISO8601（含 Z / ±HH:MM）、空格分隔、仅日期、YYYYMMDD、Unix 时间戳。
    `created_at` 的真实格式未知，所以这里宁可宽容也不要误丢数据。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # 10 位秒 / 13 位毫秒
        if number > 1e11:
            number /= 1000.0
        if number <= 0:
            return None
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    # 纯数字字符串：8 位优先按 YYYYMMDD 解析，其余按 Unix 时间戳。
    # ⚠️ 顺序很重要：8 位数字既可解释成 YYYYMMDD（如 20220101），
    #    也可解释成 Unix 秒（1970-08-22）—— 对公告时间戳来说前者才是本意。
    if text.isdigit():
        if len(text) == 8:
            try:
                return datetime.strptime(text, "%Y%m%d")
            except ValueError:
                pass  # 不是合法日期（如 12345678）→ 退回时间戳
        if len(text) in (10, 13):
            return parse_created_at(int(text))
        # 其它位数：既不是 YYYYMMDD 也不像时间戳
        return None

    match = _ISO_TZ.match(text)
    if match:
        y, mo, d, h, mi, s, tz = match.groups()
        parsed = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        if tz and tz != "Z":
            sign = 1 if tz[0] == "+" else -1
            digits = tz[1:].replace(":", "")
            offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
            parsed = parsed - sign * offset  # 归一到 UTC-naive
        return parsed

    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 公告日期解析（方案 B：从文件名尽力解析）
#
# 背景：docs 表**没有公告发布日列**，created_at 是**入库时间**
#       （实测 126 条全部同一天入库），因此时间窗口在 created_at 上毫无意义。
#       真正的日期线索在 file_name / rel_path 里 —— 但覆盖并不完整：
#         * 带完整 YYYY-MM-DD：0 条
#         * 只有年月：14 条
#         * 只有年份：52 条
#         * 完全无日期：60 条
#       所以：解析得到的用，解析不到的标为「日期未知」并**保留**（不过滤、排最后）。
# ---------------------------------------------------------------------------

#: date_source 取值
ANNOUNCE_DATE_FROM_NAME = "filename"
ANNOUNCE_DATE_UNKNOWN = "unknown"

#: 中文数字 → 阿拉伯数字（处理「二〇二五年二月」这类写法）
_CN_DIGITS = str.maketrans("〇零一二三四五六七八九０１２３４５６７８９", "001234567890123456789")

#: 完整日期：2024-04-19 / 2024年4月19日 / 2024/04/19
#: ⚠️ 中文写法必须带「日/号」收尾，否则 `2026年1-6月`（月份区间）会被当成
#: 「2026 年 1 月 6 日」，真实库里这类「1-6月」「1-9月」标题极常见。
_DATE_FULL = re.compile(
    r"(?<!\d)(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"
    r"|(?<!\d)(20\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)"
)
#: 紧凑日期 YYYYMMDD —— 真实库里文件名的主格式（如 20260829_2026年半年度报告.pdf）。
#: ⚠️ 必须排在「只有年月」之前：否则 `20260429_2026年一季度报告.pdf` 会被
#: `_DATE_YM` 抢到「2026年」而已，整库日期全部退化成 YYYY-01-01。
#: 末尾 `(?!\s*[-~—]\s*\d)` 排除 `2026年1-6月` 这类区间里的年份数字。
_DATE_YMD = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)(?!\s*[-~—]\s*\d)")
#: 只有年月：2025年7月 / 2025-07
_DATE_YM = re.compile(r"(20\d{2})\s*[-年/.]\s*(\d{1,2})\s*月?")
#: 只有年份：2024年
_DATE_YEAR = re.compile(r"(20\d{2})\s*年")

#: 合理的年份范围（超出基本可判定为误匹配数字）
_MIN_YEAR, _MAX_YEAR = 2000, 2100


def normalize_cn_digits(text: str) -> str:
    """把中文数字/全角数字转成 ASCII 数字（如「二〇二五年」→「2025年」）。"""
    return (text or "").translate(_CN_DIGITS)


def parse_announce_date(*candidates: str | None) -> str | None:
    """从文件名/路径里尽力解析公告日期，返回 'YYYY-MM-DD' 或 None。

    粒度不足时按"最早可能"补齐（只有年月 → 当月 01 日；只有年份 → 当年 01-01），
    并在 date_source 之外由调用方知晓这是近似值。解析不到返回 None。
    """
    for raw in candidates:
        if not raw:
            continue
        text = normalize_cn_digits(str(raw))

        # 紧凑 YYYYMMDD 优先：真实库文件名的主格式，且能排除「1-6月」区间的干扰
        match = _DATE_YMD.search(text)
        if match:
            year, month, day = (int(g) for g in match.groups())
            if _MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    return date(year, month, day).isoformat()
                except ValueError:
                    pass  # 20260230 之类 → 继续往下试

        match = _DATE_FULL.search(text)
        if match:
            # 两个分支（中文带日/号、数字带分隔符）二选一，未命中的那组为 None
            parts = [g for g in match.groups() if g is not None]
            if len(parts) == 3:
                year, month, day = (int(g) for g in parts)
                if _MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12 and 1 <= day <= 31:
                    try:
                        return date(year, month, day).isoformat()
                    except ValueError:
                        pass  # 2 月 30 日之类 → 继续往下试

        match = _DATE_YM.search(text)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            if _MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12:
                return date(year, month, 1).isoformat()

        match = _DATE_YEAR.search(text)
        if match:
            year = int(match.group(1))
            if _MIN_YEAR <= year <= _MAX_YEAR:
                return date(year, 1, 1).isoformat()
    return None


def _row_to_dict(
    row: sqlite3.Row,
    *,
    body_chars: int | None = None,
    url_column: str | None = None,
) -> dict[str, Any]:
    """把一行转成统一 dict（对外契约，字段名固定）。

    :param body_chars: 正文截断长度；None = 不截断
    :param url_column: docs 表里实际存在的 URL 列名（可为 None）

    ⚠️ 关于两个日期字段：
      * `created_date` —— 来自 created_at，是**入库时间**，不是公告发布日；
      * `announce_date` —— 从 file_name 尽力解析出的**公告日期**（方案 B），
        解析不到为 None。排序与时间窗口过滤一律用 announce_date。

    ⚠️ 关于 `source_url`：
      优先取 docs 表里的 URL 列（管道补上之后）；拿不到时回退到巨潮的
      公告列表页 —— **真实可达的页面**，绝不伪造公告深链。
    """
    body = row["text_content"] or ""
    if body_chars is not None and len(body) > body_chars:
        body = body[:body_chars]

    created_raw = row["created_at"]
    parsed_created = parse_created_at(created_raw)
    announce_date = parse_announce_date(row["file_name"], row["rel_path"])
    raw_title = row["file_name"] or ""

    # 公告链接：库里有的用库里的，没有的用列表页兜底
    doc_url: str | None = None
    if url_column:
        try:
            candidate = row[url_column]
        except (IndexError, KeyError):
            candidate = None
        if isinstance(candidate, str) and candidate.strip().startswith(("http://", "https://")):
            doc_url = candidate.strip()

    return {
        # 业务标识
        "id": row["id"],
        "company_code": row["company_code"],
        "file_name": raw_title,
        "rel_path": row["rel_path"],
        # 时间：入库时间 + 解析出的公告日期
        "created_at": str(created_raw) if created_raw is not None else None,
        "created_date": parsed_created.date().isoformat() if parsed_created else None,
        "announce_date": announce_date,
        "date_source": ANNOUNCE_DATE_FROM_NAME if announce_date else ANNOUNCE_DATE_UNKNOWN,
        # 内容
        "page_count": row["page_count"],
        "text_content": body,
        "text_length": len(row["text_content"] or ""),
        "has_tables": bool(row["tables_json"]),
        # 出处链接（前端的 source_url 硬要求合法 http(s) URL）
        "source_url": doc_url or cninfo_list_url(str(row["company_code"] or "")),
        "source_url_is_detail": doc_url is not None,
    }


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def get_stocks() -> list[str]:
    """返回库中所有公司代码（去重、升序）。

    company_code 就是股票代码，无需从文件名解析。
    """
    with connect() as con:
        _ensure_docs_table(con)
        rows = list(
            _rows(
                con,
                "SELECT DISTINCT company_code FROM docs "
                "WHERE company_code IS NOT NULL AND TRIM(company_code) <> '' "
                "ORDER BY company_code",
            )
        )
    return [str(r["company_code"]).strip() for r in rows]


def get_announcements(
    stock_code: str,
    days: int | None = 365,
    limit: int | None = None,
    *,
    window: int | None = None,
    body_chars: int | None = None,
) -> list[dict[str, Any]]:
    """返回某公司近期公告，每条含日期、标题/来源、正文。

    :param stock_code: 公司代码（精确匹配）
    :param days: 只取最近 N 天。传 None 表示不限时间。
                 ⚠️ 时间基准是**从文件名解析出的公告日期**（announce_date），
                 不是 created_at（后者是入库时间，实测全部同一天，没有区分度）。
                 解析不到日期的公告**不会被过滤掉**，而是排到最后并标记
                 date_source='unknown'（宁可多给，不要误丢证据）。
    :param limit: 最多返回几条（None = 不限）
    :param window: `days` 的别名（兼容写法，二者取其一）
    :param body_chars: 正文截断长度；默认取 settings.ANNOUNCEMENT_MAX_CHARS，
                       传 None 表示不截断（注意可能很大）
    :returns: 列表，按公告日期倒序（日期未知的排最后），字段见 _row_to_dict
    """
    code = (stock_code or "").strip()
    if not code:
        raise ValueError("stock_code 不能为空")

    days = window if days is None else days
    if body_chars is None:
        body_chars = settings.ANNOUNCEMENT_MAX_CHARS

    with connect() as con:
        _ensure_docs_table(con)
        url_column = _url_column(con)
        rows = list(
            _rows(
                con,
                f"SELECT {_SELECT_COLUMNS} FROM docs "
                "WHERE company_code = ? ORDER BY id DESC",
                (code,),
            )
        )

    # (排序键, 是否日期未知, 条目)
    parsed_rows: list[tuple[date, bool, dict[str, Any]]] = []
    for row in rows:
        item = _row_to_dict(row, body_chars=body_chars, url_column=url_column)
        raw_date = item.get("announce_date")
        if raw_date:
            try:
                parsed_rows.append((date.fromisoformat(raw_date), False, item))
                continue
            except ValueError:
                pass
        parsed_rows.append((date.min, True, item))

    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).date()
        kept: list[tuple[date, bool, dict[str, Any]]] = []
        stale = 0
        undated = 0
        for parsed, unknown, item in parsed_rows:
            if unknown:
                undated += 1
                kept.append((parsed, unknown, item))  # 日期未知 → 保留
            elif parsed >= cutoff:
                kept.append((parsed, unknown, item))
            else:
                stale += 1
        logger.debug(
            "%s 时间窗口 %s 天：保留 %d，剔除过期 %d，日期未知保留 %d",
            code,
            days,
            len(kept),
            stale,
            undated,
        )
        parsed_rows = kept

    # 公告日期倒序；日期未知的排最后（保持 id DESC 的相对顺序）
    parsed_rows.sort(key=lambda triple: (not triple[1], triple[0]), reverse=True)

    result = [item for _parsed, _unknown, item in parsed_rows]
    if limit is not None:
        if limit < 0:
            raise ValueError("limit 不能为负数")
        result = result[:limit]
    return result


def search_announcements(keyword: str, limit: int = 50) -> list[dict[str, Any]]:
    """全文关键词检索（在正文、文件名、公司代码里找）。

    用于演示时快速定位证据来源。

    :param keyword: 关键词（至少 2 个字符；LIKE 通配符会被转义）
    :param limit: 最多返回几条
    """
    text = (keyword or "").strip()
    if len(text) < MIN_KEYWORD_LENGTH:
        raise ValueError(f"关键词至少需要 {MIN_KEYWORD_LENGTH} 个字符，收到 {keyword!r}")
    if limit < 1:
        raise ValueError("limit 必须 >= 1")

    # 转义 LIKE 通配符，避免用户输入的 % 把全库匹配出来
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    with connect() as con:
        _ensure_docs_table(con)
        url_column = _url_column(con)
        rows = list(
            _rows(
                con,
                f"SELECT {_SELECT_COLUMNS} FROM docs "
                "WHERE text_content LIKE ? ESCAPE '\\' "
                "   OR file_name     LIKE ? ESCAPE '\\' "
                "   OR company_code  LIKE ? ESCAPE '\\' "
                "ORDER BY id DESC LIMIT ?",
                (pattern, pattern, pattern, limit),
            )
        )
    return [
        _row_to_dict(row, body_chars=settings.ANNOUNCEMENT_MAX_CHARS, url_column=url_column)
        for row in rows
    ]


def count_announcements(stock_code: str | None = None) -> int:
    """公告总条数；给了 stock_code 就只数那家公司。"""
    with connect() as con:
        _ensure_docs_table(con)
        if stock_code:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM docs WHERE company_code = ?",
                ((stock_code or "").strip(),),
            ).fetchone()
        else:
            row = con.execute("SELECT COUNT(*) AS n FROM docs").fetchone()
    return int(row["n"]) if row else 0


def get_company_summary(stock_code: str, *, recent: int = 5) -> dict[str, Any] | None:
    """公司画像用：公告数量、日期区间、最近几条标题。

    日期区间基于**解析出的公告日期**（announce_date）；同时报出有多少条日期未知，
    避免把"日期未知"误读成"很久以前"。查不到该公司返回 None。
    """
    code = (stock_code or "").strip()
    if not code:
        return None

    announcements = get_announcements(code, days=None, body_chars=0)
    if not announcements:
        return None

    dates = [a["announce_date"] for a in announcements if a.get("announce_date")]
    undated = sum(1 for a in announcements if not a.get("announce_date"))
    return {
        "company_code": code,
        "announcement_count": len(announcements),
        "first_announcement_date": min(dates) if dates else None,
        "last_announcement_date": max(dates) if dates else None,
        "undated_count": undated,
        "recent_titles": [a["file_name"] for a in announcements[:recent]],
    }


def db_status() -> dict[str, Any]:
    """给 /health 用的数据源状态；**不抛异常**，把问题写在返回值里。"""
    path = db_path()
    status: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    try:
        status["announcements"] = count_announcements()
        status["stocks"] = len(get_stocks())
        status["ready"] = True
    except DatabaseNotReadyError as exc:
        status["ready"] = False
        status["error"] = str(exc).split("\n")[0]
    except Exception as exc:  # noqa: BLE001
        status["ready"] = False
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


__all__ = [
    "ANNOUNCE_DATE_FROM_NAME",
    "ANNOUNCE_DATE_UNKNOWN",
    "DOCS_COLUMNS",
    "MIN_KEYWORD_LENGTH",
    "DatabaseNotReadyError",
    "connect",
    "count_announcements",
    "db_path",
    "db_status",
    "get_announcements",
    "get_company_summary",
    "get_stocks",
    "normalize_cn_digits",
    "parse_announce_date",
    "parse_created_at",
    "search_announcements",
]
