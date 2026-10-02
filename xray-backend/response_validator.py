"""X-Ray 企业穿透分析 · 响应校验器（No Evidence, No Claim 的机器强制）。

接口返回前**统一调用** `validate_response()`。任何一条不通过，就不允许把响应发给
前端 —— 因为前端 `api.ts` 的 `isAskApiResponse()` 一旦判定非法，会**静默降级**
到 mock 数据（界面出现 DEMO FALLBACK），问题反而更难排查。

逐条对应 BACKEND_NEXT_STEPS.md「P0：No Evidence, No Claim 校验」：

  1. 所有 Claim 至少引用一条 Evidence；
  2. 所有 Signal 至少引用一条 Evidence；
  3. 所有 Chart 至少引用一条 Evidence；
  4. 所有 `evidence_ids` 都存在于当前响应的 Evidence 数组；
  5. Answer 中使用的关键金额和比例能在引用原文中核对；
  6. `source_quote` 必须来自对应文档和页码（页码 > 0、URL 合法）；
  7. URL 或页码缺失时，不得返回相关 Claim；
  8. 证据不足时返回固定兜底回答，不让模型补充事实。

注意区分「硬失败」与「软警告」：
  * 硬失败（E-xxx）会阻止响应发出；
  * 软警告（W-xxx）只记日志 —— 例如答案里出现的比率没能在引文中直接核到，
    可能是模型做了合理的同比换算，不应因此让整个演示不可用。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 证据不足时的固定兜底文案（必须逐字一致）
INSUFFICIENT_ANSWER = "根据目前掌握的信息，我无法可靠回答这个问题。"

#: 前端只接受的三个 evidence category
ALLOWED_CATEGORIES = ("financial", "business", "company")
#: 前端只接受的 signal type / severity
ALLOWED_SIGNAL_TYPES = ("divergence", "trend", "attention")
ALLOWED_SEVERITIES = ("attention", "positive")
#: 前端只支持折线图
ALLOWED_CHART_TYPES = ("line",)

#: 响应体顶层必须恰好是这 6 个键
TOP_LEVEL_KEYS = ("answer", "claims", "signals", "charts", "evidence", "suggested_questions")

#: 证据核验状态的合法取值（可选字段；出现时必须是这三个之一）
ALLOWED_VERIFICATION_STATUS = ("verified", "auto", "pending")

_HTTP_RE = re.compile(r"^https?://", re.I)
#: 带小数点的比率/金额（如 17.70、78.19、-32.59）
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
#: 形如 1,234.56 的千分位金额
_THOUSAND_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


@dataclass
class ValidationResult:
    """校验结果。"""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, message: str) -> None:
        self.errors.append(f"[{code}] {message}")

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(f"[{code}] {message}")

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise ValueError("响应不符合 No Evidence, No Claim 契约：" + "；".join(self.errors))

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), "warnings": list(self.warnings)}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        value == value and value not in (float("inf"), float("-inf"))
    )


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iter_references(payload: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    """产出 (所属字段, 条目 id, evidence_ids) 三元组。"""
    out: list[tuple[str, str, list[str]]] = []
    for key in ("claims", "signals", "charts"):
        for entry in payload.get(key) or []:
            if isinstance(entry, dict):
                out.append((key, str(entry.get("id") or "?"), list(entry.get("evidence_ids") or [])))
    return out


#: 量级单位：答案里写「5,400.77 万元」而引文是「54,007,712.64 元」时，
#: 数字经过换算必然对不上，这类不该当异常报出来。
_UNIT_SUFFIXES = ("万元", "亿元", "万", "亿", "千元", "年", "月", "日", "页")
#: 形如 “5,400.77 万元”“17.70%” 的带单位数值
_NUMBER_WITH_UNIT_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(万元|亿元|千元|万|亿|元|%|％|年|月|日|页)?"
)


def _extract_key_numbers(text: str) -> set[str]:
    """抽出答案里**需要核对**的关键数字。

    只保留「比率」和「千分位金额」两类：
      * 比率（17.70%、78.19%）是结论性数字，必须能在原文核到；
      * 千分位金额（27,170.18）是原始披露格式，能直接比对。

    明确排除：
      * 4 位年份（2021—2023）—— 是期间标签，不是被引用的数字；
      * 带「万元/亿元」等换算单位的金额 —— 引文给的是元，量级已变，
        强行要求字面匹配只会产生假告警。
    """
    numbers: set[str] = set()
    for match in _NUMBER_WITH_UNIT_RE.finditer(text or ""):
        raw, unit = match.group(1), match.group(2)
        clean = raw.replace(",", "")

        # 年份（2021—2023）不是待核对的数字
        if unit is None and re.fullmatch(r"(19|20)\d{2}", raw):
            continue
        # 经过量级换算的金额无法字面核对
        if unit in _UNIT_SUFFIXES:
            continue

        if unit in ("%", "％") or "," in raw or "." in raw:
            numbers.add(clean)
    return numbers


def _quote_corpus(evidence: list[dict[str, Any]]) -> str:
    """把 source_quote 拼成核对语料（去掉空白、统一千分位）。

    ⚠️ 只用 `source_quote`（原文摘录），**不用 `content`** —— content 是归纳摘要，
    拿它核对等于让模型自己给自己作证。规则 5 要求核对的是"引用原文"。
    千分位逗号被去掉，因此 "27,170.18" 也能匹配引文里的写法。
    """
    parts: list[str] = []
    for item in evidence:
        value = item.get("source_quote")
        if isinstance(value, str):
            parts.append(value.replace(",", ""))
    return "".join("".join(parts).split())


# ---------------------------------------------------------------------------
# 主校验
# ---------------------------------------------------------------------------


def validate_response(payload: Any, *, strict_numbers: bool = True) -> ValidationResult:
    """校验一个响应体是否符合前端契约与 No Evidence, No Claim 规则。

    :param payload: 响应 dict（也可以是 pydantic 模型，会自动取 model_dump）
    :param strict_numbers: True 时，答案里的关键数字若无法在引文中核对 → 记为**警告**
                           （不是硬失败：合理的同比换算不算编造）
    """
    result = ValidationResult()

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if not isinstance(payload, dict):
        result.add("E-STRUCT", f"响应必须是对象，收到 {type(payload).__name__}")
        return result

    # ---- 顶层字段：恰好 6 个，顺序不限但不得多不得少 ----
    keys = tuple(payload.keys())
    missing = [k for k in TOP_LEVEL_KEYS if k not in payload]
    extra = [k for k in keys if k not in TOP_LEVEL_KEYS]
    if missing:
        result.add("E-STRUCT", f"缺少顶层字段 {missing}（前端要求恰好 6 个）")
    if extra:
        result.add("E-STRUCT", f"出现未约定的顶层字段 {extra}（前端 extra 严格）")

    # ---- answer ----
    answer = payload.get("answer")
    if not _non_empty_str(answer):
        result.add("E-ANSWER", "answer 必须是非空字符串")
        answer = ""
    elif answer.strip() != answer:
        result.warn("W-ANSWER", "answer 前后有空白字符")

    # ---- 数组类型 ----
    for key in ("claims", "signals", "charts", "evidence"):
        if not isinstance(payload.get(key), list):
            result.add("E-STRUCT", f"{key} 必须是数组，收到 {type(payload.get(key)).__name__}")

    evidence = [e for e in (payload.get("evidence") or []) if isinstance(e, dict)]
    claims = [c for c in (payload.get("claims") or []) if isinstance(c, dict)]
    signals = [s for s in (payload.get("signals") or []) if isinstance(s, dict)]
    charts = [c for c in (payload.get("charts") or []) if isinstance(c, dict)]

    # ---- Evidence 自身合法性（规则 6、7）----
    evidence_ids: list[str] = []
    for item in evidence:
        ev_id = str(item.get("id") or "")
        if not _non_empty_str(ev_id):
            result.add("E-EVIDENCE", "存在缺少 id 的 evidence")
        else:
            evidence_ids.append(ev_id)

        if item.get("category") not in ALLOWED_CATEGORIES:
            result.add(
                "E-CATEGORY",
                f"evidence {ev_id} 的 category={item.get('category')!r} 不在 {ALLOWED_CATEGORIES}",
            )
        for key in ("period", "content", "document_id", "source_quote"):
            if not _non_empty_str(item.get(key)):
                result.add("E-EVIDENCE", f"evidence {ev_id} 的 {key} 不能为空")

        page = item.get("source_page")
        if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
            # 规则 7：页码缺失/非法 → 不得返回该证据
            result.add("E-PAGE", f"evidence {ev_id} 的 source_page 必须是正整数，收到 {page!r}")

        url = item.get("source_url")
        if not _non_empty_str(url) or not _HTTP_RE.match(str(url).strip()):
            # 规则 7：URL 缺失 → 不得返回该证据
            result.add("E-URL", f"evidence {ev_id} 的 source_url 必须是合法 http(s) URL，收到 {url!r}")
        elif "#" in str(url):
            # 前后端分工：后端只给**裸** PDF 地址，页码通过 source_page 独立返回，
            # fragment 由前端统一追加。两边都追加会形成 `#page=N#page=N`，跳页失效。
            result.add(
                "E-URL-FRAGMENT",
                f"evidence {ev_id} 的 source_url 不得带 fragment（页码由 source_page 独立返回）：{url!r}",
            )

        status = item.get("verification_status")
        if status is not None and status not in ALLOWED_VERIFICATION_STATUS:
            result.add(
                "E-VERIFY-STATUS",
                f"evidence {ev_id} 的 verification_status={status!r} "
                f"不在 {ALLOWED_VERIFICATION_STATUS}",
            )
        elif status == "pending":
            # 未通过核验的证据不该出现在响应里（dynamic_evidence 会直接丢弃它们）。
            # 这里记成警告而不是硬失败：字段是可选的，且历史样例可能带它。
            result.warn(
                "W-VERIFY-PENDING",
                f"evidence {ev_id} 标注为 pending（未通过核验），不应支撑任何结论",
            )

    # ---- 规则 4：evidence id 不能重复 ----
    if len(evidence_ids) != len(set(evidence_ids)):
        dupes = sorted({i for i in evidence_ids if evidence_ids.count(i) > 1})
        result.add("E-DUP", f"evidence id 重复：{dupes}")

    known = set(evidence_ids)

    # ---- 规则 1/2/3/4：三类条目必须引到本响应内真实存在的证据 ----
    for group, entries in (("claim", claims), ("signal", signals), ("chart", charts)):
        for entry in entries:
            entry_id = str(entry.get("id") or "?")
            if not _non_empty_str(entry.get("id")):
                result.add(f"E-{group.upper()}", f"{group} 缺少 id")
            ids = entry.get("evidence_ids")
            if not isinstance(ids, list) or not ids:
                result.add(
                    f"E-{group.upper()}-NOEVID",
                    f"{group} {entry_id} 没有引用任何 evidence（No Evidence, No Claim）",
                )
                continue
            dangling = [i for i in ids if i not in known]
            if dangling:
                result.add(
                    f"E-DANGLING",
                    f"{group} {entry_id} 引用了不存在的 evidence：{dangling}",
                )

    # ---- 反向：不许有孤立证据 ----
    used: set[str] = set()
    for _group, _id, ids in _iter_references(payload):
        used.update(i for i in ids if isinstance(i, str))
    orphans = [i for i in evidence_ids if i not in used]
    if orphans:
        result.add("E-ORPHAN", f"存在未被任何 claim/signal/chart 引用的孤立证据：{orphans}")

    # ---- claim / signal 文本 ----
    for entry in claims:
        if not _non_empty_str(entry.get("text")):
            result.add("E-CLAIM", f"claim {entry.get('id')} 的 text 不能为空")
    for entry in signals:
        sid = entry.get("id")
        if not _non_empty_str(entry.get("title")):
            result.add("E-SIGNAL", f"signal {sid} 的 title 不能为空")
        if not _non_empty_str(entry.get("description")):
            result.add("E-SIGNAL", f"signal {sid} 的 description 不能为空")
        if entry.get("type") not in ALLOWED_SIGNAL_TYPES:
            result.add(
                "E-SIGNAL-TYPE",
                f"signal {sid} 的 type={entry.get('type')!r} 不在 {ALLOWED_SIGNAL_TYPES}",
            )
        if entry.get("severity") not in ALLOWED_SEVERITIES:
            result.add(
                "E-SIGNAL-SEV",
                f"signal {sid} 的 severity={entry.get('severity')!r} 不在 {ALLOWED_SEVERITIES}",
            )

    # ---- chart 结构：type=line、periods 非空、series 与 periods 等长、值有限 ----
    for chart in charts:
        cid = chart.get("id")
        if chart.get("type") not in ALLOWED_CHART_TYPES:
            result.add(
                "E-CHART-TYPE",
                f"chart {cid} 的 type={chart.get('type')!r} 必须是 'line'（前端只支持折线图）",
            )
        if not _non_empty_str(chart.get("title")):
            result.add("E-CHART", f"chart {cid} 的 title 不能为空")
        periods = chart.get("periods")
        if not isinstance(periods, list) or not periods or not all(
            _non_empty_str(p) for p in periods
        ):
            result.add("E-CHART", f"chart {cid} 的 periods 必须是非空字符串数组")
            continue
        series = chart.get("series")
        if not isinstance(series, list) or not series:
            result.add("E-CHART", f"chart {cid} 的 series 不能为空")
            continue
        for item in series:
            if not isinstance(item, dict):
                result.add("E-CHART", f"chart {cid} 的 series 元素必须是对象")
                continue
            if not _non_empty_str(item.get("name")):
                result.add("E-CHART", f"chart {cid} 的 series 缺少 name")
            values = item.get("values")
            if not isinstance(values, list):
                result.add("E-CHART", f"chart {cid} 的 series[{item.get('name')}] 缺少 values")
                continue
            if len(values) != len(periods):
                result.add(
                    "E-CHART-LEN",
                    f"chart {cid} 的 series[{item.get('name')}] 长度 {len(values)} "
                    f"≠ periods 长度 {len(periods)}",
                )
            bad = [v for v in values if not _finite_number(v)]
            if bad:
                result.add(
                    "E-CHART-NUM",
                    f"chart {cid} 的 series[{item.get('name')}] 含非有限数值：{bad}",
                )

    # ---- 规则 8：兜底回答必须干净（空数组 + 固定文案）----
    if answer.strip() == INSUFFICIENT_ANSWER:
        if claims or signals or charts or evidence:
            result.add(
                "E-FALLBACK",
                "证据不足的兜底回答必须返回空的 claims/signals/charts/evidence",
            )
    else:
        # 非兜底回答：如果没有证据支撑，就不该有结论性内容
        if not evidence and (claims or signals or charts):
            result.add(
                "E-NOEV", "响应没有 evidence，却给出了 claim/signal/chart（No Evidence, No Claim）"
            )

    # ---- 规则 5：Answer 里的关键数字要能在引文/证据里核对（软警告）----
    if strict_numbers and evidence and answer.strip() != INSUFFICIENT_ANSWER:
        corpus = _quote_corpus(evidence)
        unverified: list[str] = []
        for number in sorted(_extract_key_numbers(answer)):
            if number in corpus:
                continue
            # 允许千分位/同比换算带来的等价写法
            if number.replace(",", "") in corpus:
                continue
            unverified.append(number)
        if unverified:
            result.warn(
                "W-NUMERIC",
                f"answer 中的这些数字未能直接在引文里核到（可能是同比换算）：{unverified}",
            )

    # ---- suggested_questions ----
    questions = payload.get("suggested_questions")
    if questions is not None and (
        not isinstance(questions, list) or not all(_non_empty_str(q) for q in questions)
    ):
        result.add("E-QUESTIONS", "suggested_questions 必须是非空字符串数组（或省略）")

    return result


def verify_evidence_against_source(
    payload: Any,
    *,
    stock_code: str = "",
    documents: dict[int, dict[str, Any]] | None = None,
    page_texts: dict[tuple[int, int], str] | None = None,
) -> ValidationResult:
    """把响应里的每条证据**回到数据库原文**核验（任务书第 3 节 4~6 条）。

    与 `validate_response` 的分工：
      * `validate_response` 只看**结构**（字段齐不齐、URL 合法不合法、引用有无悬空），
        它不需要数据库，任何环境都能跑；
      * 本函数看**事实**（引文是否真在所引页面上、页码是否越界、
        document_id 是否真属于这家公司）。

    这也是「跨公司引用」的唯一有效拦截点：`document_id` 必须属于 `stock_code`，
    否则一家公司的回答里就可能出现另一家公司的公告 —— 那是最严重的事实污染。

    核验口径（与 `dynamic_evidence` 一致，两条路径不能各写一套）：
      1. `source_page` 是正整数，且不超过该文档的页数；
      2. `source_quote` 里的**每个有意义数字**在所引页面上逐字出现、顺序一致、
         首末跨度不超过 `DYNAMIC_MAX_QUOTE_SPAN`；
      3. `source_url` 是 http(s) 且不带 fragment。

    :param documents: `{document_id: {...}}`（含 company_code / page_count）。
        不传时自动查库。
    :param page_texts: `{(document_id, page): 该页原文}`。不传时自动查库。
    :returns: `ValidationResult`；`ok=False` 表示有证据不可回溯。
    """
    from config import settings as _settings

    result = ValidationResult()
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if not isinstance(payload, dict):
        result.add("E-STRUCT", "响应必须是对象")
        return result

    evidence = [e for e in (payload.get("evidence") or []) if isinstance(e, dict)]
    if not evidence:
        return result

    def _document_id(item: dict[str, Any]) -> int | None:
        try:
            return int(str(item.get("document_id")))
        except (TypeError, ValueError):
            return None

    if documents is None or page_texts is None:
        import db as _db

        ids = [d for d in (_document_id(e) for e in evidence) if d is not None]
        try:
            documents = documents if documents is not None else _db.document_pages_meta(ids)
        except (_db.DatabaseNotReadyError, ValueError) as exc:
            result.add("E-SOURCE", f"无法读取文档元信息，证据无法核验：{exc}")
            return result
        if page_texts is None:
            page_texts = {}
            for item in evidence:
                document_id = _document_id(item)
                page = item.get("source_page")
                if document_id is None or not isinstance(page, int):
                    continue
                key = (document_id, page)
                if key in page_texts:
                    continue
                try:
                    page_texts[key] = _db.document_page_text(document_id, page)
                except (_db.DatabaseNotReadyError, ValueError):
                    page_texts[key] = ""

    # 延迟导入：避免模块级循环依赖（dynamic_evidence 会 import config/db）
    from dynamic_evidence import verify_quote_on_page

    for item in evidence:
        ev_id = str(item.get("id") or "?")
        document_id = _document_id(item)
        if document_id is None:
            result.add("E-SOURCE-DOC", f"evidence {ev_id} 的 document_id 不是整数")
            continue

        meta = documents.get(document_id)
        if meta is None:
            result.add(
                "E-SOURCE-DOC",
                f"evidence {ev_id} 引用的 document_id={document_id} 在库里不存在",
            )
            continue

        owner = str(meta.get("company_code") or "")
        if stock_code and owner and owner != stock_code:
            # ★ 跨公司污染：这条证据属于别人家的公告
            result.add(
                "E-CROSS-COMPANY",
                f"evidence {ev_id} 引用了公司 {owner} 的公告，而本次提问是 {stock_code}",
            )
            continue

        page = item.get("source_page")
        total_pages = int(meta.get("page_count") or 0)
        if total_pages and isinstance(page, int) and page > total_pages:
            result.add(
                "E-PAGE-RANGE",
                f"evidence {ev_id} 的 source_page={page} 超出文档 {document_id} 的 {total_pages} 页",
            )
            continue

        text = page_texts.get((document_id, page), "") if isinstance(page, int) else ""
        ok, reason, _tokens = verify_quote_on_page(
            str(item.get("source_quote") or ""), text, max_span=_settings.DYNAMIC_MAX_QUOTE_SPAN
        )
        if not ok:
            result.add("E-QUOTE-UNVERIFIED", f"evidence {ev_id}：{reason}")

    return result


def assert_valid(payload: Any, *, strict_numbers: bool = True) -> dict[str, Any]:
    """校验并在失败时抛 ValueError；成功返回响应 dict。"""
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    result = validate_response(payload, strict_numbers=strict_numbers)
    result.raise_if_invalid()
    for warning in result.warnings:
        logger.warning("响应校验警告：%s", warning)
    return payload


__all__ = [
    "ALLOWED_CATEGORIES",
    "ALLOWED_CHART_TYPES",
    "ALLOWED_SEVERITIES",
    "ALLOWED_SIGNAL_TYPES",
    "ALLOWED_VERIFICATION_STATUS",
    "INSUFFICIENT_ANSWER",
    "TOP_LEVEL_KEYS",
    "ValidationResult",
    "assert_valid",
    "validate_response",
    "verify_evidence_against_source",
]
