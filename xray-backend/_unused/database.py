"""X-Ray 企业穿透分析 · 数据库层。

SQLAlchemy 2.0 + SQLite（开发期）。

约定（需求[五]）：
  * 所有查询必须通过 stock_code 路由，绝不能跨公司串数据；
  * 每张表都有 updated_at；
  * answer_cache 用 version 原子切换。

生产期换 PostgreSQL：只需改 .env 的 DB_URL，本模块通过抽象层隔离。
"""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 声明式基类
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """全部 ORM 模型的基类（SQLAlchemy 2.0 风格）。"""

    def to_dict(self) -> dict[str, Any]:
        """便于路由层组装响应；不返回 SQLAlchemy 内部状态。"""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        pk = getattr(self, "id", None)
        code = getattr(self, "stock_code", None)
        label = f"id={pk}" if pk is not None else f"stock_code={code}"
        return f"<{type(self).__name__} {label}>"


# ---------------------------------------------------------------------------
# engine / SessionLocal
# ---------------------------------------------------------------------------


def _build_engine() -> Engine:
    """按 DB_URL 构建 engine；SQLite 需要额外参数。"""
    kwargs: dict[str, Any] = {
        "echo": settings.DB_ECHO,
        "future": True,
        "pool_pre_ping": True,
    }
    if settings.is_sqlite:
        # FastAPI 在线程池里跑同步 Session，SQLite 必须放开线程检查
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    else:
        # PostgreSQL 等：连接池回收，避免夜间长跑批拿到失效连接
        kwargs["pool_recycle"] = 1800

    return create_engine(settings.DB_URL, **kwargs)


engine: Engine = _build_engine()

#: 统一的会话工厂；expire_on_commit=False 便于 commit 后继续读取对象属性
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


# ---------------------------------------------------------------------------
# SQLite 强化：外键约束 + WAL（夜间写、白天读并发）
# ---------------------------------------------------------------------------


@event.listens_for(Engine, "connect")
def _sqlite_on_connect(dbapi_connection: Any, connection_record: Any) -> None:
    """仅对 SQLite 生效的 PRAGMA：外键、WAL、忙等待。"""
    if not settings.is_sqlite:
        return
    module = type(dbapi_connection).__module__ or ""
    if "sqlite" not in module:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
    except Exception:  # pragma: no cover - 内存库/只读库可能不支持
        logger.debug("SQLite PRAGMA 设置跳过", exc_info=True)
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# 会话与生命周期
# ---------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入：每请求一个 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/夜间任务用的事务作用域：异常自动回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）。导入 models 以注册全部表定义。"""
    import models  # noqa: F401  —— 必须导入，表才会注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成: %s（%d 张表）", settings.DB_URL, len(Base.metadata.tables))


def drop_all() -> None:
    """仅测试用：清空全部表。"""
    Base.metadata.drop_all(bind=engine)


def reset_db() -> None:
    """仅测试用：drop + create。"""
    drop_all()
    init_db()


def healthcheck() -> bool:
    """探活：执行 SELECT 1，失败返回 False（供 /health 使用）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("数据库探活失败")
        return False


def table_names() -> list[str]:
    """当前元数据里的全部表名（自检/测试用）。"""
    import models  # noqa: F401

    return sorted(Base.metadata.tables.keys())


__all__ = [
    "Base",
    "SessionLocal",
    "drop_all",
    "engine",
    "get_db",
    "healthcheck",
    "init_db",
    "reset_db",
    "session_scope",
    "table_names",
]
