"""X-Ray 企业穿透分析 · charts 生成（确定性，可追溯）。

需求：响应体的 `charts` 字段（array）。这里给出**不依赖大模型**的确定性实现：
图表数据全部来自数据字典的 15 个字段，且每个图都挂上真实 `evidence_ids`，
因此可以逐点回查，不会出现「图上的数字没有出处」。

图表种类与数据源：
  1. 营收与应收账款（line）      ← revenue_3y + accounts_receivable_3y
  2. 净利润与经营现金流（line）  ← net_profit_3y + oper_cashflow_3y
  3. 员工人数与营收增长（line）  ← social_security_count_3y + revenue_3y
  4. 被执行与失信记录（bar）     ← executed_count + dishonest_count
最多返回 CHARTS_MAX（默认 4）个；某图数据缺失时跳过该图而不是画 0。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from config import settings
from models import DICT_FIELD_NAMES

#: 三个完整会计年度的标签（与 mock_data.FISCAL_YEARS 对齐）
YEAR_LABELS: tuple[str, ...] = ("2022", "2023", "2024")

#: 万元换算（图表 Y 轴用「万元」，避免大数字挤在一起）
WAN = 10_000.0


def _series(value: Any) -> list[float | None] | None:
    """把字段值标准化成序列；非法一律 None（不插补、不用 0 冒充）。"""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    out: list[float | None] = []
    for item in value:
        if isinstance(item, bool) or item is None:
            out.append(None)
        elif isinstance(item, (int, float)):
            out.append(float(item))
        else:
            out.append(None)
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _points(labels: Sequence[str], values: Sequence[float | None], scale: float = WAN) -> list[dict[str, Any]]:
    """组装点列表；缺值点保留 null（前端会画断点，而不是伪造 0）。"""
    points: list[dict[str, Any]] = []
    for label, value in zip(labels, values):
        points.append({"label": label, "value": None if value is None else round(value / scale, 2)})
    return points


def _evidence_ids(explicit: Sequence[str], catalog: Sequence[dict[str, Any]], fields: Sequence[str]) -> list[str]:
    """优先用显式给定的证据 id；否则按字段交集从证据目录里挑。

    ⚠️ 一个都挑不到时返回空列表 —— 调用方必须据此**丢弃该图**，
    绝不允许出现没有出处的图表。
    """
    if explicit:
        return list(explicit)
    wanted = set(fields)
    picked = [
        str(row["evidence_id"])
        for row in catalog
        if wanted & set(row.get("field_names") or [])
    ]
    return picked


def build_charts(
    fields: dict[str, Any],
    evidence_catalog: Sequence[dict[str, Any]] | None = None,
    *,
    evidence_ids_by_rule: dict[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """按字段生成图表数组（确定性，不调用大模型）。

    :param fields: 公司 15 个字段（字段名必须在数据字典内）
    :param evidence_catalog: 证据目录（用于给每个图挂真实 evidence_ids）
    :param evidence_ids_by_rule: 规则编号 → 证据 id（命中信号时可让图表与信号同源）
    """
    if not settings.CHARTS_ENABLED:
        return []

    illegal = [k for k in fields if k not in DICT_FIELD_NAMES]
    if illegal:
        raise KeyError(f"charts 输入含数据字典外的字段名: {illegal}")

    catalog = list(evidence_catalog or [])
    by_rule = dict(evidence_ids_by_rule or {})
    year_labels = list(YEAR_LABELS)

    charts: list[dict[str, Any]] = []

    def add(
        chart_id: str,
        title: str,
        kind: str,
        series: list[dict[str, Any] | None],
        field_names: Sequence[str],
        rule_id: str | None = None,
        note: str = "",
    ) -> None:
        # 数据不足的序列返回 None → 丢弃整个图（而不是画一条半空的线）
        present = [s for s in series if s is not None]
        if not present:
            return
        ids = _evidence_ids(by_rule.get(rule_id or "", []), catalog, field_names)
        if not ids:
            # 没有出处的图直接不输出（宁可少一个图，也不要无源数据）
            return
        charts.append(
            {
                "id": chart_id,
                "title": title,
                "kind": kind,
                "unit": "万元",
                "series": present,
                "evidence_ids": ids,
                "note": note,
            }
        )

    # ---- 1. 营收与应收账款（S2 的直观对照）----
    revenue = _series(fields.get("revenue_3y"))
    receivable = _series(fields.get("accounts_receivable_3y"))
    if revenue and receivable:
        add(
            "chart-revenue-ar",
            "营收与应收账款",
            "line",
            [
                {"name": "营业收入", "points": _points(year_labels, revenue)},
                {"name": "应收账款", "points": _points(year_labels, receivable)},
            ],
            ("revenue_3y", "accounts_receivable_3y"),
            rule_id="S2",
            note="单位：万元；两条线的增速差即 S2 营收注水嫌疑的判定依据。",
        )

    # ---- 2. 净利润与经营现金流（S1 的直观对照）----
    net_profit = _series(fields.get("net_profit_3y"))
    cashflow = _series(fields.get("oper_cashflow_3y"))
    if net_profit and cashflow:
        add(
            "chart-profit-cashflow",
            "净利润与经营活动现金流",
            "line",
            [
                {"name": "归母净利润", "points": _points(year_labels, net_profit)},
                {"name": "经营活动现金流净额", "points": _points(year_labels, cashflow)},
            ],
            ("net_profit_3y", "oper_cashflow_3y"),
            rule_id="S1",
            note="单位：万元；两者方向背离即 S1 利润含金量疑云。",
        )

    # ---- 3. 员工人数与营收（S3 的直观对照）----
    headcount = _series(fields.get("social_security_count_3y"))
    if headcount and revenue:
        add(
            "chart-headcount-revenue",
            "社保参保人数与营收",
            "line",
            [
                # 人数用「人」为单位，不做万元换算
                {"name": "社保参保人数（人）", "points": _points(year_labels, headcount, scale=1.0)},
                {"name": "营业收入（万元）", "points": _points(year_labels, revenue)},
            ],
            ("social_security_count_3y", "revenue_3y"),
            rule_id="S3",
            note="左侧读数用于观察「营收增而人数减」的背离。",
        )

    # ---- 4. 被执行与失信记录（S4，存在即标记）----
    executed = _number(fields.get("executed_count"))
    dishonest = _number(fields.get("dishonest_count"))
    if executed is not None and dishonest is not None and (executed + dishonest) > 0:
        add(
            "chart-judicial",
            "司法风险记录",
            "bar",
            [
                {
                    "name": "记录数（条）",
                    "points": [
                        {"label": "被执行人", "value": executed},
                        {"label": "失信被执行人", "value": dishonest},
                    ],
                }
            ],
            ("executed_count", "dishonest_count"),
            rule_id="S4",
            note="被执行 + 失信 > 0 即命中 S4（无需推理配对）。",
        )

    return charts[: max(1, settings.CHARTS_MAX)]


__all__ = ["WAN", "YEAR_LABELS", "build_charts"]
