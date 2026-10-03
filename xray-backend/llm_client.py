"""X-Ray 企业穿透分析 · DeepSeek 大模型客户端。

用 **OpenAI SDK** 接入 DeepSeek（官方兼容 OpenAI 协议的 `base_url`）。

设计原则（对应需求「接入 DeepSeek + 失败降级」）：
  * 只做一件事：把「问题 + 检索到的 evidence」组装成 Prompt，要一段 answer 文字；
  * **失败绝不抛给前端**：任何异常都返回 LLMResult(ok=False)，由调用方回退到确定性模板；
  * LLM_FAKE=true 时返回固定假响应、不发起任何网络请求 —— 供 pytest 与离线演示使用；
  * 未配置 key 时不尝试联网，直接给出可读的降级原因。

配置（.env）：
    DEEPSEEK_API_KEY=sk-xxx
    DEEPSEEK_BASE_URL=https://api.deepseek.com
    DEEPSEEK_MODEL=deepseek-chat
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from config import settings

logger = logging.getLogger(__name__)

#: LLM_FAKE=true 时返回的固定文案（测试与离线演示用）
FAKE_ANSWER = "（离线模拟回答）该公司公开披露数据已核验，未发现超出证据范围的结论。"

#: 降级原因文案（面向最终用户，友好、不暴露内部细节）
FALLBACK_MESSAGES: dict[str, str] = {
    "disabled": "大模型生成未启用，以下为基于公开数据的模板回答。",
    "no_api_key": "未配置 DeepSeek API Key，以下为基于公开数据的模板回答。",
    "timeout": "大模型服务响应超时，以下为基于公开数据的模板回答。",
    "auth_error": "大模型服务鉴权失败（请检查 DEEPSEEK_API_KEY），以下为基于公开数据的模板回答。",
    "rate_limited": "大模型服务当前限流，以下为基于公开数据的模板回答。",
    "network_error": "大模型服务暂时不可用，以下为基于公开数据的模板回答。",
    "empty_response": "大模型未返回有效内容，以下为基于公开数据的模板回答。",
    "error": "大模型生成失败，以下为基于公开数据的模板回答。",
}


# ---------------------------------------------------------------------------
# 结果容器
# ---------------------------------------------------------------------------


@dataclass
class LLMResult:
    """一次 answer 生成的结果。"""

    text: str
    ok: bool = False
    #: ok=True 时的来源：deepseek / fake；ok=False 时的原因键（见 FALLBACK_MESSAGES）
    source: str = "error"
    fallback_reason: str | None = None
    model: str = ""
    elapsed_ms: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_fallback(self) -> bool:
        return not self.ok

    @property
    def notice(self) -> str:
        """给 answer 文案用的降级说明（ok 时为空串）。"""
        if self.ok:
            return ""
        return FALLBACK_MESSAGES.get(self.fallback_reason or "error", FALLBACK_MESSAGES["error"])

    def to_log(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 调用可观测性（测试断言「命中缓存不调用 LLM」用）
# ---------------------------------------------------------------------------

CALL_COUNT = 0


def reset_call_count() -> None:
    """重置调用计数（测试用）。"""
    global CALL_COUNT
    CALL_COUNT = 0


def call_count() -> int:
    """累计调用次数（含 fake 模式）。"""
    return CALL_COUNT


def _bump_call_count() -> None:
    global CALL_COUNT
    CALL_COUNT += 1


# ---------------------------------------------------------------------------
# Prompt 组装
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "你是企业风险分析助手，服务于招股书核验场景。\n"
    "硬性规则：\n"
    "1. 只能基于给定的【证据】作答，不得引入证据之外的数字、事实或推测；\n"
    "2. 不得编造来源、链接或字段；证据不足时必须明确说「数据不足」；\n"
    "3. 只输出 2-4 句中文结论，不要 markdown、不要列表、不要标题；\n"
    "4. 若涉及风险信号，须点明信号编号（S1/S2/S3/S4）与方向；\n"
    "5. 不做投资建议。"
)


def build_evidence_block(evidence: Sequence[dict[str, Any]], limit: int = 8) -> str:
    """把 evidence 列表压成 Prompt 里的证据块。

    每条都带编号与出处，便于模型引用、也便于人工核对。
    """
    if not evidence:
        return "（无可用证据）"

    lines: list[str] = []
    for item in list(evidence)[:limit]:
        ev_id = item.get("id") or item.get("evidence_id") or "?"
        bits = [f"[{ev_id}]"]
        if item.get("category"):
            bits.append(f"类别={item['category']}")
        if item.get("period"):
            bits.append(f"期间={item['period']}")
        lines.append(" ".join(bits))
        if item.get("content"):
            lines.append(f"  内容：{item['content']}")
        if item.get("source_quote"):
            lines.append(f"  原文摘录：{item['source_quote']}")
        provenance = " / ".join(
            str(x) for x in (item.get("document_title"), item.get("source_page")) if x
        )
        if provenance:
            lines.append(f"  出处：{provenance}")
    return "\n".join(lines)


def build_user_prompt(
    question: str,
    evidence: Sequence[dict[str, Any]],
    *,
    company_name: str = "",
    stock_code: str = "",
    signals: Sequence[dict[str, Any]] = (),
) -> str:
    """组装用户 Prompt：问题 + 公司 + 已判定信号 + 证据。"""
    parts: list[str] = []
    if company_name or stock_code:
        parts.append(f"公司：{company_name}（{stock_code}）")
    parts.append(f"问题：{question}")

    if signals:
        signal_lines = [
            f"- {s.get('type')} {s.get('title')}（{s.get('severity')}）：{s.get('description')}"
            for s in signals
        ]
        parts.append("规则引擎已判定的风险信号（必须采纳，不要否定）：\n" + "\n".join(signal_lines))
    else:
        parts.append("规则引擎未命中任何风险信号。")

    parts.append("【证据】\n" + build_evidence_block(evidence))
    parts.append("请基于以上证据，用 2-4 句中文给出结论。")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 错误归类
# ---------------------------------------------------------------------------


def classify_error(exc: BaseException) -> str:
    """把异常归类成降级原因键（不依赖 openai 具体异常类型，便于测试注入）。"""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return "timeout"
    if "authentication" in name or "permission" in name or "401" in text or "403" in text:
        return "auth_error"
    if "ratelimit" in name or "429" in text or "rate limit" in text:
        return "rate_limited"
    if "connection" in name or "connect" in text or "network" in text or "dns" in text:
        return "network_error"
    return "error"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def generate_answer(
    question: str,
    evidence: Sequence[dict[str, Any]],
    *,
    company_name: str = "",
    stock_code: str = "",
    signals: Sequence[dict[str, Any]] = (),
    prompt_override: str | None = None,
    system_prompt_override: str | None = None,
    thinking: bool | None = None,
    json_mode: bool = False,
) -> LLMResult:
    """调用 DeepSeek 生成回答；**永不抛异常**。

    两种用法：
      * 默认：按「问题 + 证据」组装问答 prompt（ask.py 的实时问答）；
      * `prompt_override`：直接使用调用方给的完整 user prompt
        （analyzer.py 的公告风险分析用，需要 JSON 输出格式的专门指令）。
      * `system_prompt_override`：调用方有独立输出契约时覆盖默认 system prompt；
        留空时保持现有风险分析行为。

    :param thinking: 是否开启思考模式。**None = 用 settings.DEEPSEEK_THINKING**。
        ⚠️ 实测（deepseek-flash @ 本接入点）：思考模式对"写几句话结论"有帮助，
           但对"严格 JSON 抽取"是**有害**的 —— 模型会一直推演、把整个 max_tokens
           预算耗在 reasoning 上，正文永远是空字符串（finish_reason=length）。
           因此 analyzer 的 JSON 分析显式传 thinking=False。
    :param json_mode: 请求兼容 OpenAI 的原生 JSON Object 输出；动态结构化问答开启，
        普通文本回答保持关闭。

    返回 LLMResult：ok=True 时 text 为模型输出；ok=False 时调用方必须走降级路径，
    并把 result.notice 拼进回答让用户知道发生了什么。
    """
    started = time.perf_counter()

    # ---- 测试/离线：固定假响应，不联网 ----
    if settings.LLM_FAKE:
        _bump_call_count()
        return LLMResult(
            text=FAKE_ANSWER,
            ok=True,
            source="fake",
            model="fake-model",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    # ---- 未启用 ----
    if not settings.LLM_ENABLED:
        return LLMResult(text="", ok=False, source="fallback", fallback_reason="disabled",
                         model=settings.DEEPSEEK_MODEL)

    # ---- 未配置 key：不尝试联网 ----
    if not settings.DEEPSEEK_API_KEY:
        return LLMResult(text="", ok=False, source="fallback", fallback_reason="no_api_key",
                         model=settings.DEEPSEEK_MODEL)

    if prompt_override is not None:
        prompt = prompt_override
    else:
        prompt = build_user_prompt(
            question, evidence, company_name=company_name, stock_code=stock_code, signals=signals
        )

    try:
        from openai import OpenAI  # 延迟导入：未安装时也能启动服务

        # api_key 显式传值（缺失时给占位串），避免 SDK 回落到环境变量 OPENAI_API_KEY
        client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY or "EMPTY",
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.REQUEST_TIMEOUT_MS / 1000,
            max_retries=1,
        )
        _bump_call_count()
        # 思考模式：DeepSeek 用 extra_body 传 thinking。
        # reasoning_effort 只在配置了才带（部分模型/接入点不接受该参数）。
        use_thinking = settings.DEEPSEEK_THINKING if thinking is None else thinking
        extra_body: dict[str, Any] = {}
        if use_thinking:
            extra_body["thinking"] = {"type": "enabled"}
        if use_thinking and settings.DEEPSEEK_REASONING_EFFORT:
            extra_body["reasoning_effort"] = settings.DEEPSEEK_REASONING_EFFORT

        create_kwargs: dict[str, Any] = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt_override or SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": settings.LLM_MAX_TOKENS,
        }
        if extra_body:
            create_kwargs["extra_body"] = extra_body
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**create_kwargs)
        content = (response.choices[0].message.content or "").strip()
        # 思考模式下模型会把推理过程放在 reasoning_content，正文仍是 content；
        # 若正文为空但推理非空，说明只产出了思考，不能当作有效答案。
        reasoning = ""
        try:
            reasoning = (getattr(response.choices[0].message, "reasoning_content", "") or "")
        except (AttributeError, IndexError):
            reasoning = ""
        usage: dict[str, Any] = {}
        if getattr(response, "usage", None) is not None:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
            details = getattr(response.usage, "completion_tokens_details", None)
            if details is not None:
                usage["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
        try:
            usage["finish_reason"] = getattr(response.choices[0], "finish_reason", None)
        except (AttributeError, IndexError):
            pass
        if reasoning:
            # 只记长度，不落库整段思考（体积大且无核验价值）
            usage["reasoning_chars"] = len(reasoning)
        if not content:
            logger.warning("DeepSeek 返回空内容")
            return LLMResult(text="", ok=False, source="fallback", fallback_reason="empty_response",
                             model=settings.DEEPSEEK_MODEL, usage=usage,
                             elapsed_ms=(time.perf_counter() - started) * 1000)
        return LLMResult(
            text=content,
            ok=True,
            source="deepseek",
            model=settings.DEEPSEEK_MODEL,
            usage=usage,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    except ImportError as exc:
        logger.error("未安装 openai SDK，无法调用 DeepSeek: %s", exc)
        return LLMResult(text="", ok=False, source="fallback", fallback_reason="error",
                         model=settings.DEEPSEEK_MODEL, error=f"ImportError: {exc}",
                         elapsed_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都降级，绝不抛给前端
        reason = classify_error(exc)
        logger.warning("DeepSeek 调用失败（%s），降级为模板回答: %s", reason, exc)
        return LLMResult(
            text="",
            ok=False,
            source="fallback",
            fallback_reason=reason,
            model=settings.DEEPSEEK_MODEL,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )


__all__ = [
    "CALL_COUNT",
    "FAKE_ANSWER",
    "FALLBACK_MESSAGES",
    "SYSTEM_PROMPT",
    "LLMResult",
    "build_evidence_block",
    "build_user_prompt",
    "call_count",
    "classify_error",
    "generate_answer",
    "reset_call_count",
]
