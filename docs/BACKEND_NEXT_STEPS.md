# 后端同学下一步任务

> 状态：首轮后端任务已完成；本文为历史任务书，当前实现与运行方式以 `docs/HANDOFF.md` 和 `xray-backend/README.md` 为准。

> 项目：Ask the Company 48 小时 Hackathon Demo  
> 当前 Demo 公司：思看科技（688583）  
> 最高原则：`No Evidence, No Claim`  
> 开始前请先完整阅读仓库根目录 `AGENTS.md`、`README.md` 和 `docs/HANDOFF.md`。

## 目标

基于 SQLite 实现一个稳定、可验证的 FastAPI 问答接口：

```http
POST /companies/{id}/ask
Content-Type: application/json
```

前端只通过该接口与后端耦合。后端负责从数据库检索证据、生成结构化回答、验证 Evidence 引用，并在证据不足时拒绝生成公司事实。

## 当前输入

更新版数据库：

```text
D:\Codes\XueJunHackathon\cninfo.db
```

当前数据库已有 `docs`、`chunks`、`chunks_fts`、`evidence` 和 `meta`，但数据库同学仍在修复金额、单位、半年报分类、FTS 模式、招股书 Chunk 和风险 Evidence。

后端可以立即搭建接口和查询框架，但在数据库 P0 修复完成前，不得直接使用当前 `evidence.value` 和 `evidence.unit` 生成真实财务结论。当前应把 `source_quote` 视为最终核验依据。

## P0：接口与数据访问

### 1. 创建最小 FastAPI 服务

实现：

```http
POST /companies/688583/ask
```

请求体：

```json
{
  "question": "你最近真的赚钱吗？"
}
```

数据库路径通过环境变量配置：

```text
DATABASE_PATH=D:\Codes\XueJunHackathon\cninfo.db
```

不要在代码中硬编码个人目录。

### 2. SQLite 使用方式

- FastAPI 只读访问稳定数据库。
- 使用参数化 SQL，禁止拼接用户输入。
- 每次查询必须包含 `company_code`。
- 默认过滤：

```sql
parse_status = 'ok'
superseded = 0
```

- 请求期间不要运行 Pipeline 或修改数据库。
- Hackathon 阶段采用“离线构建数据库，后端只读”的模式。
- 如果需要并发连接，使用短连接或明确的连接生命周期，不共享跨线程 cursor。

### 3. 查询层职责

至少提供以下内部能力：

- 按公司、文档类型、报告期查询文档。
- 按公司和关键词查询 Chunk/FTS。
- 按公司、指标、文档和页码查询 Evidence。
- 将 Evidence 与 docs JOIN，补齐：
  - 文档标题
  - 原始 URL
  - PDF 查看器页码
  - 报告期
  - 原文摘录

不要把整个 `text_content` 直接发送给模型。

## P0：三条稳定问题

Hackathon 阶段先实现三个确定性意图处理器，不引入 LangGraph、多 Agent 或复杂 GraphRAG。

### 1. 收入结构

问题：

```text
你的收入结构发生了什么变化？
```

数据范围：上交所招股说明书（注册稿）PDF 查看器第 321、322、324 页。

输出应覆盖：

- 便携式 3D 扫描仪收入仍增长，但收入占比下降。
- 跟踪式产品收入和占比上升。
- 图表展示两类产品占比变化。

### 2. 盈利质量

问题：

```text
你最近真的赚钱吗？
```

数据范围：2025 年半年度报告 PDF 查看器第 8、9 页。

输出应覆盖：

- 营业收入同比增长 17.70%。
- 归母净利润同比增长 2.06%。
- 扣非归母净利润同比下降 2.93%。
- 经营现金流净额同比下降 32.59%。
- 图表展示收入、利润和现金流增速分化。

### 3. 主要风险

问题：

```text
目前最值得关注的风险是什么？
```

数据范围：2025 年半年度报告 PDF 查看器第 43 页。

输出应覆盖：

- 产品结构变化可能影响毛利率。
- 技术优势减弱可能影响售价和市场份额。
- 下游重要行业需求收缩可能影响收入。
- 必须说明这些是公司披露的风险提示，不代表风险已经发生。

### 4. 证据不足问题

例如：

```text
你的员工喜欢吃水果吗？
```

固定返回：

```text
根据目前掌握的信息，我无法可靠回答这个问题。
```

并返回空的 Claim、Signal、Chart 和 Evidence 数组。未知问题应返回 HTTP 200，不应作为服务器错误处理。

## P0：响应契约

返回 snake_case JSON：

```json
{
  "answer": "...",
  "claims": [
    {
      "id": "CL-001",
      "text": "...",
      "evidence_ids": ["EV-001"]
    }
  ],
  "signals": [
    {
      "id": "SIG-001",
      "type": "divergence",
      "title": "...",
      "severity": "attention",
      "description": "...",
      "evidence_ids": ["EV-001"]
    }
  ],
  "charts": [
    {
      "id": "CHART-001",
      "type": "line",
      "title": "...",
      "subtitle": "...",
      "unit": "%",
      "periods": ["2021", "2022", "2023"],
      "series": [
        {
          "name": "便携式 3D 扫描仪",
          "values": [78.19, 68.87, 57.87]
        }
      ],
      "evidence_ids": ["EV-001"]
    }
  ],
  "evidence": [
    {
      "id": "EV-001",
      "category": "business",
      "period": "2021—2023",
      "content": "...",
      "document_id": "DOC-001",
      "document_title": "...",
      "source_page": 322,
      "source_quote": "...",
      "source_url": "https://..."
    }
  ],
  "suggested_questions": []
}
```

前端运行时校验要求：

- `answer` 必须是非空字符串。
- `claims`、`signals`、`evidence` 必须是数组。
- `charts` 可以省略或使用数组。
- Claim、Signal、Chart 的 `evidence_ids` 必须非空，并且全部指向返回的 Evidence。
- Evidence ID 不能重复。
- Evidence category 当前只接受：

```text
financial
business
company
```

- `source_page` 必须是正整数。
- `source_quote` 必须非空。
- `source_url` 必须是合法 HTTP(S) URL。
- 图表每个 series 的 values 数量必须等于 periods 数量。
- 图表数值必须是有限数字。

任何结构不符合要求，前端都会进入 `DEMO FALLBACK`。

## P0：No Evidence, No Claim 校验

响应返回前必须执行：

1. 所有 Claim 至少引用一条 Evidence。
2. 所有 Signal 至少引用一条 Evidence。
3. 所有 Chart 至少引用一条 Evidence。
4. 所有 `evidence_ids` 都存在于当前响应的 Evidence 数组。
5. Answer 中使用的关键金额和比例能在引用原文中核对。
6. `source_quote` 必须来自对应文档和页码。
7. URL 或页码缺失时，不得返回相关 Claim。
8. 证据不足时返回固定兜底回答，不让模型补充事实。

建议为此实现独立的响应校验函数，并在接口返回前统一调用。

## 数据库修复完成前的临时原则

当前数据库结构化金额存在错误，暂时遵循：

- 不直接信任 `evidence.value` 和 `evidence.unit`。
- 优先使用已经人工核验的 Demo Evidence。
- 使用 `source_quote` 重新核对数字。
- 不把错误的自动抽取内容传给前端。
- 数据库同学重建后，再切换为结构化字段。

不要在后端永久复制一套修复数据库错误的复杂解析逻辑；数字和单位抽取问题应由数据库 Pipeline 修复。

## 来源和可靠度

当前数据库主要保存巨潮资讯 URL，而前端 Demo 使用上交所原始 PDF。

- 不得凭空改写或猜测来源 URL。
- 对三条 Demo，应优先返回已经核验的上交所原始 PDF URL。
- 如果只找到巨潮资讯原文，应返回实际 URL，并与前端确认它是否属于“高可靠度官方来源”。
- 页码必须按实际返回的 PDF 文件核验，不能把不同版本 PDF 的页码混用。

## API 行为

- 正常回答：HTTP 200。
- 证据不足：HTTP 200，返回固定兜底结构。
- 请求格式错误：HTTP 422 或 400。
- 服务异常：HTTP 5xx，前端会自动降级。
- 避免超过前端 8 秒超时；三条 Demo 应尽量在 2 秒内返回。
- 配置允许前端开发地址访问的 CORS，例如 `http://localhost:3000`。

## 测试与验收

至少覆盖：

1. 三条稳定问题分别返回不同 Answer。
2. 前两条有 Chart，风险问题无 Chart。
3. 每个 Claim Evidence 可追溯。
4. 每个 Signal Evidence 可追溯。
5. Chart Evidence 可追溯。
6. 未知问题返回证据不足。
7. 不存在悬空 Evidence ID。
8. source page 和 URL 完整。
9. 公司 ID 不存在时不会查询到其他公司数据。
10. 数据库不可用时返回 5xx，而不是伪造回答。

请提供四个实际响应样例：

- 收入结构
- 盈利质量
- 主要风险
- 员工喜欢吃水果

前端将直接使用这些响应做联调。

## 最终交付物

- FastAPI 服务代码。
- 依赖与启动说明。
- `.env.example` 或环境变量说明。
- SQLite 查询层。
- 三条稳定问题处理器。
- Evidence/响应校验器。
- 接口测试。
- 四个响应样例。

## 不要做

- 不要引入 LangGraph、多 Agent、复杂 GraphRAG 或用户系统。
- 不要直接生成 ECharts JavaScript，只返回语义图表数据。
- 不要绕过 Evidence 生成公司事实。
- 不要让 FastAPI 在请求期间运行数据 Pipeline。
- 不要实现尚未确认接口的 V0.2 Nightly Pipeline、Snapshot 或维护模式。
- 不要修改前端 snake_case 到 camelCase 的适配职责。

