# X-Ray 企业穿透分析 · 后端

面向答辩/演示的**招股书核验问答系统**后端。

**数据源**：`cninfo.db` 的 `docs` / `chunks` / `evidence` 表 —— 从 PDF 提取的**公告原文**。
**三条稳定 Demo 问题**：由 `demo_handlers.py` 用**已核验的证据**确定性作答
（不依赖大模型、不依赖缓存，毫秒级返回）。
**其它问题**：返回固定兜底文案，不让模型补充事实。
**判断方式**：风险分析由 DeepSeek 读原文给出结论，**每条结论必须附原文引用**。
**调度**：应用内**不含**定时任务 —— 生产环境由系统 cron 驱动，demo 环境手动执行。

---

## ① Demo 三条命令

```bash
# 1) 起服务（三条稳定问题无需大模型，也无需先跑批处理）
python main.py

# 2) 提问
curl -X POST http://localhost:8000/companies/688583/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"你最近真的赚钱吗？"}'

# 3)（可选）跑一遍全量风险分析，产出缓存与报告
python scripts/run_night_batch.py --demo
```

安装依赖（首次）：

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# 在 .env 里填 DEEPSEEK_API_KEY（可选）
```

> **不配 API key 也能完整演示三条稳定问题** —— 这正是做确定性处理器的理由。
> 大模型只影响「非稳定问题 → 缓存风险结论」这条链路。

四条演示问题的实测响应已固化在 `samples/`（见 ⑥ 响应样例）。

---

## ② 接口

**路径保持不变**，没有任何前缀：

| 接口 | 说明 |
| --- | --- |
| `POST /companies/{stock_code}/ask` | 主接口：三条稳定问题确定性作答；其余返回固定兜底 |
| `GET /companies/{stock_code}/profile` | 公司画像：公告数量、日期区间、最近标题、风险摘要 |
| `GET /companies/{stock_code}/signals` | 风险信号（来自缓存的分析结论） |
| `POST /admin/refresh?stock_code=xxx` | 手动触发分析（`X-Admin-Token` 头或 `?token=`） |
| `GET /health` | 健康检查 + 数据源状态 + 缓存统计 |

| `GET /search?keyword=xxx` | 全文检索公告（演示时找证据） |

### 示例

```bash
# 主接口
curl -s -X POST "http://127.0.0.1:8000/companies/688583/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"这家公司有什么风险？"}'

# 公司画像 / 信号 / 健康检查 / 检索
curl -s "http://127.0.0.1:8000/companies/688583/profile"
curl -s "http://127.0.0.1:8000/companies/688583/signals"
curl -s "http://127.0.0.1:8000/health"
curl -s "http://127.0.0.1:8000/search?keyword=营业收入"

# 手动分析一家（或全部）
curl -s -X POST "http://127.0.0.1:8000/admin/refresh?stock_code=688583" \
  -H "X-Admin-Token: xray-demo-token"
```

代码带交易所后缀也能识别（`688583.SH` → `688583`）。

### 响应结构（顶层恰好 6 个字段，顺序固定）

**契约以 `xuejun-hackathon/src/lib/api.ts` 的运行时校验器为准** ——
它不通过就会**静默降级为 mock**（界面显示 `DEMO FALLBACK`），所以每个取值都是硬约束。

```jsonc
{
  "answer":  "688583：净利润为正但经营现金流为负……最新公告（2025-07-01）：董事会议事规则（2025年7月修订）.PDF。",
  "claims":  [ { "id": "CL-001", "text": "…", "evidence_ids": ["EV-001"] } ],
  "signals": [ { "id": "SIG-S1", "type": "divergence", "title": "利润与现金流背离",
                 "severity": "attention", "description": "…", "evidence_ids": ["EV-001"] } ],
  "charts":  [ { "id": "CHART-RISK-BY-TYPE", "type": "line", "title": "风险发现分布",
                 "subtitle": "…", "unit": "条", "periods": ["S1 利润/现金流", "S4 司法合规"],
                 "series": [ { "name": "发现条数", "values": [1.0, 1.0] } ],
                 "evidence_ids": ["EV-001", "EV-002"] } ],
  "evidence": [ { "id": "EV-001", "category": "financial", "period": "2025-07-01",
                  "content": "…", "document_id": "1",
                  "document_title": "2024年年度报告.PDF", "source_page": 283,
                  "source_quote": "…逐字原文…", "source_url": "https://…",
                  "risk_dimension": "现金真实性" } ],
  "suggested_questions": [ "…" ]
}
```

**逐项硬约束（前端校验器逐条检查）**

| 字段 | 约束 | 不满足的后果 |
| --- | --- | --- |
| `signals[].type` | `divergence` / `trend` / `attention` | 整包判非法 → 降级 mock |
| `signals[].severity` | `attention` / `positive` | 同上 |
| `charts[].type` | 必须是 `"line"`（前端只实现折线） | 同上 |
| `charts[].series[].values` | 长度**必须等于** `periods` 长度，元素为有限数值 | 同上 |
| `evidence[].category` | `financial` / `business` / `company` | 同上 |
| `evidence[].source_page` | **正整数**（不接受 `null`） | 同上 |
| `evidence[].source_url` | 合法 `http(s)` URL（不接受 `null`） | 同上 |
| `evidence[].source_quote` | 非空（`No Evidence, No Claim`） | 同上 |
| `claims[]` / `signals[]` / `charts[]` 的 `evidence_ids` | 非空，且都能在 `evidence[]` 里解析 | 同上 |

内部规则编号 `S1`–`S4` 仍然用于 LLM prompt 与统计，对外由 `schemas.SIGNAL_TYPE_MAP`
/ `SEVERITY_MAP` 翻译成前端取值：

| 内部 | 前端 type | 前端 severity |
| --- | --- | --- |
| S1 利润与现金流背离 | `divergence` | high/medium → `attention` |
| S2 营收与应收背离 | `attention` | high/medium → `attention` |
| S3 人员与规模背离 | `attention` | low → `positive` |
| S4 司法合规风险 | `attention` | |

**验收入口**：

```bash
python scripts/check_frontend_contract.py
```

它把 `api.ts` 的 `isAskApiResponse()` 逐条翻译成 Python，用**真实库 + 桩 LLM**
跑完整链路并逐字段判定。当前结果：整包通过。

### `source_url` 与 `source_page` 怎么来的

* **`source_url`**：优先取 `docs` 表的 URL 列（`source_url` / `url` / `doc_url` 任一，
  存在即自动采用）；拿不到就**丢弃该条证据** —— 宁可少给一条，也不编一个点开就 404 的链接。
  确定性处理器（三条 Demo）用的都是库里的**真实公告直链**。
* **`source_page`**：必须是**被引用片段真正所在的页码**。

> ⚠️ 这里曾经是错的：旧实现把该公告的 `page_count` 当引用页码，
> 于是 520 页的招股书里每条引用都声称出自「第 520 页」——
> 这正是 BACKEND_NEXT_STEPS.md 明令禁止的**伪造页码**。
>
> 现在分两条路：
> 1. **确定性处理器**：每条引用的页码在 `demo_handlers._FACTS` 里显式声明，
>    并由 `verify_facts()` 回到 `chunks` 表逐条核验（页码对不对、原文在不在那一页）；
> 2. **LLM 分析链路**：prompt 按「`[第 N 页]`」给出分页正文（来自 `chunks` 表），
>    要求模型回填 `source_page`；随后校验该页码落在公告页数范围内，
>    **拿不到可信页码就丢弃该条证据**（`ask.py` 的 `_build_evidence`）。

### 响应校验器（No Evidence, No Claim 的机器强制）

```bash
python scripts/check_frontend_contract.py
```

接口返回前统一调用 `response_validator.validate_response()`，逐条强制：

1. claim / signal / chart 都必须至少引用一条 evidence；
2. 所有 `evidence_ids` 必须存在于本响应；
3. evidence id 不得重复；不得有孤立证据；
4. `source_page` 必须是正整数、`source_url` 必须是合法 http(s)；
5. chart 的 `series[].values` 长度必须等于 `periods`、且都是有限数值；
6. 顶层恰好 6 个字段；
7. answer 里的关键比率/金额要能在 `source_quote` 里核到（软警告）；
8. 兜底回答必须四个数组全空。

校验不过的响应**不会发给前端**（前端判定非法会静默降级成 mock，
反而更难排查），而是记日志 + 退化为固定兜底。

### 三条稳定问题由谁作答

`demo_handlers.py` 针对固定演示问题给出**确定性**答案：

| 问题 | 证据来源 | 图表 |
| --- | --- | --- |
| 你的收入结构发生了什么变化？ | 招股书 `id=299` 第 **329** 页 | 有 |
| 你最近真的赚钱吗？ | 2025 半年报 `id=15` 第 **8** 页 | 有 |
| 目前最值得关注的风险是什么？ | 2025 半年报 `id=15` 第 **43** 页 | 无 |
| （其它问题） | — | 无，返回固定兜底 |

> 意图判定顺序与前端 `mock-data.ts` 的 `selectResponse()` **完全一致**
> （先「赚钱/盈利/利润/现金流」，再「风险」，最后「收入结构」），
> 否则同一个问题在「后端作答」与「前端降级作答」两条路径下会得到不同答案。


---

## ③ 数据库

唯一数据源：**`cninfo.db`**（当前实际路径 `xuejun-hackathon/data/cninfo.db`）。

| 表 | 内容 | 本后端怎么用 |
| --- | --- | --- |
| `docs` | 1905 条公告（14 列，含 `document_type` / `report_period` / `published_at` / `source_url` / `parse_status` / `superseded`） | 公告元数据、正文、**真实直链** |
| `chunks` | 4941 条**按页切分**的正文（`page_number` + `content`） | **核验页码**、给 LLM 贴分页原文 |
| `evidence` | 648 条结构化证据（含 3 条 `review_status='verified'` 的风险证据） | 只读参考；`value`/`unit` 尚不可信 |
| `chunks_fts` | trigram 全文索引 | 备用（当前检索仍走 `LIKE`） |
| `meta` | `fts_mode=trigram` | 能力探测 |

查询一律**只读**（`mode=ro`），参数化 SQL，**每次查询都带 `company_code`**，
默认过滤 `parse_status='ok' AND superseded=0`。
旧版 8 列库仍能跑（`has_extended_schema()` 做能力探测，缺表缺列自动降级）。

> ⚠️ **不要直接信任 `evidence.value` / `evidence.unit`** —— 数据库同学仍在修金额与单位。
> 出结论时以 `source_quote` 为准重新核对数字（`response_validator` 会做这件事）。

### 路径解析（自适应）

`DB_PATH`（或规范名 `DATABASE_PATH`，后者优先）默认 `data/cninfo.db`（相对 `xray-backend/`）。
**找不到时依次回退**：

1. `xray-backend/data/cninfo.db`
2. `../data/cninfo.db`（库放仓库根）
3. `../xuejun-hackathon/data/cninfo.db`（**当前实际位置**）

`DB_PATH_STRICT=true` 可关闭回退（测试「库不存在」场景用）。

```bash
python -c "import config; print(config.settings.db_file)"   # 看实际用的是哪个库
```

### ⚠️ 两个必须知道的数据事实

**1. `created_at` 是入库时间，不是公告日期。**
实测真实库全部同一天入库（`2026-10-02`），**零区分度**。所以时间窗口不能建立在它上面。

**2. 公告日期从文件名解析（方案 B）；新版库另有 `published_at` 列。**
`parse_announce_date()` 支持这些写法，解析结果放在 `announce_date` 字段：

| 文件名里的写法 | 解析结果 | 说明 |
| --- | --- | --- |
| `20260429_2026年一季度报告.pdf` | `2026-04-29` | **真实库主格式**（紧凑 YYYYMMDD） |
| `...（2025年7月15日）.pdf` | `2025-07-15` | 中文完整日期（必须带「日/号」） |
| `...（2025年7月）.pdf` / `2025-07` | `2025-07-01` | 只有年月 → 补 01 |
| `二〇二五年七月` / `２０２５年７月` | `2025-07-01` | 中文/全角数字 |
| `2024年年度报告.pdf` | `2024-01-01` | 只有年份 → 补 01-01 |
| `2026年1-6月经营情况.pdf` | `2026-01-01` | **月份区间**不可当日期 |
| 无任何日期 | `None`（`date_source="unknown"`） | 保留、排最后 |

**解析不到日期的公告不会被丢弃**，而是排到最后并标记为「日期未知」——宁可多给，不要误丢证据。

> 紧凑 `YYYYMMDD` 曾漏支持，导致整库日期退化成「标题里的年份 → `YYYY-01-01`」，
> 排序与时间窗口全部失真。现有 3 个回归测试锁住这个行为。

> **`days=365` 在真实库上是反直觉的**：会把绝大多数有日期的公告筛掉。
> 所以 `ANALYSIS_WINDOW_DAYS` **默认为空（不按时间过滤）**，
> 取全部公告、按 `announce_date` 倒序，再由 `ANALYSIS_MAX_ANNOUNCEMENTS`（默认 30）截断。
>
> ```bash
> # 实测效果（真实库 000066，共 481 条）
> days=30  →   1 条
> days=90  →  12 条
> days=365 →  88 条
> days=None → 481 条
> ```


库文件不存在、缺少 `docs` 表、或缺少列时，`db.py` 抛 `DatabaseNotReadyError`
并指出**具体缺什么**（含实际表名/列名），接口层翻译成 503 + 明确文案，绝不静默返回空。

查看实际表结构：

```bash
python scripts/inspect_db.py --rows 3
```

---

## ④ 分析流程与缓存

```
run_night_batch.py
   → db.get_stocks()                     取全部公司
   → db.get_announcements(code, days)    取该公司近期公告原文
   → analyzer.analyze_company(code)      拼 prompt → 调 DeepSeek → 结构化结果
   → cache/company_risk_{code}.json      当日缓存（同一天不重复调用 LLM）
   → logs/company_risk_{code}.json       逐家结论
   → logs/night_batch_report.json/.txt   汇总报告
```

### prompt 的硬性约束（`analyzer.SYSTEM_PROMPT`）

1. **只能依据给定公告原文**，不得引入外部知识或推测；
2. 每条结论**必须附原文引用**（逐字照抄 20–80 字），不得改写或杜撰；
3. 找不到依据就把 summary 写成「无足够信息」，且两个数组都返回 `[]`；
4. findings 只能归入 `S1`–`S4` 四类；
5. 只输出严格 JSON。

### 结果清洗（`analyzer.validate_analysis`）

即便模型不听话，也会在入库前清理：

* 丢弃 **没有 `source_quote`** 的证据；
* 丢弃 `type` 不是 S1–S4 的 finding；
* 丢弃**引用了不存在证据**的 finding；
* 清理没有任何 finding 引用的**孤立证据**；
* 报了 `high`/`medium` 却**没有有效 finding** → 降级为 `unknown`（防空口定罪）。

### 缓存

`cache/company_risk_{code}.json`，含 `version` 与 `cache_date`。
**同一家公司同一天不重复调用 LLM**；跨天自动失效；结构版本升级时旧缓存自动失效；
文件损坏按未命中处理（不抛异常）。

### 批处理参数

```bash
python scripts/run_night_batch.py --demo          # 只处理前 5 家
python scripts/run_night_batch.py                 # 全量
python scripts/run_night_batch.py --dry-run       # 只打印计划，不调 LLM、不写文件
python scripts/run_night_batch.py --demo --force  # 忽略当日缓存
python scripts/run_night_batch.py --limit 2 --days 90
```

报告字段：`run_at` / `mode` / `companies_processed` / `alerts_found` /
`duration_seconds` / `status`（外加诊断字段：逐家风险等级分布、失败原因等）。
`status` 取值 `success` / `partial` / `failed`，退出码 0 表示没整体失败。

---

## ⑤ 目录结构

```
xray-backend/
├── main.py                  FastAPI 入口、lifespan、CORS、统一异常处理（无调度器）
├── ask.py                   5 条路由；answer 优先级：确定性处理器 → 固定兜底
├── demo_handlers.py         ★ 三条稳定 Demo 问题的确定性处理器 + 事实核验
├── response_validator.py    ★ No Evidence, No Claim 响应校验器
├── db.py                    ★ 唯一数据访问层（docs/chunks/evidence，只读 sqlite3）
├── analyzer.py              ★ 唯一 LLM 判断逻辑（prompt + 清洗 + 当日缓存）
├── config.py                BaseSettings（DATABASE_PATH / DeepSeek / 缓存 / 日志）
├── schemas.py               响应契约（顶层恰好 6 键，Evidence 必须带原文引用）
├── llm_client.py            DeepSeek 客户端（OpenAI SDK）+ 失败降级文案
├── matcher.py               中文问题归一与相似度匹配
├── logging_config.py        logging + RotatingFileHandler
├── scripts/
│   ├── run_night_batch.py   ★ 批处理入口（--demo / --dry-run，写两份报告）
│   ├── make_samples.py      ★ 生成四个前端联调用响应样例（真的调接口）
│   ├── check_frontend_contract.py  把 api.ts 校验器译成 Python 逐字段判定
│   ├── check_real_pipeline.py      真实库全链路自检
│   ├── inspect_db.py        只读打印 cninfo.db 表结构
│   ├── selfcheck.py         结构自检（无需第三方依赖）
│   ├── check_db.py          db.py 行为自检（真跑真实 sqlite）
│   └── check_batch.py       analyzer / 批处理行为自检
├── samples/                 ★ 四个响应样例（前端联调直接用）
├── tests/                   合成库（3 家公司、8 列）—— 行为测试
│   ├── conftest.py          测试库 + fixtures（LLM 已 mock）
│   ├── helpers.py           契约断言工具
│   ├── test_api_db.py       db.py 的 pytest 用例
│   ├── test_api_contract.py 接口契约与缓存行为
│   └── test_api_batch.py    analyzer 与批处理
├── tests_real/              真实 cninfo.db —— 三条 Demo 问题的端到端验收
│   ├── conftest.py          指向真实库（与 tests/ 环境刻意隔离）
│   ├── pytest.ini           独立 basetemp，避免沙箱 ACL 冲突
│   └── test_api_real_demo.py  10 条验收 + 校验器 + 意图判定
├── deploy/crontab.xray      生产环境 cron 示例
├── _unused/                 旧链路归档（逐文件说明废弃原因见其 README）
│   └── README.md
├── cache/                   分析缓存（自动生成）
├── logs/                    批处理报告（自动生成）
├── requirements.txt
├── pytest.ini
├── .env.example
└── .gitignore
```

> 数据库本身不在本目录（`*.db` 已 gitignore）：当前在 `../xuejun-hackathon/data/cninfo.db`。

---

## ⑥ 测试与响应样例

**两套测试必须分别跑** —— 它们的环境互相冲突（合成库 vs 真实库，
`config.settings` 在导入时就固化）：

```bash
python -m pip install -r requirements.txt

python -m pytest tests        # 合成库：db / 契约 / 批处理 / analyzer 行为
python -m pytest tests_real   # 真实库：三条 Demo 问题的端到端验收
```

重新生成前端联调用的响应样例：

```bash
python scripts/make_samples.py          # 写到 samples/
```

样例**不是手写的**：脚本真的调用一次应用（TestClient 走完整路由 + 校验 + 兜底），
把响应原样落盘，所以样例与线上行为不会漂移。`tests_real` 里有一条用例专门断言
「磁盘上的样例 == 当前实现」，防止改完代码忘了重跑脚本。


| 文件 | 覆盖 |
| --- | --- |
| `test_api_db.py` | 三个查询函数；时间窗口/limit/正文截断；`created_at` 六种格式 + 坏日期行**必须保留**；`LIKE` 通配符转义；**只读**；缺库/空库/缺表/缺列四种情况都报明确错误 |
| `test_api_contract.py` | 路径无 `/api` 前缀；顶层 6 字段及顺序；high 风险公司出 findings+带引用的证据；**「无足够信息」时数组允许为空**；answer 非空且有最新公告；`.SH` 后缀归一；缓存命中；过期缓存仍可用；统一错误结构；profile/signals/health/search/admin |
| `test_api_batch.py` | prompt 硬约束；JSON 解析容错（代码块/前后废话/非 JSON）；清洗规则（丢弃悬空引用、非法 type、无 quote 证据、孤立证据、空口定罪降级）；**当日缓存不重复调用 LLM**；隔日失效；LLM 失败降级；批量隔离失败；`--dry-run` 不写文件；`--demo` 限量；报告六个必需字段 |

测试全程 `LLM_FAKE=true`（或直接 monkeypatch `analyzer.generate_answer`），**不发起任何外部请求**；
数据库指向临时文件，不碰真实的 `data/cninfo.db`。

> `pytest.ini` 里把 `basetemp` 指到了工作区内 —— 受限环境下系统临时目录无法创建 SQLite 文件。

无第三方依赖时也能先跑结构/行为自检：

```bash
python scripts/selfcheck.py      # 结构一致性
python scripts/check_db.py       # db.py（真跑 sqlite）
python scripts/check_batch.py    # analyzer + 批处理
```

---

## ⑦ 生产环境调度

应用**不含**定时器。生产由系统 cron 驱动：

```cron
# 每天 02:30 跑一次全量分析
30 2 * * * cd /opt/xray/xray-backend && /usr/bin/python3 scripts/run_night_batch.py >> logs/cron.log 2>&1
```

参考 `deploy/crontab.xray`。日志与报告都在 `logs/`（脚本自身按 `created_at`/运行日归档）。

---

## ⑧ 关于 `_unused/`

旧链路（ORM + 结构化财务字段 + 确定性规则引擎 + 外部渠道抓取 + 应用内调度）与本轮
新链路的数据模型不兼容，已整体归档到 `_unused/`，**不再被任何代码引用**。
每个文件的废弃原因与替代关系见 `_unused/README.md`。确认无误后可整目录删除。
