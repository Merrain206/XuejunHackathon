"""analyzer.py 与 run_night_batch.py 测试（LLM 全部 mock 掉）。"""

from __future__ import annotations

import functools
import json
import runpy
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@functools.lru_cache(maxsize=1)
def night_batch_module():
    """按**文件路径**加载 scripts/run_night_batch.py。

    不用 `import scripts.run_night_batch`：scripts/ 不是包（没有 __init__.py），
    而该脚本是设计成 `python scripts/run_night_batch.py` 直接执行的。
    runpy 的加载方式与真实调用一致。
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    return runpy.run_path(str(PROJECT_ROOT / "scripts" / "run_night_batch.py"))


def run_night_main(argv: list[str]) -> int:
    return night_batch_module()["main"](argv)


def company_path(code: str) -> Path:
    return night_batch_module()["company_path"](code)


# ---------------------------------------------------------------------------
# prompt 的硬约束
# ---------------------------------------------------------------------------


def test_system_prompt_demands_verbatim_quotes_and_allows_no_info():
    from analyzer import INSUFFICIENT, SYSTEM_PROMPT

    assert "逐字照抄" in SYSTEM_PROMPT, "必须要求逐字引用原文"
    assert INSUFFICIENT in SYSTEM_PROMPT, "必须允许回答「无足够信息」"
    assert "不许编造" in SYSTEM_PROMPT
    for signal_type in ("S1", "S2", "S3", "S4"):
        assert signal_type in SYSTEM_PROMPT, f"prompt 应说明 {signal_type} 的含义"
    assert "严格 JSON" in SYSTEM_PROMPT


def test_build_prompt_includes_announcement_ids_and_content():
    from analyzer import build_prompt

    announcements = [
        {
            "id": 7,
            "file_name": "年报.pdf",
            "created_date": "2024-04-19",
            "page_count": 200,
            "text_content": "营业收入 5.2 亿元。",
        }
    ]
    prompt = build_prompt("688583", announcements)
    assert "688583" in prompt
    assert "id=7" in prompt
    assert "年报.pdf" in prompt
    assert "营业收入 5.2 亿元。" in prompt
    assert "无足够信息" in prompt


def test_build_prompt_truncates_body():
    from analyzer import build_prompt

    long_text = "字" * 5000
    prompt = build_prompt(
        "688583",
        [{"id": 1, "file_name": "x.pdf", "created_date": None, "page_count": 1, "text_content": long_text}],
        max_chars=100,
    )
    assert long_text not in prompt
    assert "字" * 100 in prompt


# ---------------------------------------------------------------------------
# LLM 输出解析容错
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_ok",
    [
        ('{"a":1}', True),
        ('```json\n{"a":1}\n```', True),
        ('```\n{"a":1}\n```', True),
        ('好的，结果如下：\n{"a":1}\n以上。', True),
        ("完全不是 JSON", False),
        ("", False),
        ("[1,2,3]", False),
    ],
)
def test_extract_json_block(raw, expected_ok):
    from analyzer import extract_json_block

    got = extract_json_block(raw)
    assert (got is not None) == expected_ok, f"{raw!r} → {got!r}"


def test_validate_analysis_normalizes_and_drops_bad_items():
    from analyzer import validate_analysis

    result = validate_analysis(
        {
            "risk_level": "HIGH",  # 大写应被归一
            "summary": "有风险",
            "findings": [
                {"id": "F1", "title": "T", "type": "s1", "severity": "HIGH",
                 "description": "d", "evidence_ids": ["Q1"]},
                {"id": "F2", "title": "无引用", "type": "S2", "severity": "low",
                 "description": "d", "evidence_ids": ["NOPE"]},        # 悬空引用 → 丢弃
                {"id": "F3", "title": "类型非法", "type": "S9", "severity": "low",
                 "description": "d", "evidence_ids": ["Q1"]},           # type 非法 → 丢弃
            ],
            "evidence_quotes": [
                {"id": "Q1", "risk_dimension": "现金真实性", "content": "c",
                 "source_quote": "原文片段", "source_id": 1, "source_file": "a.pdf",
                 "source_date": "2024-04-19"},
                {"id": "Q2", "content": "没有原文引用"},                     # 无 quote → 丢弃
            ],
        }
    )
    assert result["risk_level"] == "high"
    assert len(result["findings"]) == 1, result["findings"]
    assert result["findings"][0]["type"] == "S1"
    assert result["findings"][0]["severity"] == "high"
    # Q2 无 source_quote 被丢；Q1 被 F1 引用所以保留
    assert [q["id"] for q in result["evidence_quotes"]] == ["Q1"]


def test_validate_analysis_removes_orphan_quotes():
    """没有任何 finding 引用时，不该留下孤立 quote。"""
    from analyzer import validate_analysis

    result = validate_analysis(
        {
            "risk_level": "low",
            "summary": "s",
            "findings": [],
            "evidence_quotes": [
                {"id": "Q1", "source_quote": "原文", "content": "c", "risk_dimension": "其他"}
            ],
        }
    )
    assert result["findings"] == []
    assert result["evidence_quotes"] == [], "孤立 quote 应被清理"


def test_validate_analysis_downgrades_high_without_findings():
    """报了 high 却没有有效 finding → 降级 unknown，防止空口定罪。"""
    from analyzer import validate_analysis

    result = validate_analysis({"risk_level": "high", "summary": "s", "findings": [], "evidence_quotes": []})
    assert result["risk_level"] == "unknown"


def test_validate_analysis_handles_garbage():
    from analyzer import validate_analysis

    for garbage in (None, [], "string", 42):
        result = validate_analysis(garbage)
        assert result["risk_level"] == "unknown"
        assert result["findings"] == []
        assert result["evidence_quotes"] == []


# ---------------------------------------------------------------------------
# analyze_company：正常路径与缓存
# ---------------------------------------------------------------------------


def test_analyze_company_returns_structured_result(fake_llm):
    from analyzer import analyze_company

    result = analyze_company("688583", force=True)
    assert result["risk_level"] == "high"
    assert result["source"] == "llm"
    assert result["llm_called"] is True
    assert len(result["findings"]) == 2
    assert len(result["evidence_quotes"]) == 2
    for quote in result["evidence_quotes"]:
        assert quote["source_quote"], "每条证据必须有原文引用"


def test_same_day_cache_prevents_second_llm_call(llm_calls):
    """同一家公司同一天不重复调用 LLM。"""
    from analyzer import analyze_company

    analyze_company("688583", force=True)
    calls_after_first = len(llm_calls)

    second = analyze_company("688583")  # 不 force
    assert second["source"] == "cache"
    assert second["llm_called"] is False
    assert len(llm_calls) == calls_after_first, "第二次不应再调用 LLM"


def test_cache_expires_on_a_different_day(monkeypatch, llm_calls):
    """换一天后缓存失效，应重新调用 LLM。"""
    import analyzer

    analyze_company_ = analyzer.analyze_company
    analyze_company_("688583", force=True)
    calls_after_first = len(llm_calls)

    monkeypatch.setattr(analyzer, "_today", lambda: "2099-01-01")
    result = analyzer.analyze_company("688583")
    assert result["source"] == "llm", "非当日缓存必须失效"
    assert len(llm_calls) > calls_after_first


def test_cache_file_written_with_metadata(fake_llm):
    from analyzer import ANALYSIS_VERSION, analyze_company, cache_path

    analyze_company("000001", force=True)
    path = cache_path("000001")
    assert path.is_file(), f"应写入缓存文件 {path}"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == ANALYSIS_VERSION
    assert data["cache_date"]
    assert data["analyzed_at"]
    assert data["risk_level"] == "low"


def test_corrupt_cache_is_treated_as_miss(fake_llm):
    from analyzer import analyze_company, cache_path

    path = cache_path("000001")
    path.write_text("{ 这不是 json", encoding="utf-8")

    result = analyze_company("000001")  # 不应抛异常
    assert result["source"] == "llm"


def test_wrong_cache_version_ignored(fake_llm):
    from analyzer import analyze_company, cache_path

    path = cache_path("000001")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 999
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert analyze_company("000001")["source"] == "llm"


def test_llm_failure_returns_unknown_without_raising(monkeypatch):
    """LLM 失败 → 返回 unknown + 说明，绝不抛异常。"""
    import analyzer
    from llm_client import LLMResult

    def _fail(*_a, **_kw):
        return LLMResult(text="", ok=False, source="fallback",
                         fallback_reason="no_api_key", model="m")

    monkeypatch.setattr(analyzer, "generate_answer", _fail)
    result = analyzer.analyze_company("688583", force=True)
    assert result["risk_level"] == "unknown"
    assert result["source"] == "llm_failed"
    assert result["findings"] == []
    assert "无足够信息" in result["summary"]


def test_unparseable_llm_output_returns_unknown(monkeypatch):
    import analyzer
    from llm_client import LLMResult

    monkeypatch.setattr(
        analyzer, "generate_answer",
        lambda *a, **kw: LLMResult(text="我拒绝输出 JSON", ok=True, source="fake", model="m"),
    )
    result = analyzer.analyze_company("688583", force=True)
    assert result["source"] == "parse_failed"
    assert result["risk_level"] == "unknown"


def test_analyze_rejects_empty_code():
    from analyzer import analyze_company

    with pytest.raises(ValueError):
        analyze_company("")


def test_analyze_many_isolates_failures(monkeypatch, fake_llm):
    """批量分析中单家异常不影响其他家。"""
    import analyzer

    real = analyzer.analyze_company

    def _flaky(code, **kwargs):
        if code == "000001":
            raise RuntimeError("模拟单家失败")
        return real(code, **kwargs)

    monkeypatch.setattr(analyzer, "analyze_company", _flaky)
    results = analyzer.analyze_many(["688583", "000001", "600036"])
    assert set(results) == {"688583", "000001", "600036"}
    assert results["000001"]["risk_level"] == "unknown"
    assert results["000001"]["source"] == "error"
    assert results["688583"]["risk_level"] == "high"


# ---------------------------------------------------------------------------
# run_night_batch.py
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(fake_llm, capsys):
    """--dry-run 不调 LLM、不写任何文件。"""
    from config import settings

    before = {p.name for p in settings.log_dir.glob("*")} if settings.log_dir.is_dir() else set()

    assert run_night_main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "dry-run" in output
    assert "预计产出" in output

    after = {p.name for p in settings.log_dir.glob("*")} if settings.log_dir.is_dir() else set()
    assert after == before, f"dry-run 不应写文件，新增了 {after - before}"


def test_demo_mode_limits_companies(fake_llm):
    """--demo 只处理前 N 家（默认 5，库里 3 家则全处理）。"""
    from config import settings

    assert run_night_main(["--demo", "--force"]) == 0

    report = json.loads((settings.log_dir / "night_batch_report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "demo"
    assert report["companies_processed"] == 3, report
    assert report["status"] in ("success", "partial")


def test_full_run_produces_reports_with_required_fields(fake_llm):
    from config import settings

    assert run_night_main(["--force"]) == 0

    report_path = settings.log_dir / "night_batch_report.json"
    text_path = settings.log_dir / "night_batch_report.txt"
    assert report_path.is_file()
    assert text_path.is_file()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    for field in (
        "run_at",
        "mode",
        "companies_processed",
        "alerts_found",
        "duration_seconds",
        "status",
    ):
        assert field in report, f"汇总报告缺少字段 {field}"

    assert report["mode"] == "full"
    assert report["companies_processed"] == 3
    assert report["alerts_found"] >= 1, "688583 是 high，应至少 1 条预警"
    assert isinstance(report["duration_seconds"], (int, float))
    assert "688583" in report["alert_companies"]

    text = text_path.read_text(encoding="utf-8")
    assert "X-Ray 夜间批处理报告" in text
    assert "688583" in text


def test_per_company_json_written(fake_llm):
    assert run_night_main(["--demo", "--force"]) == 0
    for code in ("688583", "000001", "600036"):
        path = company_path(code)
        assert path.is_file(), f"缺少 {path.name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["risk_level"] in ("high", "medium", "low", "unknown")
        assert "findings" in data and "evidence_quotes" in data


def test_invalid_limit_rejected():
    with pytest.raises(SystemExit):
        run_night_main(["--limit", "0"])
