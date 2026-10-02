"""X-Ray 企业穿透分析 · APScheduler 装配。

需求[七]：cron 每天 02:00；.env 提供开关可切为每 5 分钟一次（开发模式）。

注意：夜间作业现在**只做数据采集落库**（tasks/night_batch.py），
不再计算信号、不再预生成答案、不涉及版本切换。

用法（main.py 的 lifespan）：
    from tasks.scheduler import start_scheduler, shutdown_scheduler
    start_scheduler()          # 启动
    shutdown_scheduler()       # 关停

线程与事件循环（踩过的坑，务必保留注释）：
    * 采集是 **async**（数据源要并发、要各自超时）；
    * 但 APScheduler 的同步 job 跑在线程池里，且 FastAPI 的 async lifespan
      自身处在运行中的事件循环里 —— 在 lifespan 里直接 asyncio.run() 会抛
      "asyncio.run() cannot be called from a running event loop"；
    * 因此 job 一律提交到**专用单线程池**，由该线程自己 asyncio.run()。
      这样既不阻塞 API 事件循环，也不会嵌套事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from database import SessionLocal, init_db
from tasks.night_batch import (
    STATUS_FAILED,
    TASK_ADMIN_REFRESH,
    TASK_NIGHT_BATCH,
    BatchReport,
    run_night_batch,
    send_feishu_sync,
)

logger = logging.getLogger(__name__)

NIGHT_JOB_ID = "xray-night-batch"
ADMIN_JOB_ID = "xray-admin-refresh"

_scheduler: BackgroundScheduler | None = None
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 作业体：在专用线程里跑自己的事件循环
# ---------------------------------------------------------------------------


def _run_in_worker(coro_factory: Any, label: str) -> BatchReport | None:
    """在专用线程中执行一个 async 采集作业，返回报告。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xray-night")

    def _task() -> BatchReport | None:
        db = SessionLocal()
        try:
            return asyncio.run(coro_factory(db))
        except Exception:  # noqa: BLE001
            logger.exception("[%s] 作业执行失败", label)
            return None
        finally:
            db.close()

    future = _executor.submit(_task)
    try:
        return future.result()
    except Exception:  # noqa: BLE001
        logger.exception("[%s] 作业结果获取失败", label)
        return None


def job_night_batch() -> None:
    """定时采集作业（遍历全部公司）。"""
    logger.info("[调度] 夜间采集开始")
    report = _run_in_worker(lambda db: run_night_batch(db, task_name=TASK_NIGHT_BATCH), TASK_NIGHT_BATCH)
    _after_job(report)


def job_admin_refresh(stock_code: str | None = None, force: bool = True) -> BatchReport | None:
    """手动触发采集（/admin/refresh 用）。

    接口层只负责校验 token 并提交作业；真正执行仍走这里，
    保证「手动触发」与「定时触发」走同一套代码路径。
    """
    label = f"{TASK_ADMIN_REFRESH}:{stock_code or 'ALL'}"
    logger.info("[调度] 手动触发采集 %s", label)
    report = _run_in_worker(
        lambda db: run_night_batch(
            db,
            task_name=TASK_ADMIN_REFRESH,
            force=force,
            only_stock_code=stock_code,
        ),
        label,
    )
    _after_job(report)
    return report


def _after_job(report: BatchReport | None) -> None:
    """作业收尾：失败时发告警（未配置 webhook 时只记日志）。"""
    if report is None:
        return
    if report.status == STATUS_FAILED:
        try:
            send_feishu_sync(report)
        except Exception:  # noqa: BLE001 —— 告警失败绝不影响主流程
            logger.exception("告警发送异常")


# ---------------------------------------------------------------------------
# 启停
# ---------------------------------------------------------------------------


def build_trigger() -> CronTrigger | IntervalTrigger:
    """按 .env 构造触发器：默认 cron 每天 02:00；开发模式每 5 分钟。"""
    if settings.NIGHTLY_DEV_MODE:
        return IntervalTrigger(minutes=max(1, settings.NIGHTLY_DEV_INTERVAL_MINUTES))
    return CronTrigger(hour=settings.NIGHTLY_CRON_HOUR, minute=settings.NIGHTLY_CRON_MINUTE)


def describe_schedule() -> str:
    """给 /health 展示的调度描述。"""
    if settings.NIGHTLY_DEV_MODE:
        return f"每 {max(1, settings.NIGHTLY_DEV_INTERVAL_MINUTES)} 分钟（NIGHTLY_DEV_MODE=true）"
    return f"每天 {settings.NIGHTLY_CRON_HOUR:02d}:{settings.NIGHTLY_CRON_MINUTE:02d}"


def start_scheduler() -> BackgroundScheduler | None:
    """启动调度器（幂等）。SCHEDULER_ENABLED=false 时返回 None。"""
    global _scheduler
    with _lock:
        if not settings.SCHEDULER_ENABLED:
            logger.info("SCHEDULER_ENABLED=false，不启动定时任务")
            return None
        if _scheduler is not None and _scheduler.running:
            logger.debug("调度器已在运行，跳过重复启动")
            return _scheduler

        init_db()

        # coalesce=True：错过的多次触发合并为一次，避免补跑风暴
        # max_instances=1：同一作业不允许重叠执行（采集是写操作）
        scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        )
        scheduler.add_job(
            job_night_batch,
            trigger=build_trigger(),
            id=NIGHT_JOB_ID,
            name="X-Ray 夜间数据采集",
            replace_existing=True,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info("调度器已启动：%s（%s）", describe_schedule(), NIGHT_JOB_ID)
        return scheduler


def shutdown_scheduler(wait: bool = False) -> None:
    """关停调度器并释放线程池。"""
    global _scheduler, _executor
    with _lock:
        if _scheduler is not None:
            try:
                if _scheduler.running:
                    _scheduler.shutdown(wait=wait)
                logger.info("调度器已关停")
            except Exception:  # noqa: BLE001
                logger.exception("调度器关停异常")
            finally:
                _scheduler = None
        if _executor is not None:
            try:
                _executor.shutdown(wait=wait)
            except Exception:  # noqa: BLE001
                logger.exception("采集线程池关停异常")
            finally:
                _executor = None


def get_scheduler() -> BackgroundScheduler | None:
    """当前调度器实例（/health 与测试用）。"""
    return _scheduler


def list_jobs() -> list[dict[str, Any]]:
    """列出已注册作业（/health 用）。"""
    if _scheduler is None:
        return []
    return [
        {
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        }
        for job in _scheduler.get_jobs()
    ]


def trigger_now(stock_code: str | None = None, force: bool = True) -> BatchReport | None:
    """立即执行一次采集（同步；供脚本/测试用，不依赖调度器是否启动）。"""
    return job_admin_refresh(stock_code=stock_code, force=force)


__all__ = [
    "ADMIN_JOB_ID",
    "NIGHT_JOB_ID",
    "build_trigger",
    "describe_schedule",
    "get_scheduler",
    "job_admin_refresh",
    "job_night_batch",
    "list_jobs",
    "shutdown_scheduler",
    "start_scheduler",
    "trigger_now",
]
