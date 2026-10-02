# 今晚任务书：后端 Agent

> 日期：2026-10-02  
> 基线提交：`057d18a`  
> 目标：保留思看科技确定性 Demo，同时让四家公司能够回答未预设的金融问题。  
> 最高原则：`No Evidence, No Claim`

## 0. 开始前

1. 拉取最新 `main`，完整阅读 `AGENTS.md`、`README.md`、`docs/HANDOFF.md` 和本任务书。
2. 执行 `git status --short`，不要覆盖他人改动。
3. 只修改 `xray-backend/**`、后端测试和后端说明；不要修改 `src/**`、根目录 Pipeline 或数据库文件。
4. 真实 `cninfo.db` 只读。不要在请求期间运行 Pipeline、建索引或写数据库。
5. DeepSeek Key 只能放环境变量，绝不能提交 `.env` 或密钥。

## 1. 产品优先级

`POST /companies/{code}/ask` 的决策顺序必须是：

1. 思看科技现有三条稳定问题：继续走 `demo_handlers`，返回速度和内容不得退化。
2. 明确证据不足问题（如员工吃水果）：直接返回固定兜底，不调用模型。
3. 其他公司或其他金融问题：进入动态 Evidence-first 问答。
4. 任一环节证据不足、模型失败、解析失败或校验失败：HTTP 200 返回固定证据不足结构。

不得删除当前确定性处理器，不得让真实 LLM 成为思看科技稳定 Demo 的单点故障。

## 2. 动态问答的最小架构

### 2.1 先检索 Evidence，再让模型组织

今晚不要让模型自行编造 `source_quote`、页码或 URL。推荐流程：

```text
用户问题
→ 按 company_code 检索 evidence 表
→ 必要时从 chunks 补充少量分页原文
→ 后端生成候选 Evidence（ID/文档/页码/原文/URL）
→ DeepSeek 只能选择已有 Evidence ID 并组织 Answer/Claim/Signal
→ 后端机械校验
→ 返回或证据不足
```

P0 先覆盖结构化财务问题；P1 有余力再接 chunks 的非结构化风险问题。

### 2.2 检索要求

- 每条 SQL 必须带 `company_code = ?`，全部参数化。
- 默认过滤 `docs.parse_status='ok'` 和 `docs.superseded=0`。
- Evidence 必须 JOIN docs，补齐标题和真实 `source_url`。
- 优先识别收入、归母净利润、扣非利润、经营现金流、总资产、EPS、资产负债率、研发、应收账款和存货等词。
- 当前 `chunks_fts` 只有 801 行，而 `chunks` 有 2845 行，不能假设 FTS 已覆盖四家公司；数据库修复合入前可使用按公司过滤的参数化 `LIKE`。
- 每次给模型的候选 Evidence 控制在 6～15 条，不能把整份公告或整个数据库塞进 Prompt。
- 优先最新报告期，同时保留必要的对比期。

### 2.3 DeepSeek 输出

复用现有 `llm_client.py`，不要再引入第二套 SDK。模型只输出：

```json
{
  "answer": "...",
  "claims": [
    {"id":"CL-DYN-001","text":"...","evidence_ids":["EV-..."]}
  ],
  "signals": [
    {"id":"SIG-DYN-001","type":"trend","title":"...","severity":"attention","description":"...","evidence_ids":["EV-..."]}
  ]
}
```

- P0 动态回答固定 `charts: []`。
- `evidence` 由后端候选集合确定，不由模型生成。
- `suggested_questions` 使用产品约定的四公司通用问题，不让模型自由扩写。
- 温度保持低值；JSON 解析失败必须降级，最多允许一次轻量修复，不做无限重试。

## 3. No Evidence, No Claim 硬校验

返回前必须验证：

1. 所有 Claim 和 Signal 至少引用一条候选 Evidence。
2. 所有 Evidence ID 都存在于本次响应，禁止跨响应或跨公司引用。
3. `source_page` 是正整数且不超过文档页数。
4. `source_quote` 忽略空白后必须是对应页原文的连续子串。
5. `source_url` 是该 document 的真实 HTTP(S) PDF 地址，且不带 fragment。
6. Answer/Claim 中出现的关键金额和比例至少能在被引用 Evidence 中匹配。
7. 去掉不合格 Claim 后，如果没有 Claim，整次返回证据不足。
8. 模型不得使用候选 Evidence 以外的公司事实。

沿用并扩展现有 `response_validator.py`，不要绕过统一校验出口。

## 4. Evidence 核验状态

在保持现有字段兼容的前提下，为动态 Evidence 增加可选字段：

```json
"verification_status": "verified | auto | pending"
```

- 思看科技现有人工核验证据：`verified`。
- 数据库 `review_status=verified` 且原文页码复核通过：`verified`。
- 自动抽取但本次请求已完成原文/页码机械核验：`auto`。
- 未通过机械核验的 Evidence 不应返回；如保留诊断只能是 `pending`，且不得支撑 Claim。

同步更新 Pydantic schema、样例和前端契约说明，但保持该字段可选，避免破坏当前前端。

## 5. 四家公司与推荐问题

支持代码：`688583`、`600570`、`000066`、`300558`。

除思看科技稳定问题外，通用推荐问题固定为：

1. `最近营业收入和归母净利润表现如何？`
2. `经营现金流表现如何？`
3. `近几个报告期的盈利趋势是什么？`
4. `你的员工喜欢吃水果吗？`

公司不存在仍返回 404；公司存在但问题证据不足返回 HTTP 200 的固定兜底结构。

## 6. 超时与失败策略

- 思看科技稳定三问继续满足 2 秒以内。
- 动态 LLM 目标 20 秒以内，硬上限与前端约定不超过 45 秒。
- 未配置 Key：动态问题立即证据不足，不阻塞。
- LLM 超时、限流、空响应、非法 JSON：记录日志并证据不足。
- 不向前端泄露 Prompt、密钥、模型异常堆栈。
- 不在 `/ask` 请求期间下载 PDF 或修改 SQLite。

## 7. 必须测试

### 单元测试

- 思看科技原有全部测试不变并通过。
- 四家公司检索严格隔离。
- 模型只能引用候选 Evidence ID。
- 悬空 ID、错误页码、错误 quote、跨公司 evidence 被拒绝。
- 无 Key、超时、非法 JSON、空内容均稳定降级。
- 动态回答 `charts=[]` 仍通过契约。
- 员工水果问题不调用模型。

### 真实库验收

至少对每家公司执行：

```text
最近营业收入和归母净利润表现如何？
经营现金流表现如何？
你的员工喜欢吃水果吗？
```

验收：

- 前两问有证据时返回公司自己的 Evidence；没有证据时明确拒答。
- 水果问题四个数组为空。
- 所有返回 quote 能在 `cninfo.db` 对应页核到。
- 所有 URL 为裸地址。

执行：

```powershell
python -m pytest tests -q
python -m pytest tests_real -q
```

真实 DeepSeek 只做受控冒烟，不把真实调用放进自动测试。

## 8. 交付物

- 动态检索与问答代码。
- Pydantic 契约和响应校验扩展。
- Fake LLM 测试。
- 四家公司至少各一份动态响应样例。
- `.env.example` 中的 DeepSeek 配置说明，不含密钥。
- PR 描述记录：模型、平均耗时、成功问题、拒答问题、测试结果和已知限制。

## 9. 禁止事项与停止线

- 不引入 LangGraph、多 Agent、向量数据库或复杂 GraphRAG。
- 不让模型生成或猜测 Evidence 来源。
- 不在 `/ask` 中下载公告。
- 不为动态回答生成图表。
- 不修改思看科技已核验 PDF 和页码。
- 不修改或提交 `cninfo.db`、`ruvector.db`。
- 距离截止不足 8 小时时停止扩展检索范围；若动态链路不稳定，保留代码开关并默认回到证据不足，不能影响稳定三问。

