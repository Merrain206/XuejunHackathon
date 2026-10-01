# Ask the Company — Frontend MVP

思看科技（688583）的可验证企业问答 Demo。当前版本默认使用经公开招股书核验的 Mock Evidence；配置后端地址后会自动请求 FastAPI。

## 本地运行

```bash
npm install
npm run dev
```

打开 `http://localhost:3000`，根路径会跳转到 `/company/688583`。

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
      "source_page": 206,
      "source_quote": "...",
      "source_url": "https://..."
    }
  ],
  "suggested_questions": ["..."]
}
```

所有 Claim 和 Signal 必须包含至少一个真实 `evidence_id`。没有足够证据时，后端应返回空数组和固定兜底回答。

## 验证

```bash
npm run build
```
