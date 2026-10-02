"""analyzer.py + scripts/run_night_batch.py 行为自检。

这两个模块依赖 config(pydantic) / db(sqlite3) / llm_client(httpx, openai)，
本机装不上第三方库，因此用**桩模块**替换 pydantic / pydantic_settings / httpx /
openai，然后真实导入并执行，验证：

  ① prompt 硬约束（逐字引用、允许"无足够信息"、S1-S4）；
  ② LLM 输出解析容错（代码块、前后废话、非法 JSON）；
  ③ validate_analysis 的清洗规则（丢弃悬空引用/非法 type/无 quote 的证据）；
  ④ analyze_company 正常路径 + 当日缓存不重复调用 + 隔日失效 + 失败降级；
  ⑤ run_night_batch 的 --dry-run 不写文件、--demo 限量、报告六个必需字段。

与 tests/test_api_*.py 的断言一一对应，只是不经过 pytest。
"""

from __future__ import annotations

import json
import runpy
import shutil
import sqlite3
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# ① 桩模块
# ---------------------------------------------------------------------------

DEFAULTS = {
    "APP_NAME": "X-Ray",
    "APP_VERSION": "0.5.0",
    "DEBUG": False,
    "API_PREFIX": "",
    "HOST": "127.0.0.1",
    "PORT": 8000,
    "DB_PATH": "",
    "ANALYSIS_WINDOW_DAYS": None,
    "ANALYSIS_MAX_ANNOUNCEMENTS": 30,
    "ANNOUNCEMENT_MAX_CHARS": 4000,
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_MODEL": "deepseek-chat",
    "REQUEST_TIMEOUT_MS": 60000,
    "LLM_MAX_TOKENS": 900,
    "LLM_TEMPERATURE": 0.2,
    "LLM_ENABLED": True,
    "LLM_FAKE": True,
    "CACHE_ENABLED": True,
    "CHARTS_ENABLED": True,
    "CHARTS_MAX": 4,
    "ASK_HIT_BUDGET_MS": 100,
    "MATCH_SIMILARITY_THRESHOLD": 0.8,
    "LOG_LEVEL": "WARNING",
    "LOG_FILE": "",
    "LOG_MAX_BYTES": 5242880,
    "LOG_BACKUP_COUNT": 3,
    "CORS_ORIGINS": "http://localhost:3000",
    "DEFAULT_QUESTIONS": ("该公司的营收情况如何？",),
    "ADMIN_TOKEN": "xray-demo-token",
}


def _identity(*_a, **_kw):
    def _wrap(func):
        return func
    return _wrap


class _StubBaseSettings:
    def __init__(self, **overrides):
        for key, value in DEFAULTS.items():
            setattr(self, key, value)
        for key, value in overrides.items():
            setattr(self, key, value)

    @property
    def db_file(self) -> Path:
        candidate = Path(self.DB_PATH)
        return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)

    @property
    def cache_dir(self) -> Path:
        return PROJECT_ROOT / "cache"

    @property
    def log_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def log_file_path(self) -> Path:
        return self.log_dir / "xray.log"

    @property
    def llm_ready(self) -> bool:
        return True

    @property
    def cors_origin_list(self):
        return ["http://localhost:3000"]

    def describe(self):
        return {"db_path": str(self.db_file)}


pm = types.ModuleType("pydantic")
pm.computed_field = _identity
pm.field_validator = _identity
# config.py 还用 model_validator 做 DATABASE_PATH -> DB_PATH 别名解析；
# 桩里必须一并提供，否则导入 config 直接 ImportError。
pm.model_validator = _identity
pm.BaseModel = object
sys.modules["pydantic"] = pm

psm = types.ModuleType("pydantic_settings")
psm.BaseSettings = _StubBaseSettings
psm.SettingsConfigDict = lambda **kw: dict(kw)
sys.modules["pydantic_settings"] = psm

httpx_stub = types.ModuleType("httpx")


class _FakeTimeout:
    def __init__(self, *a, **kw):
        pass


class _FakeAsyncClient:
    def __init__(self, *a, **kw):
        raise AssertionError("测试不应发起真实 HTTP 请求")


httpx_stub.Timeout = _FakeTimeout
httpx_stub.AsyncClient = _FakeAsyncClient
sys.modules["httpx"] = httpx_stub

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = lambda *a, **kw: (_ for _ in ()).throw(
    AssertionError("测试不应调用真实 OpenAI/DeepSeek")
)
sys.modules["openai"] = openai_stub

# ---------------------------------------------------------------------------
# ② 造库（严格按 docs schema）+ 清空 cache/logs
# ---------------------------------------------------------------------------

TEST_DIR = PROJECT_ROOT / ".test-tmp" / "batch-check"
if TEST_DIR.exists():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
TEST_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = TEST_DIR / "cninfo.db"

for sub in ("cache", "logs"):
    target = PROJECT_ROOT / sub
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

TODAY = date.today()
d = lambda n: (TODAY - timedelta(days=n)).isoformat()

ROWS = [
    ("688583", "2023年年度报告.pdf", "a/1.pdf", 206,
     "归属于上市公司股东的净利润 8600 万元，但经营活动产生的现金流量净额为 -1200 万元。", None, d(10) + " 09:30:00"),
    ("688583", "关于涉及诉讼的公告.pdf", "a/2.pdf", 6,
     "涉及一起买卖合同纠纷，涉案金额 3200 万元。", '[{"c":1}]', d(30) + " 10:00:00"),
    ("000001", "某银行2024年年度报告.pdf", "b/1.pdf", 300,
     "本行实现营业收入 1200 亿元。", None, d(5) + " 08:00:00"),
    ("600036", "招行2024年年度报告.pdf", "c/1.pdf", 280,
     "实现营业收入 3300 亿元。", None, d(1) + " 07:15:00"),
]
con = sqlite3.connect(DB_FILE)
con.execute(
    """CREATE TABLE docs (id INTEGER PRIMARY KEY AUTOINCREMENT, company_code TEXT,
       file_name TEXT, rel_path TEXT, page_count INTEGER, text_content TEXT,
       tables_json TEXT, created_at TEXT)"""
)
con.executemany(
    "INSERT INTO docs (company_code, file_name, rel_path, page_count, text_content,"
    " tables_json, created_at) VALUES (?,?,?,?,?,?,?)",
    ROWS,
)
con.commit()
con.close()

DEFAULTS["DB_PATH"] = str(DB_FILE)

# ---------------------------------------------------------------------------
# ③ 导入被测模块
# ---------------------------------------------------------------------------

for name in ("config", "db", "llm_client", "analyzer"):
    sys.modules.pop(name, None)

import analyzer  # noqa: E402
import config  # noqa: E402

print(f"       已真实导入 {analyzer.__file__}")
print(f"       测试库 {DB_FILE}（{len(ROWS)} 行）")

# ---------------------------------------------------------------------------
# ④ 假 LLM（返回合法 JSON，模拟真实模型输出）
# ---------------------------------------------------------------------------

FAKE_BY_CODE = {
    "688583": {
        "risk_level": "high",
        "summary": "净利润为正但经营现金流为负，且存在诉讼，风险偏高。",
        "findings": [
            {"id": "F1", "title": "利润与现金流背离", "type": "S1", "severity": "high",
             "description": "净利润为正，但经营活动现金流净额为负。", "evidence_ids": ["Q1"]},
            {"id": "F2", "title": "存在诉讼", "type": "S4", "severity": "medium",
             "description": "涉及买卖合同纠纷。", "evidence_ids": ["Q2"]},
        ],
        "evidence_quotes": [
            {"id": "Q1", "risk_dimension": "现金真实性", "content": "经营现金流为负",
             "source_quote": "归属于上市公司股东的净利润 8600 万元，但经营活动产生的现金流量净额为 -1200 万元",
             "source_id": 1, "source_file": "2023年年度报告.pdf", "source_date": d(10)},
            {"id": "Q2", "risk_dimension": "司法风险", "content": "涉及诉讼",
             "source_quote": "涉及一起买卖合同纠纷，涉案金额 3200 万元",
             "source_id": 2, "source_file": "关于涉及诉讼的公告.pdf", "source_date": d(30)},
        ],
    },
    "000001": {"risk_level": "low", "summary": "未发现明显风险。", "findings": [], "evidence_quotes": []},
    "600036": {"risk_level": "unknown", "summary": "无足够信息", "findings": [], "evidence_quotes": []},
}

LLM_CALLS: list[str] = []


def fake_generate_answer(question, evidence=None, **kwargs):
    from llm_client import LLMResult

    prompt = kwargs.get("prompt_override") or ""
    code = next((c for c in FAKE_BY_CODE if c in prompt), "")
    LLM_CALLS.append(code)
    payload = FAKE_BY_CODE.get(code, FAKE_BY_CODE["600036"])
    return LLMResult(text=json.dumps(payload, ensure_ascii=False), ok=True,
                     source="fake", model="fake-model", elapsed_ms=1.0)


analyzer.generate_answer = fake_generate_answer

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("① prompt 硬约束")
print("=" * 74)

sp = analyzer.SYSTEM_PROMPT
check("prompt 要求逐字照抄原文", "逐字照抄" in sp)
check("prompt 允许回答「无足够信息」", analyzer.INSUFFICIENT in sp)
check("prompt 禁止编造", "不许编造" in sp)
check("prompt 限定 S1-S4", all(t in sp for t in ("S1", "S2", "S3", "S4")))
# 必须显式要求"整段回复就是一个 JSON 对象"并禁止 markdown 代码块：
# 思考模式下模型容易写一段解释性散文，导致解析不到 JSON 而每次降级。
check("prompt 要求整段输出就是一个 JSON 对象", "整个回复必须是一个 JSON 对象" in sp)
check("prompt 禁止 markdown 代码块", "```json" in sp)
check("prompt 要求回填真实 source_page", "source_page" in sp and "总页数" in sp)

prompt = analyzer.build_prompt("688583", [
    {"id": 7, "file_name": "年报.pdf", "created_date": "2024-04-19",
     "page_count": 200, "text_content": "营业收入 5.2 亿元。"}
])
check("prompt 含公司代码", "688583" in prompt)
check("prompt 含公告 id", "id=7" in prompt)
check("prompt 含公告文件名", "年报.pdf" in prompt)
check("prompt 含正文", "营业收入 5.2 亿元。" in prompt)
long_prompt = analyzer.build_prompt("X", [
    {"id": 1, "file_name": "x.pdf", "created_date": None, "page_count": 1,
     "text_content": "字" * 5000}
], max_chars=100)
check("prompt 正文按 max_chars 截断", "字" * 100 in long_prompt and "字" * 101 not in long_prompt)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("② LLM 输出解析容错")
print("=" * 74)

ex = analyzer.extract_json_block
check("直接 JSON", ex('{"a":1}') == '{"a":1}')
check("```json 代码块", ex('```json\n{"a":1}\n```') == '{"a":1}')
check("``` 代码块", ex('```\n{"a":1}\n```') == '{"a":1}')
check("前后有废话", ex('结果如下：\n{"a":1}\n以上。') == '{"a":1}')
check("非 JSON → None", ex("完全不是 JSON") is None)
check("空串 → None", ex("") is None)
check("数组不是对象 → None", ex("[1,2,3]") is None)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("③ validate_analysis 清洗规则")
print("=" * 74)

v = analyzer.validate_analysis({
    "risk_level": "HIGH",
    "summary": "有风险",
    "findings": [
        {"id": "F1", "title": "T", "type": "s1", "severity": "HIGH", "description": "d",
         "evidence_ids": ["Q1"]},
        {"id": "F2", "title": "悬空引用", "type": "S2", "severity": "low", "description": "d",
         "evidence_ids": ["NOPE"]},
        {"id": "F3", "title": "非法 type", "type": "S9", "severity": "low", "description": "d",
         "evidence_ids": ["Q1"]},
    ],
    "evidence_quotes": [
        {"id": "Q1", "risk_dimension": "现金真实性", "content": "c", "source_quote": "原文片段"},
        {"id": "Q2", "content": "没有原文引用"},
    ],
})
check("risk_level 大写被归一", v["risk_level"] == "high", v["risk_level"])
check("type 小写被归一为 S1", v["findings"][0]["type"] == "S1")
check("severity 小写被归一", v["findings"][0]["severity"] == "high")
check("仅保留 1 条有效 finding（悬空引用/非法 type 被丢）", len(v["findings"]) == 1, str(len(v["findings"])))
check("无 source_quote 的证据被丢弃", [q["id"] for q in v["evidence_quotes"]] == ["Q1"],
      str([q["id"] for q in v["evidence_quotes"]]))

orphan = analyzer.validate_analysis({
    "risk_level": "low", "summary": "s", "findings": [],
    "evidence_quotes": [{"id": "Q1", "source_quote": "原文", "content": "c"}],
})
check("无 finding 时孤立 quote 被清理", orphan["evidence_quotes"] == [])

downgrade = analyzer.validate_analysis({
    "risk_level": "high", "summary": "s", "findings": [], "evidence_quotes": []
})
check("报 high 但无有效 finding → 降级 unknown（防空口定罪）",
      downgrade["risk_level"] == "unknown", downgrade["risk_level"])

for garbage in (None, [], "str", 42):
    g = analyzer.validate_analysis(garbage)
    check(f"垃圾输入 {garbage!r} 不抛异常且返回 unknown",
          g["risk_level"] == "unknown" and g["findings"] == [] and g["evidence_quotes"] == [])

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("④ analyze_company —— 正常路径 / 缓存 / 降级")
print("=" * 74)

result = analyzer.analyze_company("688583", force=True)
check("688583 → high", result["risk_level"] == "high", str(result["risk_level"]))
check("来源为 llm", result["source"] == "llm")
check("llm_called=True", result["llm_called"] is True)
check("2 条 finding", len(result["findings"]) == 2, str(len(result["findings"])))
check("2 条 evidence_quotes", len(result["evidence_quotes"]) == 2, str(len(result["evidence_quotes"])))
check("每条 quote 都有原文引用",
      all(q["source_quote"] for q in result["evidence_quotes"]))

cache_file = analyzer.cache_path("688583")
check("缓存文件已写入", cache_file.is_file(), str(cache_file))
cached = json.loads(cache_file.read_text(encoding="utf-8"))
check("缓存含 version", cached.get("version") == analyzer.ANALYSIS_VERSION)
check("缓存含 cache_date", cached.get("cache_date") == TODAY.isoformat())
check("缓存含 analyzed_at", bool(cached.get("analyzed_at")))

# 当日缓存：不再调用 LLM
before_calls = len(LLM_CALLS)
second = analyzer.analyze_company("688583")
check("当日缓存命中 → source=cache", second["source"] == "cache", second["source"])
check("当日缓存命中 → llm_called=False", second["llm_called"] is False)
check("当日缓存命中 → 未再调用 LLM", len(LLM_CALLS) == before_calls,
      f"{before_calls} → {len(LLM_CALLS)}")

# 隔日失效
real_today = analyzer._today
analyzer._today = lambda: "2099-01-01"
third = analyzer.analyze_company("688583")
check("隔日缓存失效 → 重新调用 LLM", third["source"] == "llm", third["source"])
check("隔日确实又调用了一次", len(LLM_CALLS) > before_calls)
analyzer._today = real_today

# 损坏缓存 → 按未命中
analyzer.cache_path("000001").write_text("{ 坏 json", encoding="utf-8")
fresh = analyzer.analyze_company("000001")
check("损坏缓存按未命中处理（不抛异常）", fresh["source"] == "llm")

# 版本不符 → 按未命中
analyzer.cache_path("000001").write_text(
    json.dumps({"version": 999, "cache_date": TODAY.isoformat(), "risk_level": "low"}),
    encoding="utf-8",
)
check("缓存版本不符按未命中", analyzer.analyze_company("000001")["source"] == "llm")

# LLM 失败 → unknown，不抛异常
def failing_llm(*_a, **_kw):
    from llm_client import LLMResult
    return LLMResult(text="", ok=False, source="fallback",
                     fallback_reason="no_api_key", model="m")


analyzer.generate_answer = failing_llm
failed = analyzer.analyze_company("688583", force=True)
check("LLM 失败 → risk_level=unknown", failed["risk_level"] == "unknown")
check("LLM 失败 → source=llm_failed", failed["source"] == "llm_failed")
check("LLM 失败 → findings 为空", failed["findings"] == [])
check("LLM 失败 → summary 含「无足够信息」", analyzer.INSUFFICIENT in failed["summary"])
check("LLM 失败不抛异常", True)

# 输出不可解析 → parse_failed
analyzer.generate_answer = lambda *a, **kw: __import__("llm_client").LLMResult(
    text="我拒绝输出 JSON", ok=True, source="fake", model="m"
)
bad_output = analyzer.analyze_company("688583", force=True)
check("输出非 JSON → source=parse_failed", bad_output["source"] == "parse_failed", bad_output["source"])
check("输出非 JSON → risk_level=unknown", bad_output["risk_level"] == "unknown")

# 批量隔离失败
analyzer.generate_answer = fake_generate_answer
real_analyze = analyzer.analyze_company


def flaky(code, **kwargs):
    if code == "000001":
        raise RuntimeError("模拟单家失败")
    return real_analyze(code, **kwargs)


analyzer.analyze_company = flaky
many = analyzer.analyze_many(["688583", "000001", "600036"])
check("批量返回全部公司", set(many) == {"688583", "000001", "600036"}, str(sorted(many)))
check("失败的那家为 error/unknown",
      many["000001"]["source"] == "error" and many["000001"]["risk_level"] == "unknown")
check("其他家不受影响", many["688583"]["risk_level"] == "high")
analyzer.analyze_company = real_analyze

try:
    analyzer.analyze_company("")
    check("空代码抛 ValueError", False)
except ValueError:
    check("空代码抛 ValueError", True)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("⑤ run_night_batch —— dry-run / demo / 报告字段")
print("=" * 74)

# 重新绑定假 LLM（run_night_batch 内部 import analyze_company）
analyzer.generate_answer = fake_generate_answer
night = runpy.run_path(str(PROJECT_ROOT / "scripts" / "run_night_batch.py"))
main = night["main"]
company_path = night["company_path"]

logs_dir = PROJECT_ROOT / "logs"
before_files = {p.name for p in logs_dir.glob("*")} if logs_dir.is_dir() else set()
rc = main(["--dry-run"])
after_files = {p.name for p in logs_dir.glob("*")} if logs_dir.is_dir() else set()
check("--dry-run 退出码 0", rc == 0, str(rc))
check("--dry-run 不写任何文件", after_files == before_files,
      f"新增 {after_files - before_files}")

rc = main(["--demo", "--force"])
check("--demo 退出码 0", rc == 0, str(rc))
report = json.loads((logs_dir / "night_batch_report.json").read_text(encoding="utf-8"))
check("--demo 模式标记为 demo", report["mode"] == "demo", report["mode"])
check("--demo 处理 3 家", report["companies_processed"] == 3, str(report["companies_processed"]))
for field in ("run_at", "mode", "companies_processed", "alerts_found",
              "duration_seconds", "status"):
    check(f"报告含必需字段 {field}", field in report)
check("alerts_found 至少 1（688583 是 high）", report["alerts_found"] >= 1,
      str(report["alerts_found"]))
check("alert_companies 含 688583", "688583" in report["alert_companies"])
check("duration_seconds 是数字", isinstance(report["duration_seconds"], (int, float)))

for code in ("688583", "000001", "600036"):
    path = company_path(code)
    check(f"逐家结果已落地 {path.name}", path.is_file())
    data = json.loads(path.read_text(encoding="utf-8"))
    check(f"{code} 结果含 risk_level/findings/evidence_quotes",
          all(k in data for k in ("risk_level", "findings", "evidence_quotes")))

text_report = (logs_dir / "night_batch_report.txt").read_text(encoding="utf-8")
check("txt 报告有人可读标题", "X-Ray 夜间批处理报告" in text_report)
check("txt 报告含公司代码", "688583" in text_report)
check("txt 报告含原文引用", "原文:" in text_report)

rc = main(["--force"])
check("全量模式退出码 0", rc == 0, str(rc))
full = json.loads((logs_dir / "night_batch_report.json").read_text(encoding="utf-8"))
check("全量模式标记为 full", full["mode"] == "full", full["mode"])
check("全量统计 3 家", full["companies_processed"] == 3, str(full["companies_processed"]))

try:
    main(["--limit", "0"])
    check("--limit 0 被拒绝", False)
except SystemExit:
    check("--limit 0 被拒绝", True)

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("⑥ 清理")
print("=" * 74)
shutil.rmtree(TEST_DIR, ignore_errors=True)
for sub in ("cache", "logs"):
    shutil.rmtree(PROJECT_ROOT / sub, ignore_errors=True)
check("测试产物已清理", not (PROJECT_ROOT / "logs").exists())

print()
print("=" * 74)
print(f"通过 {PASSED} 项")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("analyzer / run_night_batch 行为自检全部通过")
