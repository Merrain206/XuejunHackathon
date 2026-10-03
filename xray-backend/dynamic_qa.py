"""X-Ray 企业穿透分析 · 动态 Evidence-first 问答（四家公司通用问题）。

定位
----
`ask.py` 的决策顺序里，这是**第三条**分支：

    1. 思看科技三条稳定问题 → `demo_handlers`（确定性、毫秒级，不碰模型）
    2. 明确证据不足 / 不在覆盖面内的问题（如「员工喜欢吃水果」）→ 固定兜底，**不调模型**
    3. 其他公司、其他金融问题 → **本模块**
    4. 本模块任一环节失败 → 固定兜底（HTTP 仍 200）

流程（先检索、再让模型组织、最后机械校验）
----------------------------------------
    问题
      → dynamic_evidence.retrieve_candidates()  ← 参数化 SQL、逐条回原文核验
      → 组装 Prompt（模型只能从候选 id 里挑）
      → llm_client.generate_answer()            ← 复用同一个 SDK，不引入第二套
      → 解析 JSON（最多一次轻量修复）
      → 机械校验（悬空 id / 无证据条目 / 数字不在引文里）
      → 组装响应（charts 固定 []）

模型**没有**生成 evidence 的能力：`evidence` 数组完全由候选集合决定，
模型只输出 `answer` / `claims` / `signals`。它给不出合法的 evidence_ids
就整次返回证据不足 —— 这是 `No Evidence, No Claim` 在这个分支的落地方式。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Sequence

from config import settings
from demo_handlers import DEMO_COMPANY_CODE, build_insufficient_response
from dynamic_evidence import (
    Candidate,
    candidates_by_id,
    is_in_scope,
    is_risk_question,
    render_candidates_for_prompt,
    retrieve_candidates,
)
from llm_client import LLMResult, generate_answer
from response_validator import INSUFFICIENT_ANSWER, _extract_key_numbers, _quote_corpus
from schemas import to_evidence_category

logger = logging.getLogger(__name__)

#: 来源标记（进 X-XRay-Cache 响应头，便于演示时一眼看出走了哪条路径）
SOURCE_DYNAMIC = "dynamic-llm"
SOURCE_INSUFFICIENT = "insufficient"

#: 允许的 chart 类型（P0 固定为空数组，这里留常量是为了将来收敛时不必改校验）
_ALLOWED_CHART_TYPES = ("line",)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是严谨的上市公司公开信息分析助手。

【输出格式 —— 最重要的一条，违反会被程序丢弃】
你的整个回复必须**只有一个 JSON 对象**：从第一个字符 `{` 开始，到最后一个字符 `}` 结束。
* 不要在 JSON 前后写任何分析、说明、前言、总结或结语 —— 一句话都不要；
* 不要用 markdown 代码块（不要 ```json）；
* 不要输出 JSON 以外的任何字符。
你可以在模型内部的 reasoning 里思考，但**正式回复里只放 JSON**。
（历史教训：先写一段分析、末尾再补一个 JSON，会让整次回答被判定为无效。）

【绝对硬性规则】
1. 你**只能**使用用户给出的【候选证据】里的内容。不得引入任何外部知识、传闻或推测。
2. 每条 claim / signal 的 `evidence_ids` 只能填候选证据里出现过的 id（形如 EV-001）。
   不许自己发明 id，不许引用候选之外的任何来源。
3. 找不到足够依据时，把 answer 写成「根据目前掌握的信息，我无法可靠回答这个问题。」，
   并让 claims 与 signals 都返回空数组 []。**绝对不许编造结论来凑数。**
4. answer 与每条 claim 里出现的金额、比例，必须能在你引用的候选证据原文摘录里
   逐字找到（可以换单位描述，但数字本身不要改、不要自己推算新数字）。
5. 不要做投资建议，不要预测股价，不要评价管理层人品。

【输出格式（严格遵守，字段名一个字都不能改）】
{
  "answer": "3-5 句中文结论，说明数据表现与原因",
  "claims": [
    {"id": "CL-DYN-001", "text": "一句可核验的结论", "evidence_ids": ["EV-001"]}
  ],
  "signals": [
    {"id": "SIG-DYN-001", "type": "divergence|trend|attention",
     "title": "不超过20字的短标题", "severity": "attention|positive",
     "description": "这条信号说明什么（不超过200字）", "evidence_ids": ["EV-001"]}
  ]
}

signal.type 的含义（按这个选，不要问"编号定义"）：
  * divergence —— 两个指标方向背离（如收入增长但利润/现金流下降）；
  * trend      —— 单一指标在多个报告期呈现持续方向（连续下降/回升）；
  * attention  —— 需要关注、但还不构成明确背离或趋势的情况。
severity 只用两个取值：attention（需关注）/ positive（偏正面）。

如果证据不足以回答，返回这一行（不要有别的内容）：
{"answer":"根据目前掌握的信息，我无法可靠回答这个问题。","claims":[],"signals":[]}

再说最后一次：只输出 JSON，从 `{` 开始，到 `}` 结束，前后不要有任何其他文字。
"""

#: 给用户消息末尾再钉一次格式要求。实测把要求同时放在 system 与 user 两条消息里，
#: 比只放 system 更容易让模型守住「纯 JSON」这条线。
_JSON_REMINDER = (
    "\n\n【再次确认输出格式】只输出一个 JSON 对象（从 { 开始，到 } 结束），"
    "前后不要写任何分析文字，不要用 markdown 代码块。"
)


def build_user_prompt(question: str, candidates: Sequence[Candidate], stock_code: str) -> str:
    """组装用户 Prompt：问题 + 候选证据（编号、指标、报告期、原文摘录）。"""
    risk_scope = (
        "\n【风险问题边界】只能总结候选财务证据直接反映的风险信号；"
        "不得声称已覆盖公司的全部经营、法律、行业或合规风险。\n"
        if is_risk_question(question)
        else ""
    )
    return (
        f"公司代码：{stock_code}\n"
        f"问题：{question}\n"
        f"{risk_scope}\n"
        f"【候选证据】（只能引用这些 id）\n"
        f"{render_candidates_for_prompt(candidates)}\n\n"
        f"请基于以上候选证据回答问题。若证据不足，按规则 3 返回兜底 JSON。"
        f"{_JSON_REMINDER}"
    )


# ---------------------------------------------------------------------------
# JSON 解析（含一次轻量修复）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)

#: 「像回答」的对象里可接受的键名。
#:
#: ⚠️ 实测（deepseek-flash，真实接入点）：即使 system prompt 三次强调输出格式，
#:    模型仍会自己改字段名 —— 出现过 `{"结论": "...", "claims": [...]}` 和
#:    `{"conclusion": "..."}`。因为一个键名不符就把**内容完全正确、引用也正确**
#:    的回答判成"非法 JSON"，是实测踩到的最大浪费。所以：
#:      * 宽松识别键名（含中文别名）；
#:      * 解析成功后**归一化**成约定的 `answer`/`claims`/`signals`。
_PAYLOAD_KEYS = ("answer", "claims", "signals")
_ANSWER_ALIASES = ("answer", "结论", "conclusion", "回答", "summary", "摘要", "result")
_CLAIMS_ALIASES = ("claims", "主张", "结论列表")
_SIGNALS_ALIASES = ("signals", "信号", "风险信号")


def parse_llm_json(text: str) -> dict[str, Any] | None:
    """把模型输出解析成 dict（并归一化键名）；解析不出返回 None。

    三层容错，全部是**确定性**修复，不猜内容：

      1. 整串 / 去 ``` 围栏后就是 JSON；
      2. 从散文里扫出所有**花括号平衡**的片段，逐个尝试（专治"分析 + 末尾 JSON"）；
      3. 首尾花括号截取（兜底）。

    解析成功后按 `_normalize_payload` 归一化键名（`结论` → `answer` 等）。

    只做这些修复，不做无限重试：模型持续返回垃圾时重试三次只会把 20 秒预算耗光，
    结果一样是兜底。
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    candidates: list[Any] = []
    for attempt in (raw, _FENCE_RE.sub("", raw).strip()):
        candidates.append(_try_json(attempt))
    for block in _brace_blocks(raw):
        candidates.append(_try_json(block))
    first, last = raw.find("{"), raw.rfind("}")
    if 0 <= first < last:
        candidates.append(_try_json(raw[first : last + 1]))

    for parsed in candidates:
        normalized = _normalize_payload(parsed)
        if normalized is not None:
            return normalized
    return None


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _normalize_payload(value: Any) -> dict[str, Any] | None:
    """把模型给的对象归一化成约定的键名；不像回答就返回 None。

    归一化只做**改名**，不改内容：
      * `answer` ← 第一个命中的 `_ANSWER_ALIASES`；
      * `claims` / `signals` ← 别名，或对象里唯一一个「元素都是对象且带 evidence_ids」
        的数组（模型有时会把它们塞在别的键下）。
    """
    if not isinstance(value, dict):
        return None

    answer: Any = None
    for key in _ANSWER_ALIASES:
        if key in value and isinstance(value[key], str) and value[key].strip():
            answer = value[key]
            break

    def _pick(aliases: tuple[str, ...]) -> Any:
        for key in aliases:
            found = value.get(key)
            if isinstance(found, list):
                return found
        return None

    claims = _pick(_CLAIMS_ALIASES)
    signals = _pick(_SIGNALS_ALIASES)

    # 兜底：从所有数组里认出「引用了 evidence_ids 的那种」
    if claims is None or signals is None:
        for item in value.values():
            if not isinstance(item, list):
                continue
            entries = [x for x in item if isinstance(x, dict)]
            if not entries or not all("evidence_ids" in x for x in entries):
                continue
            typed = [str(x.get("type") or "") for x in entries]
            looks_like_signal = all(t in ("divergence", "trend", "attention") for t in typed)
            if signals is None and looks_like_signal:
                signals = item
            elif claims is None and not looks_like_signal:
                claims = item

    if answer is None and claims is None and signals is None:
        return None
    return {
        "answer": answer if isinstance(answer, str) else "",
        "claims": claims if isinstance(claims, list) else [],
        "signals": signals if isinstance(signals, list) else [],
    }


def _brace_blocks(text: str, *, limit: int = 64) -> list[str]:
    """扫出所有**花括号平衡**的片段（按起点从后往前，优先拿最后那个）。

    从后往前是刻意的：实测模型的散文里会夹带 `（EV-001）` 这类圆括号内容，
    而真正的 JSON 在末尾；先试末尾的块能少解析几次，也避免把文中
    意外出现的 `{...}` 当成回答。

    字符串状态要跟踪，否则 `{"text": "}"}` 里的花括号会被误判成块边界。
    """
    blocks: list[tuple[int, int]] = []
    stack: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            blocks.append((start, index + 1))
            if len(blocks) >= limit:
                break
    blocks.sort(key=lambda span: span[0], reverse=True)
    return [text[start:end] for start, end in blocks]


# ---------------------------------------------------------------------------
# 机械校验
# ---------------------------------------------------------------------------


def _clean_evidence_ids(raw: Any, known: set[str]) -> list[str]:
    """只保留候选集合里真实存在的 id（保序去重）。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value in known and value not in out:
            out.append(value)
    return out


def _numbers_supported(text: str, corpus: str) -> list[str]:
    """`text` 里有没有**引文核不到**的关键数字（比率/千分位金额）。

    复用 `response_validator` 的数字抽取口径，保证「动态回答」与「稳定三问」
    用的是同一把尺子 —— 两处各写一套迟早会漂移。
    """
    missing: list[str] = []
    for number in sorted(_extract_key_numbers(text)):
        if number in corpus or number.replace(",", "") in corpus:
            continue
        missing.append(number)
    return missing


def validate_dynamic_payload(
    payload: dict[str, Any],
    candidates: Sequence[Candidate],
    *,
    question: str = "",
    stock_code: str = "",
    max_evidence: int | None = None,
    max_claims: int | None = None,
    max_signals: int | None = None,
    strict_numbers: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """把模型输出**收敛成**可返回的结构，并返回被丢弃的原因。

    校验规则（对应任务书第 3 节 1~8 条）：

      1. 每条 claim / signal 至少引用一条本次候选证据；
      2. 引用的 id 必须存在于本次候选集合（悬空 id → 丢弃该条）；
      3. 页码与链接不由模型提供（候选已被机械核验过），因此不会被模型污染；
      4~6. Claim / Signal 的数字必须能在它自己引用的 Evidence 原文里核到；
           动态正式链路默认硬校验，不能从未引用候选中借用数字；
      7. 丢弃后没有 claim 且没有 signal → 整次证据不足；
      8. 模型不得使用候选之外的公司事实（规则 2 的必然结果）。

    :returns: `(payload, notes)`；`payload` 的 evidence 数组**只包含被引用到的候选**。
    """
    notes: list[str] = []
    max_evidence = int(max_evidence or settings.DYNAMIC_MAX_EVIDENCE)
    max_claims = int(max_claims or settings.DYNAMIC_MAX_CLAIMS)
    max_signals = int(max_signals or settings.DYNAMIC_MAX_SIGNALS)

    by_id = candidates_by_id(candidates)
    known = set(by_id)

    answer = str(payload.get("answer") or "").strip()
    raw_claims = payload.get("claims") if isinstance(payload.get("claims"), list) else []
    raw_signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []

    claims: list[dict[str, Any]] = []
    for entry in raw_claims:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        ids = _clean_evidence_ids(entry.get("evidence_ids"), known)
        if not ids:
            notes.append(f"claim {entry.get('id')!r} 没有有效的候选证据引用，已丢弃")
            continue
        claims.append(
            {
                "id": str(entry.get("id") or f"CL-DYN-{len(claims) + 1:03d}")[:64],
                "text": text[:1000],
                "evidence_ids": ids,
            }
        )
        if len(claims) >= max_claims:
            break

    signals: list[dict[str, Any]] = []
    for entry in raw_signals:
        if not isinstance(entry, dict):
            continue
        ids = _clean_evidence_ids(entry.get("evidence_ids"), known)
        if not ids:
            notes.append(f"signal {entry.get('id')!r} 没有有效的候选证据引用，已丢弃")
            continue
        title = str(entry.get("title") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not title or not description:
            notes.append(f"signal {entry.get('id')!r} 缺少 title/description，已丢弃")
            continue
        signal_type = str(entry.get("type") or "attention").strip()
        if signal_type not in ("divergence", "trend", "attention"):
            notes.append(f"signal {entry.get('id')!r} 的 type={signal_type!r} 不合法，已丢弃")
            continue
        severity = str(entry.get("severity") or "attention").strip()
        if severity not in ("attention", "positive"):
            severity = "attention"
        signals.append(
            {
                "id": str(entry.get("id") or f"SIG-DYN-{len(signals) + 1:03d}")[:64],
                "type": signal_type,
                "title": title[:64],
                "severity": severity,
                "description": description[:1000],
                "evidence_ids": ids,
            }
        )
        if len(signals) >= max_signals:
            break

    if not claims and not signals:
        # 模型只给了一段 prose（没按格式给 claims）—— 实测 deepseek-flash 经常这样：
        # answer 里写着「营业收入连续两期下滑…（EV-001、EV-002）」，claims 却是 []。
        # 直接丢掉等于把一份带正确引用的回答白白降级成"无法回答"，
        # 所以在这里**机械化地**把有引用编号的句子转成 claim。
        coerced = _coerce_answer_into_claims(answer, by_id)
        if coerced:
            claims, extra = coerced
            notes.extend(extra)
        else:
            notes.append("模型没有给出任何带有效证据的 claim/signal，整次按证据不足处理")
            return _insufficient_payload(), notes

    if strict_numbers:
        verified_claims: list[dict[str, Any]] = []
        for entry in claims:
            missing = _numbers_unsupported_by_references(entry["text"], entry["evidence_ids"], by_id)
            if missing:
                notes.append(
                    f"claim {entry['id']} 的数字未被其引用 Evidence 支撑，已丢弃：{missing}"
                )
                continue
            verified_claims.append(entry)
        claims = verified_claims

        verified_signals: list[dict[str, Any]] = []
        for entry in signals:
            signal_text = f"{entry['title']} {entry['description']}"
            missing = _numbers_unsupported_by_references(
                signal_text, entry["evidence_ids"], by_id
            )
            if missing:
                notes.append(
                    f"signal {entry['id']} 的数字未被其引用 Evidence 支撑，已丢弃：{missing}"
                )
                continue
            verified_signals.append(entry)
        signals = verified_signals

        if not claims and not signals:
            notes.append("逐条引用数字校验后没有剩余 claim/signal，整次按证据不足处理")
            return _insufficient_payload(), notes
    if answer.strip() == INSUFFICIENT_ANSWER or not answer:
        answer = _answer_from_claims(claims)

    # ---- 只保留被引用到的候选证据（No Evidence, No Claim 的反向约束）----
    used: list[str] = []
    for group in (claims, signals):
        for entry in group:
            for ev_id in entry["evidence_ids"]:
                if ev_id not in used:
                    used.append(ev_id)
    selected = [by_id[ev_id] for ev_id in used[:max_evidence]]

    # 被 max_evidence 截掉的 id 不能再留在 claims/signals 里。删掉引用后必须再做
    # 一次逐条数字核验，防止结论依赖的恰好是被截掉的那条 Evidence。
    kept = {c.id for c in selected}
    claims = [_drop_dangling(entry, kept) for entry in claims]
    signals = [_drop_dangling(entry, kept) for entry in signals]
    claims = [entry for entry in claims if entry["evidence_ids"]]
    signals = [entry for entry in signals if entry["evidence_ids"]]
    if strict_numbers:
        claims = [
            entry for entry in claims
            if not _numbers_unsupported_by_references(
                entry["text"], entry["evidence_ids"], by_id
            )
        ]
        signals = [
            entry for entry in signals
            if not _numbers_unsupported_by_references(
                f"{entry['title']} {entry['description']}", entry["evidence_ids"], by_id
            )
        ]
    if not claims and not signals:
        notes.append("证据被上限截断后没有剩余引用，整次按证据不足处理")
        return _insufficient_payload(), notes

    # 去掉因整条 Claim/Signal 被截断而变成孤儿的 Evidence。
    active_ids = {
        ev_id
        for group in (claims, signals)
        for entry in group
        for ev_id in entry["evidence_ids"]
    }
    selected = [candidate for candidate in selected if candidate.id in active_ids]

    # Answer 没有独立的 evidence_ids，只能使用最终返回的 Evidence 数字并集。
    # 若模型 Answer 带入了其它候选或编造数字，用已经逐条核验的 Claim 重建；
    # 没有 Claim 可以重建时，整次拒答。
    corpus = _quote_corpus([
        {"source_quote": c.raw_quote or c.display_quote or c.source_quote}
        for c in selected
    ])
    missing_answer = _numbers_supported(answer, corpus)
    dangling_answer_refs = sorted(set(_EVIDENCE_REF_RE.findall(answer)) - active_ids)
    if missing_answer or dangling_answer_refs:
        if missing_answer:
            notes.append(f"answer 的数字未被最终返回 Evidence 支撑：{missing_answer}")
        if dangling_answer_refs:
            notes.append(f"answer 引用了未最终返回的 Evidence：{dangling_answer_refs}")
        if claims:
            answer = _answer_from_claims(claims)
        else:
            return _insufficient_payload(), notes

    return (
        {
            "answer": answer,
            "claims": claims,
            "signals": signals,
            "charts": [],
            "evidence": [_normalize_evidence(c) for c in selected],
            "suggested_questions": suggested_questions_for(question, stock_code=stock_code),
        },
        notes,
    )


def _drop_dangling(entry: dict[str, Any], kept: set[str]) -> dict[str, Any]:
    return {**entry, "evidence_ids": [i for i in entry["evidence_ids"] if i in kept]}


#: 模型明确表示"答不了"的说法。出现这些词时**不做** prose→claim 转换：
#: 那说明模型自己判断证据不足，把它的拒答句子硬转成 claim 就是自欺欺人。
_INSUFFICIENT_MARKERS = (
    "数据不足",
    "无法判断",
    "无法可靠回答",
    "无法回答",
    "没有对应数据",
    "未提供",
    "未披露",
    "无法确认",
    "不足以",
    "insufficient",
)

#: 句末切分（中文句号/分号/换行）
_SENTENCE_SPLIT_RE = re.compile(r"[。；;\n]+")
#: 句子里引用的候选编号（EV-001 / EV-001、EV-002 / EV-001,EV-002）
_EVIDENCE_REF_RE = re.compile(r"EV-\d{1,4}")


def _numbers_unsupported_by_references(
    text: str,
    evidence_ids: Sequence[str],
    by_id: dict[str, Candidate],
) -> list[str]:
    """只用条目自己引用的 Evidence 核验金额和比例。"""
    referenced = [by_id[ev_id] for ev_id in evidence_ids if ev_id in by_id]
    corpus = _quote_corpus([
        {"source_quote": c.raw_quote or c.display_quote or c.source_quote}
        for c in referenced
    ])
    return _numbers_supported(text, corpus)


def _is_insufficient_answer(answer: str) -> bool:
    """模型是否明确表示"答不了"（含固定兜底文案或数据不足类措辞）。"""
    text = str(answer or "").strip()
    if not text:
        return True
    if text == INSUFFICIENT_ANSWER:
        return True
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in _INSUFFICIENT_MARKERS)


def _coerce_answer_into_claims(
    answer: str, by_id: dict[str, Candidate]
) -> tuple[list[dict[str, Any]], list[str]] | None:
    """把"带引用编号的 prose 答案"机械转换成 claims。

    为什么需要：真实模型经常返回 `{"answer": "...（EV-001、EV-002）...", "claims": []}`。
    解析没问题、引用也真实存在，但 claims 为空 → 按规则 7 会整次降级为证据不足，
    于是一份**内容正确**的回答在界面上变成「无法回答」。

    转换规则（全部可机械验证，不含任何自由发挥）：
      1. 按句切分，只保留**引用了候选编号**的句子；
      2. 句子里的编号必须都在本次候选集合内（否则整句丢弃）；
      3. 只保留含**可核验数字**的句子（否则它支撑不了 `No Evidence, No Claim`）；
      4. 上限 `DYNAMIC_MAX_CLAIMS` 条。

    ⚠️ 拒答句（含"数据不足/无法判断"等）一律不转换 —— 那不是结论。
    """
    text = str(answer or "").strip()
    if not text:
        return None
    if text == INSUFFICIENT_ANSWER:
        return None

    notes: list[str] = []
    claims: list[dict[str, Any]] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        # 含"数据不足"的句子一律跳过：那是模型的**保留意见**，不是结论。
        # 注意判据是**逐句**而不是整段 —— 实测模型会把「营业收入…下滑 11.65%」
        # 和「更长期趋势数据不足」写在同一段里，整段否决会把前面的结论一起扔掉。
        if _is_insufficient_answer(sentence):
            continue
        ids = [i for i in _EVIDENCE_REF_RE.findall(sentence) if i in by_id]
        if not ids:
            continue
        unique_ids: list[str] = []
        for ev_id in ids:
            if ev_id not in unique_ids:
                unique_ids.append(ev_id)
        if not _extract_key_numbers(sentence):
            continue
        claims.append(
            {
                "id": f"CL-DYN-{len(claims) + 1:03d}",
                "text": sentence[:1000],
                "evidence_ids": unique_ids,
            }
        )
        if len(claims) >= settings.DYNAMIC_MAX_CLAIMS:
            break

    if not claims:
        return None
    notes.append(f"模型未按格式给出 claims，已从 answer 的引用句机械转换出 {len(claims)} 条")
    return claims, notes


def _normalize_evidence(candidate: Candidate) -> dict[str, Any]:
    """候选 → 响应体 evidence[]（category 归一到前端只认的三类）。"""
    item = candidate.to_response_item()
    item["category"] = to_evidence_category(item.get("category"))
    return item


def _answer_from_claims(claims: Sequence[dict[str, Any]]) -> str:
    """模型没给 answer 但给了带证据的 claim 时，用 claim 文本拼一句。

    宁可给一句朴素的结论，也不要因为 answer 字段缺失就把整次回答丢掉 ——
    那会让一个本来有证据的答案退化成「无法回答」。
    """
    parts = [str(c["text"]).rstrip("。；;，, ") for c in claims[:3]]
    return "根据公开披露数据：" + "；".join(parts) + "。"


def _insufficient_payload() -> dict[str, Any]:
    return build_insufficient_response()


# ---------------------------------------------------------------------------
# 推荐问题
# ---------------------------------------------------------------------------


def suggested_questions_for(question: str, *, stock_code: str = "") -> list[str]:
    """本次响应的推荐问题。

    * 思看科技（688583）保持它自己那四条（`DEFAULT_QUESTIONS`）与固定顺序；
    * 其他公司用产品约定的四条通用问题（`DYNAMIC_QUESTIONS`）。

    ⚠️ 一律由**后端常量**决定，绝不让模型自由生成推荐问题 ——
    模型推荐的往往是它自己没证据覆盖的问题。
    """
    del question  # 保留签名：将来若要按问题裁剪，改动只在这里
    if (stock_code or "").strip() == DEMO_COMPANY_CODE:
        return list(settings.DEFAULT_QUESTIONS)
    return list(settings.DYNAMIC_QUESTIONS)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def is_supported_company(stock_code: str) -> bool:
    """该公司是否在**产品确认的四家**名单里（`settings.SUPPORTED_COMPANY_CODES`）。

    任务书第 5 节只承诺支持 688583 / 600570 / 000066 / 300558。
    名单之外的公司即使将来库里有了 evidence，也**不走动态问答** ——
    否则"支持哪些公司"就变成由数据碰巧决定，而不是由产品决定，
    演示时对着一家没确认过的公司给出回答是风险。
    """
    code = str(stock_code or "").strip()
    return code in {str(c).strip() for c in settings.SUPPORTED_COMPANY_CODES}


def build_dynamic_response(
    stock_code: str,
    question: str,
    *,
    llm: Any = None,
) -> tuple[dict[str, Any], str]:
    """动态问答主入口。

    :param llm: 可注入的 LLM 调用（默认 `llm_client.generate_answer`）。
        测试用假实现注入，避免真联网。
    :returns: `(payload, source)`；任何失败都返回**固定兜底**结构与 `"insufficient"`。
    """
    started = time.perf_counter()
    code = str(stock_code or "").strip()

    if not settings.DYNAMIC_QA_ENABLED:
        return build_insufficient_response(), SOURCE_INSUFFICIENT

    # 名单之外的公司：不检索、不调模型，直接兜底（见 is_supported_company 的说明）
    if not is_supported_company(code):
        logger.info("公司 %s 不在动态问答支持名单内，返回固定兜底", code)
        return _with_questions(build_insufficient_response(), code), SOURCE_INSUFFICIENT

    # 覆盖面粗判：问题映射不到任何已知财务指标 → 不调模型，直接兜底。
    # 「你的员工喜欢吃水果吗？」就走这条路（任务书第 7 节明确要求不调用模型）。
    if not is_in_scope(question):
        logger.info("问题不在动态问答覆盖面内（%s），返回固定兜底且不调用模型", question[:40])
        return _with_questions(build_insufficient_response(), code), SOURCE_INSUFFICIENT

    try:
        candidates = retrieve_candidates(code, question)
    except Exception:  # noqa: BLE001 - 检索异常也必须降级，不能把 500 抛给前端
        logger.exception("候选证据检索失败 %s", code)
        return _with_questions(build_insufficient_response(), code), SOURCE_INSUFFICIENT

    if not candidates:
        logger.info("公司 %s 没有可用候选证据，返回固定兜底", code)
        return _with_questions(build_insufficient_response(), code), SOURCE_INSUFFICIENT

    prompt = build_user_prompt(question, candidates, code)
    call = llm or generate_answer
    result: LLMResult = call(
        question,
        [c.to_prompt_item() for c in candidates],
        stock_code=code,
        prompt_override=prompt,
        system_prompt_override=SYSTEM_PROMPT,
        thinking=False,
    )

    if not result.ok:
        logger.warning(
            "动态问答降级：模型不可用（%s）company=%s", result.fallback_reason, code
        )
        return _with_questions(build_insufficient_response(), code), SOURCE_INSUFFICIENT

    payload = parse_llm_json(result.text)
    if payload is None:
        logger.warning("动态问答降级：模型输出不是合法 JSON（前 120 字：%r）", result.text[:120])
        return _with_questions(build_insufficient_response(), code), SOURCE_INSUFFICIENT

    validated, notes = validate_dynamic_payload(
        payload, candidates, question=question, stock_code=code
    )

    for note in notes:
        logger.info("动态回答校验：%s", note)

    validated["suggested_questions"] = suggested_questions_for(question, stock_code=code)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "动态回答 %s %.0fms claims=%d signals=%d evidence=%d 候选=%d",
        code,
        elapsed_ms,
        len(validated["claims"]),
        len(validated["signals"]),
        len(validated["evidence"]),
        len(candidates),
    )
    if validated["answer"].strip() == INSUFFICIENT_ANSWER:
        return validated, SOURCE_INSUFFICIENT
    return validated, SOURCE_DYNAMIC


def _with_questions(payload: dict[str, Any], stock_code: str) -> dict[str, Any]:
    """给兜底结构补上该公司的推荐问题（四家公司的问题列表不同）。"""
    payload["suggested_questions"] = suggested_questions_for("", stock_code=stock_code)
    return payload


__all__ = [
    "SOURCE_DYNAMIC",
    "SOURCE_INSUFFICIENT",
    "SYSTEM_PROMPT",
    "build_dynamic_response",
    "build_user_prompt",
    "is_supported_company",
    "parse_llm_json",
    "suggested_questions_for",
    "validate_dynamic_payload",
]
