"""X-Ray 企业穿透分析 · FastAPI 入口。

职责：FastAPI 入口、lifespan、CORS、异常处理、路由注册。

启动流程（lifespan）：
  1. configure_logging()   —— logging + RotatingFileHandler
  2. 检查 cninfo.db 是否存在 —— 缺失时**只警告不退出**（服务照常起，
     请求时才给出清晰的报错），这样 /health 依然可用、便于排查
  3. 日志打印 DB 覆盖情况（股票数 / 公告数），让人一眼看出连的是哪个库

⚠️ 结构说明（本轮改造后）：
  * 已**拆除定时调度** —— 生产环境由系统 cron 调用
    `scripts/run_night_batch.py`；demo 环境手动触发。
  * 已**移除 ORM 与结构化财务表** —— 数据源是 cninfo.db 里的公告原文，
    统一通过 db.py 访问，LLM 判断逻辑统一在 analyzer.py。

⚠️ 接口路径无任何前缀：POST /companies/{stock_code}/ask
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import settings
from logging_config import configure_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 启动自检（不阻断启动）
# ---------------------------------------------------------------------------


def _check_database() -> None:
    """启动时探测数据库；失败只记日志，不阻止服务启动。"""
    try:
        import db as db_module
    except Exception:  # noqa: BLE001
        logger.exception("db 模块导入失败")
        return

    try:
        stocks = db_module.get_stocks()
        total = db_module.count_announcements()
        logger.info(
            "数据库就绪：%s（股票 %d 家，公告 %d 条）",
            db_module.db_path(),
            len(stocks),
            total,
        )
        if not stocks:
            logger.warning("数据库里没有查到任何股票代码，请确认表结构是否匹配（见 db.py）")
    except db_module.DatabaseNotReadyError as exc:
        logger.warning("数据库不可用：%s", exc)
        logger.warning("服务仍会启动；调用接口时会返回明确的错误提示。")
    except Exception:  # noqa: BLE001
        logger.exception("数据库自检异常（服务仍会启动）")


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log_path = configure_logging()
    logger.info("=" * 72)
    logger.info("%s v%s 启动中（日志: %s）", settings.APP_NAME, settings.APP_VERSION, log_path)
    logger.info("配置：%s", settings.describe())

    _check_database()

    logger.info("%s 已就绪", settings.APP_NAME)
    logger.info("提示：demo 预读请执行  python scripts/run_night_batch.py --demo")
    try:
        yield
    finally:
        logger.info("%s 已停止", settings.APP_NAME)


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """应用工厂（测试可多次调用拿到干净实例）。"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "面向答辩/演示的招股书核验问答系统。\n\n"
            "数据源是 cninfo.db 中的公告原文；结论由 DeepSeek 基于**原文引用**生成，"
            "prompt 强制要求「找不到依据就说无足够信息」。\n"
            "响应体顶层固定 6 个字段："
            "answer / claims / signals / charts / evidence / suggested_questions。"
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-XRay-Cache", "X-XRay-Elapsed-Ms", "X-XRay-Answer-Source"],
    )

    # ---------------- 统一异常处理：绝不让前端白屏 ----------------

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.info("参数校验失败 %s: %s", request.url.path, exc.errors())
        return JSONResponse(
            status_code=422,
            content={"error": "请求参数不合法", "detail": str(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": detail, "detail": ""})

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        detail = f"{type(exc).__name__}: {exc}" if settings.DEBUG else ""
        return JSONResponse(
            status_code=500,
            content={"error": "服务器内部错误", "detail": detail},
        )

    # ---------------- 路由 ----------------

    from ask import router as xray_router

    # prefix 恒为空 —— 接口必须就是 /companies/{stock_code}/ask
    app.include_router(xray_router, prefix=settings.API_PREFIX)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
            "ask_example": "POST /companies/688583/ask",
            "demo": "python scripts/run_night_batch.py --demo",
        }

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_config=None,  # 交给 logging_config 统一控制
    )
