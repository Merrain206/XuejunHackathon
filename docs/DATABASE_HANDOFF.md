# 数据库任务交付说明（四公司 Evidence 基础）

> 日期：2026-10-02
> 任务书：`docs/TONIGHT_DATABASE_TASKS.md`
> 候选库：`cninfo.multicompany.next.db`（**未替换**稳定库 `cninfo.db`）

## 1. 结论速览

| 任务书章节 | 状态 | 结果 |
|---|---|---|
| §2 修复四公司全文索引 | ✅ 完成 | `chunks_fts` 801 → **2845**，覆盖率 100%，公司隔离 0 泄漏 |
| §3 Evidence 质量抽检 | ✅ 完成 | 抽检 **503/503** 条：verified 3、auto 497、excluded 3、**rejected 0** |
| §4 后端稳定查询 | ✅ 完成 | `cninfo_queries.py`（5 类查询，只读 + 参数化 + 自动排除隔离记录） |
| §5 公司目录与覆盖矩阵 | ✅ 完成 | `docs/companies.json`、`docs/coverage_matrix.md` |
| §6 候选库交付流程 | ✅ 完成 | 完整性检查全过；交付元数据见第 5 节 |
| §7 验收脚本 | ✅ 完成 | `acceptance_check.py` **退出码 0**（全部 P0 通过） |

**候选库结构完整、索引可用、引文全部可核验。稳定 `cninfo.db` 未被覆盖。**

## 1.1 本轮修复（用户确认后执行）

| 问题 | 处理 | 结果 |
|---|---|---|
| 500 条引文是表格行拼接，后端严格核验会判"找不到" | `repair_evidence_quotes.py` 重写为**标注页的连续原文** | 500/500 连续原文 |
| 3 条缺陷证据（`period` 为空、value 抽错） | 显式隔离 `evidence.excluded = 1` + 原因留档 | 不参与回答，验收转 0 |

修复只发生在候选库；稳定 `cninfo.db` 全程未改动。

## 2. 新增 / 更新的脚本

| 文件 | 作用 |
|---|---|
| `rebuild_fts_4companies.py` | 在候选库重建 `chunks_fts`（trigram），可重复执行 |
| `repair_evidence_quotes.py` | **把引文重写为标注页的连续原文，并隔离不可用证据** |
| `evidence_verify.py` | **引文核验 + 引文修复规则**（被其它脚本共用） |
| `evidence_audit.py` | Evidence 质量抽检，产出四公司质量报告 |
| `acceptance_check.py` | 只读验收，退出码 0/1/2 |
| `cninfo_queries.py` | 后端可复制的只读参数化查询层 |
| `build_company_catalog.py` | 生成公司目录与覆盖矩阵 |
| `test_evidence_verify.py` | 核验口径回归测试（49 个用例） |

## 3. 完整重建命令

```powershell
# 1) 重建候选库（从稳定库复制后只改候选库的索引）
python rebuild_fts_4companies.py --src cninfo.db --dst cninfo.multicompany.next.db

# 2) 修复引文形态并隔离不可用证据（可重复执行；先 --dry-run 看计划）
python repair_evidence_quotes.py --db cninfo.multicompany.next.db --dry-run
python repair_evidence_quotes.py --db cninfo.multicompany.next.db

# 3) 只读自检（不写库）
python rebuild_fts_4companies.py --verify-only --db cninfo.multicompany.next.db

# 4) 只读验收（退出码 0 = 全部 P0 通过）
python acceptance_check.py --db cninfo.multicompany.next.db

# 5) Evidence 全量核验 + 生成质量报告
python evidence_audit.py --db cninfo.multicompany.next.db --full

# 6) 生成公司目录与覆盖矩阵
python build_company_catalog.py --db cninfo.multicompany.next.db

# 7) 核验口径回归测试
python test_evidence_verify.py
```

重建与修复都可重复执行：连续两次重建后 `chunks_fts` 的 `(rowid, content)` 完全一致；
连续两次修复第二次为 0 改动（引文已经是连续原文）。

## 4. 关键发现

### 4.1 FTS 缺口（已修复）

稳定库 `chunks_fts` 的 801 行**全部属于恒生电子（600570）**。
思看科技、中国长城、贝达药业三家在 FTS 里一条都搜不到 —— 2044 条有效 chunks 缺失。
修复后各公司覆盖与任务书 §1 的 chunks 分布完全对齐：

| 公司 | chunks | 修复前 FTS | 修复后 FTS |
|---|---:|---:|---:|
| 思看科技 688583 | 1624 | 0 | 1624 |
| 恒生电子 600570 | 801 | 801 | 801 |
| 中国长城 000066 | 282 | 0 | 282 |
| 贝达药业 300558 | 138 | 0 | 138 |
| **合计** | **2845** | **801** | **2845** |

检索耗时最慢 5.5 ms、平均 1.55 ms（目标 < 300 ms）；四公司 × 三关键词 0 泄漏。
LIKE 兜底同样隔离，最慢 2.62 ms。

### 4.2 引文形态已修复：现在是页面上连续的原文

**问题**：`evidence.source_quote` 原本是表格行单元格拼接：

```
营业收入 | 185,101,612.27 | 176,848,509.44 | 4.67
```

而 PDF 文本层把同一行的单元格抽成了多行：

```
营业收入
185,101,612.27
176,848,509.44
4.67
```

后端 `db.page_from_markers()` 用的是「忽略空白后连续子串」这一严格口径，
因此原来 503 条里**只有 3 条**能通过，其余 500 条都会被判"找不到"。

**已修复**：`repair_evidence_quotes.py` 把每条引文重写成**标注页上真实存在的一段
连续原文** —— 取「覆盖全部内容片段的最小区间」。修复后：

| 项目 | 修复前 | 修复后 |
|---|---:|---:|
| 引文是标注页连续原文 | 3 | **500** |
| 引文需按 segment 核验 | 500 | 0 |
| 无法核验 | 0 | 0 |

修复的硬约束（全部由脚本机械保证，任何一条不满足就**不修**并转人工）：

1. 新引文必须是标注页原文的连续子串；
2. 新引文必须仍覆盖原引文的**全部内容片段**（标签 + 每个数字）；
3. 新引文不得引入原引文之外的内容（防止把邻行数字卷进来）；
4. **不改任何数字、页码、URL、value、unit、period** —— 只把"拼接写法"换成"原文写法"。

原引文完整保留在 `evidence.original_quote`，`quote_repaired = 1` 标记已重写，可回溯核对。

**对后端的影响**：`db.page_from_markers()` 现在可以直接核对库里的
`source_quote`，无需改用 segment 级核验。若后端仍想做 segment 级核验，
`evidence_verify.page_findings()` 也可直接用。

### 4.3 3 条缺陷证据已隔离（不再参与回答）

思看科技招股书（document 299）的 3 条规则抽取记录 `period` 为空：

| Evidence | metric | document | page | 原问题 |
|---:|---|---:|---:|---|
| 3103 | net_profit | 299 | 325 | `period` 为空，且 `value` 抽错（写成 `2024.0`，引文实为净资产收益率/每股收益） |
| 3104 | net_profit | 299 | 325 | 同上 |
| 3127 | rd_expense | 299 | 361 | `period` 为空 |

它们的 `period` **无法从页面原文推断**，因此没有臆造字段值，而是显式隔离：

```sql
UPDATE evidence SET excluded = 1, excluded_reason = '...' WHERE id IN (3103, 3104, 3127);
```

- 行仍在库中留档，`excluded_reason` 写明原因；
- `cninfo_queries.py` 的所有查询默认排除 `excluded = 1`（老库没有该列时自动退化）；
- 抽检与验收脚本都会把它们单独列为 `excluded`，**不计入通过率、也不当作通过**。

实测：这 3 条已不出现在任何查询结果里，也不参与报告期排序。

### 4.4 重复 canonical source URL = 133 组（有解释）

133 个 URL 各出现 2 次，形态完全一致：**1 条 `superseded=1` + 1 条 `superseded=0`，
同一家公司、同一份 PDF**。这是版本更替（supersede）机制的正常结果 ——
同一份公告先以原始文件名入库，再以规范的 `YYYYMMDD_` 前缀文件名重新入库。

- 跨公司共用同一 URL：**0 组**
- 同一 URL 下多条 active：**0 组**

因为 FTS 只索引 active 文档，`chunks_fts` 里不会有重复内容。故判为可解释、不阻塞。

### 4.5 数据缺口（不得补零）

| 缺口 | 说明 |
|---|---|
| 中国长城 000066、贝达药业 300558 | **无**研发类指标（rd_expense / rd_investment / rd_ratio） |
| 恒生电子 600570、贝达药业 300558 | **无** `debt_ratio` |
| 贝达药业 300558 | **无** `accounts_receivable` / `inventory` |
| 恒生电子、中国长城、贝达药业 | **无**结构化风险 Evidence（风险问题只能靠 chunks 检索） |
| 全库 | 1631 份有效文档没有 chunks，行级检索只能覆盖有 chunks 的 30 份文档 |

四家公司均覆盖 5 个必查核心指标（revenue / net_profit_attr / operating_cash_flow /
total_assets / eps）。完整矩阵见 `docs/coverage_matrix.md`。

## 5. 候选库交付元数据（§6.5）

| 项目 | 值 |
|---|---|
| 路径 | `D:\hackathon\cninfo.multicompany.next.db` |
| 大小 | 122,277,888 bytes（116.6 MiB） |
| SHA-256 | `40c1ccdcf0a405ed602ec2b78fd210da983e4c546afe73f819cbc5b924dee205` |
| 生成命令 | ① `python rebuild_fts_4companies.py --src cninfo.db --dst cninfo.multicompany.next.db`<br>② `python repair_evidence_quotes.py --db cninfo.multicompany.next.db` |
| `PRAGMA integrity_check` | ok |
| 孤儿 chunks / evidence | 0 / 0 |
| 跨公司关联 | 0 |
| FTS 覆盖率 | 2845/2845 = 100% |
| 引文连续原文 | 500/500（+ 3 条隔离） |

> `.db` 已被 Git 忽略，通过团队约定的文件传递方式交付。
> **只有用户明确确认后，才允许替换稳定 `cninfo.db`。**

## 6. 四公司计数（§8 PR 描述用）

| 公司 | 代码 | docs | 有效 docs | chunks | evidence | 指标种类 | 最新报告期 |
|---|---|---:|---:|---:|---:|---:|---|
| 思看科技 | 688583 | 312 | 175 | 1624 | 286 | 18 | 2026H1 |
| 恒生电子 | 600570 | 564 | 500 | 801 | 116 | 12 | 2026H1 |
| 中国长城 | 000066 | 481 | 461 | 282 | 52 | 11 | 2026H1 |
| 贝达药业 | 300558 | 548 | 533 | 138 | 49 | 7 | 2026H1 |
| **合计** | | **1905** | **1669** | **2845** | **503** | | |

`review_status` 分布：思看科技 283 auto + 3 verified；其余三家全部 auto。
**auto 不等于人工核验，任何地方都不得如此表述。**

`excluded` 分布：思看科技 3 条（即 4.3 的缺陷记录），其余为 0。

## 7. 验收结果

`acceptance_check.py` 在候选库上：**18 项通过、1 项警告、0 项失败 → 退出码 0**。

唯一的警告是 133 组重复 canonical source URL，形态全部是 supersede
（1 条 superseded + 1 条 active，同公司同 PDF，无跨公司、无多条 active），
已在脚本输出里逐组给出解释。

`evidence_audit.py --full` 也返回 **0**：
抽检 500 条参与回答的证据，`verified 3`、`auto 497`、`rejected 0`、
`excluded 3`；引文连续原文 **500/500**；
报告见 `docs/data_quality_report_4_companies.md`。

`test_evidence_verify.py`：**49 个用例全部通过**。

## 8. 未做的事（边界）

- 没有覆盖稳定 `cninfo.db`，没有改 `ruvector.db`。
- 没有改 `src/**` 或 `xray-backend/**`（只读查阅后端代码以对齐契约）。
- 没有把 auto 提升成 verified。
- 没有新增数据库或向量数据库产品。
- 没有为凑命中率伪造 page / quote / URL / value / unit。
- 引文修复只做「拼接写法 → 原文写法」的等价替换，未改动任何事实数值。
- 隔离的 3 条只加标记，**没有删除行、没有臆造 period/value**。

## 9. 表结构变更（候选库）

`evidence` 表新增 4 列，全部向后兼容（旧代码 `SELECT *` 多拿字段但不受影响）：

| 列 | 含义 |
|---|---|
| `excluded` | 1 = 已隔离，不参与回答 |
| `excluded_reason` | 隔离原因（可审计） |
| `quote_repaired` | 1 = 引文已按页原文重写 |
| `original_quote` | 重写前的原始引文（可回溯） |

若后端 Pydantic schema 对 `evidence` 做严格字段校验，需要允许这 4 个额外字段；
`cninfo_queries.py` 的查询**不返回**这些内部列（只用于过滤），因此走该模块不受影响。
