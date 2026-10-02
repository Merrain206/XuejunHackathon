"""用**真实库**跑通完整链路（LLM 用桩，不联网）。

验证：config 的路径自适应（方案 A）→ db 取公告 → analyzer 拼 prompt / 解析 /
清洗 / 缓存 → 结论结构正确。

用法： python scripts/check_real_pipeline.py
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(label)


# ---- 桩掉第三方依赖 ----
def _identity(*_a, **_kw):
    def _wrap(f):
        return f
    return _wrap


pm = types.ModuleType("pydantic")
pm.computed_field = _identity
pm.field_validator = _identity
sys.modules["pydantic"] = pm
psm = types.ModuleType("pydantic_settings")
sys.modules["pydantic_settings"] = psm

httpx_stub = types.ModuleType("httpx")
httpx_stub.Timeout = lambda *a, **kw: None
httpx_stub.AsyncClient = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应联网"))
sys.modules["httpx"] = httpx_stub
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应调用真实模型"))
sys.modules["openai"] = openai_stub


# ---- 让真实的 config.py 能被导入（它只用到 SettingsConfigDict / field_validator / computed_field）----
def _settings_config_dict(**kw):
    return dict(kw)


class _RealBaseSettings:
    """够用的 BaseSettings 替身：把类属性当默认值，支持 .env 式覆盖。

    config.Settings 里全部是 `NAME: type = default` 形式（没有嵌套模型），
    所以直接读 __annotations__ 取默认值即可。
    """

    def __init__(self, **overrides):
        cls = type(self)
        for name in getattr(cls, "__annotations__", {}):
            if name.startswith("_") or name == "model_config":
                continue
            if hasattr(cls, name):
                setattr(self, name, getattr(cls, name))
        for key, value in overrides.items():
            setattr(self, key, value)


psm.BaseSettings = _RealBaseSettings
psm.SettingsConfigDict = _settings_config_dict
sys.modules["pydantic_settings"] = psm

import config  # noqa: E402
import analyzer  # noqa: E402
import db  # noqa: E402

settings = config.settings

print(f"       数据库：{settings.db_file}")
check("方案 A 生效：自动解析到真实库", settings.db_file.is_file(), str(settings.db_file))

stocks = db.get_stocks()
check("读到公司列表", len(stocks) >= 1, str(stocks[:5]))

code = stocks[0]
total = db.count_announcements(code)
check(f"{code} 有公告", total > 0, f"{total} 条")

# 时间窗口：默认 365 天在真实数据下的效果
for window in (30, 90, 365, None):
    items = db.get_announcements(code, days=window, limit=None, body_chars=0)
    dated = [i for i in items if i["announce_date"]]
    print(f"       days={str(window):>4} → {len(items):>3} 条（其中有日期 {len(dated)}）")

win365 = db.get_announcements(code, days=365, body_chars=0)
check("365 天窗口在真实数据下确实筛掉了有日期的公告（说明窗口生效）",
      len(win365) < total, f"{len(win365)} < {total}")

# 无日期的必须保留
undated_all = [i for i in db.get_announcements(code, days=None, body_chars=0) if not i["announce_date"]]
undated_win = [i for i in win365 if not i["announce_date"]]
check("无日期公告在窗口过滤后仍全部保留",
      len(undated_win) == len(undated_all), f"{len(undated_win)}/{len(undated_all)}")
# 日期解析覆盖率：紧凑 YYYYMMDD 修好后，绝大多数公告都应解析出真实日期。
# 注意不要断言「无日期数量 ≥ N」——那等于把解析缺陷当成期望值。
dated_all = [i for i in db.get_announcements(code, days=None, body_chars=0) if i["announce_date"]]
coverage = len(dated_all) / total if total else 0
check("公告日期解析覆盖率 ≥ 95%", coverage >= 0.95,
      f"{len(dated_all)}/{total} = {coverage:.1%}")
check("解析出的日期已精确到日（不是一律 YYYY-01-01）",
      len({i["announce_date"] for i in dated_all}) > 100,
      f"不同日期数 {len({i['announce_date'] for i in dated_all})}")

# ---- LLM 桩：返回固定 JSON ----
FAKE = {
    "risk_level": "medium",
    "summary": "公告显示公司存在募集资金管理与诉讼相关事项，需持续关注。",
    "findings": [
        {"id": "F1", "title": "诉讼事项", "type": "S4", "severity": "medium",
         "description": "公告提及诉讼相关事项。", "evidence_ids": ["Q1"]}
    ],
    "evidence_quotes": [
        {"id": "Q1", "risk_dimension": "司法风险", "content": "存在诉讼相关公告",
         "source_quote": "关于涉及诉讼的公告", "source_id": 1,
         "source_file": "关于涉及诉讼的公告.PDF", "source_date": None}
    ],
}
CAPTURED: dict[str, str] = {}


def fake_llm(question, evidence=None, **kw):
    from llm_client import LLMResult

    CAPTURED["prompt"] = kw.get("prompt_override") or ""
    return LLMResult(text=json.dumps(FAKE, ensure_ascii=False), ok=True,
                     source="fake", model="fake", elapsed_ms=1.0)


analyzer.generate_answer = fake_llm
shutil.rmtree(ROOT / "cache", ignore_errors=True)

result = analyzer.analyze_company(code, force=True)
check("analyze_company 成功", result["risk_level"] == "medium", str(result["risk_level"]))
check("findings 解析正确", len(result["findings"]) == 1)
check("evidence_quotes 含原文引用",
      bool(result["evidence_quotes"] and result["evidence_quotes"][0]["source_quote"]))
check("写入了当日缓存", analyzer.cache_path(code).is_file())

prompt = CAPTURED.get("prompt", "")
check("prompt 含真实公司代码", code in prompt)
check("prompt 含真实公告正文片段", "募集资金" in prompt or "诉讼" in prompt,
      f"prompt 长度 {len(prompt)}")
check("prompt 含公告 id 与文件名", "id=" in prompt and ".pdf" in prompt.lower())

# 当日缓存生效
calls = {"n": 0}


def counting_llm(question, evidence=None, **kw):
    calls["n"] += 1
    return fake_llm(question, evidence, **kw)


analyzer.generate_answer = counting_llm
second = analyzer.analyze_company(code)
check("同一天第二次读缓存、不再调 LLM",
      second["source"] == "cache" and calls["n"] == 0, f"source={second['source']} calls={calls['n']}")

shutil.rmtree(ROOT / "cache", ignore_errors=True)

print()
print("=" * 70)
print(f"通过 {PASSED} 项")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("真实库完整链路自检通过")
