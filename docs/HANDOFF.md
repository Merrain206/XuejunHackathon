# Ask the Company — Codex 交接记忆

> 状态日期：2026-10-02  
> 工作区：`D:\Codes\XueJunHackathon`  
> 当前角色：前端开发；用户负责遇到不确定产品取舍时做决策

## 1. 项目目标

Ask the Company 是 X-Ray「透视·真相」赛道的企业信息理解产品。用户不是阅读大量公告，而是直接向公司提问；系统只基于可追溯 Evidence 生成 Answer、Claim、Signal 和 Chart。

最高原则：`No Evidence, No Claim`。证据不足时固定回答：

> 根据目前掌握的信息，我无法可靠回答这个问题。

这是 48 小时 Hackathon。优先完成稳定、可讲清楚的垂直闭环，不要引入 LangGraph、多 Agent、复杂 GraphRAG、用户系统或其他非必要基础设施。

## 2. 当前已经完成

- Next.js 16 + React 19 + TypeScript + Tailwind CSS 4 前端。
- 公司页路由：`/company/688583`，根路径自动跳转过去。
- 公司固定为思看科技（688583，上交所科创板）。
- 提问输入、分阶段加载动画、Answer、Claim、X-Ray Signal、ECharts、Evidence 列表和 Evidence 抽屉。
- Claim 和 Signal 中的 Evidence ID 均可点击并打开对应证据。
- 3 个问题对应 3 套独立回答，不再复用同一段总结。
- 后端适配器把 FastAPI snake_case 响应转换成前端 camelCase 类型。
- 未配置后端时使用 Mock；后端超时、HTTP 错误或 JSON 解析失败时自动切换已核验演示数据，并显示 `DEMO FALLBACK`。
- 未准备的问题返回证据不足回答，不伪造公司事实。

## 3. 稳定 Demo 脚本

| 问题 | 核心 Signal | 图表 | Evidence |
|---|---|---:|---|
| 你的收入结构发生了什么变化？ | 核心产品收入增长，但依赖度下降 | 有 | 招股书 PDF 第 322 / 324 页（另 321 页为收入合计与同比） |
| 你最近真的赚钱吗？ | 收入增长，但利润和现金流增长未同步 | 有 | 2025 半年报第 8 页（会计数据）与第 9 页（现金流下降原因） |
| 目前最值得关注的风险是什么？ | 增长依赖持续技术领先与下游需求 | 无 | 2025 半年报第 43 页 |

关键数字：

- 2021—2023 年，便携式 3D 扫描仪收入占比从 78.19% 降至 57.87%。
- 同期跟踪式产品收入从 1,893.69 万元增至 7,222.66 万元，占比从 11.77% 升至 26.58%。
- 2025 年上半年营业收入同比增长 17.70%，归母净利润增长 2.06%，扣非归母净利润下降 2.93%，经营现金流净额下降 32.59%。
- 风险回答必须说明这些是公司披露的风险提示，不代表风险已经发生。

## 4. 数据来源

- 招股说明书：`https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf`
- 2025 年半年度报告：`https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-28/688583_20250828_9F77.pdf`
- 页码采用 PDF 查看器页码，不是印刷页码。
- `cninfo(1).db` 和 `ruvector.db` 当前是仓库根目录下未跟踪的数据文件；前端 Demo 仍从 `src/lib/mock-data.ts` 读取已核验数据，不直接读取 SQLite。

### 4.1 页码逐页核验结论（2026-10-02，后端联调轮）

两份 PDF 已用 pypdf 逐页抽取文本核实，**招股书三条证据不在同一页**：

| 事实 | 注册稿 PDF 查看器页 | 该页页脚 | 说明 |
|---|---:|---|---|
| 便携式扫描仪 78.19% → 57.87% | **322** | 1-1-321 | 逐字核到 |
| 跟踪式产品 11.77% → 26.58% | **324** | 1-1-323 | 逐字核到 |
| 主营收入合计 27,170.18 / 同比 31.88% | **321** | 1-1-320 | 逐字核到 |

⚠️ **PDF 查看器第 323 页并不支持跟踪式引用** —— 该页内容是「彩色 3D 扫描仪 … 五大系列
便携式 3D 扫描仪划分依据」，没有跟踪式收入数据。因此 `src/lib/mock-data.ts` 里
`EV-STR-002` 的 `sourcePage: 323` 需要改成 **324**（后端已按 324 交付）。

⚠️ 注册稿共 **506 页**，与巨潮上市稿（520 页）**不是同一份**：页数不同、偏移也不固定
（便携式那段：上市稿 329 → 注册稿 322；跟踪式那段：上市稿 332 → 注册稿 324）。
所以后端 `cninfo.db` 无法为注册稿页码作证，只能用 PDF 本体核验。

半年报（269 页）与库内同名文档为同一份，**第 8 / 9 / 43 页已与库逐字对齐**。

## 5. 前后端契约

请求：

```http
POST /companies/688583/ask
Content-Type: application/json

{"question":"你最近真的赚钱吗？"}
```

后端返回 snake_case：

```json
{
  "answer": "...",
  "claims": [{ "id": "...", "text": "...", "evidence_ids": ["..."] }],
  "signals": [{ "id": "...", "type": "divergence", "title": "...", "severity": "attention", "description": "...", "evidence_ids": ["..."] }],
  "charts": [{ "id": "...", "type": "line", "title": "...", "subtitle": "...", "unit": "%", "periods": ["..."], "series": [{ "name": "...", "values": [1] }], "evidence_ids": ["..."] }],
  "evidence": [{ "id": "...", "category": "financial", "period": "...", "content": "...", "document_id": "...", "document_title": "...", "source_page": 8, "source_quote": "...", "source_url": "..." }],
  "suggested_questions": ["..."]
}
```

`sourceMode: "api" | "mock" | "fallback"` 由前端适配层添加，后端无需返回。不要让后端直接生成 ECharts JavaScript，只返回语义图表数据。

## 6. 关键文件

- `src/components/company-experience.tsx`：公司页主体、提问状态、加载步骤、Answer/Signal/Evidence 交互。
- `src/components/evidence-chart.tsx`：ECharts 渲染，支持正负值范围。
- `src/lib/mock-data.ts`：三套 Demo 回答、真实证据和问题路由。
- `src/lib/api.ts`：FastAPI 请求、8 秒超时、字段转换和失败降级。
- `src/lib/types.ts`：前端统一数据类型。
- `src/app/globals.css`：页面样式和动画。
- `README.md`：启动方法与接口示例。
- `AGENTS.md`：下次 Codex 必须遵守的项目约束。

## 7. 运行与验证

```powershell
npm install
npm run dev
```

打开 `http://localhost:3000/company/688583`。

真实后端地址通过 `.env.local` 配置：

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### 7.1 后端启动（联调时）

```powershell
cd xray-backend
python -m pip install -r requirements.txt
# cninfo.db 放到仓库根目录即可，无需改 .env（会自动探测）
python main.py                        # http://127.0.0.1:8000
curl.exe -s http://127.0.0.1:8000/health     # 看 db_path 与 database
```

后端自测（两套必须分开跑）：

```powershell
python -m pytest tests -q          # 105 passed（合成库）
python -m pytest tests_real -q     # 54 passed（真实库）
```

2026-10-02 后端联调轮验证结果：四条问题全部 HTTP 200；三条稳定回答互不相同且各带
3 条 Evidence，收入结构与盈利质量各带 1 个图表，风险问题无图表；未知问题返回固定兜底
且四个数组全空；所有证据均为上交所 HTTPS PDF 且带 `#page=N` 锚点；单条回答耗时
约 70–90 ms（远低于前端 8 秒超时）。

提交前至少运行：

```powershell
npm run build
```

2026-10-02 验证结果：构建通过；模拟后端不可达时，3 个问题均返回不同答案，8 个 Claim 的 Evidence 均可点击，Evidence 抽屉正常，浏览器运行时错误为 0。

## 8. V0.2 架构：规划而非现状

产品规划采用“白天查询，夜间更新”：22:00–05:00 构建新的数据 Snapshot，验证通过才原子切换；失败则继续使用上一份稳定 Snapshot。后端计划增加 `GET /system/status`、维护期 503、Snapshot 元数据、Nightly Intelligence Pipeline 和 Evidence Validation。

这些能力目前尚未接入前端。实现时先确认后端接口已经可用，再做维护页和 Snapshot 更新时间展示；不要只按本地时间强制锁死页面，也不要把架构文档中的规划当作已完成事实。

## 9. 下一步优先级

1. 与后端联调 `POST /companies/{id}/ask`，用真实响应覆盖三条 Demo 问题，同时保留降级路径。
2. 为返回结构加最小运行时校验，避免后端缺字段导致 UI 崩溃。
3. 若后端实现 V0.2 状态接口，再增加维护模式和 `active_snapshot/data_updated_at` 展示。
4. 只有核心 Demo 稳定且时间充足时，再考虑 Evidence Graph、Timeline 或更多公司。

需要用户决策的问题：真实后端契约发生变化、是否启用维护模式、是否牺牲 Demo 稳定性增加新功能。不要擅自扩大范围。

## 10. 工作区注意事项

- 当前前端增强尚未提交，修改集中在 `src/app/globals.css`、`src/components/` 和 `src/lib/`。
- `cninfo(1).db`、`ruvector.db` 是未跟踪文件，不要删除、覆盖或提交，除非用户明确授权。
- 仓库目前只有一个已有提交：`cf13f94 初始化 Ask the Company 前端 MVP`。
