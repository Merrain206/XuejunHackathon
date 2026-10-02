"""X-Ray 企业穿透分析 · 三条稳定 Demo 问题的**确定性意图处理器**。

为什么需要这个模块
------------------
`ask` 接口原先只读 LLM 生成的缓存（`cache/company_risk_{code}.json`）。没配 API key 时
缓存里全是 `risk_level=unknown`，于是演示时 `signals` / `charts` 全空 —— 界面能开，
但**演示不出内容**。

因此对**固定演示问题**改用确定性处理器：直接给出已人工核验的回答、图表与证据，
不依赖大模型、不依赖缓存、毫秒级返回。其余问题仍走原来的缓存 / LLM 链路。

设计红线（对应 BACKEND_NEXT_STEPS.md）
--------------------------------------
1. `No Evidence, No Claim` —— 每条 claim / signal / chart 都引用本响应内真实存在的
   evidence id；
2. **不伪造页码** —— 每个引用的页码都必须在 `facts` 里显式声明，并且启动/自检时
   用 `db.find_pages` 回到原文核验（见 `verify_facts()`）；
3. **不伪造链接** —— `source_url` 一律取库里的真实直链（docs.source_url），
   拿不到就让 `verify_facts()` 报错，而不是编一个点开就 404 的地址；
4. 证据不足时返回固定兜底文案，**不让模型补充事实**。

意图判定与前端 `mock-data.ts` 的 `selectResponse()` **完全同序**，否则同一个问题在
「后端作答」与「前端降级作答」两条路径下会得到不同答案。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 固定文案
# ---------------------------------------------------------------------------

#: 证据不足时的固定兜底文案（BACKEND_NEXT_STEPS.md 逐字规定）
INSUFFICIENT_ANSWER = "根据目前掌握的信息，我无法可靠回答这个问题。"

#: 风险类回答必须点明的免责声明（需求：必须说明是"风险提示"而非"已经发生"）
RISK_DISCLAIMER = "以上均为公司披露的风险提示，不代表相关风险已经发生。"

#: Demo 公司（当前固定为思看科技）。处理器对任何公司都可用，但三条 Demo 事实
#: 只存在于该公司的招股书/半年报里，因此按公司代码做归属校验。
DEMO_COMPANY_CODE = "688583"

#: 三条 Demo 问题的标准问法（前端 demoSuggestedQuestions 用的就是这三句）
QUESTION_STRUCTURE = "你的收入结构发生了什么变化？"
QUESTION_PROFITABILITY = "你最近真的赚钱吗？"
QUESTION_RISK = "目前最值得关注的风险是什么？"


# ---------------------------------------------------------------------------
# 已核验事实目录
#
# 每条 fact 的结构：
#   key          —— 证据 id（EV-xxx）
#   document_id  —— cninfo.db 的 docs.id（canonical 版本：superseded=0）
#   page         —— ★ PDF 查看器页码；必须与原文实际所在页一致
#   locate       —— ★ 用于回查的真实片段（可以是跨行拼接的表格文本），
#                    verify_facts() 会确认它确实出现在 document_id 的第 page 页
#   category     —— financial / business / company（前端只认这三个）
#   period       —— 期间标签
#   content      —— 证据摘要
#   quote        —— 展示给用户的原文摘录
# ---------------------------------------------------------------------------

#: 招股说明书 —— 收入结构（第 328/329 页，2021—2023 年度）
_PROSPECTUS_DOC = 299
#: 2025 年半年度报告 —— 盈利质量（第 8 页）与风险提示（第 43 页）
_HALF_YEAR_DOC = 15

_FACTS: dict[str, dict[str, Any]] = {
    # ---------------- 收入结构 ----------------
    "EV-STR-001": {
        "document_id": _PROSPECTUS_DOC,
        "page": 329,
        "category": "business",
        "period": "2021—2023",
        "content": "便携式 3D 扫描仪收入持续增长，但占主营业务收入的比例连续下降。",
        "quote": (
            "报告期内，公司便携式 3D 扫描仪的销售收入分别为 12,579.02 万元、"
            "14,189.49 万元、15,722.33 万元和 6,664.36 万元，占主营业务收入的比例分别为"
            "78.19%、68.87%、57.87%和 44.36%。"
        ),
        # 回查锚点：用第 329 页那段逐字原文（表格拼接文本换行不稳，正文最可靠）
        "locate": (
            "公司便携式 3D 扫描仪的销售收入分别为 12,579.02 万元、"
            "14,189.49 万元、15,722.33 万元和 6,664.36 万元"
        ),
    },
    "EV-STR-002": {
        "document_id": _PROSPECTUS_DOC,
        "page": 329,
        "category": "business",
        "period": "2021—2023",
        "content": "跟踪式 3D 视觉数字化产品收入和收入占比快速提升。",
        "quote": (
            "跟踪式3D视觉数字化产品 5,000.57 33.28% 7,222.66 26.58% 3,711.22 18.01% "
            "1,893.69 11.77%"
        ),
        "locate": "跟踪式3D视觉数字化 5,000.57 33.28% 7,222.66 26.58% 3,711.22 18.01% 1,893.69 11.77%",
    },
    "EV-STR-003": {
        "document_id": _PROSPECTUS_DOC,
        "page": 329,
        "category": "financial",
        "period": "2021—2023",
        "content": "公司主营业务收入规模连续增长，2023 年达 27,170.18 万元。",
        "quote": "合 计 15,024.45 100.00% 27,170.18 100.00% 20,602.47 100.00% 16,088.21 100.00%",
        "locate": "合 计 15,024.45 100.00% 27,170.18 100.00% 20,602.47 100.00% 16,088.21 100.00%",
    },
    # ---------------- 盈利质量 ----------------
    "EV-PRO-001": {
        "document_id": _HALF_YEAR_DOC,
        "page": 8,
        "category": "financial",
        "period": "2025 年上半年",
        "content": "营业收入同比增长 17.70%，归母净利润同比增长 2.06%。",
        "quote": (
            "营业收入 176,848,509.44 150,248,052.96 17.70 "
            "归属于上市公司股东的净利润 54,007,712.64 52,918,429.73 2.06"
        ),
        "locate": "营业收入 176,848,509.44 150,248,052.96 17.70",
    },
    "EV-PRO-002": {
        "document_id": _HALF_YEAR_DOC,
        "page": 8,
        "category": "financial",
        "period": "2025 年上半年",
        "content": "扣非归母净利润同比下降 2.93%，利润增速明显低于收入增速。",
        "quote": (
            "归属于上市公司股东的扣除非经常性损益的净利润 47,074,322.51 "
            "48,493,603.14 -2.93"
        ),
        "locate": "47,074,322.51",
    },
    "EV-PRO-003": {
        "document_id": _HALF_YEAR_DOC,
        "page": 8,
        "category": "financial",
        "period": "2025 年上半年",
        "content": "经营活动现金流净额同比下降 32.59%。",
        "quote": "经营活动产生的现金流量净额 31,159,083.37 46,225,989.56 -32.59",
        "locate": "31,159,083.37",
    },
    # ---------------- 主要风险（第 43 页，库中 review_status='verified'） ----------------
    "EV-RISK-001": {
        "document_id": _HALF_YEAR_DOC,
        "page": 43,
        "category": "business",
        "period": "2025 年上半年",
        "content": "产品结构变化可能对销售毛利率产生不利影响。",
        "quote": (
            "如果公司未来的产品销售结构中，毛利率较低的产品的销售占比明显上升，"
            "则公司销售毛利率将受到不利影响。"
        ),
        "locate": "毛利率较低的产品的销售占比明显上升",
    },
    "EV-RISK-002": {
        "document_id": _HALF_YEAR_DOC,
        "page": 43,
        "category": "business",
        "period": "2025 年上半年",
        "content": "技术优势减弱可能影响产品售价和市场占有率。",
        "quote": (
            "如果公司未来产品技术优势减弱或消除，与竞争对手的优势不明显，"
            "则公司产品的销售价格和市场占有率将受到不利影响。"
        ),
        "locate": "产品技术优势减弱或消除",
    },
    "EV-RISK-003": {
        "document_id": _HALF_YEAR_DOC,
        "page": 43,
        "category": "business",
        "period": "2025 年上半年",
        "content": "下游重要应用领域需求萎缩可能导致公司收入下降。",
        "quote": (
            "如果包括航空航天、汽车制造、工程机械、交通运输在内下游重要应用领域"
            "市场需求萎缩，则可能导致公司收入下降，甚至面临业绩大幅下滑的风险。"
        ),
        "locate": "下游重要应用领域市场需求萎缩",
    },
}


# ---------------------------------------------------------------------------
# 三条 Demo 回答（文案与前端 mock-data.ts 对齐，避免两条路径答案不一致）
# ---------------------------------------------------------------------------

_STRUCTURE_RESPONSE: dict[str, Any] = {
    "answer": (
        "现有证据显示，思看科技的收入结构正在从较依赖便携式 3D 扫描仪，转向更多元的"
        "三维视觉数字化产品组合。2021—2023 年，便携式扫描仪收入仍在增长，但占主营业务"
        "收入的比例由 78.19% 降至 57.87%；同期，跟踪式产品占比由 11.77% 升至 26.58%。"
        "这更像是其他产品线增长更快，而不是核心产品萎缩。"
    ),
    "claims": [
        {
            "id": "CL-STR-001",
            "text": "便携式 3D 扫描仪收入增长，但收入占比持续下降。",
            "evidence_ids": ["EV-STR-001"],
        },
        {
            "id": "CL-STR-002",
            "text": "跟踪式 3D 视觉数字化产品正在成为更重要的收入来源。",
            "evidence_ids": ["EV-STR-002"],
        },
        {
            "id": "CL-STR-003",
            "text": "同期主营业务收入总额由 16,088.21 万元增至 27,170.18 万元，收入结构变化发生在整体增长之中。",
            "evidence_ids": ["EV-STR-003"],
        },
    ],
    "signals": [
        {
            "id": "SIG-STR-001",
            "type": "divergence",
            "title": "核心产品收入增长，但依赖度下降",
            "severity": "positive",
            "description": (
                "便携式扫描仪收入向上、占比向下，说明其他产品线增长速度更快，"
                "产品结构正在多元化。"
            ),
            "evidence_ids": ["EV-STR-001", "EV-STR-002"],
        }
    ],
    "charts": [
        {
            "id": "CHART-STR-001",
            "type": "line",
            "title": "两类产品收入占比变化",
            "subtitle": "便携式扫描仪占比下降，跟踪式产品占比持续上升",
            "unit": "%",
            "periods": ["2021", "2022", "2023"],
            "series": [
                {"name": "便携式 3D 扫描仪", "values": [78.19, 68.87, 57.87]},
                {"name": "跟踪式 3D 视觉数字化产品", "values": [11.77, 18.01, 26.58]},
            ],
            "evidence_ids": ["EV-STR-001", "EV-STR-002"],
        }
    ],
    "evidence_ids": ["EV-STR-001", "EV-STR-002", "EV-STR-003"],
}

_PROFITABILITY_RESPONSE: dict[str, Any] = {
    "answer": (
        "是的，思看科技在 2025 年上半年仍然盈利：归母净利润为 5,400.77 万元，"
        "同比增长 2.06%。但盈利质量有两个值得注意的变化：营收增长 17.70%，明显快于"
        "净利润；扣非归母净利润下降 2.93%，经营现金流净额下降 32.59%。"
        "因此，更准确的结论是「仍在赚钱，但利润增速和现金回收弱于收入增长」。"
    ),
    "claims": [
        {
            "id": "CL-PRO-001",
            "text": "2025 年上半年营业收入同比增长 17.70%，归母净利润同比增长 2.06%。",
            "evidence_ids": ["EV-PRO-001"],
        },
        {
            "id": "CL-PRO-002",
            "text": "扣非归母净利润同比下降 2.93%。",
            "evidence_ids": ["EV-PRO-002"],
        },
        {
            "id": "CL-PRO-003",
            "text": "经营活动现金流净额同比下降 32.59%。",
            "evidence_ids": ["EV-PRO-003"],
        },
    ],
    "signals": [
        {
            "id": "SIG-PRO-001",
            "type": "divergence",
            "title": "收入增长，但利润和现金流增长未同步",
            "severity": "attention",
            "description": (
                "营收保持两位数增长，但净利润仅小幅增长，扣非利润与经营现金流下降，"
                "需继续观察投入转化和现金回收。"
            ),
            "evidence_ids": ["EV-PRO-001", "EV-PRO-002", "EV-PRO-003"],
        }
    ],
    "charts": [
        {
            "id": "CHART-PRO-001",
            "type": "line",
            "title": "2025年上半年关键指标同比变化",
            "subtitle": "收入增长与扣非利润、经营现金流出现分化",
            "unit": "%",
            "periods": ["营业收入", "归母净利润", "扣非净利润", "经营现金流"],
            "series": [{"name": "同比变化", "values": [17.7, 2.06, -2.93, -32.59]}],
            "evidence_ids": ["EV-PRO-001", "EV-PRO-002", "EV-PRO-003"],
        }
    ],
    "evidence_ids": ["EV-PRO-001", "EV-PRO-002", "EV-PRO-003"],
}

_RISK_RESPONSE: dict[str, Any] = {
    "answer": (
        "根据目前掌握的信息，公司没有披露已经发生、足以对生产经营构成实质影响的"
        "重大风险。更值得持续关注的是技术差异化能否维持，以及产品结构和下游需求变化"
        "是否压低毛利率或收入。公司明确提示：低毛利率产品占比上升会影响整体毛利率；"
        "技术优势减弱会影响售价和市场份额；航空航天、汽车制造等下游需求收缩可能拖累"
        "收入。" + " " + RISK_DISCLAIMER
    ),
    "claims": [
        {
            "id": "CL-RISK-001",
            "text": "低毛利率产品占比上升可能拖累整体毛利率。",
            "evidence_ids": ["EV-RISK-001"],
        },
        {
            "id": "CL-RISK-002",
            "text": "技术优势减弱可能影响售价和市场占有率。",
            "evidence_ids": ["EV-RISK-002"],
        },
        {
            "id": "CL-RISK-003",
            "text": "重要下游行业需求下降可能导致公司收入下降。",
            "evidence_ids": ["EV-RISK-003"],
        },
    ],
    "signals": [
        {
            "id": "SIG-RISK-001",
            "type": "attention",
            "title": "增长依赖持续技术领先与下游需求",
            "severity": "attention",
            "description": (
                "公司当前未披露已发生的重大经营风险，但技术差异化、产品毛利结构和"
                "下游景气度是需要继续验证的变量。"
            ),
            "evidence_ids": ["EV-RISK-001", "EV-RISK-002", "EV-RISK-003"],
        }
    ],
    # 需求明确：风险问题**无图表**
    "charts": [],
    "evidence_ids": ["EV-RISK-001", "EV-RISK-002", "EV-RISK-003"],
}

#: 意图 → 预置回答
_HANDLERS: dict[str, dict[str, Any]] = {
    "structure": _STRUCTURE_RESPONSE,
    "profitability": _PROFITABILITY_RESPONSE,
    "risk": _RISK_RESPONSE,
}


# ---------------------------------------------------------------------------
# 意图判定
# ---------------------------------------------------------------------------

#: ⚠️ 判定顺序必须与前端 mock-data.ts 的 selectResponse() 完全一致：
#: 先「赚钱/盈利/利润/现金流」，再「风险/注意/关注」，最后「收入结构/…」。
_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("profitability", ("赚钱", "盈利", "利润", "现金流")),
    ("risk", ("风险", "注意", "关注")),
    ("structure", ("收入结构", "产品线", "扫描仪", "多元")),
)


def detect_intent(question: str) -> str | None:
    """把问题归类到三条稳定意图之一；认不出返回 None（调用方走兜底）。

    判定顺序与关键词均与前端 mock 保持一致，保证「后端作答」和
    「前端降级作答」对同一个问题给出同一套回答。
    """
    text = (question or "").strip()
    if not text:
        return None
    for intent, keywords in _INTENT_RULES:
        if any(k in text for k in keywords):
            return intent
    return None


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def _evidence_item(fact_key: str, *, stock_code: str) -> dict[str, Any] | None:
    """把一条 fact 组装成响应体 evidence[] 的元素。

    页码、链接、标题全部来自**真实库**：拿不到真实直链就返回 None，
    由调用方剔除该条证据（宁可不答，也不编造出处）。
    """
    fact = _FACTS.get(fact_key)
    if fact is None:
        logger.warning("未登记的 fact：%s", fact_key)
        return None

    document_id = int(fact["document_id"])
    url = db.document_url(document_id)
    if not url:
        logger.error(
            "文档 %s 没有真实 source_url，剔除证据 %s（不伪造链接）", document_id, fact_key
        )
        return None

    title = db.document_title(document_id) or str(document_id)
    return {
        "id": fact_key,
        "category": fact["category"],
        "period": fact["period"],
        "content": fact["content"],
        "document_id": str(document_id),
        "document_title": title,
        "source_page": int(fact["page"]),
        "source_quote": fact["quote"],
        "source_url": url,
    }


def build_demo_response(stock_code: str, question: str) -> dict[str, Any] | None:
    """三条稳定问题的确定性回答。

    :returns: 完整响应 dict；问题不属于三条稳定意图、或该公司没有对应文档时返回 None
              （调用方继续走缓存 / LLM 链路）。
    """
    code = (stock_code or "").strip()
    intent = detect_intent(question)
    if intent is None:
        return None

    # 三条 Demo 事实只属于 Demo 公司；别的公司提问时不要硬套它的数据
    if code != DEMO_COMPANY_CODE:
        logger.info("问题命中 %s 意图，但公司 %s 非 Demo 公司，交给通用链路", intent, code)
        return None

    template = _HANDLERS[intent]

    evidence: list[dict[str, Any]] = []
    for key in template["evidence_ids"]:
        item = _evidence_item(key, stock_code=code)
        if item is not None:
            evidence.append(item)

    known = {e["id"] for e in evidence}
    if not known:
        logger.error("公司 %s 的 %s 意图证据全部不可用，回退到通用链路", code, intent)
        return None

    def _keep(ids: list[str]) -> list[str]:
        return [i for i in ids if i in known]

    # ⚠️ 必须 deepcopy：模板里的 claims/signals/charts 含**嵌套可变对象**
    #    （如 chart.series[].values）。浅拷贝 `{**c}` 会让嵌套列表与模块级常量
    #    共享同一份内存 —— 调用方（或某个测试）一旦改动返回值，就会永久污染
    #    预置模板，后续请求跟着出错。这个坑真实发生过：
    #    某测试把 series[0].values 改成 2 个元素，导致之后所有 structure 响应
    #    都因 "series 长度 ≠ periods 长度" 被判非法并退化成兜底回答。
    claims = [
        {**deepcopy(c), "evidence_ids": _keep(list(c["evidence_ids"]))}
        for c in template["claims"]
    ]
    signals = [
        {**deepcopy(s), "evidence_ids": _keep(list(s["evidence_ids"]))}
        for s in template["signals"]
    ]
    charts = [
        {**deepcopy(c), "evidence_ids": _keep(list(c["evidence_ids"]))}
        for c in template["charts"]
    ]

    # No Evidence, No Claim：引不到证据的条目直接剔除，而不是留空数组字段
    claims = [c for c in claims if c["evidence_ids"]]
    signals = [s for s in signals if s["evidence_ids"]]
    charts = [c for c in charts if c["evidence_ids"]]

    # 反向：没有任何条目引用的证据不要留在响应里（避免孤立证据）
    used: set[str] = set()
    for group in (claims, signals, charts):
        for entry in group:
            used.update(entry["evidence_ids"])
    evidence = [e for e in evidence if e["id"] in used]

    return {
        "answer": template["answer"],
        "claims": claims,
        "signals": signals,
        "charts": charts,
        "evidence": evidence,
        "suggested_questions": list(SUGGESTED_QUESTIONS),
    }


def build_insufficient_response() -> dict[str, Any]:
    """证据不足时的固定兜底结构（HTTP 200，四个数组都为空）。"""
    return {
        "answer": INSUFFICIENT_ANSWER,
        "claims": [],
        "signals": [],
        "charts": [],
        "evidence": [],
        "suggested_questions": list(SUGGESTED_QUESTIONS),
    }


#: 前端 demoSuggestedQuestions（保持与 mock-data.ts 一致）
SUGGESTED_QUESTIONS: tuple[str, ...] = (
    QUESTION_STRUCTURE,
    QUESTION_PROFITABILITY,
    QUESTION_RISK,
    "你的员工喜欢吃水果吗？",
)


# ---------------------------------------------------------------------------
# 自检：把每条引用的页码/原文回到真实库核验
# ---------------------------------------------------------------------------


def verify_facts(*, stock_code: str = DEMO_COMPANY_CODE) -> list[str]:
    """核验 `_FACTS` 里每条引用的页码与原文。

    检查项：
      1. document_id 是 canonical 版本（存在、superseded=0、parse_status='ok'）；
      2. `locate` 片段确实出现在 `page` 这一页（页码不是抄来的，是查出来的）；
      3. 有真实 source_url。

    :returns: 问题描述列表；空列表 = 全部通过。
    """
    problems: list[str] = []
    for key, fact in _FACTS.items():
        document_id = int(fact["document_id"])
        page = int(fact["page"])

        page_text = db.get_page_text(document_id, page)
        if not page_text:
            problems.append(f"{key}: 文档 {document_id} 第 {page} 页取不到原文")
            continue

        needle = str(fact["locate"])
        squeezed = re_squeeze(page_text)
        if re_squeeze(needle) not in squeezed:
            # 表格文本在库里可能被换行/空格拆开，逐 token 顺序核对作为兜底
            if not _tokens_in_order(needle, page_text):
                actual = db.find_pages(document_id, _longest_token(needle))
                problems.append(
                    f"{key}: 引用片段不在文档 {document_id} 第 {page} 页"
                    f"（实际出现于页 {actual or '未找到'}）"
                )
                continue

        if not db.document_url(document_id):
            problems.append(f"{key}: 文档 {document_id} 缺少真实 source_url")
    return problems


def _longest_token(text: str) -> str:
    """取最长的数字 token（如 31,159,083.37）用于回查所在页。"""
    import re as _re

    tokens = _re.findall(r"[\d,]+\.\d+|\d{2,}", text)
    return max(tokens, key=len) if tokens else text[:12]


def re_squeeze(text: str) -> str:
    """压掉所有空白，便于跨行表格文本比对。"""
    return "".join((text or "").split())


def _tokens_in_order(needle: str, haystack: str) -> bool:
    """needle 的空白分词是否按顺序出现在 haystack 中（容忍中间有换行）。"""
    parts = (needle or "").split()
    if not parts:
        return False
    pos = 0
    for part in parts:
        found = haystack.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    return True


__all__ = [
    "DEMO_COMPANY_CODE",
    "INSUFFICIENT_ANSWER",
    "QUESTION_PROFITABILITY",
    "QUESTION_RISK",
    "QUESTION_STRUCTURE",
    "RISK_DISCLAIMER",
    "SUGGESTED_QUESTIONS",
    "build_demo_response",
    "build_insufficient_response",
    "detect_intent",
    "verify_facts",
]
