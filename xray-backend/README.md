# X-Ray 企业穿透分析 · 后端

面向答辩/演示的**招股书核验问答系统**后端。

> 当前状态（2026-10-03）：v2 已晋升为仓库根目录稳定 `cninfo.db`，共有 2254 条 Evidence、2158 条可用、96 条隔离。动态问答已覆盖财务、治理、审计、股东回报、员工与激励、供应链、研发和风险等主题，并可从最终 Evidence 确定性生成折线图/柱状图。本文保留的首轮 503/407 Evidence 与 P0 `charts=[]` 说明仅是历史实现记录；最终事实与验证结果以 `docs/HANDOFF.md` 为准。

**数据源**：`cninfo.db` 的 `docs` / `chunks` / `evidence` 表 —— 从 PDF 提取的**公告原文**。
**三条稳定 Demo 问题**：由 `demo_handlers.py` 用**已核验的证据**确定性作答
（不依赖大模型、不依赖缓存，毫秒级返回）。
**其它金融问题（四家公司）**：走**动态 Evidence-first 问答** —— 后端先检索候选证据并
回到原文逐字核验，模型只能在候选集合里挑 id 组织答案，最后机械校验（见 ②）。
**证据不足 / 覆盖面之外的问题**：返回固定兜底文案，**不调用模型**，不让模型补充事实。
**调度**：应用内**不含**定时任务 —— 生产环境由系统 cron 驱动，demo 环境手动执行。

### 决策顺序（`POST /companies/{code}/ask`）

| 顺序 | 条件 | 走哪条路 | 是否调模型 |
| ---: | --- | --- | --- |
| 1 | 思看科技（688583）的三条稳定问题 | `demo_handlers` 确定性作答 | 否 |
| 2 | 问题映射不到任何已知财务指标（如「员工喜欢吃水果」） | 固定兜底 | 否 |
| 3 | 其他公司 / 其他金融问题，且检索到**核验通过**的候选证据 | 动态 Evidence-first | 是（可开关） |
| 4 | 上面任一步失败、无证据、模型不可用、解析/校验失败 | 固定兜底（HTTP 200） | — |

支持的公司：`688583`（思看科技）、`600570`、`000066`、`300558`。
> 动态链路有**保命开关** `DYNAMIC_QA_ENABLED=false`：关掉后除稳定三问外一律证据不足，
> 绝不影响演示基线。
>
> 本轮交付的 PR 描述（模型 / 耗时 / 成功与拒答问题 / 测试结果 / 已知限制）
> 见 `docs/BACKEND_PR_NOTES.md`。

---

## ① Demo 三条命令

**环境**：Python 3.10+（本项目实测 3.14.3）。

```bash
# 1) 装依赖 + 准备配置
python -m pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
#   公开部署建议显式指定仓库根稳定库，并用 /health 核对绝对路径（见 ③）。
#   DATABASE_PATH=D:\Codes\XueJunHackathon\cninfo.db
#   DEEPSEEK_API_KEY=         ← 可选！三条稳定问题不需要大模型

# 2) 起服务（三条稳定问题无需大模型，也无需先跑批处理）
python main.py              # → http://127.0.0.1:8000

# 3)（可选）跑一遍全量风险分析，产出缓存与报告
python scripts/run_night_batch.py --demo
```

> **不配 API key 也能完整演示三条稳定问题** —— 这正是做确定性处理器的理由。
> 三条稳定问题不调用大模型；其他已覆盖金融问题在检索到可用 Evidence 后调用 DeepSeek，认不出的意图一律返回证据不足。

### 四条演示问题（真实调用示例）

```powershell
$body = @{ question = "你的收入结构发生了什么变化？" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/companies/688583/ask" `
  -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 8
```

```bash
# 收入结构（带图表）
curl -s -X POST http://127.0.0.1:8000/companies/688583/ask \
  -H "Content-Type: application/json" -d '{"question":"你的收入结构发生了什么变化？"}'

# 盈利质量（带图表）
curl -s -X POST http://127.0.0.1:8000/companies/688583/ask \
  -H "Content-Type: application/json" -d '{"question":"你最近真的赚钱吗？"}'

# 主要风险（无图表）
curl -s -X POST http://127.0.0.1:8000/companies/688583/ask \
  -H "Content-Type: application/json" -d '{"question":"目前最值得关注的风险是什么？"}'

# 证据不足（HTTP 200 + 固定兜底，四个数组全空）
curl -s -X POST http://127.0.0.1:8000/companies/688583/ask \
  -H "Content-Type: application/json" -d '{"question":"你的员工喜欢吃水果吗？"}'

# 健康检查（含最终解析出的数据库路径，不含任何密钥）
curl -s http://127.0.0.1:8000/health
```

四条演示问题的实测响应已固化在 `samples/`（见 ⑥ 响应样例）。
`suggested_questions` **只返回这四条，且顺序固定**。

---

## ② 接口

**路径保持不变**，没有任何前缀：

| 接口 | 说明 |
| --- | --- |
| `POST /companies/{stock_code}/ask` | 主接口：三条稳定问题确定性作答；其他已覆盖金融问题走动态 Evidence-first |
| `GET /companies/{stock_code}/profile` | 公司画像：公告数量、日期区间、最近标题、风险摘要 |
| `GET /companies/{stock_code}/signals` | 风险信号（来自缓存的分析结论） |
| `POST /admin/refresh?stock_code=xxx` | 手动触发分析；`ADMIN_TOKEN` 留空时关闭，启用后只接受 `X-Admin-Token` 请求头 |
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
  -H "X-Admin-Token: <随机长 token>"
```

代码带交易所后缀也能识别（`688583.SH` → `688583`）。

### 响应结构（顶层恰好 6 个字段，顺序固定）

**契约以仓库根目录 `src/lib/api.ts` 的运行时校验器为准** ——
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

三条 Demo 的出处**不取库里的链接**，而是取自 `verified_sources.py` 里两份**已逐页核验**的
上交所原始 PDF（原因见下表）。LLM 分析链路仍走库里的 `docs.source_url`。

| 问题 | 权威来源（`source_url`） | `document_title` | PDF 查看器页码 |
| --- | --- | --- | --- |
| 收入结构 | 上交所《招股说明书（注册稿）》`001845_20240816_R2YE.pdf`（506 页） | 思看科技首次公开发行股票并在科创板上市招股说明书（注册稿） | **321 / 322 / 324** |
| 盈利质量 | 上交所《2025 年半年度报告》`688583_20250828_9F77.pdf`（269 页） | 思看科技 2025 年半年度报告 | **8 / 9** |
| 主要风险 | 同上 | 同上 | **2 / 43** |

* **`source_url`**：必须是**可直接打开且不带 fragment 的 PDF 原始地址**，并满足前端的高可靠度规则
  （`HTTPS` + `sse.com.cn` 或其子域名）。拿不到就**丢弃该条证据** ——
  宁可少给一条，也不编一个点开就 404 的链接，更不拿公告列表页冒充原文。
* **`source_page`**：必须是**被引用片段真正所在的页码**，并在自检中回到原文逐字核验。
  前端负责把它追加成 `#page=N`；后端不得重复追加，否则会形成双 fragment。

> ⚠️ 两个真实存在过的错误，都已经被回归测试钉死：
>
> **1）伪造页码**：旧实现把公告的 `page_count` 当引用页码，于是 520 页的招股书里
> 每条引用都声称出自「第 520 页」。现在分两条路：
> 1. **确定性处理器**：页码在 `demo_handlers._FACTS` 里显式声明，由 `verify_facts()`
>    （对库）与 `verify_facts_against_pdf()`（对 PDF 本体）核验；
> 2. **LLM 分析链路**：prompt 按「`[第 N 页]`」给出分页正文，要求模型回填 `source_page`，
>    越界即视为不可信并**丢弃该条证据**（`ask.py` 的 `_build_evidence`）。
>
> **2）张冠李戴（页码配错 PDF）**：`cninfo.db` 里的招股书是**巨潮上市稿（520 页）**，
> 而前端已核验的权威地址是**上交所注册稿（506 页）**。两版**页数不同、页码偏移也不固定**
> （便携式那段：上市稿 329 → 注册稿 322；跟踪式那段：上市稿 332 → 注册稿 324），
> 所以**库无法为注册稿的页码作证**。因此：
> * 对外 `source_url` / `document_title` / `source_page` / `source_quote` 一律指注册稿；
> * 库只用来确认「这条引文在库内那份文档里确实存在且唯一」；
> * 注册稿的页码由 `verify_facts_against_pdf()` 直接对 PDF 本体核验
>   （需要 `pip install pypdf` + 下载注册稿，缺了会 skip 而不是假通过）。

> ⚠️ **招股书三条证据不在同一页**，这不是笔误：321 页是主营业务收入合计与同比变动表，
> 322 页是便携式扫描仪那段，324 页是跟踪式产品那段。
> **第 323 页只讲彩色扫描仪与五大系列，没有跟踪式收入数据** ——
> 前后端均已把跟踪式证据固定在第 324 页。

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

| 问题 | 证据来源（已核验的上交所 PDF） | PDF 查看器页码 | 图表 |
| --- | --- | --- | --- |
| 你的收入结构发生了什么变化？ | 招股说明书（注册稿） | 321 / 322 / 324 | 有 |
| 你最近真的赚钱吗？ | 2025 年半年度报告 | 8 / 9 | 有 |
| 目前最值得关注的风险是什么？ | 2025 年半年度报告 | 2 / 43 | 无 |
| （其它问题） | — | — | 无，返回固定兜底 |

> 意图判定顺序与前端 `mock-data.ts` 的 `selectResponse()` **完全一致**
> （先「赚钱/盈利/利润/现金流」，再「风险」，最后「收入结构」），
> 否则同一个问题在「后端作答」与「前端降级作答」两条路径下会得到不同答案。
>
> ⚠️ 注意：判定的单位是**意图**而不是"那三句话"。像「这家公司有什么风险？」
> 含「风险」，会命中风险意图并正常作答 —— 这是期望行为。
> 兜底只对**认不出意图**的问题生效（如「你的员工喜欢吃水果吗？」）。


---

## ②-补 动态 Evidence-first 问答（四家公司）

### 一句话

模型**没有**生成证据的能力：`evidence` 数组完全由后端从 `cninfo.db` 检索、
并回到原文逐字核验过；模型只输出 `answer` / `claims` / `signals`，
且只能引用本次候选集合里出现过的 id。

```
问题
 → dynamic_evidence.retrieve_candidates()   参数化 SQL（每条都带 company_code）
                                              + 默认过滤 parse_status='ok' AND superseded=0
                                              + JOIN docs 取标题与真实链接
 → 机械核验每条候选：页码是正整数且不越界、引文里的数字在该页逐字出现、
   顺序一致、首末跨度 ≤ DYNAMIC_MAX_QUOTE_SPAN
 → 生成 EV-001…EV-0NN 的**封闭候选集合**（6~15 条）
 → dynamic_qa 组装 JSON-only Prompt → DeepSeek → 解析（含容错）
 → 机械校验：悬空 id 丢弃、无证据条目丢弃、数字必须能在引文里核到
 → 从最终 Evidence 确定性构建图表（需要时最多 3 张）→ 再走统一出口 response_validator
```

### 引文核验为什么不是「整串子串匹配」

库里 `evidence.source_quote` 是**按表格结构转写的**（列之间 `|`，
如 `营业收入（元） | 7,958,051,684.14 | 6,366,241,242.46 | 25.00%`），
而 PDF 文本层里同一段没有 `|`。整串匹配会 **100% 失败**（旧库实测 541/541 条全挂），
而只比"去掉数字后的标签"又几乎全过（等于不校验）。

因此核验规则是三条，缺一不可：

1. 引文里每个**有意义数字**（去逗号后 ≥3 位、或百分数）必须在所引页面上逐字出现；
2. 这些数字在页面上的**先后顺序**必须与引文一致（防止拿不同表格的单元格拼凑）；
3. 首末数字在该页上的**跨度不得超过 `DYNAMIC_MAX_QUOTE_SPAN`**（默认 200 字符）——
   保证它们确实属于同一段连续原文。

首轮 503 条 Evidence 的核验结果属于历史基线；当前稳定库已在扩库阶段重新执行连续原文、长度和数字边界校验，2254 条中有 2158 条可参与回答、96 条隔离。

**例外：不含数字的定性引文**（风险因素、政策表述）。新库里 688583 有 3 条
`review_status='verified'`、`method='manual'` 的风险证据，通篇没有数字 ——
风险因素本就是定性表述。若坚持"必须有数字"，这些**人工核验过的**成果会被全数拒绝。
所以这类引文走一条**更强**的口径：整串忽略空白后必须是该页原文的**连续子串**，
且长度 ≥ 20 字（短句在整篇里到处都是，证不了出处）。

> **对外的 `source_quote` 是页面原文里的连续片段**（用 `db.document_page_text` 切出来，
> 带指标标签，如 `营业收入（元） 7,958,051,684.14 6,366,241,242.46 25.00%`），
> 不是那份带 `|` 的转写 —— 所以任何人拿它回原文搜都能搜到。

### 核验状态（可选字段）

`evidence.verification_status`（**可选**，前端不校验，保持向后兼容）：

| 值 | 含义 |
| --- | --- |
| `verified` | 库里 `review_status='verified'` 且本次原文/页码复核通过 |
| `auto` | 本次请求已完成机械核验（页码、原文逐字、链接都对得上） |
| `pending` | 未通过核验。**不会返回** —— 不通过的候选直接丢弃，宁可证据不足 |

### 真实模型实测（2026-10-02，deepseek-flash，新库）

受控冒烟：`python scripts/smoke_dynamic.py`（**不放进自动测试**，会真联网、真花钱）。

| 问题 | 结果 |
| --- | --- |
| 思看科技三条稳定问题 | 全部 `demo-handler`，**66–76 ms**，不碰模型 |
| 其他三家公司 × 三个金融问题（9 次） | **9/9 `dynamic-llm` 成功** |
| 「你的员工喜欢吃水果吗？」 | 4/4 `insufficient`，四个数组全空，**0 次模型调用** |
| 动态回答耗时 | **3.3–8.9 s**（换用补齐直链与指标的新库后，比旧库的 3.1–16.4 s 快一倍） |

> 换库前后对比（同一份代码、同一组问题）：旧的库下 9 次里有 1 次模型自己判断
> "候选证据里没有该指标数据"而拒答；新库补齐了四家公司的结构化证据与 PDF 直链，
> 9/9 全部答出。**拒答行为本身是期望的**（`No Evidence, No Claim`），
> 只是不该由数据缺口来触发。
>
> 两条路径都在自动化测试里固化：理想模型必须走通
> （`tests_real/test_api_real_dynamic.py`），编造引用的坏模型必须被拦下
> （同文件的 `test_dynamic_questions_never_claim_when_no_evidence`）。

**真实事故（已修，值得记住）**：即使 system prompt 三次强调「只输出 JSON」，
deepseek-flash 仍会：
① 先写一段分析、末尾才补 JSON；② 把 `answer` 写成 `结论` / `conclusion`；
③ 给出 prose 结论却把 `claims` 留空（连 `EV-xxx` 编号也不写）。
早期实现只做整串 `json.loads` + 只认 `claims` 字段，于是一份**引用正确**的回答
被整次降级成「无法回答」。现在 `dynamic_qa.parse_llm_json` 做三层确定性容错
（围栏 → 平衡花括号扫描 → 首尾截取 + 键名归一化），
`_coerce_answer_into_claims` 再把带数字/编号的句子机械转成 claim
只有句子明确带有效 `EV-xxx` 时才会机械转成 Claim；没有显式 ID 时不会凭相同数字猜出处。这些行为都有回归测试。

---

## ③ 数据库

唯一数据源：**`cninfo.db`**（默认放在**仓库根目录**，见下面的路径解析）。

| 表 | 内容 | 本后端怎么用 |
| --- | --- | --- |
| `docs` | 1905 条公告（14 列，含 `document_type` / `report_period` / `published_at` / `source_url` / `parse_status` / `superseded`） | 公告元数据、正文、**真实直链** |
| `chunks` | 2845 条**按页切分**的正文（`page_number` + `content`） | **核验页码**、给 LLM 贴分页原文 |
| `evidence` | 2254 条结构化证据（2158 条可用、96 条隔离） | 动态问答的候选来源；后端强制过滤 `excluded=1` |
| `chunks_fts` | 2845 / 2845 条全文索引（`meta.fts_mode=trigram`） | 全文检索与候选召回 |
| `meta` | `schema_version=1.0` / `fts_mode=trigram` | 能力探测 |

> **四家公司的 `docs.source_url` 已 100% 补齐**（688583 312/312、600570 564/564、
> 000066 481/481、300558 548/548），所以动态证据都能给出**文档自己的 PDF 直链**，
> 不再需要退回到巨潮公告列表页。`tests_real` 里有一条测试专门防止它退化。
>
> **没有 chunks 的文档走页界标记**：1905 篇里 1905 篇都有「--- 第N页 ---」标记，
> 所以即使某篇没有切页，`db.document_page_text` 仍能取到该页原文、照常核验。
>
> ⚠️ **不要直接信任 `evidence.value` / `evidence.unit`** —— 数据库同学仍在修金额与单位。
> 出结论时以 `source_quote` 为准重新核对数字（`response_validator` 会做这件事）。
> 同理，`evidence.content` 是自动抽取的残渣（「营业收入：214.0%」），
> 动态证据的 `content` 由后端按已核验摘录重新拼，不使用原字段。

### `evidence.metric` 取值（动态问号 → 指标映射的依据）

| 类别 | metric |
| --- | --- |
| 收入与利润 | `revenue`、`net_profit`、`net_profit_attr`（归母）、`net_profit_deducted`（扣非）、`eps` |
| 现金与资产 | `operating_cash_flow`、`total_assets`、`equity_attr`（归母净资产）、`debt_ratio` |
| 经营质量 | `gross_margin`、`rd_investment`、`rd_expense`、`rd_ratio`、`accounts_receivable`、`inventory` |
| 风险（定性、手工核验） | `risk_product_mix`、`risk_tech_edge`、`risk_downstream_demand` |

> ⚠️ 「归母净利润」对应的是 **`net_profit_attr`**，不是 `net_profit`。
> 实测 `300558` **一条 `net_profit` 都没有**，只有 `net_profit_attr` ——
> 关键词→指标的映射顺序写反会让它问归母净利润时无据可用。
> `dynamic_evidence.METRIC_RULES` 里每条规则的关键词顺序都按"更具体的排前面"
> 排列（`扣非` 在 `净利润` 前、`研发投入占` 在 `营业收入` 前、`净资产` 在 `归母` 前），
> 这些顺序都有回归测试，改动前请先看 `tests/test_api_dynamic_qa.py` 里的断言。

查询一律**只读**（`mode=ro`），参数化 SQL，**每次查询都带 `company_code`**，
默认过滤 `parse_status='ok' AND superseded=0`。
旧版 8 列库仍能跑（`has_extended_schema()` 做能力探测，缺表缺列自动降级；
没有 `evidence` 表的库会直接返回空候选，不会抛错）。

### 路径解析（自适应）

`DATABASE_PATH`（规范名，**优先级最高**）或旧名 `DB_PATH`；两者都留空时自动探测：

1. `../cninfo.db` —— **仓库根目录（默认布局，不用挪库）** ← 推荐
2. `xray-backend/data/cninfo.db`
3. `../data/cninfo.db`

`DB_PATH_STRICT=true` 可关闭自动探测（测试「库不存在」场景用）。
**指向不存在的文件时不会静默创建空库**：`db.py` 抛 `DatabaseNotReadyError`，
接口返回 503 + 明确路径，`/health` 里也能看到解析结果。

```bash
python -c "import config; print(config.settings.db_file)"   # 看实际用的是哪个库
curl -s http://127.0.0.1:8000/health | python -m json.tool   # db_path 在 capabilities.settings 里
```

`.env.example` 只写通用示例（`DATABASE_PATH=` 留空即可），**不硬编码任何个人绝对路径**。
数据库一律以 `mode=ro` 只读打开，请求期间不写库、不重建。

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
├── ask.py                   5 条路由；决策顺序：确定性处理器 → 覆盖面外兜底 → 动态问答 → 兜底
├── demo_handlers.py         ★ 三条稳定 Demo 问题的确定性处理器 + 事实核验
├── dynamic_evidence.py      ★ 动态问答的候选检索 + 原文/页码机械核验
├── dynamic_qa.py            ★ 动态问答主流程（Prompt / JSON 解析 / 机械校验 / 组装）
├── verified_sources.py      ★ 已核验的权威 PDF 目录（上交所注册稿 / 半年报）
├── response_validator.py    ★ No Evidence, No Claim 响应校验器（含数据库原文复核）
├── db.py                    ★ 唯一数据访问层（docs/chunks/evidence，只读 sqlite3）
├── analyzer.py              ★ 唯一 LLM 判断逻辑（prompt + 清洗 + 当日缓存）
├── config.py                BaseSettings（DATABASE_PATH / DeepSeek / 动态问答开关 / 缓存 / 日志）
├── schemas.py               响应契约（顶层恰好 6 键，Evidence 必须带原文引用）
├── llm_client.py            DeepSeek 客户端（OpenAI SDK）+ 失败降级文案
├── matcher.py               中文问题归一与相似度匹配
├── logging_config.py        logging + RotatingFileHandler
├── scripts/
│   ├── run_night_batch.py   ★ 批处理入口（--demo / --dry-run，写两份报告）
│   ├── make_samples.py      ★ 生成前端联调用响应样例（真的调接口；--dynamic 生成动态样例）
│   ├── smoke_dynamic.py     ★ 真实模型受控冒烟（四家公司 × 通用问题，会联网）
│   ├── check_frontend_contract.py  把 api.ts 校验器译成 Python 逐字段判定
│   ├── check_real_pipeline.py      真实库全链路自检
│   ├── inspect_db.py        只读打印 cninfo.db 表结构
│   ├── selfcheck.py         结构自检（无需第三方依赖）
│   ├── check_db.py          db.py 行为自检（真跑真实 sqlite）
│   └── check_batch.py       analyzer / 批处理行为自检
├── samples/                 ★ 响应样例（4 个稳定 + 12 个动态，前端联调直接用）
├── tests/                   合成库（3 家公司、8 列 + 一份扩展库）—— 行为测试
│   ├── conftest.py          测试库 + fixtures（LLM 已 mock；含 4 公司扩展库构造）
│   ├── helpers.py           契约断言工具
│   ├── test_api_db.py       db.py 的 pytest 用例
│   ├── test_api_contract.py 接口契约与缓存行为
│   ├── test_api_batch.py    analyzer 与批处理
│   ├── test_api_dynamic_qa.py    动态问答单元测试（解析/校验/核验，不碰库）
│   └── test_api_dynamic.py       动态问答接口与检索隔离（扩展合成库）
├── tests_real/              真实 cninfo.db —— 端到端验收
│   ├── conftest.py          指向真实库（与 tests/ 环境刻意隔离）
│   ├── pytest.ini           独立 basetemp，避免沙箱 ACL 冲突
│   ├── test_api_real_demo.py     10 条稳定 Demo 验收 + 校验器 + 来源/页码回归
│   └── test_api_real_dynamic.py  ★ 四家公司动态问答验收（检索隔离/引文可核/URL 裸地址）
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

> 数据库本身不在本目录（`*.db` 已 gitignore）：默认放在**仓库根目录**。

---

## ⑥ 测试与响应样例

**两套测试必须分别跑** —— 它们的环境互相冲突（合成库 vs 真实库，
`config.settings` 在导入时就固化）：

```bash
python -m pip install -r requirements.txt

python -m pytest tests -q        # 合成库：db / 契约 / 批处理 / analyzer 行为
python -m pytest tests_real -q   # 真实库：三条 Demo 问题的端到端验收
```

`tests_real` 需要仓库根目录有 `cninfo.db`（或用 `XRAY_REAL_DB` / `DATABASE_PATH` 指定）；
找不到会 **skip 整个目录**，而不是伪造一个库来自我盖章。

**可选**：额外对**注册稿 PDF 本体**核验招股书的逐字原文与页码
（库里的招股书是另一个版本，无法替注册稿的页码作证）：

```bash
python -m pip install pypdf
# 下载 https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf
set XRAY_PROSPECTUS_PDF=D:\path\to\001845_20240816_R2YE.pdf   # PowerShell: $env:XRAY_PROSPECTUS_PDF=...
python -m pytest tests_real -q -k pdf
```

没有 `pypdf` 或缺 PDF 时，这条用例会 skip 并提示原因（不会假通过）。

重新生成前端联调用的响应样例：

```bash
python scripts/make_samples.py                # 4 个稳定样例
python scripts/make_samples.py --dynamic      # 额外 12 个动态样例（四家公司 × 3 问）
```

样例**不是手写的**：脚本真的调用一次应用（TestClient 走完整路由 + 校验 + 兜底），
把响应原样落盘，所以样例与线上行为不会漂移。`tests_real` 里有一条用例专门断言
「磁盘上的样例 == 当前实现」，防止改完代码忘了重跑脚本。

`--dynamic` 用一个**确定性假 LLM**（只看候选证据就能写出合法 JSON）：
样例要能复现、能进版本库、不能让评审看到随机内容。真实模型的表现见 ②-补。

真实模型**受控冒烟**（会联网、会花钱，不进自动测试）：

```bash
# 需要 .env 里配好 DEEPSEEK_API_KEY；四家公司 × 通用问题，打印来源/耗时/成功率
python scripts/smoke_dynamic.py
```

### 测试文件对照

| 文件 | 覆盖 |
| --- | --- |
| `test_api_db.py` | 三个查询函数；时间窗口/limit/正文截断；`created_at` 六种格式 + 坏日期行**必须保留**；`LIKE` 通配符转义；**只读**；缺库/空库/缺表/缺列四种情况都报明确错误 |
| `test_api_contract.py` | 路径无 `/api` 前缀；顶层 6 字段及顺序；high 风险公司出 findings+带引用的证据；**「无足够信息」时数组允许为空**；answer 非空且有最新公告；`.SH` 后缀归一；缓存命中；过期缓存仍可用；统一错误结构；profile/signals/health/search/admin |
| `test_api_batch.py` | prompt 硬约束；JSON 解析容错（代码块/前后废话/非 JSON）；清洗规则（丢弃悬空引用、非法 type、无 quote 证据、孤立证据、空口定罪降级）；**当日缓存不重复调用 LLM**；隔日失效；LLM 失败降级；批量隔离失败；`--dry-run` 不写文件；`--demo` 限量；报告六个必需字段 |
| `test_api_dynamic_qa.py` | JSON 解析三层容错（含 `结论`/`conclusion` 键名归一、字符串里的 `}`）；悬空 id / 跨响应 id 被丢弃；无证据条目丢弃后整次兜底；charts 恒为 `[]`；引文核验（数字缺失/顺序颠倒/跨度过宽/无数字 全部拒绝）；Claim/Signal 按自身 Evidence 做数字硬校验；仅显式 `EV-xxx` 可做 prose→claim 转换；拒答句不转换；无 Key/超时/非法 JSON/空内容降级；水果问题不调用模型 |
| `test_api_dynamic.py` | 四家公司检索严格隔离（候选文档必须属于本公司）；页码越界/页码缺失/伪造引文三条脏数据被拦下；返回引文必须是所引页面的连续原文；候选编号连续且不超预算；覆盖面外问题在**检索层**即断掉；动态回答过统一校验出口；**思看科技稳定三问不被动态链路抢走**；非思看公司拿不到思看事实；保命开关 |
| `tests_real/test_api_real_dynamic.py` | 真实库上的四家公司：候选引文能在所引页核到；页码不越界；URL 是裸地址；核验状态只有 verified/auto；动态回答端到端合法且经数据库侧复核；水果问题四个数组全空且 0 次模型调用；稳定三问不退化；「坏模型」编造引用被拦下 |

测试全程 `LLM_FAKE=true`（或直接 monkeypatch `analyzer.generate_answer` /
`dynamic_qa.generate_answer`），**不发起任何外部请求**；
数据库指向临时文件，不碰真实的 `cninfo.db`。

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

## ⑧ 前后端联调信息（本轮交付）

| 项 | 值 |
| --- | --- |
| 启动 | `python main.py`（默认 `127.0.0.1:8000`） |
| Python | 3.10+（实测 3.14.3） |
| 依赖 | `python -m pip install -r requirements.txt` |
| `.env` 必需变量 | **无**。数据库留空即自动探测；`DEEPSEEK_API_KEY` 只影响非稳定问题 |
| 前端只需设置 | `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` |
| 权威 PDF ① | 招股说明书（注册稿）`https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf` —— 第 321 / 322 / 324 页 |
| 权威 PDF ② | 2025 年半年度报告 `https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-28/688583_20250828_9F77.pdf` —— 第 2 / 8 / 9 / 43 页 |

`/health` 示例（节选）：

```jsonc
{
  "status": "ok",
  "database": true,
  "companies": 4,
  "llm_ready": false,
  "capabilities": {
    "settings": { "db_path": "D:\\Codes\\XueJunHackathon\\cninfo.db" }   // 示例；实际为解析后的路径
  }
}
```

四条问题的调用示例见 ①。

### 前端需要注意的差异（本轮新增）

| 项 | 说明 |
| --- | --- |
| `evidence.verification_status` | **新增可选字段**（`verified`/`auto`/`pending`）。不返回时前端按未标注处理；`pending` 永远不会出现。加可选字段是为了不破坏现有前端契约 |
| 响应头 `X-XRay-Cache` | 新增取值 `dynamic-llm`（动态链路作答）与 `insufficient`（固定兜底）。`demo-handler` 仍是思看科技三条稳定问题 |
| 动态回答的 `charts` | 从最终通过校验的 Evidence 确定性构建；支持 `line` / `bar`，一个复合问题最多 3 张 |
| `suggested_questions` | 思看科技仍是原来四条、顺序不变；**其他三家公司用产品约定的通用四问**（营收/归母净利润、经营现金流、盈利趋势、员工水果） |
| `evidence.category` | 库里 `category='risk'` 的证据在响应里是 **`business`**（前端只认三个值，见「已知限制」第 7 条） |
| 非思看公司的风险问题 | 可总结当前 Evidence 直接支持的风险信号，但不得声称覆盖全部经营、法律、行业或合规风险 |

### 已知限制 / 未完成事项

1. **注册稿 PDF 不在版本库**（13 MB）。页码核验用例需要手工下载后才跑，
   否则 skip；库那份是上市稿，**不能**替注册稿作证。
2. **`cninfo.db` 不入库**（`*.db` 已 gitignore），部署时需单独放置到仓库根目录。当前回滚备份为 `cninfo.pre-v2-7408dcc2.db`（同样不入库）。
3. 半年报页码虽与库完全一致，但 SSE 该 URL 对脚本化下载返回 JS 反爬页
   （浏览器/正常客户端可打开）；核验时用的是与之同版的库内文档 + 人工确认。
4. Nightly Pipeline / Snapshot / 维护模式均**未接入当前产品闭环**，
   属目标架构，不要当成已完成能力。
5. **动态问答仍受公告覆盖和摘录上下文限制**：2158 条可用 Evidence 中绝大多数是机械核验的 `auto`，并非人工逐条核验；`verification_status` 必须如实展示。96 条无法安全形成精确短摘录或字段有缺陷的 Evidence 已隔离，不能参与候选、Prompt 或响应。
6. **风险回答只能总结当前 Evidence 直接支持的信号**，不能冒充完整风险清单；诉讼、监管、质押、商誉等未检索到时，也不能推断公司不存在该事项。
7. `category='risk'` 的证据在响应里被归一成 **`business`**：
   前端 `api.ts` 的 `isEvidence()` 只接受 financial/business/company，
   而任务书明确要求不得修改前端。这是**已知的契约妥协**，不是数据丢失。
8. 动态图表只允许由后端从最终 Evidence 确定性构建，模型不得直接给出图表数值；证据不足时仍返回 `charts: []`。

---

## ⑨ 关于 `_unused/`

旧链路（ORM + 结构化财务字段 + 确定性规则引擎 + 外部渠道抓取 + 应用内调度）与本轮
新链路的数据模型不兼容，已整体归档到 `_unused/`，**不再被任何代码引用**。
每个文件的废弃原因与替代关系见 `_unused/README.md`。确认无误后可整目录删除。
