"""三条稳定 Demo 问题 + 响应校验器 + No Evidence, No Claim 的验收测试。

对应 BACKEND_NEXT_STEPS.md「测试与验收」的 10 条：

  1. 三条稳定问题分别返回不同 Answer
  2. 前两条有 Chart，风险问题无 Chart
  3. 每个 Claim Evidence 可追溯
  4. 每个 Signal Evidence 可追溯
  5. Chart Evidence 可追溯
  6. 未知问题返回证据不足
  7. 不存在悬空 Evidence ID
  8. source page 和 URL 完整
  9. 公司 ID 不存在时不会查询到其他公司数据
 10. 数据库不可用时返回 5xx，而不是伪造回答

⚠️ 本文件刻意**不启用 LLM_FAKE**：三条 Demo 的意义就是"不依赖大模型也能演示"，
   如果测试里靠假 LLM 兜住，就等于没测到确定性这条路径。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import db
import demo_handlers
import response_validator
from config import settings

DEMO_CODE = demo_handlers.DEMO_COMPANY_CODE  # 688583

Q_STRUCTURE = demo_handlers.QUESTION_STRUCTURE
Q_PROFITABILITY = demo_handlers.QUESTION_PROFITABILITY
Q_RISK = demo_handlers.QUESTION_RISK
Q_INSUFFICIENT = "你的员工喜欢吃水果吗？"

STABLE_QUESTIONS = (Q_STRUCTURE, Q_PROFITABILITY, Q_RISK)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_db(real_db_path) -> None:
    """确认 Demo 公司确实在这份真实库里。

    跳过（而不是伪造一个库）是刻意的：这些断言的价值就在于"引用的是真数据"，
    用临时构造的库来满足它们，等于自己给自己盖章。
    """
    if DEMO_CODE not in db.get_stocks():
        pytest.skip(f"真实库里没有 Demo 公司 {DEMO_CODE}：{settings.db_file}")


@pytest.fixture()
def client(real_db: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """走真实路由的客户端。

    显式关掉 LLM：Demo 路径不该碰大模型；若哪天回归成"要调 LLM 才有答案"，
    这里会立刻暴露（而不是被一个假 LLM 悄悄兜住）。
    """
    monkeypatch.setattr(settings, "LLM_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "LLM_FAKE", False, raising=False)
    import main

    return TestClient(main.app)


def _ask(client: TestClient, question: str, code: str = DEMO_CODE) -> dict:
    resp = client.post(f"/companies/{code}/ask", json={"question": question})
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    return resp.json()


def _all_evidence_ids(payload: dict) -> set[str]:
    return {e["id"] for e in payload["evidence"]}


def _referenced_ids(payload: dict) -> list[str]:
    refs: list[str] = []
    for key in ("claims", "signals", "charts"):
        for entry in payload[key]:
            refs.extend(entry["evidence_ids"])
    return refs


# ---------------------------------------------------------------------------
# 验收 1：三条稳定问题分别返回不同 Answer
# ---------------------------------------------------------------------------


def test_acceptance_1_three_questions_distinct_answers(client: TestClient):
    answers = {q: _ask(client, q)["answer"] for q in STABLE_QUESTIONS}
    assert len(set(answers.values())) == 3, f"三条稳定问题的 Answer 有重复：{answers}"
    for question, answer in answers.items():
        assert answer.strip(), f"{question} 的 answer 为空"
        assert answer.strip() != response_validator.INSUFFICIENT_ANSWER, (
            f"{question} 落到了兜底回答，说明确定性处理器没生效"
        )


# ---------------------------------------------------------------------------
# 验收 2：前两条有 Chart，风险问题无 Chart
# ---------------------------------------------------------------------------


def test_acceptance_2_charts_present_and_absent(client: TestClient):
    assert len(_ask(client, Q_STRUCTURE)["charts"]) >= 1, "收入结构应带图表"
    assert len(_ask(client, Q_PROFITABILITY)["charts"]) >= 1, "盈利质量应带图表"
    assert _ask(client, Q_RISK)["charts"] == [], "主要风险不应带图表"


# ---------------------------------------------------------------------------
# 验收 3/4/5：Claim / Signal / Chart 的 Evidence 全部可追溯
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", STABLE_QUESTIONS)
def test_acceptance_3_4_5_all_references_traceable(client: TestClient, question: str):
    payload = _ask(client, question)
    known = _all_evidence_ids(payload)

    assert payload["claims"], "三条稳定问题都应至少有 1 条 claim"
    assert payload["signals"], "三条稳定问题都应至少有 1 条 signal"
    assert known, "三条稳定问题都应带 evidence"

    for group, key in (("claim", "claims"), ("signal", "signals"), ("chart", "charts")):
        for entry in payload[key]:
            assert entry["evidence_ids"], f"{group} {entry['id']} 没有引用任何证据"
            dangling = [i for i in entry["evidence_ids"] if i not in known]
            assert not dangling, f"{group} {entry['id']} 引用了不存在的证据 {dangling}"


# ---------------------------------------------------------------------------
# 验收 6：未知问题返回证据不足（HTTP 200）
# ---------------------------------------------------------------------------


def test_acceptance_6_unknown_question_returns_insufficient(client: TestClient):
    payload = _ask(client, Q_INSUFFICIENT)
    assert payload["answer"] == response_validator.INSUFFICIENT_ANSWER
    for key in ("claims", "signals", "charts", "evidence"):
        assert payload[key] == [], f"兜底回答的 {key} 必须为空数组"


@pytest.mark.parametrize(
    "question",
    ["今天天气怎么样？", "你们的股价明天会涨吗？", "CEO 的星座是什么？", "随便聊聊"],
)
def test_acceptance_6b_various_unknown_questions_fall_back(
    client: TestClient, question: str
):
    payload = _ask(client, question)
    assert payload["answer"] == response_validator.INSUFFICIENT_ANSWER
    assert payload["claims"] == [] and payload["evidence"] == []


def test_unknown_question_is_http_200_not_error(client: TestClient):
    """未知问题必须是 200 + 兜底，不能当服务器错误处理。"""
    resp = client.post(f"/companies/{DEMO_CODE}/ask", json={"question": Q_INSUFFICIENT})
    assert resp.status_code == 200
    assert "error" not in resp.json()


# ---------------------------------------------------------------------------
# 验收 7：不存在悬空 Evidence ID（含反向：没有孤立证据）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", list(STABLE_QUESTIONS) + [Q_INSUFFICIENT])
def test_acceptance_7_no_dangling_and_no_orphan(client: TestClient, question: str):
    payload = _ask(client, question)
    known = _all_evidence_ids(payload)

    for rid in _referenced_ids(payload):
        assert rid in known, f"悬空引用：{rid}"

    # evidence id 不得重复
    ids = [e["id"] for e in payload["evidence"]]
    assert len(ids) == len(set(ids)), f"evidence id 重复：{ids}"

    # 反向：没有既不被引用、也不出现在响应里的孤立证据
    used = set(_referenced_ids(payload))
    orphans = [i for i in ids if i not in used]
    assert not orphans, f"存在孤立证据：{orphans}"


# ---------------------------------------------------------------------------
# 验收 8：source page 和 URL 完整
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", STABLE_QUESTIONS)
def test_acceptance_8_source_page_and_url_complete(client: TestClient, question: str):
    payload = _ask(client, question)
    assert payload["evidence"], "应有证据"
    for ev in payload["evidence"]:
        page = ev.get("source_page")
        assert isinstance(page, int) and page > 0, f"{ev['id']} 的 source_page 非法：{page!r}"
        # 页码不得等于公告总页数（那是旧实现伪造页码的特征）
        assert page < 500, f"{ev['id']} 的页码 {page} 像是把公告总页数当成了引用页"
        url = ev.get("source_url")
        assert isinstance(url, str) and url.startswith(("http://", "https://")), (
            f"{ev['id']} 的 source_url 非法：{url!r}"
        )
        assert ev.get("source_quote", "").strip(), f"{ev['id']} 缺少 source_quote"


def test_acceptance_8b_pages_match_the_real_document(real_db: None):
    """页码必须能在真实库里核到（不是抄来的、也不是猜的）。"""
    problems = demo_handlers.verify_facts()
    assert problems == [], f"引用核验未通过：{problems}"


def test_acceptance_8c_demo_pages_are_the_verified_ones(real_db: None):
    """回归保护：招股书引用是第 329 页（有人误传成 323），半年报是 8 / 43 页。"""
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_STRUCTURE)
    assert payload is not None
    pages = {e["source_page"] for e in payload["evidence"]}
    assert pages == {329}, f"招股书收入结构证据的页码应为 329，实际 {pages}"

    prof = demo_handlers.build_demo_response(DEMO_CODE, Q_PROFITABILITY)
    assert {e["source_page"] for e in prof["evidence"]} == {8}

    risk = demo_handlers.build_demo_response(DEMO_CODE, Q_RISK)
    assert {e["source_page"] for e in risk["evidence"]} == {43}


# ---------------------------------------------------------------------------
# 验收 9：公司 ID 不存在时不会查询到其他公司数据
# ---------------------------------------------------------------------------


def test_acceptance_9_unknown_company_returns_404(client: TestClient):
    resp = client.post("/companies/999999/ask", json={"question": Q_PROFITABILITY})
    assert resp.status_code == 404, f"不存在的公司应 404，实际 {resp.status_code}"
    assert "error" in resp.json()


def test_acceptance_9b_other_company_gets_own_data_not_demo_data(client: TestClient):
    """库里的**其它**公司问 Demo 问题：不得套用思看的招股书数据。

    这是"跨公司串数据"最危险的形态 —— 数据本身是真的，但属于别的公司。
    """
    others = [c for c in db.get_stocks() if c != DEMO_CODE]
    if not others:
        pytest.skip("库中只有 Demo 一家公司，无法验证跨公司隔离")
    other = others[0]

    payload = _ask(client, Q_STRUCTURE, code=other)
    # 要么命中兜底，要么给出的证据必须都属于这家公司
    if payload["evidence"]:
        for ev in payload["evidence"]:
            assert ev["document_id"] in {
                str(row["id"])
                for row in db.get_documents(other, include_superseded=True)
            }, f"公司 {other} 的回答里混入了其它公司的文档 {ev['document_id']}"
    assert payload["answer"] != demo_handlers.build_demo_response(
        DEMO_CODE, Q_STRUCTURE
    )["answer"], f"公司 {other} 不应得到思看科技的收入结构回答"


def test_acceptance_9c_stock_code_suffix_is_normalized(client: TestClient):
    """688583.SH 这种带后缀的写法要能正常作答（归一化后命中处理器）。"""
    payload = _ask(client, Q_PROFITABILITY, code=f"{DEMO_CODE}.SH")
    assert payload["answer"] != response_validator.INSUFFICIENT_ANSWER
    assert payload["claims"], "带后缀的代码应命中确定性处理器"


# ---------------------------------------------------------------------------
# 确定性：不依赖大模型
# ---------------------------------------------------------------------------


def test_demo_works_with_llm_disabled(client: TestClient):
    """三条 Demo 必须在**没有大模型**的情况下照常作答。

    这正是做确定性处理器的理由：没配 API key 时界面也能演示出内容。
    client fixture 已经关掉 LLM_ENABLED，这里再确认答案不是 LLM 产物。
    """
    for question in STABLE_QUESTIONS:
        payload = _ask(client, question)
        assert payload["answer"] != response_validator.INSUFFICIENT_ANSWER, question
        assert payload["evidence"], question


def test_demo_does_not_touch_llm_call_counter(client: TestClient):
    """回答 Demo 问题不该发起任何 LLM 调用。"""
    import llm_client

    llm_client.reset_call_count()
    _ask(client, Q_PROFITABILITY)
    assert llm_client.call_count() == 0, "确定性处理器不应调用大模型"


# ---------------------------------------------------------------------------
# 官方来源链接
# ---------------------------------------------------------------------------


def test_evidence_urls_are_real_official_links(client: TestClient):
    """三条 Demo 的证据必须指向真实可达的公告直链，而不是列表页兜底。"""
    for question in STABLE_QUESTIONS:
        payload = _ask(client, question)
        for ev in payload["evidence"]:
            url = ev["source_url"]
            assert "cninfo.com.cn" in url or url.endswith(".pdf") or url.endswith(".PDF"), (
                f"{ev['id']} 的链接不像公告直链：{url}"
            )
            # 必须真的是"深链"，不是 fallback 的检索列表页
            assert "fulltextSearch" not in url, f"{ev['id']} 用的是列表页兜底：{url}"


# ---------------------------------------------------------------------------
# 验收 10：数据库不可用时返回 5xx，而不是伪造回答
# ---------------------------------------------------------------------------


def test_acceptance_10_db_unavailable_returns_5xx(
    monkeypatch: pytest.MonkeyPatch, scratch_dir
):
    """库缺失时必须 5xx/明确错误，绝不返回编造的公司事实。

    ⚠️ 不用 pytest 的 tmp_path_factory：受限沙箱下 basetemp 目录被用过后会
       被打上受限 ACL 并锁定，mktemp 在里面 scandir 会直接 PermissionError。
       改用工作区内的 scratch 目录。
    """
    import main

    missing = scratch_dir / "definitely-missing" / "cninfo.db"
    assert not missing.exists()

    monkeypatch.setattr(settings, "DB_PATH", str(missing), raising=False)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(missing), raising=False)
    monkeypatch.setattr(settings, "DB_PATH_STRICT", True, raising=False)

    client = TestClient(main.app, raise_server_exceptions=False)
    resp = client.post(f"/companies/{DEMO_CODE}/ask", json={"question": Q_PROFITABILITY})

    assert resp.status_code >= 500, f"库不可用应返回 5xx，实际 {resp.status_code}"
    body = resp.json()
    assert "error" in body, f"错误响应应含 error 字段，实际 {body}"
    # 不得把兜底文案当正常回答返回（那会掩盖"库挂了"）
    assert Q_PROFITABILITY not in json.dumps(body, ensure_ascii=False)


def test_database_path_env_alias_is_used(monkeypatch: pytest.MonkeyPatch):
    """BACKEND_NEXT_STEPS.md 规定用 DATABASE_PATH 配置库位置，必须真的生效。"""
    from config import Settings

    # 本目录的 conftest 设了 DATABASE_PATH 环境变量；构造 Settings 前先摘掉，
    # 否则它会被当成"显式传入"从而盖住构造参数（pydantic-settings 的优先级）。
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)

    target = "D:/somewhere/else/cninfo.db"
    fresh = Settings(DATABASE_PATH=target, DB_PATH="data/cninfo.db")
    assert fresh.DATABASE_PATH == target
    assert fresh.DB_PATH == target, "DATABASE_PATH 应优先于 DB_PATH"

    # 未显式给 DATABASE_PATH 时，回落 DB_PATH（向后兼容既有 .env）
    fallback = Settings(DB_PATH="data/legacy.db")
    assert fallback.DATABASE_PATH == "data/legacy.db"


# ---------------------------------------------------------------------------
# 响应校验器本身
# ---------------------------------------------------------------------------


def test_validator_passes_all_four_demo_responses():
    for question in STABLE_QUESTIONS + (Q_INSUFFICIENT,):
        payload = demo_handlers.build_demo_response(DEMO_CODE, question) or (
            demo_handlers.build_insufficient_response()
        )
        result = response_validator.validate_response(payload)
        assert result.ok, f"{question} 校验失败：{result.errors}"


def test_validator_detects_dangling_evidence():
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_PROFITABILITY)
    payload["claims"][0]["evidence_ids"] = ["EV-DOES-NOT-EXIST"]
    result = response_validator.validate_response(payload)
    assert not result.ok
    assert any("悬空" in e or "DANGLING" in e or "不存在" in e for e in result.errors)


def test_validator_detects_claim_without_evidence():
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_PROFITABILITY)
    payload["claims"][0]["evidence_ids"] = []
    result = response_validator.validate_response(payload)
    assert not result.ok, "无证据的 claim 必须被判非法"


def test_validator_detects_bad_page_and_url():
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_PROFITABILITY)
    payload["evidence"][0]["source_page"] = 0
    payload["evidence"][1]["source_url"] = "not-a-url"
    result = response_validator.validate_response(payload)
    assert not result.ok
    joined = " ".join(result.errors)
    assert "source_page" in joined
    assert "source_url" in joined


def test_validator_detects_chart_length_mismatch():
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_STRUCTURE)
    payload["charts"][0]["series"][0]["values"] = [1.0, 2.0]
    result = response_validator.validate_response(payload)
    assert not result.ok
    assert any("长度" in e or "LEN" in e for e in result.errors)


def test_validator_rejects_unknown_top_level_key():
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_PROFITABILITY)
    payload["coverage"] = 0.9
    result = response_validator.validate_response(payload)
    assert not result.ok
    assert any("顶层字段" in e for e in result.errors)


def test_validator_rejects_orphan_evidence():
    payload = demo_handlers.build_demo_response(DEMO_CODE, Q_PROFITABILITY)
    payload["evidence"].append(
        {
            "id": "EV-ORPHAN",
            "category": "financial",
            "period": "2025 年上半年",
            "content": "没人引用的证据",
            "document_id": "15",
            "source_page": 8,
            "source_quote": "占位",
            "source_url": "http://example.com/x.pdf",
        }
    )
    result = response_validator.validate_response(payload)
    assert not result.ok
    assert any("孤立" in e or "ORPHAN" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 意图判定：必须与前端 mock-data.ts 的 selectResponse() 同序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (Q_STRUCTURE, "structure"),
        (Q_PROFITABILITY, "profitability"),
        (Q_RISK, "risk"),
        ("公司现金流怎么样", "profitability"),
        ("有什么需要关注的吗", "risk"),
        ("产品线有什么变化", "structure"),
        ("", None),
        ("午饭吃什么", None),
    ],
)
def test_detect_intent(question: str, expected: str | None):
    assert demo_handlers.detect_intent(question) == expected


def test_risk_keyword_does_not_beat_profitability():
    """同时含两类关键词时，必须与前端一致地优先判为盈利质量。

    前端 selectResponse() 先查「赚钱/盈利/利润/现金流」，再查「风险」。
    这里如果顺序反了，同一个问题会因走了不同路径而给出不同回答。
    """
    assert demo_handlers.detect_intent("现金流有风险吗") == "profitability"
    assert demo_handlers.detect_intent("利润下滑有什么风险") == "profitability"


# ---------------------------------------------------------------------------
# 契约细节
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", list(STABLE_QUESTIONS) + [Q_INSUFFICIENT])
def test_response_has_exactly_six_top_level_keys(client: TestClient, question: str):
    payload = _ask(client, question)
    assert list(payload.keys()) == list(response_validator.TOP_LEVEL_KEYS), (
        f"顶层键必须恰好是 {response_validator.TOP_LEVEL_KEYS}，实际 {list(payload.keys())}"
    )


def test_demo_answers_within_latency_budget(client: TestClient):
    """三条 Demo 应远快于前端 8 秒超时（目标 2 秒内）。"""
    import time

    for question in STABLE_QUESTIONS:
        started = time.perf_counter()
        _ask(client, question)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"{question} 用了 {elapsed:.2f}s，超过 2s 预算"


def test_source_header_reports_demo_handler(client: TestClient):
    resp = client.post(f"/companies/{DEMO_CODE}/ask", json={"question": Q_RISK})
    assert resp.headers.get("X-XRay-Cache") == "demo-handler"


def test_samples_on_disk_match_live_responses(client: TestClient):
    """samples/*.json 必须与当前实现保持一致（否则前端联调会用过期样例）。"""
    mapping = {
        "sample_structure.json": Q_STRUCTURE,
        "sample_profitability.json": Q_PROFITABILITY,
        "sample_risk.json": Q_RISK,
        "sample_insufficient.json": Q_INSUFFICIENT,
    }
    samples_dir = settings.cache_dir.parent / "samples"
    if not samples_dir.is_dir():
        pytest.skip("samples/ 不存在（可运行 scripts/make_samples.py 生成）")

    for filename, question in mapping.items():
        path = samples_dir / filename
        if not path.is_file():
            pytest.skip(f"{filename} 不存在")
        saved = json.loads(path.read_text(encoding="utf-8"))
        live = _ask(client, question)
        assert saved == live, (
            f"{filename} 与当前实现不一致；请重新运行 scripts/make_samples.py"
        )
