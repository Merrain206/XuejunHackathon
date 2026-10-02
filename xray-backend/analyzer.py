"""X-Ray 企业穿透分析 · 唯一的 LLM 判断逻辑。

流程（`analyze_company`）：
    db.get_announcements(code)  →  拼 prompt  →  调 DeepSeek  →  结构化 dict
    →  当日 JSON 缓存到 cache/company_risk_{code}.json

返回结构（固定）：
    {
      "risk_level": "high" | "medium" | "low" | "unknown",
      "summary":    "一句话结论",
      "findings":   [ {id, title, type(S1-S4), severity, description} ],
      "evidence_quotes": [ {id, risk_dimension, content, source_quote,
                            source_id, source_file, source_date} ]
    }

prompt 里的硬约束（这是本项目的立身之本）：
  1. **只能依据给定公告原文**，不得引入外部事实；
  2. 每条结论必须附**原文引用**（照抄一段原文），否则不许下结论；
  3. 找不到依据就明确说「无足够信息」，**不许编造**；
  4. findings 只能归入 S1-S4 四类风险主题；
  5. 输出**严格 JSON**，不要 markdown 代码块。

缓存策略：同一家公司**同一天**不重复调用 LLM（直接读缓存）。
缓存目录 `cache/`，文件名 `company_risk_{code}.json`。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from config import settings
from db import DatabaseNotReadyError, get_announcements, get_stocks
from llm_client import LLMResult, generate_answer

logger = logging.getLogger(__name__)

#: 缓存结构版本 —— 改动返回结构时必须 +1（旧缓存自动失效）
ANALYSIS_VERSION = 1

#: 风险等级
RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"
RISK_UNKNOWN = "unknown"
ALLOWED_RISK_LEVELS = (RISK_HIGH, RISK_MEDIUM, RISK_LOW, RISK_UNKNOWN)

#: 四类风险主题（与 schemas.SignalType 一致）
ALLOWED_TYPES = ("S1", "S2", "S3", "S4")
ALLOWED_SEVERITIES = ("high", "medium", "low")

#: 无足够信息时的固定结论文案
INSUFFICIENT = "无足够信息"

#: 缓存文件名模板
CACHE_TEMPLATE = "company_risk_{code}.json"

SYSTEM_PROMPT = """你是一名严谨的上市公司公告风险分析师，服务于招股书核验场景。

【绝对硬性规则 —— 违反即视为无效输出】
1. 你**只能**依据用户提供的【公告原文】作答。不得引入任何外部知识、传闻或推测。
2. 每一条结论（finding）**必须**附带至少一条**原文引用**（evidence_quotes.source_quote），
   引用必须是从公告原文里**逐字照抄**的片段（20-80 字），不得改写、不得拼接、不得杜撰。
3. 如果公告原文里**找不到足够依据**，必须诚实地把 summary 写成「无足够信息」，
   并且 findings 与 evidence_quotes 都返回空数组 []。**绝对不许编造结论来凑数。**
4. findings 的 type 只能是以下四类之一：
   S1 利润与现金流背离（净利润增长但经营现金流下降/为负）
   S2 营收与应收账款背离（应收账款增速显著高于营收增速）
   S3 人员与经营规模背离（营收增长但社保参保人数下降）
   S4 司法与合规风险（诉讼、被执行、行政处罚等）
   不属于这四类的观察，不要写进 findings。
5. 只输出**严格 JSON**，不要 markdown 代码块，不要多余解释。

【输出格式（严格遵守）】
{
  "risk_level": "high|medium|low|unknown",
  "summary": "一句话结论（不超过 120 字）",
  "findings": [
    {"id": "F1", "title": "短标题(不超过20字)", "type": "S1|S2|S3|S4",
     "severity": "high|medium|low", "description": "依据与判断(不超过200字)",
     "evidence_ids": ["Q1"]}
  ],
  "evidence_quotes": [
    {"id": "Q1", "risk_dimension": "现金真实性|资产健康度|经营稳定性|司法风险|盈利质量|其他",
     "content": "该引用的要点归纳(不超过100字)", "source_quote": "逐字照抄的原文片段",
     "source_id": 公告id(整数), "source_file": "公告文件名", "source_date": "YYYY-MM-DD 或 null"}
  ]
}

如果无足够信息，返回：{"risk_level":"unknown","summary":"无足够信息","findings":[],"evidence_quotes":[]}
"""


# ---------------------------------------------------------------------------
# 缓存路径与读写
# ---------------------------------------------------------------------------


def cache_path(stock_code: str) -> Path:
    """缓存文件路径（cache/company_risk_{code}.json）。"""
    return settings.cache_dir / CACHE_TEMPLATE.format(code=stock_code)


def _today() -> str:
    return date.today().isoformat()


def load_cache(stock_code: str, *, allow_stale: bool = False) -> dict[str, Any] | None:
    """读缓存。

    :param allow_stale: True 时忽略"是否今天生成"的限制（ask.py 展示旧摘要用）
    :returns: 缓存 dict；不存在 / 结构版本不符 / 非当日（且不允许过期）→ None
    """
    path = cache_path(stock_code)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("缓存文件损坏，按未命中处理：%s", path)
        return None

    if not isinstance(data, dict) or data.get("version") != ANALYSIS_VERSION:
        logger.info("缓存结构版本不符，按未命中处理：%s", path)
        return None

    if not allow_stale and data.get("cache_date") != _today():
        logger.info("缓存非当日（%s），按未命中处理：%s", data.get("cache_date"), path)
        return None
    return data


def save_cache(stock_code: str, analysis: dict[str, Any]) -> Path:
    """写缓存（含 version 与 cache_date，供当日判定）。"""
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(stock_code)
    payload = {
        "version": ANALYSIS_VERSION,
        "cache_date": _today(),
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        **analysis,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def clear_cache(stock_code: str | None = None) -> int:
    """删除缓存；返回删除的文件数。"""
    cache_dir = settings.cache_dir
    if not cache_dir.is_dir():
        return 0
    pattern = CACHE_TEMPLATE.format(code=stock_code) if stock_code else "company_risk_*.json"
    removed = 0
    for path in cache_dir.glob(pattern):
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("删除缓存失败：%s", path)
    return removed


# ---------------------------------------------------------------------------
# prompt 组装
# ---------------------------------------------------------------------------


def build_prompt(stock_code: str, announcements: Sequence[dict[str, Any]],
                 *, max_chars: int | None = None) -> str:
    """把公告拼成分析 prompt。

    每条公告带 id / 文件名 / 日期，便于模型在 evidence_quotes 里回填 source_*。
    """
    limit = max_chars if max_chars is not None else settings.ANNOUNCEMENT_MAX_CHARS
    lines: list[str] = [
        f"请分析公司代码 {stock_code} 的以下公告，找出其中**有原文依据的**风险信号。",
        f"共 {len(announcements)} 条公告。",
        "",
        "【公告原文】",
    ]
    for item in announcements:
        body = (item.get("text_content") or "").strip()
        if not body:
            continue
        if limit and len(body) > limit:
            body = body[:limit]
        lines.append("")
        lines.append(
            f"--- 公告 id={item.get('id')} | 文件={item.get('file_name')} | "
            f"日期={item.get('announce_date') or '未知'} | 页数={item.get('page_count')} ---"
        )
        lines.append(body)

    lines.append("")
    lines.append(
        "请严格按 system 里给定的 JSON 格式输出。"
        "再次强调：每条 finding 必须有对应的 evidence_quotes 原文引用；"
        f"找不到依据就把 summary 写成「{INSUFFICIENT}」并让两个数组为空。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM 输出解析（容错）
# ---------------------------------------------------------------------------

#: ```json ... ``` 代码块
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json_block(text: str) -> str | None:
    """从模型输出里抠出 JSON 对象字符串。

    依次尝试：整体解析 → 去 ``` 代码块 → 取第一个 { 到最后一个 }。
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fenced = _FENCE.search(stripped)
    if fenced:
        inner = fenced.group(1).strip()
        if inner.startswith("{"):
            return inner

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return None


def _clean_str(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _clean_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def validate_analysis(raw: Any) -> dict[str, Any]:
    """把 LLM 的原始 JSON 规范化成固定结构。

    宁可丢字段也不抛异常；但**丢弃悬空引用**（finding 引用了不存在的 quote）。
    """
    if not isinstance(raw, dict):
        return _empty_analysis("模型未返回 JSON 对象")

    level = _clean_str(raw.get("risk_level"), limit=16).lower()
    if level not in ALLOWED_RISK_LEVELS:
        level = RISK_UNKNOWN

    summary = _clean_str(raw.get("summary"), limit=500) or INSUFFICIENT

    # ---- evidence_quotes：必须带 source_quote，否则不算证据 ----
    quotes: list[dict[str, Any]] = []
    seen_qids: set[str] = set()
    raw_quotes = raw.get("evidence_quotes")
    if isinstance(raw_quotes, list):
        for idx, item in enumerate(raw_quotes, start=1):
            if not isinstance(item, dict):
                continue
            quote = _clean_str(item.get("source_quote"), limit=1000)
            if not quote:
                # 没有原文引用 → 直接丢弃（这是"结论必须附引用"的兜底）
                logger.debug("丢弃无 source_quote 的证据 #%d", idx)
                continue
            qid = _clean_str(item.get("id"), limit=32) or f"Q{idx}"
            if qid in seen_qids:
                qid = f"Q{idx}"
            seen_qids.add(qid)
            quotes.append(
                {
                    "id": qid,
                    "risk_dimension": _clean_str(item.get("risk_dimension"), limit=32) or "其他",
                    "content": _clean_str(item.get("content"), limit=500) or quote[:100],
                    "source_quote": quote,
                    "source_id": _clean_int(item.get("source_id")),
                    "source_file": _clean_str(item.get("source_file"), limit=256) or None,
                    "source_date": _clean_str(item.get("source_date"), limit=32) or None,
                }
            )

    known_qids = {q["id"] for q in quotes}

    # ---- findings：type 必须是 S1-S4；悬空引用被剔除 ----
    findings: list[dict[str, Any]] = []
    raw_findings = raw.get("findings")
    if isinstance(raw_findings, list):
        for idx, item in enumerate(raw_findings, start=1):
            if not isinstance(item, dict):
                continue
            ftype = _clean_str(item.get("type"), limit=8).upper()
            if ftype not in ALLOWED_TYPES:
                logger.debug("丢弃 type 非 S1-S4 的 finding：%r", item.get("type"))
                continue
            severity = _clean_str(item.get("severity"), limit=16).lower()
            if severity not in ALLOWED_SEVERITIES:
                severity = "medium"
            eids_raw = item.get("evidence_ids")
            eids = []
            if isinstance(eids_raw, list):
                eids = [str(e).strip() for e in eids_raw if str(e).strip()]
            eids = [e for e in eids if e in known_qids]
            if not eids:
                # 找不到引用的 finding 不输出（拒绝无源结论）
                logger.debug("丢弃无有效引用的 finding：%r", item.get("title"))
                continue
            findings.append(
                {
                    "id": _clean_str(item.get("id"), limit=32) or f"F{idx}",
                    "title": _clean_str(item.get("title"), limit=64) or f"风险发现 {idx}",
                    "type": ftype,
                    "severity": severity,
                    "description": _clean_str(item.get("description"), limit=1000),
                    "evidence_ids": eids,
                }
            )

    # findings 与 quotes 必须自洽：无 findings 时不该留着孤立 quotes
    used = {e for f in findings for e in f["evidence_ids"]}
    quotes = [q for q in quotes if q["id"] in used]

    # 无 findings 却报 high/medium → 降级为 unknown，避免"空口定罪"
    if not findings and level in (RISK_HIGH, RISK_MEDIUM):
        logger.info("模型报了 %s 风险但没给出有效 findings，降级为 unknown", level)
        level = RISK_UNKNOWN

    return {
        "risk_level": level,
        "summary": summary,
        "findings": findings,
        "evidence_quotes": quotes,
    }


def _empty_analysis(reason: str) -> dict[str, Any]:
    return {
        "risk_level": RISK_UNKNOWN,
        "summary": f"{INSUFFICIENT}（{reason}）",
        "findings": [],
        "evidence_quotes": [],
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def analyze_company(
    stock_code: str,
    *,
    use_cache: bool = True,
    force: bool = False,
    days: int | None = -1,
    max_announcements: int | None = None,
) -> dict[str, Any]:
    """分析一家公司，返回结构化结论 dict。

    :param use_cache: 允许读当日缓存
    :param force: 忽略缓存，强制重新分析
    :param days: 公告时间窗口（天）。**默认 -1 表示取 settings.ANALYSIS_WINDOW_DAYS**；
                 传 None 表示明确不做时间过滤。
    :returns: {risk_level, summary, findings[], evidence_quotes[], ...元信息}
              **任何失败都不抛异常**，而是返回 risk_level=unknown + 说明。
    """
    code = (stock_code or "").strip()
    if not code:
        raise ValueError("stock_code 不能为空")

    window = settings.ANALYSIS_WINDOW_DAYS if days == -1 else days

    # ---- 1) 缓存 ----
    if use_cache and not force:
        cached = load_cache(code)
        if cached is not None:
            logger.info("%s 命中当日缓存，跳过 LLM 调用", code)
            return {**cached, "source": "cache", "llm_called": False}

    # ---- 2) 取公告 ----
    try:
        announcements = get_announcements(
            code,
            days=window,
            limit=max_announcements if max_announcements is not None
            else settings.ANALYSIS_MAX_ANNOUNCEMENTS,
        )
    except DatabaseNotReadyError as exc:
        logger.error("数据库不可用，无法分析 %s：%s", code, exc)
        return {**_empty_analysis("数据库不可用"), "source": "error",
                "error": str(exc).split("\n")[0], "llm_called": False}

    if not announcements:
        analysis = _empty_analysis(f"公司 {code} 在最近 {window} 天内没有公告")
        analysis["summary"] = INSUFFICIENT
        result = {**analysis, "source": "no_data", "llm_called": False}
        if use_cache:
            try:
                save_cache(code, result)
            except OSError:
                logger.warning("写缓存失败：%s", cache_path(code))
        return result

    # ---- 3) 调 LLM ----
    prompt = build_prompt(code, announcements)
    llm: LLMResult = generate_answer(
        f"分析 {code} 的公告风险",
        evidence=[],
        stock_code=code,
        prompt_override=prompt,
    )

    if not llm.ok:
        logger.warning("%s 的 LLM 调用失败（%s）", code, llm.fallback_reason)
        return {
            **_empty_analysis(f"大模型不可用：{llm.notice}"),
            "source": "llm_failed",
            "llm_called": True,
            "error": llm.fallback_reason,
        }

    block = extract_json_block(llm.text)
    if block is None:
        logger.error("%s 的模型输出里找不到 JSON：%r", code, (llm.text or "")[:200])
        return {
            **_empty_analysis("模型输出不是可解析的 JSON"),
            "source": "parse_failed",
            "llm_called": True,
            "raw": (llm.text or "")[:2000],
        }

    try:
        analysis = validate_analysis(json.loads(block))
    except ValueError as exc:
        logger.error("%s 的模型输出 JSON 非法：%s", code, exc)
        return {
            **_empty_analysis("模型输出 JSON 非法"),
            "source": "parse_failed",
            "llm_called": True,
        }

    result = {
        **analysis,
        "source": "llm",
        "llm_called": True,
        "model": llm.model,
        "announcements_used": len(announcements),
        "window_days": window,
    }

    # ---- 4) 写缓存 ----
    if use_cache:
        try:
            save_cache(code, result)
        except OSError:
            logger.warning("写缓存失败：%s", cache_path(code))
    return result


def analyze_many(
    stock_codes: Sequence[str],
    *,
    use_cache: bool = True,
    force: bool = False,
    days: int | None = None,
) -> dict[str, dict[str, Any]]:
    """批量分析；返回 {code: 结论}。任何一家失败都不影响其他家。"""
    results: dict[str, dict[str, Any]] = {}
    for index, code in enumerate(stock_codes, start=1):
        logger.info("[%d/%d] 分析 %s", index, len(stock_codes), code)
        try:
            results[code] = analyze_company(code, use_cache=use_cache, force=force, days=days)
        except Exception as exc:  # noqa: BLE001 —— 单家失败不中断整批
            logger.exception("分析 %s 时发生异常", code)
            results[code] = {**_empty_analysis(f"异常：{type(exc).__name__}"),
                             "source": "error", "llm_called": False}
    return results


def demo_stocks(limit: int = 5) -> list[str]:
    """demo 模式用的公司列表：取库里的前 N 家（按代码升序，稳定可复现）。"""
    try:
        return get_stocks()[: max(1, limit)]
    except DatabaseNotReadyError:
        return []


__all__ = [
    "ALLOWED_RISK_LEVELS",
    "ALLOWED_SEVERITIES",
    "ALLOWED_TYPES",
    "ANALYSIS_VERSION",
    "CACHE_TEMPLATE",
    "INSUFFICIENT",
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    "RISK_UNKNOWN",
    "SYSTEM_PROMPT",
    "analyze_company",
    "analyze_many",
    "build_prompt",
    "cache_path",
    "clear_cache",
    "demo_stocks",
    "extract_json_block",
    "load_cache",
    "save_cache",
    "validate_analysis",
]
