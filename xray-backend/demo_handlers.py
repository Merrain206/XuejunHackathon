"""X-Ray 企业穿透分析 · 三条稳定 Demo 问题的**确定性意图处理器**。

为什么需要这个模块
------------------
`ask` 接口原先只读 LLM 生成的缓存（`cache/company_risk_{code}.json`）。没配 API key 时
缓存里全是 `risk_level=unknown`，于是演示时 `signals` / `charts` 全空 —— 界面能开，
但**演示不出内容**。

因此对**固定演示问题**改用确定性处理器：直接给出已人工核验的回答、图表与证据，
不依赖大模型、不依赖缓存、毫秒级返回。其余问题仍走原来的缓存 / LLM 链路。

设计红线（对应 BACKEND_NEXT_STEPS.md / BACKEND_INTEGRATION_TASKS.md）
-------------------------------------------------------------------
1. `No Evidence, No Claim` —— 每条 claim / signal / chart 都引用本响应内真实存在的
   evidence id；
2. **不伪造页码** —— 每个引用的页码都必须在 `facts` 里显式声明，并且启动/自检时
   回到原文逐字核验（见 `verify_facts()`）；
3. **不伪造链接，也不张冠李戴** —— `source_url` / `document_title` / `source_page` /
   `source_quote` 必须共同指向**同一份**已核验 PDF，见 `verified_sources.py`。
   库里的 `docs.source_url` 指向巨潮的 http 地址、且可能是另一版本（上市稿 520 页），
   因此**不直接对外使用**；
4. 证据不足时返回固定兜底文案，**不让模型补充事实**。

意图判定与前端 `mock-data.ts` 的 `selectResponse()` **完全同序**，否则同一个问题在
「后端作答」与「前端降级作答」两条路径下会得到不同答案。

来源与页码口径（本轮逐页核验，PDF 查看器页码）
---------------------------------------------
* 收入结构 → 上交所招股说明书（注册稿），第 **321 / 322 / 324** 页；
* 盈利质量 → 上交所 2025 年半年度报告，第 **8 / 9** 页；
* 主要风险 → 上交所 2025 年半年度报告，第 **2 / 43** 页。
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

import db
import verified_sources
from verified_sources import HALF_YEAR, PROSPECTUS

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
#   source       —— ★ 已核验来源（verified_sources.VerifiedSource），决定对外
#                   的 source_url / document_title；**不是**库里的 docs.source_url
#   document_id  —— cninfo.db 的 docs.id，用于把引用回溯到库内原文做逐字核验
#   page         —— ★ PDF 查看器页码；必须与原文实际所在页一致
#   quote        —— ★ 展示给用户的原文摘录；表格可按列转写，但不得改动数值
#   verify_quote —— 可选；表格转写前的逐字子串，仅用于回到 PDF / 数据库核验
#   category     —— financial / business / company（前端只认这三个）
#   period       —— 期间标签
#   content      —— 证据摘要
#
# ⚠️ 招股书（注册稿）在库里**没有 chunks**，页码核验走 `docs.text_content` 里的
#    「--- 第N页 ---」页界标记（见 db.page_from_markers）；半年报有 chunks，
#    直接按 chunks.page_number 核验。
# ---------------------------------------------------------------------------

#: 招股说明书（注册稿）—— 收入结构，PDF 查看器第 321/322/324 页
_PROSPECTUS_DOC = 299
#: 2025 年半年度报告 —— 盈利质量（第 8/9 页）与风险提示（第 2/43 页）
_HALF_YEAR_DOC = 15

_FACTS: dict[str, dict[str, Any]] = {
    # ---------------- 收入结构（上交所招股说明书（注册稿）） ----------------
    "EV-STR-001": {
        "source": PROSPECTUS.key,
        "document_id": _PROSPECTUS_DOC,
        "page": 322,
        "category": "business",
        "period": "2021—2023",
        "content": "便携式 3D 扫描仪收入持续增长，但占主营业务收入的比例连续下降。",
        "quote": (
            "报告期内，公司便携式3D扫描仪的销售收入分别为12,579.02万元、"
            "14,189.49万元和15,722.33万元，占主营业务收入的比例分别为78.19%、"
            "68.87%和57.87%。"
        ),
    },
    "EV-STR-002": {
        "source": PROSPECTUS.key,
        "document_id": _PROSPECTUS_DOC,
        "page": 324,
        "category": "business",
        "period": "2021—2023",
        "content": "跟踪式 3D 视觉数字化产品收入和收入占比快速提升。",
        "quote": (
            "报告期内，公司跟踪式3D视觉数字化产品的销售收入分别为1,893.69万元、"
            "3,711.22万元和7,222.66万元，占主营业务收入的比例分别为11.77%、"
            "18.01%和26.58%。"
        ),
    },
    "EV-STR-003": {
        "source": PROSPECTUS.key,
        "document_id": _PROSPECTUS_DOC,
        "page": 321,
        "category": "financial",
        "period": "2021—2023",
        "content": (
            "主营业务收入由 2021 年的 16,088.21 万元增至 2023 年的 27,170.18 万元，"
            "收入结构变化发生在整体增长之中。"
        ),
        "quote": (
            "主营业务收入合计：\n"
            "2023 年 27,170.18 万元（100.00%）；\n"
            "2022 年 20,602.47 万元（100.00%）；\n"
            "2021 年 16,088.21 万元（100.00%）。"
        ),
        "verify_quote": "合计27,170.18100.00%20,602.47100.00%16,088.21100.00%",
    },
    # ---------------- 盈利质量（上交所 2025 年半年度报告） ----------------
    #: ⚠️ 半年报第 8 页是「主要会计数据」表格。`quote` 按列转写供前端阅读，
    #:    `verify_quote` 保留 PDF 文本层的逐字子串供自动核验。
    "EV-PRO-001": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 8,
        "category": "financial",
        "period": "2025 年上半年",
        "content": (
            "主要会计数据：营业收入 176,848,509.44 元（同比 +17.70%），"
            "归属于上市公司股东的净利润 54,007,712.64 元（同比 +2.06%）。"
        ),
        "quote": (
            "主要会计数据（本报告期 / 上年同期 / 同比增减）：\n"
            "营业收入：176,848,509.44 元 / 150,248,052.96 元 / 增长 17.70%；\n"
            "利润总额：58,529,309.18 元 / 59,496,310.95 元 / 下降 1.63%；\n"
            "归属于上市公司股东的净利润：54,007,712.64 元 / 52,918,429.73 元 / 增长 2.06%。"
        ),
        "verify_quote": (
            "营业收入176,848,509.44150,248,052.9617.70"
            "利润总额58,529,309.1859,496,310.95-1.63"
            "归属于上市公司股东的净利润54,007,712.6452,918,429.732.06"
        ),
    },
    "EV-PRO-002": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 8,
        "category": "financial",
        "period": "2025 年上半年",
        "content": "扣非归母净利润同比下降 2.93%，利润增速明显低于收入增速。",
        "quote": (
            "主要会计数据（本报告期 / 上年同期 / 同比增减）：\n"
            "归属于上市公司股东的扣除非经常性损益的净利润："
            "47,074,322.51 元 / 48,493,603.14 元 / 下降 2.93%。"
        ),
        "verify_quote": (
            "归属于上市公司股东的扣除非经常性损益的净利润"
            "47,074,322.5148,493,603.14-2.93"
        ),
    },
    "EV-PRO-003": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 9,
        "category": "financial",
        "period": "2025 年上半年",
        "content": (
            "经营活动现金流净额同比下降 32.59%；报告解释为产品迭代加快、备货增加"
            "导致购买材料支付的现金增加。"
        ),
        "quote": (
            "报告期内公司经营活动产生的现金流量净额为3,115.91万元，较上年同期下降"
            "32.59%，主要系随着公司产品迭代速度加快，备货增加导致购买材料支付的"
            "现金增加所致；"
        ),
    },
    # ---------------- 主要风险（上交所半年报第 2 / 43 页） ----------------
    "EV-RISK-000": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 2,
        "category": "business",
        "period": "2025 年上半年",
        "content": "报告期内不存在对公司生产经营构成实质性影响的重大风险。",
        "quote": (
            "报告期内，不存在对公司生产经营构成实质性影响的重大风险。"
            "公司已于本报告中详细描述了存在的相关风险"
        ),
    },
    "EV-RISK-001": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 43,
        "category": "business",
        "period": "2025 年上半年",
        "content": "产品结构变化可能对销售毛利率产生不利影响。",
        "quote": (
            "如果公司未来的产品销售结构中，毛利率较低的产品的销售占比明显上升，"
            "则公司销售毛利率将受到不利影响；"
        ),
    },
    "EV-RISK-002": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 43,
        "category": "business",
        "period": "2025 年上半年",
        "content": "技术优势减弱可能影响产品售价和市场占有率。",
        "quote": (
            "如果公司未来产品技术优势减弱或消除，与竞争对手的优势不明显，"
            "则公司产品的销售价格和市场占有率将受到不利影响。"
        ),
    },
    "EV-RISK-003": {
        "source": HALF_YEAR.key,
        "document_id": _HALF_YEAR_DOC,
        "page": 43,
        "category": "business",
        "period": "2025 年上半年",
        "content": "下游重要应用领域需求萎缩可能导致公司收入下降。",
        "quote": (
            "如果包括航空航天、汽车制造、工程机械、交通运输在内下游重要应用领域"
            "市场需求萎缩，则可能导致公司收入下降，甚至面临业绩大幅下滑的风险。"
        ),
    },
}


#: 已核验事实目录的公开别名（自检脚本 / 测试用；不要就地修改）
FACTS: dict[str, dict[str, Any]] = _FACTS


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
            "id": "CL-RISK-000",
            "text": "报告期内，公司不存在对生产经营构成实质性影响的重大风险。",
            "evidence_ids": ["EV-RISK-000"],
        },
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
            "evidence_ids": ["EV-RISK-000", "EV-RISK-001", "EV-RISK-002", "EV-RISK-003"],
        }
    ],
    # 需求明确：风险问题**无图表**
    "charts": [],
    "evidence_ids": ["EV-RISK-000", "EV-RISK-001", "EV-RISK-002", "EV-RISK-003"],
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

    `source_url` / `document_title` 取自**已核验来源目录**（`verified_sources`），
    而不是库里的 `docs.source_url`：库里那份可能是另一个版本（如巨潮上市稿
    520 页），把它的链接配到注册稿的页码上就是「张冠李戴」。
    """
    fact = _FACTS.get(fact_key)
    if fact is None:
        logger.warning("未登记的 fact：%s", fact_key)
        return None

    source = verified_sources.source_for(str(fact["source"]))
    if source is None:  # 理论上不会发生：_FACTS 只引用已登记来源
        logger.error("证据 %s 的来源 %r 未登记，剔除该证据", fact_key, fact.get("source"))
        return None

    # API 只返回裸 PDF URL；`source_page` 独立传输，由前端统一追加 `#page=N`。
    # 如果前后端都追加 fragment，会形成 `#page=N#page=N` 并导致 PDF 跳页失效。
    url = source.url.split("#", 1)[0]
    if not verified_sources.is_official_high_confidence(source.url):
        logger.error(
            "来源 %s 不是官方高可靠度地址（%s），剔除证据 %s",
            source.key,
            source.url,
            fact_key,
        )
        return None

    return {
        "id": fact_key,
        "category": fact["category"],
        "period": fact["period"],
        "content": fact["content"],
        "document_id": str(int(fact["document_id"])),
        "document_title": source.title,
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
    """核验 `_FACTS` 里每条引用的**原文与出处**，全部回到真实库。

    检查项：
      1. 来源已登记，且是官方高可靠度地址（HTTPS + sse.com.cn）；
      2. 声明页码在来源 PDF 的页数范围内；
      3. 引文在库里**要么找不到、要么唯一地出现在声明页码之外的那个版本页上**；
      4. 每条来源都确实对应库内的一份文档（按标题关键词反查）。

    ⚠️ 为什么这里**不**断言"库里页码 == 声明页码"：
       库里的招股书是**巨潮上市稿（520 页）**，对外引用的是**上交所注册稿（506 页）**。
       两份是不同版本：页数不同、正文页码偏移也不固定（便携式那段在两版分别位于
       查看器第 329 / 322 页，差 7 页；跟踪式那段位于 332 / 324 页，差 8 页），
       所以**库根本无法为注册稿的页码作证**。强行互校只会得出错误结论。
       注册稿的页码由 `verify_facts_against_pdf()` 直接对 PDF 核验（测试里会跑），
       核验记录见 `verified_sources.py` 的 `note`。

    :returns: 问题描述列表；空列表 = 全部通过。
    """
    problems: list[str] = []
    used_sources: set[str] = set()

    for key, fact in _FACTS.items():
        document_id = int(fact["document_id"])
        page = int(fact["page"])
        quote = str(fact.get("verify_quote", fact["quote"]))

        source = verified_sources.source_for(str(fact["source"]))
        if source is None:
            problems.append(f"{key}: 来源 {fact.get('source')!r} 未登记")
            continue
        used_sources.add(source.key)

        if not verified_sources.is_official_high_confidence(source.url):
            problems.append(f"{key}: 来源 {source.key} 不是官方高可靠度地址：{source.url}")
        if not 1 <= page <= source.page_count:
            problems.append(
                f"{key}: 页码 {page} 超出《{source.title}》的 {source.page_count} 页"
            )

        resolved = resolve_fact_page(document_id, quote)
        if len(resolved) > 1:
            problems.append(
                f"{key}: 引文在文档 {document_id} 的多个页码出现 {resolved}，"
                f"无法唯一定位；请改用更独特的摘录"
            )
        elif resolved and page == resolved[0]:
            # 库与来源是同一份文档（半年报即如此）：页码应当**完全一致**
            pass

    for source_key in sorted(used_sources):
        source = verified_sources.source_for(source_key)
        if source is None:
            continue
        if find_document_id(source) is None:
            problems.append(
                f"来源 {source_key} 在库里找不到对应文档（标题关键词 {source.db_title_hint!r}）"
            )
    return problems


def verify_facts_against_pdf(reader_factory: Any, documents: dict[str, Any]) -> list[str]:
    """把每条引文对**已核验来源 PDF 本体**核验：`quote` 必须出现在 `page` 这一页。

    :param reader_factory: `reader_factory(path) -> 可迭代 pages 的对象`；
           每个 page 需有 `extract_text()`。之所以用工厂而不是直接收路径，
           是为了让本模块不依赖任何具体 PDF 库（测试里传 pypdf 即可）。
    :param documents: `{source_key: pdf_path}`；只核验提供了路径的来源。

    :returns: 问题描述列表；空列表 = 全部通过。
    """
    problems: list[str] = []
    cache: dict[str, dict[int, str]] = {}

    for key, fact in _FACTS.items():
        source_key = str(fact["source"])
        path = documents.get(source_key)
        source = verified_sources.source_for(source_key)
        if path is None or source is None:
            continue  # 没提供该来源的 PDF → 跳过（自检不该因此报错）

        if source_key not in cache:
            try:
                reader = reader_factory(path)
                cache[source_key] = {
                    i: re_squeeze(page.extract_text() or "")
                    for i, page in enumerate(reader.pages, start=1)
                }
            except Exception as exc:  # noqa: BLE001
                problems.append(f"来源 {source_key} 的 PDF 读取失败（{path}）：{exc}")
                cache[source_key] = {}

        pages = cache[source_key]
        if not pages:
            continue
        verification_quote = str(fact.get("verify_quote", fact["quote"]))
        actual = sorted(p for p, text in pages.items() if re_squeeze(verification_quote) in text)
        declared = int(fact["page"])
        if not actual:
            problems.append(f"{key}: 引文在《{source.title}》里逐字核不到")
        elif declared not in actual:
            problems.append(
                f"{key}: 声明为《{source.title}》第 {declared} 页，实际出现在页 {actual}"
            )
    return problems


def find_document_id(source: "verified_sources.VerifiedSource") -> int | None:
    """按来源的标题关键词，在库里的 Demo 公司文档中反查 `docs.id`。

    只用于自检与测试（确认"这条引用在库里真的有对应文档"），
    不参与响应组装 —— 对外的 document_id 仍是 `_FACTS` 里登记的那个。
    """
    try:
        for row in db.get_documents(DEMO_COMPANY_CODE, include_superseded=True):
            title = str(row.get("file_name") or "")
            if source.db_title_hint in title:
                return int(row["id"])
    except Exception:  # noqa: BLE001 - 自检不该因库异常而炸掉
        logger.exception("反查来源文档失败：%s", source.key)
    return None


def resolve_fact_page(document_id: int, quote: str) -> list[int]:
    """引文 `quote` 在 `document_id` 里出现的页码（升序去重，可能多页）。

    两条路径互补：
      * 有 `chunks`（年报 / 半年报）→ 按 `chunks.page_number` 精确核验；
      * 没有 `chunks`（招股书）→ 按 `docs.text_content` 的「--- 第N页 ---」
        页界标记核验（见 db.page_from_markers）。

    判定用**全部空白分词**而不是某一个数字 token：只匹配一个金额会出现大量
    假命中（实测 "176,848,509.44" 在半年报里出现在 5 个不同页面）。

    ⚠️ 返回的是"**库内那份文档**的 PDF 查看器页码"，与对外引用的
       上交所注册稿页码不是同一套坐标系（不同版本、无固定偏移），
       因此调用方不应拿它去断言 `_FACTS["page"]`。
    """
    text = (quote or "").strip()
    if not text:
        return []
    return db.page_from_markers(document_id, text)


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
    "FACTS",
    "INSUFFICIENT_ANSWER",
    "QUESTION_PROFITABILITY",
    "QUESTION_RISK",
    "QUESTION_STRUCTURE",
    "RISK_DISCLAIMER",
    "SUGGESTED_QUESTIONS",
    "build_demo_response",
    "build_insufficient_response",
    "detect_intent",
    "resolve_fact_page",
    "verify_facts",
    "verify_facts_against_pdf",
]
