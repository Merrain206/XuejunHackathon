# Ask the Company

面向思看科技（688583）、恒生电子（600570）、中国长城（000066）和贝达药业（300558）的可验证企业问答 Demo。仓库包含 Next.js 前端、FastAPI 后端和 SQLite 数据管道。思看科技默认使用经公开招股书和 2025 年半年度报告核验的 Mock Evidence；其余公司依赖动态 Evidence 服务，不会降级到思看科技演示数据。动态问答覆盖财务、治理、审计、股东回报、员工与激励、供应链、关联关系、研发和风险等普通投资者主题。

## 本地运行

```bash
npm install
npm run dev
```

打开 `http://localhost:3000`，根路径会跳转到 `/company/688583`。也可以通过页面内的公司切换入口访问另外三家公司。

## 连接 FastAPI

复制 `.env.example` 为 `.env.local`，设置：

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

前端请求：

```text
POST /companies/688583/ask
Content-Type: application/json

{"question":"你的收入结构发生了什么变化？"}
```

请求路径中的公司代码随当前页面切换，前端超时为 45 秒。思看科技请求失败时回退到已核验 Demo；其他公司请求失败时只显示证据不足/服务不可用状态。

响应字段采用 Python 友好的 snake_case：

```json
{
  "answer": "...",
  "claims": [
    { "id": "CL-001", "text": "...", "evidence_ids": ["EV-001"] }
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
      "title": "两类产品收入占比变化",
      "subtitle": "...",
      "unit": "%",
      "periods": ["2021", "2022", "2023"],
      "series": [
        { "name": "便携式 3D 扫描仪", "values": [78.19, 68.87, 57.87] }
      ],
      "evidence_ids": ["EV-001", "EV-002"]
    }
  ],
  "evidence": [
    {
      "id": "EV-001",
      "category": "business",
      "period": "2021—2023",
      "content": "...",
      "document_id": "DOC-001",
      "document_title": "招股说明书（注册稿）",
      "source_page": 322,
      "source_quote": "...",
      "source_url": "https://static.sse.com.cn/...pdf"
    }
  ],
  "suggested_questions": ["..."]
}
```

所有 Claim、Signal 和 Chart 必须包含真实 `evidence_id`。动态折线图/柱状图由后端从最终通过校验的 Evidence 确定性构建，一个复合问题最多返回 3 张图；模型不直接生成图表数据。`source_url` 返回裸 PDF 地址，前端根据 `source_page` 统一追加页码锚点。没有足够证据时，后端应返回空数组和固定兜底回答。

前端通过 `sourceMode` 区分四种数据状态：`api`（真实后端）、`mock`（思看科技未配置后端时的演示数据）、`fallback`（思看科技后端异常后的已核验降级数据）和 `unavailable`（其他公司动态证据服务不可用）。`sourceMode` 是前端适配层字段，不要求后端返回。

当前稳定演示问题：

- 你的收入结构发生了什么变化？
- 你最近真的赚钱吗？
- 目前最值得关注的风险是什么？
- 你的员工喜欢吃水果吗？（用于演示证据不足）

完整交接说明见 [`docs/HANDOFF.md`](docs/HANDOFF.md)，普通投资者 Evidence 扩展范围与缺口见 [`docs/INVESTOR_EVIDENCE_EXPANSION.md`](docs/INVESTOR_EVIDENCE_EXPANSION.md)。

## 验证

```bash
npm run build
```
