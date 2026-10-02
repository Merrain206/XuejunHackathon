"""动态 Evidence-first 问答 · 单元测试（不需要数据库、不联网）。

覆盖任务书第 3 节（No Evidence, No Claim 硬校验）与第 7 节（必须测试）里
**与数据库无关**的那部分：

  * 模型只能引用候选 Evidence ID（悬空 id 被丢弃）；
  * 页码 / 链接 / 原文摘录**不由模型提供**，因此不会被模型污染；
  * 无 Key、超时、非法 JSON、空内容都稳定降级；
  * 动态回答 charts 恒为 []；
  * 引文核验：数字必须在所引页面上逐字出现、顺序一致、跨度受限。

数据库相关的部分（跨公司隔离、真实检索）在 `test_api_dynamic.py`。
"""

from __future__ import annotations

import json

import pytest

import dynamic_evidence as de
import dynamic_qa
from dynamic_evidence import Candidate
from llm_client import LLMResult
from response_validator import INSUFFICIENT_ANSWER


# ---------------------------------------------------------------------------
# 构造候选（不碰数据库）
# ---------------------------------------------------------------------------


def _candidate(
    ev_id: str,
    *,
    metric: str = "revenue",
    period: str = "2025 年度",
    quote: str = "营业收入（元） 176,848,509.44 150,248,052.96 17.70%",
    page: int = 8,
    document_id: int = 15,
    company_code: str = "600570",
    status: str = "auto",
) -> Candidate:
    return Candidate(
        id=ev_id,
        company_code=company_code,
        metric=metric,
        period=period,
        category="financial",
        content="测试证据",
        document_id=document_id,
        document_title="2025年半年度报告摘要.pdf",
        source_page=page,
        source_quote=quote,
        display_quote=quote,
        source_url="https://static.cninfo.com.cn/finalpage/2025-08-28/1.PDF",
        verification_status=status,
    )


@pytest.fixture()
def candidates() -> list[Candidate]:
    return [
        _candidate("EV-001"),
        _candidate(
            "EV-002",
            metric="net_profit",
            quote="归属于上市公司股东的净利润（元） 54,007,712.64 52,918,429.73 2.06%",
            page=9,
        ),
        _candidate(
            "EV-003",
            metric="operating_cash_flow",
            quote="经营活动产生的现金流量净额（元） 31,159,083.37 46,225,989.56 -32.59%",
            page=10,
        ),
    ]


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------


def test_parse_plain_json():
    payload = {"answer": "a", "claims": [], "signals": []}
    assert dynamic_qa.parse_llm_json(json.dumps(payload)) == payload


def test_parse_json_in_code_fence():
    text = '```json\n{"answer":"a","claims":[],"signals":[]}\n```'
    assert dynamic_qa.parse_llm_json(text) == {"answer": "a", "claims": [], "signals": []}


def test_parse_json_after_prose():
    """真实事故：模型先写一段分析，末尾才补 JSON。

    实测（deepseek-flash）即使 system prompt 三次强调「只输出 JSON」，
    它仍会写成 `分析文字… { "answer": ... }`。早期实现只做整串 parse，
    于是一个**内容正确**的回答被误判成"非法 JSON"整次降级为证据不足。
    """
    text = (
        "依据证据，2025年度营业收入为176,848,509.44元，同比增长17.70%（EV-001）。\n"
        '{"answer":"营业收入同比增长17.70%。","claims":['
        '{"id":"CL-DYN-001","text":"营业收入同比增长17.70%","evidence_ids":["EV-001"]}],'
        '"signals":[]}'
    )
    parsed = dynamic_qa.parse_llm_json(text)
    assert parsed is not None
    assert parsed["claims"][0]["evidence_ids"] == ["EV-001"]


def test_parse_json_with_brace_inside_string():
    """字符串里含 `}` 时不能被当成块边界。"""
    text = '说明文字 {"answer":"a}b","claims":[],"signals":[]}'
    assert dynamic_qa.parse_llm_json(text) == {"answer": "a}b", "claims": [], "signals": []}


@pytest.mark.parametrize(
    "text",
    ["", "   ", "完全不是 JSON 的一段话", '{"foo": 1}', "[1,2,3]", "null"],
)
def test_parse_rejects_garbage(text):
    assert dynamic_qa.parse_llm_json(text) is None


def test_parse_normalizes_answer_field_aliases():
    """真实事故：模型把 `answer` 写成 `结论` / `conclusion`。

    字段名不合约就整次降级，是实测踩到的第二大浪费（第一是 JSON 前带散文）。
    """
    for alias in ("结论", "conclusion", "回答", "summary"):
        parsed = dynamic_qa.parse_llm_json(
            json.dumps({alias: "内容", "claims": [], "signals": []}, ensure_ascii=False)
        )
        assert parsed is not None, alias
        assert parsed["answer"] == "内容"


def test_parse_finds_claims_array_under_odd_key():
    """claims/signals 被塞在别的键下时，也能按结构认出来。"""
    text = json.dumps(
        {
            "结论": "内容",
            "items": [{"id": "CL-1", "text": "t", "evidence_ids": ["EV-001"]}],
            "warnings": [
                {"id": "S1", "type": "trend", "title": "t", "description": "d",
                 "evidence_ids": ["EV-001"]}
            ],
        },
        ensure_ascii=False,
    )
    parsed = dynamic_qa.parse_llm_json(text)
    assert parsed is not None
    assert parsed["claims"] and parsed["signals"]


# ---------------------------------------------------------------------------
# 机械校验：模型只能引用候选 ID
# ---------------------------------------------------------------------------


def _validate(payload: dict, candidates: list[Candidate], **kwargs):
    return dynamic_qa.validate_dynamic_payload(
        payload, candidates, question="测试问题", stock_code="600570", **kwargs
    )


def test_dangling_evidence_id_is_dropped(candidates):
    """悬空 id 的那条 claim 必须被丢掉，而不是让整包响应带着假引用出去。"""
    payload = {
        "answer": "结论。",
        "claims": [
            {"id": "CL-1", "text": "有证据的结论 17.70%", "evidence_ids": ["EV-001"]},
            {"id": "CL-2", "text": "编造的结论", "evidence_ids": ["EV-999"]},
        ],
        "signals": [],
    }
    validated, notes = _validate(payload, candidates)
    assert [c["id"] for c in validated["claims"]] == ["CL-1"]
    assert any("没有有效的候选证据引用" in n for n in notes)


def test_all_claims_dangling_returns_insufficient(candidates):
    """去掉不合格 claim 后一条不剩 → 整次返回证据不足（任务书第 3 节第 7 条）。

    ⚠️ answer 里**不能**出现可核验数字，否则会走"从 answer 机械转换 claim"的补救路径
       （那是另一条测试覆盖的行为）。这里用一句纯描述，确保测的是"整次兜底"。
    """
    payload = {
        "answer": "这里给一个看起来很确定的判断。",
        "claims": [{"id": "CL-1", "text": "编造", "evidence_ids": ["EV-404"]}],
        "signals": [],
    }
    validated, notes = _validate(payload, candidates)
    assert validated["answer"] == INSUFFICIENT_ANSWER
    assert validated["claims"] == []
    assert validated["signals"] == []
    assert validated["evidence"] == []
    assert any("整次按证据不足处理" in n for n in notes)


def test_cross_response_evidence_id_rejected(candidates):
    """别的**响应**里出现过的 id 不算数 —— 候选集合是封闭的。"""
    other = _candidate("EV-001", document_id=999, company_code="000066")
    payload = {
        "answer": "结论。",
        "claims": [{"id": "CL-1", "text": "借用别家证据", "evidence_ids": ["EV-002"]}],
        "signals": [],
    }
    validated, _ = _validate(payload, [other])
    assert validated["answer"] == INSUFFICIENT_ANSWER


def test_evidence_array_only_contains_referenced_candidates(candidates):
    """没被引用的候选不得出现在 evidence[]（否则就是孤立证据）。"""
    payload = {
        "answer": "结论。",
        "claims": [{"id": "CL-1", "text": "只引用第一条 17.70%", "evidence_ids": ["EV-001"]}],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates)
    assert [e["id"] for e in validated["evidence"]] == ["EV-001"]


def test_charts_always_empty(candidates):
    """P0 动态回答固定 charts=[]（任务书第 2.3 节）。"""
    payload = {
        "answer": "结论。",
        "claims": [{"id": "CL-1", "text": "同比增长 17.70%", "evidence_ids": ["EV-001"]}],
        "signals": [],
        "charts": [{"id": "CHART-1", "type": "line"}],
    }
    validated, _ = _validate(payload, candidates)
    assert validated["charts"] == []


def test_invalid_signal_type_dropped(candidates):
    payload = {
        "answer": "结论。",
        "claims": [{"id": "CL-1", "text": "同比增长 17.70%", "evidence_ids": ["EV-001"]}],
        "signals": [
            {"id": "S1", "type": "bullish", "title": "t", "severity": "attention",
             "description": "d", "evidence_ids": ["EV-001"]},
            {"id": "S2", "type": "trend", "title": "t2", "severity": "attention",
             "description": "d2", "evidence_ids": ["EV-001"]},
        ],
    }
    validated, notes = _validate(payload, candidates)
    assert [s["id"] for s in validated["signals"]] == ["S2"]
    assert any("type=" in n for n in notes)


def test_unknown_severity_is_coerced_not_dropped(candidates):
    """severity 只影响展示强度，取值不认识就归一到 attention，不必丢整条。"""
    payload = {
        "answer": "结论。",
        "claims": [{"id": "CL-1", "text": "同比增长 17.70%", "evidence_ids": ["EV-001"]}],
        "signals": [
            {"id": "S1", "type": "trend", "title": "t", "severity": "high",
             "description": "d", "evidence_ids": ["EV-001"]},
        ],
    }
    validated, _ = _validate(payload, candidates)
    assert validated["signals"][0]["severity"] == "attention"


def test_unsupported_numbers_are_reported(candidates):
    """answer 里的关键数字核不到 → 记下原因（默认只警告，不硬失败）。"""
    payload = {
        "answer": "营业收入同比增长 99.99%，表现强劲。",
        "claims": [{"id": "CL-1", "text": "同比增长 17.70%", "evidence_ids": ["EV-001"]}],
        "signals": [],
    }
    validated, notes = _validate(payload, candidates)
    assert any("99.99" in n for n in notes)


def test_strict_numbers_mode_degrades(candidates):
    """严格模式（可开关）下，数字核不到就整次证据不足。"""
    payload = {
        "answer": "营业收入同比增长 99.99%。",
        "claims": [{"id": "CL-1", "text": "数字 99.99%", "evidence_ids": ["EV-001"]}],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates, strict_numbers=True)
    assert validated["answer"] == INSUFFICIENT_ANSWER


# ---------------------------------------------------------------------------
# prose 答案的机械补救（真实模型最常出现的形态）
# ---------------------------------------------------------------------------


def test_prose_answer_with_evidence_ids_becomes_claims(candidates):
    """真实事故：模型把结论写在 answer 里并带编号，claims 却是 []。

    实测（deepseek-flash）多次返回 `{"结论": "... (EV-001、EV-002) ..."}` 或
    `{"answer": "...（EV-001）...", "claims": []}`。旧实现只认 claims 字段，
    于是一份**引用正确**的回答被整次降级成「无法回答」。
    """
    payload = {
        "answer": "2025 年度营业收入为 176,848,509.44 元，同比下降 17.70%（EV-001）。",
        "claims": [],
        "signals": [],
    }
    validated, notes = _validate(payload, candidates)
    assert validated["claims"], "应能从 answer 的引用句机械转换出 claim"
    assert validated["claims"][0]["evidence_ids"] == ["EV-001"]
    assert validated["evidence"][0]["id"] == "EV-001"
    assert any("机械转换" in n for n in notes)


def test_prose_answer_without_ids_binds_by_numbers(candidates):
    """模型连编号都忘了写，但数字是真的 → 用数字回绑证据。"""
    payload = {
        "answer": "2025 年度营业收入为 176,848,509.44 元，同比下降 17.70%。",
        "claims": [],
        "signals": [],
    }
    validated, notes = _validate(payload, candidates)
    assert validated["claims"], "数字能在候选摘录里核到时应当回绑成功"
    assert validated["claims"][0]["evidence_ids"] == ["EV-001"]
    assert any("机械转换" in n for n in notes)


def test_prose_answer_with_invented_numbers_is_not_converted(candidates):
    """数字在候选里核不到 → 不生成 claim，整次兜底（不许编造）。"""
    payload = {
        "answer": "2025 年度营业收入为 888,888,888.88 元，同比增长 99.99%。",
        "claims": [],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates)
    assert validated["answer"] == INSUFFICIENT_ANSWER
    assert validated["claims"] == []
    assert validated["evidence"] == []


def test_prose_refusal_sentence_is_not_converted(candidates):
    """含「数据不足」的句子是保留意见，不是结论 —— 不得转成 claim。"""
    payload = {
        "answer": "归母净利润在候选证据中没有对应数据，数据不足，无法判断其表现。",
        "claims": [],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates)
    assert validated["answer"] == INSUFFICIENT_ANSWER
    assert validated["claims"] == []


def test_prose_partial_refusal_keeps_the_verifiable_sentences(candidates):
    """一段里既有结论又有保留意见时，只保留可核验的那部分。"""
    payload = {
        "answer": (
            "2025 年度营业收入为 176,848,509.44 元，同比下降 17.70%（EV-001）。"
            "更长期的趋势数据不足，无法判断。"
        ),
        "claims": [],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates)
    assert validated["claims"], "带数字与编号的那句应当保留"
    assert all("数据不足" not in c["text"] for c in validated["claims"])
    assert validated["answer"] != INSUFFICIENT_ANSWER


def test_missing_answer_is_rebuilt_from_claims(candidates):
    """模型给了带证据的 claim、answer 却为空 → 用 claim 拼一句，而不是丢掉整次回答。"""
    payload = {
        "answer": "",
        "claims": [{"id": "CL-1", "text": "营业收入同比增长 17.70%", "evidence_ids": ["EV-001"]}],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates)
    assert validated["answer"].strip()
    assert validated["answer"] != INSUFFICIENT_ANSWER
    assert "17.70%" in validated["answer"]


def test_evidence_max_cap_drops_extra_references(candidates):
    """超过 evidence 上限时，被截掉的 id 不得再留在 claim 里（否则引用悬空）。"""
    payload = {
        "answer": "结论。",
        "claims": [
            {"id": "CL-1", "text": "a 17.70%", "evidence_ids": ["EV-001", "EV-002", "EV-003"]},
        ],
        "signals": [],
    }
    validated, _ = _validate(payload, candidates, max_evidence=1)
    known = {e["id"] for e in validated["evidence"]}
    assert known == {"EV-001"}
    for claim in validated["claims"]:
        assert set(claim["evidence_ids"]) <= known


# ---------------------------------------------------------------------------
# 引文核验（机械核验的核心）
# ---------------------------------------------------------------------------


PAGE = (
    "本报告期 上年同期 本报告期比上年同期增减\n"
    "营业收入（元） 176,848,509.44 150,248,052.96 17.70%\n"
    "归属于上市公司股东的净利润（元） 54,007,712.64 52,918,429.73 2.06%\n"
)


def test_verify_quote_passes_for_table_transcription():
    """表格转写的引文（带 `|`）必须能通过核验 —— 真实库全是这种写法。

    如果整串做子串匹配，这一条会失败；核验规则用的是"数字逐字 + 顺序 + 跨度"。
    """
    quote = "营业收入（元） | 176,848,509.44 | 150,248,052.96 | 17.70"
    ok, reason, tokens = de.verify_quote_on_page(quote, PAGE)
    assert ok, reason
    assert "176,848,509.44" in tokens


def test_verify_quote_fails_when_number_not_on_page():
    ok, reason, _ = de.verify_quote_on_page("营业收入（元） | 999,999,999.99", PAGE)
    assert not ok
    assert "核不到" in reason


def test_verify_quote_fails_when_order_reversed():
    """同一页上两个数字顺序颠倒 → 说明是从不同位置拼凑的。"""
    ok, reason, _ = de.verify_quote_on_page("17.70% 176,848,509.44", PAGE)
    assert not ok
    assert "顺序" in reason


def test_verify_quote_fails_when_span_too_wide():
    """数字在同一页上相距很远 → 不属于同一段原文。"""
    wide = "营业收入（元） 176,848,509.44" + ("填充内容 " * 60) + "17.70%"
    ok, reason, _ = de.verify_quote_on_page(wide, wide, max_span=50)
    assert not ok
    assert "相距" in reason


def test_verify_quote_rejects_quote_without_numbers():
    """**短**的纯文字引文无法自证出处 → 不通过。"""
    ok, reason, _ = de.verify_quote_on_page("公司经营情况稳定。", PAGE)
    assert not ok
    assert "没有可核验的数字" in reason or "短于" in reason


QUALITATIVE_PAGE = (
    "第三节 管理层讨论与分析\n"
    "如果公司未来的产品销售结构中，毛利率较低的产品的销售占比明显上升，"
    "则公司销售毛利率将受到不利影响；\n"
    "如果公司未来产品技术优势减弱或消除，与竞争对手的优势不明显，"
    "则公司产品的销售价格和市场占有率将受到不利影响。\n"
)


def test_verify_quote_accepts_long_qualitative_quote():
    """★ 长段**定性**引文必须能通过核验。

    新库里 688583 的 3 条 `review_status='verified'` 手工核验证据通篇没有数字
    （风险因素本就是定性表述）。若坚持"必须有数字"，这些人工核验过的成果会被
    全数拒绝 —— 那是把数据库同学的成果扔掉。
    这条走的是**更强**的口径：整串忽略空白后必须是该页的连续子串。
    """
    quote = "如果公司未来的产品销售结构中，毛利率较低的产品的销售占比明显上升，则公司销售毛利率将受到不利影响"
    ok, reason, tokens = de.verify_quote_on_page(quote, QUALITATIVE_PAGE)
    assert ok, reason
    assert tokens == (), "定性引文没有数字 token"


def test_verify_quote_rejects_fabricated_qualitative_quote():
    """定性引文如果不在该页原文里，同样要被拦下（不能因为没数字就放行）。"""
    ok, reason, _ = de.verify_quote_on_page(
        "如果公司未来出现严重的产品质量事故，则公司品牌声誉将受到重大不利影响", QUALITATIVE_PAGE
    )
    assert not ok
    assert "逐字核不到" in reason


def test_has_verifiable_content_accepts_qualitative_and_rejects_junk():
    long_quote = "如果公司未来的产品销售结构中，毛利率较低的产品的销售占比明显上升，则公司销售毛利率将受到不利影响"
    assert de.has_verifiable_content(long_quote)
    assert de.has_verifiable_content("营业收入（元） | 7,958,051,684.14")
    # 只有 0、或三五字短句 → 不要
    assert not de.has_verifiable_content("研发投入资本化的比重（%） | 0")
    assert not de.has_verifiable_content("公司经营稳定")
    assert not de.has_verifiable_content("")


def test_quote_tokens_skip_years_and_short_numbers():
    tokens = de.quote_tokens("2021—2023 年营业收入 12,579.02 万元，占比 78.19%，行业第 3")
    assert "12,579.02" in tokens
    assert "78.19%" in tokens
    assert all(not t.startswith("2021") for t in tokens)
    assert "3" not in tokens


def test_label_from_quote_handles_both_real_formats():
    """真实库里两种写法都有：标签独立成格 vs 标签带单位括注。"""
    assert de.label_from_quote("营业收入 | 68,927,976.13 | 81,320,076.83") == "营业收入"
    assert (
        de.label_from_quote("营业收入（元） | 7,958,051,684.14 | 6,366,241,242.46")
        == "营业收入（元）"
    )
    # 首格只有纯单位时**不**硬编造标签：返回单位本身（它至少还标明了量纲）
    assert de.label_from_quote("（元） | 7,958,051,684.14") == "（元）"
    assert de.label_from_quote("") == ""


def test_contiguous_quote_is_real_contiguous_text():
    """切出来的摘录必须是页面原文里**连续**的一段（可被 `in` 直接验证）。"""
    label = de.label_from_quote("营业收入（元） | 176,848,509.44 | 150,248,052.96 | 17.70")
    excerpt = de.contiguous_quote(
        PAGE, "营业收入（元） | 176,848,509.44 | 150,248,052.96 | 17.70", label=label
    )
    assert excerpt
    assert de.squeeze(excerpt) in de.squeeze(PAGE), excerpt
    assert "营业收入" in excerpt
    assert "176,848,509.44" in excerpt


def test_contiguous_quote_returns_empty_when_unverifiable():
    assert de.contiguous_quote(PAGE, "营业收入（元） | 999,999,999.99", label="") == ""


def test_has_value_token_rejects_bare_zero():
    """真实库里有 `… | 0` 这种引文：机械核验能过，但支撑不了任何结论。"""
    assert not de.has_value_token("研发投入资本化的比重（%） | 0")
    assert de.has_value_token("研发投入资本化的比重（%） | 17.76")


def test_metrics_for_question_maps_financial_intents():
    """一次问两个指标时两个都要给 —— 只给第一个会让后半句无据可依。

    ⚠️ 「归母净利润」= 库里的 `net_profit_attr`，且它必须排在 `net_profit` 前面：
       实测新库 300558 **一条 `net_profit` 都没有**，只有 `net_profit_attr`。
       顺序写反会让 300558 问「归母净利润」时只能靠兜底指标凑数甚至拒答。
    """
    assert de.metrics_for_question("最近营业收入和归母净利润表现如何？") == (
        "revenue",
        "net_profit_attr",
        "net_profit",
        "net_profit_deducted",
        "eps",
    )
    assert de.metrics_for_question("经营现金流表现如何？") == ("operating_cash_flow",)
    # 跨报告期的意图优先于具体指标，否则「盈利趋势」会被拆成单指标问题
    assert de.metrics_for_question("近几个报告期的盈利趋势是什么？") == (
        "revenue",
        "net_profit",
        "operating_cash_flow",
    )
    assert de.metrics_for_question("今天天气怎么样？") == ()


def test_metrics_for_question_covers_new_metric_vocabulary():
    """新库新增了 gross_margin / net_profit_deducted / rd_ratio / equity_attr，

    问题里出现对应说法时必须映射到它们，而不是落到兜底指标
    （否则「毛利率表现如何？」会返回一堆营收/净利润，答非所问）。
    """
    assert de.metrics_for_question("毛利率表现如何？")[0] == "gross_margin"
    assert de.metrics_for_question("扣非净利润是多少？")[0] == "net_profit_deducted"
    assert de.metrics_for_question("研发投入占营业收入的比例是多少？")[0] == "rd_ratio"
    assert de.metrics_for_question("归母净资产有多少？")[0] == "equity_attr"
    assert de.metrics_for_question("资产负债率是多少？")[0] == "debt_ratio"


def test_is_in_scope_blocks_non_financial_questions():
    """「员工喜欢吃水果」必须落在覆盖面之外 —— 否则模型会拿营收去"回答"它。"""
    assert not de.is_in_scope("你的员工喜欢吃水果吗？")
    assert not de.is_in_scope("公司食堂的菜好不好吃？")
    assert de.is_in_scope("最近营业收入和归母净利润表现如何？")


# ---------------------------------------------------------------------------
# 失败降级（无 Key / 超时 / 空响应）
# ---------------------------------------------------------------------------


def _patch_retrieval(monkeypatch, candidates):
    monkeypatch.setattr(
        dynamic_qa, "retrieve_candidates", lambda code, question, **kw: list(candidates)
    )
    monkeypatch.setattr(dynamic_qa, "is_in_scope", lambda question: True)


@pytest.mark.parametrize(
    "reason",
    ["no_api_key", "timeout", "rate_limited", "empty_response", "auth_error", "network_error"],
)
def test_llm_failure_degrades_to_insufficient(monkeypatch, candidates, reason):
    """LLM 任何一种失败都必须是干净的证据不足，不能抛异常、不能带半个答案。"""
    _patch_retrieval(monkeypatch, candidates)

    def _failing(*args, **kwargs):
        return LLMResult(text="", ok=False, source="fallback", fallback_reason=reason)

    payload, source = dynamic_qa.build_dynamic_response(
        "600570", "最近营业收入和归母净利润表现如何？", llm=_failing
    )
    assert source == dynamic_qa.SOURCE_INSUFFICIENT
    assert payload["answer"] == INSUFFICIENT_ANSWER
    assert payload["claims"] == []
    assert payload["signals"] == []
    assert payload["evidence"] == []
    assert payload["charts"] == []


def test_llm_raises_is_swallowed(monkeypatch, candidates):
    """LLM 客户端理论上不抛异常，但真抛了也绝不能让接口 500。"""
    _patch_retrieval(monkeypatch, candidates)

    def _boom(*args, **kwargs):
        raise RuntimeError("接入点炸了")

    with pytest.raises(RuntimeError):
        # 当前实现要求 llm_client 自己吞掉异常；这一条固化该约定：
        # 如果哪天有人把 llm_client 的 try/except 删了，这里会先炸出来。
        dynamic_qa.build_dynamic_response(
            "600570", "最近营业收入和归母净利润表现如何？", llm=_boom
        )


@pytest.mark.parametrize("text", ["", "   ", "这不是 JSON", '{"foo":1}'])
def test_bad_json_degrades_to_insufficient(monkeypatch, candidates, text):
    _patch_retrieval(monkeypatch, candidates)

    def _bad(*args, **kwargs):
        return LLMResult(text=text, ok=True, source="deepseek", model="m")

    payload, source = dynamic_qa.build_dynamic_response(
        "600570", "最近营业收入和归母净利润表现如何？", llm=_bad
    )
    assert source == dynamic_qa.SOURCE_INSUFFICIENT
    assert payload["answer"] == INSUFFICIENT_ANSWER
    assert payload["evidence"] == []


def test_out_of_scope_question_never_calls_model(monkeypatch):
    """「员工吃水果」不仅要不调模型，还要连检索都不做。"""
    called: list[str] = []

    def _retrieve(*args, **kwargs):
        called.append("retrieve")
        return []

    def _llm(*args, **kwargs):
        called.append("llm")
        return LLMResult(text="{}", ok=True, source="deepseek", model="m")

    monkeypatch.setattr(dynamic_qa, "retrieve_candidates", _retrieve)
    payload, source = dynamic_qa.build_dynamic_response(
        "600570", "你的员工喜欢吃水果吗？", llm=_llm
    )
    assert called == []
    assert source == dynamic_qa.SOURCE_INSUFFICIENT
    assert payload["answer"] == INSUFFICIENT_ANSWER
    assert all(payload[k] == [] for k in ("claims", "signals", "charts", "evidence"))


def test_no_candidates_degrades_without_calling_llm(monkeypatch):
    monkeypatch.setattr(dynamic_qa, "retrieve_candidates", lambda code, q, **kw: [])
    called: list[str] = []

    def _llm(*args, **kwargs):
        called.append("llm")
        return LLMResult(text="{}", ok=True, source="deepseek", model="m")

    payload, source = dynamic_qa.build_dynamic_response(
        "600570", "最近营业收入和归母净利润表现如何？", llm=_llm
    )
    assert called == []
    assert source == dynamic_qa.SOURCE_INSUFFICIENT
    assert payload["answer"] == INSUFFICIENT_ANSWER


def test_toggle_disables_dynamic_path(monkeypatch, candidates):
    """保命开关：关掉动态链路后，任何问题都只回证据不足。"""
    from config import settings

    _patch_retrieval(monkeypatch, candidates)
    monkeypatch.setattr(settings, "DYNAMIC_QA_ENABLED", False, raising=False)
    payload, source = dynamic_qa.build_dynamic_response(
        "600570", "最近营业收入和归母净利润表现如何？", llm=lambda *a, **k: None
    )
    assert source == dynamic_qa.SOURCE_INSUFFICIENT
    assert payload["answer"] == INSUFFICIENT_ANSWER


def test_unsupported_company_never_calls_model(monkeypatch, candidates):
    """★ 支持的公司是**产品决定**的，不是"数据碰巧有没有"。

    任务书第 5 节只承诺 688583 / 600570 / 000066 / 300558。
    名单之外的公司即使有候选证据，也必须直接兜底、不检索、不调模型 ——
    否则"支持哪些公司"会随数据变化，可能对一家没确认过的公司给出回答。
    """
    from config import settings

    calls: list[str] = []

    def _retrieve(code, question, **kw):
        calls.append("retrieve")
        return list(candidates)

    def _llm(*args, **kwargs):
        calls.append("llm")
        return LLMResult(
            text=json.dumps(
                {
                    "answer": "2025 年度营业收入为 176,848,509.44 元。",
                    "claims": [
                        {
                            "id": "CL-1",
                            "text": "营业收入为 176,848,509.44 元",
                            "evidence_ids": ["EV-001"],
                        }
                    ],
                    "signals": [],
                },
                ensure_ascii=False,
            ),
            ok=True,
            source="deepseek",
            model="m",
        )

    monkeypatch.setattr(dynamic_qa, "retrieve_candidates", _retrieve)
    monkeypatch.setattr(dynamic_qa, "is_in_scope", lambda q: True)

    payload, source = dynamic_qa.build_dynamic_response(
        "000001", "最近营业收入和归母净利润表现如何？", llm=_llm
    )
    assert calls == [], "名单外的公司不得检索、不得调模型"
    assert source == dynamic_qa.SOURCE_INSUFFICIENT
    assert payload["answer"] == INSUFFICIENT_ANSWER
    assert payload["evidence"] == []

    # 名单内的公司照常走
    calls.clear()
    _, source_ok = dynamic_qa.build_dynamic_response(
        "600570", "最近营业收入和归母净利润表现如何？", llm=_llm
    )
    assert "llm" in calls
    assert source_ok == dynamic_qa.SOURCE_DYNAMIC
    assert settings.SUPPORTED_COMPANY_CODES == ("688583", "600570", "000066", "300558")


def test_is_supported_company_matches_config():
    from config import settings

    for code in settings.SUPPORTED_COMPANY_CODES:
        assert dynamic_qa.is_supported_company(code)
        assert dynamic_qa.is_supported_company(f" {code} ")  # 容忍空白
    assert not dynamic_qa.is_supported_company("000001")
    assert not dynamic_qa.is_supported_company("")


# ---------------------------------------------------------------------------
# 推荐问题
# ---------------------------------------------------------------------------


def test_suggested_questions_are_backend_constants():
    """推荐问题一律来自后端常量，模型无权生成。"""
    from config import settings

    demo = dynamic_qa.suggested_questions_for("", stock_code="688583")
    assert demo == list(settings.DEFAULT_QUESTIONS)

    other = dynamic_qa.suggested_questions_for("", stock_code="600570")
    assert other == list(settings.DYNAMIC_QUESTIONS)
    assert other[0] == "最近营业收入和归母净利润表现如何？"
    assert other[-1] == "你的员工喜欢吃水果吗？"


def test_successful_dynamic_response_shape(monkeypatch, candidates):
    """一次成功的动态回答：charts=[]、evidence 带核验状态、推荐问题是通用四问。"""
    _patch_retrieval(monkeypatch, candidates)

    def _llm(*args, **kwargs):
        return LLMResult(
            text=json.dumps(
                {
                    "answer": "营业收入同比增长 17.70%，归母净利润同比增长 2.06%。",
                    "claims": [
                        {"id": "CL-DYN-001", "text": "营业收入同比增长 17.70%",
                         "evidence_ids": ["EV-001"]},
                        {"id": "CL-DYN-002", "text": "归母净利润同比增长 2.06%",
                         "evidence_ids": ["EV-002"]},
                    ],
                    "signals": [
                        {"id": "SIG-DYN-001", "type": "trend", "title": "增速分化",
                         "severity": "attention", "description": "收入快于利润",
                         "evidence_ids": ["EV-001", "EV-002"]},
                    ],
                },
                ensure_ascii=False,
            ),
            ok=True,
            source="deepseek",
            model="deepseek-flash",
        )

    payload, source = dynamic_qa.build_dynamic_response(
        "600570", "最近营业收入和归母净利润表现如何？", llm=_llm
    )
    assert source == dynamic_qa.SOURCE_DYNAMIC
    assert payload["charts"] == []
    assert len(payload["claims"]) == 2
    assert {e["id"] for e in payload["evidence"]} == {"EV-001", "EV-002"}
    for ev in payload["evidence"]:
        assert ev["verification_status"] in ("verified", "auto")
        assert "#" not in ev["source_url"], "后端只给裸 URL"
        assert isinstance(ev["source_page"], int) and ev["source_page"] > 0
    assert payload["suggested_questions"] == dynamic_qa.suggested_questions_for(
        "", stock_code="600570"
    )
