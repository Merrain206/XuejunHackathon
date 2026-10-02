"""X-Ray 企业穿透分析 · 日志配置。

需求[二]明确要求：logging + RotatingFileHandler。

设计：
  * 控制台 + 轮转文件双输出；
  * 文件默认 logs/xray.log，>=5MB 轮转、保留 3 份（可通过 .env 覆盖）；
  * configure_logging() 幂等 —— lifespan 与脚本都会调用，重复调用不会叠加 handler
    （否则日志会成倍重复打印，是这类项目最常见的"小毛病"）。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from config import settings

_configured = False

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(force: bool = False) -> Path:
    """配置根 logger，返回日志文件路径。幂等。"""
    global _configured
    if _configured and not force:
        return settings.log_file_path

    log_path = settings.log_file_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    # 清掉已有 handler（force 或重复调用时避免日志重复）
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(settings.LOG_LEVEL)
    root.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_path),
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(settings.LOG_LEVEL)
        root.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - 目录不可写时只保留控制台
        root.warning("无法创建日志文件 %s，仅输出到控制台: %s", log_path, exc)

    # 让 uvicorn 的访问日志走同一套格式
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    _configured = True
    root.debug("日志已配置：level=%s file=%s", settings.LOG_LEVEL, log_path)
    return log_path


def get_logger(name: str) -> logging.Logger:
    """取 logger（必要时先完成配置）。"""
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


__all__ = ["configure_logging", "get_logger"]
