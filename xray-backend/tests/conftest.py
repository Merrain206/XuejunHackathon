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
