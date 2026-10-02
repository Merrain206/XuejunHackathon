"""用真实库跑一遍批处理入口（LLM 用桩，不联网）。

验证 scripts/run_night_batch.py 的真实执行路径：
  --dry-run 不写文件 / --demo 限量 / 逐家 JSON / 两份汇总报告 / 六字段齐全。
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


def _identity(*_a, **_kw):
    def _wrap(f):
        return f
    return _wrap


# ---- 桩掉第三方依赖 ----
pm = types.ModuleType("pydantic")
pm.computed_field = _identity
pm.field_validator = _identity
# config.py 还用 model_validator 做 DATABASE_PATH -> DB_PATH 别名解析，
# 桩里必须一并提供，否则导入 config 直接 ImportError。
pm.model_validator = _identity
sys.modules["pydantic"] = pm

psm = types.ModuleType("pydantic_settings")


class _RealBaseSettings:
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
psm.SettingsConfigDict = lambda **kw: dict(kw)
sys.modules["pydantic_settings"] = psm

httpx_stub = types.ModuleType("httpx")
httpx_stub.Timeout = lambda *a, **kw: None
httpx_stub.AsyncClient = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应联网"))
sys.modules["httpx"] = httpx_stub
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应调用真实模型"))
sys.modules["openai"] = openai_stub

import analyzer  # noqa: E402
from config import settings  # noqa: E402

# ---- LLM 桩：返回合法结构化 JSON ----
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
CALLS = {"n": 0}


def fake_llm(question, evidence=None, **kw):
    from llm_client import LLMResult

    CALLS["n"] += 1
    return LLMResult(text=json.dumps(FAKE, ensure_ascii=False), ok=True,
                     source="fake", model="fake", elapsed_ms=1.0)


analyzer.generate_answer = fake_llm

print(f"       数据库：{settings.db_file}")
check("方案 A 解析到真实库", settings.db_file.is_file(), str(settings.db_file))

# 干净起点
for sub in ("cache", "logs"):
    shutil.rmtree(ROOT / sub, ignore_errors=True)

import runpy  # noqa: E402

nb = runpy.run_path(str(ROOT / "scripts" / "run_night_batch.py"))
main = nb["main"]
company_path = nb["company_path"]
logs_dir = settings.log_dir

# ---- 1) --dry-run ----
before = {p.name for p in logs_dir.glob("*")} if logs_dir.is_dir() else set()
rc = main(["--dry-run"])
after = {p.name for p in logs_dir.glob("*")} if logs_dir.is_dir() else set()
check("--dry-run 退出码 0", rc == 0, str(rc))
check("--dry-run 不写任何文件", after == before, f"新增 {after - before}")
check("--dry-run 未调用 LLM", CALLS["n"] == 0, str(CALLS["n"]))

# ---- 2) --demo ----
rc = main(["--demo", "--force"])
check("--demo 退出码 0", rc == 0, str(rc))

report_path = logs_dir / "night_batch_report.json"
text_path = logs_dir / "night_batch_report.txt"
check("生成汇总 JSON", report_path.is_file())
check("生成人可读报告", text_path.is_file())

report = json.loads(report_path.read_text(encoding="utf-8"))
for field in ("run_at", "mode", "companies_processed", "alerts_found",
              "duration_seconds", "status"):
    check(f"报告含必需字段 {field}", field in report)
check("mode = demo", report["mode"] == "demo", report["mode"])
check("companies_processed >= 1", report["companies_processed"] >= 1, str(report["companies_processed"]))
check("alerts_found >= 1（桩返回 medium）", report["alerts_found"] >= 1, str(report["alerts_found"]))
check("window 标注为不限", "不限" in str(report.get("window")), str(report.get("window")))
check("db_path 指向真实库", report["db_path"] == str(settings.db_file))

# ---- 3) 逐家 JSON ----
stocks = json.loads(json.dumps(report["alert_companies"]))
check("alert_companies 非空", len(stocks) >= 1, str(stocks))
for code in stocks:
    path = company_path(code)
    check(f"逐家结果已落地 {path.name}", path.is_file())
    data = json.loads(path.read_text(encoding="utf-8"))
    check(f"{code} 含 risk_level/findings/evidence_quotes",
          all(k in data for k in ("risk_level", "findings", "evidence_quotes")))
    check(f"{code} 的 evidence 带原文引用",
          all(q.get("source_quote") for q in data.get("evidence_quotes") or []))

text = text_path.read_text(encoding="utf-8")
check("txt 报告有人可读标题", "X-Ray 夜间批处理报告" in text)
check("txt 报告含原文引用行", "原文:" in text)
check("txt 报告含公告窗口说明", "公告窗口" in text)

# ---- 4) 当日缓存生效：再跑一次不应再调 LLM ----
calls_before = CALLS["n"]
rc = main(["--demo"])
check("第二次 --demo 退出码 0", rc == 0, str(rc))
check("第二次未再调用 LLM（当日缓存命中）", CALLS["n"] == calls_before,
      f"{calls_before} → {CALLS['n']}")
report2 = json.loads(report_path.read_text(encoding="utf-8"))
check("第二次来源为 cache/成功", report2["status"] in ("success", "partial"), report2["status"])

# ---- 5) 参数校验 ----
try:
    main(["--limit", "0"])
    check("--limit 0 被拒绝", False)
except SystemExit:
    check("--limit 0 被拒绝", True)

# 清理
shutil.rmtree(ROOT / "cache", ignore_errors=True)
shutil.rmtree(ROOT / "logs", ignore_errors=True)

print()
print("=" * 70)
print(f"通过 {PASSED} 项")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("批处理入口真实链路自检通过")
