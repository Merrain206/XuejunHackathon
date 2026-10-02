# PR：四公司数据库基础（FTS 修复 + Evidence 引文修复与抽检 + 后端查询层）

> 任务书：`docs/TONIGHT_DATABASE_TASKS.md`
> 分支：`feat/database-4companies-evidence`
> 基线：`03b0f4e`

## 0. 一句话结论

稳定库 `chunks_fts` 只有 **801 行且全部属于恒生电子**（另外三家一条都搜不到）；
同时 503 条 Evidence 里 **500 条的 `source_quote` 是表格行拼接**，后端严格核验会判
"找不到"。本 PR 在**候选库**里把索引补全到 **2845/2845（100%）**，把引文全部重写为
**标注页的连续原文**，隔离 3 条无法支撑回答的缺陷记录，并交付后端只读查询层与验收脚本。

**稳定 `cninfo.db` 全程未被改动，也没有提交任何数据库文件。**

---

## 1. 候选数据库获取方式（§6.5）

| 项目 | 值 |
|---|---|
| 路径 | `cninfo.multicompany.next.db`（仓库根目录） |
| 大小 | 122,277,888 bytes（116.6 MiB） |
| SHA-256 | `13f95fad520c56539e9b32227c6d48697691ebb56b9c74c20cc3c60fc1567670` |
| 生成方式 | 见第 2 节两条命令，从稳定库复制后只改候选库 |

**获取步骤（任何同学可复现）：**

1. 取稳定 `cninfo.db` 放到仓库根目录（`.db` 已被 `.gitignore` 忽略，不入版本库）；
2. 依次执行第 2 节的两条命令；
3. 用 `python acceptance_check.py --db cninfo.multicompany.next.db` 校验，
   退出码应为 `0`，并用第 1 节的 SHA-256 与本地文件比对。

> ⚠️ 替换稳定 `cninfo.db` 需用户明确确认；本 PR 不做替换。

---

## 2. 完整重建命令

```powershell
# ① 重建四公司全文索引（从稳定库复制出候选库，只在候选库重建）
python rebuild_fts_4companies.py --src cninfo.db --dst cninfo.multicompany.next.db

# ② 修复引文形态并隔离不可用证据（先 --dry-run 可只看计划不写库）
python repair_evidence_quotes.py --db cninfo.multicompany.next.db --dry-run
python repair_evidence_quotes.py --db cninfo.multicompany.next.db

# ③ 只读自检与验收（都不写库）
python rebuild_fts_4companies.py --verify-only --db cninfo.multicompany.next.db
python acceptance_check.py --db cninfo.multicompany.next.db

# ④ 生成质量报告、公司目录与覆盖矩阵
python evidence_audit.py --db cninfo.multicompany.next.db --full
python build_company_catalog.py --db cninfo.multicompany.next.db

# ⑤ 核验口径回归测试
python test_evidence_verify.py
```

两条写库命令都**可重复执行**：连续两次重建后 `chunks_fts` 的 `(rowid, content)`
完全一致；连续两次修复第二次为 0 改动（引文已是连续原文）。

---

## 3. 四公司 docs / chunks / evidence / metrics 计数

| 公司 | 代码 | docs | 有效 docs | chunks | evidence | 指标种类 | 已隔离 |
|---|---|---:|---:|---:|---:|---:|---:|
| 思看科技 | 688583 | 312 | 175 | 1624 | 286 | 18 | 3 |
| 恒生电子 | 600570 | 564 | 500 | 801 | 116 | 12 | 0 |
| 中国长城 | 000066 | 481 | 461 | 282 | 52 | 11 | 0 |
| 贝达药业 | 300558 | 548 | 533 | 138 | 49 | 7 | 0 |
| **合计** | | **1905** | **1669** | **2845** | **503** | | **3** |

`review_status`：`auto` 500 条、`verified` 3 条（思看科技人工核验的 3 条风险证据）。
**`auto` 只代表机械校验通过，不等于人工核验，任何地方都不得如此表述。**

数据来源与产物（均在仓库内，非数据库文件）：

- `docs/data_quality_report_4_companies.md` —— 四公司抽检报告
- `docs/coverage_matrix.md` —— 指标覆盖矩阵与缺口清单
- `docs/companies.json` —— 四公司机器可读目录
- `docs/DATABASE_HANDOFF.md` —— 交付说明与重建命令

---

## 4. FTS 覆盖率与检索耗时（§2 验收）

**修复前后对比：**

| 公司 | 有效 chunks | 修复前 FTS | 修复后 FTS |
|---|---:|---:|---:|
| 思看科技 688583 | 1624 | **0** | 1624 |
| 恒生电子 600570 | 801 | 801 | 801 |
| 中国长城 000066 | 282 | **0** | 282 |
| 贝达药业 300558 | 138 | **0** | 138 |
| **合计** | **2845** | **801** | **2845** |

- 覆盖率：**2845/2845 = 100%**，无遗漏、无越界索引、rowid 与 `chunks.id` 一一对应
- `meta.fts_mode = 'trigram'` 与真实 tokenizer 一致（脚本校验，不一致直接判失败）
- 检索耗时：最慢 **3.08 ms**、平均 **1.15 ms**（目标 < 300 ms）
- 公司隔离：四公司 × 三关键词（营业收入 / 归属于上市公司股东的净利润 /
  经营活动产生的现金流量净额）**0 泄漏**
- LIKE 兜底（FTS 不可用时）：最慢 **2.85 ms**，同样 0 泄漏

修复前 FTS 只覆盖恒生电子，也就是说思看科技、中国长城、贝达药业三家
**在全文检索里完全不可见** —— 这是本次最关键的修复。

---

## 5. Evidence 抽检通过率（§3 验收）

全量核验 **503 条**：

| 判定 | 条数 | 说明 |
|---|---:|---|
| `verified` | 3 | 人工复核（思看科技风险证据） |
| `auto` | 404 | 机械校验通过 |
| `rejected` | **0** | 无 |
| `excluded` | 96 | 显式隔离，不参与回答 |
| **通过率（不含隔离）** | **407/407 = 100%** | |

引文可核验性：**连续原文 407/407**（修复前只有 3 条）；可用引文长度 P90/最大值为 **65/118 字符**。另有 93 条因连续区间会卷入原引文之外的数字而隔离，加上原有 3 条缺陷记录，共隔离 96 条。
`source_url` 可达性：抽检涉及 30 个去重 URL，**实测全部 HTTP 200**（真实发请求，非字符串替换）。

### 5.1 引文修复（本 PR 的核心修正之一）

`source_quote` 原本是表格行单元格拼接，而 PDF 文本层把同一行抽成多行：

```
原（拼接）: 营业收入 | 332,583,883.61 | 271,707,663.51 | 22.41 | 206,024,686.41
新（原文）: 营业收入
            332,583,883.61
            271,707,663.51
            22.41
            206,024,686.41
```

新引文就是**标注页原文本身**，因此后端 `db.page_from_markers()` 的
「忽略空白后连续子串」严格口径**可直接通过，后端无需改成 segment 级核验**。

三条硬约束由脚本机械保证，任一不满足即**不修**并转人工：

1. 必须是标注页原文的连续子串；
2. 必须仍覆盖原引文**全部内容片段**（标签 + 每个数字）；
3. 不得引入原引文之外的内容（有测试专门断言不会把邻行净利润卷进来）。

**未改动任何数字、页码、URL、value、unit、period**；原引文保留在
`evidence.original_quote`，`quote_repaired = 1` 标记，可逐条回溯核对。

### 5.2 隔离的 3 条缺陷记录

思看科技招股书（document 299）的规则抽取记录 `period` 为空：

| Evidence | metric | document | page | 问题 |
|---:|---|---:|---:|---|
| 3103 | net_profit | 299 | 325 | `period` 为空，且 `value` 抽错（写成 `2024.0`，引文实为净资产收益率/每股收益） |
| 3104 | net_profit | 299 | 325 | 同上 |
| 3127 | rd_expense | 299 | 361 | `period` 为空 |

它们的 `period` **无法从页面原文推断**，因此不臆造字段值，而是写
`evidence.excluded = 1` + `excluded_reason` 留档并排除在回答之外。
`cninfo_queries.py` 的所有查询默认排除 `excluded = 1`，因此即使后端忘记加条件，
也不会把不可用证据喂给模型。

**隔离记录单独计为 `excluded`，不计入通过率、也不当作通过** —— 不是把失败藏起来。

### 5.3 修掉一个假通过

上一版 `evidence_audit.py` 把「引文在该页找不到」降级成 `auto` 并输出
**100% 通过率**；实测那 61 条里 58 条引文根本没在标注页命中。
本 PR 重写核验口径并写成 63 个回归测试，锁住以下真实踩过的坑：

- 数值必须按**完整数字**比对：压空白会让相邻单元格粘成一个数字
  （`332,583,883.61` + `271,707,663.51` → `...883.61271,707...`），后一个数字永远匹配不上
- 区间短横不是负号：`33,000-35,000` 曾被读成 `-35000`
- 千分位/百分号/小数位等价（`22.41` = `22.41%`，`0.60` = `0.6`）
- 行标签被表格拆行时按子序列兜住，但限定 ≥ 4 字，避免单字假命中
- 机械校验**永远不把 `auto` 提升为 `verified`**

---

## 6. 未解决的数据缺口（不补零、不补空值）

| 缺口 | 说明 |
|---|---|
| 中国长城 000066、贝达药业 300558 | **无**研发类指标（`rd_expense` / `rd_investment` / `rd_ratio`） |
| 恒生电子 600570、贝达药业 300558 | **无** `debt_ratio` |
| 贝达药业 300558 | **无** `accounts_receivable` / `inventory` |
| 恒生电子 / 中国长城 / 贝达药业 | **无**结构化风险 Evidence（风险问题只能靠 chunks 检索，不得虚构） |
| 全库 | **1631 份有效文档没有 chunks** —— 行级检索只能覆盖有 chunks 的 30 份文档 |
| 全库 | `evidence.excluded = 1` 的 96 条已隔离，未从根上修正上游抽取规则 |

四家公司均覆盖 5 个必查核心指标（revenue / net_profit_attr / operating_cash_flow /
total_assets / eps）。完整矩阵见 `docs/coverage_matrix.md`。

### 6.1 其它已解释的观察

- **重复 canonical source URL 133 组**：形态全部为
  「1 条 `superseded=1` + 1 条 `superseded=0`，同一公司、同一 PDF」，
  是版本更替（supersede）机制的正常结果。跨公司共用 URL = **0 组**，
  同一 URL 下多条 active = **0 组**，故 FTS 不会索引到重复内容。
- **接口层没有破坏性变更**：`evidence` 表新增 4 列
  （`excluded` / `excluded_reason` / `quote_repaired` / `original_quote`），
  旧代码 `SELECT *` 会多拿到字段但不受影响；`cninfo_queries.py` 的查询**不返回**
  这些内部列（只用于过滤），因此走该模块的后端不受影响。
  只有当后端 Pydantic schema 对 `evidence` 做**严格**字段校验时才需要放行这 4 列。

---

## 7. 验收结果

```
test_evidence_verify.py                      EXIT=0   63 个用例全部通过
rebuild_fts_4companies.py --verify-only      EXIT=0   自检全部通过
repair_evidence_quotes.py                    EXIT=0   重复执行为 0 改动（幂等）
evidence_audit.py --full --no-network        EXIT=0   rejected 0、连续原文 407/407
build_company_catalog.py                     EXIT=0
acceptance_check.py                          EXIT=0   18 项通过 / 0 项失败
```

`acceptance_check.py` 覆盖：结构完整性（`integrity_check` = ok、孤儿 chunks/evidence = 0、
跨公司关联 = 0、`source_page` 未越界、无 `#page=` fragment）、全文索引
（tokenizer 一致、行数 = 纳入范围、rowid 一一对应、未索引不该索引的文档）、
公司隔离与检索耗时、Evidence 全量机械核验、四公司计数、候选库 SHA-256。
失败时**逐条打印公司 / document_id / page / 原因**，不只输出「检查成功」。

稳定性：稳定库 `cninfo.db` 的 mtime 与 SHA-256 在本轮全程未变
（`7408dcc2be70c3afbbd0bd4437beea6f2f5bf63803f6a69a6f8ce932055cad40`）。

---

## 8. 本 PR 提交内容（§8 白名单）

| 类别 | 文件 |
|---|---|
| Pipeline / 索引修复代码 | `rebuild_fts_4companies.py`、`repair_evidence_quotes.py` |
| 只读数据检查脚本 | `evidence_verify.py`、`evidence_audit.py`、`acceptance_check.py`、`cninfo_queries.py`、`build_company_catalog.py` |
| 回归测试 | `test_evidence_verify.py` |
| 四公司数据质量报告 | `docs/data_quality_report_4_companies.md` |
| 覆盖矩阵与目录 | `docs/coverage_matrix.md`、`docs/companies.json` |
| 运行说明 | `docs/DATABASE_HANDOFF.md` |

**未提交**：任何 `.db` / `.db.bak` / PDF / 日志 / 含密钥文件（已用
`git diff --name-only` 核对，并确认 `.gitignore` 覆盖 `*.db`、`*.db.bak*`、`*.log`、`*.pem`）。

**未改动**：`src/**`、`xray-backend/**`、`ruvector.db`、稳定 `cninfo.db`。
`ruvector.db` 本 PR 全程未读写。

---

## 9. 给后端的对接提示

1. **`cninfo_queries.py` 可直接照抄**：五类查询均只读、参数化、强制 `company_code`、
   默认过滤 `parse_status='ok'` 且 `superseded=0`，并自动排除 `excluded=1`。
   老库（缺 `excluded` 列）会自动退化，不会报错。
2. **`source_quote` 现在可以直接自证**：`db.page_from_markers()` 对库里的
   `source_quote` 全部能核到，无需改为 segment 级核验。
3. **`excluded = 1` 的记录不得用于回答**：目前 96 条，包括 3 条字段缺陷记录和 93 条无法安全生成精确短摘录的记录。
4. **`auto` 不等于人工核验**：只有 3 条 `review_status='verified'`。
5. 若要把库内 Evidence 喂给模型，建议从 `evidence_verify.py` 复用核验规则，
   避免自己实现时把「找不到」误判为通过。
