"""X-Ray 企业穿透分析 · 路由与回答合成。

接口契约（**保持不变**，未因本轮改造而改动）：
  1) POST /companies/{stock_code}/ask      —— 主接口
  2) GET  /companies/{stock_code}/profile  —— 公司画像
  3) GET  /companies/{stock_code}/signals  —— 风险信号（读缓存结论）
  4) POST /admin/refresh?stock_code=xxx    —— 手动触发分析（简单 token 校验）
  5) GET  /health                          —— 健康检查 + 最近一次同步时间

⚠️ 路径**绝不能**带 /api/v1 前缀（沿用既有约定）。

响应体顶层恰好 6 个字段，顺序固定：
    answer / claims / signals / charts / evidence / suggested_questions

回答合成顺序（需求 E）：
    1. 优先读**该公司已缓存的风险摘要**（analyzer 生成的 cache/company_risk_{code}.json）；
    2. 再实时查该公司**最新公告**，补一句「最新动态」；
    3. 合成最终 answer。
    * 无缓存时不现场调用 LLM（保持接口快速），而是提示先跑批处理；
    * LLM 说「无足够信息」时，claims/evidence 允许为空数组（契约已放开）。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from analyzer import (
    INSUFFICIENT,
    RISK_HIGH,
    RISK_MEDIUM,
    load_cache,
)
from config import settings
from db import (
    DatabaseNotReadyError,
    cninfo_list_url,
    count_announcements,
    db_status,
    get_announcements,
    get_company_summary,
    search_announcements,
)
from schemas import (
    AdminRefreshResponse,
    AskRequest,
    AskResponse,
    ChartSeries,
    ChartSpec,
    Claim,
    CompanyProfile,
    ErrorResponse,
    Evidence,
    HealthResponse,
    Signal,
    SignalListResponse,
    to_evidence_category,
    to_severity,
    to_signal_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["xray"])

HEADER_SOURCE = "X-XRay-Cache"
HEADER_ELAPSED = "X-XRay-Elapsed-Ms"

#: 代码归一：去掉 .SH/.SZ/.BJ 等后缀（库里的 company_code 是纯数字）
_SUFFIX = re.compile(r"\.(SH|SZ|BJ|SS|HK)$", re.I)


def error_response(status_code: int, error: str, detail: str = "") -> JSONResponse:
    """统一 {"error", "detail"}，不让前端白屏。"""
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


def normalize_code(raw: str) -> str:
    """把用户输入的代码归一成库里 company_code 的形式。"""
    code = (raw or "").strip().upper()
    code = _SUFFIX.sub("", code)
    return code


def _db_unavailable(exc: DatabaseNotReadyError) -> JSONResponse:
    return error_response(503, "数据库不可用", str(exc).split("\n")[0])


# ---------------------------------------------------------------------------
# 回答合成
# ---------------------------------------------------------------------------

#: 无缓存时的提示（不现场调 LLM，避免接口变慢）
NO_ANALYSIS_HINT = (
    "该公司暂无预生成的风险分析。请先执行 python scripts/run_night_batch.py --demo（或全量）"
    "生成分析结论后再提问。"
)


def _signal_type_of(finding: dict[str, Any]) -> str:
    """内部规则编号（S1-S4），用于分组统计。"""
    value = str(finding.get("type") or "").strip().upper()
    return value if value in ("S1", "S2", "S3", "S4") else "S4"


def _announcement_index(announcements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """建索引：docs.id（字符串）与文件名 → 公告行，用于补 source_page / source_url。"""
    index: dict[str, dict[str, Any]] = {}
    for item in announcements:
        index[str(item.get("id"))] = item
        name = item.get("file_name")
        if name:
            index[str(name)] = item
    return index


def _lookup_announcement(
    quote: dict[str, Any], index: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """按 source_id 找公告；找不到再用 source_file 兜一次。"""
    source_id = quote.get("source_id")
    if source_id is not None:
        found = index.get(str(source_id))
        if found:
            return found
    source_file = quote.get("source_file")
    if source_file:
        return index.get(str(source_file))
    return None


def _build_evidence(
    quotes: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    stock_code: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """把 evidence_quotes 转成响应体 evidence[]，并返回 quote_id → evidence_id 映射。

    ⚠️ 严格对齐前端 api.ts 的 isEvidence()：category 只能是
    financial/business/company，document_id 非空，source_page 为正整数，
    source_url 为合法 http(s) URL，source_quote 非空。
    任一项不满足，**前端会把整包响应判为非法并静默降级为 mock**，
    所以这里逐项兜底，而不是把空值透传出去。
    """
    index = _announcement_index(announcements)
    fallback_url = cninfo_list_url(stock_code)

    evidence: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}

    for quote in quotes:
        raw_quote = str(quote.get("source_quote") or "").strip()
        if not raw_quote:
            logger.warning("跳过缺少 source_quote 的证据：%r", quote.get("id"))
            continue

        hit = _lookup_announcement(quote, index)
        ev_id = f"EV-{len(evidence) + 1:03d}"
        mapping[str(quote.get("id"))] = ev_id

        # document_id：优先公告真实 id；退而用 source_id；再不行用文件名
        if hit is not None:
            document_id = str(hit.get("id"))
        elif quote.get("source_id") is not None:
            document_id = str(quote["source_id"])
        else:
            document_id = str(quote.get("source_file") or "unknown")

        # source_url：库里有直链就用，否则用巨潮公告列表页（真实可达）
        source_url = ""
        if hit is not None:
            source_url = str(hit.get("source_url") or "")
        if not source_url.startswith(("http://", "https://")):
            source_url = fallback_url

        # source_page：前端要求 >0 的整数。库里没有"引用所在页"这一信息，
        # 用公告页数夹取，默认 1（公告起始页），绝不传 null。
        page = 1
        if hit is not None:
            raw_pages = hit.get("page_count")
            if isinstance(raw_pages, int) and raw_pages > 0:
                page = raw_pages if raw_pages > 0 else 1
        page = max(1, page)

        period = (
            str(quote.get("source_date") or "").strip()
            or (str(hit.get("announce_date")) if hit and hit.get("announce_date") else "")
            or (str(hit.get("created_date")) if hit and hit.get("created_date") else "")
            or "公告披露期间"
        )

        evidence.append(
            {
                "id": ev_id,
                "category": to_evidence_category(str(quote.get("risk_dimension") or "")),
                "period": period[:64],
                "content": str(quote.get("content") or raw_quote[:200]),
                "document_id": document_id,
                "document_title": str(
                    (hit.get("file_name") if hit else None)
                    or quote.get("source_file")
                    or document_id
                ),
                "source_page": page,
                "source_quote": raw_quote,
                "source_url": source_url,
                "risk_dimension": quote.get("risk_dimension") or "其他",
            }
        )
    return evidence, mapping


def _build_claims(
    findings: list[dict[str, Any]], mapping: dict[str, str]
) -> list[dict[str, Any]]:
    """claims —— 前端只校验 id/text 非空、evidence_ids 非空且可解析。"""
    claims: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        ids = [mapping[q] for q in (finding.get("evidence_ids") or []) if q in mapping]
        if not ids:
            continue  # 无引用的结论不输出（No Evidence, No Claim）
        title = str(finding.get("title") or "").strip()
        description = str(finding.get("description") or "").strip()
        text = f"{title}：{description}".strip("：") or title or description
        claims.append(
            {
                "id": f"CL-{index:03d}",
                "text": text[:1000],
                "evidence_ids": ids,
            }
        )
    return claims


def _build_signals(
    findings: list[dict[str, Any]], mapping: dict[str, str], summary: str
) -> list[dict[str, Any]]:
    """signals —— 前端只认 type ∈ {divergence,trend,attention}、
    severity ∈ {attention,positive}，且不接受 side 字段（多余的字段前端不校验，
    但保持精简更安全）。
    """
    signals: list[dict[str, Any]] = []
    for finding in findings:
        ids = [mapping[q] for q in (finding.get("evidence_ids") or []) if q in mapping]
        if not ids:
            continue
        rule_id = _signal_type_of(finding)
        title = str(finding.get("title") or rule_id).strip()[:64] or rule_id
        description = str(finding.get("description") or summary).strip() or summary
        signals.append(
            {
                # 形如 SIG-S1；前端只要求非空字符串
                "id": f"SIG-{rule_id}",
                "type": to_signal_type(rule_id),
                "title": title,
                "severity": to_severity(str(finding.get("severity") or "medium")),
                "description": description[:1000],
                "evidence_ids": ids,
            }
        )
    return signals


#: 图表里各风险主题的中文短标签
_RULE_LABELS = {"S1": "利润/现金流", "S2": "营收/应收", "S3": "人员", "S4": "司法合规"}


def _build_charts(
    analysis: dict[str, Any], mapping: dict[str, str]
) -> list[dict[str, Any]]:
    """由 findings 派生图表：按风险主题统计条数。

    前端只支持折线图（api.ts 里写死 `value.type !== "line"` 即判非法），
    且要求 `series[].values` 长度与 `periods` 一致、元素为有限数值。
    这里用「每类主题累计发现条数」构造折线，periods 即四类主题。

    :param mapping: quote_id → evidence_id（由 _build_evidence 产出）
    没有任何有效引用时返回空数组（契约允许 charts 为空）。
    """
    if not settings.CHARTS_ENABLED:
        return []

    findings = analysis.get("findings") or []
    if not findings:
        return []

    counts: dict[str, int] = {}
    for finding in findings:
        key = _signal_type_of(finding)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return []

    periods = [f"{k} {_RULE_LABELS[k]}" for k in ("S1", "S2", "S3", "S4") if k in counts]
    values = [float(counts[k]) for k in ("S1", "S2", "S3", "S4") if k in counts]
    if not periods or len(periods) != len(values):
        return []

    # 借用本次 finding 引用到、且确实生成了 evidence 的 id（图表也要有出处）
    evidence_ids: list[str] = []
    for finding in findings:
        for quote_id in finding.get("evidence_ids") or []:
            ev_id = mapping.get(str(quote_id))
            if ev_id and ev_id not in evidence_ids:
                evidence_ids.append(ev_id)
    if not evidence_ids:
        return []

    return [
        {
            "id": "CHART-RISK-BY-TYPE",
            "type": "line",
            "title": "风险发现分布",
            "subtitle": "按四类风险主题统计本次分析发现的条数",
            "unit": "条",
            "periods": periods,
            "series": [{"name": "发现条数", "values": values}],
            "evidence_ids": evidence_ids,
        }
    ]


def _latest_announcement_line(stock_code: str) -> str:
    """实时查最新公告，补一句「最新动态」。查询失败不影响主回答。"""
    try:
        latest = get_announcements(stock_code, days=None, limit=1, body_chars=120)
    except (DatabaseNotReadyError, ValueError):
        return ""
    if not latest:
        return ""
    item = latest[0]
    date_text = item.get("announce_date") or "日期未知"
    title = item.get("file_name") or "未命名公告"
    return f"最新公告（{date_text}）：{title}。"


def compose_answer(
    analysis: dict[str, Any] | None,
    *,
    stock_code: str,
    latest_line: str,
    from_cache: bool,
) -> str:
    """合成 answer：缓存摘要 + 最新动态。"""
    if analysis is None:
        parts = [NO_ANALYSIS_HINT]
        if latest_line:
            parts.append(latest_line)
        return "".join(parts)

    summary = str(analysis.get("summary") or INSUFFICIENT)
    level = str(analysis.get("risk_level") or "unknown")
    findings = analysis.get("findings") or []

    parts = [f"{stock_code}：{summary}"]
    if findings:
        parts.append(f"（风险等级：{level}，共 {len(findings)} 条发现）")
    elif level == "unknown":
        parts.append("（未在公告中找到足够依据）")
    if latest_line:
        parts.append(latest_line)
    if not from_cache:
        parts.append("（以下为已有分析结论，非本次实时生成。）")
    return "".join(parts)


def suggest_questions(analysis: dict[str, Any] | None) -> list[str]:
    """建议问题：优先与命中的风险主题相关，再补足到 5 条。"""
    found = {
        _signal_type_of(f) for f in ((analysis or {}).get("findings") or [])
    }
    mapping = {
        "S1": "该公司的利润与现金流是否匹配？",
        "S2": "该公司的应收账款增长是否异常？",
        "S3": "该公司的社保参保人数变化如何？",
        "S4": "该公司有哪些司法与合规风险？",
    }
    ordered = [mapping[t] for t in ("S1", "S2", "S3", "S4") if t in found]
    ordered += [q for q in settings.DEFAULT_QUESTIONS if q not in ordered]

    seen: list[str] = []
    for question in ordered:
        if question not in seen:
            seen.append(question)
    return seen[:5] or list(settings.DEFAULT_QUESTIONS)


def build_answer_payload(
    stock_code: str, *, question: str = "", allow_stale_cache: bool = True
) -> tuple[dict[str, Any], str]:
    """组装响应体；返回 (payload, 来源标记)。"""
    analysis = load_cache(stock_code, allow_stale=allow_stale_cache)
    source = "hit-cache" if analysis is not None else "miss"

    latest_line = _latest_announcement_line(stock_code)
    answer = compose_answer(
        analysis, stock_code=stock_code, latest_line=latest_line, from_cache=analysis is not None
    )

    quotes = (analysis or {}).get("evidence_quotes") or []
    findings = (analysis or {}).get("findings") or []

    # 取公告行本身，用于补前端硬要求字段（document_id / source_page / source_url）
    announcements: list[dict[str, Any]] = []
    if quotes:
        try:
            announcements = get_announcements(
                stock_code, days=None, limit=None, body_chars=0
            )
        except (DatabaseNotReadyError, ValueError) as exc:
            logger.warning("读取公告行失败（source_url/source_page 将走兜底）：%s", exc)

    evidence, mapping = _build_evidence(quotes, announcements, stock_code)
    claims = _build_claims(findings, mapping)
    signals = _build_signals(findings, mapping, answer)
    charts = _build_charts(analysis or {}, mapping)

    payload = AskResponse(
        answer=answer,
        claims=[Claim(**c) for c in claims],
        signals=[Signal(**s) for s in signals],
        charts=[ChartSpec(**c) for c in charts],
        evidence=[Evidence(**e) for e in evidence],
        suggested_questions=suggest_questions(analysis),
    ).to_dict()
    return payload, source


# ---------------------------------------------------------------------------
# 1) POST /companies/{stock_code}/ask
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{stock_code}/ask",
    response_model=AskResponse,
    responses={404: {"description": "公司不存在"}, 503: {"description": "数据库不可用"}},
    summary="提问：读缓存风险摘要 + 实时补最新公告",
)
def ask_company(stock_code: str, payload: AskRequest, request: Request) -> Any:
    started = time.perf_counter()
    code = normalize_code(stock_code)
    if not code:
        return error_response(422, "股票代码不能为空", "路径参数 stock_code 缺失")

    # 公司是否存在（以库里是否有公告为准）
    try:
        summary = get_company_summary(code, recent=1)
    except DatabaseNotReadyError as exc:
        return _db_unavailable(exc)

    if summary is None:
        return error_response(
            404,
            "公司不存在",
            f"data/cninfo.db 的 docs 表里没有 company_code={code} 的公告；"
            f"请确认代码是否正确，或先执行 python scripts/run_night_batch.py",
        )

    try:
        body, source = build_answer_payload(code, question=payload.question)
    except Exception as exc:  # noqa: BLE001
        logger.exception("组装回答失败 %s", code)
        return error_response(500, "答案组装失败", f"{type(exc).__name__}: {exc}")

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info("ask %s [%s] %.1fms q=%r", code, source, elapsed_ms, payload.question[:40])
    return JSONResponse(
        status_code=200,
        content=body,
        headers={HEADER_SOURCE: source, HEADER_ELAPSED: f"{elapsed_ms:.1f}"},
    )


# ---------------------------------------------------------------------------
# 2) GET /companies/{stock_code}/profile
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{stock_code}/profile",
    response_model=CompanyProfile,
    responses={404: {"description": "公司不存在"}, 503: {"description": "数据库不可用"}},
    summary="公司画像（公告覆盖情况 + 风险摘要）",
)
def company_profile(stock_code: str) -> Any:
    code = normalize_code(stock_code)
    try:
        summary = get_company_summary(code)
    except DatabaseNotReadyError as exc:
        return _db_unavailable(exc)

    if summary is None:
        return error_response(404, "公司不存在", f"docs 表里没有 company_code={code} 的公告")

    analysis = load_cache(code, allow_stale=True)
    return CompanyProfile(
        stock_code=code,
        name=code,  # docs 表没有公司名称字段，暂以代码代替
        announcement_count=summary["announcement_count"],
        first_announcement_date=summary["first_announcement_date"],
        last_announcement_date=summary["last_announcement_date"],
        recent_titles=summary["recent_titles"],
        risk_level=(analysis or {}).get("risk_level"),
        summary=(analysis or {}).get("summary"),
        analyzed_at=(analysis or {}).get("analyzed_at"),
    )


# ---------------------------------------------------------------------------
# 3) GET /companies/{stock_code}/signals
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{stock_code}/signals",
    response_model=SignalListResponse,
    responses={404: {"description": "公司不存在"}, 503: {"description": "数据库不可用"}},
    summary="风险信号（来自缓存的分析结论）",
)
def company_signals(stock_code: str) -> Any:
    code = normalize_code(stock_code)
    try:
        summary = get_company_summary(code, recent=1)
    except DatabaseNotReadyError as exc:
        return _db_unavailable(exc)
    if summary is None:
        return error_response(404, "公司不存在", f"docs 表里没有 company_code={code} 的公告")

    analysis = load_cache(code, allow_stale=True)
    if analysis is None:
        return SignalListResponse(stock_code=code, signals=[], computed_at=None, source="no_cache")

    quotes = analysis.get("evidence_quotes") or []
    findings = analysis.get("findings") or []
    try:
        announcements = get_announcements(code, days=None, limit=None, body_chars=0)
    except (DatabaseNotReadyError, ValueError):
        announcements = []
    _evidence, mapping = _build_evidence(quotes, announcements, code)
    raw = _build_signals(findings, mapping, str(analysis.get("summary") or ""))

    signals: list[Signal] = []
    for item in raw:
        try:
            signals.append(Signal(**item))
        except Exception:  # noqa: BLE001 —— 单条不合法就跳过，不让整个接口失败
            logger.warning("signal 不合法，跳过：%r", item.get("type"))
    return SignalListResponse(
        stock_code=code,
        signals=signals,
        computed_at=analysis.get("analyzed_at"),
        source=analysis.get("source"),
    )


# ---------------------------------------------------------------------------
# 4) POST /admin/refresh?stock_code=xxx
# ---------------------------------------------------------------------------


@router.post(
    "/admin/refresh",
    response_model=AdminRefreshResponse,
    responses={401: {"description": "token 校验失败"}},
    summary="手动触发分析（简单 token 校验）",
)
def admin_refresh(
    request: Request,
    stock_code: str | None = Query(default=None, description="留空 = 遍历全部公司"),
    force: bool = Query(default=True, description="忽略当日缓存，强制重新分析"),
    token: str | None = Query(default=None, description="简单 token 校验"),
) -> Any:
    provided = request.headers.get("X-Admin-Token") or token or ""
    if provided != settings.ADMIN_TOKEN:
        logger.warning("admin/refresh 鉴权失败（stock_code=%s）", stock_code)
        return error_response(401, "鉴权失败", "token 不正确；请通过 X-Admin-Token 头或 ?token= 传入")

    from analyzer import analyze_company, analyze_many, demo_stocks

    try:
        if stock_code:
            code = normalize_code(stock_code)
            if get_company_summary(code, recent=1) is None:
                return error_response(404, "公司不存在", f"docs 表里没有 company_code={code} 的公告")
            result = analyze_company(code, force=force)
            detail = f"{code}: risk_level={result.get('risk_level')} source={result.get('source')}"
        else:
            codes = demo_stocks(limit=5)
            if not codes:
                return error_response(503, "数据库不可用", "读不到任何公司代码")
            results = analyze_many(codes, force=force)
            detail = f"已分析 {len(results)} 家：{', '.join(sorted(results))}"
    except DatabaseNotReadyError as exc:
        return _db_unavailable(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("手动分析失败")
        return error_response(500, "分析失败", f"{type(exc).__name__}: {exc}")

    return AdminRefreshResponse(status="completed", stock_code=stock_code, detail=detail)


# ---------------------------------------------------------------------------
# 5) GET /health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, summary="健康检查 + 最近一次同步时间")
def health() -> Any:
    status = db_status()
    last_at = None
    level_counts: dict[str, int] = {}
    if status.get("ready"):
        try:
            from analyzer import cache_path  # noqa: F401
            import json as _json

            cache_dir = settings.cache_dir
            if cache_dir.is_dir():
                stamps: list[str] = []
                for path in cache_dir.glob("company_risk_*.json"):
                    try:
                        data = _json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if data.get("analyzed_at"):
                        stamps.append(str(data["analyzed_at"]))
                    level = str(data.get("risk_level") or "unknown")
                    level_counts[level] = level_counts.get(level, 0) + 1
                last_at = max(stamps) if stamps else None
        except Exception:  # noqa: BLE001
            logger.exception("读取缓存统计失败")

    capabilities = {
        "data_source": "data/cninfo.db（docs 表公告原文）",
        "answer_contract": [
            "answer",
            "claims",
            "signals",
            "charts",
            "evidence",
            "suggested_questions",
        ],
        "analysis": {
            "cached_companies": sum(level_counts.values()),
            "risk_level_counts": level_counts,
            "cache_dir": str(settings.cache_dir),
        },
        "llm": {
            "enabled": settings.LLM_ENABLED,
            "ready": settings.llm_ready,
            "model": settings.DEEPSEEK_MODEL,
            "fake": settings.LLM_FAKE,
            "note": "风险判断在批处理时调用；ask 接口只读缓存",
        },
        "scheduling": "应用内无调度器；生产由系统 cron 调 scripts/run_night_batch.py",
        "settings": settings.describe(),
    }

    return HealthResponse(
        status="ok" if status.get("ready") else "degraded",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        database=bool(status.get("ready")),
        mock_mode=False,
        scheduler_enabled=False,
        last_sync_at=last_at,
        last_sync_status=str(status.get("error"))[:200] if status.get("error") else None,
        companies=int(status.get("stocks") or 0),
        llm_ready=settings.llm_ready,
        llm_model=settings.DEEPSEEK_MODEL,
        capabilities=capabilities,
    )


# ---------------------------------------------------------------------------
# 便捷检索（演示时找证据用；不属于原 5 条契约，但显式暴露）
# ---------------------------------------------------------------------------


@router.get("/search", summary="全文检索公告（演示时找证据）")
def search(keyword: str = Query(min_length=2), limit: int = Query(default=50, ge=1, le=200)) -> Any:
    try:
        hits = search_announcements(keyword, limit=limit)
    except DatabaseNotReadyError as exc:
        return _db_unavailable(exc)
    except ValueError as exc:
        return error_response(422, "参数不合法", str(exc))
    return {
        "keyword": keyword,
        "count": len(hits),
        "total": count_announcements(),
        "hits": [
            {
                "company_code": h["company_code"],
                "file_name": h["file_name"],
                "announce_date": h["announce_date"],
                "text_length": h["text_length"],
            }
            for h in hits
        ],
    }


__all__ = [
    "HEADER_ELAPSED",
    "HEADER_SOURCE",
    "ErrorResponse",
    "build_answer_payload",
    "compose_answer",
    "error_response",
    "normalize_code",
    "router",
    "suggest_questions",
]
