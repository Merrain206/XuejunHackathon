"""验证后端响应能通过**前端 api.ts 的运行时校验器**。

这是本次契约适配的验收标准：
    xuejun-hackathon/src/lib/api.ts 里的 isAskApiResponse() 只要返回 false，
    前端就会丢掉真实响应、静默降级为 mock（界面显示 DEMO FALLBACK）。
    所以必须逐字段对齐 —— 本脚本把那个校验器逐条翻译成 Python。

用法：
    python scripts/check_frontend_contract.py
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# 1) 桩掉 LLM（用真实库 + 假模型输出跑整条链路）
# ---------------------------------------------------------------------------
import analyzer  # noqa: E402
from config import settings  # noqa: E402

FAKE = {
    "risk_level": "high",
    "summary": "净利润为正但经营现金流为负，且存在诉讼，风险偏高。",
    "findings": [
        {"id": "F1", "title": "利润与现金流背离", "type": "S1", "severity": "high",
         "description": "净利润为正但经营活动现金流净额为负。", "evidence_ids": ["Q1"]},
        {"id": "F2", "title": "存在诉讼", "type": "S4", "severity": "medium",
         "description": "公司涉及买卖合同纠纷。", "evidence_ids": ["Q2"]},
    ],
    "evidence_quotes": [
        {"id": "Q1", "risk_dimension": "现金真实性", "content": "经营现金流为负",
         "source_quote": "归属于上市公司股东的净利润为正，但经营活动产生的现金流量净额为负",
         "source_id": None, "source_file": None, "source_date": None},
        {"id": "Q2", "risk_dimension": "司法风险", "content": "涉及诉讼",
         "source_quote": "公司涉及一起买卖合同纠纷", "source_id": None,
         "source_file": None, "source_date": None},
    ],
}


def fake_llm(question, evidence=None, **kw):
    from llm_client import LLMResult
    return LLMResult(text=json.dumps(FAKE, ensure_ascii=False), ok=True,
                     source="fake", model="fake", elapsed_ms=1.0)


analyzer.generate_answer = fake_llm

print(f"数据库: {settings.db_file}")
check("找到真实库", settings.db_file.is_file(), str(settings.db_file))

import importlib  # noqa: E402

import db  # noqa: E402

stocks = db.get_stocks()
if not stocks:
    print("库里没有公司，无法测试")
    sys.exit(1)
code = stocks[0]
print(f"公司: {code}\n")

# ---------------------------------------------------------------------------
# 2) 前端校验器的 Python 移植（逐条对应 api.ts）
# ---------------------------------------------------------------------------


def is_non_empty_string(v) -> bool:
    return isinstance(v, str) and len(v.strip()) > 0


def is_string_array(v) -> bool:
    return isinstance(v, list) and all(is_non_empty_string(x) for x in v)


def is_optional_string(v) -> bool:
    return v is None or isinstance(v, str)


def is_http_url(v) -> bool:
    if not is_non_empty_string(v):
        return False
    return v.startswith("http://") or v.startswith("https://")


def is_claim(v) -> bool:
    return (
        isinstance(v, dict)
        and is_non_empty_string(v.get("id"))
        and is_non_empty_string(v.get("text"))
        and is_string_array(v.get("evidence_ids"))
        and len(v.get("evidence_ids") or []) > 0
    )


def is_signal(v) -> bool:
    return (
        isinstance(v, dict)
        and is_non_empty_string(v.get("id"))
        and v.get("type") in ("divergence", "trend", "attention")
        and is_non_empty_string(v.get("title"))
        and v.get("severity") in ("attention", "positive")
        and is_non_empty_string(v.get("description"))
        and is_string_array(v.get("evidence_ids"))
        and len(v.get("evidence_ids") or []) > 0
    )


def is_chart(v) -> bool:
    if not isinstance(v, dict):
        return False
    periods, series = v.get("periods"), v.get("series")
    if not (
        is_non_empty_string(v.get("id"))
        and v.get("type") == "line"
        and is_non_empty_string(v.get("title"))
        and is_optional_string(v.get("subtitle"))
        and isinstance(v.get("unit"), str)
        and is_string_array(periods)
        and len(periods or []) > 0
        and isinstance(series, list)
        and len(series) > 0
        and is_string_array(v.get("evidence_ids"))
        and len(v.get("evidence_ids") or []) > 0
    ):
        return False
    for item in series:
        if not (isinstance(item, dict) and is_non_empty_string(item.get("name"))):
            return False
        values = item.get("values")
        if not isinstance(values, list) or len(values) != len(periods):
            return False
        for x in values:
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                return False
    return True


def is_evidence(v) -> bool:
    return (
        isinstance(v, dict)
        and is_non_empty_string(v.get("id"))
        and v.get("category") in ("financial", "business", "company")
        and is_non_empty_string(v.get("period"))
        and is_non_empty_string(v.get("content"))
        and is_optional_string(v.get("document_title"))
        and is_non_empty_string(v.get("document_id"))
        and isinstance(v.get("source_page"), int)
        and not isinstance(v.get("source_page"), bool)
        and v.get("source_page") > 0
        and is_non_empty_string(v.get("source_quote"))
        and is_http_url(v.get("source_url"))
    )


def is_ask_response(v) -> tuple[bool, str]:
    if not isinstance(v, dict):
        return False, "顶层不是对象"
    if not is_non_empty_string(v.get("answer")):
        return False, "answer 非空字符串不满足"
    for field, fn in (("claims", is_claim), ("signals", is_signal), ("evidence", is_evidence)):
        arr = v.get(field)
        if not isinstance(arr, list):
            return False, f"{field} 不是数组"
        for i, item in enumerate(arr):
            if not fn(item):
                return False, f"{field}[{i}] 校验失败：{json.dumps(item, ensure_ascii=False)[:200]}"
    charts = v.get("charts")
    if charts is not None:
        if not isinstance(charts, list):
            return False, "charts 不是数组"
        for i, item in enumerate(charts):
            if not is_chart(item):
                return False, f"charts[{i}] 校验失败：{json.dumps(item, ensure_ascii=False)[:200]}"
    sq = v.get("suggested_questions")
    if sq is not None and not is_string_array(sq):
        return False, "suggested_questions 非法"
    ids = [e["id"] for e in v["evidence"]]
    if len(set(ids)) != len(ids):
        return False, "evidence id 有重复"
    known = set(ids)
    refs = [c["evidence_ids"] for c in v["claims"]]
    refs += [s["evidence_ids"] for s in v["signals"]]
    refs += [c["evidence_ids"] for c in (charts or [])]
    for group in refs:
        for rid in group:
            if rid not in known:
                return False, f"悬空引用 {rid}"
    return True, ""


# ---------------------------------------------------------------------------
# 3) 生成真实响应
# ---------------------------------------------------------------------------
import ask  # noqa: E402

try:
    payload, source = ask.build_answer_payload(code, question="这家公司有什么风险？")
except Exception as exc:  # noqa: BLE001
    print(f"build_answer_payload 抛异常：{type(exc).__name__}: {exc}")
    raise

# ---------------------------------------------------------------------------
# 3b) 四个 Demo 响应都要过前端校验器
#
# 三个稳定问题 + 证据不足兜底，逐个走一遍真实链路（含校验器与兜底），
# 因为它们才是前端联调时实际会拿到的响应。
# ---------------------------------------------------------------------------
DEMO_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("收入结构", "你的收入结构发生了什么变化？"),
    ("盈利质量", "你最近真的赚钱吗？"),
    ("主要风险", "目前最值得关注的风险是什么？"),
    ("证据不足", "你的员工喜欢吃水果吗？"),
)

#: 三条 Demo 事实（招股书/半年报页码）只属于思看科技 688583。
#: ⚠️ 不能用 stocks[0]：库内公司按代码升序，第一家是 000066，
#:    拿它问 Demo 问题只会正确地命中兜底，从而误判成失败。
DEMO_CODE = "688583"

print()
print("=" * 74)
print("四个 Demo 响应 · 前端校验器逐包判定")
print("=" * 74)
print(f"Demo 公司: {DEMO_CODE}")
for label, question in DEMO_QUESTIONS:
    demo_payload, demo_source = ask.build_answer_payload(DEMO_CODE, question=question)
    passed, reason = is_ask_response(demo_payload)
    check(
        f"[{label}] 整包通过前端 isAskApiResponse()",
        passed,
        f"{reason} src={demo_source} claims={len(demo_payload['claims'])} "
        f"signals={len(demo_payload['signals'])} charts={len(demo_payload['charts'])} "
        f"evidence={len(demo_payload['evidence'])}",
    )
    check(f"[{label}] answer 非空", is_non_empty_string(demo_payload.get("answer")))
    for ev in demo_payload["evidence"]:
        check(
            f"[{label}] evidence {ev['id']} 页码为正整数且 URL 合法",
            isinstance(ev.get("source_page"), int)
            and ev["source_page"] > 0
            and is_http_url(ev.get("source_url")),
            f"page={ev.get('source_page')} url={str(ev.get('source_url'))[:48]}",
        )

# 三条稳定问题的 Answer 必须互不相同；且前两条要有图、风险问题无图
_demo_payloads = {
    label: ask.build_answer_payload(DEMO_CODE, question=q)[0]
    for label, q in DEMO_QUESTIONS[:3]
}
_answers = [p["answer"] for p in _demo_payloads.values()]
check("三条稳定问题的 Answer 互不相同", len(set(_answers)) == 3, f"{len(set(_answers))} 种")
check(
    "收入结构 / 盈利质量 各带 1 张图",
    len(_demo_payloads["收入结构"]["charts"]) == 1
    and len(_demo_payloads["盈利质量"]["charts"]) == 1,
    f"{len(_demo_payloads['收入结构']['charts'])} / {len(_demo_payloads['盈利质量']['charts'])}",
)
check("主要风险问题无图表", len(_demo_payloads["主要风险"]["charts"]) == 0)
check(
    "兜底回答的四个数组都为空且文案固定",
    _demo_payloads.get("证据不足") is None
    and ask.build_answer_payload(DEMO_CODE, question="你的员工喜欢吃水果吗？")[0]["answer"]
    == "根据目前掌握的信息，我无法可靠回答这个问题。",
)

print()
print("=" * 74)
print("主响应（通用链路）逐项判定")
print("=" * 74)

ok, why = is_ask_response(payload)
check("整包通过前端 isAskApiResponse()", ok, why)

check("answer 非空字符串", is_non_empty_string(payload.get("answer")))
if payload["claims"]:
    check("claims 全部合法", all(is_claim(c) for c in payload["claims"]))
    check("claim.id 非空", all(is_non_empty_string(c.get("id")) for c in payload["claims"]))
if payload["signals"]:
    check("signals 全部合法", all(is_signal(s) for s in payload["signals"]))
    check("signal.type ∈ {divergence,trend,attention}",
          all(s["type"] in ("divergence", "trend", "attention") for s in payload["signals"]),
          str([s["type"] for s in payload["signals"]]))
    check("signal.severity ∈ {attention,positive}",
          all(s["severity"] in ("attention", "positive") for s in payload["signals"]),
          str([s["severity"] for s in payload["signals"]]))
    check("signal 不再有 side 字段（前端类型里没有）",
          all("side" not in s for s in payload["signals"]))
if payload["charts"]:
    check("charts 全部合法（含 values 与 periods 等长）",
          all(is_chart(c) for c in payload["charts"]))
    check("chart.type == 'line'", all(c["type"] == "line" for c in payload["charts"]))
    check("chart 不再有 kind/points 字段（旧契约残留）",
          all("kind" not in c and "note" not in c for c in payload["charts"]))
if payload["evidence"]:
    check("evidence 全部合法", all(is_evidence(e) for e in payload["evidence"]))
    check("evidence.category ∈ {financial,business,company}",
          all(e["category"] in ("financial", "business", "company") for e in payload["evidence"]),
          str([e["category"] for e in payload["evidence"]]))
    check("evidence.source_page 全为正整数",
          all(isinstance(e["source_page"], int) and e["source_page"] > 0 for e in payload["evidence"]))
    check("evidence.source_url 全为合法 http(s) URL",
          all(is_http_url(e["source_url"]) for e in payload["evidence"]),
          str([e["source_url"] for e in payload["evidence"]][:2]))
    check("evidence.document_id 全非空",
          all(is_non_empty_string(e["document_id"]) for e in payload["evidence"]))
    check("evidence.source_quote 全非空（No Evidence, No Claim）",
          all(is_non_empty_string(e["source_quote"]) for e in payload["evidence"]))
    check("claim 不再有 verified/verification_note（前端类型里没有）",
          all("verified" not in c for c in payload["claims"]))

print()
print("=" * 74)
print(f"通过 {PASSED} 项")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("响应体与前端契约完全一致 [PASS]")
