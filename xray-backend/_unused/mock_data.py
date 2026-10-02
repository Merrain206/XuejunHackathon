"""X-Ray 企业穿透分析 · Mock 种子数据 + init_mock()。

需求[九] / 数据字典第五节：
  * 至少 2 家以上不同行业的公司（科创板 + 主板）；
  * 同时包含正样本（健康公司）与负样本（风险公司，命中 S1+S2+S3+S4）；
  * 数值自洽；
  * 每家公司都提供通用问题模板的预生成答案、claims、evidence、suggested_questions；
  * init_mock(db) 一键灌库，main.py 首次启动自动调用（仅当 MOCK_MODE=true）。

字段口径严格遵循数据字典 v0.4：
  * 时间序列字段 = 3 个完整会计年度 [2022, 2023, 2024]，单位：元；
  * 缺失一律 None，不许插补（数据字典 Prompt 约束语句）；
  * latest_quarter_* 来自 2025Q3，不是完整会计年度，禁止与年报做绝对值比较；
  * 只用字典里的 15 个字段名，不许自创字段。

⚠️ 这里出现的企业、股票代码、金额全部为演示用虚构数据，与任何真实上市公司无关。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    Announcement,
    AnswerCache,
    ClaimRecord,
    Company,
    Document,
    Evidence,
    FinanceField,
    LegalRisk,
    SignalResult,
    SocialSecurity,
    SyncLog,
)

logger = logging.getLogger(__name__)

#: 三个完整会计年度（顺序固定 [T-2, T-1, T]）
FISCAL_YEARS: tuple[int, ...] = (2022, 2023, 2024)
#: 行情口径的报告期（数据字典示例写作 "2023-2025" 风格，这里用实际年份区间）
REPORT_PERIOD: str = "2022—2024"
#: 最新一期季报（不是完整会计年度）
LATEST_QUARTER_PERIOD: str = "2025Q3"

#: 通用问题模板（与前端固定的 5 个建议问题一致）
DEFAULT_QUESTIONS: tuple[str, ...] = (
    "该公司的营收情况如何？",
    "该公司的利润含金量如何？",
    "该公司的现金流是否健康？",
    "该公司的司法风险如何？",
    "该公司的整体风险如何？",
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MockCompany:
    """一家演示公司及其全部种子数据。"""

    stock_code: str
    name: str
    industry: str
    board: str
    listing_date: date
    reg_capital: str
    profile_extra: dict[str, Any]
    fields: dict[str, Any]
    documents: tuple[tuple[str, str, str, str], ...]
    evidence: tuple[tuple[str, str, str, str, str, int | None, str, str | None, tuple[str, ...], str], ...]
    claims: tuple[tuple[str, str, tuple[str, ...]], ...]
    announcements: tuple[tuple[str, str, tuple[str, ...] | None], ...] = ()
    legal_risks: tuple[tuple[str, str, float | None, str, date], ...] = ()
    social_security: tuple[tuple[int, int | None, bool], ...] = ()
    coverage_checked: tuple[str, ...] = ()
    coverage_missing: tuple[str, ...] = ()
    expected_hits: tuple[str, ...] = ()
    expected_severity: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 正样本：科创板 · 健康公司
# ---------------------------------------------------------------------------

HEALTHY_CODE = "688001"
RISK_CODE = "600001"

HEALTHY_FIELDS: dict[str, Any] = {
    # —— 盈利质量 ——
    "revenue_3y": [950_000_000, 1_100_000_000, 1_320_000_000],
    "net_profit_3y": [80_000_000, 100_000_000, 120_000_000],
    # —— 现金真实性 ——
    "oper_cashflow_3y": [90_000_000, 112_000_000, 131_000_000],
    # —— 资产健康度 ——
    "accounts_receivable_3y": [200_000_000, 228_000_000, 260_000_000],
    "asset_liability_ratio": 38.6,
    # —— 经营稳定性 ——
    "social_security_count_3y": [1450, 1520, 1600],
    # —— 司法风险 ——
    "executed_count": 0,
    "dishonest_count": 0,
    "legal_case_count": 2,
    # —— 股东行为 ——
    "equity_pledge_count": 0,
    # —— 合规风险 ——
    "admin_penalty_count": 0,
    # —— 工商基础信息 ——
    "reg_capital": "12000万元",
    # —— 最新变化捕捉（2025Q3，非完整年度）——
    "latest_quarter_profit": 34_000_000,
    "latest_quarter_revenue": 370_000_000,
    "latest_quarter_cashflow": 41_000_000,
}

HEALTHY = MockCompany(
    stock_code=HEALTHY_CODE,
    name="示例智造股份有限公司",
    industry="高端装备制造",
    board="科创板",
    listing_date=date(2020, 7, 22),
    reg_capital="12000万元",
    profile_extra={
        "主营业务": "工业智能检测装备与配套软件",
        "员工规模": "约1600人",
        "审计机构": "示例会计师事务所（特殊普通合伙）",
    },
    fields=HEALTHY_FIELDS,
    documents=(
        ("DOC-001", "示例智造股份有限公司 2024 年年度报告", "年度报告", REPORT_PERIOD),
        ("DOC-002", "示例智造股份有限公司 2025 年第三季度报告", "季度报告", LATEST_QUARTER_PERIOD),
        ("DOC-003", "示例智造股份有限公司 2024 年年度股东大会决议公告", "公告", "2025-05-16"),
    ),
    evidence=(
        (
            "EV-001",
            "financial",
            REPORT_PERIOD,
            "营业收入由 2022 年 9.50 亿元增至 2024 年 13.20 亿元，三年复合增长约 17.9%，逐年递增。",
            "DOC-001",
            "示例智造股份有限公司 2024 年年度报告",
            26,
            "近三年主要会计数据和财务指标：营业收入 2022 年 950,000,000 元，2023 年 1,100,000,000 元，2024 年 1,320,000,000 元。",
            None,
            ("revenue_3y",),
            "盈利质量",
        ),
        (
            "EV-002",
            "financial",
            REPORT_PERIOD,
            "应收账款由 2.00 亿元增至 2.60 亿元（+30.0%），低于同期营收增速（+38.9%），回款质量正常。",
            "DOC-001",
            "示例智造股份有限公司 2024 年年度报告",
            27,
            "应收账款 2022 年 200,000,000 元，2023 年 228,000,000 元，2024 年 260,000,000 元。",
            None,
            ("accounts_receivable_3y", "revenue_3y"),
            "资产健康度",
        ),
        (
            "EV-003",
            "financial",
            REPORT_PERIOD,
            "归母净利润由 0.80 亿元增至 1.20 亿元，经营活动现金流净额由 0.90 亿元增至 1.31 亿元，两者同向增长，利润有现金支撑。",
            "DOC-001",
            "示例智造股份有限公司 2024 年年度报告",
            28,
            "归属于上市公司股东的净利润 2024 年 120,000,000 元；经营活动产生的现金流量净额 2024 年 131,000,000 元。",
            None,
            ("net_profit_3y", "oper_cashflow_3y"),
            "现金真实性",
        ),
        (
            "EV-004",
            "employment",
            REPORT_PERIOD,
            "社保参保人数由 1450 人增至 1600 人，与营收增长同向，人员规模同步扩张。",
            "DOC-001",
            "示例智造股份有限公司 2024 年年度报告",
            92,
            "企业年报社保事项：2024 年参保人数 1600 人。",
            "https://www.gsxt.gov.cn",
            ("social_security_count_3y", "revenue_3y"),
            "经营稳定性",
        ),
        (
            "EV-005",
            "legal",
            "截至2025年第三季度",
            "被执行人记录 0 条、失信被执行人记录 0 条；存量诉讼 2 起，均为正常经营纠纷，无重大执行事项。",
            "DOC-002",
            "示例智造股份有限公司 2025 年第三季度报告",
            15,
            "报告期内公司不存在被列为失信被执行人的情形。",
            "http://zxgk.court.gov.cn",
            ("executed_count", "dishonest_count", "legal_case_count"),
            "司法风险",
        ),
    ),
    claims=(
        (
            "CL-001",
            "营收三年持续增长（9.50 亿 → 13.20 亿元），且应收账款增速低于营收增速，收入增长有回款质量支撑。",
            ("EV-001", "EV-002"),
        ),
        (
            "CL-002",
            "净利润与经营活动现金流同向增长（净利 0.80 亿 → 1.20 亿，现金流 0.90 亿 → 1.31 亿），利润含金量未见异常。",
            ("EV-003",),
        ),
        (
            "CL-003",
            "社保参保人数随营收同步扩张（1450 → 1600 人），人力数据与经营规模一致。",
            ("EV-004",),
        ),
        (
            "CL-004",
            "被执行人及失信被执行人记录均为 0 条，未发现司法风险信号。",
            ("EV-005",),
        ),
    ),
    announcements=(
        ("示例智造股份有限公司 2024 年年度报告", "定期报告", (REPORT_PERIOD,)),
        ("示例智造股份有限公司 2025 年第三季度报告", "定期报告", (LATEST_QUARTER_PERIOD,)),
        ("示例智造股份有限公司 2024 年年度股东大会决议公告", "其他", ("2025-05-16",)),
    ),
    legal_risks=(
        ("涉诉", None, "已结案", date(2023, 4, 11)),
        ("涉诉", None, "已结案", date(2024, 9, 3)),
    ),
    social_security=((2022, 1450, True), (2023, 1520, True), (2024, 1600, True)),
    coverage_checked=("财务报表", "社保人数", "司法风险", "最新一期季报"),
    coverage_missing=("表外负债", "关联方交易", "行政处罚"),
    # 正样本：4 条信号全部不命中
    expected_hits=(),
    expected_severity={},
)


# ---------------------------------------------------------------------------
# 负样本：主板 · 风险公司（命中 S1+S2+S3+S4）
# ---------------------------------------------------------------------------

RISK_FIELDS: dict[str, Any] = {
    # S1：净利润↑ 但 经营现金流↓（且 2024 已为负）
    "net_profit_3y": [50_000_000, 80_000_000, 120_000_000],
    "oper_cashflow_3y": [60_000_000, 20_000_000, -30_000_000],
    # S2：营收增长但应收账款增速远高于营收增速
    "revenue_3y": [1_000_000_000, 1_500_000_000, 2_000_000_000],
    "accounts_receivable_3y": [200_000_000, 450_000_000, 800_000_000],
    "asset_liability_ratio": 72.4,
    # S3：营收↑ 但 参保人数↓
    "social_security_count_3y": [3000, 2400, 1900],
    # S4：被执行 + 失信 > 0
    "executed_count": 5,
    "dishonest_count": 3,
    "legal_case_count": 41,
    # 股东行为 / 合规风险
    "equity_pledge_count": 7,
    "admin_penalty_count": 2,
    # 工商基础信息
    "reg_capital": "58000万元",
    # 最新变化捕捉（2025Q3）：净利润由正转负，经营现金流为负
    "latest_quarter_profit": -80_000_000,
    "latest_quarter_revenue": 600_000_000,
    "latest_quarter_cashflow": -120_000_000,
}

RISK = MockCompany(
    stock_code=RISK_CODE,
    name="示例重工股份有限公司",
    industry="通用设备制造",
    board="主板",
    listing_date=date(2011, 3, 18),
    reg_capital="58000万元",
    profile_extra={
        "主营业务": "工程机械与金属结构件制造",
        "员工规模": "约1900人（社保口径）",
        "审计机构": "示例联合会计师事务所（特殊普通合伙）",
        "审计意见": "带持续经营重大不确定性段落的无保留意见",
    },
    fields=RISK_FIELDS,
    documents=(
        ("DOC-001", "示例重工股份有限公司 2024 年年度报告", "年度报告", REPORT_PERIOD),
        ("DOC-002", "示例重工股份有限公司 2025 年第三季度报告", "季度报告", LATEST_QUARTER_PERIOD),
        ("DOC-003", "示例重工股份有限公司 2024 年年度权益分派实施公告", "公告", "2024-06-20"),
    ),
    evidence=(
        (
            "EV-001",
            "financial",
            REPORT_PERIOD,
            "归母净利润由 0.50 亿元增至 1.20 亿元（+140.0%），但经营活动现金流净额由 +0.60 亿元降至 -0.30 亿元，方向背离；2025Q3 单季净利润 -0.80 亿元，由正转负。",
            "DOC-001",
            "示例重工股份有限公司 2024 年年度报告",
            31,
            "归属于上市公司股东的净利润 2022 年 50,000,000 元，2023 年 80,000,000 元，2024 年 120,000,000 元；经营活动产生的现金流量净额 2024 年 -30,000,000 元。",
            None,
            ("net_profit_3y", "oper_cashflow_3y", "latest_quarter_profit", "latest_quarter_cashflow"),
            "现金真实性",
        ),
        (
            "EV-002",
            "financial",
            REPORT_PERIOD,
            "营收由 10.00 亿元增至 20.00 亿元（+100.0%），同期应收账款由 2.00 亿元增至 8.00 亿元（+300.0%），应收账款增速超出营收增速 200.0 个百分点。",
            "DOC-001",
            "示例重工股份有限公司 2024 年年度报告",
            32,
            "营业收入 2024 年 2,000,000,000 元；应收账款 2024 年 800,000,000 元。",
            None,
            ("revenue_3y", "accounts_receivable_3y", "latest_quarter_revenue"),
            "资产健康度",
        ),
        (
            "EV-003",
            "employment",
            REPORT_PERIOD,
            "社保参保人数由 3000 人降至 1900 人（-36.7%），同期营收增长 100.0%，人力规模与经营规模背离。",
            "DOC-001",
            "示例重工股份有限公司 2024 年年度报告",
            88,
            "企业年报社保事项：2022 年参保人数 3000 人，2023 年 2400 人，2024 年 1900 人。",
            "https://www.gsxt.gov.cn",
            ("social_security_count_3y", "revenue_3y"),
            "经营稳定性",
        ),
        (
            "EV-004",
            "litigation",
            "截至2025年第三季度",
            "被执行人记录 5 条、失信被执行人记录 3 条，另有存量涉诉案件 41 起。",
            "DOC-002",
            "示例重工股份有限公司 2025 年第三季度报告",
            19,
            "报告期内公司及子公司存在被列为被执行人的情形，详见本报告第六节重大事项。",
            "http://zxgk.court.gov.cn",
            ("executed_count", "dishonest_count", "legal_case_count"),
            "司法风险",
        ),
        (
            "EV-005",
            "shareholder",
            "截至2025年第三季度",
            "股权质押公告累计 7 次；行政处罚 2 次，资产负债率 72.4%。",
            "DOC-003",
            "示例重工股份有限公司 2024 年年度权益分派实施公告",
            4,
            "控股股东累计质押股份占其所持股份比例较高，公司已按规定履行披露义务。",
            "https://www.creditchina.gov.cn",
            ("equity_pledge_count", "admin_penalty_count", "asset_liability_ratio"),
            "股东行为",
        ),
    ),
    claims=(
        (
            "CL-001",
            "净利润三年增长 140.0%，但经营活动现金流由 +0.60 亿元降至 -0.30 亿元，2025Q3 单季净利润转为 -0.80 亿元，利润含金量存疑。",
            ("EV-001",),
        ),
        (
            "CL-002",
            "应收账款增速（+300.0%）显著高于营收增速（+100.0%），相差 200.0 个百分点，存在营收注水与回款恶化风险。",
            ("EV-002",),
        ),
        (
            "CL-003",
            "营收增长 100.0% 的同时社保参保人数下降 36.7%，人力数据与经营规模不匹配。",
            ("EV-003",),
        ),
        (
            "CL-004",
            "被执行人记录 5 条、失信被执行人记录 3 条，司法风险已实际发生。",
            ("EV-004",),
        ),
        (
            "CL-005",
            "股权质押 7 次、行政处罚 2 次、资产负债率 72.4%，叠加前述风险信号，整体风险偏高。",
            ("EV-005",),
        ),
    ),
    announcements=(
        ("示例重工股份有限公司 2024 年年度报告", "定期报告", (REPORT_PERIOD,)),
        ("示例重工股份有限公司 2025 年第三季度报告", "定期报告", (LATEST_QUARTER_PERIOD,)),
        ("示例重工股份有限公司关于控股股东股权质押的公告", "股权质押", ("2024-11-08",)),
        ("示例重工股份有限公司关于控股股东股权质押的公告", "股权质押", ("2025-03-14",)),
        ("示例重工股份有限公司 2024 年年度权益分派实施公告", "其他", ("2024-06-20",)),
    ),
    legal_risks=(
        ("被执行", 18_600_000.0, "执行中", date(2025, 2, 14)),
        ("被执行", 7_300_000.0, "执行中", date(2025, 5, 9)),
        ("被执行", 2_100_000.0, "执行中", date(2025, 6, 27)),
        ("被执行", 940_000.0, "已结案", date(2024, 12, 3)),
        ("被执行", 5_500_000.0, "执行中", date(2025, 8, 1)),
        ("失信", 12_000_000.0, "未履行", date(2025, 4, 18)),
        ("失信", 3_400_000.0, "未履行", date(2025, 7, 6)),
        ("失信", 1_800_000.0, "未履行", date(2025, 9, 12)),
        ("行政处罚", 300_000.0, "已缴纳", date(2024, 10, 22)),
        ("行政处罚", 150_000.0, "已缴纳", date(2025, 1, 15)),
        ("涉诉", 22_400_000.0, "一审中", date(2025, 3, 11)),
    ),
    social_security=((2022, 3000, True), (2023, 2400, True), (2024, 1900, True)),
    coverage_checked=("财务报表", "社保人数", "司法风险", "股权质押", "行政处罚", "最新一期季报"),
    coverage_missing=("表外负债", "关联方交易", "对外担保明细"),
    # 负样本：必须同时命中 S1+S2+S3+S4
    expected_hits=("S1", "S2", "S3", "S4"),
    expected_severity={"S1": "high", "S2": "high", "S3": "high", "S4": "high"},
)


#: 全部演示公司（正样本 + 负样本）
MOCK_COMPANIES: tuple[MockCompany, ...] = (HEALTHY, RISK)


# ---------------------------------------------------------------------------
# 查询工具
# ---------------------------------------------------------------------------


def mock_company(stock_code: str) -> MockCompany | None:
    """按股票代码取演示公司定义。"""
    return next((c for c in MOCK_COMPANIES if c.stock_code == stock_code), None)


def mock_fields(stock_code: str) -> dict[str, Any] | None:
    """取该公司全字段（信号引擎的直接输入格式）。"""
    company = mock_company(stock_code)
    return dict(company.fields) if company else None


def mock_evidence_rows(stock_code: str) -> list[dict[str, Any]]:
    """按 Evidence 表结构返回证据行。"""
    company = mock_company(stock_code)
    if not company:
        return []
    rows: list[dict[str, Any]] = []
    for (
        ev_id,
        category,
        period,
        content,
        doc_id,
        doc_title,
        page,
        quote,
        url,
        field_names,
        dimension,
    ) in company.evidence:
        rows.append(
            {
                "evidence_id": ev_id,
                "stock_code": stock_code,
                "category": category,
                "period": period,
                "content": content,
                "document_id": doc_id,
                "document_title": doc_title,
                "source_page": page,
                "source_quote": quote,
                "source_url": url,
                "field_names": list(field_names),
                "risk_dimension": dimension,
            }
        )
    return rows


def mock_claim_rows(stock_code: str, question_hash: str | None = None) -> list[dict[str, Any]]:
    """按 ClaimRecord 表结构返回 claim 行。"""
    company = mock_company(stock_code)
    if not company:
        return []
    return [
        {
            "claim_id": claim_id,
            "stock_code": stock_code,
            "question_hash": question_hash,
            "text": text,
            "evidence_ids": list(evidence_ids),
            "verified": True,
            "verification_note": "证据均来自上述定期报告披露原文，可逐条回查。",
        }
        for claim_id, text, evidence_ids in company.claims
    ]


def evidence_rows_to_response(stock_code: str, evidence_ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """把证据行转成响应体 evidence[] 的字段（与附件 README 的 10 字段对齐）。"""
    rows = mock_evidence_rows(stock_code)
    if evidence_ids is not None:
        wanted = set(evidence_ids)
        rows = [r for r in rows if r["evidence_id"] in wanted]
    return [
        {
            "id": r["evidence_id"],
            "category": r["category"],
            "period": r["period"],
            "content": r["content"],
            "document_id": r["document_id"],
            "document_title": r["document_title"],
            "source_page": r["source_page"],
            "source_quote": r["source_quote"],
            "source_url": r["source_url"],
            "field_names": r["field_names"],
            "risk_dimension": r["risk_dimension"],
        }
        for r in rows
    ]


def mock_coverage(stock_code: str) -> dict[str, Any] | None:
    """覆盖情况：checked / missing / note（数据字典第 6 节强制字段）。"""
    company = mock_company(stock_code)
    if not company:
        return None
    return {
        "checked": list(company.coverage_checked),
        "missing": list(company.coverage_missing),
        "note": "本报告仅基于公开披露信息，不构成投资或信贷建议",
    }


# ---------------------------------------------------------------------------
# 灌库
# ---------------------------------------------------------------------------


def _dump(value: Any) -> str:
    """字段值统一以 JSON 文本入库（含小数必须能无损还原）。"""
    return json.dumps(value, ensure_ascii=False)


def _to_date(value: Any) -> date | None:
    """公告/案件日期支持 date 或 'YYYY-MM-DD' 字符串。"""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def clear_mock(db: Session) -> dict[str, int]:
    """清空全部业务表（保留结构）；顺序照顾外键依赖。"""
    order = (
        ClaimRecord,
        Evidence,
        Document,
        SignalResult,
        AnswerCache,
        SocialSecurity,
        LegalRisk,
        Announcement,
        FinanceField,
        SyncLog,
        Company,
    )
    removed: dict[str, int] = {}
    for model in order:
        removed[model.__tablename__] = db.query(model).delete()
    db.flush()
    return removed


def init_mock(db: Session, *, force: bool = False) -> dict[str, int]:
    """一键灌库。

    :param force: True 时先清空再灌（重复演示/测试用）；False 时若已有公司数据则跳过。
    :returns: 各表写入行数；跳过时返回 {"skipped": 1}
    """
    existing = db.scalar(select(func.count()).select_from(Company)) or 0
    if existing and not force:
        logger.info("company 表已有 %d 条记录，跳过 mock 灌库", existing)
        return {"skipped": 1}

    if force:
        clear_mock(db)

    counts: dict[str, int] = {}
    now_period = REPORT_PERIOD

    for mock in MOCK_COMPANIES:
        db.add(
            Company(
                stock_code=mock.stock_code,
                name=mock.name,
                industry=mock.industry,
                listing_date=mock.listing_date,
                reg_capital=mock.reg_capital,
                board=mock.board,
            )
        )

        # finance_fields：15 个字段逐一落长表
        for field_name, value in mock.fields.items():
            period = LATEST_QUARTER_PERIOD if field_name.startswith("latest_quarter_") else now_period
            source = {
                "social_security_count_3y": "gsxt",
                "executed_count": "zxgk",
                "dishonest_count": "zxgk",
                "legal_case_count": "wenshu",
                "admin_penalty_count": "creditchina",
                "equity_pledge_count": "cninfo",
            }.get(field_name, "cninfo")
            db.add(
                FinanceField(
                    stock_code=mock.stock_code,
                    field_name=field_name,
                    field_value=_dump(value),
                    period=period,
                    source=source,
                )
            )

        # documents
        for doc_id, title, doc_type, period in mock.documents:
            db.add(
                Document(
                    document_id=doc_id,
                    stock_code=mock.stock_code,
                    title=title,
                    doc_type=doc_type,
                    period=period,
                    url=None,
                    source="cninfo",
                )
            )

        # evidence / claim_record
        for row in mock_evidence_rows(mock.stock_code):
            db.add(Evidence(**row))
        for row in mock_claim_rows(mock.stock_code):
            db.add(ClaimRecord(**row))

        # announcement
        for title, category, dates in mock.announcements:
            raw_date = dates[0] if dates else None
            db.add(
                Announcement(
                    stock_code=mock.stock_code,
                    title=title,
                    date=_to_date(raw_date),
                    url=None,
                    content_snippet=None,
                    category=category,
                )
            )

        # legal_risk
        for case_type, amount, status, case_date in mock.legal_risks:
            source = {
                "被执行": "zxgk",
                "失信": "zxgk",
                "涉诉": "wenshu",
                "行政处罚": "creditchina",
            }.get(case_type, "mock")
            db.add(
                LegalRisk(
                    stock_code=mock.stock_code,
                    case_type=case_type,
                    amount=amount,
                    status=status,
                    date=case_date,
                    source=source,
                    detail=None,
                )
            )

        # social_security
        for year, count, disclosed in mock.social_security:
            db.add(
                SocialSecurity(
                    stock_code=mock.stock_code,
                    year=year,
                    count=count,
                    disclosed=disclosed,
                    source="gsxt",
                )
            )

        counts[mock.stock_code] = 1

    db.flush()
    logger.info(
        "mock 灌库完成：%d 家公司（%s）",
        len(MOCK_COMPANIES),
        "、".join(f"{c.stock_code}{c.name}" for c in MOCK_COMPANIES),
    )
    counts["companies"] = len(MOCK_COMPANIES)
    return counts


def mock_summary() -> str:
    """自检/日志用：列出演示公司与预期命中信号。"""
    lines = []
    for c in MOCK_COMPANIES:
        hits = "、".join(c.expected_hits) if c.expected_hits else "无（健康样本）"
        lines.append(f"  {c.stock_code} {c.name}（{c.board}·{c.industry}）→ 预期命中: {hits}")
    return "演示公司：\n" + "\n".join(lines)


__all__ = [
    "DEFAULT_QUESTIONS",
    "FISCAL_YEARS",
    "HEALTHY",
    "HEALTHY_CODE",
    "LATEST_QUARTER_PERIOD",
    "MOCK_COMPANIES",
    "MockCompany",
    "REPORT_PERIOD",
    "RISK",
    "RISK_CODE",
    "clear_mock",
    "evidence_rows_to_response",
    "init_mock",
    "mock_claim_rows",
    "mock_company",
    "mock_coverage",
    "mock_evidence_rows",
    "mock_fields",
    "mock_summary",
]

if __name__ == "__main__":  # pragma: no cover - 手动自检
    import sys

    try:
        from signal_engine import compute_signals, summarize
    except ModuleNotFoundError:
        sys.exit("缺少依赖（sqlalchemy），请先 pip install -r requirements.txt")

    print(mock_summary())
    for company in MOCK_COMPANIES:
        outcomes = compute_signals(dict(company.fields))
        stats = summarize(outcomes)
        got = tuple(stats["hit_rule_ids"])
        ok = got == company.expected_hits
        print(f"\n{company.stock_code} {company.name}")
        print(f"  实际命中: {got or '无'}   预期: {company.expected_hits or '无'}   {'✅' if ok else '❌ 不一致'}")
        print(f"  最高严重度: {stats['top_severity']}   覆盖维度: {stats['dimensions']}")
        for s in outcomes:
            flag = "命中" if s.triggered else "未命中"
            print(f"    [{flag}] {s.type} {s.title} / {s.side} / {s.severity}")
            print(f"           {s.description}")
        if ok and company.expected_severity:
            bad = {s.type: s.severity for s in outcomes if s.triggered and s.severity != company.expected_severity.get(s.type)}
            print(f"  严重度是否符合预期: {'✅' if not bad else '❌ ' + str(bad)}")
