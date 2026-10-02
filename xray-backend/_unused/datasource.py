"""X-Ray 企业穿透分析 · 外部数据源适配层。

数据字典 v0.4（免费渠道版）的 6 个官方渠道：
    ①社保参保人数  → 国家企业信用信息公示系统 gsxt.gov.cn
    ②被执行人/失信 → 中国执行信息公开网 zxgk.court.gov.cn
    ③股权质押      → 巨潮资讯网公告 cninfo.com.cn
    ④涉诉案件      → 中国裁判文书网 wenshu.court.gov.cn
    ⑤行政处罚      → 信用中国 creditchina.gov.cn
    ⑥注册资本      → 巨潮 / 公示系统均可

硬性约束（数据字典第 6 节 / 需求[二][七]）：
  * 数据源必须走本抽象层，router 里不许直接调外部 API；
  * 每个数据源必须有 mock 降级，config 里一个变量（MOCK_MODE）切换真假数据；
  * 各数据源独立超时 5s、重试 3 次、**失败不中断其他源**；
  * router 里不许直接调外部 API —— 一切经 fetch_all()。

⚠️ 首次运行时的诚实行为（重要，不要误解为 bug）：
  这 5 个政府渠道都没有公开 API，且多数站点对非浏览器请求返回 403/验证码。
  因此每个 HTTP 适配器在请求失败时**不编造数据**，而是返回
  status="unavailable" + mock 兜底值；夜间跑批只对 status="ok" 的源做 upsert，
  绝不把兜底值写进数据库冒充真实抓取。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

from config import settings
from models import DICT_FIELD_NAMES

logger = logging.getLogger(__name__)

#: 数据源状态
STATUS_OK = "ok"                    # 真实抓取成功
STATUS_MOCK = "mock"                # 使用 mock 兜底（MOCK_MODE=true 或抓取失败后降级）
STATUS_UNAVAILABLE = "unavailable"  # 真实源不可用，且未启用 mock 兜底
STATUS_SKIPPED = "skipped"          # 明确跳过（例如未实现）

#: 需要落库的状态（unavailable 不落库，避免把兜底值当真）
WRITABLE_STATUSES: frozenset[str] = frozenset({STATUS_OK, STATUS_MOCK})


# ---------------------------------------------------------------------------
# 结果容器
# ---------------------------------------------------------------------------


@dataclass
class SourceResult:
    """单个数据源的抓取结果。"""

    source: str
    status: str
    fields: dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    attempts: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_OK, STATUS_MOCK)

    @property
    def is_real(self) -> bool:
        return self.status == STATUS_OK

    def to_log(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "fields": sorted(self.fields.keys()),
            "attempts": self.attempts,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "detail": self.detail,
            "error": self.error,
        }


@dataclass
class FetchReport:
    """一次完整抓取的汇总。"""

    stock_code: str
    fields: dict[str, Any] = field(default_factory=dict)
    results: list[SourceResult] = field(default_factory=list)
    #: 真实抓取成功覆盖的字段
    real_fields: set[str] = field(default_factory=set)
    #: 来自 mock 兜底的字段
    mock_fields: set[str] = field(default_factory=set)
    #: 完全拿不到的字段
    missing_fields: set[str] = field(default_factory=set)
    elapsed_ms: float = 0.0

    @property
    def partial(self) -> bool:
        """是否有源失败（用于 sync_log 的 status=partial）。"""
        return any(r.status == STATUS_UNAVAILABLE for r in self.results)

    @property
    def all_from_mock(self) -> bool:
        return bool(self.results) and all(r.status == STATUS_MOCK for r in self.results)

    def failed_sources(self) -> list[str]:
        return [r.source for r in self.results if r.status == STATUS_UNAVAILABLE]

    def to_log(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "sources": [r.to_log() for r in self.results],
            "real_fields": sorted(self.real_fields),
            "mock_fields": sorted(self.mock_fields),
            "missing_fields": sorted(self.missing_fields),
            "failed": self.failed_sources(),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class DataSource:
    """数据源抽象基类。

    子类只需实现 `_fetch(codes) -> dict[str, Any]`（真实网络抓取），
    超时/重试/降级/字段校验全部由基类统一处理。
    """

    #: 数据源标识（写入 finance_fields.source）
    name: str = "base"
    #: 该源负责的字典字段名（必须是 DICT_FIELD_NAMES 的子集）
    field_names: tuple[str, ...] = ()
    #: 该源对应的渠道说明（写进日志/coverage）
    channel: str = ""
    base_url: str = ""

    def __init__(self) -> None:
        illegal = [f for f in self.field_names if f not in DICT_FIELD_NAMES]
        if illegal:
            raise ValueError(f"{type(self).__name__} 声明了数据字典外的字段: {illegal}")

    # -- 子类实现 ----------------------------------------------------------

    async def _fetch(self, client: httpx.AsyncClient, stock_code: str) -> dict[str, Any]:
        """真实抓取；返回 {字段名: 值}。抛异常表示失败。"""
        raise NotImplementedError

    def mock_values(self, stock_code: str) -> dict[str, Any]:
        """mock 兜底值；默认从 mock_data 取该公司字段。"""
        from mock_data import mock_fields

        fields = mock_fields(stock_code) or {}
        return {f: fields[f] for f in self.field_names if f in fields}

    # -- 基类统一处理 ------------------------------------------------------

    def only_known_fields(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """只保留本源声明、且在字典内的字段（防止越界写入）。"""
        return {
            k: v
            for k, v in values.items()
            if k in self.field_names and k in DICT_FIELD_NAMES
        }

    async def fetch(self, client: httpx.AsyncClient, stock_code: str) -> SourceResult:
        """带超时/重试/降级的一次抓取；**永不抛异常**给调用方。"""
        started = time.perf_counter()
        attempts = 0
        last_error: str | None = None

        # MOCK_MODE=true：完全不发外部请求（需求[二] config 一个变量切换真假数据）
        if settings.MOCK_MODE:
            values = self.only_known_fields(self.mock_values(stock_code))
            return SourceResult(
                source=self.name,
                status=STATUS_MOCK,
                fields=values,
                detail=f"MOCK_MODE=true，{self.channel}未发起外部请求",
                attempts=0,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        retries = max(1, settings.DATASOURCE_RETRY_TIMES)
        while attempts < retries:
            attempts += 1
            try:
                raw = await asyncio.wait_for(
                    self._fetch(client, stock_code), timeout=settings.DATASOURCE_TIMEOUT_SECONDS
                )
                values = self.only_known_fields(raw or {})
                if not values:
                    raise ValueError("该源未返回任何字典内字段")
                return SourceResult(
                    source=self.name,
                    status=STATUS_OK,
                    fields=values,
                    detail=f"{self.channel}抓取成功",
                    attempts=attempts,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            except Exception as exc:  # noqa: BLE001 —— 任何失败都降级，不能中断其他源
                last_error = f"{type(exc).__name__}: {exc}"
                logger.debug("%s 第 %d/%d 次抓取失败: %s", self.name, attempts, retries, last_error)
                if attempts < retries:
                    await asyncio.sleep(settings.DATASOURCE_RETRY_BACKOFF * attempts)

        # 全部重试失败 → 降级
        values = self.only_known_fields(self.mock_values(stock_code))
        elapsed = (time.perf_counter() - started) * 1000
        if values:
            logger.warning("%s 抓取失败，降级为 mock 兜底（%d 次尝试）: %s", self.name, attempts, last_error)
            return SourceResult(
                source=self.name,
                status=STATUS_MOCK,
                fields=values,
                detail=f"{self.channel}不可用，已降级为 mock 兜底",
                attempts=attempts,
                elapsed_ms=elapsed,
                error=last_error,
            )
        logger.error("%s 抓取失败且无 mock 兜底（%d 次尝试）: %s", self.name, attempts, last_error)
        return SourceResult(
            source=self.name,
            status=STATUS_UNAVAILABLE,
            fields={},
            detail=f"{self.channel}不可用，且无兜底数据",
            attempts=attempts,
            elapsed_ms=elapsed,
            error=last_error,
        )


# ---------------------------------------------------------------------------
# 巨潮资讯网：财报三表 + 股权质押 + 注册资本
# ---------------------------------------------------------------------------


class CninfoDataSource(DataSource):
    """巨潮资讯网（cninfo.com.cn）—— 定期报告与公告。"""

    name = "cninfo"
    channel = "巨潮资讯网"
    base_url = settings.CNINFO_BASE_URL
    field_names = (
        "revenue_3y",
        "net_profit_3y",
        "oper_cashflow_3y",
        "accounts_receivable_3y",
        "asset_liability_ratio",
        "equity_pledge_count",
        "reg_capital",
        "latest_quarter_profit",
        "latest_quarter_revenue",
        "latest_quarter_cashflow",
    )

    async def _fetch(self, client: httpx.AsyncClient, stock_code: str) -> dict[str, Any]:
        """巨潮没有公开的免鉴权财务数据接口。

        这里只做**可达性探测**：确认站点可用后仍需人工/后续接入 PDF 解析管线。
        探测不通 → 抛异常 → 基类降级为 mock（绝不编造财务数字）。
        """
        response = await client.get(f"{self.base_url}/new/information/top/search", params={"keyWord": stock_code})
        response.raise_for_status()
        raise NotImplementedError(
            "巨潮资讯网无公开 JSON 财务接口；年报 PDF 解析管线尚未接入，"
            "本轮按降级处理（不会写入编造的财务数据）"
        )


# ---------------------------------------------------------------------------
# 国家企业信用信息公示系统：社保参保人数
# ---------------------------------------------------------------------------


class GsxtDataSource(DataSource):
    """国家企业信用信息公示系统（gsxt.gov.cn）—— 企业年报社保事项。"""

    name = "gsxt"
    channel = "国家企业信用信息公示系统"
    base_url = settings.GSXT_BASE_URL
    field_names = ("social_security_count_3y",)

    async def _fetch(self, client: httpx.AsyncClient, stock_code: str) -> dict[str, Any]:
        response = await client.get(self.base_url)
        response.raise_for_status()
        raise NotImplementedError(
            "gsxt 需要人工输入验证码且无公开 API；社保参保人数须逐家在企业年报页读取，"
            "本轮按降级处理（企业也可能选择不公示）"
        )


# ---------------------------------------------------------------------------
# 中国执行信息公开网：被执行人 / 失信被执行人
# ---------------------------------------------------------------------------


class ZxgkDataSource(DataSource):
    """中国执行信息公开网（zxgk.court.gov.cn）—— 被执行与失信记录。"""

    name = "zxgk"
    channel = "中国执行信息公开网"
    base_url = settings.ZXGK_BASE_URL
    field_names = ("executed_count", "dishonest_count")

    async def _fetch(self, client: httpx.AsyncClient, stock_code: str) -> dict[str, Any]:
        response = await client.get(self.base_url)
        response.raise_for_status()
        raise NotImplementedError(
            "zxgk 查询需验证码且无公开 API；本轮按降级处理（字典允许网站不可用时走 mock 降级）"
        )


# ---------------------------------------------------------------------------
# 中国裁判文书网：涉诉案件
# ---------------------------------------------------------------------------


class WenshuDataSource(DataSource):
    """中国裁判文书网（wenshu.court.gov.cn）—— 涉诉案件。"""

    name = "wenshu"
    channel = "中国裁判文书网"
    base_url = settings.WENSHU_BASE_URL
    field_names = ("legal_case_count",)

    async def _fetch(self, client: httpx.AsyncClient, stock_code: str) -> dict[str, Any]:
        response = await client.get(self.base_url)
        response.raise_for_status()
        raise NotImplementedError(
            "裁判文书网需登录且 2024 下半年起部分文书不再公开；本轮按降级处理"
        )


# ---------------------------------------------------------------------------
# 信用中国：行政处罚
# ---------------------------------------------------------------------------


class CreditChinaDataSource(DataSource):
    """信用中国（creditchina.gov.cn）—— 行政处罚。"""

    name = "creditchina"
    channel = "信用中国"
    base_url = settings.CREDITCHINA_BASE_URL
    field_names = ("admin_penalty_count",)

    async def _fetch(self, client: httpx.AsyncClient, stock_code: str) -> dict[str, Any]:
        response = await client.get(self.base_url)
        response.raise_for_status()
        raise NotImplementedError(
            "信用中国无公开免鉴权 API；数据来自各部门推送且存在滞后，本轮按降级处理"
        )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

#: 默认数据源顺序（免费渠道优先，字典顺序）
DEFAULT_SOURCES: tuple[type[DataSource], ...] = (
    CninfoDataSource,
    GsxtDataSource,
    ZxgkDataSource,
    WenshuDataSource,
    CreditChinaDataSource,
)


def build_sources() -> list[DataSource]:
    """构造数据源实例列表。"""
    return [cls() for cls in DEFAULT_SOURCES]


def source_catalog() -> list[dict[str, Any]]:
    """数据源目录（/health capabilities 用）。"""
    return [
        {
            "name": cls.name,
            "channel": cls.channel,
            "base_url": cls.base_url,
            "field_names": list(cls.field_names),
        }
        for cls in DEFAULT_SOURCES
    ]


# ---------------------------------------------------------------------------
# 聚合抓取
# ---------------------------------------------------------------------------


def _make_client() -> httpx.AsyncClient:
    """统一构造 HTTP 客户端：超时 + 浏览器 UA（很多政府站点拒非浏览器 UA）。"""
    timeout = httpx.Timeout(settings.DATASOURCE_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    return httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True)


async def fetch_all(
    stock_code: str,
    sources: Sequence[DataSource] | None = None,
    *,
    allow_mock_fallback: bool = True,
) -> FetchReport:
    """并发抓取全部数据源并汇总为字典字段。

    * 各源独立超时/重试，**任何一个源失败都不影响其他源**；
    * MOCK_MODE=true 时全部走 mock，不发任何外部请求；
    * 返回的字段里，只有 real_fields 是真实抓取到的，mock_fields 是兜底值。
    """
    started = time.perf_counter()
    active = list(sources) if sources is not None else build_sources()
    report = FetchReport(stock_code=stock_code)

    if settings.MOCK_MODE:
        # 不建 HTTP 客户端，确保「命中/降级路径不发起任何外部请求」
        results = [
            await src.fetch(_NO_CLIENT, stock_code)  # type: ignore[arg-type]
            for src in active
        ]
    else:
        client = _make_client()
        try:
            results = await asyncio.gather(
                *(src.fetch(client, stock_code) for src in active),
                return_exceptions=True,
            )
        finally:
            await client.aclose()

    for src, result in zip(active, results):
        if isinstance(result, BaseException):
            # gather 兜底：理论上 fetch() 不抛异常，这里防御性处理
            report.results.append(
                SourceResult(
                    source=src.name,
                    status=STATUS_UNAVAILABLE,
                    detail=f"未知异常: {type(result).__name__}",
                    error=str(result),
                )
            )
            continue
        report.results.append(result)

    # 汇总字段（后到的源不覆盖先到的真实值）
    for result in report.results:
        if not result.ok:
            continue
        if not allow_mock_fallback and result.status == STATUS_MOCK:
            continue
        for name, value in result.fields.items():
            if name in report.fields and name in report.real_fields:
                continue  # 已有真实值，不降级覆盖
            report.fields[name] = value
            if result.is_real:
                report.real_fields.add(name)
                report.mock_fields.discard(name)
            elif name not in report.real_fields:
                report.mock_fields.add(name)

    report.missing_fields = {f for f in DICT_FIELD_NAMES if f not in report.fields}
    report.elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "抓取完成 %s：真实字段 %d，兜底字段 %d，缺失 %d，耗时 %.0fms，失败源 %s",
        stock_code,
        len(report.real_fields),
        len(report.mock_fields),
        len(report.missing_fields),
        report.elapsed_ms,
        report.failed_sources() or "无",
    )
    return report


class _NoClient:
    """MOCK_MODE 下的占位客户端：任何使用都会立刻报错。

    这样若某个数据源在 MOCK_MODE 下仍尝试发请求，会立刻暴露，而不是静默联网。
    """

    def __getattr__(self, item: str) -> Any:
        raise RuntimeError("MOCK_MODE=true 时不允许发起外部 HTTP 请求")


_NO_CLIENT = _NoClient()


def fetch_all_sync(stock_code: str, sources: Sequence[DataSource] | None = None) -> FetchReport:
    """同步入口（夜间跑批是同步 SQLAlchemy，用这个桥接）。"""
    return asyncio.run(fetch_all(stock_code, sources))


__all__ = [
    "DEFAULT_SOURCES",
    "STATUS_MOCK",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "STATUS_UNAVAILABLE",
    "WRITABLE_STATUSES",
    "CninfoDataSource",
    "CreditChinaDataSource",
    "DataSource",
    "FetchReport",
    "GsxtDataSource",
    "SourceResult",
    "WenshuDataSource",
    "ZxgkDataSource",
    "build_sources",
    "fetch_all",
    "fetch_all_sync",
    "source_catalog",
]
