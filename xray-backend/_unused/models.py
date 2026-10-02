"""X-Ray 企业穿透分析 · ORM 模型（SQLAlchemy 2.0）。

数据合同：X-Ray 数据字典 v0.4（免费渠道版）。
  * 只能使用字典里列出的 15 个字段名，不许自创字段；
  * 时间序列字段（*_3y）存 JSON 数组，顺序固定为 [T-2, T-1, T]，
    某年缺失必须为 null，不许插补（字典 Prompt 约束语句）；
  * latest_quarter_* 系列来自季报/半年报，禁止与年报做绝对值比较，
    仅用于方向性判断与同比计算。

需求[五] 表清单（8 张）+ 支撑 6 字段响应体所需的 3 张表：
  company / finance_fields / announcement / legal_risk / social_security /
  sync_log / answer_cache / signal_result
  + documents / claim_record / evidence（支撑 claims / evidence / charts）

⚠️ 关于 signal_result：signals 现在由 ask 接口在**请求时实时计算**
   （见 signal_engine.py + services/answer_service.py）。
   该表保留作为可选的落库位置，夜间采集不再写入。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """统一 UTC 时间戳（naive，便于 SQLite 比较与排序）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: 数据字典 v0.4 定义的 15 个字段名 —— 唯一合法集合，任何地方都不许自创
DICT_FIELD_NAMES: tuple[str, ...] = (
    "revenue_3y",
    "net_profit_3y",
    "oper_cashflow_3y",
    "accounts_receivable_3y",
    "asset_liability_ratio",
    "social_security_count_3y",
    "executed_count",
    "dishonest_count",
    "equity_pledge_count",
    "legal_case_count",
    "admin_penalty_count",
    "reg_capital",
    "latest_quarter_profit",
    "latest_quarter_revenue",
    "latest_quarter_cashflow",
)

#: 时间序列型字段（JSON 数组，长度 3）
SERIES_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "revenue_3y",
        "net_profit_3y",
        "oper_cashflow_3y",
        "accounts_receivable_3y",
        "social_security_count_3y",
    }
)

#: 计数型字段（整数累计值）
COUNT_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "executed_count",
        "dishonest_count",
        "equity_pledge_count",
        "legal_case_count",
        "admin_penalty_count",
    }
)

#: 标量型字段（单值）
SCALAR_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "asset_liability_ratio",
        "reg_capital",
        "latest_quarter_profit",
        "latest_quarter_revenue",
        "latest_quarter_cashflow",
    }
)

#: 4 对矛盾信号编号（字典第三节；其他任何结论不许输出）
SIGNAL_IDS: tuple[str, ...] = ("S1", "S2", "S3", "S4")

SIGNAL_NAMES: dict[str, str] = {
    "S1": "利润含金量疑云",
    "S2": "营收注水嫌疑",
    "S3": "人力数据异常",
    "S4": "司法风险直判",
}

#: 数据字典 v0.4 的 6 个免费官方渠道
SOURCE_CHANNELS: tuple[str, ...] = (
    "cninfo",  # 巨潮资讯网：财报 / 公告 / 股权质押 / 注册资本
    "gsxt",  # 国家企业信用信息公示系统：社保参保人数
    "zxgk",  # 中国执行信息公开网：被执行人 / 失信被执行人
    "wenshu",  # 中国裁判文书网：涉诉案件
    "creditchina",  # 信用中国：行政处罚
    "mock",  # 降级种子数据
)


def new_updated_at() -> Mapped[datetime]:
    """每张表统一的 updated_at 列定义（服务端默认 + 自动刷新）。"""
    return mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        server_default=func.current_timestamp(),
        onupdate=utcnow,
        index=True,
    )


# ---------------------------------------------------------------------------
# company —— 公司画像（工商基础信息）
# ---------------------------------------------------------------------------


class Company(Base):
    """公司表：全部 A 股上市公司，绝不写死单一公司。"""

    __tablename__ = "company"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # 工商基础信息（字典第 12 行 reg_capital 的画像冗余，便于 /profile 一次取齐）
    reg_capital: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 上市板块：科创板 / 主板 / 创业板 / 北交所
    board: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    updated_at: Mapped[datetime] = new_updated_at()

    # 关系（全部按 stock_code 归属，避免跨公司串数据）
    # 注意：不使用字符串形式的 primaryjoin —— FinanceField.stock_code 已有真实外键，
    # 交给 SQLAlchemy 自动解析最稳妥，避免首次 mapper 配置期才暴露的解析失败。
    finance_fields: Mapped[list["FinanceField"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Company {self.stock_code} {self.name}>"


# ---------------------------------------------------------------------------
# finance_fields —— 15 个字典字段的落地表（长表结构）
# ---------------------------------------------------------------------------


class FinanceField(Base):
    """财务字段长表：一行 = 一个字段在某期的值。

    field_name 必须属于 DICT_FIELD_NAMES；field_value 统一存 JSON 文本：
      * 时间序列字段 → JSON 数组，如 [1000000000, 1200000000, 1500000000]
      * 计数型字段   → JSON 整数，如 0
      * 标量型字段   → JSON 原值，如 45.2 / "5000万元"
    """

    __tablename__ = "finance_fields"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "field_name", "period", name="uq_finance_field_stock_name_period"
        ),
        Index("ix_finance_fields_stock_field", "stock_code", "field_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(
        String(16), ForeignKey("company.stock_code", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: JSON 序列化后的值（数组 / 整数 / 标量）
    field_value: Mapped[str] = mapped_column(Text, nullable=False)
    #: 报告期，如 "2021—2023" 或 "2025Q3"
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 来源渠道：cninfo / gsxt / zxgk / wenshu / creditchina / mock
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = new_updated_at()

    company: Mapped["Company"] = relationship(back_populates="finance_fields")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FinanceField {self.stock_code} {self.field_name} {self.period}>"


# ---------------------------------------------------------------------------
# announcement / legal_risk / social_security
# ---------------------------------------------------------------------------


class Announcement(Base):
    """公告表：巨潮资讯网定期公告（含股权质押）。"""

    __tablename__ = "announcement"
    __table_args__ = (Index("ix_announcement_stock_date", "stock_code", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 公告分类：定期报告 / 股权质押 / 其他
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Announcement {self.stock_code} {self.title[:20]}>"


class LegalRisk(Base):
    """司法风险表：裁判文书 / 被执行 / 失信（统一归集，按 case_type 区分）。"""

    __tablename__ = "legal_risk"
    __table_args__ = (Index("ix_legal_risk_stock_type", "stock_code", "case_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: 案件类型：被执行 / 失信 / 涉诉 / 行政处罚
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 涉案金额（元）；未知为 None
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 来源渠道（zxgk / wenshu / creditchina）
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LegalRisk {self.stock_code} {self.case_type} {self.amount}>"


class SocialSecurity(Base):
    """社保参保人数（按年）。字典第 6 行：部分年份可能缺失，不许插补。"""

    __tablename__ = "social_security"
    __table_args__ = (
        UniqueConstraint("stock_code", "year", name="uq_social_security_stock_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 参保人数；企业可选择不公示 → None
    count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 是否公示（False = 企业选择不公示）
    disclosed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, default="gsxt")
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SocialSecurity {self.stock_code} {self.year}={self.count}>"


# ---------------------------------------------------------------------------
# signal_result —— 4 对矛盾信号的计算结果
# ---------------------------------------------------------------------------


class SignalResult(Base):
    """信号结果表：夜间重算后写入；白天只读。"""

    __tablename__ = "signal_result"
    __table_args__ = (
        UniqueConstraint("stock_code", "rule_name", name="uq_signal_result_stock_rule"),
        Index("ix_signal_result_stock_side", "stock_code", "side"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: 规则编号 S1/S2/S3/S4
    rule_name: Mapped[str] = mapped_column(String(8), nullable=False)
    #: 是否命中
    triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: positive | negative（命中风险即 negative）
    side: Mapped[str] = mapped_column(String(16), nullable=False, default="positive")
    #: high | medium | low（按差值幅度分档）
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    #: 人类可读的判定理由，如「净利润8亿→12亿(+50%)，经营现金流9亿→7亿(-22%)」
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 命中所依据的字典字段名清单（JSON 数组）
    field_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SignalResult {self.stock_code} {self.rule_name} {self.severity}>"


# ---------------------------------------------------------------------------
# answer_cache —— 预生成答案，version 原子切换
# ---------------------------------------------------------------------------


class AnswerCache(Base):
    """答案缓存表：按 (stock_code, question_hash, version) 分片，绝不跨公司串数据。"""

    __tablename__ = "answer_cache"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "question_hash", "version", name="uq_answer_cache_stock_hash_version"
        ),
        Index("ix_answer_cache_lookup", "stock_code", "question_hash", "version"),
        Index("ix_answer_cache_stock_version", "stock_code", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: 标准化问题的稳定哈希（sha256 前 32 位）
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 原始问题文本（供 matcher 做相似度兜底）
    question_text: Mapped[str] = mapped_column(String(512), nullable=False)
    #: 标准化后的问题文本（去标点 + 同义词替换）
    normalized_question: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: 完整 6 字段响应体的 JSON（answer/claims/signals/charts/evidence/suggested_questions）
    answer_json: Mapped[str] = mapped_column(Text, nullable=False)
    #: 版本号：全部成功后原子切换；失败保留旧版本
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    #: 该条缓存是否当前生效版本
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    #: 生成方式：deepseek / fake（LLM_FAKE）/ template（降级兜底）
    generator: Mapped[str] = mapped_column(String(16), nullable=False, default="template")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AnswerCache {self.stock_code} v{self.version} {self.question_hash[:8]}>"


# ---------------------------------------------------------------------------
# documents / claim_record / evidence —— 支撑 claims / evidence / charts
# ---------------------------------------------------------------------------


class Document(Base):
    """文档表：招股书 / 年报 / 季报 / 公告，供 evidence 溯源。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 业务主键，如 DOC-001
    document_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    #: 招股说明书 / 年度报告 / 半年度报告 / 季度报告 / 公告
    doc_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, default="cninfo")
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document {self.document_id} {self.title[:20]}>"


class Evidence(Base):
    """证据表：与响应体 evidence 的 10 个字段一一对应。"""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("stock_code", "evidence_id", name="uq_evidence_stock_evidence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 业务主键，如 EV-001（按公司分片，避免跨公司重号）
    evidence_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: 关联文档业务键
    document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 冗余文档标题，避免前端二次查询
    document_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 原文摘录（可核查的原始表述）
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: 关联的字典字段名（JSON 数组），用于信号与图表的证据绑定
    field_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: 风险维度：盈利质量 / 现金真实性 / 资产健康度 / 经营稳定性 / 司法风险 /
    #:           股东行为 / 合规风险 / 工商基础信息 / 最新变化捕捉
    risk_dimension: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Evidence {self.evidence_id} {self.category}>"


class ClaimRecord(Base):
    """主张表：每条 claim 必须关联至少 1 个真实 evidence_id。"""

    __tablename__ = "claim_record"
    __table_args__ = (
        UniqueConstraint("stock_code", "claim_id", name="uq_claim_stock_claim"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 业务主键，如 CL-001
    claim_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: 该 claim 归属的问题哈希；NULL = 通用 claim（任何问题都可引用）
    question_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: evidence_id 列表（JSON 数组），不得为空
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 核查方式说明（可验证性证据）
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ClaimRecord {self.claim_id} {self.text[:20]}>"


# ---------------------------------------------------------------------------
# sync_log —— 同步日志
# ---------------------------------------------------------------------------


class SyncLog(Base):
    """同步日志表：夜间跑批的增量依据 + /health 的最近同步时间来源。"""

    __tablename__ = "sync_log"
    __table_args__ = (Index("ix_sync_log_stock_task", "stock_code", "task_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: NULL = 全局任务（如夜间跑批整体）
    stock_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    task_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: success / failed / partial / running
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    rows_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.current_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = new_updated_at()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SyncLog {self.task_name} {self.status} rows={self.rows_affected}>"


#: 需求[五] 点名的 8 张核心表
CORE_TABLES: tuple[str, ...] = (
    "company",
    "finance_fields",
    "announcement",
    "legal_risk",
    "social_security",
    "sync_log",
    "answer_cache",
    "signal_result",
)

#: 支撑 claims / evidence 的 3 张表
SUPPORT_TABLES: tuple[str, ...] = ("documents", "evidence", "claim_record")

#: 全部 ORM 模型
ALL_MODELS: tuple[type[Base], ...] = (
    Company,
    FinanceField,
    Announcement,
    LegalRisk,
    SocialSecurity,
    SignalResult,
    AnswerCache,
    Document,
    Evidence,
    ClaimRecord,
    SyncLog,
)


def has_field_name(field_name: str) -> bool:
    """硬性约束：只能用字典里的 15 个字段，不许自创字段。"""
    return field_name in DICT_FIELD_NAMES


def model_updated_at_audit() -> dict[str, bool]:
    """自检：逐表确认是否都有 updated_at（需求[五]硬规则）。"""
    report: dict[str, bool] = {}
    for model in ALL_MODELS:
        columns: Any = model.__table__.columns
        report[model.__table__.name] = "updated_at" in columns
    return report


__all__ = [
    "ALL_MODELS",
    "CORE_TABLES",
    "COUNT_FIELD_NAMES",
    "DICT_FIELD_NAMES",
    "SCALAR_FIELD_NAMES",
    "SERIES_FIELD_NAMES",
    "SIGNAL_IDS",
    "SIGNAL_NAMES",
    "SOURCE_CHANNELS",
    "SUPPORT_TABLES",
    "Announcement",
    "AnswerCache",
    "Base",
    "ClaimRecord",
    "Company",
    "Document",
    "Evidence",
    "FinanceField",
    "LegalRisk",
    "SignalResult",
    "SocialSecurity",
    "SyncLog",
    "has_field_name",
    "model_updated_at_audit",
    "utcnow",
]
