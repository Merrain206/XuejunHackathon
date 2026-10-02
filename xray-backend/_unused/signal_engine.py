"""X-Ray 企业穿透分析 · 4 对矛盾信号规则引擎。

硬性约束（数据字典第 6 节 / 需求[六]）：
  * 纯函数，禁止调 LLM —— 本模块只 import 标准库，绝无任何网络或模型调用；
  * 只有 4 对信号，其他任何结论不许输出；
  * 输出 {type, title, side, severity, description}，severity 按差值幅度分档；
  * 判定以数据字典 v0.4 为准，字段名只能是字典里的 15 个。

判定阈值（确定性常量；字典只给方向、未给数值，故在此显式定义并集中管理）：

  S1 净利润↑ 且 经营现金流↓
      现金流降幅 ≥50% → high｜≥25% → medium｜≥10% → low；最小触发降幅 min_cash_drop = 10%
  S2 营收增长率 < 应收账款增长率
      差值 ≥20 个百分点 → high｜≥10 → medium｜≥5 → low；最小触发差值 min_ar_gap = 5pp
  S3 营收↑ 且 参保人数↓
      营收增幅 ≥10%（min_revenue_growth）且人数降幅 ≥5%（min_headcount_drop）
      人数降幅 ≥20% → high｜≥10% → medium｜其余 → low
  S4 被执行 + 失信 > 0（存在即标记，无需推理配对）
      被执行 ≥3 次或失信 ≥1 次 → high｜合计 ≥2 → medium｜合计 =1 → low

严重度升档（字典「最新变化捕捉」维度的用途）：
  S1/S2/S3 的 severity 由近三年序列差值定档；若最新一期季报出现方向性反转
  （净利润由正转负 / 应收账款增速继续跑赢营收 / 趋势未修复），升一档。
  latest_quarter_* 只用于方向判断，绝不与年报做绝对值比较（字典明文禁止）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from models import DICT_FIELD_NAMES, SIGNAL_IDS, SIGNAL_NAMES, utcnow

# ---------------------------------------------------------------------------
# 阈值常量（集中管理，便于审计）
# ---------------------------------------------------------------------------

MIN_CASH_DROP = 0.10          # S1：经营现金流最小触发降幅
MIN_AR_GAP = 0.05             # S2：应收账款增速需超过营收增速的最小差值（百分点/100）
MIN_REVENUE_GROWTH = 0.10      # S3：营收最小增幅
MIN_HEADCOUNT_DROP = 0.05      # S3：参保人数最小降幅

CASH_DROP_HIGH = 0.50
CASH_DROP_MEDIUM = 0.25

AR_GAP_HIGH = 0.20
AR_GAP_MEDIUM = 0.10

HEADCOUNT_DROP_HIGH = 0.20
HEADCOUNT_DROP_MEDIUM = 0.10

EXECUTED_HIGH = 3              # S4：被执行次数达到即 high
TOTAL_MEDIUM = 2               # S4：合计达到即 medium

#: 每条 signal_result 唯一 id 前缀（配合 rule_name 拼出 SIG-S1 形式）
SIGNAL_ROW_PREFIX = "SIG"

#: 风险维度 → 中文标签（写入 coverage）
DIMENSION_REVENUE = "盈利质量"
DIMENSION_CASH = "现金真实性"
DIMENSION_ASSET = "资产健康度"
DIMENSION_STABILITY = "经营稳定性"
DIMENSION_JUDICIAL = "司法风险"


@dataclass(frozen=True)
class RuleDescriptor:
    """规则元数据：名称与判定条件取自数据字典第三节。"""

    rule_id: str
    title: str
    logic: str
    dimension: str
    field_names: tuple[str, ...]
    meaning: str


SIGNAL_RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor(
        rule_id="S1",
        title=SIGNAL_NAMES["S1"],
        logic="净利润↑ 但 经营现金流↓",
        dimension=DIMENSION_CASH,
        field_names=(
            "net_profit_3y",
            "oper_cashflow_3y",
            "latest_quarter_profit",
            "latest_quarter_cashflow",
        ),
        meaning="利润是账面的，钱没进来。可能存在的风险：收入确认激进、应收账款堆积、利润操纵",
    ),
    RuleDescriptor(
        rule_id="S2",
        title=SIGNAL_NAMES["S2"],
        logic="营收增长率 < 应收账款增长率",
        dimension=DIMENSION_ASSET,
        field_names=(
            "revenue_3y",
            "accounts_receivable_3y",
            "latest_quarter_revenue",
        ),
        meaning="货卖了，钱没收回来。可能存在的风险：冲量虚增营收、客户回款能力恶化、坏账风险",
    ),
    RuleDescriptor(
        rule_id="S3",
        title=SIGNAL_NAMES["S3"],
        logic="营收↑ 但 参保人数↓",
        dimension=DIMENSION_STABILITY,
        field_names=("revenue_3y", "social_security_count_3y"),
        meaning="人少了业绩涨了，可疑。可能存在的风险：数据造假、大量外包未披露、业务模式异常",
    ),
    RuleDescriptor(
        rule_id="S4",
        title=SIGNAL_NAMES["S4"],
        logic="被执行 + 失信 > 0",
        dimension=DIMENSION_JUDICIAL,
        field_names=("executed_count", "dishonest_count"),
        meaning="直接风险信号，不需要推理配对。存在即标记",
    ),
)

#: 3 条需要配对的规则（S4 是直判）
PAIRING_RULES: tuple[str, ...] = ("S1", "S2", "S3")


@dataclass
class SignalOutcome:
    """单条信号的判定结果（与 signal_result 表结构对应）。"""

    id: str
    type: str
    title: str
    side: str
    severity: str
    description: str
    triggered: bool
    reason: str
    field_names: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    computed_at: Any = None

    def to_signal_dict(self, evidence_ids: Sequence[str]) -> dict[str, Any]:
        """转成响应体 signals[] 的元素（schemas.Signal 的 6 个字段）。"""
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "side": self.side,
            "severity": self.severity,
            "description": self.description,
            "evidence_ids": list(evidence_ids),
        }

    def to_row(self) -> dict[str, Any]:
        """转成 signal_result 表的写入字典。"""
        return {
            "rule_name": self.type,
            "triggered": self.triggered,
            "side": self.side,
            "severity": self.severity,
            "reason": self.reason,
            "field_names": list(self.field_names),
            "computed_at": self.computed_at or utcnow(),
        }


# ---------------------------------------------------------------------------
# 取数与算术工具（纯函数，零副作用）
# ---------------------------------------------------------------------------


def _clean_number(value: Any) -> float | None:
    """把任意入库值转成有限浮点数；非法/缺失一律 None（不插补、不默认 0）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _clean_series(value: Any) -> list[float | None] | None:
    """把时间序列标准化为长度≥2 的列表；含 null 的位置原样保留（字典要求不插补）。"""
    if isinstance(value, (list, tuple)):
        series = [_clean_number(v) for v in value]
    elif value is None:
        return None
    else:
        single = _clean_number(value)
        if single is None:
            return None
        series = [single]
    if len(series) < 2:
        return None
    return series


def _final_value(series: Sequence[float | None]) -> float | None:
    """序列最后一个非空值（用于计数型序列）。"""
    for value in reversed(list(series)):
        if value is not None:
            return value
    return None


def _growth(first: float | None, last: float | None) -> float | None:
    """增长率。

    基准为正 → 常规比值；
    基准为 0 → 无定义，返回 None（不编造增长率）；
    基准为负 → 用绝对值做分母（负→更正/更负的方向性比较，仅在符号相同时可比）。
    """
    if first is None or last is None or first == 0:
        return None
    if first > 0 and last >= 0:
        return (last - first) / first
    if first < 0 and last <= 0:
        # 同为负：亏损收窄 = 正向改善
        return (abs(first) - abs(last)) / abs(first)
    return None


def _relative_drop(first: float | None, last: float | None) -> float | None:
    """降幅（0.5 = 下降 50%）；不是下降则返回 None。

    以序列内的最大绝对值为分母，可正确处理正负穿越（如 +2 亿 → -1 亿）。
    """
    if first is None or last is None or last >= first:
        return None
    denominator = abs(first) if abs(first) > 0 else None
    if denominator is None:
        return None
    drop = (first - last) / denominator
    return drop if drop > 0 else None


def _increasing(series: Sequence[float | None], min_growth: float = 0.0) -> bool:
    """首尾均非空且严格增长（可选最小增幅）。"""
    first, last = series[0], series[-1]
    if first is None or last is None or last <= first:
        return False
    if min_growth <= 0:
        return True
    growth = _growth(first, last)
    return growth is not None and growth >= min_growth


def _decreasing(series: Sequence[float | None], min_drop: float = 0.0) -> bool:
    """首尾均非空且严格下降（可选最小降幅）。"""
    first, last = series[0], series[-1]
    if first is None or last is None or last >= first:
        return False
    if min_drop <= 0:
        return True
    drop = _relative_drop(first, last)
    return drop is not None and drop >= min_drop


def _pct(value: float | None, digits: int = 1) -> str:
    """百分比文案；None → 「数据缺失」。"""
    if value is None:
        return "数据缺失"
    return f"{value * 100:.{digits}f}%"


def _yi(value: float | None, digits: int = 2) -> str:
    """元 → 亿元文案（便于人读）；None → 「数据缺失」。"""
    if value is None:
        return "数据缺失"
    return f"{value / 1e8:.{digits}f}亿"


def _pp(value: float | None, digits: int = 1) -> str:
    """差值 → 百分点文案。"""
    if value is None:
        return "数据缺失"
    return f"{value * 100:.{digits}f}个百分点"


_SEVERITY_ORDER = ("low", "medium", "high")


def _upgrade(severity: str) -> str:
    """升一档（封顶 high）。"""
    try:
        idx = _SEVERITY_ORDER.index(severity)
    except ValueError:
        return severity
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def _all_present(series: Sequence[float | None] | None) -> bool:
    """序列存在且每一期都有值（字典：某年缺失标 null，不许插补 → 缺失则不算）。"""
    return bool(series) and all(v is not None for v in series)


def get_field_value(fields: dict[str, Any], name: str) -> Any:
    """从字段字典取字典字段值；字段名不在字典内直接报错（防止自创字段）。"""
    if name not in DICT_FIELD_NAMES:
        raise KeyError(f"{name!r} 不是数据字典 v0.4 的字段名，禁止自创字段")
    return fields.get(name)


def make_signal_id(rule_id: str) -> str:
    """signal_result 的行 id 形如 SIG-S1（稳定、可预测）。"""
    return f"{SIGNAL_ROW_PREFIX}-{rule_id}"


# ---------------------------------------------------------------------------
# 各条规则的判定（逐条独立，便于单测）
# ---------------------------------------------------------------------------


def _skipped(rule: RuleDescriptor, fields: dict[str, Any], missing: list[str], note: str) -> SignalOutcome:
    """与数据字典无关的字段缺失 → 不误报。"""
    return SignalOutcome(
        id=make_signal_id(rule.rule_id),
        type=rule.rule_id,
        title=rule.title,
        side="positive",
        severity="low",
        description=f"未触发（数据不足以判定）：{note}",
        triggered=False,
        reason=f"字段缺失，规则未参与判定：{note}",
        field_names=list(rule.field_names),
        missing_fields=missing,
        computed_at=utcnow(),
    )


def _evaluate_s1(fields: dict[str, Any]) -> SignalOutcome:
    """S1 利润含金量疑云：净利润↑ 但 经营现金流↓。"""
    rule = SIGNAL_RULES[0]
    profit = _clean_series(get_field_value(fields, "net_profit_3y"))
    cashflow = _clean_series(get_field_value(fields, "oper_cashflow_3y"))

    if profit is None or cashflow is None:
        return _skipped(
            rule,
            fields,
            ["net_profit_3y" if profit is None else "", "oper_cashflow_3y" if cashflow is None else ""],
            "缺少近三年净利润或经营现金流序列",
        )

    profit_up = _increasing(profit)
    drop = _relative_drop(cashflow[0], cashflow[-1])
    cash_down = drop is not None and drop >= MIN_CASH_DROP

    latest_profit = _clean_number(get_field_value(fields, "latest_quarter_profit"))
    latest_cash = _clean_number(get_field_value(fields, "latest_quarter_cashflow"))
    reversal = latest_profit is not None and latest_profit < 0

    profit_growth = _growth(profit[0], profit[-1])
    reason = (
        f"净利润 {_yi(profit[0])}→{_yi(profit[-1])}（{_pct(profit_growth)}），"
        f"经营现金流 {_yi(cashflow[0])}→{_yi(cashflow[-1])}（-{_pct(drop)}）"
    )

    if not (profit_up and cash_down):
        return SignalOutcome(
            id=make_signal_id(rule.rule_id),
            type=rule.rule_id,
            title=rule.title,
            side="positive",
            severity="low",
            description=f"未触发：{reason}，方向一致，未出现「净利增而现金降」",
            triggered=False,
            reason=reason,
            field_names=list(rule.field_names),
            computed_at=utcnow(),
        )

    if drop >= CASH_DROP_HIGH:
        severity = "high"
    elif drop >= CASH_DROP_MEDIUM:
        severity = "medium"
    else:
        severity = "low"

    upgrades: list[str] = []
    if reversal:
        severity = _upgrade(severity)
        upgrades.append("最新一期季报净利润为负（方向反转）")
    if latest_cash is not None and latest_cash < 0:
        severity = _upgrade(severity)
        upgrades.append("最新一期经营现金流为负")

    description = (
        f"净利润 {_yi(profit[0])}→{_yi(profit[-1])}（{_pct(profit_growth)}），"
        f"但经营现金流 {_yi(cashflow[0])}→{_yi(cashflow[-1])}（-{_pct(drop)}），"
        f"利润与现金方向背离。{rule.meaning}"
    )
    if upgrades:
        description += f"（升档依据：{'；'.join(upgrades)}）"

    return SignalOutcome(
        id=make_signal_id(rule.rule_id),
        type=rule.rule_id,
        title=rule.title,
        side="negative",
        severity=severity,
        description=description,
        triggered=True,
        reason=reason + (f"；{'；'.join(upgrades)}" if upgrades else ""),
        field_names=list(rule.field_names),
        computed_at=utcnow(),
    )


def _evaluate_s2(fields: dict[str, Any]) -> SignalOutcome:
    """S2 营收注水嫌疑：营收增长率 < 应收账款增长率。"""
    rule = SIGNAL_RULES[1]
    revenue = _clean_series(get_field_value(fields, "revenue_3y"))
    receivable = _clean_series(get_field_value(fields, "accounts_receivable_3y"))

    if revenue is None or receivable is None:
        return _skipped(
            rule,
            fields,
            ["revenue_3y" if revenue is None else "", "accounts_receivable_3y" if receivable is None else ""],
            "缺少近三年营收或应收账款序列",
        )

    rev_growth = _growth(revenue[0], revenue[-1])
    ar_growth = _growth(receivable[0], receivable[-1])

    if rev_growth is None or ar_growth is None:
        return _skipped(rule, fields, [], "营收取应收账款的基期为 0 或符号不一致，增长率无法定义")

    gap = ar_growth - rev_growth
    reason = (
        f"营收增长率 {_pct(rev_growth)}，应收账款增长率 {_pct(ar_growth)}，"
        f"应收账款快 {_pp(gap)}"
    )

    # 字典口径：营收增长 且 应收账款增速更快
    if not (rev_growth > 0 and gap >= MIN_AR_GAP):
        return SignalOutcome(
            id=make_signal_id(rule.rule_id),
            type=rule.rule_id,
            title=rule.title,
            side="positive",
            severity="low",
            description=f"未触发：{reason}，回款质量未见异常",
            triggered=False,
            reason=reason,
            field_names=list(rule.field_names),
            computed_at=utcnow(),
        )

    if gap >= AR_GAP_HIGH:
        severity = "high"
    elif gap >= AR_GAP_MEDIUM:
        severity = "medium"
    else:
        severity = "low"

    upgrades: list[str] = []
    latest_revenue = _clean_number(get_field_value(fields, "latest_quarter_revenue"))
    if latest_revenue is not None and latest_revenue <= 0:
        severity = _upgrade(severity)
        upgrades.append("最新一期营收为负（方向反转）")

    description = (
        f"营收增长率 {_pct(rev_growth)} 低于应收账款增长率 {_pct(ar_growth)}"
        f"（相差 {_pp(gap)}），{rule.meaning}"
    )
    if upgrades:
        description += f"（升档依据：{'；'.join(upgrades)}）"

    return SignalOutcome(
        id=make_signal_id(rule.rule_id),
        type=rule.rule_id,
        title=rule.title,
        side="negative",
        severity=severity,
        description=description,
        triggered=True,
        reason=reason + (f"；{'；'.join(upgrades)}" if upgrades else ""),
        field_names=list(rule.field_names),
        computed_at=utcnow(),
    )


def _evaluate_s3(fields: dict[str, Any]) -> SignalOutcome:
    """S3 人力数据异常：营收↑ 但 参保人数↓。"""
    rule = SIGNAL_RULES[2]
    revenue = _clean_series(get_field_value(fields, "revenue_3y"))
    headcount = _clean_series(get_field_value(fields, "social_security_count_3y"))

    if revenue is None or headcount is None:
        return _skipped(
            rule,
            fields,
            ["revenue_3y" if revenue is None else "", "social_security_count_3y" if headcount is None else ""],
            "缺少近三年营收或社保参保人数序列（社保可能未公示）",
        )

    # 字典允许社保部分年份缺失，但配对判定需要首尾可比
    if not _all_present(headcount):
        return _skipped(
            rule, fields, ["social_security_count_3y"], "社保参保人数存在未公示年份，首尾不可比，按不触发处理"
        )

    rev_growth = _growth(revenue[0], revenue[-1])
    hc_drop = _relative_drop(headcount[0], headcount[-1])
    revenue_up = rev_growth is not None and rev_growth >= MIN_REVENUE_GROWTH
    headcount_down = hc_drop is not None and hc_drop >= MIN_HEADCOUNT_DROP

    reason = (
        f"营收 {_yi(revenue[0])}→{_yi(revenue[-1])}（{_pct(rev_growth)}），"
        f"参保人数 {headcount[0]:.0f}→{headcount[-1]:.0f}（-{_pct(hc_drop)}）"
    )

    if not (revenue_up and headcount_down):
        return SignalOutcome(
            id=make_signal_id(rule.rule_id),
            type=rule.rule_id,
            title=rule.title,
            side="positive",
            severity="low",
            description=f"未触发：{reason}，人力与营收方向一致",
            triggered=False,
            reason=reason,
            field_names=list(rule.field_names),
            computed_at=utcnow(),
        )

    if hc_drop >= HEADCOUNT_DROP_HIGH:
        severity = "high"
    elif hc_drop >= HEADCOUNT_DROP_MEDIUM:
        severity = "medium"
    else:
        severity = "low"

    description = (
        f"营收增长 {_pct(rev_growth)} 的同时参保人数下降 {_pct(hc_drop)}"
        f"（{headcount[0]:.0f}→{headcount[-1]:.0f}人），{rule.meaning}"
    )

    return SignalOutcome(
        id=make_signal_id(rule.rule_id),
        type=rule.rule_id,
        title=rule.title,
        side="negative",
        severity=severity,
        description=description,
        triggered=True,
        reason=reason,
        field_names=list(rule.field_names),
        computed_at=utcnow(),
    )


def _evaluate_s4(fields: dict[str, Any]) -> SignalOutcome:
    """S4 司法风险直判：被执行 + 失信 > 0（存在即标记）。"""
    rule = SIGNAL_RULES[3]
    executed = _clean_number(get_field_value(fields, "executed_count"))
    dishonest = _clean_number(get_field_value(fields, "dishonest_count"))

    if executed is None or dishonest is None:
        return _skipped(
            rule,
            fields,
            ["executed_count" if executed is None else "", "dishonest_count" if dishonest is None else ""],
            "缺少被执行人/失信被执行人记录数",
        )

    executed_i = int(executed)
    dishonest_i = int(dishonest)
    total = executed_i + dishonest_i
    reason = f"被执行人记录 {executed_i} 条，失信被执行人记录 {dishonest_i} 条"

    if total <= 0:
        return SignalOutcome(
            id=make_signal_id(rule.rule_id),
            type=rule.rule_id,
            title=rule.title,
            side="positive",
            severity="low",
            description=f"未触发：{reason}，无司法风险记录",
            triggered=False,
            reason=reason,
            field_names=list(rule.field_names),
            computed_at=utcnow(),
        )

    if executed_i >= EXECUTED_HIGH or dishonest_i >= 1:
        severity = "high"
    elif total >= TOTAL_MEDIUM:
        severity = "medium"
    else:
        severity = "low"

    description = f"存在司法风险记录：{reason}，合计 {total} 条。{rule.meaning}"

    return SignalOutcome(
        id=make_signal_id(rule.rule_id),
        type=rule.rule_id,
        title=rule.title,
        side="negative",
        severity=severity,
        description=description,
        triggered=True,
        reason=reason,
        field_names=list(rule.field_names),
        computed_at=utcnow(),
    )


_EVALUATORS = {
    "S1": _evaluate_s1,
    "S2": _evaluate_s2,
    "S3": _evaluate_s3,
    "S4": _evaluate_s4,
}


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def evaluate_rule(rule_id: str, fields: dict[str, Any]) -> SignalOutcome:
    """判定单条规则。rule_id 只能是 S1-S4。"""
    if rule_id not in SIGNAL_IDS:
        raise ValueError(f"未知信号编号 {rule_id!r}；只允许 {SIGNAL_IDS}")
    return _EVALUATORS[rule_id](fields)


def compute_signals(fields: dict[str, Any]) -> list[SignalOutcome]:
    """按 S1→S4 顺序返回全部 4 条结果（命中与未命中都返回）。

    纯函数：不读写数据库、不发网络请求、不调 LLM。
    """
    if not isinstance(fields, dict):
        raise TypeError("fields 必须是 {字段名: 值} 字典")
    illegal = [k for k in fields if k not in DICT_FIELD_NAMES]
    if illegal:
        raise KeyError(f"输入含数据字典外的字段名，禁止使用: {illegal}")
    return [evaluate_rule(rule_id, fields) for rule_id in SIGNAL_IDS]


def triggered_signals(fields: dict[str, Any]) -> list[SignalOutcome]:
    """只返回命中的信号（前端信号卡只画命中的）。"""
    return [o for o in compute_signals(fields) if o.triggered]


def rule_catalog() -> list[dict[str, Any]]:
    """规则目录（供 /health capabilities 与文档使用）。"""
    return [
        {
            "rule_id": r.rule_id,
            "title": r.title,
            "logic": r.logic,
            "dimension": r.dimension,
            "field_names": list(r.field_names),
            "meaning": r.meaning,
        }
        for r in SIGNAL_RULES
    ]


def summarize(signals: Sequence[SignalOutcome]) -> dict[str, Any]:
    """汇总：命中的编号、最高严重度、涉及维度。"""
    hit = [s for s in signals if s.triggered]
    rank = {"high": 3, "medium": 2, "low": 1}
    top = max((s.severity for s in hit), key=lambda sev: rank.get(sev, 0), default=None)
    rule_dimension = {r.rule_id: r.dimension for r in SIGNAL_RULES}
    return {
        "hit_rule_ids": [s.type for s in hit],
        "hit_count": len(hit),
        "top_severity": top,
        "dimensions": sorted({rule_dimension[s.type] for s in hit if s.type in rule_dimension}),
        "negative_count": sum(1 for s in hit if s.side == "negative"),
        "skipped_rule_ids": [s.type for s in signals if not s.triggered and s.missing_fields],
    }


__all__ = [
    "AR_GAP_HIGH",
    "AR_GAP_MEDIUM",
    "CASH_DROP_HIGH",
    "CASH_DROP_MEDIUM",
    "EXECUTED_HIGH",
    "HEADCOUNT_DROP_HIGH",
    "HEADCOUNT_DROP_MEDIUM",
    "MIN_AR_GAP",
    "MIN_CASH_DROP",
    "MIN_HEADCOUNT_DROP",
    "MIN_REVENUE_GROWTH",
    "PAIRING_RULES",
    "RuleDescriptor",
    "SIGNAL_RULES",
    "SignalOutcome",
    "TOTAL_MEDIUM",
    "compute_signals",
    "evaluate_rule",
    "get_field_value",
    "make_signal_id",
    "rule_catalog",
    "summarize",
    "triggered_signals",
]
