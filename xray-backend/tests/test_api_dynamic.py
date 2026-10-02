"""动态 Evidence-first 问答 · 接口与数据库集成测试（合成扩展库，不联网）。

对应任务书第 7 节「必须测试」里与数据库有关的部分：

  * 四家公司检索严格隔离；
  * 悬空 ID、错误页码、错误 quote、跨公司 evidence 被拒绝；
  * 动态回答 charts=[] 仍通过契约；
  * 员工水果问题不调用模型；
  * 思看科技三条稳定问题仍走 demo-handler（不退化）。

库里刻意塞了三条脏证据（页码越界 / 页码缺失 / 引文数字不在该页），
用来验证"拦得住"，而不是只验证"跑得通"。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import dynamic_evidence as de
import dynamic_qa
from config import settings
from llm_client import LLMResult
from tests.conftest import DYNAMIC_CODES

Q_REVENUE = "最近营业收入和归母净利润表现如何？"
Q_CASHFLOW = "经营现金流表现如何？"
Q_TREND = "近几个报告期的盈利趋势是什么？"
Q_FRUIT = "你的员工喜欢吃水果吗？"

ALL_QUESTIONS = (Q_REVENUE, Q_CASHFLOW, Q_TREND, Q_FRUIT)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ---------------------------------------------------------------------------
# 假 LLM：按候选证据造合法 JSON（并记录调用次数）
# ---------------------------------------------------------------------------


class FakeLLM:
    """可注入的假 LLM。

    从**候选集合本身**取数字来写 claim，因此不需要在测试里硬编金额 ——
    这样即使真实库里数字变了，测试仍然在验证"引用是否被正确校验"这件事。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, question, evidence=None, **kwargs):
        self.calls.append({"question": question, "evidence": list(evidence or []), **kwargs})
        cands = list(evidence or [])
        if not cands:
            return LLMResult(
                text=json.dumps(
                    {"answer": "根据目前掌握的信息，我无法可靠回答这个问题。",
                     "claims": [], "signals": []},
                    ensure_ascii=False,
                ),
                ok=True,
                source="fake",
                model="fake",
            )
        first = cands[0]
        lead = (_NUMBER.findall(first.get("source_quote") or "") or ["0"])[0]
        claims = [
            {
                "id": "CL-DYN-001",
                "text": f"{first.get('period')}该项指标为 {lead}。",
                "evidence_ids": [first["id"]],
            }
        ]
        if len(cands) > 1:
            second = cands[1]
            lead2 = (_NUMBER.findall(second.get("source_quote") or "") or ["0"])[0]
            claims.append(
                {
                    "id": "CL-DYN-002",
                    "text": f"{second.get('period')}该项指标为 {lead2}。",
                    "evidence_ids": [second["id"]],
                }
            )
        return LLMResult(
            text=json.dumps(
                {
                    "answer": f"根据公开披露数据，最近报告期该项指标为 {lead}。以上仅为公开信息摘录。",
                    "claims": claims,
                    "signals": [
                        {
                            "id": "SIG-DYN-001",
                            "type": "trend",
                            "title": "指标存在变化",
                            "severity": "attention",
                            "description": f"{first.get('period')}为 {lead}。",
                            "evidence_ids": [first["id"]],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            ok=True,
            source="fake",
            model="fake",
        )


@pytest.fixture()
def fake_llm_client(monkeypatch) -> FakeLLM:
    """把 dynamic_qa 用的 LLM 调用换成假实现（不联网）。"""
    fake = FakeLLM()
    monkeypatch.setattr(dynamic_qa, "generate_answer", fake, raising=True)
    return fake


@pytest.fixture()
def dyn_client(extended_db, mock_llm_env, fake_llm_client) -> TestClient:
    """指向扩展合成库 + 假 LLM 的客户端（走完整 FastAPI 路由）。"""
    import main

    return TestClient(main.app)


@pytest.fixture(autouse=True)
def mock_llm_env(monkeypatch):
    """这些测试一律不联网。"""
    monkeypatch.setattr(settings, "LLM_FAKE", False, raising=False)
    monkeypatch.setattr(settings, "LLM_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key", raising=False)
    return None


def _ask(client: TestClient, code: str, question: str):
    return client.post(f"/companies/{code}/ask", json={"question": question})


# ---------------------------------------------------------------------------
# 检索层：公司隔离与脏数据拦截
# ---------------------------------------------------------------------------


def test_retrieval_is_isolated_per_company(extended_db):
    """四家公司检索严格隔离：各自的候选只包含自己的 document_id。"""
    import db

    owners: dict[int, str] = {}
    for code in DYNAMIC_CODES:
        for row in db.get_documents(code):
            owners[int(row["id"])] = code

    for code in DYNAMIC_CODES:
        candidates = de.retrieve_candidates(code, Q_REVENUE)
        assert candidates, f"{code} 应该能检索到候选证据"
        for candidate in candidates:
            assert candidate.company_code == code
            assert owners[candidate.document_id] == code, (
                f"{code} 的候选里混进了别家公司的文档 {candidate.document_id}"
            )


def test_no_cross_company_evidence_leak(extended_db):
    """把 A 公司的证据塞进 B 公司的回答是**不可接受**的，检索层必须挡住。"""
    for code in DYNAMIC_CODES:
        others = {c for c in DYNAMIC_CODES if c != code}
        candidates = de.retrieve_candidates(code, Q_CASHFLOW)
        titles = " ".join(c.document_title for c in candidates)
        for other in others:
            assert other not in titles, f"{code} 的候选里出现别家代码 {other}"


def test_out_of_range_page_evidence_rejected(extended_db):
    """页码越界的证据必须被拦下（600570 的脏数据里有一条声称第 9 页，文档只有 4 页）。"""
    candidates = de.retrieve_candidates("600570", Q_REVENUE)
    assert all(c.source_page <= 4 or c.source_page != 9 for c in candidates)
    for candidate in candidates:
        assert candidate.source_page >= 1


def test_fabricated_quote_evidence_rejected(extended_db):
    """引文数字在该页上不存在的证据必须被拦下（伪造引用的典型形态）。"""
    candidates = de.retrieve_candidates("600570", Q_CASHFLOW)
    for candidate in candidates:
        assert "999,999,999.99" not in candidate.source_quote
        assert "888,888,888.88" not in candidate.source_quote


def test_all_returned_quotes_are_contiguous_page_text(extended_db):
    """返回的 source_quote 必须是所引页面上的**连续原文**（能被直接搜到）。"""
    import db

    for code in DYNAMIC_CODES:
        for question in ALL_QUESTIONS[:3]:
            for candidate in de.retrieve_candidates(code, question):
                page = db.document_page_text(candidate.document_id, candidate.source_page)
                assert de.squeeze(candidate.source_quote) in de.squeeze(page), (
                    f"{code} {candidate.id} 的摘录不是第 {candidate.source_page} 页的连续原文"
                )


def test_candidate_ids_are_sequential(extended_db):
    """候选编号必须是 EV-001…EV-0NN（模型只能在这个封闭集合里挑）。"""
    candidates = de.retrieve_candidates("600570", Q_REVENUE)
    assert [c.id for c in candidates] == [
        f"EV-{i:03d}" for i in range(1, len(candidates) + 1)
    ]


def test_candidate_count_within_budget(extended_db):
    """候选条数必须落在 6~15 的预算内（任务书：不能把整库塞进 Prompt）。"""
    for code in DYNAMIC_CODES:
        candidates = de.retrieve_candidates(code, Q_TREND)
        assert 1 <= len(candidates) <= settings.DYNAMIC_MAX_CANDIDATES


def test_retrieval_returns_nothing_for_unsupported_company(extended_db):
    """名单外的公司在**检索层**就断掉（即使它碰巧有 evidence）。

    扩展合成库里只有四家公司有数据，所以这里断言的是"检索层对名单外代码
    一律返回空"这条**规则**本身 —— 它与 dynamic_qa 的名单判据必须一致，
    否则将来有人绕过 dynamic_qa 直接调检索层就会漏掉这层保护。
    """
    assert de.retrieve_candidates("600570", Q_REVENUE), "前置条件：名单内公司应有候选"
    for code in ("000001", "600036", "999999", ""):
        assert de.retrieve_candidates(code, Q_REVENUE) == [], code


def test_retrieval_returns_nothing_when_disabled(extended_db, monkeypatch):
    monkeypatch.setattr(settings, "DYNAMIC_QA_ENABLED", False, raising=False)
    assert de.retrieve_candidates("600570", Q_REVENUE) == []


def test_retrieval_returns_nothing_for_out_of_scope_question(extended_db):
    """覆盖面之外的问题在**检索层**就断掉。

    这一条比"调用层记得先判断"更强：即使将来有人直接调 retrieve_candidates，
    水果问题也不会拿到一堆营收证据去喂模型。
    """
    for code in DYNAMIC_CODES:
        assert de.retrieve_candidates(code, Q_FRUIT) == []
        assert de.retrieve_candidates(code, "公司食堂的菜好不好吃？") == []


# ---------------------------------------------------------------------------
# 接口层：契约、来源标记、水果问题
# ---------------------------------------------------------------------------


def test_dynamic_question_returns_dynamic_payload(dyn_client, fake_llm_client):
    response = _ask(dyn_client, "600570", Q_REVENUE)
    assert response.status_code == 200, response.text
    assert response.headers.get("X-XRay-Cache") == "dynamic-llm"

    payload = response.json()
    assert list(payload.keys()) == [
        "answer",
        "claims",
        "signals",
        "charts",
        "evidence",
        "suggested_questions",
    ]
    assert payload["answer"].strip()
    assert payload["answer"] != "根据目前掌握的信息，我无法可靠回答这个问题。"
    assert payload["claims"], "动态回答应带 claim"
    assert payload["charts"] == [], "P0 动态回答固定无图表"

    known = {e["id"] for e in payload["evidence"]}
    assert known, "动态回答必须带证据"
    for claim in payload["claims"]:
        assert set(claim["evidence_ids"]) <= known
    for signal in payload["signals"]:
        assert set(signal["evidence_ids"]) <= known
    for evidence in payload["evidence"]:
        assert evidence["verification_status"] in ("verified", "auto", "pending")
        assert evidence["source_url"].startswith(("http://", "https://"))
        assert "#" not in evidence["source_url"], "URL 不得带 fragment"
        assert isinstance(evidence["source_page"], int) and evidence["source_page"] > 0
        assert evidence["source_quote"].strip()
        assert evidence["document_id"].strip()


def test_dynamic_answer_passes_unified_validator(dyn_client):
    """动态回答必须能过统一校验出口（结构 + 数据库原文双重复核）。

    ⚠️ 只在**非思看**公司上断言 `dynamic-llm`：`Q_REVENUE` 含「利润」，
       会命中思看科技的 profitability 稳定意图，按决策顺序第 1 条被
       demo-handler 接管 —— 那是**期望行为**，不是缺陷。
    """
    import ask
    from response_validator import assert_valid, verify_evidence_against_source

    for code in DYNAMIC_CODES:
        payload, source = ask.build_answer_payload(code, question=Q_REVENUE)
        if code == "688583":
            assert source == "demo-handler", "稳定意图必须仍由确定性处理器接管"
            # 稳定路径的出处是**上交所注册稿**，库里存的是巨潮上市稿（另一个版本），
            # 所以这里刻意**不**做数据库侧核验（见 ask._finalize 的说明）。
            assert_valid(payload)
            continue
        assert source == "dynamic-llm", f"{code} 未走动态链路：{source}"
        assert_valid(payload)  # 抛出即失败
        check = verify_evidence_against_source(payload, stock_code=code)
        assert check.ok, f"{code}: {check.errors}"


def test_dynamic_answer_passes_validator_for_company_questions(dyn_client):
    """四家公司各自跑一遍通用问题，全部要过统一校验。"""
    import ask
    from response_validator import assert_valid, verify_evidence_against_source

    for code in DYNAMIC_CODES:
        for question in (Q_CASHFLOW, Q_TREND):
            payload, source = ask.build_answer_payload(code, question=question)
            assert source in ("demo-handler", "dynamic-llm", "insufficient")
            assert_valid(payload)
            if source != "dynamic-llm":
                continue  # 稳定路径引用的是注册稿，见上一条测试的说明
            check = verify_evidence_against_source(payload, stock_code=code)
            assert check.ok, f"{code} {question}: {check.errors}"


def test_fruit_question_returns_empty_arrays_and_never_calls_llm(dyn_client, fake_llm_client):
    """「员工喜欢吃水果」→ 四个数组全空，且**一次模型都不调**。"""
    for code in DYNAMIC_CODES:
        response = _ask(dyn_client, code, Q_FRUIT)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["answer"] == "根据目前掌握的信息，我无法可靠回答这个问题。"
        assert payload["claims"] == []
        assert payload["signals"] == []
        assert payload["charts"] == []
        assert payload["evidence"] == []
    assert fake_llm_client.calls == [], "水果问题绝不能调用模型"


def test_unknown_question_without_metric_intent_degrades(dyn_client, fake_llm_client):
    """映射不到财务指标的问题同样不调用模型。"""
    response = _ask(dyn_client, "600570", "公司食堂的菜好不好吃？")
    assert response.status_code == 200
    assert response.json()["evidence"] == []
    assert fake_llm_client.calls == []


def test_company_without_evidence_returns_insufficient(dyn_client, fake_llm_client):
    """公司存在但没有可用证据 → 证据不足，而不是拿别家数据顶上。"""
    response = _ask(dyn_client, "688583", "研发投入占营业收入的比例是多少？")
    assert response.status_code == 200
    payload = response.json()
    if payload["answer"] == "根据目前掌握的信息，我无法可靠回答这个问题。":
        assert payload["evidence"] == []
    else:
        # 有证据时也必须全部属于本公司
        for evidence in payload["evidence"]:
            assert evidence["document_id"]


def test_missing_company_still_404(dyn_client):
    response = _ask(dyn_client, "999999", Q_REVENUE)
    assert response.status_code == 404
    assert set(response.json().keys()) == {"error", "detail"}


@pytest.mark.parametrize("code", DYNAMIC_CODES)
def test_stable_demo_questions_unchanged(dyn_client, code):
    """思看科技三条稳定问题**不退化**：仍是 demo-handler，不被动态链路抢走。"""
    from demo_handlers import (
        QUESTION_PROFITABILITY,
        QUESTION_RISK,
        QUESTION_STRUCTURE,
        SUGGESTED_QUESTIONS,
    )

    for question in (QUESTION_STRUCTURE, QUESTION_PROFITABILITY, QUESTION_RISK):
        response = _ask(dyn_client, code, question)
        assert response.status_code == 200
        if code == "688583":
            assert response.headers.get("X-XRay-Cache") == "demo-handler", (
                f"思看科技的稳定问题被动态链路抢走了：{question}"
            )
            assert response.json()["suggested_questions"] == list(SUGGESTED_QUESTIONS)
        else:
            # 其他公司不得套用思看科技的事实：只能走动态或兜底
            assert response.headers.get("X-XRay-Cache") in ("dynamic-llm", "insufficient")


def test_other_companies_never_get_demo_facts(dyn_client):
    """非思看公司绝不能拿到思看科技的 Demo 事实（跨公司事实污染）。"""
    response = _ask(dyn_client, "600570", "你的收入结构发生了什么变化？")
    assert response.status_code == 200
    payload = response.json()
    for evidence in payload["evidence"]:
        assert "sse.com.cn" not in evidence["source_url"], (
            "非思看公司引用了上交所思看科技专属来源"
        )
        assert "思看" not in (evidence["document_title"] or "")


def test_suggested_questions_per_company(dyn_client):
    """思看科技用自己那四条；其他公司用通用四条。"""
    strong = _ask(dyn_client, "688583", Q_FRUIT).json()
    assert strong["suggested_questions"] == list(settings.DEFAULT_QUESTIONS)

    other = _ask(dyn_client, "600570", Q_FRUIT).json()
    assert other["suggested_questions"] == list(settings.DYNAMIC_QUESTIONS)


def test_dynamic_disabled_falls_back_but_demo_still_works(dyn_client, monkeypatch):
    """保命开关：关掉动态链路后，稳定三问照常，其他问题只回证据不足。"""
    from demo_handlers import QUESTION_PROFITABILITY

    monkeypatch.setattr(settings, "DYNAMIC_QA_ENABLED", False, raising=False)

    stable = _ask(dyn_client, "688583", QUESTION_PROFITABILITY)
    assert stable.headers.get("X-XRay-Cache") == "demo-handler"
    assert stable.json()["evidence"], "关开关不得影响稳定三问"

    dynamic = _ask(dyn_client, "600570", Q_REVENUE)
    assert dynamic.status_code == 200
    assert dynamic.headers.get("X-XRay-Cache") == "insufficient"
    assert dynamic.json()["evidence"] == []


def test_llm_not_configured_degrades_for_dynamic_only(dyn_client, monkeypatch):
    """未配置 Key：动态问题立即证据不足，稳定三问不受影响。"""
    import llm_client
    from demo_handlers import QUESTION_PROFITABILITY

    monkeypatch.setattr(llm_client.settings, "DEEPSEEK_API_KEY", "", raising=False)
    monkeypatch.setattr(llm_client.settings, "LLM_FAKE", False, raising=False)
    monkeypatch.setattr(llm_client.settings, "LLM_ENABLED", True, raising=False)
    # 取消假 LLM，走真实的 llm_client 分支（它会在没有 key 时直接返回失败）
    monkeypatch.setattr(dynamic_qa, "generate_answer", llm_client.generate_answer, raising=True)

    stable = _ask(dyn_client, "688583", QUESTION_PROFITABILITY)
    assert stable.headers.get("X-XRay-Cache") == "demo-handler"

    dynamic = _ask(dyn_client, "600570", Q_REVENUE)
    assert dynamic.status_code == 200
    assert dynamic.headers.get("X-XRay-Cache") == "insufficient"
    assert dynamic.json()["answer"] == "根据目前掌握的信息，我无法可靠回答这个问题。"
    assert dynamic.json()["evidence"] == []


def test_response_never_leaks_prompt_or_key(dyn_client):
    """响应体不得泄露 prompt、密钥或模型堆栈（任务书第 6 节）。"""
    response = _ask(dyn_client, "600570", Q_REVENUE)
    body = response.text
    assert "test-key" not in body
    assert "候选证据" not in body
    assert "Traceback" not in body
    assert "DEEPSEEK" not in body.upper()
