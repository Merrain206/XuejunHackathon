#!/usr/bin/env python
"""夜间批处理：遍历所有公司 → 调 analyze_company() → 落地风险结论与汇总报告。

产出（全部在 logs/ 下）：
    logs/company_risk_{code}.json   每家公司一份分析结论
    logs/night_batch_report.json    汇总（run_at / mode / companies_processed /
                                    alerts_found / duration_seconds / status）
    logs/night_batch_report.txt     同一份汇总的人可读版本

用法：
    python scripts/run_night_batch.py --demo     # 只处理前 5 家（演示用，快）
    python scripts/run_night_batch.py            # 全量
    python scripts/run_night_batch.py --dry-run  # 只打印计划做什么，不调 LLM、不写文件
    python scripts/run_night_batch.py --demo --force   # 忽略当日缓存强制重算

依赖：除 LLM API 外不依赖外网。数据来自 data/cninfo.db（只读）。

⚠️ 本脚本**不含任何调度逻辑** —— 生产环境由系统 cron 定时调用
（见 deploy/crontab.xray），demo 环境手动执行。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

# --- 路径：保证从任意工作目录调用都能 import 到项目模块 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("night_batch")

#: demo 模式默认处理家数（3~5 家，速度快）
DEMO_LIMIT = 5

#: 报告文件名
REPORT_JSON = "night_batch_report.json"
REPORT_TXT = "night_batch_report.txt"
COMPANY_TEMPLATE = "company_risk_{code}.json"

#: 运行模式（写进报告）
MODE_DEMO = "demo"
MODE_FULL = "full"
MODE_DRY_RUN = "dry-run"

#: 视为"预警"的风险等级（写进 alerts_found）
ALERT_LEVELS = ("high", "medium")

#: 视为"分析未成功"的来源（用于判定 status 与收集错误）
FAILED_SOURCES = ("error", "llm_failed", "parse_failed", "no_data")


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def log(message: str = "") -> None:
    """带时间戳的人类可读进度输出。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}" if message else "", flush=True)


def company_path(code: str) -> Path:
    return settings.log_dir / COMPANY_TEMPLATE.format(code=code)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _describe_window(days: int | None) -> str:
    return "不限（全部公告）" if days is None else f"最近 {days} 天"


# ---------------------------------------------------------------------------
# 汇总报告
# ---------------------------------------------------------------------------


def build_report(
    *,
    mode: str,
    started_at: datetime,
    finished_at: datetime,
    results: dict[str, dict[str, Any]],
    targets: list[str],
    skipped: list[str],
    status: str,
    errors: list[str],
    force: bool,
    no_cache: bool,
    days: int | None,
) -> dict[str, Any]:
    """按约定的六个字段组装汇总报告（另附诊断字段）。"""
    alerts = {
        code: res
        for code, res in results.items()
        if str(res.get("risk_level") or "unknown") in ALERT_LEVELS
    }
    level_counts: dict[str, int] = {}
    for res in results.values():
        level = str(res.get("risk_level") or "unknown")
        level_counts[level] = level_counts.get(level, 0) + 1

    return {
        # ★ 约定的 6 个字段
        "run_at": started_at.isoformat(timespec="seconds"),
        "mode": mode,
        "companies_processed": len(results),
        "alerts_found": len(alerts),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "status": status,
        # ---- 诊断信息 ----
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "force": force,
        "cache_disabled": no_cache,
        "window": _describe_window(days),
        "db_path": str(settings.db_file),
        "llm_model": settings.DEEPSEEK_MODEL,
        "llm_fake": settings.LLM_FAKE,
        "companies_requested": len(targets),
        "companies_skipped": skipped,
        "risk_level_counts": level_counts,
        "alert_companies": sorted(alerts.keys()),
        "errors": errors,
    }


def render_text_report(report: dict[str, Any], results: dict[str, dict[str, Any]]) -> str:
    """人可读版本。"""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("X-Ray 夜间批处理报告")
    lines.append("=" * 72)
    lines.append(f"运行时间    : {report['run_at']}")
    lines.append(f"模式        : {report['mode']}")
    lines.append(f"状态        : {report['status']}")
    lines.append(f"处理公司数  : {report['companies_processed']}")
    lines.append(f"命中预警数  : {report['alerts_found']}")
    lines.append(f"耗时        : {report['duration_seconds']} 秒")
    lines.append(f"公告窗口    : {report.get('window')}")
    lines.append(f"数据库      : {report['db_path']}")
    lines.append(
        f"模型        : {report['llm_model']}"
        + ("（FAKE 模式，未联网）" if report.get("llm_fake") else "")
    )
    lines.append("")

    counts = report.get("risk_level_counts") or {}
    if counts:
        lines.append("风险等级分布：")
        for level in ("high", "medium", "low", "unknown"):
            if counts.get(level):
                lines.append(f"  {level:<8} {counts[level]}")
        lines.append("")

    if report.get("companies_skipped"):
        skipped = report["companies_skipped"]
        shown = ", ".join(skipped[:10]) + (" …" if len(skipped) > 10 else "")
        lines.append(f"本次跳过 {len(skipped)} 家：{shown}")
        lines.append("")

    if report.get("errors"):
        lines.append("错误：")
        for err in report["errors"]:
            lines.append(f"  - {err}")
        lines.append("")

    if results:
        lines.append("-" * 72)
        lines.append("逐家结论（high/medium 优先）：")
        lines.append("-" * 72)

        def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
            level = str(item[1].get("risk_level"))
            rank = {"high": 0, "medium": 1, "low": 2}.get(level, 3)
            return (rank, item[0])

        for code, res in sorted(results.items(), key=sort_key):
            lines.append("")
            lines.append(f"■ {code}  [{res.get('risk_level')}]  来源={res.get('source')}")
            lines.append(f"  摘要: {res.get('summary')}")
            quotes = {q.get("id"): q for q in (res.get("evidence_quotes") or [])}
            for finding in res.get("findings") or []:
                lines.append(
                    f"  · [{finding.get('type')}/{finding.get('severity')}] "
                    f"{finding.get('title')}"
                )
                for eid in finding.get("evidence_ids") or []:
                    quote = quotes.get(eid)
                    if quote:
                        lines.append(f"      原文: {quote.get('source_quote')}")
    else:
        lines.append("（没有处理任何公司）")

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(
    *,
    demo: bool,
    dry_run: bool,
    force: bool,
    limit: int | None,
    days: int | None,
    no_cache: bool,
) -> int:
    """执行一轮批处理；返回进程退出码。"""
    started_at = datetime.now()
    started = time.perf_counter()
    errors: list[str] = []

    # ---- 1) 取公司列表 ----
    try:
        import db as db_module

        log(f"数据库：{db_module.db_path()}")
        stocks = db_module.get_stocks()
    except Exception as exc:  # noqa: BLE001
        log(f"无法读取公司列表：{type(exc).__name__}: {exc}")
        log("请确认 cninfo.db 存在（默认 data/cninfo.db，找不到会回退 ../data/cninfo.db），")
        log("或在 .env 里设置 DB_PATH 指向实际文件。")
        return 1

    if not stocks:
        log("数据库里没有查到任何公司代码（docs 表为空？）")
        return 1

    mode = MODE_DRY_RUN if dry_run else (MODE_DEMO if demo else MODE_FULL)

    # ---- 2) 决定处理范围 ----
    if demo:
        effective_limit = limit if limit is not None else DEMO_LIMIT
        targets = stocks[: max(1, effective_limit)]
    elif limit is not None:
        targets = stocks[: max(1, limit)]
    else:
        targets = list(stocks)

    target_set = set(targets)
    skipped = [c for c in stocks if c not in target_set]

    log(
        f"模式={mode}  库内公司 {len(stocks)} 家  本次处理 {len(targets)} 家"
        + (f"  跳过 {len(skipped)} 家" if skipped else "")
    )
    log(f"公告窗口：{_describe_window(days)}")

    # ---- 3) dry-run：只打印计划 ----
    if dry_run:
        log("[dry-run] 计划如下（不会调用 LLM、不会写任何文件）：")
        log(f"[dry-run]   数据库：{settings.db_file}")
        log(f"[dry-run]   窗口  ：{_describe_window(days)}")
        log(
            f"[dry-run]   模型  ：{settings.DEEPSEEK_MODEL}"
            + ("（FAKE，不会联网）" if settings.LLM_FAKE else "")
        )
        log(
            f"[dry-run]   缓存  ："
            + ("禁用" if no_cache else ("强制重算" if force else "启用当日缓存"))
        )
        try:
            for code in targets:
                count = len(
                    db_module.get_announcements(
                        code,
                        days=days,
                        limit=settings.ANALYSIS_MAX_ANNOUNCEMENTS,
                        body_chars=0,
                    )
                )
                log(f"[dry-run]   · {code}：{count} 条公告 → 将调用一次 LLM")
        except Exception as exc:  # noqa: BLE001
            log(f"[dry-run]   （公告统计失败：{type(exc).__name__}: {exc}）")
        log("[dry-run] 预计产出：")
        log(f"[dry-run]   {settings.log_dir / COMPANY_TEMPLATE.format(code='<code>')}")
        log(f"[dry-run]   {settings.log_dir / REPORT_JSON}")
        log(f"[dry-run]   {settings.log_dir / REPORT_TXT}")
        return 0

    # ---- 4) 逐家分析 ----
    from analyzer import analyze_company

    results: dict[str, dict[str, Any]] = {}
    for index, code in enumerate(targets, start=1):
        log(f"[{index}/{len(targets)}] 分析 {code} …")
        try:
            analysis = analyze_company(
                code,
                use_cache=not no_cache,
                force=force,
                days=days,
                max_announcements=settings.ANALYSIS_MAX_ANNOUNCEMENTS,
            )
        except Exception as exc:  # noqa: BLE001 —— 单家失败不影响整批
            logger.exception("分析 %s 异常", code)
            errors.append(f"{code}: {type(exc).__name__}: {exc}")
            analysis = {
                "version": 1,
                "cache_date": date.today().isoformat(),
                "risk_level": "unknown",
                "summary": f"分析失败：{type(exc).__name__}",
                "findings": [],
                "evidence_quotes": [],
                "source": "error",
                "llm_called": False,
            }

        results[code] = analysis

        # 落地单家结果
        try:
            write_json(company_path(code), analysis)
        except OSError as exc:
            errors.append(f"{code}: 写文件失败 {exc}")
            log(f"  写文件失败：{exc}")

        if analysis.get("source") in FAILED_SOURCES:
            errors.append(f"{code}: {analysis.get('summary')}")

        log(
            f"  → risk_level={analysis.get('risk_level')}  "
            f"findings={len(analysis.get('findings') or [])}  "
            f"quotes={len(analysis.get('evidence_quotes') or [])}  "
            f"source={analysis.get('source')}"
        )

    finished_at = datetime.now()
    duration = time.perf_counter() - started

    # ---- 5) 汇总状态 ----
    processed = len(results)
    succeeded = sum(1 for r in results.values() if r.get("source") in ("llm", "cache"))
    if processed == 0:
        status = "failed"
    elif succeeded == processed:
        status = "success"
    elif succeeded > 0:
        status = "partial"
    else:
        status = "failed"

    report = build_report(
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
        results=results,
        targets=targets,
        skipped=skipped,
        status=status,
        errors=errors,
        force=force,
        no_cache=no_cache,
        days=days,
    )

    # ---- 6) 写汇总报告 ----
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        write_json(settings.log_dir / REPORT_JSON, report)
        (settings.log_dir / REPORT_TXT).write_text(
            render_text_report(report, results), encoding="utf-8"
        )
    except OSError as exc:
        log(f"写汇总报告失败：{exc}")
        return 1

    log("")
    log(
        f"完成：{processed} 家，预警 {report['alerts_found']} 家，"
        f"状态 {status}，耗时 {duration:.1f} 秒"
    )
    log(f"汇总报告：{settings.log_dir / REPORT_TXT}")
    log(f"逐家结果：{settings.log_dir / COMPANY_TEMPLATE.format(code='<code>')}")
    if errors:
        log(f"注意：有 {len(errors)} 条错误，详见报告")
    return 0 if status in ("success", "partial") else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_night_batch.py",
        description="X-Ray 夜间批处理：遍历公司 → LLM 分析 → 落地 JSON 报告",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=f"只处理前 {DEMO_LIMIT} 家公司（演示用，速度快）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划做什么，不调 LLM、不写文件",
    )
    parser.add_argument("--force", action="store_true", help="忽略当日缓存，强制重新分析")
    parser.add_argument("--no-cache", action="store_true", help="完全不读写缓存")
    parser.add_argument("--limit", type=int, default=None, help="最多处理几家公司")
    parser.add_argument(
        "--days",
        type=int,
        default=-1,
        help="公告时间窗口天数；默认 -1 表示取配置 ANALYSIS_WINDOW_DAYS（当前为不限）",
    )
    parser.add_argument(
        "--all-dates",
        action="store_true",
        help="明确不做时间过滤（等价于 --days 0，便于演示时说明）",
    )
    parser.add_argument("--log-level", default=None, help="日志级别（DEBUG/INFO/WARNING）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必须 >= 1")
    if args.days < -1:
        parser.error("--days 必须是正整数，或用 -1 表示取配置值")

    # days 语义：-1 = 取配置；0 = 明确不过滤；>0 = 该天数窗口
    if args.all_dates or args.days == 0:
        days: int | None = None
    elif args.days == -1:
        days = settings.ANALYSIS_WINDOW_DAYS
    else:
        days = args.days

    if args.log_level:
        os.environ["LOG_LEVEL"] = args.log_level.upper()

    configure_logging()

    try:
        return run(
            demo=args.demo,
            dry_run=args.dry_run,
            force=args.force,
            limit=args.limit,
            days=days,
            no_cache=args.no_cache,
        )
    except KeyboardInterrupt:
        log("已被用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
