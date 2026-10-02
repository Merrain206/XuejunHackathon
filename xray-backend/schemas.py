"""X-Ray 企业穿透分析 · Pydantic v2 请求/响应模型。

响应契约（锁定，不得改动）：
    顶层键恰好 6 个，顺序为：
        answer / claims / signals / charts / evidence / suggested_questions

  * 【严格禁止】不得出现 hit_cache / latency_ms / stock_code 等前端未定义的字段
    （测试用例显式断言这一点）。
  * claims 用 evidence_ids；evidence 带出处字段（document_title / source_page / source_quote）。
  * 本模块**不再依赖 ORM 或数据字典常量模块** —— 新数据源是 cninfo.db 的原始公告文本，
    没有结构化字段，因此信号类型、风险维度一律在此就地定义。

注意：signals 仍约束为 S1-S4 四类固定风险主题（见 analyzer.py 的 prompt）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# 枚举（用 Literal 表达，前端可直接按字符串用）
# ---------------------------------------------------------------------------

SignalSide = Literal["positive", "negative"]
SignalSeverity = Literal["high", "medium", "low"]
#: 风险主题编号（analyzer.py 的 prompt 只允许这 4 类结论）
SignalType = Literal["S1", "S2", "S3", "S4"]
#: 同一份清单的元组形式，供热解校验与错误提示复用
ALLOWED_SIGNAL_TYPES: tuple[str, ...] = ("S1", "S2", "S3", "S4")

#: charts[].kind —— 前端据此选渲染器
ChartKind = Literal["line", "bar", "pie", "donut", "table"]

#: 风险维度（用于 evidence.risk_dimension，方便前端画维度图）
RiskDimension = Literal[
    "盈利质量",
    "现金真实性",
    "资产健康度",
    "经营稳定性",
    "司法风险",
    "股东行为",
    "合规风险",
    "工商基础信息",
    "最新变化捕捉",
    "其他",
]

#: 证据来源渠道（新版只有巨潮公告 + 缓存/降级）
DataSource = Literal["cninfo", "cninfo_cache", "llm", "mock"]


class _StrictModel(BaseModel):
    """统一基类：禁止多余字段（防止私自加字段）。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    """POST /companies/{stock_code}/ask 请求体。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: Annotated[str, Field(min_length=1, max_length=500, description="用户问题")]

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question 不能为空白字符")
        return value.strip()


# ---------------------------------------------------------------------------
# 响应子结构
# ---------------------------------------------------------------------------


class Claim(_StrictModel):
    """主张：必须可被真实证据支撑。"""

    id: Annotated[str, Field(min_length=1, max_length=64, description="如 CL-001")]
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    #: 至少 1 个真实 evidence_id —— 没有证据就不许下结论
    evidence_ids: Annotated[list[str], Field(min_length=1)]
    verified: bool = False
    verification_note: str | None = None

    @field_validator("evidence_ids")
    @classmethod
    def _dedupe(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            item = item.strip()
            if item and item not in seen:
                seen.append(item)
        if not seen:
            raise ValueError("evidence_ids 不能为空：无证据不得生成 claim")
        return seen


class Signal(_StrictModel):
    """4 对矛盾信号的输出结构。"""

    id: Annotated[str, Field(min_length=2, max_length=8)]
    type: SignalType
    title: Annotated[str, Field(min_length=1, max_length=64)]
    side: SignalSide
    severity: SignalSeverity
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    #: 命中所依据的字典字段名
    evidence_ids: Annotated[list[str], Field(min_length=1)]

    @field_validator("type")
    @classmethod
    def _known_signal(cls, value: str) -> str:
        if value not in ALLOWED_SIGNAL_TYPES:
            raise ValueError(f"信号编号必须是 {ALLOWED_SIGNAL_TYPES} 之一，收到 {value!r}")
        return value

    @model_validator(mode="after")
    def _id_matches_type(self) -> "Signal":
        if self.id != self.type:
            raise ValueError(f"signal.id 必须等于 type（{self.type}），收到 id={self.id!r}")
        return self

    @field_validator("evidence_ids")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("signal.evidence_ids 不能为空")
        return cleaned


class Evidence(_StrictModel):
    """证据：必须可回溯到具体公告的原文片段。

    ⚠️ 新版数据源是公库原文（cninfo.db 的公告正文），没有结构化财务字段，
    因此不再有 field_names；取而代之的是公告出处（document_title / source_page /
    source_quote），保证「结论必须附原文引用」这条硬约束可核查。
    """

    id: Annotated[str, Field(min_length=1, max_length=64, description="如 EV-001")]
    #: 证据类别：announcement / financial / litigation / employment / shareholder / other
    category: Annotated[str, Field(min_length=1, max_length=64)]
    #: 公告日期或期间，如 "2024-04-19" 或 "2024年年度"
    period: str | None = None
    #: 证据摘要（LLM 归纳）
    content: Annotated[str, Field(min_length=1, max_length=2000)]
    #: 公告业务 id / 文件名（来自 cninfo.db）
    document_id: str | None = None
    #: 公告标题
    document_title: str | None = None
    source_page: int | None = Field(default=None, ge=1)
    #: ★ 原文摘录（抄自公告正文，用于人工核验）
    source_quote: str | None = None
    #: 公告链接
    source_url: str | None = None
    #: 风险维度（前端可直接画维度图）
    risk_dimension: RiskDimension | None = None

    @model_validator(mode="after")
    def _quote_required(self) -> "Evidence":
        """硬约束：证据必须有原文摘录，否则不算证据。

        这是「结论必须附带原文引用」在契约层的落实 —— 没有 quote 的
        evidence 不允许进入响应体。
        """
        if not (self.source_quote or "").strip():
            raise ValueError(f"evidence {self.id} 缺少 source_quote：证据必须附原文引用")
        return self


class ChartPoint(_StrictModel):
    """图表上的一个数据点。"""

    label: str
    #: 缺值点保留 null（不许用 0 冒充）
    value: float | None = None


class ChartSeries(_StrictModel):
    """一条数据序列。"""

    name: Annotated[str, Field(min_length=1, max_length=64)]
    points: Annotated[list[ChartPoint], Field(min_length=1)]


class ChartSpec(_StrictModel):
    """响应体 charts[] 的元素。

    charts 由服务端**确定性生成**（charts.py），不经过大模型；
    每个图都必须挂真实 evidence_ids，保证图上的数字可回查。
    """

    id: Annotated[str, Field(min_length=1, max_length=64)]
    title: Annotated[str, Field(min_length=1, max_length=64)]
    kind: ChartKind
    unit: str | None = None
    series: Annotated[list[ChartSeries], Field(min_length=1)]
    evidence_ids: Annotated[list[str], Field(min_length=1)]
    note: str | None = None

    @field_validator("evidence_ids")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("chart.evidence_ids 不能为空：图表数据必须有出处")
        return cleaned

    @model_validator(mode="after")
    def _series_align(self) -> "ChartSpec":
        """同一图内所有序列的点数必须一致（否则前端画错位）。"""
        lengths = {len(s.points) for s in self.series}
        if len(lengths) > 1:
            raise ValueError(f"chart {self.id} 的各序列点数不一致: {sorted(lengths)}")
        return self


class CompanyProfile(_StrictModel):
    """GET /companies/{stock_code}/profile 响应。

    ⚠️ 新版没有结构化财务字段（数据源是 cninfo.db 的公告原文），
    因此画像给的是「公告覆盖情况」而不是 15 个财务字段。
    """

    stock_code: str
    name: str
    #: 该公司在库中的公告数量
    announcement_count: int = 0
    #: 公告日期区间
    first_announcement_date: str | None = None
    last_announcement_date: str | None = None
    #: 最近若干条公告标题（便于前端展示"这家公司有什么材料"）
    recent_titles: list[str] = Field(default_factory=list)
    #: 该公司的风险分析摘要（来自缓存；未分析过则为 None）
    risk_level: str | None = None
    summary: str | None = None
    #: 分析结果生成时间与来源（cache / fresh）
    analyzed_at: str | None = None


class SignalListResponse(_StrictModel):
    """GET /companies/{stock_code}/signals 响应。"""

    stock_code: str
    signals: list[Signal] = Field(default_factory=list)
    computed_at: str | None = None
    #: 结论来源：cache（读缓存）/ fresh（本次实时分析）
    source: str | None = None


# ---------------------------------------------------------------------------
# 主响应 —— 顶层恰好 6 个键
# ---------------------------------------------------------------------------


class AskResponse(BaseModel):
    """POST /companies/{stock_code}/ask 响应体。

    顶层恰好 6 个字段，顺序固定：
        answer / claims / signals / charts / evidence / suggested_questions

    ⚠️ extra="forbid"：任何私自新增的顶层字段都会在构造时直接报错，
    这是「响应体只能有约定字段」的机器强制，而不是靠人自觉。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer: Annotated[str, Field(min_length=1, max_length=4000)]
    claims: list[Claim] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)

    # ---------------- 硬性校验 ----------------

    @field_validator("suggested_questions")
    @classmethod
    def _questions_ok(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("suggested_questions 不能为空")
        return cleaned

    @model_validator(mode="after")
    def _answer_required(self) -> "AskResponse":
        """answer 不能为空 —— 但 claims / signals / evidence **允许为空数组**。

        为什么放开：prompt 明确允许 LLM 回答「无足够信息」（公告里找不到依据时），
        那种情况不该硬塞一条没有原文引用的 claim 来凑数。
        契约仍然强制：**只要有 claim/signal/chart，其 evidence_id 必须可解析**
        （见 _evidence_ids_resolve），且每条 evidence 必须带 source_quote
        （见 Evidence._quote_required）。
        """
        if not self.answer.strip():
            raise ValueError("answer 不能为空（无足够信息也要给出说明）")
        return self

    @model_validator(mode="after")
    def _evidence_ids_resolve(self) -> "AskResponse":
        """claim / signal / chart 的 evidence_id 必须能在 evidence 里找到。

        这是「不许编造证据」的结构性保证：悬空引用直接拒绝。
        """
        known = {e.id for e in self.evidence}
        for chart in self.charts:
            dangling = [i for i in chart.evidence_ids if i not in known]
            if dangling:
                raise ValueError(f"chart {chart.id} 引用了不存在的证据: {dangling}")
        for claim in self.claims:
            dangling = [i for i in claim.evidence_ids if i not in known]
            if dangling:
                raise ValueError(f"claim {claim.id} 引用了不存在的证据: {dangling}")
        for signal in self.signals:
            dangling = [i for i in signal.evidence_ids if i not in known]
            if dangling:
                raise ValueError(f"signal {signal.id} 引用了不存在的证据: {dangling}")
        return self

    @model_validator(mode="after")
    def _no_orphan_evidence(self) -> "AskResponse":
        """反向检查：不许有既不被 claim/signal 支撑、也不被 chart 引用的孤立证据。"""
        used: set[str] = set()
        for claim in self.claims:
            used.update(claim.evidence_ids)
        for signal in self.signals:
            used.update(signal.evidence_ids)
        for chart in self.charts:
            used.update(chart.evidence_ids)
        orphan = [e.id for e in self.evidence if e.id not in used]
        if orphan:
            raise ValueError(f"存在未被任何 claim/signal/chart 引用的孤立证据: {orphan}")
        return self

    @model_validator(mode="after")
    def _chart_ids_unique(self) -> "AskResponse":
        ids = [c.id for c in self.charts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"charts 内 id 重复: {ids}")
        return self

    # ---------------- 便捷出口 ----------------

    def to_dict(self) -> dict[str, Any]:
        """给前端/answer_cache 的完整字典（JSON 安全）。"""
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 错误响应 + 健康检查
# ---------------------------------------------------------------------------


class ErrorResponse(_StrictModel):
    """需求[八].5：统一返回 {"error": "...", "detail": "..."}，不让前端白屏。"""

    error: str
    detail: str = ""


class HealthResponse(_StrictModel):
    """GET /health：健康检查 + 最近一次同步时间。"""

    status: Literal["ok", "degraded"]
    app: str
    version: str
    database: bool
    mock_mode: bool
    scheduler_enabled: bool
    last_sync_at: str | None = None
    last_sync_status: str | None = None
    companies: int = 0
    llm_ready: bool = False
    llm_model: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class AdminRefreshResponse(_StrictModel):
    """POST /admin/refresh 响应。"""

    status: Literal["accepted", "completed", "failed"]
    stock_code: str | None = None
    detail: str = ""


__all__ = [
    "ALLOWED_SIGNAL_TYPES",
    "AdminRefreshResponse",
    "AskRequest",
    "AskResponse",
    "ChartKind",
    "ChartPoint",
    "ChartSeries",
    "ChartSpec",
    "Claim",
    "CompanyProfile",
    "DataSource",
    "ErrorResponse",
    "Evidence",
    "HealthResponse",
    "RiskDimension",
    "Signal",
    "SignalListResponse",
    "SignalSeverity",
    "SignalSide",
    "SignalType",
]
