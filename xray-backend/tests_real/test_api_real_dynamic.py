"""动态 Evidence-first 问答 · **真实 cninfo.db 验收测试**。

对应 `docs/TONIGHT_BACKEND_TASKS.md` 第 7 节「真实库验收」：至少对每家公司执行

    最近营业收入和归母净利润表现如何？
    经营现金流表现如何？
    你的员工喜欢吃水果吗？

验收标准：
  * 前两问有证据时返回**公司自己的** Evidence；没有证据时明确拒答；
  * 水果问题四个数组为空；
  * 所有返回的 `source_quote` 能在 `cninfo.db` 对应页核到；
  * 所有 URL 为**裸地址**（不带 fragment）。

⚠️ 本目录的 conftest **刻意关掉 LLM**（`DEEPSEEK_API_KEY=""`）：真实库验收验证的是
   「检索 + 机械核验 + 契约」这条确定性链路，不该因为某天模型抽风而红。
   但动态回答需要模型 —— 所以这里用一个**只看候选证据就能写出合法 JSON** 的假 LLM，
   它等价于"一个完全听话的模型"，用来回答一个关键问题：
   **把模型换成理想的，这套检索与校验链路能不能跑通？**
   真实模型的冒烟另走 `scripts/smoke_dynamic.py`（受控、不放进自动测试）。
"""

from __future__ import annotations

import json
import re

import pytest

import db
import dynamic_evidence as de
import dynamic_qa
from config import settings
from llm_client import LLMResult

#: 四家公司（产品负责人已确认的名单）
CODES = ("688583", "600570", "000066", "300558")

Q_REVENUE = "最近营业收入和归母净利润表现如何？"
Q_CASHFLOW = "经营现金流表现如何？"
Q_FRUIT = "你的员工喜欢吃水果吗？"
DYNAMIC_QUESTIONS = (Q_REVENUE, Q_CASHFLOW)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def companies(real_db_path) -> tuple[str, ...]:
    """确认四家公司都在真实库里；缺了直接 skip（而不是伪造数据）。"""
    present = set(db.get_stocks())
    missing = [code for code in CODES if code not in present]
    if missing:
        pytest.skip(f"真实库里缺少公司 {missing}；现有：{sorted(present)}")
    return CODES


@pytest.fixture()
def ideal_llm(monkeypatch):
    """「一个完全听话的模型」：按候选证据写出合法 JSON。

    它引用的数字**全部来自候选摘录**，因此能验证"检索→校验→契约"这条链路；
    模型本身的能力/风格不在本文件的断言范围内。
    """
    calls: list[str] = []

    def _llm(question, evidence=None, **kwargs):
        calls.append(question)
        cands = list(evidence or [])
        if not cands:
            return LLMResult(
                text=json.dumps(
                    {
                        "answer": "根据目前掌握的信息，我无法可靠回答这个问题。",
                        "claims": [],
                        "signals": [],
                    },
                    ensure_ascii=False,
                ),
                ok=True,
                source="fake",
                model="ideal",
            )
        claims = []
        for index, item in enumerate(cands[:3], start=1):
            lead = (_NUMBER.findall(item.get("source_quote") or "") or [""])[0]
            if not lead:
                continue
            claims.append(
                {
                    "id": f"CL-DYN-{index:03d}",
                    "text": f"{item.get('period')}该项指标为 {lead}。",
                    "evidence_ids": [item["id"]],
                }
            )
        if not claims:
            return LLMResult(
                text=json.dumps(
                    {
                        "answer": "根据目前掌握的信息，我无法可靠回答这个问题。",
                        "claims": [],
                        "signals": [],
                    },
                    ensure_ascii=False,
                ),
                ok=True,
                source="fake",
                model="ideal",
            )
        signals = [
            {
                "id": "SIG-DYN-001",
                "type": "trend",
                "title": "指标存在变化",
                "severity": "attention",
                "description": f"最近报告期该项指标为 "
                f"{(_NUMBER.findall(cands[0].get('source_quote') or '') or [''])[0]}。",
                "evidence_ids": [claims[0]["evidence_ids"][0]],
            }
        ]
        return LLMResult(
            text=json.dumps(
                {
                    "answer": "根据公开披露数据，最近报告期该项指标见所引用证据。"
                    "以上仅为公开信息摘录，不构成投资建议。",
                    "claims": claims,
                    "signals": signals,
                },
                ensure_ascii=False,
            ),
            ok=True,
            source="fake",
            model="ideal",
        )

    monkeypatch.setattr(dynamic_qa, "generate_answer", _llm, raising=True)
    return calls


# ---------------------------------------------------------------------------
# 检索层：真实数据上的硬约束
# ---------------------------------------------------------------------------


def test_candidates_are_company_isolated_on_real_db(companies):
    """四家公司严格隔离：候选里的文档必须真属于该公司。"""
    for code in companies:
        owned = {int(row["id"]) for row in db.get_documents(code, include_superseded=True)}
        for question in DYNAMIC_QUESTIONS:
            candidates = de.retrieve_candidates(code, question)
            assert candidates, f"{code} / {question} 应该能检索到候选证据"
            for candidate in candidates:
                assert candidate.company_code == code
                assert candidate.document_id in owned, (
                    f"{code} 的候选 {candidate.id} 引用了别家公司的文档 {candidate.document_id}"
                )


@pytest.mark.parametrize("code", CODES)
def test_candidate_quotes_verify_against_the_real_pages(companies, code: str):
    """★ 核心验收：每条候选的 source_quote 都能在**所引页**核到。"""
    for question in DYNAMIC_QUESTIONS:
        for candidate in de.retrieve_candidates(code, question):
            page = db.document_page_text(candidate.document_id, candidate.source_page)
            assert page.strip(), (
                f"{code} {candidate.id} 的第 {candidate.source_page} 页取不到原文"
            )
            assert de.squeeze(candidate.source_quote) in de.squeeze(page), (
                f"{code} {candidate.id} 的摘录不是第 {candidate.source_page} 页的连续原文："
                f"{candidate.source_quote[:80]!r}"
            )


@pytest.mark.parametrize("code", CODES)
def test_candidate_pages_are_within_document_bounds(companies, code: str):
    for question in DYNAMIC_QUESTIONS:
        for candidate in de.retrieve_candidates(code, question):
            meta = db.document_pages_meta([candidate.document_id])[candidate.document_id]
            assert candidate.source_page >= 1
            assert candidate.source_page <= int(meta["page_count"]), (
                f"{code} {candidate.id} 页码越界：{candidate.source_page}/{meta['page_count']}"
            )


@pytest.mark.parametrize("code", CODES)
def test_candidate_urls_are_bare_http(companies, code: str):
    """所有 URL 必须是合法 http(s) 且**不带 fragment**（页码由前端追加）。"""
    for question in DYNAMIC_QUESTIONS:
        for candidate in de.retrieve_candidates(code, question):
            assert candidate.source_url.startswith(("http://", "https://")), candidate.source_url
            assert "#" not in candidate.source_url, candidate.source_url


@pytest.mark.parametrize("code", CODES)
def test_candidate_codes_are_auto_or_verified(companies, code: str):
    """候选的核验状态只能是 verified / auto；**绝不返回 pending**。"""
    for question in DYNAMIC_QUESTIONS:
        for candidate in de.retrieve_candidates(code, question):
            assert candidate.verification_status in ("verified", "auto")


def test_candidate_budget_respected_on_real_db(companies):
    """候选条数必须落在配置的 6~15 预算内。"""
    for code in companies:
        for question in (Q_REVENUE, Q_CASHFLOW, "近几个报告期的盈利趋势是什么？"):
            candidates = de.retrieve_candidates(code, question)
            assert 1 <= len(candidates) <= settings.DYNAMIC_MAX_CANDIDATES


def test_fruit_question_yields_no_candidates_on_real_db(companies):
    """水果问题在任何一家公司都不得产出候选（否则就有被模型"回答"的风险）。"""
    assert not de.is_in_scope(Q_FRUIT)
    for code in companies:
        assert de.retrieve_candidates(code, Q_FRUIT) == [], code


# ---------------------------------------------------------------------------
# 端到端：走 ask.build_answer_payload（含统一校验出口）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_dynamic_answer_end_to_end_on_real_db(companies, ideal_llm, code: str):
    """★ 验收：动态问题在四家公司上端到端返回合法契约。"""
    import ask
    from response_validator import assert_valid, verify_evidence_against_source

    payload, source = ask.build_answer_payload(code, question=Q_REVENUE)
    assert source in ("dynamic-llm", "demo-handler", "insufficient"), source
    assert_valid(payload)  # 结构 + 引用完整性

    if source != "dynamic-llm":
        # 走到稳定处理器或证据不足都是允许的结果，但必须干净
        assert payload["claims"] == [] or code == "688583"
        return

    assert payload["charts"] == [], "P0 动态回答固定无图表"
    assert payload["claims"], "动态回答应带 claim"
    assert payload["evidence"], "动态回答必须带证据"

    # 每条引用都指向本次响应内的证据
    known = {e["id"] for e in payload["evidence"]}
    for group in ("claims", "signals"):
        for entry in payload[group]:
            assert set(entry["evidence_ids"]) <= known, f"{entry['id']} 引用悬空"

    # ★ 数据库侧复核：引文真在所引页面上、document_id 真属于本公司
    check = verify_evidence_against_source(payload, stock_code=code)
    assert check.ok, check.errors

    # 公司归属：证据必须是本公司的公告
    for evidence in payload["evidence"]:
        meta = db.document_pages_meta([int(evidence["document_id"])])[
            int(evidence["document_id"])
        ]
        assert meta["company_code"] == code, (
            f"{code} 的回答里出现了 {meta['company_code']} 的公告"
        )


@pytest.mark.parametrize("code", CODES)
def test_fruit_question_returns_empty_arrays_on_real_db(companies, ideal_llm, code: str):
    """★ 验收：水果问题四个数组全空，且一次模型都没调。"""
    import ask

    payload, source = ask.build_answer_payload(code, question=Q_FRUIT)
    assert source == "insufficient"
    assert payload["answer"] == "根据目前掌握的信息，我无法可靠回答这个问题。"
    for key in ("claims", "signals", "charts", "evidence"):
        assert payload[key] == [], f"{code} 的 {key} 必须为空数组"
    assert ideal_llm == [], "水果问题绝不能调用模型"


@pytest.mark.parametrize("code", CODES)
def test_stable_three_questions_do_not_regress(companies, code: str):
    """思看科技三条稳定问题不退化；其他公司不得套用思看科技的事实。"""
    import ask
    from demo_handlers import (
        QUESTION_PROFITABILITY,
        QUESTION_RISK,
        QUESTION_STRUCTURE,
        SUGGESTED_QUESTIONS,
    )

    for question in (QUESTION_STRUCTURE, QUESTION_PROFITABILITY, QUESTION_RISK):
        payload, source = ask.build_answer_payload(code, question=question)
        if code == "688583":
            assert source == "demo-handler", f"稳定问题被动态链路抢走：{question}"
            assert payload["evidence"], f"{question} 必须有已核验证据"
            assert payload["suggested_questions"] == list(SUGGESTED_QUESTIONS)
            for evidence in payload["evidence"]:
                assert "sse.com.cn" in evidence["source_url"]
                assert "#" not in evidence["source_url"]
        else:
            assert source != "demo-handler", (
                f"{code} 不该命中思看科技的确定性处理器：{question}"
            )
            for evidence in payload.get("evidence") or []:
                assert "思看" not in (evidence.get("document_title") or "")


@pytest.mark.parametrize("code", CODES)
def test_dynamic_questions_never_claim_when_no_evidence(companies, monkeypatch, code: str):
    """把 LLM 换成"没有证据也硬编"的坏模型：必须被机械校验拦下。"""
    import ask

    def _liar(question, evidence=None, **kwargs):
        return LLMResult(
            text=json.dumps(
                {
                    "answer": "公司业绩大幅增长，前景非常好。",
                    "claims": [
                        {"id": "CL-1", "text": "业绩大幅增长", "evidence_ids": ["EV-999"]}
                    ],
                    "signals": [
                        {"id": "S1", "type": "trend", "title": "增长", "severity": "positive",
                         "description": "大幅增长", "evidence_ids": ["EV-999"]}
                    ],
                },
                ensure_ascii=False,
            ),
            ok=True,
            source="fake",
            model="liar",
        )

    monkeypatch.setattr(dynamic_qa, "generate_answer", _liar, raising=True)
    payload, source = ask.build_answer_payload(code, question=Q_REVENUE)
    if source == "insufficient":
        assert payload["claims"] == []
        assert payload["evidence"] == []
    else:
        # 即使走了动态链路，也不允许出现悬空引用
        known = {e["id"] for e in payload["evidence"]}
        for group in ("claims", "signals"):
            for entry in payload[group]:
                assert set(entry["evidence_ids"]) <= known


@pytest.mark.parametrize("code", CODES)
def test_llm_unavailable_degrades_cleanly_on_real_db(companies, monkeypatch, code: str):
    """未配置 Key / 超时：动态问题必须干净降级，稳定三问不受影响。"""
    import ask
    from demo_handlers import QUESTION_PROFITABILITY

    def _down(*args, **kwargs):
        return LLMResult(text="", ok=False, source="fallback", fallback_reason="no_api_key")

    monkeypatch.setattr(dynamic_qa, "generate_answer", _down, raising=True)

    payload, source = ask.build_answer_payload(code, question=Q_CASHFLOW)
    if code == "688583":
        # 经营现金流会命中 profitability 稳定意图 → 仍走确定性回答
        assert source == "demo-handler"
        assert payload["evidence"]
    else:
        assert source == "insufficient"
        assert payload["answer"] == "根据目前掌握的信息，我无法可靠回答这个问题。"
        assert payload["evidence"] == []

    stable, stable_source = ask.build_answer_payload(code, question=QUESTION_PROFITABILITY)
    if code == "688583":
        assert stable_source == "demo-handler"
        assert stable["evidence"], "关掉 LLM 不得影响稳定三问"


def test_retrieval_toggle_off_on_real_db(real_db_path, monkeypatch):
    """保命开关：关掉动态链路，检索层直接返回空。"""
    monkeypatch.setattr(settings, "DYNAMIC_QA_ENABLED", False, raising=False)
    for code in CODES:
        assert de.retrieve_candidates(code, Q_REVENUE) == []


# ---------------------------------------------------------------------------
# 新库（2026-10-02 版）带来的两个能力，必须固化住
# ---------------------------------------------------------------------------


def test_evidence_documents_have_direct_pdf_links(companies):
    """★ 动态证据的 `source_url` 应当是**文档自己的 PDF 直链**。

    旧库里 300558 / 600570 的 `docs.source_url` 全为空，只能回退到巨潮公告**列表页**
    （前端判为低可靠度来源）。新库补齐了四家公司的直链，这条测试防止它再退化：
    退回列表页虽然仍能过契约，但会静默降低演示可信度。
    """
    for code in companies:
        candidates = de.retrieve_candidates(code, Q_REVENUE)
        assert candidates, code
        direct = [
            c for c in candidates if "static.cninfo.com.cn" in c.source_url
        ]
        assert direct, (
            f"{code} 的候选没有任何 PDF 直链，全部退回了列表页："
            f"{[c.source_url for c in candidates][:2]}"
        )
        for candidate in candidates:
            assert candidate.source_url.endswith(".PDF") or candidate.source_url.endswith(
                ".pdf"
            ), f"{code} {candidate.id} 的链接不是 PDF：{candidate.source_url}"


def test_manually_verified_evidence_is_usable(companies):
    """★ `review_status='verified'` 的手工核验证据必须能通过机械核验。

    新库里有 3 条 688583 的风险因素证据是 `method='manual'`、`review_status='verified'`，
    而且**通篇没有数字**（风险因素本就是定性表述）。早期实现要求"引文必须含数字"，
    会把这三条人工核验过的成果全部拒绝 —— 那等于把数据库同学的核对工作扔掉。
    现在它们走"整串连续子串"这条**更强**的口径。
    """
    found = 0
    for code in companies:
        for row in db.get_evidence(code):
            if str(row.get("review_status")) != "verified":
                continue
            found += 1
            quote = str(row["source_quote"] or "")
            page = db.document_page_text(int(row["document_id"]), int(row["source_page"]))
            assert de.has_verifiable_content(quote), f"{code} id={row['id']} 被判为不可核验"
            ok, reason, _ = de.verify_quote_on_page(quote, page)
            assert ok, f"{code} id={row['id']} 核验失败：{reason}"

    if found == 0:
        pytest.skip("当前库里没有 review_status='verified' 的证据")


def test_risk_category_evidence_maps_to_a_frontend_safe_category():
    """`category='risk'` 必须映射成前端接受的三个值之一（不能改前端契约）。

    前端 `api.ts` 的 `isEvidence()` 只接受 financial / business / company；
    给 `risk` 会让**整包响应**被判非法 → 静默降级 mock。
    """
    from schemas import to_evidence_category

    assert to_evidence_category("risk") == "business"
    assert to_evidence_category("financial") == "financial"
    assert to_evidence_category("business") == "business"
    assert to_evidence_category("company") == "company"


def test_risk_questions_use_company_owned_financial_evidence(companies, ideal_llm):
    """风险问题可总结财务 Evidence 信号，但必须保持公司隔离与引用完整。"""
    risk_question = "公司的主要风险因素有哪些？"
    import ask
    from response_validator import assert_valid, verify_evidence_against_source

    for code in companies:
        payload, source = ask.build_answer_payload(code, question=risk_question)
        assert_valid(payload)
        if source == "demo-handler":
            # 思看科技的稳定风险回答：必须是它自己那条已核验链路
            assert code == "688583"
            assert payload["evidence"]
            continue
        assert source == "dynamic-llm", f"{code} 风险问题未进入动态链路：{source}"
        assert payload["claims"]
        assert payload["evidence"]
        assert payload["charts"] == []

        known = {evidence["id"] for evidence in payload["evidence"]}
        for group in ("claims", "signals"):
            for entry in payload[group]:
                assert set(entry["evidence_ids"]) <= known

        check = verify_evidence_against_source(payload, stock_code=code)
        assert check.ok, check.errors
        for evidence in payload["evidence"]:
            document_id = int(evidence["document_id"])
            meta = db.document_pages_meta([document_id])[document_id]
            assert meta["company_code"] == code


# ---------------------------------------------------------------------------
# 交付样例：四家公司各一份动态响应
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_dynamic_sample_files_exist_and_are_verifiable(companies, code: str):
    """四家公司至少各有一份动态响应样例，且样例里的引用**回到真实库可核**。

    为什么这样校验而不是重跑生成脚本：样例由**确定性假 LLM**生成（见
    `scripts/make_samples.py --dynamic`），重跑需要注入同一个假模型；
    这里改为校验"样例的落盘内容是否仍然自洽"——
    引文能在所引页面上核到、URL 是裸地址、无悬空引用。
    这恰好是样例失效时最先坏掉的属性。
    """
    import json

    samples_dir = settings.cache_dir.parent / "samples"
    if not samples_dir.is_dir():
        pytest.skip("samples/ 不存在（可运行 scripts/make_samples.py --dynamic 生成）")

    for suffix in ("revenue", "cashflow", "fruit"):
        path = samples_dir / f"dynamic_{code}_{suffix}.json"
        if not path.is_file():
            pytest.skip(f"{path.name} 不存在；请重新生成样例")
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert list(payload.keys()) == [
            "answer",
            "claims",
            "signals",
            "charts",
            "evidence",
            "suggested_questions",
        ], path.name
        # 思看科技的问题会命中稳定意图 → 由 demo_handler 作答（带图表，那是预期行为）。
        # 只有走动态链路的公司才要求 charts == []。
        if code != "688583":
            assert payload["charts"] == [], f"{path.name} 的动态回答不应带图表"

        known = {e["id"] for e in payload["evidence"]}
        for group in ("claims", "signals"):
            for entry in payload[group]:
                assert set(entry["evidence_ids"]) <= known, f"{path.name} 存在悬空引用"

        if suffix == "fruit":
            assert payload["answer"] == "根据目前掌握的信息，我无法可靠回答这个问题。"
            assert payload["evidence"] == []
            continue

        assert payload["evidence"], f"{path.name} 应有证据"
        for evidence in payload["evidence"]:
            document_id = int(evidence["document_id"])
            page = int(evidence["source_page"])
            assert evidence["source_url"].startswith(("http://", "https://")), path.name
            assert "#" not in evidence["source_url"], f"{path.name} 的 URL 带了 fragment"
            # verification_status 是**可选**字段：思看科技的稳定证据不带它（向后兼容），
            # 动态证据必须带，且只能是 verified / auto（pending 不会返回）。
            status = evidence.get("verification_status")
            if code == "688583" and "verification_status" not in evidence:
                status = "verified"  # 人工核验过的注册稿证据
            assert status in ("verified", "auto"), f"{path.name} {evidence['id']} 的核验状态"
            if code != "688583":
                meta = db.document_pages_meta([document_id])[document_id]
                assert meta["company_code"] == code, (
                    f"{path.name} 引用了 {meta['company_code']} 的公告"
                )
                assert page <= int(meta["page_count"]), f"{path.name} 页码越界"
                page_text = db.document_page_text(document_id, page)
                assert de.squeeze(evidence["source_quote"]) in de.squeeze(page_text), (
                    f"{path.name} 的 {evidence['id']} 引文在所引页上核不到"
                )
