"""接口契约测试：路径 / 6 字段 / 回答合成 / 缓存行为。"""

from __future__ import annotations

import json

import pytest

from tests.helpers import (
    CODES,
    EXPECTED_TOP_KEYS,
    FORBIDDEN_KEYS,
    MISSING_CODE,
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
    """688583 假 LLM 判为 high：应出现 2 条 finding + 2 条带原文引用的证据。"""
    payload = post_ask(analyzed, TARGET).json()

    assert payload["claims"], "high 风险公司应有 claims"
    assert payload["signals"], "high 风险公司应有 signals"
    assert payload["evidence"], "high 风险公司应有 evidence"
    assert payload["charts"], "high 风险公司应有 charts"

    # 内部规则 S1/S4 映射到前端契约的 divergence/attention
    types = {s["type"] for s in payload["signals"]}
    assert types == {"divergence", "attention"}, f"前端 signal type 不符: {types}"
    assert all(s["severity"] in ("attention", "positive") for s in payload["signals"])
    assert all("side" not in s for s in payload["signals"]), "前端类型里没有 side"

    # 前端 isEvidence 的硬要求
    for ev in payload["evidence"]:
        assert ev["source_quote"], "每条证据必须有原文引用"
        assert ev["document_title"], "证据应带公告文件名"
        assert ev["category"] in ("financial", "business", "company"), ev["category"]
        assert isinstance(ev["source_page"], int) and ev["source_page"] > 0, ev["source_page"]
        assert str(ev["source_url"]).startswith(("http://", "https://")), ev["source_url"]
        assert ev["document_id"], "document_id 不能为空"
    for claim in payload["claims"]:
        assert claim["evidence_ids"], "每条 claim 至少 1 个 evidence_id"
        assert set(claim) == {"id", "text", "evidence_ids"}, "claim 字段须与前端类型一致"


def test_answer_includes_cached_summary_and_latest_announcement(analyzed):
    """answer = 缓存摘要 + 最新公告一行。"""
    payload = post_ask(analyzed, TARGET).json()
    answer = payload["answer"]

    assert "high" in answer or "风险" in answer, answer
    assert "最新公告" in answer, f"answer 应包含最新动态：{answer}"
    assert TARGET in answer


def test_low_or_unknown_risk_allows_empty_arrays(analyzed):
    """600036 假 LLM 回「无足够信息」：claims/evidence 允许为空数组。"""
    payload = post_ask(analyzed, "600036").json()

    assert payload["claims"] == []
    assert payload["signals"] == []
    assert payload["evidence"] == []
    assert payload["charts"] == []
    assert "无足够信息" in payload["answer"] or "未在公告中找到" in payload["answer"], payload["answer"]
    assert payload["suggested_questions"], "即便无结论也要给建议问题"
    assert_contract(payload)


def test_answer_never_empty_even_without_cache(client):
    """**明确清掉缓存**后，answer 仍必须给出可读说明并提示先跑批处理。"""
    from analyzer import clear_cache

    clear_cache(TARGET)  # 前面用例可能已写入缓存，这里必须清掉才谈得上"无缓存"
    payload = post_ask(client, TARGET).json()

    assert payload["answer"].strip()
    assert "run_night_batch" in payload["answer"], f"应提示先跑批处理：{payload['answer']}"
    # 无缓存时允许 claims/evidence 为空数组（契约已放开）
    assert payload["claims"] == []
    assert payload["evidence"] == []
    assert_contract(payload)


def test_normalizes_exchange_suffix(analyzed):
    """用户可能传 688583.SH，应被归一成库里的 688583。"""
    response = analyzed.post(
        "/companies/688583.SH/ask", json={"question": "有什么风险？"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "688583" in payload["answer"]
    assert_contract(payload)


# ---------------------------------------------------------------------------
# 缓存行为
# ---------------------------------------------------------------------------


def test_cache_hit_served_from_disk(analyzed):
    response = post_ask(analyzed, TARGET)
    assert response.headers.get("X-XRay-Cache") == "hit-cache"
    assert float(response.headers["X-XRay-Elapsed-Ms"]) >= 0


def test_stale_cache_still_usable(analyzed):
    """把缓存日期改成昨天：ask 仍应能读到（allow_stale），而不是变成无结论。"""
    from analyzer import cache_path

    path = cache_path(TARGET)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cache_date"] = "2000-01-01"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    payload = post_ask(analyzed, TARGET).json()
    assert payload["claims"], "过期缓存也应能用于展示结论"
    assert_contract(payload)


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
