# X-Ray 企业穿透分析 · 后端

面向答辩/演示的**招股书核验问答系统**后端。

**数据源**：`data/cninfo.db` 的 `docs` 表 —— 从 PDF 提取的**公告原文**（没有结构化财务字段）。
**判断方式**：由 DeepSeek 读原文给出风险结论，**每条结论必须附原文引用**；
找不到依据时明确回答「无足够信息」，绝不编造。
**调度**：应用内**不含**定时任务 —— 生产环境由系统 cron 驱动，demo 环境手动执行。

---

## ① Demo 三条命令

```bash
# 1) 预读：遍历公司 → LLM 分析 → 生成缓存与报告
python scripts/run_night_batch.py --demo

# 2) 起服务
python main.py

# 3) 提问
curl -X POST http://localhost:8000/companies/688583/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"这家公司有什么风险？"}'
```

安装依赖（首次）：

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# 在 .env 里填 DEEPSEEK_API_KEY；不填也能跑，但结论都会是「无足够信息」
```

> `--demo` 只处理前 5 家公司（库里不足 5 家则全部处理），几秒内跑完，适合演示。
> 不带 `--demo` 则遍历全库。

---

## ② 接口

**路径保持不变**，没有任何前缀：

| 接口 | 说明 |
| --- | --- |
| `POST /companies/{stock_code}/ask` | 主接口：读缓存风险摘要 + 实时补一句最新公告 |
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

```jsonc
{
  "answer":   "688583：净利润为正但经营现金流为负……（风险等级：high，共 2 条发现）最新公告（2026-09-22）：2023年年度报告.pdf。",
  "claims":   [ { "id", "text", "evidence_ids", "verified", "verification_note" } ],
  "signals":  [ { "id", "type", "title", "side", "severity", "description", "evidence_ids" } ],
  "charts":   [ { "id", "title", "kind", "unit", "series", "evidence_ids", "note" } ],
  "evidence": [ { "id", "category", "period", "content", "document_id",
                  "document_title", "source_quote", "risk_dimension" } ],
  "suggested_questions": [ "…" ]
}
```

**契约要点**

* `signals[].type` 只能是 `S1`–`S4` 四类风险主题。
* 每条 `evidence` **必须带 `source_quote`（逐字原文）** —— 没有引用的证据会被契约直接拒绝。
* `claims` / `signals` / `evidence` **允许为空数组**：LLM 回答「无足够信息」时就是这种情况，
  此时不会硬塞一条没有原文引用的结论来凑数。
* 只要有 claim/signal/chart，其 `evidence_ids` 必须能在 `evidence` 里解析（悬空引用直接报错）。
* 命中情况通过响应头返回，不污染响应体：`X-XRay-Cache`（`hit-cache`/`miss`）、`X-XRay-Elapsed-Ms`。

---

## ③ 数据库

唯一数据源：**`data/cninfo.db`**，表 `docs`：

| 列 | 说明 |
| --- | --- |
| `id` | 主键 |
| `company_code` | 公司代码（即股票代码，无需从文件名解析） |
| `file_name` | 文件名（当标题用） |
| `rel_path` | 相对路径（UNIQUE） |
| `page_count` | 页数 |
| `text_content` | **公告正文**（分析主要依据） |
| `tables_json` | 表格（可能为空） |
| `created_at` | 入库时间（`DEFAULT CURRENT_TIMESTAMP`） |

所有 SQL 严格基于以上 8 列，不做任何猜测。查询一律**只读**（`mode=ro`），不写任何业务表。

### 路径解析（方案 A：自适应）

`DB_PATH` 默认 `data/cninfo.db`（相对 `xray-backend/`）。**找不到时会自动回退到
`../data/cninfo.db`** —— 所以数据库放在工作区根目录 `dsh/data/` 也能直接跑，不需要改配置、
也不需要移动文件。`DB_PATH_STRICT=true` 可关闭回退。

```bash
python -c "import config; print(config.settings.db_file)"   # 看实际用的是哪个库
```

### ⚠️ 两个必须知道的数据事实

**1. `created_at` 是入库时间，不是公告日期。**
实测你的库：126 条全部同一天入库（`2026-10-02`），**零区分度**。所以时间窗口不能建立在它上面。

**2. 公告日期只能从文件名解析，且覆盖不完整（方案 B）。**
`parse_announce_date()` 支持这些写法，解析结果放在 `announce_date` 字段：

| 文件名里的写法 | 解析结果 | 你的库 |
| --- | --- | --- |
| `...（2025年7月15日）.pdf` | `2025-07-15` | 0 条 |
| `...（2025年7月）.pdf` / `2025-07` | `2025-07-01` | 14 条 |
| `二〇二五年七月` / `２０２５年７月` | `2025-07-01` | — |
| `2024年年度报告.pdf` | `2024-01-01`（只有年份 → 补 01-01） | 52 条 |
| 无任何日期 | `None`（`date_source="unknown"`） | 58 条 |

**解析不到日期的公告不会被丢弃**，而是排到最后并标记为「日期未知」——宁可多给，不要误丢证据。

> **因此 `days=365` 在你的库上是反直觉的**：最新一份带日期的公告距今已超过 365 天，
> 固定 365 会把**全部 68 条有日期的公告**筛掉，只剩 58 条无日期的。
> 所以 `ANALYSIS_WINDOW_DAYS` **默认为空（不按时间过滤）**，
> 取全部公告、按 `announce_date` 倒序，再由 `ANALYSIS_MAX_ANNOUNCEMENTS`（默认 30）截断。
>
> ```bash
> # 实测效果（真实库 126 条）
> days=30  → 58 条（有日期 0）
> days=90  → 58 条（有日期 0）
> days=365 → 58 条（有日期 0）
> days=None → 126 条（有日期 68）
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
├── ask.py                   5 条路由 + 回答合成（读缓存摘要 + 补最新公告）
├── db.py                    ★ 唯一数据访问层（docs 表，只读 sqlite3）
├── analyzer.py              ★ 唯一 LLM 判断逻辑（prompt + 清洗 + 当日缓存）
├── config.py                BaseSettings（DB_PATH / DeepSeek / 缓存 / 日志）
├── schemas.py               响应契约（顶层恰好 6 键，Evidence 必须带原文引用）
├── llm_client.py            DeepSeek 客户端（OpenAI SDK）+ 失败降级文案
├── matcher.py               中文问题归一与相似度匹配
├── logging_config.py        logging + RotatingFileHandler
├── scripts/
│   ├── run_night_batch.py   ★ 批处理入口（--demo / --dry-run，写两份报告）
│   ├── inspect_db.py        只读打印 cninfo.db 表结构
│   ├── selfcheck.py         结构自检（无需第三方依赖）
│   ├── check_db.py          db.py 行为自检（真跑真实 sqlite）
│   └── check_batch.py       analyzer / 批处理行为自检
├── tests/
│   ├── conftest.py          测试库 + fixtures（LLM 已 mock）
│   ├── helpers.py           契约断言工具
│   ├── test_api_db.py       db.py 的 pytest 用例
│   ├── test_api_contract.py 接口契约与缓存行为
│   └── test_api_batch.py    analyzer 与批处理
├── deploy/crontab.xray      生产环境 cron 示例
├── _unused/                 旧链路归档（逐文件说明废弃原因见其 README）
│   └── README.md
├── data/cninfo.db           ← 你的公告库放这里（未纳入版本库）
├── cache/                   分析缓存（自动生成）
├── logs/                    批处理报告（自动生成）
├── requirements.txt
├── pytest.ini
├── .env.example
└── .gitignore
```

---

## ⑥ 测试

```bash
python -m pip install -r requirements.txt
python -m pytest -v
```

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
