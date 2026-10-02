# 四公司 Evidence 数据质量抽检报告

> 生成时间：2026-10-02 20:38:49
> 数据库：`cninfo.multicompany.next.db`
> 抽检脚本：`evidence_audit.py`；引文核验规则：`evidence_verify.py`
> 抽检范围：全量；URL 可达性实测：是

## 1. 汇总

| 指标 | 数值 |
|---|---|
| 抽检总条数 | 503 |
| `verified`（人工复核） | 3 |
| `auto`（机械校验通过） | 497 |
| `rejected`（不能支撑回答） | 0 |
| `excluded`（已隔离，不参与回答） | 3 |
| 缺该指标（无 Evidence） | 0 |
| 通过率（不含隔离） | 500/500 (100.0%) |

### 引文可核验性

| 项目 | 条数 |
|---|---:|
| 参与回答的证据 | 500 |
| 引文是标注页的**连续原文**（后端严格核验可通过） | 500 |
| 引文不是连续原文（需按 segment 核验） | 0 |
| 已隔离（不参与回答） | 3 |

> 「连续原文」= 忽略空白后，引文是标注页原文的一段**连续子串** —— 后端 `db.page_from_markers()` 用的就是这条口径。

### 引文归位情况

| 归位形态 | 条数 | 含义 |
|---|---:|---|
| `verbatim` | 500 | 整段引文（忽略空白）是该页**连续原文** |
| `segments` | 0 | 每个片段都在该页，但引文不是连续原文 |
| `failed` | 0 | 有片段在该页找不到 |
| `excluded` | 3 | 已隔离，不参与回答 |

> 引文已在候选库中重写为标注页的**连续原文**（见 `repair_evidence_quotes.py`），因此后端 `db.page_from_markers()` 的严格口径可以直接通过。若引文仍是 `segments` 形态（例如直接跑在稳定库上），后端应改为按 segment 核验，或先执行该修复脚本。

## 2. 逐公司逐指标明细（每指标最新 2 条，对应 §3 抽检要求）

### 思看科技（688583）

明细 20 条：verified 0、auto 20、rejected 0；缺指标 0。（该公司全量核验结果见第 1 节汇总与第 3 节）

| 指标 | Evidence ID | 报告期 | 页码 | 判定 | 归位 | 问题 |
|---|---:|---|---:|---|---|---|
| 应收账款 | 3238 | 2026H1 | 181 | 🟡 auto | verbatim | — |
| 应收账款 | 3229 | 2026 | 81 | 🟡 auto | verbatim | — |
| 资产负债率 | 3139 | 2024 | 400 | 🟡 auto | verbatim | — |
| 资产负债率 | 3138 | 2024 | 399 | 🟡 auto | verbatim | — |
| 每股收益 | 3220 | 2026H1 | 8 | 🟡 auto | verbatim | — |
| 每股收益 | 3219 | 2026H1 | 8 | 🟡 auto | verbatim | — |
| 存货 | 3242 | 2026H1 | 185 | 🟡 auto | verbatim | — |
| 存货 | 3239 | 2026H1 | 182 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3237 | 2026H1 | 178 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3214 | 2026H1 | 8 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3243 | 2026H1 | 185 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3226 | 2026H1 | 34 | 🟡 auto | verbatim | — |
| 研发费用 | 3240 | 2026H1 | 183 | 🟡 auto | verbatim | — |
| 研发费用 | 3233 | 2026H1 | 84 | 🟡 auto | verbatim | — |
| 研发投入 | 3223 | 2026H1 | 31 | 🟡 auto | verbatim | — |
| 研发投入 | 3181 | 2025H1 | 39 | 🟡 auto | verbatim | — |
| 营业收入 | 3224 | 2026H1 | 34 | 🟡 auto | verbatim | — |
| 营业收入 | 3213 | 2026H1 | 8 | 🟡 auto | verbatim | — |
| 总资产 | 3218 | 2026H1 | 8 | 🟡 auto | verbatim | — |
| 总资产 | 3206 | 2025H1 | 3 | 🟡 auto | verbatim | — |

### 恒生电子（600570）

明细 17 条：verified 0、auto 17、rejected 0；缺指标 0。（该公司全量核验结果见第 1 节汇总与第 3 节）

| 指标 | Evidence ID | 报告期 | 页码 | 判定 | 归位 | 问题 |
|---|---:|---|---:|---|---|---|
| 应收账款 | 3385 | 2022FY | 155 | 🟡 auto | verbatim | — |
| 应收账款 | 3383 | 2022FY | 115 | 🟡 auto | verbatim | — |
| 每股收益 | 3466 | 2026H1 | 3 | 🟡 auto | verbatim | — |
| 每股收益 | 3459 | 2025H1 | 3 | 🟡 auto | verbatim | — |
| 存货 | 3389 | 2022FY | 159 | 🟡 auto | verbatim | — |
| 存货 | 3386 | 2022FY | 155 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3463 | 2026H1 | 3 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3456 | 2025H1 | 3 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3465 | 2026H1 | 3 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3458 | 2025H1 | 3 | 🟡 auto | verbatim | — |
| 研发费用 | 3387 | 2022FY | 157 | 🟡 auto | verbatim | — |
| 研发费用 | 3364 | 2022FY | 19 | 🟡 auto | verbatim | — |
| 研发投入 | 3365 | 2022FY | 19 | 🟡 auto | verbatim | — |
| 营业收入 | 3462 | 2026H1 | 3 | 🟡 auto | verbatim | — |
| 营业收入 | 3455 | 2025H1 | 3 | 🟡 auto | verbatim | — |
| 总资产 | 3460 | 2026H1 | 3 | 🟡 auto | verbatim | — |
| 总资产 | 3453 | 2025H1 | 2 | 🟡 auto | verbatim | — |

### 中国长城（000066）

明细 14 条：verified 0、auto 14、rejected 0；缺指标 0。（该公司全量核验结果见第 1 节汇总与第 3 节）

| 指标 | Evidence ID | 报告期 | 页码 | 判定 | 归位 | 问题 |
|---|---:|---|---:|---|---|---|
| 应收账款 | 3282 | 2023H1 | 15 | 🟡 auto | verbatim | — |
| 资产负债率 | 3301 | 2026H1 | 4 | 🟡 auto | verbatim | — |
| 资产负债率 | 3293 | 2025H1 | 4 | 🟡 auto | verbatim | — |
| 每股收益 | 3298 | 2026H1 | 2 | 🟡 auto | verbatim | — |
| 每股收益 | 3290 | 2025H1 | 2 | 🟡 auto | verbatim | — |
| 存货 | 3283 | 2023H1 | 15 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3295 | 2026H1 | 2 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3287 | 2025H1 | 2 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3297 | 2026H1 | 2 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3289 | 2025H1 | 2 | 🟡 auto | verbatim | — |
| 营业收入 | 3294 | 2026H1 | 2 | 🟡 auto | verbatim | — |
| 营业收入 | 3286 | 2025H1 | 2 | 🟡 auto | verbatim | — |
| 总资产 | 3299 | 2026H1 | 2 | 🟡 auto | verbatim | — |
| 总资产 | 3291 | 2025H1 | 2 | 🟡 auto | verbatim | — |

### 贝达药业（300558）

明细 10 条：verified 0、auto 10、rejected 0；缺指标 0。（该公司全量核验结果见第 1 节汇总与第 3 节）

| 指标 | Evidence ID | 报告期 | 页码 | 判定 | 归位 | 问题 |
|---|---:|---|---:|---|---|---|
| 每股收益 | 3348 | 2026H1 | 1 | 🟡 auto | verbatim | — |
| 每股收益 | 3341 | 2025H1 | 1 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3345 | 2026H1 | 1 | 🟡 auto | verbatim | — |
| 归属于上市公司股东的净利润 | 3338 | 2025H1 | 1 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3347 | 2026H1 | 1 | 🟡 auto | verbatim | — |
| 经营活动产生的现金流量净额 | 3340 | 2025H1 | 1 | 🟡 auto | verbatim | — |
| 营业收入 | 3344 | 2026H1 | 1 | 🟡 auto | verbatim | — |
| 营业收入 | 3337 | 2025H1 | 1 | 🟡 auto | verbatim | — |
| 总资产 | 3349 | 2026H1 | 2 | 🟡 auto | verbatim | — |
| 总资产 | 3342 | 2025H1 | 1 | 🟡 auto | verbatim | — |

## 3. 全量核验：§3 白名单以外的证据

本节覆盖 `equity_attr`、`gross_margin`、`net_profit`、`net_profit_deducted`、`rd_ratio`、`risk_downstream_demand`、`risk_product_mix`、`risk_tech_edge` 等 135 条证据（`--full` 才有的范围）。其中 rejected 0 条。

全部通过机械校验。

## 4. rejected 明细（需要处理）

无。本次抽检没有不能支撑回答的 Evidence。

## 5. 已隔离证据（不参与回答）

这些记录在候选库中被显式标记为 `evidence.excluded = 1`，**不参与任何回答**，保留在库中留档。它们的问题无法从原文推断（如 `period` 为空），因此不臆造字段值。

- **思看科技**（688583） evidence `3104`，metric `net_profit`，document `299`，page 325
  - 已隔离，不参与回答：规则抽取缺陷：period 为空且 value 抽错（写入 2024.0，引文实为净资产收益率/每股收益），无法定位报告期与数值
- **思看科技**（688583） evidence `3103`，metric `net_profit`，document `299`，page 325
  - 已隔离，不参与回答：规则抽取缺陷：period 为空且 value 抽错（写入 2024.0，引文实为净资产收益率/每股收益），无法定位报告期与数值
- **思看科技**（688583） evidence `3127`，metric `rd_expense`，document `299`，page 361
  - 已隔离，不参与回答：规则抽取缺陷：period 为空，无法定位报告期

## 6. source_url 可达性实测

抽检涉及 30 个去重 URL，实测可达 30 个，不可达 0 个。

全部返回 HTTP 200。

## 7. 结论与限制

- 本报告的 `auto` **只代表机械校验通过**，不代表人工复核；不要把 auto 说成 verified。
- 引文核验按 segment 做：文字标签必须在该页出现，每个数字必须是该页的**完整数字**（去千分位后值相等）。
- 500 条引文是标注页的**连续原文**，后端严格核验（忽略空白后连续子串）可直接通过。
- 引文形态由 `repair_evidence_quotes.py` 重写：从标注页原文里截取覆盖全部内容片段的最小区间，**未改动任何数字、页码或 URL**；原始引文保留在 `evidence.original_quote` 里可回溯。
- `evidence.excluded = 1` 的记录被隔离，不参与回答；它们的问题无法从原文推断，因此不臆造字段值。
- 本报告只做机械核验；**未**把 auto 提升为 verified。
