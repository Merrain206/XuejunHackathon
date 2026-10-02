"""共享断言工具（被 test_api_*.py 导入）。

⚠️ 本模块不做环境注入 —— 那件事在 conftest.py 里、且在导入项目模块之前完成。
"""

from __future__ import annotations

from typing import Any

#: 响应体顶层必须恰好这些键，**顺序固定**
EXPECTED_TOP_KEYS: list[str] = [
    "answer",
    "claims",
    "signals",
    "charts",
    "evidence",
    "suggested_questions",
]

#: 绝对不许出现在响应体里的字段
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"coverage", "hit_cache", "latency_ms", "stock_code"}
)

#: 引用 evidence 的字段
REFERENCING_FIELDS = ("claims", "signals", "charts")

#: 测试库里存在的公司代码
CODES = ("688583", "000001", "600036")
#: 确定不存在的代码
MISSING_CODE = "999999"


def post_ask(client: Any, stock_code: str, question: str = "这家公司有什么风险？"):
    return client.post(f"/companies/{stock_code}/ask", json={"question": question})


def collect_evidence_ids(payload: dict[str, Any]) -> set[str]:
    used: set[str] = set()
    for group in REFERENCING_FIELDS:
        for item in payload.get(group) or []:
            used.update(item.get("evidence_ids") or [])
    return used


def assert_contract(payload: dict[str, Any]) -> None:
    """完整契约断言：键集合与顺序、禁字段、引用完整性、无孤立证据、引用必带原文。"""
    keys = list(payload.keys())
    assert keys == EXPECTED_TOP_KEYS, f"顶层键或顺序不符: {keys}"
    assert not (FORBIDDEN_KEYS & set(keys)), "响应体出现不该有的字段"
    assert isinstance(payload["answer"], str) and payload["answer"].strip(), "answer 不能为空"
    assert isinstance(payload["charts"], list), "charts 必须是 array"

    known = {e["id"] for e in payload["evidence"]}
    for group in REFERENCING_FIELDS:
        for item in payload.get(group) or []:
            dangling = set(item.get("evidence_ids") or []) - known
            assert not dangling, f"{group} 存在悬空证据引用: {dangling}"

    # 反向：不许有孤立证据
    assert collect_evidence_ids(payload) == known, "存在未被引用的孤立证据"

    # 每条证据必须带原文引用（契约硬约束）
    for ev in payload["evidence"]:
        assert (ev.get("source_quote") or "").strip(), f"evidence {ev['id']} 缺少原文引用"
