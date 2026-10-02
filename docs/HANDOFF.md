# Ask the Company — Codex 交接记忆

> 状态日期：2026-10-02  
> 工作区：`D:\Codes\XueJunHackathon`  
> 当前角色：前端开发；用户负责遇到不确定产品取舍时做决策

## 1. 项目目标

Ask the Company 是 X-Ray「透视·真相」赛道的企业信息理解产品。用户不是阅读大量公告，而是直接向公司提问；系统只基于可追溯 Evidence 生成 Answer、Claim、Signal 和 Chart。思看科技仍是稳定演示基线；前端四公司承载、候选数据库和后端动态问答的补充收口已通过自动化验收，真实 DeepSeek + 浏览器成功链路仍需人工联调。

最高原则：`No Evidence, No Claim`。证据不足时固定回答：

> 根据目前掌握的信息，我无法可靠回答这个问题。

这是 48 小时 Hackathon。优先完成稳定、可讲清楚的垂直闭环，不要引入 LangGraph、多 Agent、复杂 GraphRAG、用户系统或其他非必要基础设施。

## 2. 当前已经完成

> 本节只描述已经完成并验收的能力。动态链路已通过数据库、合成后端、真实库与前端契约测试；尚未执行真实 DeepSeek + 浏览器人工验收，不能把它描述为比赛现场已验证的稳定链路。

- Next.js 16 + React 19 + TypeScript + Tailwind CSS 4 前端。
- 公司页路由：`/company/{id}`，根路径自动跳转到 `/company/688583`。
- 支持思看科技（688583）、恒生电子（600570）、中国长城（000066）和贝达药业（300558）切换；公司目录集中在 `src/lib/companies.ts`。
- 未知公司代码显示明确的不支持状态，不会套用思看科技数据。
- 切换公司会清空问题、回答、加载状态和 Evidence 抽屉，并取消旧请求，避免迟到响应串台。
- 提问输入、分阶段加载动画、Answer、Claim、X-Ray Signal、ECharts、Evidence 列表和 Evidence 抽屉。
- Claim 和 Signal 中的 Evidence ID 均可点击并打开对应证据。
- 3 个问题对应 3 套独立回答，不再复用同一段总结。
- 后端适配器把 FastAPI snake_case 响应转换成前端 camelCase 类型。
- 思看科技未配置后端时使用 Mock；后端超时、HTTP 错误或 JSON 解析失败时自动切换已核验演示数据，并显示 `DEMO FALLBACK`。
- 其他三家公司不使用思看科技 Mock；动态服务不可用时返回证据不足结构并显示 `SERVICE UNAVAILABLE`。
- 未准备的问题返回证据不足回答，不伪造公司事实。
- 数字高亮、Evidence 可靠度等级与原因说明已经完成；官方来源识别覆盖上交所与巨潮资讯，可选 `verification_status` 支持 `verified`、`auto`、`pending`。
- FastAPI 响应具有最小运行时校验；非法结构在思看科技降级为已核验 Demo，其他公司进入服务不可用状态。
- 后端三条确定性回答、真实 SQLite 只读访问、上交所权威来源和四问样例已经接入代码库。
- 四公司动态 Evidence-first 检索、DeepSeek 结构化回答与候选库重建工具已接入代码库；后端会过滤 `excluded=1`，Claim/Signal 数字按自身引用 Evidence 硬校验，无显式 Evidence ID 时不猜出处。
- 新候选库全量 503 条 Evidence 中 407 条可参与回答，96 条隔离；407 条均为同页连续原文且不超过 200 字符，FTS 2845/2845。

## 3. 稳定 Demo 脚本

| 问题 | 核心 Signal | 图表 | Evidence |
|---|---|---:|---|
| 你的收入结构发生了什么变化？ | 核心产品收入增长，但依赖度下降 | 有 | 招股书 PDF 第 322 / 324 页（另 321 页为收入合计与同比） |
| 你最近真的赚钱吗？ | 收入增长，但利润和现金流增长未同步 | 有 | 2025 半年报第 8 页（会计数据）与第 9 页（现金流下降原因） |
| 目前最值得关注的风险是什么？ | 增长依赖持续技术领先与下游需求 | 无 | 2025 半年报第 2 页（未发生实质影响的重大风险）与第 43 页（风险因素） |

关键数字：

- 2021—2023 年，便携式 3D 扫描仪收入占比从 78.19% 降至 57.87%。
- 同期跟踪式产品收入从 1,893.69 万元增至 7,222.66 万元，占比从 11.77% 升至 26.58%。
- 2025 年上半年营业收入同比增长 17.70%，归母净利润增长 2.06%，扣非归母净利润下降 2.93%，经营现金流净额下降 32.59%。
- 风险回答必须说明这些是公司披露的风险提示，不代表风险已经发生。

## 4. 数据来源

- 招股说明书：`https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf`
- 2025 年半年度报告：`https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-28/688583_20250828_9F77.pdf`
- 页码采用 PDF 查看器页码，不是印刷页码。
- `cninfo.db` 和 `ruvector.db` 是仓库根目录下被忽略的数据文件，不要删除、覆盖或提交。前端不直接读取 SQLite；真实接口由 FastAPI 只读访问 `cninfo.db`，未配置或异常时使用 `src/lib/mock-data.ts`。

### 4.1 页码逐页核验结论（2026-10-02，后端联调轮）

两份 PDF 已用 pypdf 逐页抽取文本核实，**招股书三条证据不在同一页**：

| 事实 | 注册稿 PDF 查看器页 | 该页页脚 | 说明 |
|---|---:|---|---|
| 便携式扫描仪 78.19% → 57.87% | **322** | 1-1-321 | 逐字核到 |
| 跟踪式产品 11.77% → 26.58% | **324** | 1-1-323 | 逐字核到 |
| 主营收入合计 27,170.18 / 同比 31.88% | **321** | 1-1-320 | 逐字核到 |

⚠️ **PDF 查看器第 323 页并不支持跟踪式引用** —— 该页内容是「彩色 3D 扫描仪 … 五大系列
便携式 3D 扫描仪划分依据」，没有跟踪式收入数据。前后端均已使用第 **324** 页。

⚠️ 注册稿共 **506 页**，与巨潮上市稿（520 页）**不是同一份**：页数不同、偏移也不固定
（便携式那段：上市稿 329 → 注册稿 322；跟踪式那段：上市稿 332 → 注册稿 324）。
所以后端 `cninfo.db` 无法为注册稿页码作证，只能用 PDF 本体核验。

半年报（269 页）与库内同名文档为同一份，**第 2 / 8 / 9 / 43 页已与库逐字对齐**。

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

`sourceMode: "api" | "mock" | "fallback" | "unavailable"` 由前端适配层添加，后端无需返回。不要让后端直接生成 ECharts JavaScript，只返回语义图表数据。

Evidence 可选返回 `verification_status: "verified" | "auto" | "pending"`；字段出现时前端会校验枚举值。

`source_url` 必须是没有 `#page=` fragment 的裸 PDF URL；`source_page` 独立返回，由前端在打开原文时统一追加页码锚点。

## 6. 关键文件

- `src/components/company-experience.tsx`：公司页主体、提问状态、加载步骤、Answer/Signal/Evidence 交互。
- `src/components/evidence-chart.tsx`：ECharts 渲染，支持正负值范围。
- `src/lib/companies.ts`：四家公司资料、首页指标和推荐问题的单一来源。
- `src/lib/mock-data.ts`：三套 Demo 回答、真实证据和问题路由。
- `src/lib/api.ts`：FastAPI 请求、45 秒超时、字段转换和按公司隔离的失败降级。
- `src/lib/types.ts`：前端统一数据类型。
- `src/app/globals.css`：页面样式和动画。
- `README.md`：启动方法与接口示例。
- `AGENTS.md`：下次 Codex 必须遵守的项目约束。

## 7. 运行与验证

```powershell
npm install
npm run dev
```

打开 `http://localhost:3000/company/688583`，再使用左侧入口切换四家公司。

真实后端地址通过 `.env.local` 配置：

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### 7.1 后端启动（联调时）

```powershell
cd xray-backend
python -m pip install -r requirements.txt
# 动态四公司联调必须显式使用已验收候选库；不要擅自覆盖稳定 cninfo.db
$env:DATABASE_PATH = "D:\Codes\XueJunHackathon\cninfo.multicompany.next.db"
$env:DB_PATH_STRICT = "true"
python main.py                        # http://127.0.0.1:8000
curl.exe -s http://127.0.0.1:8000/health     # 看 db_path 与 database
```

后端自测（两套必须分开跑，数量以当前测试输出为准）：

```powershell
python -m pytest tests -q
python -m pytest tests_real -q
```

2026-10-02 后端联调轮验证结果：四条问题全部 HTTP 200；三条稳定回答互不相同且各带
收入结构与盈利质量各带 1 个图表，风险问题无图表；未知问题返回固定兜底
且四个数组全空；所有证据均为上交所 HTTPS 裸 PDF URL，页码由前端追加；单条回答耗时
约 70–90 ms（远低于前端 45 秒超时）。

提交前至少运行：

```powershell
npm run build
```

2026-10-02 验证结果：构建通过；四家公司切换、未知代码状态、切换清理和非思看公司断网隔离已完成人工验收。思看科技 3 个问题均返回不同答案，8 个 Claim 的 Evidence 均可点击，Evidence 抽屉正常，浏览器运行时错误为 0。动态 API 已通过 Fake LLM、真实候选库和契约自动化验收，仍待真实 DeepSeek + 浏览器人工联调。

## 8. V0.2 架构：规划而非现状

产品规划采用“白天查询，夜间更新”：22:00–05:00 构建新的数据 Snapshot，验证通过才原子切换；失败则继续使用上一份稳定 Snapshot。后端计划增加 `GET /system/status`、维护期 503、Snapshot 元数据、Nightly Intelligence Pipeline 和 Evidence Validation。

这些能力目前尚未接入前端。实现时先确认后端接口已经可用，再做维护页和 Snapshot 更新时间展示；不要只按本地时间强制锁死页面，也不要把架构文档中的规划当作已完成事实。

## 9. 下一步优先级

1. 用新候选库启动后端，配置真实 DeepSeek Key，完成四家公司浏览器人工成功链路验收。
2. 保持思看科技确定性三问及其 Mock/Fallback 不退化，作为动态链路失败时的稳定 Demo。
3. 自动化补充验收已完成；是否用候选库替换稳定 `cninfo.db` 仍由产品负责人决定。
4. 验收四家公司 Answer、Claim、Signal、Evidence 与 `charts: []`，确认没有跨公司引用、跨 Evidence 数字借用或被隔离证据泄漏。

需要用户决策的问题：真实后端契约发生变化、是否启用维护模式、是否牺牲 Demo 稳定性增加新功能。不要擅自扩大范围。

## 10. 工作区注意事项

- 修改前先执行 `git status --short`，不要覆盖其他同学的未提交改动。
- `cninfo.db`、`cninfo(1).db`、`ruvector.db` 不要删除、覆盖或提交，除非用户明确授权。
- 仓库只保留 Ask the Company 前端、后端、数据库管道与项目文档，不要混入其他独立项目。
