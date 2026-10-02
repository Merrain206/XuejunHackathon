"""X-Ray 企业穿透分析 · 配置层。

技术栈：pydantic-settings（pydantic v2）+ python-dotenv。

硬性约束：
  * API key 全部走 .env，不许硬编码；
  * 所有配置项都带默认值 —— 缺 .env 也能 import 成功。

本轮改造后的变化：
  * **新增** DB_PATH —— 指向 cninfo.db（公告原文库），唯一数据源；
  * **删除** 旧的 ORM 连接串、真假数据开关、5 个外部渠道 URL、调度器相关配置
    （不再有 ORM、不再有数据抓取适配层、不再有定时任务）。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

#: xray-backend/ 目录（本文件所在目录）
BASE_DIR: Path = Path(__file__).resolve().parent

#: .env 默认位置
ENV_FILE: Path = BASE_DIR / ".env"

#: 夜间跑批日志 / 缓存目录
LOG_DIR: Path = BASE_DIR / "logs"
CACHE_DIR: Path = BASE_DIR / "cache"
DATA_DIR: Path = BASE_DIR / "data"


# ---------------------------------------------------------------------------
# 设置模型
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """全部运行时配置；每一项都有默认值。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ---------------- 应用 ----------------
    APP_NAME: str = "X-Ray 企业穿透分析"
    APP_VERSION: str = "0.5.0"
    DEBUG: bool = False
    #: 接口前缀必须为空：路径就是 /companies/{stock_code}/ask
    API_PREFIX: str = ""
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # ---------------- 数据源：cninfo.db ----------------
    #: 相对路径锚定到 xray-backend/；找不到时会自动尝试 ../data/cninfo.db
    DB_PATH: str = "data/cninfo.db"
    #: BACKEND_NEXT_STEPS.md 规定用 DATABASE_PATH 配置库位置。
    #: 两者都支持：DATABASE_PATH 优先，未设置时回落到 DB_PATH（向后兼容）。
    DATABASE_PATH: str = ""
    #: true = 只认 DB_PATH，不做候选回退（测试"库不存在"场景时用）
    DB_PATH_STRICT: bool = False
    #: 喂给 LLM 的公告时间窗口（天）。**None = 不按时间过滤**（推荐）。
    #:
    #: ⚠️ 实测：真实库里最新一份带可解析日期的公告距今已超过 365 天，
    #:    因此固定 365 天窗口会把**全部有日期的公告**筛掉，只留下 58 条
    #:    "文件名无日期"的公告 —— 与"取最近的公告"这个直觉正好相反。
    #:    故默认为 None（取全部，按公告日期倒序由 ANALYSIS_MAX_ANNOUNCEMENTS 截断）。
    ANALYSIS_WINDOW_DAYS: int | None = None
    #: 拿不到单篇公告直链时的回退：巨潮资讯网该公司公告列表页（真实可达）
    CNINFO_LIST_URL: str = "https://www.cninfo.com.cn/new/fulltextSearch/full"
    #: 喂给 LLM 的公告条数上限（太多会撑爆上下文 / 太贵）
    ANALYSIS_MAX_ANNOUNCEMENTS: int = 30
    #: 单条公告正文截断长度（字符）
    ANNOUNCEMENT_MAX_CHARS: int = 4000

    # ---------------- DeepSeek 大模型 ----------------
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    #: 该接入点实测可用模型：deepseek-flash / deepseek-v4-pro
    #: （deepseek-chat 与 deepseek-reasoner 也仍被接受）
    DEEPSEEK_MODEL: str = "deepseek-flash"
    #: 是否开启思考模式（extra_body={"thinking":{"type":"enabled"}}）。
    #: 实测 deepseek-flash **默认就返回 reasoning_content**，这里显式声明以保证
    #: 行为不随服务端默认值变化。
    DEEPSEEK_THINKING: bool = True
    #: 思考强度；None 时不传该参数（部分模型不支持）
    DEEPSEEK_REASONING_EFFORT: str | None = "high"
    REQUEST_TIMEOUT_MS: int = 120_000
    #: ⚠️ 思考模式（deepseek-flash + thinking）下，**reasoning token 也从这个上限里扣**。
    #: 实测：分析 2 条公告并输出 JSON，推理就吃掉 900+ token，正文直接为空
    #: （finish_reason=stop 但 content 为空 → 被判定 empty_response）。
    #: 因此上限要给够；上限只是封顶不是目标，给大不会变慢、也不会多花钱。
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.2
    #: false 时不做 LLM 判断，直接给出「无足够信息」的降级结论
    LLM_ENABLED: bool = True
    #: 测试开关：true 时返回固定假响应，不发起任何网络请求
    LLM_FAKE: bool = False
    #: 喂 prompt 时是否按「[第 N 页]」给出分页正文（让模型能回填真实 source_page）
    PROMPT_PAGED_TEXT: bool = True
    #: 每页喂给模型的字符上限（按页切分后单页通常很短）
    PROMPT_CHARS_PER_PAGE: int = 1200

    # ---------------- 分析缓存 ----------------
    #: 缓存目录（同一家公司同一天不重复调用 LLM）
    CACHE_ENABLED: bool = True

    # ---------------- charts（响应体的图表数组） ----------------
    CHARTS_ENABLED: bool = True
    CHARTS_MAX: int = 4

    # ---------------- 查询链路 ----------------
    ASK_HIT_BUDGET_MS: int = 100
    MATCH_SIMILARITY_THRESHOLD: float = 0.8

    # ---------------- 管理接口 ----------------
    #: POST /admin/refresh 的简单 token 校验（也可用 X-Admin-Token 请求头传入）
    ADMIN_TOKEN: str = "xray-demo-token"

    # ---------------- 日志 ----------------
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = ""  # 留空 = logs/xray.log（RotatingFileHandler）
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 3

    # ---------------- CORS ----------------
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---------------- 前端固定的建议问题 ----------------
    DEFAULT_QUESTIONS: tuple[str, ...] = (
        "你的收入结构发生了什么变化？",
        "你最近真的赚钱吗？",
        "目前最值得关注的风险是什么？",
        "你的员工喜欢吃水果吗？",
    )

    # ------------------------------------------------------------------
    # 校验器（pydantic v2：field_validator，禁止 v1 的 @validator）
    # ------------------------------------------------------------------

    @field_validator("API_PREFIX")
    @classmethod
    def _forbid_legacy_prefix(cls, value: str) -> str:
        """接口路径禁止 /api/v1 前缀。"""
        cleaned = (value or "").strip().rstrip("/")
        if cleaned in {"/api/v1", "api/v1"}:
            raise ValueError(
                "API_PREFIX 不得为 /api/v1：接口路径必须是 /companies/{stock_code}/ask"
            )
        return cleaned

    @field_validator("DB_PATH")
    @classmethod
    def _check_db_path(cls, value: str) -> str:
        path = (value or "").strip()
        if not path:
            raise ValueError("DB_PATH 不能为空")
        return path

    @model_validator(mode="after")
    def _resolve_database_path_alias(self) -> "Settings":
        """DATABASE_PATH（规范名）优先；未设置时保持 DB_PATH 的默认值。

        两个名字都支持是为了兼容既有 .env / 测试；规范名来自
        BACKEND_NEXT_STEPS.md：DATABASE_PATH=...\\cninfo.db
        """
        explicit = (self.DATABASE_PATH or "").strip()
        if explicit:
            object.__setattr__(self, "DB_PATH", explicit)
        else:
            object.__setattr__(self, "DATABASE_PATH", self.DB_PATH)
        return self

    @field_validator("LOG_LEVEL")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        level = (value or "INFO").strip().upper()
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL 必须是 {sorted(allowed)} 之一，收到 {value!r}")
        return level

    @field_validator("PORT")
    @classmethod
    def _check_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError(f"PORT 必须在 1-65535 之间，收到 {value}")
        return value

    @field_validator("MATCH_SIMILARITY_THRESHOLD")
    @classmethod
    def _check_threshold(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError(f"MATCH_SIMILARITY_THRESHOLD 必须在 (0, 1] 之间，收到 {value}")
        return value

    @field_validator("ANALYSIS_MAX_ANNOUNCEMENTS", "ANNOUNCEMENT_MAX_CHARS")
    @classmethod
    def _check_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"必须是正整数，收到 {value}")
        return value

    @field_validator("DEFAULT_QUESTIONS")
    @classmethod
    def _check_questions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(q.strip() for q in value if q and q.strip())
        if not cleaned:
            raise ValueError("DEFAULT_QUESTIONS 不能为空")
        return cleaned

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------

    @property
    def db_file(self) -> Path:
        """cninfo.db 的路径。

        解析顺序（自适应，不动用户的数据库文件）：
          1. `DATABASE_PATH` / `DB_PATH` 指向的文件确实存在（或显式给了绝对路径）→ 用它；
          2. 否则依次尝试候选：
             ../cninfo.db      （★ 仓库根目录 —— 当前布局，见 BACKEND_INTEGRATION_TASKS.md）
             data/cninfo.db    （相对 xray-backend/）
             ../data/cninfo.db （库放在仓库根的 data/ 下）
          3. 都不存在 → 返回配置的路径（让 db.py 抛出带明确路径的错误，
             且绝不静默创建空库）。

        DB_PATH_STRICT=true 时跳过第 2 步（只认配置的路径），
        便于测试"库文件不存在 → 报清晰错误"这类场景。
        """
        configured = Path(self.DB_PATH)
        if not configured.is_absolute():
            configured = BASE_DIR / configured
        if configured.is_file() or self.DB_PATH_STRICT:
            return configured

        for candidate in self._db_candidates():
            if candidate.is_file():
                return candidate
        return configured

    def _db_candidates(self) -> list[Path]:
        """候选数据库路径（顺序即优先级）。"""
        return [
            # ★ 仓库根目录：`git clone` 后把 cninfo.db 放在仓库根即可直接跑，
            #   后端无需移动数据库文件（本轮联调的第一号阻塞项）。
            BASE_DIR.parent / "cninfo.db",
            BASE_DIR / "data" / "cninfo.db",
            BASE_DIR.parent / "data" / "cninfo.db",
        ]

    @property
    def db_candidates(self) -> list[Path]:
        """所有候选路径（错误提示里列出来，便于用户对照）。"""
        configured = Path(self.DB_PATH)
        if not configured.is_absolute():
            configured = BASE_DIR / configured
        return [configured, *self._db_candidates()]

    @property
    def cache_dir(self) -> Path:
        return CACHE_DIR

    @property
    def log_dir(self) -> Path:
        return LOG_DIR

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_ready(self) -> bool:
        """DeepSeek 是否可用于生成结论（LLM_FAKE=true 也算就绪）。"""
        if self.LLM_FAKE:
            return True
        return bool(self.LLM_ENABLED and self.DEEPSEEK_API_KEY and self.DEEPSEEK_MODEL)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def log_file_path(self) -> Path:
        if self.LOG_FILE.strip():
            candidate = Path(self.LOG_FILE.strip())
            return candidate if candidate.is_absolute() else (BASE_DIR / candidate)
        return LOG_DIR / "xray.log"

    def describe(self) -> dict[str, object]:
        """给 /health 用的能力报告；绝不返回密钥明文。"""
        return {
            "app": self.APP_NAME,
            "version": self.APP_VERSION,
            "debug": self.DEBUG,
            "db_path": str(self.db_file),
            "cache_enabled": self.CACHE_ENABLED,
            "cache_dir": str(self.cache_dir),
            "llm_enabled": self.LLM_ENABLED,
            "llm_key_configured": bool(self.DEEPSEEK_API_KEY),
            "llm_model": self.DEEPSEEK_MODEL,
            "llm_fake": self.LLM_FAKE,
            "charts_enabled": self.CHARTS_ENABLED,
            "scheduling": "由系统 cron 调 scripts/run_night_batch.py（应用内不含调度器）",
        }


# ---------------------------------------------------------------------------
# 单例访问
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例；测试可用 get_settings.cache_clear() 重置。"""
    return Settings()


#: 模块级便捷引用
settings: Settings = get_settings()


def reload_settings() -> Settings:
    """重新读取 .env（测试用）。"""
    get_settings.cache_clear()
    global settings
    settings = get_settings()
    return settings


__all__ = [
    "BASE_DIR",
    "CACHE_DIR",
    "DATA_DIR",
    "ENV_FILE",
    "LOG_DIR",
    "Settings",
    "get_settings",
    "reload_settings",
    "settings",
]

if __name__ == "__main__":  # pragma: no cover - 手动自检
    import json

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    print(json.dumps(get_settings().describe(), ensure_ascii=False, indent=2))
