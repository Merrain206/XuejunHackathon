"""LLM 客户端测试：Prompt 组装、错误分类、失败降级。

（旧版 test_api_cache.py / test_api_deepseek.py 依赖已归档的
 models / mock_data / tasks.night_batch / datasource，已删除；
  其中与新架构相关的覆盖迁移到这里，缓存与批处理部分在 test_api_batch.py。）
"""

from __future__ import annotations

import pytest

import llm_client


class _FakeSettings:
    """只覆写需要的字段，其余透传真实 settings。"""

    def __init__(self, **overrides):
        from config import settings as real

        self._real = real
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._real, name)


@pytest.fixture()
def patch_settings(monkeypatch):
    def _apply(**overrides):
        monkeypatch.setattr(llm_client, "settings", _FakeSettings(**overrides))

    return _apply


# ---------------------------------------------------------------------------
# 证据块与 Prompt 组装
# ---------------------------------------------------------------------------


def test_build_evidence_block_contains_ids_and_quotes():
    evidence = [
        {
            "id": "EV-001",
            "category": "financial",
            "period": "2024-04-19",
            "content": "营业收入三年递增。",
            "source_quote": "营业收入 2024 年 1,320,000,000 元。",
            "document_title": "2024 年年度报告",
            "source_page": 26,
        }
    ]
    block = llm_client.build_evidence_block(evidence)
    assert "EV-001" in block
    assert "营业收入三年递增。" in block
    assert "1,320,000,000" in block
    assert "2024 年年度报告" in block


def test_build_evidence_block_handles_empty():
    assert "无可用证据" in llm_client.build_evidence_block([])


def test_user_prompt_includes_question_signals_and_evidence():
    prompt = llm_client.build_user_prompt(
        "该公司的利润含金量如何？",
        [{"id": "EV-001", "content": "经营现金流由正转负。"}],
        company_name="示例公司",
        stock_code="688583",
        signals=[
            {
                "type": "S1",
                "title": "利润含金量疑云",
                "severity": "high",
                "description": "净利润增长但经营现金流下降。",
            }
        ],
    )
    assert "该公司的利润含金量如何？" in prompt
    assert "688583" in prompt
    assert "S1" in prompt and "利润含金量疑云" in prompt
    assert "EV-001" in prompt
    assert "不要否定" in prompt  # 明确要求采纳规则引擎结论


def test_user_prompt_states_no_signal_case():
    prompt = llm_client.build_user_prompt("营收如何？", [{"id": "EV-001", "content": "x"}])
    assert "未命中任何风险信号" in prompt


def test_system_prompt_forbids_fabrication():
    prompt = llm_client.SYSTEM_PROMPT
    assert "不得引入证据之外" in prompt
    assert "不得编造来源" in prompt
    assert "数据不足" in prompt
    assert "投资建议" in prompt


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


def test_classify_error_mapping():
    assert llm_client.classify_error(TimeoutError("timed out")) == "timeout"
    assert llm_client.classify_error(RuntimeError("401 unauthorized")) == "auth_error"
    assert llm_client.classify_error(RuntimeError("403 forbidden")) == "auth_error"
    assert llm_client.classify_error(RuntimeError("429 rate limit")) == "rate_limited"
    assert llm_client.classify_error(RuntimeError("connection reset")) == "network_error"
    assert llm_client.classify_error(ValueError("something odd")) == "error"


def test_every_fallback_reason_has_friendly_message():
    """每个降级原因都必须有面向用户的中文说明（不能出现英文技术异常）。"""
    for reason, message in llm_client.FALLBACK_MESSAGES.items():
        assert message, f"{reason} 缺少文案"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in message), f"{reason} 文案不是中文"
        assert "Traceback" not in message and "Error" not in message


# ---------------------------------------------------------------------------
# 失败降级（不联网）
# ---------------------------------------------------------------------------


def test_llm_disabled_returns_fallback(patch_settings):
    patch_settings(LLM_FAKE=False, LLM_ENABLED=False, DEEPSEEK_API_KEY="sk-test")
    result = llm_client.generate_answer("营收如何？", [])
    assert result.ok is False
    assert result.fallback_reason == "disabled"
    assert result.notice in llm_client.FALLBACK_MESSAGES.values()
    assert result.is_fallback is True


def test_missing_api_key_does_not_attempt_network(patch_settings):
    """未配置 Key：不联网、直接降级，且给出可读原因。"""
    patch_settings(LLM_FAKE=False, LLM_ENABLED=True, DEEPSEEK_API_KEY="")
    llm_client.reset_call_count()
    result = llm_client.generate_answer("营收如何？", [])
    assert result.ok is False
    assert result.fallback_reason == "no_api_key"
    assert llm_client.call_count() == 0, "未配置 Key 时不应发起调用"


def test_timeout_is_classified_and_degraded(patch_settings, monkeypatch):
    patch_settings(LLM_FAKE=False, LLM_ENABLED=True, DEEPSEEK_API_KEY="sk-test")

    import sys
    import types

    class _Boom:
        def __init__(self, *a, **kw):
            raise TimeoutError("request timed out")

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _Boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    result = llm_client.generate_answer("营收如何？", [{"id": "EV-001", "content": "x"}])
    assert result.ok is False
    assert result.fallback_reason == "timeout"
    assert llm_client.FALLBACK_MESSAGES["timeout"] in result.notice


def test_auth_error_is_classified(patch_settings, monkeypatch):
    patch_settings(LLM_FAKE=False, LLM_ENABLED=True, DEEPSEEK_API_KEY="sk-test")

    import sys
    import types

    class _AuthError(Exception):
        pass

    class _Boom:
        def __init__(self, *a, **kw):
            raise _AuthError("Error code: 401 - invalid api key")

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _Boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    result = llm_client.generate_answer("营收如何？", [])
    assert result.fallback_reason == "auth_error"


def test_unexpected_exception_never_propagates(patch_settings, monkeypatch):
    """任何意外异常都必须被吞掉并转成降级结果，绝不抛给调用方。"""
    patch_settings(LLM_FAKE=False, LLM_ENABLED=True, DEEPSEEK_API_KEY="sk-test")

    import sys
    import types

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("完全没预料到的错误")

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _Boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    result = llm_client.generate_answer("营收如何？", [])  # 不应抛异常
    assert result.ok is False
    assert result.error and "RuntimeError" in result.error


def test_fake_mode_never_touches_network(patch_settings):
    """LLM_FAKE=true：返回固定假响应，且不构造任何客户端。"""
    patch_settings(LLM_FAKE=True)
    llm_client.reset_call_count()
    result = llm_client.generate_answer("营收如何？", [])
    assert result.ok is True
    assert result.source == "fake"
    assert result.text == llm_client.FAKE_ANSWER
    assert llm_client.call_count() == 1


def test_result_to_log_has_no_secrets(patch_settings):
    patch_settings(LLM_FAKE=True)
    result = llm_client.generate_answer("营收如何？", [])
    logged = result.to_log()
    assert "api_key" not in str(logged).lower()
    assert "sk-" not in str(logged)
