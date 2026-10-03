# 今晚任务书：数据库 Agent

> 历史任务书：PR #7 已实现首轮四公司数据库任务；2026-10-03 v2 已经产品负责人验收并晋升为稳定库。当前状态以 `docs/HANDOFF.md` 为准。

> 日期：2026-10-02  
> 基线提交：`057d18a`  
> 目标：为四家公司动态证据问答提供可检索、可核验、公司隔离的数据基础。  
> 最高原则：`No Evidence, No Claim`

## 0. 开始前

1. 拉取最新 `main`，完整阅读 `AGENTS.md`、`README.md`、`docs/HANDOFF.md`、`docs/data_quality_report_688583.md` 和本任务书。
2. 执行 `git status --short`，不要覆盖他人改动。
3. 只修改根目录 Pipeline/数据检查脚本和数据质量报告；不要修改 `src/**` 或 `xray-backend/**`。
4. 当前 `cninfo.db` 是稳定基线，今晚不得直接覆盖。所有重建先输出到 `cninfo.multicompany.next.db` 或等价候选文件。
5. `.db` 文件被 Git 忽略，不要强制提交。数据库交付通过团队约定的文件传递方式完成。

## 1. 当前真实数据快照

2026-10-02 只读检查结果：

| 公司 | 代码 | docs | 有效 docs | chunks | evidence | 指标种类 |
|---|---:|---:|---:|---:|---:|---:|
| 中国长城 | 000066 | 481 | 461 | 282 | 52 | 11 |
| 贝达药业 | 300558 | 548 | 533 | 138 | 49 | 7 |
| 恒生电子 | 600570 | 564 | 500 | 801 | 116 | 12 |
| 思看科技 | 688583 | 312 | 175 | 1624 | 286 | 18 |

总计：1905 docs、2845 chunks、503 Evidence。

重要异常：`chunks_fts` 当前只有 801 行，明显没有覆盖全部 2845 chunks；在修复前后端不能依赖它完成四公司检索。

另外三家公司 Evidence 当前全部为 `review_status=auto`；思看科技为 283 条 auto、3 条 verified。不要把 auto 描述成人工核验。

## 2. P0：修复四公司全文索引

- 确认 `chunks_fts` 与 `chunks` 的 rowid 关联设计。
- 使用 trigram 重建 FTS，覆盖全部有效且未 superseded 文档的 chunks。
- `meta.fts_mode` 必须与真实 tokenizer 一致。
- 重建必须可重复执行，不得重复插入。
- 建立公司隔离查询方式；FTS 命中后必须 JOIN chunks/docs 并再次限制 `company_code`。
- 如无法在 3 小时内可靠完成，提供稳定的参数化 `LIKE` 查询和性能数据，不要让后端等待 FTS。

验收：

- 索引行数与纳入范围内 chunks 数量一致，差异必须有可解释清单。
- 对四家公司分别搜索“营业收入”“归属于上市公司股东的净利润”“经营活动产生的现金流量净额”，不得串公司。
- 给出每条查询耗时，目标本机低于 300 ms。

## 3. P0：Evidence 质量抽检

每家公司至少抽查以下指标的最新两条 Evidence：

- revenue
- net_profit_attr
- operating_cash_flow
- total_assets
- eps

若该公司还具备以下指标，也一并抽查：

- debt_ratio
- rd_expense / rd_investment
- accounts_receivable
- inventory

每条检查：

1. `document_id` 属于同一 `company_code`。
2. document 未 superseded，`parse_status='ok'`。
3. `source_page` 为正整数且不超过 `page_count`。
4. `source_quote` 忽略空白后能在该页原文连续命中。
5. `value`、`unit`、`period` 与 quote 一致。
6. `source_url` 非空，指向真实 PDF，不含 `#page=`。
7. URL 使用 HTTPS 时必须真实可访问，不能只做字符串替换。

抽检结果输出到新的 `docs/data_quality_report_4_companies.md`，明确区分：

- `verified`：人工复核。
- `auto`：规则抽取且机械校验通过。
- `rejected`：不能支撑回答。

不要批量把 auto 更新成 verified。

## 4. P0：为后端提供稳定查询

在 Pipeline 或独立只读检查脚本中提供可复制 SQL/函数示例：

- 按 `company_code + metric` 取最新 Evidence。
- 按 `company_code + 多个 metric` 取最近若干报告期。
- 按 `document_id + source_page` 取完整页文本。
- 按 `company_code + 关键词` 检索 chunks。
- JOIN docs 后返回标题、URL、page_count、report_period 和 review_status。

所有查询必须参数化，不允许把用户输入直接拼进 SQL。

数据库层不生成 Answer、Claim、Signal 或 Chart，也不调用 LLM。

## 5. P1：公司目录和覆盖矩阵

输出四公司的机器可读目录，例如 JSON 或 Python 常量来源，字段至少包含：

- code
- name
- exchange
- board
- industry label
- available metrics
- latest report period

公司名称固定为：

- 688583：思看科技
- 600570：恒生电子
- 000066：中国长城
- 300558：贝达药业

同时生成覆盖矩阵，告诉前后端哪些问题有足够 Evidence。不要因为某指标缺失而伪造空值或零值。

## 6. 候选数据库交付流程

1. 从当前稳定 `cninfo.db` 复制生成候选库。
2. 只在候选库中重建索引或修复数据。
3. 执行：

```sql
PRAGMA integrity_check;
```

4. 检查：

- 孤儿 chunks = 0
- 孤儿 evidence = 0
- 跨公司 document 关联 = 0
- 重复 canonical source URL = 0 或有解释
- FTS 覆盖符合报告

5. 把候选库路径、文件大小、SHA-256、生成命令和检查结果交给负责人。
6. 只有用户明确确认后，才允许替换稳定 `cninfo.db`。

## 7. 验收脚本

新增或更新只读检查脚本，使以下命令可在新环境复现：

```powershell
python <检查脚本> --db D:\Codes\XueJunHackathon\cninfo.multicompany.next.db
```

脚本退出码：

- `0`：所有 P0 检查通过。
- 非 `0`：打印公司、document_id、page 和失败原因。

不得只输出“检查成功”而没有计数和失败明细。

## 8. PR 交付

Git PR 只提交：

- Pipeline 或索引修复代码。
- 只读数据检查脚本。
- 四公司数据质量报告。
- 必要的运行说明。

不要提交任何数据库、PDF、日志或包含密钥的文件。

PR 描述必须给出：

- 候选数据库获取方式。
- 完整重建命令。
- 四公司 docs/chunks/evidence/metrics 计数。
- FTS 覆盖率和检索耗时。
- Evidence 抽检通过率。
- 未解决的数据缺口。

## 9. 禁止事项与停止线

- 不直接覆盖当前稳定 `cninfo.db`。
- 不修改 `ruvector.db`。
- 不修改前端或 FastAPI。
- 不把 `auto` Evidence 宣称为人工验证。
- 不为了命中测试伪造 page、quote、URL、value 或 unit。
- 不引入新的数据库产品或向量数据库。
- 距离截止不足 8 小时时停止大规模重建；若候选库未通过完整性检查，继续使用当前稳定库，并把 FTS 缺口交给后端以 `LIKE` 绕过。

