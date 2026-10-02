# 数据库同学下一步任务

> 状态：数据库 P0 已完成；本文为历史任务书，当前数据库事实以 `docs/data_quality_report_688583.md` 和 `docs/HANDOFF.md` 为准。

> 项目：Ask the Company 48 小时 Hackathon Demo  
> 当前 Demo 公司：思看科技（688583）  
> 最高原则：`No Evidence, No Claim`  
> 开始前请先完整阅读仓库根目录 `AGENTS.md` 和 `docs/HANDOFF.md`。

## 目标

交付一份后端可以只读使用、结构稳定、关键数据经过核验的 SQLite 数据库。当前更新版数据库为：

```text
D:\Codes\XueJunHackathon\cninfo.db
```

后端需要从数据库获得文档、Chunk、Evidence、原文页码和来源 URL；数据库层不负责生成最终 Answer、Claim、Signal 或 Chart。

## 当前数据库状态

- SQLite 完整性检查通过。
- 4 家公司，共 1905 份文档、4432 个 Chunk、541 条 Evidence。
- 思看科技有 312 份文档、1115 个 Chunk、179 条 Evidence。
- Chunk 和 Evidence 当前没有孤儿记录，也没有跨公司关联错误。
- 思看科技文档的 `source_url` 已填充。

以上只是当前状态，不代表数据质量已经通过验收。

## P0：必须优先完成

### 1. 修复金额截断和单位识别

当前规则抽取存在严重错误。例如原文为：

```text
营业收入 | 176,848,509.44 | 150,248,052.96 | 17.70
```

数据库却存成：

```text
value = 176
unit = %
content = 营业收入：176.0%
```

归母净利润和经营现金流也分别被错误存成 `540%`、`311%`。

需要：

- 修复 `pipeline.py` 中 `to_num()` 的数字解析，确保完整解析带千分位的金额。
- 不要根据整行表头中是否出现 `%` 来决定所有数据列的单位。
- 区分金额列、同比列、比例列和每股收益列。
- 金额应保留原始币种单位，例如 `元`、`万元`、`亿元`。
- 每股收益使用 `元/股`，比例使用 `%`。
- `content` 必须与 `value`、`unit` 和 `source_quote` 一致。

至少验证以下思看科技数据：

| 指标 | 正确值 |
|---|---:|
| 2025年上半年营业收入 | 176,848,509.44 元 |
| 归母净利润 | 54,007,712.64 元 |
| 扣非归母净利润 | 47,074,322.51 元 |
| 经营活动现金流净额 | 31,159,083.37 元 |
| 营业收入同比 | 17.70% |
| 归母净利润同比 | 2.06% |
| 扣非归母净利润同比 | -2.93% |
| 经营现金流同比 | -32.59% |

### 2. 修复半年报分类

当前“2025年半年度报告”被识别为：

```text
document_type = annual_report
report_period = 2025FY
```

正确结果应为：

```text
document_type = semiannual_report
report_period = 2025H1
```

原因是分类规则先匹配了“年度报告”。请将“半年度报告”规则放在“年度报告”之前，并重建受影响文档的元数据、Chunk 和 Evidence。

### 3. 修复 FTS 模式不一致

当前数据库实际 FTS 表为：

```text
tokenize='trigram'
```

但 `meta.fts_mode` 为：

```text
jieba
```

必须统一索引和查询模式。Hackathon 阶段建议优先选择可移植、无额外依赖的 trigram，并确保：

- `chunks_fts` 的 tokenizer 与 `meta.fts_mode` 一致。
- 重建时不会保留旧 tokenizer 的虚表。
- 插入索引和查询时使用同一套分词方式。
- 对三条稳定问题做实际检索测试，而不是只检查表是否存在。

### 4. 让招股书进入 Chunk 和检索范围

当前思看科技 `prospectus` 文档已经入库，但对应 Chunk 数量为 0，无法支持“收入结构”回答。

需要将以下文档类型纳入 Demo 检索范围：

```text
prospectus
annual_report
semiannual_report
```

并确认招股书中对应收入结构原文能够检索到；对外上交所注册稿的查看器页码为第 321、322、324 页：

- 便携式 3D 扫描仪收入占比从 78.19% 降至 57.87%。
- 跟踪式产品收入由 1,893.69 万元增至 7,222.66 万元。
- 跟踪式产品占比由 11.77% 升至 26.58%。

页码必须采用 PDF 查看器页码。

### 5. 补齐风险 Evidence

当前 Evidence 全部是 `financial`，没有可直接支持风险回答的结构化 Evidence。

请从 2025 年半年度报告 PDF 查看器第 43 页生成并核验至少三条 Evidence：

- 产品结构变化可能影响毛利率。
- 技术优势减弱可能影响售价和市场占有率。
- 下游重要行业需求萎缩可能导致收入下降。

要求：

- `source_quote` 必须是原文逐字摘录。
- `source_page = 43`。
- 明确这些是公司披露的风险提示，不代表风险已经发生。
- 如果使用 LLM 抽取，必须执行“原文包含引用”的机械校验，不通过则丢弃。

### 6. 清理重复文档

当前按 `company_code + source_url` 检查到 126 组重复来源，主要是同一 PDF 以不同文件名重复入库。

需要：

- 以 `company_code + source_url` 作为优先去重依据。
- 没有 URL 时再使用内容哈希或规范化文件名。
- 对重复文档保留一个 canonical 记录，其余标记 `superseded = 1`，不要直接无记录删除。
- 重建 Chunk 和 Evidence，避免重复检索结果。
- 增加防止后续重复入库的约束或检查。

## P1：P0 完成后处理

### 7. 补齐其他公司的来源链接

当前缺失情况：

- 000066：371 份文档缺少 URL。
- 300558：548 份文档全部缺少 URL。
- 600570：564 份文档全部缺少 URL。
- 688583：当前无缺失。

思看科技 Demo 不应被此项阻塞，但在宣称支持多公司前必须修复。

### 8. 增加 Schema 版本和约束

建议至少增加：

- `meta.schema_version`
- `chunks.document_id -> docs.id` 的逻辑或数据库外键约束
- `evidence.document_id -> docs.id` 的逻辑或数据库外键约束
- `UNIQUE(document_id, page_number, chunk_index)`
- 必要的 `company_code`、`document_type`、`metric` 联合索引

SQLite 外键如启用，运行连接必须执行：

```sql
PRAGMA foreign_keys = ON;
```

### 9. 提供可复现运行环境

补充：

- `requirements.txt`
- 数据库路径环境变量，例如 `DATABASE_PATH`
- 首次构建命令
- 增量更新命令
- 重建索引命令
- 数据校验命令
- 不使用 LLM 时的完整运行方式

不要继续依赖硬编码的 `D:\hackathon\cninfo.db`。

## 三条 Demo 的最终数据验收

数据库必须能够支持以下三条问题：

1. `你的收入结构发生了什么变化？`
   - 上交所注册稿第 321、322、324 页。
   - 至少包含两类产品的收入及占比变化。

2. `你最近真的赚钱吗？`
   - 2025 半年报第 8、9 页。
   - 包含收入、归母净利润、扣非净利润和经营现金流同比变化。

3. `目前最值得关注的风险是什么？`
   - 2025 半年报第 43 页。
   - 至少包含技术、产品毛利结构和下游需求三类风险提示。

每条 Evidence 必须具备：

- 唯一 ID
- `company_code`
- `document_id`
- 合法 category
- 报告期
- Evidence 内容
- PDF 查看器页码
- 原文摘录
- 原始文件 URL
- 抽取方式和核验状态

## 最终交付物

- 修复后的 `pipeline.py`。
- 重建后的 `cninfo.db`。
- Python 依赖和运行说明。
- 思看科技数据质量报告。
- 重复文档、OCR 文档和检索未命中清单。
- 三条 Demo 问题对应的 Evidence 查询结果或导出 JSON。

## 完成标准

- `PRAGMA integrity_check` 返回 `ok`。
- 无孤儿 Chunk 或 Evidence。
- 无跨公司关联错误。
- FTS tokenizer 与 `meta.fts_mode` 一致。
- 半年报类型和报告期正确。
- 三条 Demo 所需页码均可检索。
- 示例财务数字与 `source_quote` 完全一致。
- 思看科技使用的 Evidence 均有可打开的原始 URL。
- 数据库重建过程可以在新环境复现。

## 不要做

- 不要修改前端组件。
- 不要生成最终 Answer、Claim、Signal 或 Chart。
- 不要将 Pipeline 规划描述成已经上线的夜间更新系统。
- 不要为了命中率伪造 expected page 或 Evidence。
- 不要在原文证据不足时用模型常识补充公司事实。

