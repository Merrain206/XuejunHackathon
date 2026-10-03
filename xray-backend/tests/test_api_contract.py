"""接口契约测试：路径 / 6 字段 / 回答合成 / 缓存行为。"""

from __future__ import annotations

import json

import pytest

from tests.helpers import (
    CODES,
    EXPECTED_TOP_KEYS,
    FORBIDDEN_KEYS,
    MISSING_CODE,
    UNMATCHED_QUESTION,
    assert_contract,
    post_ask,
)

TARGET = "688583"


# ---------------------------------------------------------------------------
# 路径与顶层契约
# ---------------------------------------------------------------------------


def _all_route_paths(router, _seen: set[int] | None = None) -> set[str]:
    """递归收集路由路径。

    ⚠️ FastAPI 0.115+ 把 include_router 的结果包成 _IncludedRouter
    （没有 .path / .routes，只有 .original_router），所以必须递归展开
    这些包装层，才能拿到真实路径。
    """
    if _seen is None:
        _seen = set()
    if id(router) in _seen:
        return set()
    _seen.add(id(router))

    paths: set[str] = set()
    for route in getattr(router, "routes", []):
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        for attr in ("routes", "original_router", "router"):
            child = getattr(route, attr, None)
            if child is not None and child is not route:
                paths |= _all_route_paths(child, _seen)
    return paths


def test_routes_keep_original_paths_without_api_prefix(client):
    """路径**保持不变**：/companies/{stock_code}/ask，绝不能有 /api 前缀。"""
    paths = _all_route_paths(client.app)
    assert "/companies/{stock_code}/ask" in paths, f"实际路径: {sorted(paths)}"
    assert "/companies/{stock_code}/profile" in paths
    assert "/companies/{stock_code}/signals" in paths
    assert "/admin/refresh" in paths
    assert "/health" in paths
    assert not [p for p in paths if p.startswith("/api")], f"出现 /api 前缀路由: {sorted(paths)}"

    # 不只查路由表，直接打一次接口，确认真的是通的
    live = client.get("/health")
    assert live.status_code == 200, live.text


def test_ask_response_has_exactly_six_keys_in_order(analyzed):
    response = post_ask(analyzed, TARGET)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert list(payload.keys()) == EXPECTED_TOP_KEYS, f"键或顺序不符: {list(payload.keys())}"
    assert not (FORBIDDEN_KEYS & set(payload.keys())), "响应体出现不该有的字段"
    assert_contract(payload)


def test_high_risk_company_surfaces_findings_and_quotes(analyzed):
    """缓存的 high 风险结论应能转成合法的 claims / signals / evidence。

    ⚠️ 走的是 GET /signals（专门暴露缓存结论的接口）。
       POST /ask 对非稳定问题一律返回固定兜底，不再读缓存 —— 见下面
       test_ask_unknown_question_returns_fixed_fallback。
    """
    payload = analyzed.get(f"/companies/{TARGET}/signals").json()

    assert payload["signals"], "high 风险公司应有 signals"

    # 内部规则 S1/S4 映射到前端契约的 divergence/attention
    types = {s["type"] for s in payload["signals"]}
    assert types == {"divergence", "attention"}, f"前端 signal type 不符: {types}"
    assert all(s["severity"] in ("attention", "positive") for s in payload["signals"])
    assert all("side" not in s for s in payload["signals"]), "前端类型里没有 side"
    for signal in payload["signals"]:
        assert signal["evidence_ids"], "每条 signal 至少引用 1 条证据"


def test_cached_quotes_build_valid_evidence(analyzed):
    """缓存里的 evidence_quotes 经 _build_evidence 后必须满足前端硬要求。"""
    from ask import _build_evidence
    from analyzer import load_cache
    from db import get_announcements

    analysis = load_cache(TARGET, allow_stale=True)
    assert analysis is not None, "应能读到缓存结论"
    announcements = get_announcements(TARGET, days=None, limit=None, body_chars=0)

    evidence, mapping = _build_evidence(
        analysis.get("evidence_quotes") or [], announcements, TARGET
    )

    assert evidence, "带 source_page 的引用应能生成证据"
    assert set(mapping) == {"Q1", "Q2"}, f"quote_id 映射不完整：{mapping}"
    for ev in evidence:
        assert ev["source_quote"], "每条证据必须有原文引用"
        assert ev["document_title"], "证据应带公告文件名"
        assert ev["category"] in ("financial", "business", "company"), ev["category"]
        assert isinstance(ev["source_page"], int) and ev["source_page"] > 0, ev["source_page"]
        assert str(ev["source_url"]).startswith(("http://", "https://")), ev["source_url"]
        assert ev["document_id"], "document_id 不能为空"


def test_evidence_without_source_page_is_dropped(analyzed):
    """没有可信页码的引用必须被丢弃 —— 绝不用公告总页数冒充引用页码。

    这是旧实现的一个真实缺陷：source_page 曾被写成该公告的 page_count，
    于是招股书 520 页时每条引用都声称出自第 520 页。
    """
    from ask import _build_evidence
    from db import get_announcements

    announcements = get_announcements(TARGET, days=None, limit=None, body_chars=0)
    quotes = [
        {
            "id": "Q1",
            "risk_dimension": "盈利质量",
            "content": "缺页码的引用",
            "source_quote": "某段原文",
            "source_id": 1,
            "source_file": "2023年年度报告.pdf",
            "source_date": None,
            # 刻意不给 source_page
        }
    ]
    evidence, mapping = _build_evidence(quotes, announcements, TARGET)
    assert evidence == [], "缺页码的证据必须被丢弃"
    assert mapping == {}, "被丢弃的引用不得留下映射（否则 claim 会引用悬空）"


def test_answer_includes_cached_summary_and_latest_announcement(analyzed):
    """缓存摘要仍可在 /signals 读到（answer 合成已按新契约收敛到固定兜底）。"""
    payload = analyzed.get(f"/companies/{TARGET}/signals").json()
    assert payload["signals"], "应有来自缓存的信号"
    assert payload["computed_at"], "应报出结论生成时间"
    assert payload["stock_code"] == TARGET


def test_low_or_unknown_risk_company_still_answers(analyzed):
    """600036 假 LLM 回「无足够信息」：接口仍应正常返回，不报错。"""
    payload = post_ask(analyzed, "600036").json()

    assert payload["claims"] == []
    assert payload["signals"] == []
    assert payload["evidence"] == []
    assert payload["charts"] == []
    assert payload["suggested_questions"], "即便无结论也要给建议问题"
    assert_contract(payload)


def test_ask_unknown_question_returns_fixed_fallback(client):
    """认不出意图的问题一律固定兜底，**不读缓存、不让模型补充事实**。

    ⚠️ 历史行为是"读缓存摘要 + 补一句最新公告"，该分支已按
       BACKEND_NEXT_STEPS.md 移除：否则同一个问题会因为"那天有没有跑过批处理"
       而得到不同答案，甚至把旧缓存里未核验的结论当成回答发出去。
    ⚠️ 这里必须用**不命中任何稳定意图关键词**的问题：「风险 / 关注 / 注意」
       会命中 risk 意图并被正常作答，那是期望行为，不是兜底。
    """
    from analyzer import clear_cache

    clear_cache(TARGET)
    payload = post_ask(client, TARGET, question=UNMATCHED_QUESTION).json()

    from demo_handlers import INSUFFICIENT_ANSWER

    assert payload["answer"] == INSUFFICIENT_ANSWER
    assert payload["claims"] == []
    assert payload["evidence"] == []
    assert_contract(payload)


def test_normalizes_exchange_suffix(analyzed):
    """用户可能传 688583.SH，应被归一成库里的 688583，并正常作答。

    ⚠️ 确定性处理器对三条稳定问题的**出处来自已核验来源目录**，与库里那份
       文档是不是同一个版本无关（库里是巨潮上市稿，对外引用上交所注册稿），
       所以合成库上也会正常返回 demo-handler。这里同时验证"没有 404、契约合法"。
    """
    response = analyzed.post(
        "/companies/688583.SH/ask",
        json={"question": "你的收入结构发生了什么变化？"},
    )
    assert response.status_code == 200, response.text
    assert response.headers.get("X-XRay-Cache") == "demo-handler"
    assert_contract(response.json())


# ---------------------------------------------------------------------------
# 缓存行为
# ---------------------------------------------------------------------------


def test_demo_sources_come_from_verified_catalog_not_the_db(analyzed):
    """三条 Demo 的出处一律是**已核验的上交所 PDF**，与库里存的是哪一版无关。

    合成测试库的 docs 表连 `source_url` 列都没有（更别说 chunks），
    但注册稿/半年报的 `source_url` 来自 `verified_sources.py`，
    因此这里必须仍然给出上交所 HTTPS 地址，而不是巨潮列表页、
    也不是"因为库不完整就给兜底"。
    """
    from verified_sources import is_official_high_confidence

    response = post_ask(analyzed, TARGET, question="你最近真的赚钱吗？")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert response.headers.get("X-XRay-Cache") == "demo-handler"
    assert payload["evidence"], "确定性处理器应当给出已核验证据"
    for ev in payload["evidence"]:
        assert is_official_high_confidence(ev["source_url"]), ev["source_url"]
        assert "cninfo" not in ev["source_url"], "不得给巨潮链接（前端只认上交所为高可靠度）"
    assert_contract(payload)


def test_every_company_gets_valid_contract(analyzed):
    """任何公司、任何问题都必须返回合法契约（不能白屏、不能悬空引用）。"""
    from demo_handlers import QUESTION_PROFITABILITY

    for code in CODES:
        for question in ("你的收入结构发生了什么变化？", QUESTION_PROFITABILITY, "完全无关的问题"):
            payload = post_ask(analyzed, code, question=question).json()
            assert_contract(payload)
            assert payload["answer"].strip()


def test_stale_cache_still_usable(analyzed):
    """把缓存日期改成昨天：/signals 仍应能读到（allow_stale），而不是变成无结论。"""
    from analyzer import cache_path

    path = cache_path(TARGET)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cache_date"] = "2000-01-01"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    payload = analyzed.get(f"/companies/{TARGET}/signals").json()
    assert payload["signals"], "过期缓存也应能用于展示结论"


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------


def test_unknown_company_returns_structured_error(client):
    response = post_ask(client, MISSING_CODE)
    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == {"error", "detail"}
    assert body["error"]


def test_invalid_body_returns_structured_error(client):
    response = client.post(f"/companies/{TARGET}/ask", json={})
    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == {"error", "detail"}


# ---------------------------------------------------------------------------
# profile / signals / health / search
# ---------------------------------------------------------------------------


def test_profile_reports_announcement_coverage(analyzed):
    response = analyzed.get(f"/companies/{TARGET}/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["stock_code"] == TARGET
    assert body["announcement_count"] == 5, body["announcement_count"]
    assert body["recent_titles"]
    assert body["first_announcement_date"] and body["last_announcement_date"]
    assert body["risk_level"] == "high"
    assert body["summary"]


def test_profile_unknown_company_404(client):
    assert client.get(f"/companies/{MISSING_CODE}/profile").status_code == 404


def test_signals_endpoint_reads_cache(analyzed):
    response = analyzed.get(f"/companies/{TARGET}/signals")
    assert response.status_code == 200
    body = response.json()
    types = {s["type"] for s in body["signals"]}
    assert types == {"divergence", "attention"}, types
    assert body["computed_at"], "应带分析时间"
    assert body["source"] == "llm"


def test_health_reports_db_and_contract(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["companies"] >= 3
    assert body["capabilities"]["answer_contract"] == EXPECTED_TOP_KEYS
    assert "无调度器" in body["capabilities"]["scheduling"]
    assert "DEEPSEEK_API_KEY" not in json.dumps(body, ensure_ascii=False)


def test_search_endpoint(client):
    response = client.get("/search", params={"keyword": "营业收入"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert body["hits"]

    # 过短关键词 → 结构化 422
    bad = client.get("/search", params={"keyword": "营"})
    assert bad.status_code == 422
    assert set(bad.json().keys()) == {"error", "detail"}


def test_admin_refresh_requires_token(client, fake_llm):
    denied = client.post("/admin/refresh?stock_code=688583")
    assert denied.status_code == 401
    assert set(denied.json().keys()) == {"error", "detail"}


def test_admin_refresh_is_disabled_without_configured_token(client, fake_llm, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    response = client.post(
        "/admin/refresh",
        params={"stock_code": "688583", "token": "xray-demo-token"},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "管理接口未启用"


def test_admin_refresh_with_token_analyzes(client, fake_llm):
    from config import settings

    ok = client.post(
        "/admin/refresh",
        params={"stock_code": "688583"},
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "completed"
    assert "risk_level" in body["detail"]


@pytest.mark.parametrize("code", CODES)
def test_all_companies_answerable(analyzed, code):
    payload = post_ask(analyzed, code).json()
    assert_contract(payload)
