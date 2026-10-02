"""tests_real/ 的环境配置：**针对真实 cninfo.db** 的验收测试。

为什么单独一个目录、单独一套 conftest
--------------------------------------
`tests/conftest.py` 会在导入项目模块之前把 `DB_PATH` 指向一个**临时构造的
8 列小库**（3 家公司、无 chunks / evidence / source_url）。那是刻意为之：
让 db.py 的行为测试不依赖外部数据。

但三条 Demo 问题的验收测试恰恰相反 —— 它们要验证的是「引用的是**真实**文档、
**真实**页码、**真实**链接」。用临时库跑这些断言等于自己给自己盖章。
两套需求的环境互相冲突（`config.settings` 在导入时就固化），所以分开：

    tests/        —— 合成库，跑 db / contract / batch / analyzer 行为
    tests_real/   —— 真实 cninfo.db，跑三条 Demo 问题的端到端验收

运行方式（两条命令，或见 README 的「跑测试」一节）：

    python -m pytest tests -q
    python -m pytest tests_real -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# ⚠️ 必须在导入任何项目模块之前设置环境（config.settings 在导入时固化）。
#
# 这里**只设库路径**，刻意不设 LLM_FAKE / DEEPSEEK_API_KEY：
#   * 三条 Demo 的意义就是"不依赖大模型也能演示"，用假 LLM 兜住就等于没测到；
#   * 真 Key 由 .env 提供，但本目录的测试不会真的发起网络请求
#     （client fixture 会关掉 LLM_ENABLED）。
#
# 真实库定位顺序（BACKEND_INTEGRATION_TASKS.md「P0-1」）：
#   1. XRAY_REAL_DB（本测试目录专用覆盖）
#   2. DATABASE_PATH（规范名）
#   3. 仓库根目录 cninfo.db —— 默认布局，**无需移动数据库**
# 三者都没有 → real_db_path fixture 会 skip 整个目录（而不是伪造一个库）。
# ---------------------------------------------------------------------------


def _resolve_real_db() -> Path:
    """按 XRAY_REAL_DB → DATABASE_PATH → 仓库根 cninfo.db 的顺序定位真实库。"""
    for env_name in ("XRAY_REAL_DB", "DATABASE_PATH"):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            candidate = Path(value)
            return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)
    return PROJECT_ROOT.parent / "cninfo.db"


_REAL_DB = _resolve_real_db()

os.environ["DB_PATH"] = str(_REAL_DB)
os.environ["DATABASE_PATH"] = str(_REAL_DB)
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ["LLM_FAKE"] = "false"
# 不使用真 Key 发请求：本目录只验证确定性路径
os.environ["DEEPSEEK_API_KEY"] = ""

#: 临时目录也要落在工作区内（受限沙箱下 $TEMP 不可写）
_RUN_TMP = PROJECT_ROOT / ".pytest-run-real"
_RUN_TMP.mkdir(parents=True, exist_ok=True)

#: ⚠️ scratch 目录**不能**放在 _RUN_TMP（= pytest 的 basetemp）里面：
#:    basetemp 会被沙箱整棵打上受限 ACL，在其下再 mkdir 一律 PermissionError。
#:    所以另用一个独立目录（与主测试套件的 .pytest-data/ 同思路）。
_SCRATCH_ROOT = PROJECT_ROOT / ".pytest-data-real"

for _var in ("TMPDIR", "TEMP", "TMP"):
    os.environ[_var] = str(_RUN_TMP)


def _patch_pytest_tmpdir_cleanup() -> None:
    """让 pytest 会话结束时对 basetemp 的清理失败不再炸掉整个会话。

    受限沙箱会给用过的目录打上受限 ACL（连 os.scandir 都拒绝），而 pytest 在
    sessionfinish 会对 basetemp 调 cleanup_dead_symlinks → PermissionError。
    这是**纯清理动作**的失败，不代表任何测试失败，故吞掉。
    """
    try:
        from _pytest import pathlib as _pytest_pathlib
        from _pytest import tmpdir as _pytest_tmpdir
    except Exception:  # pragma: no cover - 仅防御
        return

    if getattr(_pytest_tmpdir.cleanup_dead_symlinks, "_xray_patched", False):
        return

    original = _pytest_tmpdir.cleanup_dead_symlinks

    def tolerant_cleanup(basetemp):  # type: ignore[no-untyped-def]
        try:
            return original(basetemp)
        except OSError:
            return None

    tolerant_cleanup._xray_patched = True  # type: ignore[attr-defined]
    _pytest_tmpdir.cleanup_dead_symlinks = tolerant_cleanup
    _pytest_pathlib.cleanup_dead_symlinks = tolerant_cleanup


_patch_pytest_tmpdir_cleanup()


def pytest_report_header(config: pytest.Config) -> str:
    return f"tests_real: 真实库 {_REAL_DB}"


@pytest.fixture(scope="session")
def real_db_path() -> Path:
    """真实库路径；不存在就直接跳过整个目录的测试。"""
    if not _REAL_DB.is_file():
        pytest.skip(
            f"真实库不存在：{_REAL_DB}\n"
            f"请把 cninfo.db 放到仓库根目录，或用 XRAY_REAL_DB / DATABASE_PATH 指定路径。"
        )
    return _REAL_DB


@pytest.fixture()
def scratch_dir():
    """给单个测试用的干净目录。

    ⚠️ 不用 pytest 的 tmp_path / tmp_path_factory：它依赖 basetemp，而本环境下
       被用过的 basetemp 目录会被沙箱打上受限 ACL（连 os.scandir 都拒绝），
       fixture 存取阶段就 PermissionError。这里直接用工作区内的独立目录。
    """
    import shutil
    import uuid

    path = _SCRATCH_ROOT / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
