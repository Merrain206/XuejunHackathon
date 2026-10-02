"""X-Ray 企业穿透分析 · 夜间批处理（night_batch）。

需求（最新）：**夜间批处理仅负责数据采集并写入数据库**，
             移除所有的规则预计算和信号判定逻辑。

因此本模块只做四件事：
  1. 读 sync_log 取上一轮水位，只做增量；
  2. 遍历 company 表**全部公司**（绝不写死某一家），逐家采集；
  3. upsert 字段入库（finance_fields / social_security / legal_risk）；
  4. 写 sync_log（含每源成败），失败时打印日志 + 预留飞书 webhook 钩子。

**不做的事**（已按需求移除）：
  * ✗ 不计算 4 对矛盾信号（signals 改由 ask.py 在请求时实时计算）
  * ✗ 不写 signal_result 表
  * ✗ 不预生成答案、不写 answer_cache、不做版本切换
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from datasource import STATUS_OK, FetchReport, fetch_all
from mock_data import FISCAL_YEARS, LATEST_QUARTER_PERIOD, REPORT_PERIOD
from models import (
    Company,
    FinanceField,
    LegalRisk,
    SocialSecurity,
    SyncLog,
    utcnow,
)

logger = logging.getLogger(__name__)

#: sync_log.task_name 取值
TASK_NIGHT_BATCH = "night_batch"
TASK_ADMIN_REFRESH = "admin_refresh"

#: sync_log.status 取值
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

#: 飞书 webhook 占位地址：等于它时只记日志、不发请求
FEISHU_PLACEHOLDER = "https://open.feishu.cn/open-apis/bot/v2/hook/REPLACE_ME"


# ---------------------------------------------------------------------------
# 结果容器
# ---------------------------------------------------------------------------


@dataclass
class CompanyOutcome:
    """单家公司的采集结果。"""

    stock_code: str
    name: str = ""
    ok: bool = False
    skipped: bool = False
    fields_written: int = 0
    failed_sources: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class BatchReport:
    """整轮采集结果。"""

    started_at: datetime
    finished_at: datetime | None = None
    task_name: str = TASK_NIGHT_BATCH
    status: str = STATUS_FAILED
    companies_total: int = 0
    companies_ok: int = 0
    companies_skipped: int = 0
    companies_failed: int = 0
    fields_written: int = 0
    outcomes: list[CompanyOutcome] = field(default_factory=list)
    error_msg: str | None = None

    @property
    def elapsed_ms(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds() * 1000

    def summary(self) -> str:
        return (
            f"采集 {self.status}：公司 {self.companies_ok}/{self.companies_total} 成功"
            f"（跳过 {self.companies_skipped}，失败 {self.companies_failed}），"
            f"字段写入 {self.fields_written}，耗时 {self.elapsed_ms:.0f}ms"
        )

    def to_log(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "companies_total": self.companies_total,
            "companies_ok": self.companies_ok,
            "companies_skipped": self.companies_skipped,
            "companies_failed": self.companies_failed,
            "fields_written": self.fields_written,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error_msg": self.error_msg,
            "failed": [
                {"stock_code": o.stock_code, "error": o.error, "sources": o.failed_sources}
                for o in self.outcomes
                if not o.ok and not o.skipped
            ],
        }


# ---------------------------------------------------------------------------
# sync_log 读写（增量水位）
# ---------------------------------------------------------------------------


def last_sync_time(db: Session, task_name: str = TASK_NIGHT_BATCH) -> datetime | None:
    """最近一次成功/部分成功采集的完成时间。"""
    return db.scalar(
        select(func.max(SyncLog.finished_at)).where(
            SyncLog.task_name == task_name,
            SyncLog.status.in_((STATUS_SUCCESS, STATUS_PARTIAL)),
            SyncLog.finished_at.is_not(None),
        )
    )


def last_company_sync_time(
    db: Session, stock_code: str, task_name: str = TASK_NIGHT_BATCH
) -> datetime | None:
    """某家公司最近一次成功采集时间（逐家增量）。"""
    return db.scalar(
        select(func.max(SyncLog.finished_at)).where(
            SyncLog.stock_code == stock_code,
            SyncLog.task_name == task_name,
            SyncLog.status.in_((STATUS_SUCCESS, STATUS_PARTIAL)),
        )
    )


def open_sync_log(db: Session, task_name: str, stock_code: str | None = None) -> SyncLog:
    row = SyncLog(
        stock_code=stock_code,
        task_name=task_name,
        status="running",
        rows_affected=0,
        started_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def close_sync_log(
    db: Session,
    row: SyncLog,
    *,
    status: str,
    rows_affected: int = 0,
    error_msg: str | None = None,
) -> None:
    row.status = status
    row.rows_affected = rows_affected
    row.finished_at = utcnow()
    row.error_msg = error_msg
    db.add(row)


# ---------------------------------------------------------------------------
# upsert（纯采集落库）
# ---------------------------------------------------------------------------


def upsert_finance_fields(
    db: Session,
    stock_code: str,
    fields: dict[str, Any],
    sources: dict[str, str] | None = None,
) -> int:
    """按 (stock_code, field_name, period) upsert；返回受影响行数。"""
    src_map = sources or {}
    changed = 0

    for field_name, value in fields.items():
        period = LATEST_QUARTER_PERIOD if field_name.startswith("latest_quarter_") else REPORT_PERIOD
        existing = db.scalar(
            select(FinanceField).where(
                FinanceField.stock_code == stock_code,
                FinanceField.field_name == field_name,
                FinanceField.period == period,
            )
        )
        payload = json.dumps(value, ensure_ascii=False)
        source = src_map.get(field_name, "mock")
        if existing is None:
            db.add(
                FinanceField(
                    stock_code=stock_code,
                    field_name=field_name,
                    field_value=payload,
                    period=period,
                    source=source,
                )
            )
            changed += 1
        elif existing.field_value != payload or existing.source != source:
            existing.field_value = payload
            existing.source = source
            db.add(existing)
            changed += 1
    return changed


def upsert_social_security(db: Session, stock_code: str, fields: dict[str, Any]) -> int:
    """把 social_security_count_3y 展开成按年行；None 照写（不插补，供前端显示未公示）。"""
    series = fields.get("social_security_count_3y")
    if not isinstance(series, (list, tuple)):
        return 0

    changed = 0
    for year, count in zip(FISCAL_YEARS, series):
        value = None if count is None else int(count)
        existing = db.scalar(
            select(SocialSecurity).where(
                SocialSecurity.stock_code == stock_code, SocialSecurity.year == year
            )
        )
        if existing is None:
            db.add(
                SocialSecurity(
                    stock_code=stock_code,
                    year=year,
                    count=value,
                    disclosed=value is not None,
                    source="gsxt",
                )
            )
            changed += 1
        elif existing.count != value:
            existing.count = value
            existing.disclosed = value is not None
            db.add(existing)
            changed += 1
    return changed


def replace_legal_risk_counts(db: Session, stock_code: str, fields: dict[str, Any]) -> int:
    """司法/合规计数按 (stock_code, case_type) 替换一条汇总行（避免无限增长）。"""
    mapping = (
        ("被执行", fields.get("executed_count"), "zxgk"),
        ("失信", fields.get("dishonest_count"), "zxgk"),
        ("涉诉", fields.get("legal_case_count"), "wenshu"),
        ("行政处罚", fields.get("admin_penalty_count"), "creditchina"),
    )
    changed = 0
    for case_type, count, source in mapping:
        if count is None:
            continue
        existing = db.scalar(
            select(LegalRisk).where(
                LegalRisk.stock_code == stock_code, LegalRisk.case_type == case_type
            )
        )
        detail = f"累计 {int(count)} 条（数据源统计口径）"
        if existing is None:
            db.add(
                LegalRisk(
                    stock_code=stock_code,
                    case_type=case_type,
                    amount=None,
                    status="累计统计",
                    date=None,
                    source=source,
                    detail=detail,
                )
            )
            changed += 1
        else:
            existing.detail = detail
            existing.source = source
            existing.status = "累计统计"
            db.add(existing)
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# 单家公司（只采集落库）
# ---------------------------------------------------------------------------


def collect_company(db: Session, company: Company, report: FetchReport) -> CompanyOutcome:
    """采集一家公司并落库。**不做任何信号判定或答案预生成。**"""
    started = time.perf_counter()
    outcome = CompanyOutcome(stock_code=company.stock_code, name=company.name)

    if not report.fields:
        outcome.error = "该公司的数据源未返回任何字典内字段"
        outcome.failed_sources = report.failed_sources()
        outcome.elapsed_ms = (time.perf_counter() - started) * 1000
        return outcome

    # 真实抓取成功的字段标其渠道名；其余标 mock
    source_map = {name: ("mock" if name in report.mock_fields else "cninfo") for name in report.fields}
    for result in report.results:
        if result.status != STATUS_OK:
            continue
        for name in result.fields:
            source_map[name] = result.source

    outcome.fields_written += upsert_finance_fields(db, company.stock_code, report.fields, source_map)
    outcome.fields_written += upsert_social_security(db, company.stock_code, report.fields)
    outcome.fields_written += replace_legal_risk_counts(db, company.stock_code, report.fields)

    company.updated_at = utcnow()
    db.add(company)

    outcome.ok = True
    outcome.failed_sources = report.failed_sources()
    outcome.elapsed_ms = (time.perf_counter() - started) * 1000
    return outcome


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def run_night_batch(
    db: Session,
    *,
    task_name: str = TASK_NIGHT_BATCH,
    force: bool = False,
    only_stock_code: str | None = None,
) -> BatchReport:
    """执行一轮采集。

    :param force: 忽略增量水位，强制重算全部公司
    :param only_stock_code: 只跑这一家（/admin/refresh 用）；None = 遍历全表
    """
    report = BatchReport(started_at=utcnow(), task_name=task_name)

    last_sync = None if force else last_sync_time(db, task_name)
    if last_sync:
        logger.info("[%s] 增量水位: %s", task_name, last_sync.isoformat())

    stmt = select(Company).order_by(Company.stock_code)
    if only_stock_code:
        stmt = stmt.where(Company.stock_code == only_stock_code)
    companies = list(db.scalars(stmt).all())
    report.companies_total = len(companies)

    if not companies:
        report.status = STATUS_SUCCESS
        report.finished_at = utcnow()
        logger.warning("[%s] company 表为空，没有公司可采集", task_name)
        return report

    global_log = open_sync_log(db, task_name)
    db.commit()

    try:
        for company in companies:
            if not force and only_stock_code is None and last_sync:
                company_last = last_company_sync_time(db, company.stock_code, task_name)
                if company_last and company_last >= last_sync:
                    report.companies_skipped += 1
                    report.outcomes.append(
                        CompanyOutcome(
                            stock_code=company.stock_code, name=company.name, ok=True, skipped=True
                        )
                    )
                    continue

            company_log = open_sync_log(db, task_name, company.stock_code)
            db.commit()

            try:
                fetch_report = await fetch_all(company.stock_code)
                outcome = collect_company(db, company, fetch_report)
                report.outcomes.append(outcome)
                report.fields_written += outcome.fields_written

                if outcome.ok:
                    report.companies_ok += 1
                    close_sync_log(
                        db,
                        company_log,
                        status=STATUS_PARTIAL if fetch_report.partial else STATUS_SUCCESS,
                        rows_affected=outcome.fields_written,
                        error_msg=("; ".join(f"{s} 不可用" for s in outcome.failed_sources) or None),
                    )
                else:
                    report.companies_failed += 1
                    close_sync_log(
                        db,
                        company_log,
                        status=STATUS_FAILED,
                        rows_affected=outcome.fields_written,
                        error_msg=outcome.error or "采集失败",
                    )
                db.commit()
                logger.info(
                    "[%s] %s %s：字段写入 %d，耗时 %.0fms",
                    task_name,
                    company.stock_code,
                    "成功" if outcome.ok else "失败",
                    outcome.fields_written,
                    outcome.elapsed_ms,
                )
            except Exception as exc:  # noqa: BLE001 —— 一家失败不影响其他家
                db.rollback()
                report.companies_failed += 1
                report.outcomes.append(
                    CompanyOutcome(
                        stock_code=company.stock_code,
                        name=company.name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                logger.exception("[%s] %s 采集异常，继续下一家", task_name, company.stock_code)
                try:
                    row = open_sync_log(db, task_name, company.stock_code)
                    close_sync_log(db, row, status=STATUS_FAILED, error_msg=f"{type(exc).__name__}: {exc}")
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()

        report.finished_at = utcnow()
        if report.companies_failed == 0:
            report.status = (
                STATUS_PARTIAL if any(o.failed_sources for o in report.outcomes) else STATUS_SUCCESS
            )
        else:
            report.status = STATUS_FAILED
            report.error_msg = f"{report.companies_failed} 家公司采集失败"

        close_sync_log(
            db,
            global_log,
            status=report.status,
            rows_affected=report.fields_written,
            error_msg=report.error_msg,
        )
        db.commit()

    except Exception as exc:  # noqa: BLE001 —— 整轮崩了也要留痕
        db.rollback()
        report.finished_at = utcnow()
        report.status = STATUS_FAILED
        report.error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("[%s] 采集整体失败", task_name)
        try:
            row = open_sync_log(db, task_name)
            close_sync_log(db, row, status=STATUS_FAILED, error_msg=report.error_msg)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    logger.info("[%s] %s", task_name, report.summary())
    if report.status != STATUS_SUCCESS:
        await notify_feishu(report)
    return report


def run_night_batch_sync(db: Session, **kwargs: Any) -> BatchReport:
    """同步入口（脚本用）。调度侧请走 tasks.scheduler（专用线程 + asyncio.run）。"""
    return asyncio.run(run_night_batch(db, **kwargs))


# ---------------------------------------------------------------------------
# 飞书告警钩子（预留）
# ---------------------------------------------------------------------------


async def notify_feishu(report: BatchReport) -> bool:
    """失败告警；未配置或仍是占位地址时只打印日志、不发请求。"""
    text = f"【X-Ray】{report.task_name} 采集 {report.status}\n{report.summary()}"
    if report.error_msg:
        text += f"\n错误：{report.error_msg}"

    url = (settings.FEISHU_WEBHOOK_URL or "").strip()
    if not url or url == FEISHU_PLACEHOLDER or "REPLACE_ME" in url:
        logger.warning("[飞书告警未配置，仅打印] %s", text.replace("\n", " | "))
        return False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.post(url, json={"msg_type": "text", "content": {"text": text}})
            response.raise_for_status()
        logger.info("飞书告警已发送")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("飞书告警发送失败（不影响采集）: %s", exc)
        return False


def send_feishu_sync(report: BatchReport) -> bool:
    """同步版告警（调度线程里用）。"""
    return asyncio.run(notify_feishu(report))


__all__ = [
    "FEISHU_PLACEHOLDER",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_SUCCESS",
    "TASK_ADMIN_REFRESH",
    "TASK_NIGHT_BATCH",
    "BatchReport",
    "CompanyOutcome",
    "close_sync_log",
    "collect_company",
    "last_company_sync_time",
    "last_sync_time",
    "notify_feishu",
    "open_sync_log",
    "replace_legal_risk_counts",
    "run_night_batch",
    "run_night_batch_sync",
    "send_feishu_sync",
    "upsert_finance_fields",
    "upsert_social_security",
]
