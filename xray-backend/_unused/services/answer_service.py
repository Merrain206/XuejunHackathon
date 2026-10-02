"""X-Ray 企业穿透分析 · 答案服务。

职责（最新需求）：
  * **所有请求都在这里实时组装**：signals 实时计算、charts 确定性生成、
    answer 由 DeepSeek 生成（失败则回退确定性模板）；
  * 夜间批处理**不再参与**任何规则预计算或信号判定，它只负责采集数据入库
    （见 tasks/night_batch.py）。

不变的硬性约束：
  * 组装结果必须通过 schemas.AskResponse 校验 —— 顶层恰好 6 键、
    claims/evidence 非空、所有 evidence_id 可解析、无孤立证据；
  * 大模型失败**绝不抛给前端**：answer 里附带友好说明 + 确定性模板兜底。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from charts import build_charts
from config import settings
from llm_client import LLMResult, generate_answer as llm_generate_answer
from matcher import question_hash
from mock_data import DEFAULT_QUESTIONS, mock_evidence_rows
from models import AnswerCache, ClaimRecord, Company, Evidence, utcnow
from schemas import AskResponse, ChartSpec, Claim, Evidence as EvidenceSchema, Signal
from signal_engine import SIGNAL_RULES, SignalOutcome, compute_signals, summarize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 来源标记
# ---------------------------------------------------------------------------


class AnswerSource(str, Enum):
    """answer 文字的来源（写进 answer_cache.generator，便于排查）。"""

    DEEPSEEK = "deepseek"
    FAKE = "fake"            # LLM_FAKE=true（离线/测试）
    TEMPLATE = "template"    # 大模型不可用时的确定性兜底


#: 未命中缓存时，answer 里标注的数据时效说明
STALE_NOTE = "（本回答由本地库实时组装，数据截至最近一次同步。）"


@dataclass
class AnswerContext:
    """组装一次回答所需的全部材料。"""

    stock_code: str
    company_name: str
    fields: dict[str, Any]
    evidence_catalog: list[dict[str, Any]]
    stored_claims: list[dict[str, Any]]
    signals: list[SignalOutcome]
    question: str = ""
    #: 附加在 answer 末尾的说明（如匹配度提示或数据时效）
    extra_note: str = ""
    #: 本次是否来自缓存
    from_cache: bool = False


# ---------------------------------------------------------------------------
# 取上下文
# ---------------------------------------------------------------------------


def _decode_field_value(raw: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _load_fields(db: Session, stock_code: str) -> dict[str, Any]:
    """从 finance_fields 读取该公司 15 个字段。"""
    from models import FinanceField

    rows = db.scalars(select(FinanceField).where(FinanceField.stock_code == stock_code)).all()
    return {row.field_name: _decode_field_value(row.field_value) for row in rows}


def _load_evidence(db: Session, stock_code: str) -> list[dict[str, Any]]:
    """从 evidence 表读证据目录；库里没有则回退种子数据。"""
    rows = db.scalars(
        select(Evidence).where(Evidence.stock_code == stock_code).order_by(Evidence.evidence_id)
    ).all()
    if rows:
        return [
            {
                "evidence_id": r.evidence_id,
                "category": r.category,
                "period": r.period,
                "content": r.content,
                "document_id": r.document_id,
                "document_title": r.document_title,
                "source_page": r.source_page,
                "source_quote": r.source_quote,
                "source_url": r.source_url,
                "field_names": list(r.field_names or []),
                "risk_dimension": r.risk_dimension,
            }
            for r in rows
        ]
    return mock_evidence_rows(stock_code)


def _load_stored_claims(db: Session, stock_code: str, qhash: str) -> list[dict[str, Any]]:
    """读取 claim；优先该问题专属，其次通用。"""
    rows = db.scalars(
        select(ClaimRecord).where(ClaimRecord.stock_code == stock_code).order_by(ClaimRecord.claim_id)
    ).all()
    general: list[dict[str, Any]] = []
    specific: list[dict[str, Any]] = []
    for r in rows:
        item = {
            "claim_id": r.claim_id,
            "text": r.text,
            "evidence_ids": list(r.evidence_ids or []),
            "verified": r.verified,
            "verification_note": r.verification_note,
        }
        if r.question_hash and r.question_hash == qhash:
            specific.append(item)
        elif not r.question_hash:
            general.append(item)
    return specific or general


def build_context(
    db: Session,
    stock_code: str,
    question: str,
    *,
    from_cache: bool = False,
    extra_note: str = "",
) -> AnswerContext | None:
    """收集上下文；公司不存在或没有字段时返回 None。

    ⚠️ signals 在此**实时计算**（不再是夜间预计算结果的读取）。
    """
    company = db.scalar(select(Company).where(Company.stock_code == stock_code))
    if company is None:
        return None

    fields = _load_fields(db, stock_code)
    if not fields:
        from mock_data import mock_fields

        fields = mock_fields(stock_code) or {}
    if not fields:
        return None

    return AnswerContext(
        stock_code=stock_code,
        company_name=company.name,
        fields=fields,
        evidence_catalog=_load_evidence(db, stock_code),
        stored_claims=_load_stored_claims(db, stock_code, question_hash(question)),
        signals=compute_signals(fields),
        question=question,
        extra_note=extra_note,
        from_cache=from_cache,
    )


# ---------------------------------------------------------------------------
# 证据绑定
# ---------------------------------------------------------------------------


def evidence_for_signal(signal: SignalOutcome, catalog: Sequence[dict[str, Any]]) -> list[str]:
    """为一条信号挑出**字段/维度层面相关**的证据；挑不到返回空（调用方须丢弃该信号）。"""
    rule_dimension = next((r.dimension for r in SIGNAL_RULES if r.rule_id == signal.type), None)
    signal_fields = set(signal.field_names)
    picked: list[str] = []
    for row in catalog:
        same_dimension = rule_dimension is not None and row.get("risk_dimension") == rule_dimension
        overlap = signal_fields & set(row.get("field_names") or [])
        if same_dimension or overlap:
            picked.append(str(row["evidence_id"]))
    return picked


def build_signals(ctx: AnswerContext) -> list[dict[str, Any]]:
    """把**实时**信号判定结果转成响应结构；拿不到证据的信号一律不输出。"""
    payload: list[dict[str, Any]] = []
    for outcome in ctx.signals:
        if not outcome.triggered:
            continue
        ids = evidence_for_signal(outcome, ctx.evidence_catalog)
        if not ids:
            logger.warning(
                "%s 信号 %s 命中但找不到可引证据，按不输出处理（拒绝编造）",
                ctx.stock_code,
                outcome.type,
            )
            continue
        payload.append(outcome.to_signal_dict(ids))
    return payload


def build_charts_for(ctx: AnswerContext, signals: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """确定性生成 charts；命中的信号优先用其证据，保证图与信号同源。"""
    by_rule = {s["type"]: list(s["evidence_ids"]) for s in signals}
    return build_charts(ctx.fields, ctx.evidence_catalog, evidence_ids_by_rule=by_rule)


def build_claims(ctx: AnswerContext, signals: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """优先用库里 claims；缺口用确定性模板从命中信号补（文案引用真实数字）。"""
    claims: list[dict[str, Any]] = []
    used: set[str] = set()

    for row in ctx.stored_claims:
        ids = [i for i in row["evidence_ids"] if i]
        if not ids:
            continue
        claims.append(
            {
                "id": row["claim_id"],
                "text": row["text"],
                "evidence_ids": ids,
                "verified": bool(row.get("verified", False)),
                "verification_note": row.get("verification_note"),
            }
        )
        used.add(row["claim_id"])

    for signal in signals:
        claim_id = f"CL-{signal['type']}"
        if claim_id in used:
            continue
        claims.append(
            {
                "id": claim_id,
                "text": signal["description"],
                "evidence_ids": list(signal["evidence_ids"]),
                "verified": True,
                "verification_note": "由确定性规则引擎依据上述证据计算得出。",
            }
        )
        used.add(claim_id)
    return claims


def build_evidence(
    claim_payload: Sequence[dict[str, Any]],
    signal_payload: Sequence[dict[str, Any]],
    chart_payload: Sequence[dict[str, Any]],
    catalog: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只输出被 claim / signal / chart 引用到的证据（避免孤立证据导致校验失败）。"""
    wanted: set[str] = set()
    for group in (claim_payload, signal_payload, chart_payload):
        for item in group:
            wanted.update(item["evidence_ids"])
    out: list[dict[str, Any]] = []
    for row in catalog:
        if row["evidence_id"] not in wanted:
            continue
        out.append(
            {
                "id": row["evidence_id"],
                "category": row["category"],
                "period": row["period"],
                "content": row["content"],
                "document_id": row["document_id"],
                "document_title": row["document_title"],
                "source_page": row["source_page"],
                "source_quote": row["source_quote"],
                "source_url": row["source_url"],
                "field_names": row.get("field_names"),
                "risk_dimension": row.get("risk_dimension"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# answer 文案
# ---------------------------------------------------------------------------


def template_answer(ctx: AnswerContext, signals: Sequence[dict[str, Any]]) -> str:
    """确定性模板文案（大模型不可用时的兜底，也是 Prompt 的素材）。"""
    stats = summarize(ctx.signals)
    parts: list[str] = []

    if signals:
        titles = "、".join(f"{s['type']} {s['title']}" for s in signals)
        parts.append(
            f"{ctx.company_name}（{ctx.stock_code}）经 4 对矛盾信号交叉核验，"
            f"命中 {len(signals)} 条风险信号：{titles}；最高严重度 {stats.get('top_severity') or 'low'}。"
        )
        for s in signals:
            parts.append(f"· {s['type']} {s['title']}（{s['severity']}）：{s['description']}")
    else:
        parts.append(
            f"{ctx.company_name}（{ctx.stock_code}）经 4 对矛盾信号交叉核验，"
            f"未命中任何风险信号，利润、现金流、回款、人力与司法维度方向一致。"
        )
    return "".join(parts)


def compose_answer(ctx: AnswerContext, signals: Sequence[dict[str, Any]]) -> tuple[str, LLMResult]:
    """生成 answer 文字。

    返回 (最终 answer, LLM 调用结果)。大模型失败时：
      answer = 降级说明 + 确定性模板，**绝不返回空字符串**、绝不抛异常。
    """
    baseline = template_answer(ctx, signals)

    result = llm_generate_answer(
        ctx.question,
        [r for r in ctx.evidence_catalog],
        company_name=ctx.company_name,
        stock_code=ctx.stock_code,
        signals=list(signals),
    )

    if result.ok and result.text:
        text = result.text
    else:
        notice = result.notice
        text = f"{notice} {baseline}"

    if ctx.extra_note:
        text = f"{text}{ctx.extra_note}"
    return text, result


def source_of(result: LLMResult) -> AnswerSource:
    if result.ok and result.source == "fake":
        return AnswerSource.FAKE
    if result.ok:
        return AnswerSource.DEEPSEEK
    return AnswerSource.TEMPLATE


def compose_suggested_questions(ctx: AnswerContext) -> list[str]:
    """建议问题：优先与命中信号相关，再补足到 5 条。"""
    hit = {s.type for s in ctx.signals if s.triggered}
    mapping = {
        "S1": "该公司的利润含金量如何？",
        "S2": "该公司的营收情况如何？",
        "S3": "该公司的社保参保人数变化如何？",
        "S4": "该公司的司法风险如何？",
    }
    ordered = [mapping[r] for r in ("S1", "S2", "S3", "S4") if r in hit]
    ordered += [q for q in DEFAULT_QUESTIONS if q not in ordered]

    seen: list[str] = []
    for q in ordered:
        if q not in seen:
            seen.append(q)
    return seen[:5] or list(DEFAULT_QUESTIONS)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


@dataclass
class GeneratedAnswer:
    """组装结果 + 元信息。"""

    payload: dict[str, Any]
    source: AnswerSource
    llm: LLMResult | None = None
    signals_count: int = 0
    charts_count: int = 0
    notes: list[str] = field(default_factory=list)


def build_response(
    db: Session,
    stock_code: str,
    question: str,
    *,
    from_cache: bool = False,
    extra_note: str = "",
) -> GeneratedAnswer | None:
    """实时组装完整响应（6 键）。公司不存在返回 None。

    流程：实时算 signals → 确定性生成 charts → 取 evidence → 调 DeepSeek 生成 answer
          → 过 AskResponse 契约校验。
    """
    ctx = build_context(db, stock_code, question, from_cache=from_cache, extra_note=extra_note)
    if ctx is None:
        return None

    signal_payload = build_signals(ctx)
    chart_payload = build_charts_for(ctx, signal_payload)
    claim_payload = build_claims(ctx, signal_payload)
    evidence_payload = build_evidence(claim_payload, signal_payload, chart_payload, ctx.evidence_catalog)

    answer, llm_result = compose_answer(ctx, signal_payload)

    # 契约校验：任何字段缺失/多余/证据悬空都会在这里抛错
    response = AskResponse(
        answer=answer,
        claims=[Claim(**c) for c in claim_payload],
        signals=[Signal(**s) for s in signal_payload],
        charts=[ChartSpec(**c) for c in chart_payload],
        evidence=[EvidenceSchema(**e) for e in evidence_payload],
        suggested_questions=compose_suggested_questions(ctx),
    )

    notes: list[str] = []
    if not from_cache:
        notes.append(STALE_NOTE)
    if not llm_result.ok:
        notes.append(f"降级原因：{llm_result.fallback_reason or 'error'}")

    return GeneratedAnswer(
        payload=response.to_dict(),
        source=source_of(llm_result),
        llm=llm_result,
        signals_count=len(signal_payload),
        charts_count=len(chart_payload),
        notes=notes,
    )


def build_response_or_raise(
    db: Session, stock_code: str, question: str, *, from_cache: bool = False
) -> dict[str, Any]:
    """组装失败时抛 ValueError（供调用方记录错误）。"""
    generated = build_response(db, stock_code, question, from_cache=from_cache)
    if generated is None:
        raise ValueError(f"公司不存在或缺少字段数据: {stock_code}")
    return generated.payload


# ---------------------------------------------------------------------------
# answer_cache 读写
# ---------------------------------------------------------------------------

#: 缓存里 generator 字段的取值
GENERATOR_DEEPSEEK = "deepseek"
GENERATOR_TEMPLATE = "template"
GENERATOR_FAKE = "fake"


def load_cached(db: Session, stock_code: str, qhash: str) -> AnswerCache | None:
    """按 stock_code + question_hash 读一条缓存记录（是否命中由调用方判定）。"""
    return db.scalar(
        select(AnswerCache).where(
            AnswerCache.stock_code == stock_code,
            AnswerCache.question_hash == qhash,
        )
    )


def list_cached(db: Session, stock_code: str) -> list[AnswerCache]:
    """读取某公司全部缓存（供 matcher 做相似度匹配）。"""
    return list(
        db.scalars(
            select(AnswerCache)
            .where(AnswerCache.stock_code == stock_code)
            .order_by(AnswerCache.question_hash)
        ).all()
    )


def store_response(
    db: Session,
    stock_code: str,
    question: str,
    payload: dict[str, Any],
    *,
    source: AnswerSource = AnswerSource.TEMPLATE,
) -> AnswerCache:
    """把一次实时组装结果写回缓存。

    - 因为 signals 现在实时计算，缓存同时记录 computed_at，便于前端显示数据时效；
    - 按 (stock_code, question_hash) 唯一，重复提问直接覆盖。
    """
    qhash = question_hash(question)
    row = load_cached(db, stock_code, qhash)
    if row is None:
        row = AnswerCache(
            stock_code=stock_code,
            question_hash=qhash,
            question_text=question,
            normalized_question=None,
            answer_json=json.dumps(payload, ensure_ascii=False),
            version=1,
            is_active=True,
            generator=source.value,
            generated_at=utcnow(),
        )
        db.add(row)
    else:
        row.answer_json = json.dumps(payload, ensure_ascii=False)
        row.question_text = question
        row.generator = source.value
        row.is_active = True
        row.generated_at = utcnow()
        db.add(row)
    db.flush()
    return row


def decode_cached_answer(row: AnswerCache) -> dict[str, Any] | None:
    """解码缓存的 answer_json 并做契约校验；损坏则返回 None（按未命中处理）。"""
    try:
        data = json.loads(row.answer_json)
        AskResponse(**data)
        return data
    except Exception:  # noqa: BLE001
        logger.error("缓存答案不符合最新契约，按未命中处理", exc_info=True)
        return None


__all__ = [
    "GENERATOR_DEEPSEEK",
    "GENERATOR_FAKE",
    "GENERATOR_TEMPLATE",
    "STALE_NOTE",
    "AnswerContext",
    "AnswerSource",
    "GeneratedAnswer",
    "build_charts_for",
    "build_claims",
    "build_context",
    "build_evidence",
    "build_response",
    "build_response_or_raise",
    "build_signals",
    "compose_answer",
    "compose_suggested_questions",
    "decode_cached_answer",
    "evidence_for_signal",
    "list_cached",
    "load_cached",
    "source_of",
    "store_response",
    "template_answer",
]
