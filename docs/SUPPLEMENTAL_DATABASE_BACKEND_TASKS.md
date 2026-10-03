# 数据库与后端补充收口任务书

> 日期：2026-10-02
> 基线提交：`b8b5923`（PR #7 与 PR #8 已合并）
> 性质：修复已实现链路中的验收缺口，不扩展产品范围
> 最高原则：`No Evidence, No Claim`

> 历史实施状态（2026-10-02）：本文件记录首轮 503/407 Evidence 的收口任务。后续 v2 已扩展为 2254/2158 Evidence，并于 2026-10-03 经产品负责人验收后晋升为稳定 `cninfo.db`；当前状态与验证结果以 `docs/HANDOFF.md` 为准。产品负责人确认：非思看公司风险问题可以进入动态链路，但只能总结 Evidence 直接支持的风险信号，不得冒充完整风险清单。

## 0. 已确认基线

以下能力不需要重做：

- 前端已经支持四家公司切换、45 秒超时和非思看公司隔离降级。
- 数据库已交付四公司 FTS、Evidence 核验脚本、候选库生成脚本和只读查询示例。
- 后端已交付四公司动态 Evidence-first 链路、稳定三问优先级、结构化响应和失败兜底。
- 思看科技三条稳定 Demo 与水果问题不得退化。

本轮只处理本次独立验收发现的缺口。数据库 Agent 只修改根目录数据脚本和数据库文档；后端 Agent 只修改 `xray-backend/**`、后端测试和后端文档。不要修改 `src/**`，不要引入 LangGraph、多 Agent、向量数据库、复杂 GraphRAG 或新产品功能。

## 1. 数据库 Agent：P0 引文修复必须真正防止内容污染

### 1.1 已复现问题

`repair_evidence_quotes.py` 当前会计算 `extra_segments`，但没有在它非空时拒绝修复，而是无条件把记录加入 `planned`。

独立复验结果：

- 计划修复 497 条、无法修复 0 条。
- 按“原引文数字集合”和“修复后数字集合”做规范化差集，至少 93 条修复引文带入了原引文没有的数字。
- 24 条修复后引文超过 500 字符，2 条超过 1000 字符。
- Evidence `3091` 从 81 字符扩展到 1467 字符，卷入了利润表的大段无关项目。

这类引文虽然是同一页的连续原文，可以通过当前验收，但不满足“最小、相关、不会把邻行数字卷进来”，也可能诱导模型使用无关数字。

### 1.2 必须修改

1. 修正 `_extra_content()`：原引文和候选 span 两边的数字都必须先使用同一套 `canonical_number()` 规范化，再做集合差。
2. `extra_segments` 非空时不得进入 `planned`；应尝试更短窗口，仍无法满足时进入 `unfixable`，不得静默写库。
3. 增加跨度保护：修复后的 `squeeze(source_quote)` 建议不超过 200 字符，与后端 `DYNAMIC_MAX_QUOTE_SPAN` 对齐；超过时不得用整页或大表片段冒充精确摘录。
4. 不允许简单按字符截断，因为截断可能切断数字、单位或标签。必须从页面原文中选择完整、连续且只覆盖目标内容的区间。
5. `verify()` 和 `acceptance_check.py` 必须检查：
   - 新引文没有原引文之外的数字；
   - 新引文长度/跨度在约定上限内；
   - `original_quote`、`quote_repaired` 可回溯；
   - 不满足条件的记录不得计入通过率。
6. 增加真实缺陷回归测试，至少覆盖 Evidence `3091`，并断言不会把营业收入、营业成本、税金等相邻行数字卷入净利润 Evidence。

如果某条表格 Evidence 无法在 200 字符内生成连续精确摘录，可以保留原记录用于审计，但必须标成 `excluded` 或新的明确不可用状态，不得支撑动态回答。

### 1.3 P1：只读源库的可复现重建

当前 Windows 工作区中的 `cninfo.db` 带只读属性。`shutil.copy2(src, dst)` 会把只读属性复制到候选库，随后 SQLite 在 `DROP TABLE chunks_fts` 时失败：

```text
sqlite3.OperationalError: attempt to write a readonly database
```

要求：

- 复制后只解除候选文件的只读属性，不修改源库属性或内容。
- Windows 与普通可写文件两种情况都要测试。
- 重建失败时清晰提示原因，不留下被误认为可用的半成品候选库。

### 1.4 数据库交付与验收

重新生成候选库和以下文档：

- `docs/data_quality_report_4_companies.md`
- `docs/DATABASE_HANDOFF.md`
- `docs/PR_DATABASE_4COMPANIES.md`

必须提供：

- 新候选库路径、大小和 SHA-256。
- FTS 覆盖与公司隔离结果。
- 精确摘录通过数、因额外数字/跨度过长而隔离的数量。
- 最长引文长度及 P90，不能只报告“连续原文 100%”。
- 以下命令的真实退出码：

```powershell
python test_evidence_verify.py
python rebuild_fts_4companies.py --src cninfo.db --dst cninfo.multicompany.next.db
python repair_evidence_quotes.py --db cninfo.multicompany.next.db
python acceptance_check.py --db cninfo.multicompany.next.db
```

候选库仍不得提交 Git，也不得自动替换稳定 `cninfo.db`。是否换库由产品负责人决定。

## 2. 后端 Agent：P0 严格执行数据库隔离状态

### 2.1 已复现问题

数据库候选库新增了：

```text
evidence.excluded
evidence.excluded_reason
```

但 `xray-backend/dynamic_evidence.py::_fetch_rows()` 只过滤公司、有效文档和 superseded 状态，没有过滤 `excluded=1`。独立复验确认 Evidence `3103`、`3104`、`3127` 虽已被数据库隔离，仍能通过后端自己的页面引文核验。

### 2.2 必须修改

- 通过 `PRAGMA table_info(evidence)` 探测可选列。
- 新库存在 `excluded` 时追加参数安全的常量过滤：`COALESCE(e.excluded, 0) = 0`。
- 老库没有该列时保持兼容，不得 SQL 报错。
- 增加真实候选库或合成库测试，明确断言 `excluded=1` 永远不会进入候选、Prompt 或响应。
- 后端不得只依赖“排序后可能选不到”来规避隔离记录。

## 3. 后端 Agent：P0 数字必须由每条 Claim 自己引用的 Evidence 支撑

### 3.1 已复现问题

`validate_dynamic_payload()` 当前存在两层缺口：

1. `strict_numbers` 默认是 `False`，正式动态请求没有传 `True`，未核到的数字只写日志仍会返回。
2. 即使传入 `strict_numbers=True`，数字语料也是所有候选 Evidence 的合并文本，而不是该 Claim 的 `evidence_ids`。因此可以出现：

```text
EV-001 原文只有 100.00
EV-002 原文包含 999.99
Claim 写 999.99，但只引用 EV-001
```

该错误响应目前仍能通过严格模式。

### 3.2 必须修改

1. 动态正式链路必须启用硬数字校验，不得依赖调用者记得传布尔参数；建议删除宽松默认值，或提供只有严格模式的生产入口。
2. 每条 Claim 的关键金额和比例只能在它自己的 `evidence_ids` 所指 Evidence 中核对。
3. Signal 的 `title` 和 `description` 若含金额或比例，也必须按其自己的引用做相同校验。
4. Answer 至少必须在最终返回的 Evidence 集合中核到全部关键数字。更稳妥的方案是从校验通过的 Claims 确定性拼接 Answer，避免保留已经被丢弃 Claim 的内容。
5. 某条 Claim 数字不受其引用支持时，丢弃该 Claim；全部 Claim 被丢弃后返回固定证据不足结构。
6. 禁止用“数字在任意候选里出现过”作为引用正确的证明。

新增回归测试至少覆盖：

- 数字只存在于未引用 Evidence 时必须拒绝。
- 一个 Claim 引用多条 Evidence 时可以使用这些 Evidence 的数字并集。
- Signal 编造数字必须拒绝。
- Answer 保留了已被删除 Claim 的数字时必须重建或拒绝。
- 全部失败时四个数组为空并返回固定兜底文案。

## 4. 后端 Agent：P0 收紧模型格式容错

当前 `_coerce_answer_into_claims()` 在模型没有返回 Claims 时，会尝试仅凭 Answer 中的数字反向绑定某条 Evidence。相同金额、比例或报告期可能在多条证据中重复，取排序最靠前的一条不能证明模型真的引用了它。

本轮采用最小安全规则：

- 可以继续接受代码围栏、JSON 前后说明文字和字段别名等确定性格式修复。
- Answer 句子包含明确且有效的 `EV-xxx` 时，可以机械转换为 Claim，但仍必须按该 ID 做逐条数字校验。
- Answer 没有明确 Evidence ID 时，不得仅凭相同数字自动绑定出处；直接返回证据不足。
- 模型返回 `claims=[]` 且没有可验证的显式引用时，必须拒答，不为提高成功率降低证据标准。

不要增加第二次模型调用、复杂重试或新的 Agent 编排。

## 5. 后端 Agent：P1 运行与交付脚本

### 5.1 超时对齐

当前 `REQUEST_TIMEOUT_MS=120000`，而前端超时是 45 秒。120 秒不是“低于 45 秒”。动态模型调用应在前端放弃之前结束并释放资源：

- 将实际模型请求硬上限调整为不超过 40 秒，给网络返回和前端处理预留余量。
- 超时后返回固定证据不足结构。
- 文档、`.env.example` 与真实配置保持一致。

### 5.2 Windows 契约检查脚本

`scripts/check_frontend_contract.py` 的 25 项检查都通过后，最后打印 `✅` 会在当前 GBK 控制台触发 `UnicodeEncodeError`，导致进程退出码为 1。

要求：

- 显式配置 UTF-8 输出，或改成不依赖控制台字符集的 ASCII 文案。
- Windows 下真实执行退出码必须为 0。

## 6. 联合验收

两个 Agent 修复完成并合并后，使用同一份新候选库执行：

```powershell
# 数据库
python test_evidence_verify.py
python acceptance_check.py --db cninfo.multicompany.next.db

# 后端
cd xray-backend
$env:DATABASE_PATH = "D:\Codes\XueJunHackathon\cninfo.multicompany.next.db"
$env:DB_PATH_STRICT = "true"
python -m pytest tests -q
python -m pytest tests_real -q
python scripts/check_frontend_contract.py
```

还必须进行一次 Fake LLM 定向测试：

1. Claim 引用错误 Evidence，但数字存在于另一候选中 → 必须拒绝。
2. 数据库 Evidence 为 `excluded=1` → 不得进入候选。
3. 模型只写数字、不写 `EV-xxx` 且 `claims=[]` → 必须拒绝。
4. 合法 Claim 引用正确 Evidence → 正常返回，`charts=[]`。
5. 水果问题 → 不检索、不调用模型、四个数组为空。
6. 思看科技三条稳定问题 → 内容、图表、页码和速度不退化。

最终交付摘要必须写清楚：测试数量、退出码、候选库哈希、被隔离 Evidence 数量、动态请求超时和仍未覆盖的问题。GitHub 没有 CI 时，不得只写“全部通过”，必须附可复现命令。

## 7. 停止线

- 非思看公司的风险问答仅允许总结财务 Evidence 直接支持的风险信号；不得扩展成无证据的完整风险分析。
- 不新增动态图表。
- 不增加公司。
- 不修改前端。
- 不自动替换稳定数据库。
- 修复后若真实 DeepSeek 仍不稳定，保留 `DYNAMIC_QA_ENABLED=false` 保命开关；稳定 Demo 优先。
