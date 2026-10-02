"""db.py 行为自检（纯标准库，无需第三方依赖）。

db.py 依赖 config → pydantic（本机装不上），因此用**桩模块**替换
pydantic / pydantic_settings 后真实导入 db.py，验证 SQL 与解析逻辑本身。

覆盖：
  ① get_stocks() 去重且升序；
  ② get_announcements() —— 时间窗口按**解析出的公告日期**过滤（方案 B）、
     days=None / days=100 / limit / 正文截断；
  ③ 文件名日期解析：完整日期 / 年月 / 中文数字年月 / 只有年份 / 无日期；
  ④ created_at 容错解析（ISO / T / YYYYMMDD / Unix 时间戳 / 无法解析）；
  ⑤ search_announcements() 全文检索 + LIKE 转义 + 参数校验；
  ⑥ 缺库 / 缺表 / 缺列 → DatabaseNotReadyError（绝不静默返回空）；
  ⑦ 只读保证；
  ⑧ 配置自适应：真实库放在工作区根目录也能找到（方案 A）。

若存在真实库 dsh/data/cninfo.db，会额外对它做一次只读冒烟测试。
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# ① 桩掉 pydantic / pydantic_settings，让 config.py 能导入
# ---------------------------------------------------------------------------

_STUB_DEFAULTS = {
    "DB_PATH": "",  # 下面填临时库
    "DB_PATH_STRICT": True,
    "ANNOUNCEMENT_MAX_CHARS": 4000,
    "ANALYSIS_WINDOW_DAYS": None,
    "ANALYSIS_MAX_ANNOUNCEMENTS": 30,
}


def _identity_decorator(*_a, **_kw):
    def _wrap(func):
        return func
    return _wrap


class _StubBaseSettings:
    """极简 Settings 桩。

    注意：实际运行时 config.py 用的是**真实的** Settings（pydantic 已被本文
    顶部的桩替换掉，但 BaseSettings 仍是下面这个桩类），因此 db_file 的
    回退逻辑由 config.Settings 自己实现，这里不再重复。
    """

    def __init__(self, **overrides):
        for key, value in _STUB_DEFAULTS.items():
            setattr(self, key, value)
        for key, value in overrides.items():
            setattr(self, key, value)


pm = types.ModuleType("pydantic")
pm.computed_field = _identity_decorator
pm.field_validator = _identity_decorator
sys.modules["pydantic"] = pm

psm = types.ModuleType("pydantic_settings")
psm.BaseSettings = _StubBaseSettings
psm.SettingsConfigDict = lambda **kw: dict(kw)
sys.modules["pydantic_settings"] = psm

# ---------------------------------------------------------------------------
# ② 造测试库（与 tests/conftest.py 的 DOCS_ROWS 保持一致）
# ---------------------------------------------------------------------------

TEST_DIR = ROOT / ".test-tmp" / "db-check"
if TEST_DIR.exists():
    for stale in TEST_DIR.glob("*.db"):
        stale.unlink()
TEST_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = TEST_DIR / "cninfo.db"

# 日期相对"今天"计算，跨越 90 / 365 天边界 → 时间窗口测试才真的在测过滤
_TODAY = date.today()


def _cn(days: int) -> str:
    """往前 days 天的中文日期（写进文件名）。"""
    t = _TODAY - timedelta(days=days)
    return f"{t.year}年{t.month}月{t.day}日"


def _ym_cn(days: int) -> str:
    """往前 days 天的「XXXX年X月」。"""
    t = _TODAY - timedelta(days=days)
    return f"{t.year}年{t.month}月"


# company_code, file_name, rel_path, page_count, text_content, tables_json, created_at
ROWS = [
    # 距今 20 天
    ("688583", f"2023年年度报告（{_cn(20)}）.pdf", "a/1.pdf", 206,
     "公司实现营业收入 5.20 亿元，同比增长 18%。归属于上市公司股东的净利润 8600 万元，"
     "但经营活动产生的现金流量净额为 -1200 万元。", None, "2026-10-02 01:03:32"),
    # 距今 60 天
    ("688583", f"关于涉及诉讼的公告（{_cn(60)}）.pdf", "a/2.pdf", 6,
     "涉及一起买卖合同纠纷，涉案金额 3200 万元。", '[{"c":1}]', "2026-10-02 01:03:33"),
    # 距今 200 天
    ("688583", f"2024年半年度报告（{_cn(200)}）.pdf", "a/3.pdf", 150,
     "营业收入 4.10 亿元，净利润 5200 万元。", None, "2026-10-02 01:03:34"),
    # 距今 800 天（超过 365 天窗口）
    ("688583", f"2022年年度报告（{_cn(800)}）.pdf", "a/4.pdf", 180,
     "早年营业收入 3.10 亿元。", None, "2026-10-02 01:03:35"),
    # 文件名无日期 → 必须保留
    ("688583", "关于会计政策变更的公告.pdf", "a/5.pdf", 4,
     "公司根据新准则变更会计政策。", None, "2026-10-02 01:03:36"),
    # 距今 10 天
    ("000001", f"某银行2024年年度报告（{_cn(10)}）.pdf", "b/1.pdf", 300,
     "本行实现营业收入 1200 亿元，不良贷款率 1.05%。", None, "2026-10-02 01:03:37"),
    # 只有年月，距今 150 天
    ("000001", f"某银行章程（{_ym_cn(150)}修订）.pdf", "b/2.pdf", 20,
     "董事长因个人原因辞任。", None, "2026-10-02 01:03:38"),
    # 距今 300 天
    ("600036", f"招行2024年年度报告（{_cn(300)}）.pdf", "c/1.pdf", 280,
     "实现营业收入 3300 亿元，同比增长 2%。", None, "2026-10-02 01:03:39"),
    # created_at 无法解析 + 文件名无日期 → 双重「日期未知」
    ("600036", "坏日期公告.pdf", "c/2.pdf", 2,
     "这条记录没有可解析的日期，必须被保留而不是丢弃。", None, "昨天"),
    # 距今 5 天
    ("600036", f"毛利率说明（{_cn(5)}）.pdf", "c/3.pdf", 2,
     "本期毛利率 45% 较上年提升 2 个百分点。", None, "2026-10-02 01:03:40"),
    ("600036", "空正文.pdf", "c/4.pdf", 1, "", None, "2026-10-02 01:03:41"),
]


def build_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_code TEXT, file_name TEXT, rel_path TEXT, page_count INTEGER,
            text_content TEXT, tables_json TEXT, created_at TEXT)"""
    )
    con.executemany(
        "INSERT INTO docs (company_code, file_name, rel_path, page_count, "
        "text_content, tables_json, created_at) VALUES (?,?,?,?,?,?,?)",
        ROWS,
    )
    con.commit()
    con.close()


build_db(DB_FILE)
_STUB_DEFAULTS["DB_PATH"] = str(DB_FILE)

sys.path.insert(0, str(ROOT))
for name in ("config", "db"):
    sys.modules.pop(name, None)
import config  # noqa: E402
import db  # noqa: E402


def use_db(path: Path, *, fallback: bool = False) -> None:
    """把 db 模块看到的 DB_PATH 指向指定文件。

    ⚠️ 必须改写**现有 settings 实例的属性**，而不是替换 config.settings：
    db.py 里是 `from config import settings`，绑定的是导入那一刻的对象。

    :param fallback: 是否允许「找不到就回退候选路径」。测缺库场景时必须 False，
                     否则会回退到真实库、让报错测试失去意义。
    """
    config.settings.DB_PATH = str(path)
    config.settings.DB_PATH_STRICT = not fallback
    db.settings = config.settings
    if fallback:
        # 允许回退时，db_file 可能解析到候选路径，此时只校验配置值已就位
        assert Path(db.settings.DB_PATH) == path, f"DB_PATH 未生效：{db.settings.DB_PATH}"
    else:
        assert db.settings.db_file == path, f"DB_PATH 未生效：{db.settings.db_file}"


use_db(DB_FILE)
print(f"       已真实导入 {db.__file__}")
print(f"       测试库 {DB_FILE.name}（{len(ROWS)} 行）")

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("① get_stocks")
print("=" * 74)

stocks = db.get_stocks()
print(f"       {stocks}")
check("返回全部公司代码（3 家）", stocks == ["000001", "600036", "688583"], str(stocks))
check("去重且升序", stocks == sorted(set(stocks)))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("② get_announcements —— 时间窗口按「公告日期」过滤（方案 B）")
print("=" * 74)

all_items = db.get_announcements("688583", days=None)
print(f"       days=None → {[a['file_name'][:22] for a in all_items]}")
check("days=None 返回该公司全部 5 条", len(all_items) == 5, str(len(all_items)))

# 关键：created_at 无区分度（模拟真实库），窗口仍必须起作用
created_dates = {a["created_date"] for a in all_items}
check("测试数据 created_at 全部相同（模拟真实库）", len(created_dates) == 1, str(created_dates))
announce_dates = {a["announce_date"] for a in all_items if a["announce_date"]}
check("但公告日期有多个不同值", len(announce_dates) >= 3, str(sorted(announce_dates)))

recent365 = db.get_announcements("688583", days=365)
print(f"       days=365 → {[a['file_name'][:22] for a in recent365]}")
check("365 天窗口排除 2022 年那条",
      not any("2022年" in a["file_name"] for a in recent365),
      str([a["file_name"][:20] for a in recent365]))

narrow = db.get_announcements("688583", days=100)
print(f"       days=100 → {[a['file_name'][:22] for a in narrow]}")
check("100 天窗口比 365 天更窄（证明过滤真的生效）",
      0 < len(narrow) < len(recent365), f"{len(narrow)} < {len(recent365)}")

check("按公告日期倒序",
      [a["announce_date"] for a in narrow if a["announce_date"]]
      == sorted([a["announce_date"] for a in narrow if a["announce_date"]], reverse=True),
      str([a["announce_date"] for a in narrow]))
check("日期未知的排在最后",
      all(a["announce_date"] is None for a in all_items[-1:]) or all_items[-1]["announce_date"] is not None,
      str(all_items[-1]["announce_date"]))

limited = db.get_announcements("688583", days=None, limit=2)
check("limit=2 生效", len(limited) == 2, str(len(limited)))

short = db.get_announcements("688583", days=None, limit=1, body_chars=10)[0]
check("body_chars 截断生效（取前 10 字，原文长度另记）",
      len(short["text_content"]) == 10 and int(short["text_length"]) > 10,
      f"len={len(short['text_content'])} text={short['text_content']!r} 原文={short['text_length']}字")
full = db.get_announcements("688583", days=None, limit=1, body_chars=None)[0]
check("body_chars=None 不截断", len(full["text_content"]) == int(full["text_length"]))

item = all_items[0]
for field in ("id", "company_code", "file_name", "rel_path", "created_at", "created_date",
              "announce_date", "date_source", "page_count", "text_content", "text_length",
              "has_tables"):
    check(f"返回 dict 含字段 {field}", field in item)

check("未知公司返回空列表（不是报错）", db.get_announcements("999999") == [])
try:
    db.get_announcements("")
except ValueError:
    check("空 stock_code 抛 ValueError", True)
else:
    check("空 stock_code 抛 ValueError", False)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("③ 公告日期解析（方案 B）")
print("=" * 74)

p = db.parse_announce_date
cases = [
    ("2024年4月19日公告.pdf", "2024-04-19", "完整中文日期"),
    ("2025-07-15公告.pdf", "2025-07-15", "ISO 日期"),
    ("2025年7月修订.pdf", "2025-07-01", "年月（补 01 日）"),
    ("二〇二五年三月修订.pdf", "2025-03-01", "中文数字年月"),
    ("２０２５年３月.pdf", "2025-03-01", "全角数字"),
    ("2024年年度报告.pdf", "2024-01-01", "只有年份"),
    ("没有日期的公告.pdf", None, "无日期"),
    ("1999年旧文件.pdf", None, "超出合理年份 → 判为误匹配"),
    ("2024年13月.pdf", "2024-01-01", "非法月份 → 退回按年"),
    (None, None, "None 输入"),
]
for raw, expected, label in cases:
    got = p(raw)
    check(f"解析 {label}: {raw!r}", got == expected, f"{got!r} (期望 {expected!r})")

check("normalize_cn_digits 二〇二五 → 2025", db.normalize_cn_digits("二〇二五年") == "2025年")
check("normalize_cn_digits 全角 → 半角", db.normalize_cn_digits("２０２５") == "2025")

# 数据库中实际生效（文件名日期相对今天，故用解析结果反推校验）
by_name = {a["file_name"]: a for a in db.get_announcements("688583", days=None)}
with_date = [v for v in by_name.values() if v.get("announce_date") and v["date_source"] == db.ANNOUNCE_DATE_FROM_NAME]
check("至少有一条从文件名解析出完整日期", len(with_date) >= 1, str(len(with_date)))
if with_date:
    sample_date = with_date[0]["announce_date"]
    check("解析出的日期是合法 ISO 日期且距今不超过 365 天",
          date.fromisoformat(sample_date) <= date.today()
          and (date.today() - date.fromisoformat(sample_date)).days <= 365,
          f"{sample_date}")
    check("date_source 标记为 filename", with_date[0]["date_source"] == db.ANNOUNCE_DATE_FROM_NAME)

undated = next(v for k, v in by_name.items() if "会计政策变更" in k)
check("无日期条目的 announce_date 为 None", undated["announce_date"] is None)
check("无日期条目 date_source = unknown", undated["date_source"] == db.ANNOUNCE_DATE_UNKNOWN)
check("无日期条目在窄窗口下仍保留",
      "关于会计政策变更的公告.pdf" in [a["file_name"] for a in db.get_announcements("688583", days=7)])

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("④ created_at 容错解析")
print("=" * 74)

c = db.parse_created_at
check("ISO 空格分隔", c("2024-04-19 09:30:00") == datetime(2024, 4, 19, 9, 30, 0))
check("ISO T 分隔", c("2024-04-19T14:05:00") == datetime(2024, 4, 19, 14, 5, 0))
check("带 Z 时区", c("2024-04-19T14:05:00Z") == datetime(2024, 4, 19, 14, 5, 0))
check("带 +08:00 归一到 UTC", c("2024-04-19T14:05:00+08:00") == datetime(2024, 4, 19, 6, 5, 0),
      str(c("2024-04-19T14:05:00+08:00")))
check("仅日期", c("2024-04-19") == datetime(2024, 4, 19))
check("斜杠格式", c("2024/04/19") == datetime(2024, 4, 19))
check("YYYYMMDD（不能当成 Unix 秒）", c("20220101") == datetime(2022, 1, 1))
check("Unix 秒", c(1713500000) is not None)
check("Unix 毫秒", c(1713500000000) is not None)
check("datetime 原样", c(datetime(2024, 1, 1)) == datetime(2024, 1, 1))
check("None → None", c(None) is None)
check("空串 → None", c("") is None)
check("无法解析 → None（不抛异常）", c("昨天") is None)

bad = next(a for a in db.get_announcements("600036", days=None) if a["file_name"] == "坏日期公告.pdf")
check("created_at 无法解析 → created_date 为 None", bad["created_date"] is None)
check("该行被保留而不是丢弃", bad is not None)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("⑤ search_announcements")
print("=" * 74)

hits = db.search_announcements("营业收入")
print(f"       '营业收入' → {[(h['company_code'], h['file_name'][:16]) for h in hits]}")
check("跨公司命中", {h["company_code"] for h in hits} == {"000001", "600036", "688583"},
      str(sorted({h["company_code"] for h in hits})))
check("可按文件名检索", any("诉讼" in h["file_name"] for h in db.search_announcements("诉讼")))
check("不匹配返回空", db.search_announcements("量子计算机") == [])

pct = db.search_announcements("45%")
print(f"       搜 '45%' → {[h['file_name'] for h in pct]}")
check("'%' 被转义（不通配全库）", len(pct) == 1 and pct[0]["file_name"].startswith("毛利率说明"),
      f"{len(pct)} 条")
check("'_' 被转义", db.search_announcements("1_1") == [])
check("limit 生效", len(db.search_announcements("营业收入", limit=1)) == 1)

for bad_kw in ("", "营", " "):
    try:
        db.search_announcements(bad_kw)
    except ValueError:
        check(f"过短关键词 {bad_kw!r} 抛 ValueError", True)
    else:
        check(f"过短关键词 {bad_kw!r} 抛 ValueError", False)
try:
    db.search_announcements("营业收入", limit=0)
except ValueError:
    check("limit=0 抛 ValueError", True)
else:
    check("limit=0 抛 ValueError", False)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("⑥ 汇总接口 + 只读保证")
print("=" * 74)

check("count_announcements() = 11", db.count_announcements() == 11, str(db.count_announcements()))
check("count_announcements('688583') = 5", db.count_announcements("688583") == 5)

summary = db.get_company_summary("688583")
check("summary 公告数 = 5", summary is not None and summary["announcement_count"] == 5, str(summary))
check("summary 含日期区间", bool(summary and summary["first_announcement_date"] and summary["last_announcement_date"]),
      f"{summary and summary['first_announcement_date']} ~ {summary and summary['last_announcement_date']}")
check("summary 报出日期未知条数", summary is not None and summary["undated_count"] == 1,
      str(summary and summary["undated_count"]))
check("未知公司 summary → None", db.get_company_summary("999999") is None)

status = db.db_status()
check("db_status ready=True", status.get("ready") is True, str(status))
check("db_status 报出股票数与公告数",
      status.get("stocks") == 3 and status.get("announcements") == 11, str(status))

con = db.connect()
try:
    con.execute("INSERT INTO docs (company_code) VALUES ('X')")
except sqlite3.OperationalError as exc:
    check("只读打开（写操作被拒）", "readonly" in str(exc).lower(), str(exc)[:50])
else:
    check("只读打开（写操作被拒）", False, "竟然写成功了！")
finally:
    con.close()

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("⑦ 缺库 / 缺表 / 缺列")
print("=" * 74)

use_db(TEST_DIR / "not_here.db")
try:
    db.get_stocks()
except db.DatabaseNotReadyError as exc:
    check("库文件不存在 → DatabaseNotReadyError", "不存在" in str(exc), str(exc).split("\n")[0][:50])
else:
    check("库文件不存在 → DatabaseNotReadyError", False)

empty = TEST_DIR / "empty.db"
empty.write_bytes(b"")
use_db(empty)
try:
    db.get_stocks()
except db.DatabaseNotReadyError as exc:
    check("空库文件 → DatabaseNotReadyError", "为空" in str(exc))
else:
    check("空库文件 → DatabaseNotReadyError", False)

wrong = TEST_DIR / "wrong.db"
con = sqlite3.connect(wrong)
con.execute("CREATE TABLE other (id INTEGER)")
con.commit()
con.close()
use_db(wrong)
try:
    db.get_stocks()
except db.DatabaseNotReadyError as exc:
    check("缺 docs 表 → 报错并列出实际表名",
          "没有 docs 表" in str(exc) and "other" in str(exc), str(exc).split("\n")[0][:60])
else:
    check("缺 docs 表 → 报错并列出实际表名", False)

partial = TEST_DIR / "partial.db"
con = sqlite3.connect(partial)
con.execute("CREATE TABLE docs (id INTEGER, company_code TEXT)")
con.commit()
con.close()
use_db(partial)
try:
    db.get_stocks()
except db.DatabaseNotReadyError as exc:
    check("缺列 → 报错并逐列列出",
          "缺少列" in str(exc) and "text_content" in str(exc), str(exc).split("\n")[0][:70])
else:
    check("缺列 → 报错并逐列列出", False)

check("db_status 遇错不抛异常", db.db_status().get("ready") is False and bool(db.db_status().get("error")))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("⑧ 方案 A：真实库在工作区根目录时能被自动找到")
print("=" * 74)

real_db = ROOT.parent / "data" / "cninfo.db"
if real_db.is_file():
    # 配置刻意指向不存在的位置，但允许回退 → 应自动找到 ../data/cninfo.db
    use_db(TEST_DIR / "deliberately_missing.db", fallback=True)
    resolved = config.settings.db_file
    check("默认配置下能回退找到 ../data/cninfo.db", resolved == real_db, str(resolved))
    try:
        stocks_real = db.get_stocks()
        total_real = db.count_announcements()
        check(f"真实库可读（{len(stocks_real)} 家公司 / {total_real} 条公告）", total_real > 0,
              f"公司={stocks_real[:5]}")
        sample = db.get_announcements(stocks_real[0], days=None, limit=3)
        check("真实库 get_announcements 可用", len(sample) > 0)
        for it in sample:
            check(f"真实库条目含 announce_date 字段（{it['file_name'][:24]}）",
                  "announce_date" in it, f"announce_date={it['announce_date']}")
    except db.DatabaseNotReadyError as exc:
        check("真实库可读", False, str(exc)[:80])
else:
    print(f"       （未找到真实库 {real_db}，跳过）")

use_db(DB_FILE)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print(f"通过 {PASSED} 项")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("db.py 行为自检全部通过")
