"""X-Ray 后端结构自检（纯标准库，无需第三方依赖）。

它做四件事：
  1. 全部 Python 文件可编译；
  2. 全项目跨模块 import 可解析（防删/移文件后留下悬空导入）；
  3. 架构一致性：唯一数据层 db.py、唯一 LLM 判断逻辑 analyzer.py、
     无调度器、旧模块确实已归档；
  4. 交付清单齐全。

真正的行为测试请用 pytest：
    python -m pip install -r requirements.txt
    python -m pytest -v
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))
    if ok:
        PASSED += 1
    else:
        FAILURES.append(label)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def py_files() -> list[Path]:
    """只检查活动代码：_unused/ 是归档，不参与检查。"""
    return sorted(
        p
        for p in ROOT.rglob("*.py")
        if "__pycache__" not in str(p) and "_unused" not in p.parts
    )


# ===========================================================================
print("=" * 74)
print("① 语法编译")
print("=" * 74)
import py_compile  # noqa: E402

errors: list[str] = []
for path in py_files():
    tmp = Path(str(path) + ".pyc.tmp")
    try:
        py_compile.compile(str(path), doraise=True, cfile=str(tmp))
    except py_compile.PyCompileError as exc:
        errors.append(f"{path.name}: {exc}")
    finally:
        if tmp.exists():
            tmp.unlink()
check(f"全部 {len(py_files())} 个活动 Python 文件可编译", not errors, str(errors[:3]))

# ===========================================================================
print()
print("=" * 74)
print("② 跨模块 import 可解析（防悬空导入）")
print("=" * 74)


def top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    if path.name == "__init__.py":
        for child in path.parent.glob("*.py"):
            if child.stem != "__init__":
                names.add(child.stem)
    return names


def resolve(module: str) -> Path | None:
    candidate = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package_init = ROOT.joinpath(*module.split(".")) / "__init__.py"
    return package_init if package_init.is_file() else None


cache: dict[Path, set[str]] = {}
dangling: list[str] = []
for path in py_files():
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        target = resolve(node.module)
        if target is None:
            continue  # 第三方或标准库
        if target not in cache:
            cache[target] = top_level_names(target)
        for alias in node.names:
            if alias.name != "*" and alias.name not in cache[target]:
                dangling.append(
                    f"{path.relative_to(ROOT)}: from {node.module} import {alias.name}"
                )
check(f"{len(cache)} 个项目内模块的 import 全部可解析", not dangling, str(dangling[:4]))

# ===========================================================================
print()
print("=" * 74)
print("③ 架构一致性")
print("=" * 74)

# ---- 唯一数据访问层 ----
db_src = read("db.py")
check("db.py 存在（唯一数据访问层）", (ROOT / "db.py").is_file())
check("db.py 提供 get_stocks()", "def get_stocks(" in db_src)
check("db.py 提供 get_announcements()", "def get_announcements(" in db_src)
check("db.py 提供 search_announcements()", "def search_announcements(" in db_src)
check("db.py 只读连接（mode=ro）", "mode=ro" in db_src)
check("db.py 缺库/缺表抛明确异常", "class DatabaseNotReadyError" in db_src)
check("db.py 不写业务表（无 INSERT/UPDATE/CREATE）",
      not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|CREATE\s+TABLE)\b", db_src, re.I))
check("db.py 只用 docs 表", "FROM docs" in db_src and "FROM announcements" not in db_src)

# 严格按用户给的 8 列
EXPECTED_COLUMNS = ("id", "company_code", "file_name", "rel_path",
                    "page_count", "text_content", "tables_json", "created_at")
m = re.search(r"DOCS_COLUMNS: tuple\[str, \.\.\.\] = \((.*?)\n\)", db_src, re.S)
declared = re.findall(r'"([a-z_]+)"', m.group(1)) if m else []
check("db.py 声明 docs 的 8 个列名且与 schema 一致",
      tuple(declared) == EXPECTED_COLUMNS, str(declared))

# ---- 唯一 LLM 判断逻辑 ----
an_src = read("analyzer.py")
check("analyzer.py 存在（唯一 LLM 判断逻辑）", (ROOT / "analyzer.py").is_file())
check("analyzer 提供 analyze_company()", "def analyze_company(" in an_src)
check("analyzer prompt 要求逐字引用原文", "逐字照抄" in an_src)
check("analyzer prompt 允许「无足够信息」", "无足够信息" in an_src)
check("analyzer prompt 禁止编造", "不许编造" in an_src)
check("analyzer 限定 S1-S4", all(t in an_src for t in ("S1", "S2", "S3", "S4")))
check("analyzer 有当日缓存", "cache_date" in an_src and "ANALYSIS_VERSION" in an_src)
check("analyzer 缓存目录为 cache/", "cache_dir" in an_src)
check("analyzer 失败不抛异常（返回 unknown）", "RISK_UNKNOWN" in an_src)
check("analyzer 丢弃无 source_quote 的证据", "没有原文引用" in an_src or "缺少 source_quote" in an_src)

# ---- 已拆除调度 ----
check("应用内无调度器模块", not (ROOT / "tasks").exists())
check("main.py 不再引入调度器",
      "scheduler" not in read("main.py").lower() or "无调度器" in read("main.py"))
check("ask.py 声明无调度器",
      "无调度器" in read("ask.py") or "不含调度" in read("ask.py"))

# 只检查**真实的依赖行**，不检查注释里的说明文字
req_lines = [
    line.strip() for line in read("requirements.txt").splitlines()
    if line.strip() and not line.strip().startswith("#")
]
req_pkgs = {re.split(r"[<>=!\[]", line)[0].strip().lower() for line in req_lines}
check("requirements 不再依赖 APScheduler", "apscheduler" not in req_pkgs, str(sorted(req_pkgs)))
check("requirements 不再依赖 SQLAlchemy（改用 sqlite3）", "sqlalchemy" not in req_pkgs)
for pkg in ("fastapi", "uvicorn", "pydantic", "pydantic-settings", "httpx", "openai", "pytest"):
    check(f"requirements 含 {pkg}", pkg in req_pkgs)

# ---- 旧链路已归档 ----
LEGACY = ("models.py", "database.py", "signal_engine.py", "mock_data.py",
          "datasource.py", "charts.py")
for name in LEGACY:
    check(f"{name} 已移出活动代码", not (ROOT / name).exists())
    check(f"_unused/{name} 归档存在", (ROOT / "_unused" / name).is_file())
check("services/ 与 tasks/ 已归档",
      not (ROOT / "services").exists() and not (ROOT / "tasks").exists())
check("_unused/README.md 说明废弃原因", (ROOT / "_unused" / "README.md").is_file())

# ---- 契约保持不变 ----
schemas_src = read("schemas.py")
EXPECTED_TOP = ["answer", "claims", "signals", "charts", "evidence", "suggested_questions"]
resp_cls = next(
    (n for n in ast.walk(ast.parse(schemas_src))
     if isinstance(n, ast.ClassDef) and n.name == "AskResponse"), None
)
resp_fields = [
    n.target.id for n in (resp_cls.body if resp_cls else [])
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
]
check("AskResponse 顶层 6 字段且顺序不变", resp_fields == EXPECTED_TOP, str(resp_fields))
check("coverage 仍未出现", "coverage" not in resp_fields)
check("schemas.py 不再依赖 models（已解耦）", "from models" not in schemas_src)
check("证据必须带原文引用（契约级）", "_quote_required" in schemas_src)
check("claims/evidence 允许为空数组", "_answer_required" in schemas_src)

# ---- 接口路径不变 ----
ask_src = read("ask.py")
routes = []
for node in ast.walk(ast.parse(ask_src)):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
                    and dec.func.attr in ("get", "post") and dec.args \
                    and isinstance(dec.args[0], ast.Constant):
                routes.append(dec.args[0].value)
check("主路径仍为 /companies/{stock_code}/ask",
      "/companies/{stock_code}/ask" in routes, str(routes))
check("无 /api 前缀路由", not [p for p in routes if p.startswith("/api")], str(routes))

# ---- run_night_batch ----
nb_src = read("scripts/run_night_batch.py")
check("run_night_batch 支持 --demo", '"--demo"' in nb_src or "'--demo'" in nb_src)
check("run_night_batch 支持 --dry-run", "--dry-run" in nb_src)
check("run_night_batch 遍历 get_stocks()", "get_stocks()" in nb_src)
check("run_night_batch 调用 analyze_company", "analyze_company" in nb_src)
check("run_night_batch 写逐家 JSON", "company_risk_" in nb_src)
check("run_night_batch 写汇总 JSON", "night_batch_report.json" in nb_src)
check("run_night_batch 写人可读报告", "night_batch_report.txt" in nb_src)
for field in ("run_at", "mode", "companies_processed", "alerts_found",
              "duration_seconds", "status"):
    check(f"报告含字段 {field}", f'"{field}"' in nb_src)
check("run_night_batch 不含调度逻辑（不 import apscheduler）",
      "apscheduler" not in nb_src.lower())

# ---- 数据源指向 ----
cfg_src = read("config.py")
check("config 有 DB_PATH", "DB_PATH" in cfg_src)
check("config 默认指向 data/cninfo.db", "data/cninfo.db" in cfg_src)
check("config 已删除旧 DB_URL", "DB_URL" not in cfg_src)
check("config 已删除 MOCK_MODE", "MOCK_MODE" not in cfg_src)
check("config 已删除调度配置", "NIGHTLY_CRON_HOUR" not in cfg_src)
for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "LLM_FAKE"):
    check(f"config 含 {key}", key in cfg_src)

env = read(".env.example")
check(".env.example 含 DB_PATH", "DB_PATH" in env)
check(".env.example 含 DeepSeek 三项",
      all(k in env for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")))
check(".env.example 不再有旧 DB_URL/MOCK_MODE",
      "DB_URL=" not in env and "MOCK_MODE" not in env)

# ===========================================================================
print()
print("=" * 74)
print("④ 交付清单")
print("=" * 74)

EXPECTED_FILES = (
    "db.py",
    "analyzer.py",
    "ask.py",
    "main.py",
    "config.py",
    "schemas.py",
    "llm_client.py",
    "logging_config.py",
    "matcher.py",
    "scripts/run_night_batch.py",
    "scripts/inspect_db.py",
    "tests/conftest.py",
    "tests/helpers.py",
    "tests/test_api_db.py",
    "tests/test_api_contract.py",
    "tests/test_api_batch.py",
    "requirements.txt",
    "pytest.ini",
    ".env.example",
    ".gitignore",
    "README.md",
    "_unused/README.md",
    "deploy/crontab.xray",
)
missing = [f for f in EXPECTED_FILES if not (ROOT / f).is_file()]
check(f"{len(EXPECTED_FILES)} 个交付文件齐全", not missing, f"缺: {missing}")

test_files = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
check("测试文件为 test_api_*.py 命名", all(n.startswith("test_api_") for n in test_files),
      str(test_files))
check("测试覆盖 db / contract / batch 三块",
      all(any(k in n for n in test_files) for k in ("db", "contract", "batch")), str(test_files))

print()
print("=" * 74)
print(f"通过 {PASSED} 项")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("结构自检全部通过。行为测试请运行：python -m pytest -v")
