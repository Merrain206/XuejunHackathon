"""charts.py 行为自检（纯标准库）。

charts.py 依赖 config / models（需要 pydantic + sqlalchemy），本环境装不上，
因此这里用**桩模块**替换这两个依赖后真实导入 charts.py，验证生成逻辑本身。

校验点：
  1. 健康样本：产出营收/应收、净利/现金流、人力 共 3 张图；**不**产出司法图（全 0）；
  2. 风险样本：额外产出 chart-judicial；
  3. 每个图都有真实 evidence_ids；序列点数一致；点值为 None 的保留 null；
  4. 数据不足（缺字段 / 序列含 null）时**不画那张图**；
  5. CHARTS_ENABLED=false 时返回空数组；
  6. CHARTS_MAX 生效；
  7. 单位换算正确（元 → 万元）。
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# ① 用桩模块替换 config / models，让 charts.py 能被真实导入
# ---------------------------------------------------------------------------

DICT15 = (
    "revenue_3y", "net_profit_3y", "oper_cashflow_3y", "accounts_receivable_3y",
    "asset_liability_ratio", "social_security_count_3y", "executed_count",
    "dishonest_count", "equity_pledge_count", "legal_case_count", "admin_penalty_count",
    "reg_capital", "latest_quarter_profit", "latest_quarter_revenue", "latest_quarter_cashflow",
)


class _StubSettings:
    """可调开关的假配置。"""

    CHARTS_ENABLED = True
    CHARTS_MAX = 4


stub_config = types.ModuleType("config")
stub_config.settings = _StubSettings()
sys.modules["config"] = stub_config

stub_models = types.ModuleType("models")
stub_models.DICT_FIELD_NAMES = DICT15
sys.modules["models"] = stub_models

sys.path.insert(0, str(ROOT))
# 确保加载的是真实 charts.py 而不是缓存
sys.modules.pop("charts", None)
charts = importlib.import_module("charts")
print(f"       已真实导入 {charts.__file__}")

# ---------------------------------------------------------------------------
# ② 构造真实字段（取自 mock_data 的数值，与第 2 批一致）
# ---------------------------------------------------------------------------

HEALTHY_FIELDS = {
    "revenue_3y": [950_000_000, 1_100_000_000, 1_320_000_000],
    "net_profit_3y": [80_000_000, 100_000_000, 120_000_000],
    "oper_cashflow_3y": [90_000_000, 112_000_000, 131_000_000],
    "accounts_receivable_3y": [200_000_000, 228_000_000, 260_000_000],
    "asset_liability_ratio": 38.6,
    "social_security_count_3y": [1450, 1520, 1600],
    "executed_count": 0,
    "dishonest_count": 0,
    "legal_case_count": 2,
    "equity_pledge_count": 0,
    "admin_penalty_count": 0,
    "reg_capital": "12000万元",
    "latest_quarter_profit": 34_000_000,
    "latest_quarter_revenue": 370_000_000,
    "latest_quarter_cashflow": 41_000_000,
}

RISK_FIELDS = {
    "revenue_3y": [1_000_000_000, 1_500_000_000, 2_000_000_000],
    "net_profit_3y": [50_000_000, 80_000_000, 120_000_000],
    "oper_cashflow_3y": [60_000_000, 20_000_000, -30_000_000],
    "accounts_receivable_3y": [200_000_000, 450_000_000, 800_000_000],
    "asset_liability_ratio": 72.4,
    "social_security_count_3y": [3000, 2400, 1900],
    "executed_count": 5,
    "dishonest_count": 3,
    "legal_case_count": 41,
    "equity_pledge_count": 7,
    "admin_penalty_count": 2,
    "reg_capital": "58000万元",
    "latest_quarter_profit": -80_000_000,
    "latest_quarter_revenue": 600_000_000,
    "latest_quarter_cashflow": -120_000_000,
}

EVIDENCE = [
    {"evidence_id": "EV-001", "field_names": ["net_profit_3y", "oper_cashflow_3y"], "risk_dimension": "现金真实性"},
    {"evidence_id": "EV-002", "field_names": ["revenue_3y", "accounts_receivable_3y"], "risk_dimension": "资产健康度"},
    {"evidence_id": "EV-003", "field_names": ["social_security_count_3y", "revenue_3y"], "risk_dimension": "经营稳定性"},
    {"evidence_id": "EV-004", "field_names": ["executed_count", "dishonest_count"], "risk_dimension": "司法风险"},
]

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("① 健康样本：3 张图，无司法图")
print("=" * 74)

healthy = charts.build_charts(HEALTHY_FIELDS, EVIDENCE)
ids = [c["id"] for c in healthy]
print(f"       charts: {ids}")
check("健康样本产出 3 张图", len(healthy) == 3, str(len(healthy)))
check("含营收/应收账款图", "chart-revenue-ar" in ids)
check("含净利/现金流图", "chart-profit-cashflow" in ids)
check("含人力/营收图", "chart-headcount-revenue" in ids)
check("不含司法风险图（被执行+失信=0）", "chart-judicial" not in ids)

check("每个图都有真实 evidence_ids", all(c["evidence_ids"] for c in healthy))
check("每个图 kind 合法", all(c["kind"] in ("line", "bar", "pie", "donut", "table") for c in healthy))
check("每个图序列非空", all(c["series"] for c in healthy))
check("同图内各序列点数一致",
      all(len({len(s["points"]) for s in c["series"]}) == 1 for c in healthy))
check("每个点都有 label/value 键",
      all({"label", "value"} <= set(p) for c in healthy for s in c["series"] for p in s["points"]))

# 单位换算：13.20 亿元 = 132000 万元
rev_chart = next(c for c in healthy if c["id"] == "chart-revenue-ar")
rev_series = next(s for s in rev_chart["series"] if s["name"] == "营业收入")
values = [p["value"] for p in rev_series["points"]]
check("营收换算为万元正确（9.5/11.0/13.2 亿 → 95000/110000/132000）",
      values == [95000.0, 110000.0, 132000.0], str(values))
check("年份标签正确", [p["label"] for p in rev_series["points"]] == ["2022", "2023", "2024"])

hc_chart = next(c for c in healthy if c["id"] == "chart-headcount-revenue")
hc_series = next(s for s in hc_chart["series"] if "参保人数" in s["name"])
check("人数不做万元换算（原值 1450/1520/1600）",
      [p["value"] for p in hc_series["points"]] == [1450.0, 1520.0, 1600.0],
      str([p["value"] for p in hc_series["points"]]))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("② 风险样本：多一张司法图")
print("=" * 74)

risk = charts.build_charts(RISK_FIELDS, EVIDENCE)
risk_ids = [c["id"] for c in risk]
print(f"       charts: {risk_ids}")
check("风险样本含司法风险图", "chart-judicial" in risk_ids)
judicial = next(c for c in risk if c["id"] == "chart-judicial")
check("司法图为 bar 类型", judicial["kind"] == "bar")
check("司法图取值为被执行5/失信3",
      [p["value"] for p in judicial["series"][0]["points"]] == [5.0, 3.0],
      str([p["value"] for p in judicial["series"][0]["points"]]))
check("司法图出处指向 EV-004", judicial["evidence_ids"] == ["EV-004"], str(judicial["evidence_ids"]))

# 命中信号时优先用信号自己的证据
with_rule = charts.build_charts(RISK_FIELDS, EVIDENCE, evidence_ids_by_rule={"S1": ["EV-001"]})
s1_chart = next(c for c in with_rule if c["id"] == "chart-profit-cashflow")
check("命中信号时图表复用该信号的证据", s1_chart["evidence_ids"] == ["EV-001"], str(s1_chart["evidence_ids"]))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("③ 数据不足 / 无出处 → 不画该图")
print("=" * 74)

partial = dict(HEALTHY_FIELDS)
partial["accounts_receivable_3y"] = None
ids_partial = [c["id"] for c in charts.build_charts(partial, EVIDENCE)]
check("缺应收账款时不画营收/应收图", "chart-revenue-ar" not in ids_partial, str(ids_partial))
check("其余图仍在", "chart-profit-cashflow" in ids_partial)

# 证据目录为空 → 全部图都无出处 → 返回空数组
check("无证据目录时返回空数组（不画无源图）", charts.build_charts(HEALTHY_FIELDS, []) == [])

# 序列含 null：保留 null 点而不是填 0
with_null = dict(HEALTHY_FIELDS)
with_null["net_profit_3y"] = [80_000_000, None, 120_000_000]
null_chart = next(
    (c for c in charts.build_charts(with_null, EVIDENCE) if c["id"] == "chart-profit-cashflow"),
    None,
)
check("含 null 的序列仍产图（保留断点）", null_chart is not None)
if null_chart:
    np_series = next(s for s in null_chart["series"] if s["name"] == "归母净利润")
    check("null 点原样保留（不插补成 0）",
          [p["value"] for p in np_series["points"]] == [8000.0, None, 12000.0],
          str([p["value"] for p in np_series["points"]]))

# ---------------------------------------------------------------------------
print()
print("=" * 74)
print("④ 开关与上限")
print("=" * 74)

_StubSettings.CHARTS_ENABLED = False
check("CHARTS_ENABLED=false → 空数组", charts.build_charts(HEALTHY_FIELDS, EVIDENCE) == [])
_StubSettings.CHARTS_ENABLED = True

_StubSettings.CHARTS_MAX = 2
check("CHARTS_MAX=2 → 最多 2 张", len(charts.build_charts(RISK_FIELDS, EVIDENCE)) == 2)
_StubSettings.CHARTS_MAX = 4
check("CHARTS_MAX=4 → 风险样本 4 张", len(charts.build_charts(RISK_FIELDS, EVIDENCE)) == 4)

# 字典外字段必须报错（不许自创字段）
try:
    charts.build_charts({"revenue_4y": [1, 2, 3]}, EVIDENCE)
except KeyError:
    check("字典外字段名被拒绝", True)
else:
    check("字典外字段名被拒绝", False, "竟然通过了")

print()
print("=" * 74)
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("charts.py 行为自检全部通过")
