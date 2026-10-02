"""pytest 共享配置：环境注入 + fixtures。

⚠️ 环境注入必须在导入任何项目模块之前完成（config.settings 在导入时就固化）。
⚠️ 测试库必须建在**工作区内**：受限环境下 $TEMP 无法创建 SQLite 文件
   （表现为 "unable to open database file"），故使用 .test-tmp/（已 gitignore）。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 环境适配：让 pytest 的临时目录在受限沙箱下可用
#
# 背景（实测）：
#   * 系统临时目录（$TEMP）在受限环境下**无法创建文件**，而 pytest 的
#     tmp_path / tmp_path_factory 默认就建在 $TEMP 下 → PermissionError；
#   * 本进程创建过的目录随后可能无法 os.scandir（沙箱 ACL），
#     而 pytest 在 sessionfinish 会对 basetemp 调 scandir 清理旧临时目录，
#     于是**即使测试本身全部通过**，会话结束仍抛 PermissionError。
# 处理：
#   1) 把临时根指到工作区内的 .pytest-run/（见 pytest.ini 的 --basetemp）；
#   2) 给那个纯清理动作加一层容错 —— 它的失败不代表任何测试失败。
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_RUN_TMP = PROJECT_ROOT / ".pytest-run"
_RUN_TMP.mkdir(parents=True, exist_ok=True)

for _var in ("TMPDIR", "TEMP", "TMP"):
    os.environ[_var] = str(_RUN_TMP)
tempfile.tempdir = str(_RUN_TMP)


def _patch_pytest_tmpdir_cleanup() -> None:
    """让 pytest 的旧临时目录清理失败时不再炸掉整个会话。"""
    try:
        from _pytest import pathlib as _pytest_pathlib
        from _pytest import tmpdir as _pytest_tmpdir
    except Exception:  # pragma: no cover - 仅防御
        return

    if getattr(_pytest_tmpdir.cleanup_dead_symlinks, "_xray_patched", False):
        return

    original = _pytest_tmpdir.cleanup_dead_symlinks

    def tolerant_cleanup(basetemp):  # type: ignore[no-untyped-def]
        try:
            return original(basetemp)
        except OSError:
            # 受限沙箱下无法枚举 basetemp → 跳过清理，不影响任何测试结果
            return None

    tolerant_cleanup._xray_patched = True  # type: ignore[attr-defined]
    _pytest_tmpdir.cleanup_dead_symlinks = tolerant_cleanup
    _pytest_pathlib.cleanup_dead_symlinks = tolerant_cleanup


_patch_pytest_tmpdir_cleanup()

# ⚠️ 测试库不能放在 pytest 的 basetemp（.pytest-run）里 —— 那个目录由 pytest
#    管理/清理，放进去会被清掉或撞权限。所以另用一个独立目录 .pytest-data/。
TEST_DIR = PROJECT_ROOT / ".pytest-data"
TEST_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = TEST_DIR / "cninfo.db"

os.environ["DB_PATH"] = str(DB_FILE)
# ⚠️ 必须同时写规范名 DATABASE_PATH：它在 config.Settings 里优先级高于 DB_PATH。
#    若外面 shell 里恰好设了 DATABASE_PATH（联调时很常见），只设 DB_PATH 会被它盖住，
#    整套测试就会跑去连真实库 —— 合成库断言全线失败，且看着像代码坏了。
os.environ["DATABASE_PATH"] = str(DB_FILE)
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LLM_ENABLED"] = "true"
os.environ["LLM_FAKE"] = "true"          # 默认不联网
os.environ["DEEPSEEK_API_KEY"] = ""      # 真 Key 一律不带进测试
os.environ["CHARTS_ENABLED"] = "true"

# ---------------------------------------------------------------------------
# 测试数据：3 家公司，含各种 created_at 格式与一条坏日期
# ---------------------------------------------------------------------------

TODAY = date.today()


def _d(days: int) -> str:
    """相对今天往前 days 天的 ISO 日期。"""
    return (TODAY - timedelta(days=days)).isoformat()


def _d_cn(days: int) -> str:
    """相对今天往前 days 天的中文日期（年X月Y日），用于写进文件名。"""
    target = TODAY - timedelta(days=days)
    return f"{target.year}年{target.month}月{target.day}日"


def _d_ym_cn(days: int) -> str:
    """相对今天往前 days 天的「XXXX年X月」形式。"""
    target = TODAY - timedelta(days=days)
    return f"{target.year}年{target.month}月"


#: company_code, file_name, rel_path, page_count, text_content, tables_json, created_at
#:
#: ⚠️ 设计要点（对应 db.py 的「方案 B」）：
#:   * created_at 全部设为同一时刻 —— 模拟真实库（实测 126 条全部同一天入库，
#:     入库时间零区分度，不能当公告日期用）；
#:   * 日期线索放在**文件名**里，覆盖 parse_announce_date 需要处理的各种写法：
#:     完整日期 / 只有年月 / 中文数字年月 / 只有年份 / 完全没有；
#:   * 相对"今天"的距离**刻意跨越 90 / 365 天的边界**，所以时间窗口测试是真的在测过滤。
DOCS_ROWS = [
    # 距今 20 天
    (
        "688583",
        f"2023年年度报告（{_d_cn(20)}）.pdf",
        "a/1.pdf",
        206,
        "公司实现营业收入 5.20 亿元，同比增长 18%。归属于上市公司股东的净利润 8600 万元，"
        "但经营活动产生的现金流量净额为 -1200 万元。应收账款余额 1.85 亿元。",
        None,
        "2026-10-02 01:03:32",
    ),
    # 距今 60 天
    (
        "688583",
        f"关于涉及诉讼的公告（{_d_cn(60)}）.pdf",
        "a/2.pdf",
        6,
        "公司近日收到法院传票，涉及一起买卖合同纠纷，涉案金额 3200 万元，目前处于一审阶段。",
        '[{"col":1}]',
        "2026-10-02 01:03:33",
    ),
    # 距今 200 天
    (
        "688583",
        f"2024年半年度报告（{_d_cn(200)}）.pdf",
        "a/3.pdf",
        150,
        "营业收入 4.10 亿元，净利润 5200 万元。",
        None,
        "2026-10-02 01:03:34",
    ),
    # 距今 800 天（远超 365 天窗口）
    (
        "688583",
        f"2022年年度报告（{_d_cn(800)}）.pdf",
        "a/4.pdf",
        180,
        "早年营业收入 3.10 亿元。",
        None,
        "2026-10-02 01:03:35",
    ),
    # 文件名无日期 → announce_date=None，必须被保留
    (
        "688583",
        "关于会计政策变更的公告.pdf",
        "a/5.pdf",
        4,
        "公司根据新准则变更会计政策，详见本公告正文。",
        None,
        "2026-10-02 01:03:36",
    ),
    # 距今 10 天
    (
        "000001",
        f"某银行2024年年度报告（{_d_cn(10)}）.pdf",
        "b/1.pdf",
        300,
        "本行实现营业收入 1200 亿元，不良贷款率 1.05%，拨备覆盖率 260%。",
        None,
        "2026-10-02 01:03:37",
    ),
    # 只有年月，距今 150 天
    (
        "000001",
        f"某银行章程（{_d_ym_cn(150)}修订）.pdf",
        "b/2.pdf",
        20,
        "董事长因个人原因辞任，由行长代为履行董事长职责。",
        None,
        "2026-10-02 01:03:38",
    ),
    # 距今 300 天
    (
        "600036",
        f"招行2024年年度报告（{_d_cn(300)}）.pdf",
        "c/1.pdf",
        280,
        "实现营业收入 3300 亿元，同比增长 2%，净利润 1460 亿元。",
        None,
        "2026-10-02 01:03:39",
    ),
    # created_at 无法解析 + 文件名无日期 → 双重「日期未知」，必须保留
    (
        "600036",
        "坏日期公告.pdf",
        "c/2.pdf",
        2,
        "这条记录没有可解析的日期，必须被保留而不是丢弃。",
        None,
        "昨天",
    ),
    # 距今 5 天（最近）
    (
        "600036",
        f"毛利率说明（{_d_cn(5)}）.pdf",
        "c/3.pdf",
        2,
        "本期毛利率 45% 较上年提升 2 个百分点。",
        None,
        "2026-10-02 01:03:40",
    ),
]


def build_docs_db(path: Path) -> None:
    """按用户提供的真实 schema 造库（8 列，一个字都不多）。"""
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_code TEXT,
            file_name TEXT,
            rel_path TEXT,
            page_count INTEGER,
            text_content TEXT,
            tables_json TEXT,
            created_at TEXT)"""
    )
    con.executemany(
        "INSERT INTO docs (company_code, file_name, rel_path, page_count, "
        "text_content, tables_json, created_at) VALUES (?,?,?,?,?,?,?)",
        DOCS_ROWS,
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# 假 LLM：返回**合法 JSON**，让 analyzer 的解析链路真正跑起来
# ---------------------------------------------------------------------------

#: 688583 会被判定为 high（净利增长但经营现金流为负 + 诉讼）
FAKE_ANALYSIS_BY_CODE: dict[str, dict] = {
    "688583": {
        "risk_level": "high",
        "summary": "净利润为正但经营现金流为负，且存在诉讼，风险偏高。",
        "findings": [
            {
                "id": "F1",
                "title": "利润与现金流背离",
                "type": "S1",
                "severity": "high",
                "description": "净利润为正，但经营活动现金流净额为负。",
                "evidence_ids": ["Q1"],
            },
            {
                "id": "F2",
                "title": "存在诉讼",
                "type": "S4",
                "severity": "medium",
                "description": "涉及买卖合同纠纷，涉案金额 3200 万元。",
                "evidence_ids": ["Q2"],
            },
        ],
        "evidence_quotes": [
            {
                "id": "Q1",
                "risk_dimension": "现金真实性",
                "content": "净利润为正但经营现金流为负",
                "source_quote": "归属于上市公司股东的净利润 8600 万元，但经营活动产生的现金流量净额为 -1200 万元",
                "source_id": 1,
                "source_file": "2023年年度报告.pdf",
                "source_date": _d(10),
                # ★ 模型必须回填引用页码；缺失会被 ask.py 按 No Evidence 规则丢弃
                "source_page": 1,
            },
            {
                "id": "Q2",
                "risk_dimension": "司法风险",
                "content": "涉及买卖合同纠纷",
                "source_quote": "涉及一起买卖合同纠纷，涉案金额 3200 万元，目前处于一审阶段",
                "source_id": 2,
                "source_file": "关于涉及诉讼的公告.pdf",
                "source_date": _d(30),
                "source_page": 2,
            },
        ],
    },
    # 000001：低风险
    "000001": {
        "risk_level": "low",
        "summary": "公告未发现明显风险信号。",
        "findings": [],
        "evidence_quotes": [],
    },
    # 600036：模拟模型"找不到依据"
    "600036": {
        "risk_level": "unknown",
        "summary": "无足够信息",
        "findings": [],
        "evidence_quotes": [],
    },
}


def _json_fake_answer(question: str, evidence=None, **kwargs):
    """替代 llm_client.generate_answer：按 prompt 里的公司代码返回对应 JSON。"""
    from llm_client import LLMResult

    prompt = kwargs.get("prompt_override") or ""
    code = ""
    for candidate in FAKE_ANALYSIS_BY_CODE:
        if candidate in prompt or (kwargs.get("stock_code") == candidate):
            code = candidate
            break
    payload = FAKE_ANALYSIS_BY_CODE.get(code, FAKE_ANALYSIS_BY_CODE["600036"])
    return LLMResult(
        text=json.dumps(payload, ensure_ascii=False),
        ok=True,
        source="fake",
        model="fake-model",
        elapsed_ms=1.0,
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def scratch_dir():
    """给单个测试用的干净目录。

    ⚠️ 不用 pytest 的 tmp_path：它依赖 basetemp，而本环境下 basetemp 目录
    会被沙箱打上受限 ACL，导致 fixture 拆卸阶段 PermissionError。
    这里直接用工作区内的 .pytest-data/scratch（已 gitignore），
    每个测试开始前清掉旧内容。清理失败也不影响测试结果。
    """
    import uuid

    path = TEST_DIR / "scratch" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _prepare_environment():
    """建库 + 准备干净的 cache/logs 目录。"""
    build_docs_db(DB_FILE)
    for sub in ("cache", "logs"):
        target = PROJECT_ROOT / sub
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    yield
    # 清理测试产物
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture()
def fake_llm(monkeypatch):
    """把 analyzer 用的 LLM 调用换成确定性假实现（返回合法 JSON）。"""
    monkeypatch.setattr("analyzer.generate_answer", _json_fake_answer, raising=True)
    return FAKE_ANALYSIS_BY_CODE


@pytest.fixture()
def llm_calls(monkeypatch):
    """记录 LLM 调用次数（断言缓存生效用）。"""
    calls: list[str] = []

    def _counting(question, evidence=None, **kwargs):
        calls.append(kwargs.get("stock_code") or question)
        return _json_fake_answer(question, evidence, **kwargs)

    monkeypatch.setattr("analyzer.generate_answer", _counting, raising=True)
    return calls


@pytest.fixture(scope="session")
def client(_prepare_environment):
    """带 lifespan 的 TestClient。"""
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def analyzed(fake_llm, client):
    """预先把三家公司分析一遍并写入缓存（多数接口测试的前置条件）。"""
    from analyzer import analyze_company

    for code in ("688583", "000001", "600036"):
        analyze_company(code, force=True)
    return client


# ---------------------------------------------------------------------------
# 扩展合成库：给「动态 Evidence-first 问答」用的四家公司 + evidence/chunks
#
# 为什么单独造一份库、而不是往 DOCS_ROWS 里加行：
#   上面那份 8 列小库是 db.py「方案 B」的行为测试基线（公告条数、日期窗口等
#   断言都写死了 5 / 3 / 3 条）。往里插数据会一次性打破一堆无关断言。
#   所以动态问答用**独立的一份库**（同一目录，另一个文件名），各测各的。
#
# 设计要点（刻意保留真实库里的"脏"特征，让测试真的在测核验逻辑）：
#   * 引文是**表格转写**（带 `|`），不是页面原文 —— 真实库就是这样，
#     直接整串子串匹配会 100% 失败（见 dynamic_evidence 的注释）；
#   * 逐条引文都能在所引页面上逐字核到数字，顺序也一致；
#   * 刻意塞三条**必须被拒绝**的脏数据：
#       - 页码越界、页码缺失、引文里的数字在该页不存在。
# ---------------------------------------------------------------------------

EXT_DB_FILE = TEST_DIR / "cninfo_ext.db"
#: 动态问答测试用的四家公司（与产品约定的名单一致）
DYNAMIC_CODES = ("688583", "600570", "000066", "300558")

#: 每家公司的文档：(file_name, page_count, {页码: 该页原文}, report_period)
_EXT_DOCS: dict[str, list[tuple[str, int, dict[int, str], str]]] = {}
#: 每家公司的证据：(document_index, metric, period, value, unit, page, source_quote, review_status)
_EXT_EVIDENCE: dict[str, list[tuple]] = {}
#: 刻意注入的脏数据：只属于 600570，用来验证"拦得住"
_DIRTY_EVIDENCE: list[tuple] = []


def _page(lines: list[str]) -> str:
    return "\n".join(lines)


def _build_extended_fixtures() -> None:
    """构造四家公司的文档/证据（模块导入时执行一次）。"""
    for code in DYNAMIC_CODES:
        docs: list[tuple[str, int, dict[int, str], str]] = []
        rows: list[tuple] = []

        # ---- 文档 0：2025 年半年度报告摘要（4 页）----
        rev_now, rev_prev, rev_pct = 176848509.44, 150248052.96, "17.70"
        np_now, np_prev, np_pct = 54007712.64, 52918429.73, "2.06"
        ocf_now, ocf_prev, ocf_pct = 31159083.37, 46225989.56, "-32.59"
        page1 = _page(
            [
                f"证券代码：{code} 证券简称：某公司",
                "2025 年半年度报告摘要",
                "2、主要会计数据和财务指标",
            ]
        )
        page2 = _page(
            [
                "本报告期 上年同期 本报告期比上年同期增减",
                f"营业收入（元） {rev_now:,.2f} {rev_prev:,.2f} {rev_pct}%",
                f"归属于上市公司股东的净利润（元） {np_now:,.2f} {np_prev:,.2f} {np_pct}%",
                f"经营活动产生的现金流量净额（元） {ocf_now:,.2f} {ocf_prev:,.2f} {ocf_pct}%",
                f"基本每股收益（元/股） 0.79 0.78 1.28%",
                f"总资产（元） 1234567890.12 1111111111.11 11.11%",
            ]
        )
        page3 = _page(["3、公司股东数量及持股情况", "报告期末普通股股东总数 10,000 户"])
        page4 = _page(["4、控股股东或实际控制人变更情况", "公司报告期控股股东未发生变更。"])
        docs.append(
            (f"20250828_{code}2025年半年度报告摘要.pdf", 4, {1: page1, 2: page2, 3: page3, 4: page4}, "2025FY")
        )
        rows.extend(
            [
                (0, "revenue", "2025FY", rev_now, "元", 2,
                 f"营业收入（元） | {rev_now:,.2f} | {rev_prev:,.2f} | {rev_pct}", "auto"),
                (0, "net_profit", "2025FY", np_now, "元", 2,
                 f"归属于上市公司股东的净利润（元） | {np_now:,.2f} | {np_prev:,.2f} | {np_pct}", "auto"),
                (0, "operating_cash_flow", "2025FY", ocf_now, "元", 2,
                 f"经营活动产生的现金流量净额（元） | {ocf_now:,.2f} | {ocf_prev:,.2f} | {ocf_pct}", "auto"),
                (0, "eps", "2025FY", 0.79, "元/股", 2, "基本每股收益（元/股） | 0.79 | 0.78 | 1.28", "auto"),
                (0, "total_assets", "2025FY", 1234567890.12, "元", 2,
                 "总资产（元） | 1,234,567,890.12 | 1,111,111,111.11 | 11.11", "auto"),
            ]
        )

        # ---- 文档 1：2024 年年度报告（6 页，带 chunks）----
        rev24, rev23, rev24_pct = 210000000.00, 180000000.00, "16.67"
        np24, np23, np24_pct = 66000000.00, 61000000.00, "8.20"
        ocf24, ocf23, ocf24_pct = 48000000.00, 55000000.00, "-12.73"
        y1 = _page([f"证券代码：{code}", "2024 年年度报告", "第一节 重要提示、目录和释义"])
        y2 = _page(["第二节 公司简介和主要财务指标"])
        y3 = _page(
            [
                "本报告期 上年同期 本报告期比上年同期增减",
                f"营业收入（元） {rev24:,.2f} {rev23:,.2f} {rev24_pct}%",
                f"归属于上市公司股东的净利润（元） {np24:,.2f} {np23:,.2f} {np24_pct}%",
                f"经营活动产生的现金流量净额（元） {ocf24:,.2f} {ocf23:,.2f} {ocf24_pct}%",
                f"基本每股收益（元/股） 0.98 0.91 7.69%",
            ]
        )
        y4 = _page(["第三节 管理层讨论与分析", "报告期内公司主营业务未发生重大变化。"])
        y5 = _page(["第四节 公司治理", "公司治理结构完善。"])
        y6 = _page(["第五节 环境和社会责任", "公司积极履行社会责任。"])
        docs.append(
            (
                f"20250428_{code}2024年年度报告.pdf",
                6,
                {1: y1, 2: y2, 3: y3, 4: y4, 5: y5, 6: y6},
                "2024FY",
            )
        )
        rows.extend(
            [
                (1, "revenue", "2024FY", rev24, "元", 3,
                 f"营业收入（元） | {rev24:,.2f} | {rev23:,.2f} | {rev24_pct}", "auto"),
                (1, "net_profit", "2024FY", np24, "元", 3,
                 f"归属于上市公司股东的净利润（元） | {np24:,.2f} | {np23:,.2f} | {np24_pct}", "auto"),
                (1, "operating_cash_flow", "2024FY", ocf24, "元", 3,
                 f"经营活动产生的现金流量净额（元） | {ocf24:,.2f} | {ocf23:,.2f} | {ocf24_pct}", "auto"),
                # 只有 2023 年那一列的证据：用来验证"报告期不同 → 候选不同"
                (1, "revenue", "2023FY", rev23, "元", 3,
                 f"营业收入（元） | {rev23:,.2f} | 150,000,000.00 | 20.00", "auto"),
            ]
        )

        # ---- 文档 2：2024 年半年度报告（3 页，无 chunks，走页界标记）----
        m1 = _page(["2024 年半年度报告", "一、重要提示"])
        m2 = _page(
            [
                "主要会计数据",
                f"营业收入（元） {110000000.00:,.2f} {95000000.00:,.2f} 15.79%",
                f"归属于上市公司股东的净利润（元） {33000000.00:,.2f} {29000000.00:,.2f} 13.79%",
            ]
        )
        m3 = _page(["二、公司基本情况", "报告期内公司经营情况稳定。"])
        docs.append(
            (f"20240830_{code}2024年半年度报告.pdf", 3, {1: m1, 2: m2, 3: m3}, "2024H1")
        )
        rows.extend(
            [
                (2, "revenue", "2024H1", 110000000.00, "元", 2,
                 "营业收入（元） | 110,000,000.00 | 95,000,000.00 | 15.79", "auto"),
                (2, "net_profit", "2024H1", 33000000.00, "元", 2,
                 "归属于上市公司股东的净利润（元） | 33,000,000.00 | 29,000,000.00 | 13.79", "auto"),
            ]
        )

        _EXT_DOCS[code] = docs
        _EXT_EVIDENCE[code] = rows

    # ---- 脏数据：只挂在 600570 上，三条都必须被拦下 ----
    _DIRTY_EVIDENCE.extend(
        [
            # a) 页码越界（文档只有 4 页，却声称第 9 页）
            (0, "revenue", "2025FY", 1.0, "元", 9, "营业收入（元） | 1.00 | 2.00", "auto"),
            # b) 页码缺失（NULL）
            (0, "net_profit", "2025FY", 1.0, "元", None,
             "归属于上市公司股东的净利润（元） | 1.00 | 2.00", "auto"),
            # c) 引文里的数字在该页上根本不存在（伪造引用）
            (1, "operating_cash_flow", "2024FY", 1.0, "元", 3,
             "经营活动产生的现金流量净额（元） | 999,999,999.99 | 888,888,888.88", "auto"),
        ]
    )


_build_extended_fixtures()


def build_extended_db(path: Path) -> None:
    """造一份带 docs/chunks/evidence 的扩展合成库。

    ⚠️ **必须用全新的路径**：Windows 上已打开的 SQLite 文件无法 unlink
       （会报 WinError 32）。所以每次测试用带 uuid 的独立文件名，
       而不是反复覆盖同一个 `cninfo_ext.db`。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_code TEXT, file_name TEXT, rel_path TEXT, page_count INTEGER,
            text_content TEXT, tables_json TEXT, created_at TEXT,
            document_type TEXT, published_at TEXT, report_period TEXT,
            source_url TEXT, parse_status TEXT DEFAULT 'ok', superseded INTEGER DEFAULT 0)"""
    )
    con.execute(
        """CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL, company_code TEXT NOT NULL,
            page_number INTEGER, chunk_index INTEGER, content TEXT NOT NULL,
            content_len INTEGER)"""
    )
    con.execute(
        """CREATE TABLE evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_code TEXT NOT NULL, document_id INTEGER NOT NULL,
            category TEXT, metric TEXT, period TEXT, value REAL, unit TEXT,
            content TEXT, source_page INTEGER, source_quote TEXT,
            method TEXT, review_status TEXT DEFAULT 'auto', created_at TEXT)"""
    )

    for code, docs in _EXT_DOCS.items():
        doc_ids: list[int] = []
        for file_name, page_count, pages, report_period in docs:
            # text_content 里放「--- 第N页 ---」页界标记（招股书那种没有 chunks 的文档）
            body = "".join(f"--- 第{page}页 ---\n{text}\n" for page, text in sorted(pages.items()))
            is_half_year = "半年度报告" in file_name and "摘要" not in file_name
            source_url = f"http://static.cninfo.com.cn/finalpage/2025-08-28/{code}0001.PDF"
            cursor = con.execute(
                "INSERT INTO docs (company_code, file_name, rel_path, page_count, text_content,"
                " tables_json, created_at, document_type, report_period, source_url,"
                " parse_status, superseded) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    code,
                    file_name,
                    f"{code}/{file_name}",
                    page_count,
                    body,
                    None,
                    "2026-10-02 01:00:00",
                    "annual_report" if "年度报告" in file_name else "q1_report",
                    report_period,
                    source_url,
                    "ok",
                    0,
                ),
            )
            document_id = int(cursor.lastrowid)
            doc_ids.append(document_id)
            # 半年报有 chunks（模拟真实库：年报/半年报切了页，招股书没切）
            if "半年度报告" in file_name and "摘要" not in file_name:
                continue
            for page, text in sorted(pages.items()):
                con.execute(
                    "INSERT INTO chunks (document_id, company_code, page_number, chunk_index,"
                    " content, content_len) VALUES (?,?,?,?,?,?)",
                    (document_id, code, page, 0, text, len(text)),
                )

        for row in _EXT_EVIDENCE[code]:
            doc_index, metric, period, value, unit, page, quote, review = row
            con.execute(
                "INSERT INTO evidence (company_code, document_id, category, metric, period,"
                " value, unit, content, source_page, source_quote, method, review_status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    code,
                    doc_ids[doc_index],
                    "financial",
                    metric,
                    period,
                    value,
                    unit,
                    f"{metric} {period} 摘要",
                    page,
                    quote,
                    "rule",
                    review,
                ),
            )

    for doc_index, metric, period, value, unit, page, quote, review in _DIRTY_EVIDENCE:
        doc_ids = [r["id"] for r in con.execute(
            "SELECT id FROM docs WHERE company_code = ? ORDER BY id", ("600570",)
        )]
        con.execute(
            "INSERT INTO evidence (company_code, document_id, category, metric, period, value,"
            " unit, content, source_page, source_quote, method, review_status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "600570",
                doc_ids[doc_index],
                "financial",
                metric,
                period,
                value,
                unit,
                f"{metric} {period} 脏数据",
                page,
                quote,
                "rule",
                review,
            ),
        )
    con.commit()
    con.close()


@pytest.fixture()
def extended_db(monkeypatch):
    """把 settings 指向扩展合成库（四家公司 + evidence），用完自动还原。

    ⚠️ 必须同时改 `DATABASE_PATH`：它在 config.Settings 里的优先级高于 `DB_PATH`
        （`_resolve_database_path_alias`），只改后者会被前者盖住，
        测试就会跑去连上面那份 8 列小库，动态链路永远是"没有 evidence 表"。
    ⚠️ 每次用**新的文件名**：Windows 上已打开过的库文件无法删除/覆盖。
    """
    import uuid

    path = TEST_DIR / f"cninfo_ext_{uuid.uuid4().hex[:10]}.db"
    build_extended_db(path)
    from config import settings

    for name in ("DB_PATH", "DATABASE_PATH"):
        monkeypatch.setattr(settings, name, str(path), raising=False)
    yield path
    # 清理尽力而为：文件可能仍被连接占用（Windows），失败不影响测试结果
    try:
        path.unlink()
    except OSError:
        pass
