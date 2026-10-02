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
#: 信号类型 / 严重度 —— 取值必须与前端 xuejun-hackathon/src/lib/api.ts
#: 的运行时校验器**完全一致**。前端校验不通过会静默降级为 mock 并显示
#: DEMO FALLBACK，所以这里的每个取值都是硬约束，不是风格偏好。
SignalType = Literal["divergence", "trend", "attention"]
SignalSeverity = Literal["attention", "positive"]
ALLOWED_SIGNAL_TYPES: tuple[str, ...] = ("divergence", "trend", "attention")
ALLOWED_SIGNAL_SEVERITIES: tuple[str, ...] = ("attention", "positive")

#: 内部风险主题（S1-S4）→ 前端 signal type 的映射。
#: 内部仍用 S1-S4 做 LLM prompt 的约束（结构清晰、便于对账），
#: 对外则翻译成前端认识的三类。
SIGNAL_TYPE_MAP: dict[str, str] = {
    "S1": "divergence",   # 利润与现金流背离
    "S2": "attention",    # 营收与应收背离 → 需要关注
    "S3": "attention",    # 人员与规模背离 → 需要关注
    "S4": "attention",    # 司法合规风险 → 需要关注
}

#: 内部 severity（high/medium/low）→ 前端 severity（attention/positive）
SEVERITY_MAP: dict[str, str] = {
    "high": "attention",
    "medium": "attention",
    "low": "positive",
}

#: 前端只支持折线图（api.ts 里写死 `type !== "line"` 即判不合法）
ChartKind = Literal["line"]

#: 证据类别 —— 前端校验器只接受这三个值
EvidenceCategory = Literal["financial", "business", "company"]

#: 证据核验状态（**可选字段**，前端不校验；缺省时前端按"未标注"处理）。
#:
#:   * `verified` —— 已人工核验（思看科技现有 Demo 证据 + 库里 review_status=verified
#                   且本次原文页码复核通过的行）；
#:   * `auto`     —— 本次请求已完成机械核验（页码、原文逐字、链接都对得上）；
#:   * `pending`  —— 未通过核验。**不返回**：不通过的证据根本不会进响应，
#:                   宁可证据不足，也不给一条无法回溯的出处。
#:
#: 设为可选是刻意的：现有前端与已发布样例都没有这个字段，加必填会直接破坏兼容。
VerificationStatus = Literal["verified", "auto", "pending"]


def to_signal_type(rule_id: str) -> str:
    """内部规则编号（S1-S4）→ 前端 signal type。"""
    return SIGNAL_TYPE_MAP.get((rule_id or "").strip().upper(), "attention")


def to_severity(level: str) -> str:
    """内部 severity → 前端 severity。"""
    return SEVERITY_MAP.get((level or "").strip().lower(), "attention")


def to_evidence_category(hint: str | None) -> str:
    """把内部/模型的类别提示归一到前端接受的三个值。

    前端 isEvidence() 只认 financial / business / company，
    给别的值会导致**整包响应**被判定非法 → 静默降级 mock。

    ⚠️ 新库里 `evidence.category` 出现了第四个取值 `risk`（手工核验的风险因素证据，
       如 688583 的 `risk_product_mix`）。它必须映射到 **business** ——
       前端契约只认三个值，而任务书明确要求"不得修改前端"。
       风险因素属于公告主体内容，归到 business 语义上也站得住。
    """
    text = (hint or "").strip().lower()
    if text in ("financial", "business", "company"):
        return text
    if text == "risk":
        return "business"
    if any(k in text for k in ("财务", "会计", "利润", "现金", "营收", "资产", "financial")):
        return "financial"
    if any(k in text for k in ("工商", "基础", "company", "注册", "股东")):
        return "company"
    # 其余一律归 business（公告主体内容）
    return "business"

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
    """主张：必须可被真实证据支撑。

    字段严格对齐前端 types.ts 的 Claim（只有 id / text / evidence_ids）——
    多加字段前端不会读，反而让契约含糊。
    """

    id: Annotated[str, Field(min_length=1, max_length=64, description="如 CL-001")]
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    #: 至少 1 个真实 evidence_id —— 没有证据就不许下结论
    evidence_ids: Annotated[list[str], Field(min_length=1)]

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
    """风险信号 —— 字段与取值严格对齐前端 api.ts 的 isSignal()。

    前端校验要求：id / title / description 非空字符串，
    type ∈ {divergence,trend,attention}，severity ∈ {attention,positive}，
    evidence_ids 非空且都能在 evidence[] 里找到。
    """

    #: 形如 SIG-S1 —— 前端只要求非空（样例里是 SIG-STR-001）
    id: Annotated[str, Field(min_length=1, max_length=64)]
    type: SignalType
    title: Annotated[str, Field(min_length=1, max_length=64)]
    severity: SignalSeverity
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    evidence_ids: Annotated[list[str], Field(min_length=1)]

    @field_validator("type")
    @classmethod
    def _known_signal(cls, value: str) -> str:
        if value not in ALLOWED_SIGNAL_TYPES:
            raise ValueError(
                f"signal.type 必须是 {ALLOWED_SIGNAL_TYPES} 之一（前端契约），收到 {value!r}"
            )
        return value

    @field_validator("severity")
    @classmethod
    def _known_severity(cls, value: str) -> str:
        if value not in ALLOWED_SIGNAL_SEVERITIES:
            raise ValueError(
                f"signal.severity 必须是 {ALLOWED_SIGNAL_SEVERITIES} 之一（前端契约），"
                f"收到 {value!r}"
            )
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("signal.evidence_ids 不能为空")
        return cleaned


class Evidence(_StrictModel):
    """证据：必须可回溯到具体公告的原文片段。

    ⚠️ 字段与取值严格对齐前端 api.ts 的 isEvidence()：
      * category 只接受 financial / business / company；
      * document_id 非空字符串；
      * source_page 必须是**正整数**（不接受 null）；
      * source_url 必须是合法 http(s) URL（不接受 null）；
      * source_quote 非空。
    任意一条不满足 → 前端判定整包响应非法 → **静默降级为 mock**。
    """

    id: Annotated[str, Field(min_length=1, max_length=64, description="如 EV-001")]
    #: 前端只认这三个值
    category: EvidenceCategory
    #: 公告日期或期间，如 "2024-04-19" 或 "2024 年上半年"
    period: Annotated[str, Field(min_length=1, max_length=64)]
    #: 证据摘要（LLM 归纳）
    content: Annotated[str, Field(min_length=1, max_length=2000)]
    #: 公告 id（来自 cninfo.db 的 docs.id），必须是字符串
    document_id: Annotated[str, Field(min_length=1, max_length=64)]
    #: 公告标题
    document_title: str | None = None
    #: 页码：前端要求 >0 的整数。库里给不出单条引用的精确页码时，
    #: 用 1（公告起始页）并在 content 里说明，而不是传 null 让前端降级。
    source_page: int = Field(default=1, ge=1)
    #: ★ 原文摘录（抄自公告正文，用于人工核验）
    source_quote: Annotated[str, Field(min_length=1, max_length=2000)]
    #: ★ 公告链接：必须是合法 http(s) URL
    source_url: Annotated[str, Field(min_length=1, max_length=1024)]
    #: 风险维度（前端可直接画维度图；可选，不参与前端校验）
    risk_dimension: RiskDimension | None = None
    #: 核验状态（**可选**，见 VerificationStatus 的说明）。
    #: 默认 None 而不是 "auto"：老样例与三条稳定 Demo 的语义是"未标注"，
    #: 硬塞一个默认值会让「人工核验」与「自动核验」在数据上再也分不开。
    verification_status: VerificationStatus | None = None

    @field_validator("source_url")
    @classmethod
    def _url_must_be_http(cls, value: str) -> str:
        """前端 isHttpUrl() 要求能被 new URL() 解析且协议是 http/https。"""
        candidate = (value or "").strip()
        if not candidate.startswith(("http://", "https://")):
            raise ValueError(
                f"evidence.source_url 必须是 http(s) URL（前端契约），收到 {value!r}"
            )
        return candidate


class ChartSeries(_StrictModel):
    """一条数据序列 —— 前端要求 values 长度与 periods 一致。"""

    name: Annotated[str, Field(min_length=1, max_length=64)]
    values: Annotated[list[float], Field(min_length=1)]


class ChartSpec(_StrictModel):
    """响应体 charts[] 的元素 —— 严格对齐前端 isChart()。

    前端要求：
      id / title 非空，type 必须是 "line"，
      unit 是字符串（可以是空串，但不能缺），
      periods 非空字符串数组，
      series 非空且每条的 values 长度 === periods 长度、元素为有限数值，
      evidence_ids 非空且可解析。
    """

    id: Annotated[str, Field(min_length=1, max_length=64)]
    type: ChartKind
    title: Annotated[str, Field(min_length=1, max_length=64)]
    #: 前端会读 subtitle（类型里是可选，但 mock 数据都给了）
    subtitle: str = ""
    unit: str = ""
    periods: Annotated[list[str], Field(min_length=1)]
    series: Annotated[list[ChartSeries], Field(min_length=1)]
    evidence_ids: Annotated[list[str], Field(min_length=1)]

    @field_validator("evidence_ids")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("chart.evidence_ids 不能为空：图表数据必须有出处")
        return cleaned

    @model_validator(mode="after")
    def _series_align(self) -> "ChartSpec":
        """每条序列的 values 必须与 periods 等长（前端逐项校验，不等长即判非法）。"""
        expected = len(self.periods)
        bad = [s.name for s in self.series if len(s.values) != expected]
        if bad:
            raise ValueError(
                f"chart {self.id} 的 series {bad} 长度与 periods({expected}) 不一致"
            )
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
    "ALLOWED_SIGNAL_SEVERITIES",
    "ALLOWED_SIGNAL_TYPES",
    "SEVERITY_MAP",
    "SIGNAL_TYPE_MAP",
    "AdminRefreshResponse",
    "AskRequest",
    "AskResponse",
    "ChartKind",
    "ChartSeries",
    "ChartSpec",
    "Claim",
    "CompanyProfile",
    "DataSource",
    "ErrorResponse",
    "Evidence",
    "EvidenceCategory",
    "HealthResponse",
    "RiskDimension",
    "Signal",
    "SignalListResponse",
    "SignalSeverity",
    "SignalType",
    "VerificationStatus",
    "to_evidence_category",
    "to_severity",
    "to_signal_type",
]
