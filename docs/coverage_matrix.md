# 四公司 Evidence 覆盖矩阵

> 生成时间：2026-10-02 20:38:49
> 生成脚本：`build_company_catalog.py`

矩阵里的数字是**该指标在 evidence 表里的条数**。
`—` 表示该指标在这家公司的库内**没有证据** —— 这是缺口，不是零值，不允许用模型常识补全。

| 指标 | 思看科技<br>`688583` | 恒生电子<br>`600570` | 中国长城<br>`000066` | 贝达药业<br>`300558` |
|---|---:|---:|---:|---:|
| 营业收入 (`revenue`) ★ | 42 | 18 | 9 | 8 |
| 归属于上市公司股东的净利润 (`net_profit_attr`) ★ | 31 | 15 | 8 | 8 |
| 经营活动产生的现金流量净额 (`operating_cash_flow`) ★ | 30 | 17 | 8 | 8 |
| 总资产 (`total_assets`) ★ | 10 | 9 | 4 | 6 |
| 每股收益 (`eps`) ★ | 12 | 10 | 4 | 6 |
| 净利润 (`net_profit`) | 43 | 8 | 3 | — |
| 扣除非经常性损益后的净利润 (`net_profit_deducted`) | 8 | 14 | 5 | 7 |
| 归属于上市公司股东的净资产 (`equity_attr`) | 10 | 9 | 4 | 6 |
| 资产负债率 (`debt_ratio`) | 7 | — | 5 | — |
| 研发费用 (`rd_expense`) | 26 | 5 | — | — |
| 研发投入 (`rd_investment`) | 4 | 1 | — | — |
| 研发投入占营业收入比例 (`rd_ratio`) | 8 | — | — | — |
| 应收账款 (`accounts_receivable`) | 20 | 5 | 1 | — |
| 存货 (`inventory`) | 25 | 5 | 1 | — |
| 毛利率 (`gross_margin`) | 7 | — | — | — |

★ = 必查核心指标。

## 逐公司可回答问题

### 思看科技（688583）

| 问题 | 需要的指标 | 覆盖 | 可回答 | 缺口 |
|---|---|---|---|---|
| 你的收入结构和变化是什么？ | revenue | 1/1 | ✅ | — |
| 你最近真的赚钱吗？ | net_profit_attr、net_profit_deducted、operating_cash_flow、revenue | 4/4 | ✅ | — |
| 你的资产规模和资产负债情况如何？ | total_assets、debt_ratio | 2/2 | ✅ | — |
| 你的每股收益表现如何？ | eps | 1/1 | ✅ | — |
| 你的研发投入情况如何？ | rd_expense、rd_investment、rd_ratio | 3/3 | ✅ | — |
| 你的应收账款和存货情况如何？ | accounts_receivable、inventory | 2/2 | ✅ | — |

### 恒生电子（600570）

| 问题 | 需要的指标 | 覆盖 | 可回答 | 缺口 |
|---|---|---|---|---|
| 你的收入结构和变化是什么？ | revenue | 1/1 | ✅ | — |
| 你最近真的赚钱吗？ | net_profit_attr、net_profit_deducted、operating_cash_flow、revenue | 4/4 | ✅ | — |
| 你的资产规模和资产负债情况如何？ | total_assets、debt_ratio | 1/2 | ⬜ | debt_ratio |
| 你的每股收益表现如何？ | eps | 1/1 | ✅ | — |
| 你的研发投入情况如何？ | rd_expense、rd_investment、rd_ratio | 2/3 | ⬜ | rd_ratio |
| 你的应收账款和存货情况如何？ | accounts_receivable、inventory | 2/2 | ✅ | — |

### 中国长城（000066）

| 问题 | 需要的指标 | 覆盖 | 可回答 | 缺口 |
|---|---|---|---|---|
| 你的收入结构和变化是什么？ | revenue | 1/1 | ✅ | — |
| 你最近真的赚钱吗？ | net_profit_attr、net_profit_deducted、operating_cash_flow、revenue | 4/4 | ✅ | — |
| 你的资产规模和资产负债情况如何？ | total_assets、debt_ratio | 2/2 | ✅ | — |
| 你的每股收益表现如何？ | eps | 1/1 | ✅ | — |
| 你的研发投入情况如何？ | rd_expense、rd_investment、rd_ratio | 0/3 | ⬜ | rd_expense、rd_investment、rd_ratio |
| 你的应收账款和存货情况如何？ | accounts_receivable、inventory | 2/2 | ✅ | — |

### 贝达药业（300558）

| 问题 | 需要的指标 | 覆盖 | 可回答 | 缺口 |
|---|---|---|---|---|
| 你的收入结构和变化是什么？ | revenue | 1/1 | ✅ | — |
| 你最近真的赚钱吗？ | net_profit_attr、net_profit_deducted、operating_cash_flow、revenue | 4/4 | ✅ | — |
| 你的资产规模和资产负债情况如何？ | total_assets、debt_ratio | 1/2 | ⬜ | debt_ratio |
| 你的每股收益表现如何？ | eps | 1/1 | ✅ | — |
| 你的研发投入情况如何？ | rd_expense、rd_investment、rd_ratio | 0/3 | ⬜ | rd_expense、rd_investment、rd_ratio |
| 你的应收账款和存货情况如何？ | accounts_receivable、inventory | 0/2 | ⬜ | accounts_receivable、inventory |

## 数据缺口清单

- 四家公司均已覆盖全部 5 个必查核心指标。

## 非结构化风险证据

- 思看科技（688583）：风险：下游需求×1、风险：产品销售结构×1、风险：技术优势×1
- 恒生电子（600570）：**无**结构化风险 Evidence（风险类问题只能靠 chunks 检索，不得虚构）
- 中国长城（000066）：**无**结构化风险 Evidence（风险类问题只能靠 chunks 检索，不得虚构）
- 贝达药业（300558）：**无**结构化风险 Evidence（风险类问题只能靠 chunks 检索，不得虚构）
