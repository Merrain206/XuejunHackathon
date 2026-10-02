"""X-Ray 企业穿透分析 · 三条 Demo 回答的**权威来源目录**。

为什么单独一个模块
------------------
`cninfo.db` 里同一份文件常常存在**多个版本**（巨潮上市稿 520 页 vs 上交所注册稿
506 页），两版的页数、页码、甚至披露期间都不同。数据库只知道它自己入库的那一版，
所以 **`docs.source_url` 不能直接当成对外的 `source_url`** —— 那会把 A 版的页码
配到 B 版的链接上，点开必然对不上（BACKEND_INTEGRATION_TASKS.md 明令禁止）。

因此把"前端已人工核验过的两份上交所原始 PDF"固化成常量，并逐页核验（见
`demo_handlers` 的 `verify_facts()`）：

  * 收入结构 → 上交所《招股说明书（注册稿）》，PDF 查看器第 321/322/324 页；
  * 盈利质量 → 上交所《2025 年半年度报告》，PDF 查看器第 8、9 页；
  * 主要风险 → 上交所《2025 年半年度报告》，PDF 查看器第 2、43 页。

核验方式（本轮实测，结论可复现）：
  * 招股书注册稿 506 页，正文页脚为「1-1-320/321/322」，即 **页脚号 = 查看器号 − 1**；
    用 pypdf 逐页抽取文本，三条原文的落点与上表一致。
  * 半年报 269 页，与 `cninfo.db` 的 chunks 表为同一份文件（首页「N / 269」页脚
    与 chunks 的 page_number 完全一致），所以相关原文既能核到页，也能核到字。

红线
----
1. **不得伪造 URL**：这里的 URL 都是可直接打开的 PDF 原始地址，不是公告列表页；
2. **不得张冠李戴**：`source_page` 与 `source_url` 必须属于同一份 PDF；
3. 未完成逐字核验的文档不得放进本目录，也不得支撑 Claim / Signal / Chart。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Demo 公司（当前固定为思看科技）
DEMO_COMPANY_CODE: Final[str] = "688583"


@dataclass(frozen=True)
class VerifiedSource:
    """一份**已人工核验**的权威 PDF。"""

    key: str
    #: 可直接打开的 PDF 原始地址（HTTPS、上交所域名 → 前端判为「高可靠度」）
    url: str
    #: 展示给用户的文档标题
    title: str
    #: 该 PDF 的 PDF 查看器总页数（用于自检页码是否越界）
    page_count: int
    #: `cninfo.db` 里对应文档的定位关键词（用于按库内容反查真实 document_id）
    db_title_hint: str
    #: 口径说明（为什么会是这个页码），写给后来人看
    note: str

#: 上交所《思看科技首次公开发行股票并在科创板上市招股说明书（注册稿）》2024-08-16
#: 前端 mock-data.ts 里已核验的 `prospectusUrl`，逐字一致。
PROSPECTUS_URL: Final[str] = (
    "https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf"
)

#: 上交所《思看科技 2025 年半年度报告》2025-08-28
#: 前端 mock-data.ts 里已核验的 `halfYearReportUrl`，逐字一致。
HALF_YEAR_URL: Final[str] = (
    "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-28/688583_20250828_9F77.pdf"
)

#: 注册稿：506 页（PDF 目录 `/Count 506`）；页脚 1-1-320～1-1-322
PROSPECTUS: Final[VerifiedSource] = VerifiedSource(
    key="prospectus",
    url=PROSPECTUS_URL,
    title="思看科技首次公开发行股票并在科创板上市招股说明书（注册稿）",
    page_count=506,
    db_title_hint="招股说明书",
    note=(
        "上交所注册稿，PDF 查看器共 506 页；正文页脚为「1-1-N」，页脚号 = 查看器号 − 1。"
        "注意与巨潮上市稿（520 页）不是同一份，页码不可混用。"
    ),
)

#: 2025 年半年度报告：269 页，与 cninfo.db 中同名文档为同一份
HALF_YEAR: Final[VerifiedSource] = VerifiedSource(
    key="half_year",
    url=HALF_YEAR_URL,
    title="思看科技 2025 年半年度报告",
    page_count=269,
    db_title_hint="2025年半年度报告",
    note=(
        "上交所半年报，PDF 查看器共 269 页，首页页脚为「N / 269」，"
        "与 cninfo.db chunks 表的 page_number 口径一致。"
    ),
)

#: 按 key 取来源
SOURCES: Final[dict[str, VerifiedSource]] = {
    PROSPECTUS.key: PROSPECTUS,
    HALF_YEAR.key: HALF_YEAR,
}

#: 前端 api.ts 判定「高可靠度来源」的规则：HTTPS + sse.com.cn 及其子域名。
#: 放在这里是为了让测试能直接断言，而不是把规则抄进测试里。
OFFICIAL_HOST_SUFFIX: Final[str] = "sse.com.cn"


def is_official_high_confidence(url: str | None) -> bool:
    """该 URL 是否会被前端判为「官方高可靠度」。

    规则（与前端一致）：`https://` + 主机名是 `sse.com.cn` 或其子域名。
    """
    if not isinstance(url, str):
        return False
    candidate = url.strip()
    if not candidate.lower().startswith("https://"):
        return False
    host = candidate[len("https://"):].split("/", 1)[0].split(":", 1)[0].lower()
    return host == OFFICIAL_HOST_SUFFIX or host.endswith("." + OFFICIAL_HOST_SUFFIX)


def source_for(key: str) -> VerifiedSource | None:
    """按 key 取已核验来源；未登记返回 None。"""
    return SOURCES.get(key)


def all_sources() -> tuple[VerifiedSource, ...]:
    """全部已核验来源（自检 / 测试用）。"""
    return tuple(SOURCES.values())


__all__ = [
    "DEMO_COMPANY_CODE",
    "HALF_YEAR",
    "HALF_YEAR_URL",
    "OFFICIAL_HOST_SUFFIX",
    "PROSPECTUS",
    "PROSPECTUS_URL",
    "SOURCES",
    "VerifiedSource",
    "all_sources",
    "is_official_high_confidence",
    "source_for",
]
